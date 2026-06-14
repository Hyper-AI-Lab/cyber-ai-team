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
    CompanyContextSnapshot,
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
    RISK_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    OWNER_REVIEW_MIN_RISK = "medium"

    def __init__(
        self,
        *,
        agent_manager,
        memory_steward_service,
        tool_registry=None,
        audit_service=None,
        company_context_service=None,
        session_factory=async_session,
    ):
        self._agent_manager = agent_manager
        self._memory_steward = memory_steward_service
        self._tool_registry = tool_registry
        self._audit = audit_service
        self._metrics = getattr(audit_service, "_metrics", None)
        self._company_context = company_context_service
        self._session_factory = session_factory

    def set_company_context_service(self, service) -> None:
        self._company_context = service

    async def scan_and_plan(
        self,
        *,
        actor: str = "autonomous_planner",
        include_role_gaps: bool = True,
        include_memory_findings: bool = True,
        include_company_context: bool = True,
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

        if include_company_context:
            for snapshot in await self._load_company_context_snapshots(safe_limit):
                try:
                    result = await self.create_plan_from_company_context_snapshot(
                        snapshot["id"],
                        actor=actor,
                    )
                    (created if result["created"] else existing).append(result["plan"])
                except Exception as exc:
                    errors.append(self._error("company_context_snapshot", snapshot["id"], exc))

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

        policy = self._role_gap_policy(gap)
        task_specs = []
        task_specs.append(
            self._task_spec(
                "Assess role gap risk",
                "Evaluate severity, requested tools, and execution policy before acting.",
                "plan.risk_assess",
                "role_gap",
                gap_id,
                "low",
                {"policy": policy, "source_type": "role_gap", "source_id": gap_id},
            )
        )
        if gap.get("requested_tools"):
            task_specs.append(
                self._task_spec(
                    "Check requested tool readiness",
                    "Verify requested tools exist and identify approval-gated capabilities.",
                    "tools.readiness_check",
                    "role_gap",
                    gap_id,
                    "low",
                    {
                        "requested_tools": gap.get("requested_tools", []),
                        "policy": policy,
                    },
                )
            )
        if not gap.get("proposed_role"):
            task_specs.append(
                self._task_spec(
                    "Propose missing role",
                    f"Generate a role proposal for: {gap['title']}",
                    "role_gap.propose",
                    "role_gap",
                    gap_id,
                    "low",
                    {"gap_id": gap_id},
                )
            )
        apply_task = self._task_spec(
            "Apply role proposal",
            f"Instantiate the reviewed role proposal for: {gap['title']}",
            "role_gap.apply",
            "role_gap",
            gap_id,
            policy["max_risk"],
            {"gap_id": gap_id, "policy": policy},
        )
        task_specs.extend(self._with_owner_review(apply_task, policy))

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
                "policy": policy,
            },
            task_specs=self._number_task_specs(task_specs),
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

        policy = self._memory_finding_policy(finding)
        remediation_task = self._task_spec(
            "Plan and apply memory remediation",
            finding["recommendation"],
            "memory_finding.remediate",
            "memory_steward_finding",
            finding_id,
            policy["max_risk"],
            {"finding_id": finding_id, "policy": policy},
            agent_id=finding.get("agent_id"),
        )
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
                "policy": policy,
            },
            task_specs=self._number_task_specs([
                self._task_spec(
                    "Assess memory finding risk",
                    "Evaluate severity and remediation policy before changing memory.",
                    "plan.risk_assess",
                    "memory_steward_finding",
                    finding_id,
                    "low",
                    {
                        "policy": policy,
                        "source_type": "memory_steward_finding",
                        "source_id": finding_id,
                    },
                ),
                *self._with_owner_review(remediation_task, policy),
            ]),
        )
        await self._record(
            "autonomous_plan.created",
            actor=actor,
            resource_id=plan["id"],
            metadata={"source_type": "memory_steward_finding", "source_id": finding_id},
        )
        return {"created": True, "plan": plan}

    async def create_plan_from_company_context_snapshot(
        self,
        snapshot_id: str,
        *,
        actor: str = "autonomous_planner",
    ) -> dict[str, Any]:
        existing = await self._find_active_plan("company_context_snapshot", snapshot_id)
        if existing:
            return {"created": False, "plan": existing}
        if not self._company_context:
            raise ValueError("Company context sync service is not available")

        assessment = await self._company_context.assess_snapshot(snapshot_id)
        unsafe_count = assessment["unsafe_role_count"]
        policy = {
            "policy_version": "planner-company-context-v1",
            "source_type": "company_context_snapshot",
            "source_id": snapshot_id,
            "max_risk": "medium" if unsafe_count else "low",
            "owner_review_required": unsafe_count > 0,
            "review_reasons": (
                [f"{unsafe_count} role specs require owner review before creation"]
                if unsafe_count
                else []
            ),
            "blockers": [],
            "autonomous_execution": "owner_review" if unsafe_count else "auto",
        }
        task_specs = [
            self._task_spec(
                "Assess ERPNext company context",
                "Summarize synced ERPNext context, freshness, role coverage, and gaps.",
                "company_context.assess",
                "company_context_snapshot",
                snapshot_id,
                "low",
                {"snapshot_id": snapshot_id, "policy": policy},
            ),
            self._task_spec(
                "Seed company memory from ERPNext",
                "Write durable memory entries for the normalized ERPNext company profile.",
                "company_context.seed_memory",
                "company_context_snapshot",
                snapshot_id,
                "low",
                {"snapshot_id": snapshot_id, "policy": policy},
            ),
            self._task_spec(
                "Apply low-risk internal roles",
                "Create only auto-policy roles whose tools are ready and non-side-effectful.",
                "company_context.apply_low_risk_roles",
                "company_context_snapshot",
                snapshot_id,
                "low",
                {"snapshot_id": snapshot_id, "policy": policy},
            ),
            self._task_spec(
                "Report risky role backlog",
                "Convert side-effectful or approval-gated role specs into explicit role gaps.",
                "company_context.report_risky_roles",
                "company_context_snapshot",
                snapshot_id,
                "low",
                {"snapshot_id": snapshot_id, "policy": policy},
            ),
        ]
        if unsafe_count:
            task_specs.append(
                self._task_spec(
                    "Owner review: risky ERPNext-derived roles",
                    "Review role gaps created for side-effectful or approval-gated specialists.",
                    "plan.owner_review",
                    "company_context_snapshot",
                    snapshot_id,
                    "medium",
                    {
                        "review_for": "company_context.report_risky_roles",
                        "review_title": "Risky ERPNext-derived role backlog",
                        "review_description": (
                            "ERPNext context implies specialist roles that use side-effectful "
                            "or approval-gated tools. Review their generated role gaps before "
                            "creating live agents."
                        ),
                        "source_type": "company_context_snapshot",
                        "source_id": snapshot_id,
                        "policy": policy,
                        "review_reasons": policy["review_reasons"],
                        "target_payload": {"snapshot_id": snapshot_id},
                    },
                    autonomous_allowed=False,
                )
            )

        plan = await self._create_plan(
            title="Apply ERPNext company context",
            objective=(
                "Turn synced ERPNext setup and business records into Cyber-Team memory, "
                "safe internal roles, and reviewable role-gap backlog."
            ),
            source_type="company_context_snapshot",
            source_id=snapshot_id,
            priority="medium" if unsafe_count else "low",
            created_by=actor,
            context={
                "company_namespace": assessment["company_namespace"],
                "source_hash": assessment["source_hash"],
                "counts": assessment["counts"],
                "policy": policy,
            },
            task_specs=self._number_task_specs(task_specs),
        )
        await self._record(
            "autonomous_plan.created",
            actor=actor,
            resource_id=plan["id"],
            metadata={"source_type": "company_context_snapshot", "source_id": snapshot_id},
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
            if task["task_type"] == "plan.risk_assess":
                result = await self._execute_risk_assessment(task)
            elif task["task_type"] == "tools.readiness_check":
                result = await self._execute_tool_readiness(task)
            elif task["task_type"] == "plan.owner_review":
                result = await self._execute_owner_review(task, actor)
            elif task["task_type"] == "role_gap.propose":
                result = await self._execute_role_gap_propose(task, actor)
            elif task["task_type"] == "role_gap.apply":
                result = await self._execute_role_gap_apply(task, actor)
            elif task["task_type"] == "memory_finding.remediate":
                result = await self._execute_memory_remediation(task, actor)
            elif task["task_type"] == "company_context.assess":
                result = await self._execute_company_context_assess(task)
            elif task["task_type"] == "company_context.seed_memory":
                result = await self._execute_company_context_seed_memory(task, actor)
            elif task["task_type"] == "company_context.apply_low_risk_roles":
                result = await self._execute_company_context_apply_low_risk_roles(
                    task,
                    actor,
                )
            elif task["task_type"] == "company_context.report_risky_roles":
                result = await self._execute_company_context_report_risky_roles(
                    task,
                    actor,
                )
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

    async def _execute_risk_assessment(self, task: dict[str, Any]) -> dict[str, Any]:
        policy = task["action_payload"].get("policy") or {}
        return await self._finish_task(
            task["id"],
            "completed",
            result={
                "policy": policy,
                "max_risk": policy.get("max_risk", task.get("risk_level", "low")),
                "owner_review_required": policy.get("owner_review_required", False),
                "review_reasons": policy.get("review_reasons", []),
                "blockers": policy.get("blockers", []),
            },
        )

    async def _execute_tool_readiness(self, task: dict[str, Any]) -> dict[str, Any]:
        requested_tools = task["action_payload"].get("requested_tools") or []
        readiness = self._tool_readiness(requested_tools)
        if not readiness["registry_available"]:
            return await self._finish_task(
                task["id"],
                "blocked",
                result={"tool_readiness": readiness},
                error="Tool registry is not available for readiness checks.",
            )
        if readiness["missing_tools"]:
            return await self._finish_task(
                task["id"],
                "blocked",
                result={"tool_readiness": readiness},
                error="Requested tools are not registered: "
                + ", ".join(readiness["missing_tools"]),
            )
        if readiness["unavailable_tools"]:
            return await self._finish_task(
                task["id"],
                "blocked",
                result={"tool_readiness": readiness},
                error="Requested tools are not ready: "
                + ", ".join(
                    tool["name"] for tool in readiness["unavailable_tools"]
                ),
            )
        return await self._finish_task(
            task["id"],
            "completed",
            result={"tool_readiness": readiness},
        )

    async def _execute_owner_review(
        self,
        task: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        approval_id = task.get("approval_id")
        if approval_id:
            executable = await self._agent_manager.approval_is_executable(
                approval_id,
                target_type="autonomous_task",
                target_id=task["id"],
            )
            if not executable:
                return await self._finish_task(
                    task["id"],
                    "waiting_approval",
                    approval_id=approval_id,
                    result={
                        "approval_id": approval_id,
                        "review_status": "pending",
                        "review_for": task["action_payload"].get("review_for"),
                    },
                )
            await self._agent_manager.consume_approval(
                approval_id,
                consumer="autonomous_planner",
                target_type="autonomous_task",
                target_id=task["id"],
            )
            return await self._finish_task(
                task["id"],
                "completed",
                approval_id=approval_id,
                result={
                    "approval_id": approval_id,
                    "review_status": "approved",
                    "review_for": task["action_payload"].get("review_for"),
                },
            )

        if not hasattr(self._agent_manager, "_request_approval"):
            return await self._finish_task(
                task["id"],
                "blocked",
                error="Approval service is not available for owner review.",
            )

        payload = {
            **task["action_payload"],
            "plan_id": task["plan_id"],
            "review_task_id": task["id"],
            "risk_level": task["risk_level"],
        }
        description = self._owner_review_description(task)
        requested_id = await self._agent_manager._request_approval(
            None,
            "autonomous_task.review",
            description,
            payload,
            requester=actor,
            requester_type="agent",
            risk_level=task["risk_level"],
            target_type="autonomous_task",
            target_id=task["id"],
            expires_in_minutes=1440,
        )
        return await self._finish_task(
            task["id"],
            "waiting_approval",
            approval_id=requested_id,
            result={
                "approval_id": requested_id,
                "review_status": "requested",
                "review_for": task["action_payload"].get("review_for"),
            },
        )

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

    async def _execute_company_context_assess(
        self,
        task: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._company_context:
            return await self._finish_task(
                task["id"],
                "blocked",
                error="Company context sync service is not available.",
            )
        result = await self._company_context.assess_snapshot(task["target_id"])
        return await self._finish_task(task["id"], "completed", result=result)

    async def _execute_company_context_seed_memory(
        self,
        task: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        if not self._company_context:
            return await self._finish_task(
                task["id"],
                "blocked",
                error="Company context sync service is not available.",
            )
        result = await self._company_context.seed_snapshot_memory(
            task["target_id"],
            actor=actor,
        )
        return await self._finish_task(task["id"], "completed", result=result)

    async def _execute_company_context_apply_low_risk_roles(
        self,
        task: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        if not self._company_context:
            return await self._finish_task(
                task["id"],
                "blocked",
                error="Company context sync service is not available.",
            )
        result = await self._company_context.apply_snapshot_low_risk_roles(
            task["target_id"],
            actor=actor,
        )
        return await self._finish_task(task["id"], "completed", result=result)

    async def _execute_company_context_report_risky_roles(
        self,
        task: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        if not self._company_context:
            return await self._finish_task(
                task["id"],
                "blocked",
                error="Company context sync service is not available.",
            )
        result = await self._company_context.report_snapshot_risky_role_gaps(
            task["target_id"],
            actor=actor,
        )
        return await self._finish_task(task["id"], "completed", result=result)

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
        if status == "blocked" and self._metrics:
            self._metrics.record_planner_block(
                error or "blocked",
                (result or {}).get("risk_level", "unknown"),
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

    async def _load_company_context_snapshots(self, limit: int) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(CompanyContextSnapshot)
                .where(CompanyContextSnapshot.status == "active")
                .order_by(desc(CompanyContextSnapshot.created_at))
                .limit(limit)
            )
            return [
                self._company_context_snapshot_to_dict(snapshot)
                for snapshot in result.scalars().all()
            ]

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

    def _role_gap_policy(self, gap: dict[str, Any]) -> dict[str, Any]:
        requested_tools = gap.get("requested_tools") or []
        readiness = self._tool_readiness(requested_tools)
        tool_risks = [tool["risk_level"] for tool in readiness["available_tools"]]
        max_risk = self._max_risk([gap.get("severity", "medium"), *tool_risks])
        blockers = []
        if requested_tools and not readiness["registry_available"]:
            blockers.append("tool_registry_unavailable")
        if readiness["missing_tools"]:
            blockers.append("missing_requested_tools")
        review_reasons = []
        if self._risk_requires_owner_review(max_risk):
            review_reasons.append(f"maximum risk is {max_risk}")
        if readiness["approval_gated_tools"]:
            review_reasons.append(
                "requested approval-gated tools: "
                + ", ".join(readiness["approval_gated_tools"])
            )
        if gap.get("severity") in {"high", "critical"}:
            review_reasons.append(f"role gap severity is {gap['severity']}")
        return {
            "policy_version": "planner-risk-v1",
            "source_type": "role_gap",
            "source_id": gap["id"],
            "max_risk": max_risk,
            "owner_review_required": self._risk_requires_owner_review(max_risk),
            "review_reasons": self._unique(review_reasons),
            "blockers": blockers,
            "tool_readiness": readiness,
            "autonomous_execution": (
                "blocked" if blockers else "owner_review" if review_reasons else "auto"
            ),
        }

    def _memory_finding_policy(self, finding: dict[str, Any]) -> dict[str, Any]:
        max_risk = self._normalize_risk(finding.get("severity", "medium"))
        review_reasons = []
        if self._risk_requires_owner_review(max_risk):
            review_reasons.append(f"memory finding severity is {max_risk}")
        if finding.get("finding_type") in {"missing_write", "memory_conflict"}:
            review_reasons.append(f"finding type is {finding['finding_type']}")
        return {
            "policy_version": "planner-risk-v1",
            "source_type": "memory_steward_finding",
            "source_id": finding["id"],
            "max_risk": max_risk,
            "owner_review_required": self._risk_requires_owner_review(max_risk),
            "review_reasons": self._unique(review_reasons),
            "blockers": [],
            "autonomous_execution": "owner_review" if review_reasons else "auto",
        }

    def _tool_readiness(self, requested_tools: list[str]) -> dict[str, Any]:
        requested = self._unique(requested_tools)
        registry_available = self._tool_registry is not None
        available = []
        missing = []
        unavailable = []
        if not registry_available:
            return {
                "registry_available": False,
                "requested_tools": requested,
                "available_tools": [],
                "missing_tools": requested,
                "unavailable_tools": [],
                "approval_gated_tools": [],
                "highest_tool_risk": "low",
            }
        for tool_name in requested:
            tool = self._get_registered_tool(tool_name)
            if not tool:
                missing.append(tool_name)
                continue
            contract = self._tool_contract(tool, tool_name)
            available.append(contract)
            if contract.get("state") not in {None, "live", "advisory"}:
                unavailable.append(contract)
        approval_gated = [
            tool["name"]
            for tool in available
            if tool.get("requires_approval") or self._risk_requires_owner_review(
                tool.get("risk_level", "low")
            )
        ]
        return {
            "registry_available": True,
            "requested_tools": requested,
            "available_tools": available,
            "missing_tools": missing,
            "unavailable_tools": unavailable,
            "approval_gated_tools": approval_gated,
            "highest_tool_risk": self._max_risk(
                [tool.get("risk_level", "low") for tool in available]
            ),
        }

    def _get_registered_tool(self, tool_name: str):
        if hasattr(self._tool_registry, "get_tool"):
            return self._tool_registry.get_tool(tool_name)
        if hasattr(self._tool_registry, "list_tools"):
            for tool in self._tool_registry.list_tools():
                if getattr(tool, "name", None) == tool_name:
                    return tool
        return None

    def _tool_contract(self, tool, fallback_name: str) -> dict[str, Any]:
        if self._tool_registry and hasattr(self._tool_registry, "get_tool_readiness"):
            readiness = self._tool_registry.get_tool_readiness(fallback_name)
        else:
            readiness = {}
        if hasattr(tool, "contract"):
            contract = tool.contract()
            return {
                "name": contract.get("name", fallback_name),
                "category": contract.get("category", "general"),
                "risk_level": self._normalize_risk(contract.get("risk_level", "low")),
                "requires_approval": bool(contract.get("requires_approval", False)),
                "description": contract.get("description", ""),
                "state": readiness.get("state", contract.get("state")),
                "readiness_reason": readiness.get(
                    "readiness_reason",
                    contract.get("readiness_reason"),
                ),
                "side_effects": readiness.get(
                    "side_effects",
                    contract.get("side_effects", False),
                ),
                "requires_configuration": readiness.get(
                    "requires_configuration",
                    contract.get("requires_configuration", False),
                ),
            }
        return {
            "name": getattr(tool, "name", fallback_name),
            "category": getattr(tool, "category", "general"),
            "risk_level": self._normalize_risk(getattr(tool, "risk_level", "low")),
            "requires_approval": bool(getattr(tool, "requires_approval", False)),
            "description": getattr(tool, "description", ""),
            "state": readiness.get("state"),
            "readiness_reason": readiness.get("readiness_reason"),
            "side_effects": readiness.get("side_effects", False),
            "requires_configuration": readiness.get("requires_configuration", False),
        }

    def _with_owner_review(
        self,
        task_spec: dict[str, Any],
        policy: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not policy.get("owner_review_required"):
            return [task_spec]
        review_task = self._task_spec(
            f"Owner review: {task_spec['title']}",
            self._owner_review_description_from_spec(task_spec, policy),
            "plan.owner_review",
            task_spec.get("target_type"),
            task_spec.get("target_id"),
            task_spec.get("risk_level", policy.get("max_risk", "medium")),
            {
                "review_for": task_spec["task_type"],
                "review_title": task_spec["title"],
                "review_description": task_spec["description"],
                "source_type": policy.get("source_type"),
                "source_id": policy.get("source_id"),
                "policy": policy,
                "review_reasons": policy.get("review_reasons", []),
                "target_payload": task_spec.get("action_payload") or {},
            },
            autonomous_allowed=False,
        )
        return [review_task, task_spec]

    @staticmethod
    def _task_spec(
        title: str,
        description: str,
        task_type: str,
        target_type: str | None,
        target_id: str | None,
        risk_level: str,
        action_payload: dict[str, Any],
        *,
        agent_id: str | None = None,
        autonomous_allowed: bool = True,
    ) -> dict[str, Any]:
        return {
            "sequence": 0,
            "title": title,
            "description": description,
            "task_type": task_type,
            "agent_id": agent_id,
            "target_type": target_type,
            "target_id": target_id,
            "risk_level": risk_level,
            "autonomous_allowed": autonomous_allowed,
            "action_payload": action_payload,
        }

    @staticmethod
    def _number_task_specs(task_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                **task_spec,
                "sequence": index,
            }
            for index, task_spec in enumerate(task_specs, start=1)
        ]

    def _owner_review_description(self, task: dict[str, Any]) -> str:
        return self._owner_review_description_from_spec(
            {
                "title": task["action_payload"].get("review_title", task["title"]),
                "description": task["action_payload"].get(
                    "review_description",
                    task["description"],
                ),
                "task_type": task["action_payload"].get("review_for", task["task_type"]),
                "risk_level": task.get("risk_level", "medium"),
            },
            task["action_payload"].get("policy") or {},
        )

    @staticmethod
    def _owner_review_description_from_spec(
        task_spec: dict[str, Any],
        policy: dict[str, Any],
    ) -> str:
        reasons = policy.get("review_reasons") or ["planner policy requires owner review"]
        return (
            f"Review autonomous planner step '{task_spec['title']}' "
            f"({task_spec['task_type']}, risk {task_spec.get('risk_level', 'medium')}). "
            f"Reasons: {', '.join(reasons)}. "
            "Approving this lets the planner continue to the next task; downstream "
            "high-risk tool grants may still request dedicated approval."
        )

    def _risk_requires_owner_review(self, risk_level: str) -> bool:
        return (
            self.RISK_RANK[self._normalize_risk(risk_level)]
            >= self.RISK_RANK[self.OWNER_REVIEW_MIN_RISK]
        )

    def _max_risk(self, risk_levels: list[str]) -> str:
        normalized = [self._normalize_risk(level) for level in risk_levels if level]
        if not normalized:
            return "low"
        return max(normalized, key=lambda level: self.RISK_RANK[level])

    def _normalize_risk(self, risk_level: str | None) -> str:
        risk = str(risk_level or "medium").lower()
        return risk if risk in self.RISK_RANK else "medium"

    @staticmethod
    def _unique(values) -> list:
        result = []
        for value in values:
            if value and value not in result:
                result.append(value)
        return result

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
    def _company_context_snapshot_to_dict(
        snapshot: CompanyContextSnapshot,
    ) -> dict[str, Any]:
        return {
            "id": snapshot.id,
            "source": snapshot.source,
            "source_id": snapshot.source_id,
            "source_hash": snapshot.source_hash,
            "company_namespace": snapshot.company_namespace,
            "status": snapshot.status,
            "normalized_profile": snapshot.normalized_profile or {},
            "erpnext_summary": snapshot.erpnext_summary or {},
            "operating_model": snapshot.operating_model or {},
            "memory_ids": snapshot.memory_ids or [],
            "agent_ids": snapshot.agent_ids or [],
            "role_manifest_ids": snapshot.role_manifest_ids or [],
            "role_gap_ids": snapshot.role_gap_ids or [],
            "approval_ids": snapshot.approval_ids or [],
            "plan_ids": snapshot.plan_ids or [],
            "errors": snapshot.errors or [],
            "created_by": snapshot.created_by,
            "created_at": snapshot.created_at.isoformat(),
            "applied_at": snapshot.applied_at.isoformat() if snapshot.applied_at else None,
        }

    @staticmethod
    def _error(source_type: str, source_id: str, exc: Exception) -> dict[str, str]:
        return {
            "source_type": source_type,
            "source_id": source_id,
            "type": type(exc).__name__,
            "message": str(exc),
        }
