"""Autonomous planning and execution service."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import selectinload

from cyber_team.clock import utc_now
from cyber_team.db import async_session
from cyber_team.db.models import (
    AutonomousPlan,
    AutonomousTask,
    MemoryStewardFinding,
    RoleGap,
)


class AutonomousPlanningService:
    """Turns operational signals into durable plans and executes safe steps."""

    ACTIVE_PLAN_STATUSES = {"planned", "running", "waiting_approval", "blocked"}
    EXECUTABLE_PLAN_STATUSES = {"planned", "running", "waiting_approval"}
    EXECUTABLE_TASK_STATUSES = {"planned", "waiting_approval"}
    ROLE_GAP_STATUSES = {"open", "proposed"}
    MEMORY_FINDING_STATUSES = {"open", "acknowledged"}

    def __init__(
        self,
        *,
        agent_manager,
        memory_steward_service,
        audit_service=None,
        session_factory=async_session,
    ):
        self._agent_manager = agent_manager
        self._memory_steward = memory_steward_service
        self._audit = audit_service
        self._session_factory = session_factory

    async def scan_and_plan(
        self,
        *,
        actor: str = "autonomous_planner",
        include_role_gaps: bool = True,
        include_memory_findings: bool = True,
        auto_execute: bool = True,
        limit: int = 50,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(limit, 200))
        created = []
        existing = []
        errors = []

        if include_role_gaps:
            for gap in await self._load_role_gaps(safe_limit):
                try:
                    result = await self.create_plan_from_role_gap(gap["id"], actor=actor)
                    (created if result["created"] else existing).append(result["plan"])
                except Exception as exc:
                    errors.append(self._error("role_gap", gap["id"], exc))

        if include_memory_findings:
            for finding in await self._load_memory_findings(safe_limit):
                try:
                    result = await self.create_plan_from_memory_finding(
                        finding["id"],
                        actor=actor,
                    )
                    (created if result["created"] else existing).append(result["plan"])
                except Exception as exc:
                    errors.append(self._error("memory_steward_finding", finding["id"], exc))

        execution = None
        if auto_execute:
            execution = await self.execute_ready_plans(actor=actor, limit=safe_limit)

        summary = {
            "scanned_at": utc_now().isoformat(),
            "actor": actor,
            "plans_created": len(created),
            "plans_existing": len(existing),
            "created_plan_ids": [plan["id"] for plan in created],
            "existing_plan_ids": [plan["id"] for plan in existing],
            "errors": errors,
            "execution": execution,
        }
        await self._record(
            "autonomous_planning.scan",
            actor=actor,
            resource_id=None,
            outcome="degraded" if errors else "success",
            metadata={key: value for key, value in summary.items() if key != "execution"},
        )
        return summary

    async def create_plan_from_role_gap(
        self,
        gap_id: str,
        *,
        actor: str = "autonomous_planner",
    ) -> dict[str, Any]:
        existing = await self._find_active_plan("role_gap", gap_id)
        if existing:
            return {"created": False, "plan": existing}

        gap = await self._get_role_gap(gap_id)
        if not gap:
            raise ValueError(f"Role gap {gap_id} not found")
        if gap["status"] not in self.ROLE_GAP_STATUSES:
            raise ValueError(f"Role gap {gap_id} is {gap['status']}")

        task_specs = []
        sequence = 1
        if not gap.get("proposed_role"):
            task_specs.append({
                "sequence": sequence,
                "title": "Propose missing role",
                "description": f"Generate a role proposal for: {gap['title']}",
                "task_type": "role_gap.propose",
                "target_type": "role_gap",
                "target_id": gap_id,
                "risk_level": "low",
                "autonomous_allowed": True,
                "action_payload": {"gap_id": gap_id},
            })
            sequence += 1
        task_specs.append({
            "sequence": sequence,
            "title": "Apply role proposal",
            "description": f"Instantiate the approved role proposal for: {gap['title']}",
            "task_type": "role_gap.apply",
            "target_type": "role_gap",
            "target_id": gap_id,
            "risk_level": "medium",
            "autonomous_allowed": True,
            "action_payload": {"gap_id": gap_id},
        })

        plan = await self._create_plan(
            title=f"Resolve role gap: {gap['title']}",
            objective=gap["description"],
            source_type="role_gap",
            source_id=gap_id,
            priority=gap["severity"],
            created_by=actor,
            context={
                "capability": gap.get("capability"),
                "requested_tools": gap.get("requested_tools", []),
                "company_namespace": gap.get("company_namespace"),
            },
            task_specs=task_specs,
        )
        await self._record(
            "autonomous_plan.created",
            actor=actor,
            resource_id=plan["id"],
            metadata={"source_type": "role_gap", "source_id": gap_id},
        )
        return {"created": True, "plan": plan}

    async def create_plan_from_memory_finding(
        self,
        finding_id: str,
        *,
        actor: str = "autonomous_planner",
    ) -> dict[str, Any]:
        existing = await self._find_active_plan("memory_steward_finding", finding_id)
        if existing:
            return {"created": False, "plan": existing}

        finding = await self._get_memory_finding(finding_id)
        if not finding:
            raise ValueError(f"Memory steward finding {finding_id} not found")
        if finding["status"] not in self.MEMORY_FINDING_STATUSES:
            raise ValueError(f"Memory steward finding {finding_id} is {finding['status']}")

        plan = await self._create_plan(
            title=f"Remediate memory finding: {finding['title']}",
            objective=finding["recommendation"],
            source_type="memory_steward_finding",
            source_id=finding_id,
            priority=finding["severity"],
            created_by=actor,
            context={
                "finding_type": finding.get("finding_type"),
                "agent_id": finding.get("agent_id"),
                "memory_namespace": finding.get("memory_namespace"),
                "company_namespace": finding.get("company_namespace"),
            },
            task_specs=[
                {
                    "sequence": 1,
                    "title": "Plan and apply memory remediation",
                    "description": finding["recommendation"],
                    "task_type": "memory_finding.remediate",
                    "agent_id": finding.get("agent_id"),
                    "target_type": "memory_steward_finding",
                    "target_id": finding_id,
                    "risk_level": finding["severity"],
                    "autonomous_allowed": True,
                    "action_payload": {"finding_id": finding_id},
                }
            ],
        )
        await self._record(
            "autonomous_plan.created",
            actor=actor,
            resource_id=plan["id"],
            metadata={"source_type": "memory_steward_finding", "source_id": finding_id},
        )
        return {"created": True, "plan": plan}

    async def execute_ready_plans(
        self,
        *,
        actor: str = "autonomous_planner",
        limit: int = 50,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(limit, 200))
        plans = await self.list_plans(
            statuses=self.EXECUTABLE_PLAN_STATUSES,
            limit=safe_limit,
            include_tasks=False,
        )
        results = []
        for plan in plans:
            results.append(await self.execute_plan(plan["id"], actor=actor))
        counts = self._execution_counts(results)
        return {
            "executed_at": utc_now().isoformat(),
            "actor": actor,
            "plans_reviewed": len(plans),
            "plans": results,
            **counts,
        }

    async def execute_plan(
        self,
        plan_id: str,
        *,
        actor: str = "autonomous_planner",
    ) -> dict[str, Any]:
        plan = await self.get_plan(plan_id)
        if not plan:
            raise ValueError(f"Autonomous plan {plan_id} not found")
        if plan["status"] not in self.EXECUTABLE_PLAN_STATUSES:
            return {"plan": plan, "tasks": [], "status": plan["status"]}

        await self._update_plan_status(plan_id, "running")
        task_results = []
        for task in plan["tasks"]:
            if task["status"] == "completed":
                continue
            if task["status"] not in self.EXECUTABLE_TASK_STATUSES:
                break
            result = await self.execute_task(task["id"], actor=actor)
            task_results.append(result)
            if result["status"] in {"waiting_approval", "blocked", "failed"}:
                break

        refreshed = await self._refresh_plan_status(plan_id)
        await self._record(
            "autonomous_plan.executed",
            actor=actor,
            resource_id=plan_id,
            outcome=refreshed["status"],
            metadata={
                "task_results": [
                    {
                        "task_id": result["task_id"],
                        "status": result["status"],
                        "approval_id": result.get("approval_id"),
                    }
                    for result in task_results
                ]
            },
        )
        return {
            "plan": refreshed,
            "tasks": task_results,
            "status": refreshed["status"],
        }

    async def execute_task(
        self,
        task_id: str,
        *,
        actor: str = "autonomous_planner",
    ) -> dict[str, Any]:
        task = await self._get_task(task_id)
        if not task:
            raise ValueError(f"Autonomous task {task_id} not found")
        if task["status"] == "completed":
            return {"task_id": task_id, "status": "completed", "result": task["result"]}

        await self._update_task(task_id, status="running")
        try:
            if task["task_type"] == "role_gap.propose":
                result = await self._execute_role_gap_propose(task, actor)
            elif task["task_type"] == "role_gap.apply":
                result = await self._execute_role_gap_apply(task, actor)
            elif task["task_type"] == "memory_finding.remediate":
                result = await self._execute_memory_remediation(task, actor)
            else:
                raise ValueError(f"Unsupported autonomous task type: {task['task_type']}")
        except Exception as exc:
            await self._update_task(task_id, status="failed", error=str(exc), completed=True)
            await self._record(
                "autonomous_task.failed",
                actor=actor,
                resource_id=task_id,
                outcome="failed",
                metadata={"task_type": task["task_type"], "error": str(exc)},
            )
            return {
                "task_id": task_id,
                "status": "failed",
                "error": str(exc),
            }

        await self._record(
            f"autonomous_task.{result['status']}",
            actor=actor,
            resource_id=task_id,
            outcome=result["status"],
            metadata={
                "task_type": task["task_type"],
                "target_type": task.get("target_type"),
                "target_id": task.get("target_id"),
                "approval_id": result.get("approval_id"),
            },
        )
        return {"task_id": task_id, **result}

    async def list_plans(
        self,
        *,
        status: str | None = None,
        statuses: set[str] | None = None,
        source_type: str | None = None,
        limit: int = 50,
        include_tasks: bool = True,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 200))
        async with self._session_factory() as session:
            query = select(AutonomousPlan)
            if include_tasks:
                query = query.options(selectinload(AutonomousPlan.tasks))
            if status:
                query = query.where(AutonomousPlan.status == status)
            if statuses:
                query = query.where(AutonomousPlan.status.in_(statuses))
            if source_type:
                query = query.where(AutonomousPlan.source_type == source_type)
            result = await session.execute(
                query.order_by(desc(AutonomousPlan.created_at)).limit(safe_limit)
            )
            return [
                self._plan_to_dict(plan, include_tasks=include_tasks)
                for plan in result.scalars().all()
            ]

    async def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(AutonomousPlan)
                .options(selectinload(AutonomousPlan.tasks))
                .where(AutonomousPlan.id == plan_id)
            )
            plan = result.scalar_one_or_none()
            return self._plan_to_dict(plan) if plan else None

    async def _execute_role_gap_propose(
        self,
        task: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        gap_id = task["target_id"]
        gap = await self._get_role_gap(gap_id)
        if not gap:
            return await self._finish_task(
                task["id"],
                "blocked",
                error=f"Role gap {gap_id} not found",
            )
        if gap.get("proposed_role"):
            return await self._finish_task(
                task["id"],
                "completed",
                result={"already_proposed": True, "gap_id": gap_id},
            )
        proposed = await self._agent_manager.propose_role_for_gap(gap_id)
        return await self._finish_task(
            task["id"],
            "completed",
            result={
                "gap_id": gap_id,
                "status": proposed.get("status"),
                "role_name": (
                    proposed.get("proposed_role", {})
                    .get("manifest_payload", {})
                    .get("name")
                ),
            },
        )

    async def _execute_role_gap_apply(
        self,
        task: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        gap_id = task["target_id"]
        gap = await self._get_role_gap(gap_id)
        if not gap:
            return await self._finish_task(
                task["id"],
                "blocked",
                error=f"Role gap {gap_id} not found",
            )
        if gap["status"] == "resolved":
            return await self._finish_task(
                task["id"],
                "completed",
                result={"already_resolved": True, "gap_id": gap_id},
            )

        approval_id = task.get("approval_id") or (gap.get("resolution") or {}).get(
            "pending_approval_id"
        )
        if approval_id:
            executable = await self._agent_manager.approval_is_executable(
                approval_id,
                target_type="role_gap",
                target_id=gap_id,
            )
            if not executable:
                return await self._finish_task(
                    task["id"],
                    "waiting_approval",
                    approval_id=approval_id,
                    result={"approval_id": approval_id, "gap_id": gap_id},
                )

        result = await self._agent_manager.apply_role_gap_proposal(
            gap_id,
            approval_id=approval_id,
            requested_by=actor,
        )
        if result.get("approval_required"):
            return await self._finish_task(
                task["id"],
                "waiting_approval",
                approval_id=result.get("approval_id"),
                result={
                    "gap_id": gap_id,
                    "approval_id": result.get("approval_id"),
                    "high_risk_tools": result.get("high_risk_tools", []),
                },
            )
        if result.get("status") == "resolved":
            return await self._finish_task(
                task["id"],
                "completed",
                result={
                    "gap_id": gap_id,
                    "agent_id": (result.get("resolution") or {}).get("agent_id"),
                    "role_name": (result.get("resolution") or {}).get("role_name"),
                },
            )
        return await self._finish_task(
            task["id"],
            "blocked",
            result={"gap_id": gap_id, "status": result.get("status")},
            error="Role gap proposal did not resolve or request approval.",
        )

    async def _execute_memory_remediation(
        self,
        task: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        finding_id = task["target_id"]
        finding = await self._memory_steward.get_finding(finding_id)
        if not finding:
            return await self._finish_task(
                task["id"],
                "blocked",
                error=f"Memory steward finding {finding_id} not found",
            )
        if finding["status"] == "resolved":
            return await self._finish_task(
                task["id"],
                "completed",
                result={"already_resolved": True, "finding_id": finding_id},
            )

        await self._memory_steward.plan_remediations(
            actor=actor,
            apply_safe_actions=True,
            request_approvals=True,
            limit=100,
        )
        finding = await self._memory_steward.get_finding(finding_id)
        remediation = ((finding or {}).get("metadata") or {}).get("remediation_plan") or {}
        status = remediation.get("status")
        approval_id = remediation.get("approval_id")
        if status in {"applied", "already_applied"}:
            return await self._finish_task(
                task["id"],
                "completed",
                approval_id=approval_id,
                result={"finding_id": finding_id, "remediation_plan": remediation},
            )
        if status in {"approval_requested", "approval_pending"} and approval_id:
            return await self._finish_task(
                task["id"],
                "waiting_approval",
                approval_id=approval_id,
                result={"finding_id": finding_id, "remediation_plan": remediation},
            )
        return await self._finish_task(
            task["id"],
            "blocked",
            result={"finding_id": finding_id, "remediation_plan": remediation},
            error=remediation.get("reason") or "No executable memory remediation was produced.",
        )

    async def _finish_task(
        self,
        task_id: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        await self._update_task(
            task_id,
            status=status,
            result=result or {},
            error=error,
            approval_id=approval_id,
            completed=status in {"completed", "blocked", "failed"},
        )
        return {
            "status": status,
            "result": result or {},
            "error": error,
            "approval_id": approval_id,
        }

    async def _load_role_gaps(self, limit: int) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(RoleGap)
                .where(RoleGap.status.in_(self.ROLE_GAP_STATUSES))
                .order_by(desc(RoleGap.created_at))
                .limit(limit)
            )
            return [self._role_gap_to_dict(gap) for gap in result.scalars().all()]

    async def _load_memory_findings(self, limit: int) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(MemoryStewardFinding)
                .where(MemoryStewardFinding.status.in_(self.MEMORY_FINDING_STATUSES))
                .order_by(desc(MemoryStewardFinding.updated_at))
                .limit(limit)
            )
            return [self._memory_finding_to_dict(finding) for finding in result.scalars().all()]

    async def _get_role_gap(self, gap_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            result = await session.execute(select(RoleGap).where(RoleGap.id == gap_id))
            gap = result.scalar_one_or_none()
            return self._role_gap_to_dict(gap) if gap else None

    async def _get_memory_finding(self, finding_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(MemoryStewardFinding).where(MemoryStewardFinding.id == finding_id)
            )
            finding = result.scalar_one_or_none()
            return self._memory_finding_to_dict(finding) if finding else None

    async def _get_task(self, task_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(AutonomousTask).where(AutonomousTask.id == task_id)
            )
            task = result.scalar_one_or_none()
            return self._task_to_dict(task) if task else None

    async def _find_active_plan(
        self,
        source_type: str,
        source_id: str,
    ) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(AutonomousPlan)
                .options(selectinload(AutonomousPlan.tasks))
                .where(
                    AutonomousPlan.source_type == source_type,
                    AutonomousPlan.source_id == source_id,
                    AutonomousPlan.status.in_(self.ACTIVE_PLAN_STATUSES),
                )
                .order_by(desc(AutonomousPlan.created_at))
            )
            plan = result.scalars().first()
            return self._plan_to_dict(plan) if plan else None

    async def _create_plan(
        self,
        *,
        title: str,
        objective: str,
        source_type: str,
        source_id: str,
        priority: str,
        created_by: str,
        context: dict[str, Any],
        task_specs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        now = utc_now()
        plan_id = f"plan_{uuid.uuid4().hex[:12]}"
        async with self._session_factory() as session:
            plan = AutonomousPlan(
                id=plan_id,
                title=title[:200],
                objective=objective,
                source_type=source_type,
                source_id=source_id,
                status="planned",
                priority=priority,
                created_by=created_by,
                context=context,
                summary={},
                created_at=now,
                updated_at=now,
            )
            session.add(plan)
            for spec in task_specs:
                task = AutonomousTask(
                    id=f"task_{uuid.uuid4().hex[:12]}",
                    plan_id=plan_id,
                    sequence=spec["sequence"],
                    title=spec["title"][:200],
                    description=spec["description"],
                    task_type=spec["task_type"],
                    status="planned",
                    agent_id=spec.get("agent_id"),
                    target_type=spec.get("target_type"),
                    target_id=spec.get("target_id"),
                    action_payload=spec.get("action_payload") or {},
                    result={},
                    error=None,
                    approval_id=None,
                    autonomous_allowed=bool(spec.get("autonomous_allowed", True)),
                    risk_level=spec.get("risk_level") or "low",
                    created_at=now,
                    updated_at=now,
                )
                session.add(task)
            await session.commit()
        plan = await self.get_plan(plan_id)
        if not plan:
            raise RuntimeError(f"Failed to create autonomous plan {plan_id}")
        return plan

    async def _update_task(
        self,
        task_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        approval_id: str | None = None,
        completed: bool = False,
    ) -> None:
        now = utc_now()
        async with self._session_factory() as session:
            db_task = (
                await session.execute(
                    select(AutonomousTask).where(AutonomousTask.id == task_id)
                )
            ).scalar_one()
            db_task.status = status
            db_task.updated_at = now
            if result is not None:
                db_task.result = result
            if error is not None or status != "running":
                db_task.error = error
            if approval_id:
                db_task.approval_id = approval_id
            if completed:
                db_task.completed_at = now
            await session.commit()

    async def _update_plan_status(self, plan_id: str, status: str) -> None:
        async with self._session_factory() as session:
            db_plan = (
                await session.execute(
                    select(AutonomousPlan).where(AutonomousPlan.id == plan_id)
                )
            ).scalar_one()
            db_plan.status = status
            db_plan.updated_at = utc_now()
            await session.commit()

    async def _refresh_plan_status(self, plan_id: str) -> dict[str, Any]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(AutonomousPlan)
                .options(selectinload(AutonomousPlan.tasks))
                .where(AutonomousPlan.id == plan_id)
            )
            plan = result.scalar_one()
            statuses = [task.status for task in plan.tasks]
            if statuses and all(status == "completed" for status in statuses):
                plan.status = "completed"
                plan.completed_at = utc_now()
            elif any(status == "failed" for status in statuses):
                plan.status = "failed"
                plan.completed_at = utc_now()
            elif any(status == "waiting_approval" for status in statuses):
                plan.status = "waiting_approval"
            elif any(status == "blocked" for status in statuses):
                plan.status = "blocked"
            else:
                plan.status = "planned"
            plan.summary = self._plan_summary(plan.tasks)
            plan.updated_at = utc_now()
            await session.commit()
            return self._plan_to_dict(plan)

    async def _record(
        self,
        event_type: str,
        *,
        actor: str,
        resource_id: str | None,
        outcome: str = "success",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self._audit:
            return
        await self._audit.record(
            event_type=event_type,
            actor=actor,
            actor_type="agent",
            resource_type="autonomous_plan",
            resource_id=resource_id,
            action="run",
            outcome=outcome,
            metadata=metadata or {},
        )

    @staticmethod
    def _execution_counts(results: list[dict[str, Any]]) -> dict[str, int]:
        counts = {
            "plans_completed": 0,
            "plans_waiting_approval": 0,
            "plans_blocked": 0,
            "plans_failed": 0,
        }
        for result in results:
            status = result.get("status")
            if status == "completed":
                counts["plans_completed"] += 1
            elif status == "waiting_approval":
                counts["plans_waiting_approval"] += 1
            elif status == "blocked":
                counts["plans_blocked"] += 1
            elif status == "failed":
                counts["plans_failed"] += 1
        return counts

    @staticmethod
    def _plan_summary(tasks: list[AutonomousTask]) -> dict[str, int]:
        summary = {
            "task_count": len(tasks),
            "completed": 0,
            "waiting_approval": 0,
            "blocked": 0,
            "failed": 0,
        }
        for task in tasks:
            if task.status in summary:
                summary[task.status] += 1
        return summary

    @staticmethod
    def _plan_to_dict(
        plan: AutonomousPlan,
        *,
        include_tasks: bool = True,
    ) -> dict[str, Any]:
        response = {
            "id": plan.id,
            "title": plan.title,
            "objective": plan.objective,
            "source_type": plan.source_type,
            "source_id": plan.source_id,
            "status": plan.status,
            "priority": plan.priority,
            "created_by": plan.created_by,
            "context": plan.context or {},
            "summary": plan.summary or {},
            "created_at": plan.created_at.isoformat(),
            "updated_at": plan.updated_at.isoformat(),
            "completed_at": plan.completed_at.isoformat() if plan.completed_at else None,
        }
        if include_tasks:
            response["tasks"] = [
                AutonomousPlanningService._task_to_dict(task)
                for task in sorted(plan.tasks, key=lambda item: item.sequence)
            ]
        return response

    @staticmethod
    def _task_to_dict(task: AutonomousTask) -> dict[str, Any]:
        return {
            "id": task.id,
            "plan_id": task.plan_id,
            "sequence": task.sequence,
            "title": task.title,
            "description": task.description,
            "task_type": task.task_type,
            "status": task.status,
            "agent_id": task.agent_id,
            "target_type": task.target_type,
            "target_id": task.target_id,
            "action_payload": task.action_payload or {},
            "result": task.result or {},
            "error": task.error,
            "approval_id": task.approval_id,
            "autonomous_allowed": task.autonomous_allowed,
            "risk_level": task.risk_level,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        }

    @staticmethod
    def _role_gap_to_dict(gap: RoleGap) -> dict[str, Any]:
        return {
            "id": gap.id,
            "title": gap.title,
            "description": gap.description,
            "status": gap.status,
            "severity": gap.severity,
            "source_agent_id": gap.source_agent_id,
            "source_type": gap.source_type,
            "company_namespace": gap.company_namespace,
            "capability": gap.capability,
            "requested_tools": gap.requested_tools or [],
            "context": gap.context or {},
            "proposed_role": gap.proposed_role or {},
            "resolution": gap.resolution or {},
            "created_at": gap.created_at.isoformat(),
            "updated_at": gap.updated_at.isoformat(),
            "resolved_at": gap.resolved_at.isoformat() if gap.resolved_at else None,
        }

    @staticmethod
    def _memory_finding_to_dict(finding: MemoryStewardFinding) -> dict[str, Any]:
        return {
            "id": finding.id,
            "finding_type": finding.finding_type,
            "severity": finding.severity,
            "status": finding.status,
            "agent_id": finding.agent_id,
            "memory_namespace": finding.memory_namespace,
            "company_namespace": finding.company_namespace,
            "title": finding.title,
            "description": finding.description,
            "recommendation": finding.recommendation,
            "trace_ids": finding.trace_ids or [],
            "evidence": finding.evidence or {},
            "metadata": finding.metadata_ or {},
            "created_at": finding.created_at.isoformat(),
            "updated_at": finding.updated_at.isoformat(),
            "resolved_at": finding.resolved_at.isoformat() if finding.resolved_at else None,
        }

    @staticmethod
    def _error(source_type: str, source_id: str, exc: Exception) -> dict[str, str]:
        return {
            "source_type": source_type,
            "source_id": source_id,
            "type": type(exc).__name__,
            "message": str(exc),
        }
