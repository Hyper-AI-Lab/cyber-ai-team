"""Autonomous planning and execution service."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
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
        include_operating_cadence: bool = True,
        auto_execute: bool = True,
        limit: int = 50,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(limit, 200))
        created = []
        existing = []
        skipped = []
        errors = []
        operating_cadence_status = None

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

        if include_operating_cadence:
            operating_cadence_status = await self.operating_cadence_status(limit=safe_limit)
            for cadence in operating_cadence_status["items"]:
                if not cadence["due"]:
                    skipped.append(cadence)
                    continue
                try:
                    result = await self.create_plan_from_operating_cadence(
                        cadence,
                        actor=actor,
                    )
                    (created if result["created"] else existing).append(result["plan"])
                except Exception as exc:
                    errors.append(self._error("operating_cadence", cadence["cadence_id"], exc))

        execution = None
        if auto_execute:
            execution = await self.execute_ready_plans(actor=actor, limit=safe_limit)

        summary = {
            "scanned_at": utc_now().isoformat(),
            "actor": actor,
            "plans_created": len(created),
            "plans_existing": len(existing),
            "cadences_skipped_not_due": len(skipped),
            "created_plan_ids": [plan["id"] for plan in created],
            "existing_plan_ids": [plan["id"] for plan in existing],
            "operating_cadence_status": operating_cadence_status,
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

    async def create_plan_from_operating_cadence(
        self,
        cadence_item: dict[str, Any],
        *,
        actor: str = "autonomous_planner",
    ) -> dict[str, Any]:
        cadence_id = cadence_item["cadence_id"]
        existing = await self._find_active_plan("operating_cadence", cadence_id)
        if existing:
            return {"created": False, "plan": existing}

        refreshed_status = await self.operating_cadence_status(
            company_namespace=cadence_item.get("company_namespace"),
            limit=200,
        )
        refreshed = next(
            (
                item
                for item in refreshed_status["items"]
                if item["cadence_id"] == cadence_id
            ),
            cadence_item,
        )
        if not refreshed.get("due"):
            latest = refreshed.get("last_plan")
            if latest:
                return {"created": False, "plan": latest}
            raise ValueError(f"Operating cadence {cadence_id} is not due")

        cadence = refreshed.get("cadence") or cadence_item.get("cadence") or {}
        policy = self._operating_cadence_policy(refreshed)
        task_specs = self._number_task_specs([
            self._task_spec(
                "Assess operating cadence signals",
                (
                    "Inspect the active role cadence, current due state, and "
                    "available backlog/context signals."
                ),
                "operating_cadence.assess",
                "operating_cadence",
                cadence_id,
                "low",
                {"cadence": refreshed, "policy": policy},
                agent_id=refreshed.get("agent_id"),
            ),
            self._task_spec(
                "Prepare owner operating review",
                (
                    "Create an owner-visible review brief and checklist without "
                    "executing external side effects."
                ),
                "operating_cadence.prepare_review",
                "operating_cadence",
                cadence_id,
                "low",
                {"cadence": refreshed, "policy": policy},
                agent_id=refreshed.get("agent_id"),
            ),
            self._task_spec(
                "Record operating-loop next actions",
                (
                    "Record safe internal next actions and explicit approval "
                    "requirements for any downstream external mutations."
                ),
                "operating_cadence.record_next_actions",
                "operating_cadence",
                cadence_id,
                "low",
                {"cadence": refreshed, "policy": policy},
                agent_id=refreshed.get("agent_id"),
            ),
        ])
        plan = await self._create_plan(
            title=f"Run operating cadence: {refreshed.get('role_name') or cadence_id}",
            objective=(
                "Review the role's configured operating cadence, prepare an owner "
                "brief, and keep side effects manual-only."
            ),
            source_type="operating_cadence",
            source_id=cadence_id,
            priority="low",
            created_by=actor,
            context={
                "agent_id": refreshed.get("agent_id"),
                "role_name": refreshed.get("role_name"),
                "role_family": refreshed.get("role_family"),
                "company_namespace": refreshed.get("company_namespace"),
                "frequency": cadence.get("frequency"),
                "review_window": cadence.get("review_window"),
                "policy": policy,
            },
            task_specs=task_specs,
        )
        await self._record(
            "autonomous_plan.created",
            actor=actor,
            resource_id=plan["id"],
            metadata={"source_type": "operating_cadence", "source_id": cadence_id},
        )
        return {"created": True, "plan": plan}

    async def operating_cadence_status(
        self,
        *,
        company_namespace: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        if not hasattr(self._agent_manager, "role_operating_cadence"):
            return {
                "generated_at": utc_now().isoformat(),
                "company_namespace": company_namespace,
                "status": "unavailable",
                "detail": "Agent manager does not expose operating cadence data.",
                "counts": {
                    "cadences": 0,
                    "due": 0,
                    "not_due": 0,
                    "active_plans": 0,
                },
                "items": [],
            }

        cadence_summary = await self._agent_manager.role_operating_cadence(
            company_namespace=company_namespace,
        )
        safe_limit = max(1, min(limit, 500))
        now = utc_now()
        items = []
        for entry in (cadence_summary.get("cadences") or [])[:safe_limit]:
            cadence = entry.get("cadence") or {}
            cadence_id = cadence.get("cadence_id") or f"cadence:agent:{entry['agent_id']}"
            latest_plan = await self._latest_plan_for_source(
                "operating_cadence",
                cadence_id,
            )
            interval = self._cadence_interval(cadence.get("frequency"))
            baseline = self._plan_baseline_at(latest_plan)
            active_plan = bool(
                latest_plan
                and latest_plan.get("status") in self.ACTIVE_PLAN_STATUSES
            )
            due = not active_plan and (
                baseline is None or baseline + interval <= now
            )
            next_due_at = None
            if active_plan:
                next_due_at = None
            elif baseline:
                next_due_at = (baseline + interval).isoformat()
            item_state = "active_plan" if active_plan else "due" if due else "not_due"
            items.append(
                {
                    "cadence_id": cadence_id,
                    "agent_id": entry.get("agent_id"),
                    "role_name": entry.get("role_name"),
                    "role_family": entry.get("role_family"),
                    "status": entry.get("status"),
                    "company_namespace": entry.get("company_namespace"),
                    "frequency": cadence.get("frequency") or "weekly",
                    "review_window": cadence.get("review_window") or "Operating review",
                    "signals": cadence.get("signals") or [],
                    "checklist": cadence.get("checklist") or [],
                    "cadence": cadence,
                    "source_role_gap_id": entry.get("source_role_gap_id"),
                    "source_snapshot_id": entry.get("source_snapshot_id"),
                    "last_plan": latest_plan,
                    "due": due,
                    "state": item_state,
                    "due_reason": self._cadence_due_reason(
                        due=due,
                        active_plan=active_plan,
                        latest_plan=latest_plan,
                        frequency=cadence.get("frequency"),
                    ),
                    "next_due_at": next_due_at,
                    "interval_seconds": int(interval.total_seconds()),
                }
            )

        counts = {
            "cadences": len(items),
            "due": len([item for item in items if item["due"]]),
            "not_due": len([item for item in items if item["state"] == "not_due"]),
            "active_plans": len([item for item in items if item["state"] == "active_plan"]),
        }
        return {
            "generated_at": now.isoformat(),
            "company_namespace": company_namespace,
            "status": "ready",
            "counts": counts,
            "items": items,
            "recommended_owner_actions": cadence_summary.get("recommended_owner_actions", []),
        }

    async def list_operating_follow_ups(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        target_view: str | None = None,
        company_namespace: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return owner-reviewable follow-up plans created by operating cadences."""

        safe_limit = max(1, min(limit, 500))
        status_filter = self._normalize_follow_up_status_filter(status)
        scan_limit = 500 if any([kind, target_view, company_namespace]) else safe_limit
        async with self._session_factory() as session:
            query = (
                select(AutonomousPlan)
                .options(selectinload(AutonomousPlan.tasks))
                .where(AutonomousPlan.source_type == "operating_cadence_follow_up")
            )
            db_statuses = self._db_statuses_for_follow_up_filter(status_filter)
            if db_statuses:
                query = query.where(AutonomousPlan.status.in_(db_statuses))
            result = await session.execute(
                query.order_by(desc(AutonomousPlan.updated_at)).limit(scan_limit)
            )
            plans = [
                self._plan_to_dict(plan)
                for plan in result.scalars().all()
            ]

        items = []
        for plan in plans:
            item = self._operating_follow_up_item(plan)
            if kind and item["kind"] != kind:
                continue
            if target_view and item["target_view"] != target_view:
                continue
            if company_namespace and item["company_namespace"] != company_namespace:
                continue
            items.append(item)

        limited_items = items[:safe_limit]
        return {
            "generated_at": utc_now().isoformat(),
            "filters": {
                "status": status_filter,
                "kind": kind,
                "target_view": target_view,
                "company_namespace": company_namespace,
                "limit": safe_limit,
            },
            "counts": self._operating_follow_up_counts(limited_items),
            "items": limited_items,
        }

    async def scan_operating_cadences(
        self,
        *,
        actor: str = "autonomous_planner",
        company_namespace: str | None = None,
        auto_execute: bool = False,
        limit: int = 200,
    ) -> dict[str, Any]:
        status = await self.operating_cadence_status(
            company_namespace=company_namespace,
            limit=limit,
        )
        created = []
        existing = []
        errors = []
        for cadence in status["items"]:
            if not cadence["due"]:
                continue
            try:
                result = await self.create_plan_from_operating_cadence(
                    cadence,
                    actor=actor,
                )
                (created if result["created"] else existing).append(result["plan"])
            except Exception as exc:
                errors.append(self._error("operating_cadence", cadence["cadence_id"], exc))
        execution = None
        if auto_execute:
            execution = await self.execute_ready_plans(actor=actor, limit=limit)
        summary = {
            "scanned_at": utc_now().isoformat(),
            "actor": actor,
            "company_namespace": company_namespace,
            "cadences_reviewed": len(status["items"]),
            "cadences_due": status["counts"]["due"],
            "plans_created": len(created),
            "plans_existing": len(existing),
            "created_plan_ids": [plan["id"] for plan in created],
            "existing_plan_ids": [plan["id"] for plan in existing],
            "status": status,
            "errors": errors,
            "execution": execution,
        }
        await self._record(
            "operating_cadence.scan",
            actor=actor,
            resource_id=None,
            outcome="degraded" if errors else "success",
            metadata={
                "company_namespace": company_namespace,
                "cadences_reviewed": summary["cadences_reviewed"],
                "cadences_due": summary["cadences_due"],
                "plans_created": summary["plans_created"],
                "plans_existing": summary["plans_existing"],
                "errors": errors,
            },
        )
        return summary

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
            elif task["task_type"] == "operating_cadence.assess":
                result = await self._execute_operating_cadence_assess(task)
            elif task["task_type"] == "operating_cadence.prepare_review":
                result = await self._execute_operating_cadence_prepare_review(task)
            elif task["task_type"] == "operating_cadence.record_next_actions":
                result = await self._execute_operating_cadence_record_next_actions(
                    task,
                    actor,
                )
            elif task["task_type"] == "operating_follow_up.review":
                result = await self._execute_operating_follow_up_review(task)
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

    async def _execute_operating_cadence_assess(
        self,
        task: dict[str, Any],
    ) -> dict[str, Any]:
        cadence = task["action_payload"].get("cadence") or {}
        cadence_id = cadence.get("cadence_id") or task["target_id"]
        status = await self.operating_cadence_status(
            company_namespace=cadence.get("company_namespace"),
            limit=200,
        )
        current = next(
            (item for item in status["items"] if item["cadence_id"] == cadence_id),
            cadence,
        )
        result = {
            "cadence_id": cadence_id,
            "agent_id": current.get("agent_id"),
            "role_name": current.get("role_name"),
            "role_family": current.get("role_family"),
            "company_namespace": current.get("company_namespace"),
            "frequency": current.get("frequency"),
            "review_window": current.get("review_window"),
            "signals": current.get("signals", []),
            "state": current.get("state"),
            "due": current.get("due"),
            "manual_only_side_effects": True,
        }
        return await self._finish_task(task["id"], "completed", result=result)

    async def _execute_operating_cadence_prepare_review(
        self,
        task: dict[str, Any],
    ) -> dict[str, Any]:
        cadence = task["action_payload"].get("cadence") or {}
        checklist = cadence.get("checklist") or []
        signals = cadence.get("signals") or []
        role_name = cadence.get("role_name") or task.get("agent_id") or "Role"
        result = {
            "title": f"{role_name} operating review",
            "review_window": cadence.get("review_window") or "Operating review",
            "checklist": checklist,
            "signals_to_review": signals,
            "source_snapshot_id": cadence.get("source_snapshot_id"),
            "source_role_gap_id": cadence.get("source_role_gap_id"),
            "owner_guidance": (
                "Use this brief to inspect role work and approve any downstream "
                "external side effects explicitly. This task does not mutate ERPNext, "
                "send messages, or trigger provider writes."
            ),
        }
        return await self._finish_task(task["id"], "completed", result=result)

    async def _execute_operating_cadence_record_next_actions(
        self,
        task: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        cadence = task["action_payload"].get("cadence") or {}
        policy = task["action_payload"].get("policy") or {}
        follow_up_specs = self._operating_cadence_follow_up_specs(cadence, task)
        follow_up_plans = []
        for spec in follow_up_specs:
            follow_up_plans.append(
                await self._create_operating_follow_up_plan(
                    spec,
                    parent_task=task,
                    actor=actor,
                )
            )
        result = {
            "cadence_id": cadence.get("cadence_id") or task["target_id"],
            "safe_internal_actions": [
                "Review the generated operating brief.",
                "Open related role backlog, memory, or ERPNext context if the brief flags risk.",
                "Create or approve downstream work only through the existing approval flows.",
            ],
            "follow_ups": follow_up_specs,
            "follow_up_plans": follow_up_plans,
            "external_side_effects": {
                "allowed": False,
                "reason": policy.get(
                    "side_effect_policy",
                    "External side effects remain manual-only.",
                ),
            },
            "next_due_hint": cadence.get("next_due_at"),
        }
        return await self._finish_task(task["id"], "completed", result=result)

    async def _execute_operating_follow_up_review(
        self,
        task: dict[str, Any],
    ) -> dict[str, Any]:
        follow_up = task["action_payload"].get("follow_up") or {}
        result = {
            "follow_up": follow_up,
            "review_status": "ready_for_owner",
            "manual_only_side_effects": True,
            "owner_guidance": (
                "Review this follow-up, then use the linked owner-console area for "
                "any concrete action. External writes, communications, and provider "
                "mutations still require their dedicated approval flows."
            ),
        }
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

    async def _latest_plan_for_source(
        self,
        source_type: str,
        source_id: str,
    ) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(AutonomousPlan)
                .where(
                    AutonomousPlan.source_type == source_type,
                    AutonomousPlan.source_id == source_id,
                )
                .order_by(desc(AutonomousPlan.created_at))
            )
            plan = result.scalars().first()
            return self._plan_to_dict(plan, include_tasks=False) if plan else None

    async def _create_operating_follow_up_plan(
        self,
        spec: dict[str, Any],
        *,
        parent_task: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        source_id = spec["source_id"]
        existing = await self._find_active_plan("operating_cadence_follow_up", source_id)
        if existing:
            return {
                "status": "existing",
                "plan_id": existing["id"],
                "source_id": source_id,
                "kind": spec["kind"],
            }

        task_specs = self._number_task_specs([
            self._task_spec(
                spec["title"],
                spec["description"],
                "operating_follow_up.review",
                spec["target_type"],
                spec.get("target_id"),
                spec["risk_level"],
                {"follow_up": spec},
                agent_id=spec.get("agent_id"),
                autonomous_allowed=False,
            )
        ])
        plan = await self._create_plan(
            title=spec["title"],
            objective=spec["description"],
            source_type="operating_cadence_follow_up",
            source_id=source_id,
            priority=spec["risk_level"],
            created_by=actor,
            context={
                "parent_plan_id": parent_task["plan_id"],
                "parent_task_id": parent_task["id"],
                "cadence_id": spec["cadence_id"],
                "agent_id": spec.get("agent_id"),
                "role_name": spec.get("role_name"),
                "role_family": spec.get("role_family"),
                "company_namespace": spec.get("company_namespace"),
                "follow_up": spec,
                "manual_only_side_effects": True,
            },
            task_specs=task_specs,
        )
        return {
            "status": "created",
            "plan_id": plan["id"],
            "source_id": source_id,
            "kind": spec["kind"],
        }

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

    @staticmethod
    def _operating_cadence_policy(cadence: dict[str, Any]) -> dict[str, Any]:
        return {
            "policy_version": "planner-operating-cadence-v1",
            "source_type": "operating_cadence",
            "source_id": cadence["cadence_id"],
            "max_risk": "low",
            "owner_review_required": False,
            "review_reasons": [],
            "blockers": [],
            "autonomous_execution": "safe_internal_review",
            "side_effect_policy": (
                "Cadence reviews may inspect and summarize, but production external "
                "side effects require explicit owner approval."
            ),
        }

    def _operating_cadence_follow_up_specs(
        self,
        cadence: dict[str, Any],
        task: dict[str, Any],
    ) -> list[dict[str, Any]]:
        cadence_id = cadence.get("cadence_id") or task["target_id"]
        signals = {
            str(signal).lower()
            for signal in [
                *list(cadence.get("signals") or []),
                *list((cadence.get("cadence") or {}).get("signals") or []),
            ]
            if signal
        }
        role_family = str(cadence.get("role_family") or "operations").lower()
        base = {
            "cadence_id": cadence_id,
            "agent_id": cadence.get("agent_id") or task.get("agent_id"),
            "role_name": cadence.get("role_name") or "Operating role",
            "role_family": cadence.get("role_family") or "operations",
            "company_namespace": cadence.get("company_namespace"),
            "source_snapshot_id": cadence.get("source_snapshot_id"),
            "source_role_gap_id": cadence.get("source_role_gap_id"),
            "review_window": cadence.get("review_window") or "Operating review",
            "signals": sorted(signals),
        }
        specs: list[dict[str, Any]] = []
        if base["source_role_gap_id"]:
            specs.append(
                self._operating_follow_up_spec(
                    base,
                    kind="role_backlog_review",
                    title=f"Review role backlog linked to {base['role_name']}",
                    description=(
                        "Inspect the source role gap and confirm whether the active "
                        "role still has missing tools, approval needs, or stale context."
                    ),
                    target_type="role_gap",
                    target_id=base["source_role_gap_id"],
                    target_view="agents",
                    recommended_action="review_role_gap",
                    risk_level="low",
                )
            )
        if signals & {"memory_trace", "empty_recall", "stale_memory", "namespace"}:
            specs.append(
                self._operating_follow_up_spec(
                    base,
                    kind="memory_steward_review",
                    title=f"Review memory health for {base['role_name']}",
                    description=(
                        "Inspect recent memory traces, empty recalls, stale procedural "
                        "memory, and namespace quality before the next cadence."
                    ),
                    target_type="memory",
                    target_id=base["agent_id"],
                    target_view="memory",
                    recommended_action="review_memory_traces",
                    risk_level="low",
                )
            )
        if signals & {
            "lead",
            "opportunity",
            "customer",
            "supplier",
            "sales_invoice",
            "material_request",
            "issue",
            "ticket",
            "item",
            "project",
            "task",
            "cash_risk",
            "sla",
        }:
            specs.append(
                self._operating_follow_up_spec(
                    base,
                    kind="erpnext_review",
                    title=f"Review ERPNext signals for {base['role_name']}",
                    description=(
                        "Inspect relevant ERPNext records and summarize any business "
                        "exceptions without mutating ERPNext automatically."
                    ),
                    target_type="erpnext",
                    target_id=base["company_namespace"],
                    target_view="integrations",
                    recommended_action="review_erpnext_context",
                    risk_level="low",
                )
            )
        if signals & {"audit_event", "auth_failure", "tool_misuse", "secret", "compliance_risk"}:
            specs.append(
                self._operating_follow_up_spec(
                    base,
                    kind="security_control_review",
                    title=f"Review security controls for {base['role_name']}",
                    description=(
                        "Review audit evidence, auth failures, tool-misuse signals, "
                        "and secret-handling posture before approving operational changes."
                    ),
                    target_type="security_control",
                    target_id=base["agent_id"],
                    target_view="operations",
                    recommended_action="review_security_controls",
                    risk_level="medium",
                )
            )
        if signals & {"email", "call", "message", "owner_notification", "follow_up"}:
            specs.append(
                self._operating_follow_up_spec(
                    base,
                    kind="owner_approval_watch",
                    title=f"Review external-action approvals for {base['role_name']}",
                    description=(
                        "Check pending approvals before any outreach, provider write, "
                        "or customer-facing communication is executed."
                    ),
                    target_type="approval_queue",
                    target_id=base["agent_id"],
                    target_view="approvals",
                    recommended_action="review_pending_approvals",
                    risk_level="medium",
                )
            )
        if not specs:
            specs.append(
                self._operating_follow_up_spec(
                    base,
                    kind=f"{role_family}_operating_review",
                    title=f"Review operating loop for {base['role_name']}",
                    description=(
                        "Review the cadence brief and decide whether any role, memory, "
                        "workflow, or company-context work should be queued next."
                    ),
                    target_type="operating_cadence",
                    target_id=cadence_id,
                    target_view="operations",
                    recommended_action="review_operating_brief",
                    risk_level="low",
                )
            )
        return specs

    @staticmethod
    def _operating_follow_up_spec(
        base: dict[str, Any],
        *,
        kind: str,
        title: str,
        description: str,
        target_type: str,
        target_id: str | None,
        target_view: str,
        recommended_action: str,
        risk_level: str,
    ) -> dict[str, Any]:
        dedupe_basis = "|".join(
            [
                str(base["cadence_id"]),
                kind,
                str(target_type),
                str(target_id or ""),
            ]
        )
        source_id = f"followup_{hashlib.sha256(dedupe_basis.encode()).hexdigest()[:16]}"
        return {
            **base,
            "kind": kind,
            "title": title,
            "description": description,
            "target_type": target_type,
            "target_id": target_id,
            "target_view": target_view,
            "recommended_action": recommended_action,
            "risk_level": risk_level,
            "source_id": source_id,
            "dedupe_key": dedupe_basis,
            "external_side_effects_allowed": False,
        }

    def _operating_follow_up_item(self, plan: dict[str, Any]) -> dict[str, Any]:
        context = plan.get("context") or {}
        follow_up = context.get("follow_up") or {}
        tasks = plan.get("tasks") or []
        task_count = len(tasks)
        completed_task_count = len(
            [task for task in tasks if task.get("status") == "completed"]
        )
        active_task = next(
            (
                task
                for task in tasks
                if task.get("status") in {"planned", "running", "waiting_approval"}
            ),
            tasks[-1] if tasks else None,
        )
        risk_level = self._normalize_risk(
            follow_up.get("risk_level") or plan.get("priority")
        )
        return {
            "plan_id": plan["id"],
            "title": plan["title"],
            "description": plan["objective"],
            "status": plan["status"],
            "priority": plan["priority"],
            "risk_level": risk_level,
            "created_at": plan["created_at"],
            "updated_at": plan["updated_at"],
            "completed_at": plan.get("completed_at"),
            "parent_plan_id": context.get("parent_plan_id"),
            "parent_task_id": context.get("parent_task_id"),
            "cadence_id": context.get("cadence_id") or follow_up.get("cadence_id"),
            "agent_id": context.get("agent_id") or follow_up.get("agent_id"),
            "role_name": context.get("role_name") or follow_up.get("role_name"),
            "role_family": context.get("role_family") or follow_up.get("role_family"),
            "company_namespace": (
                context.get("company_namespace")
                or follow_up.get("company_namespace")
            ),
            "kind": follow_up.get("kind") or "operating_review",
            "target_type": follow_up.get("target_type"),
            "target_id": follow_up.get("target_id"),
            "target_view": follow_up.get("target_view") or "operations",
            "recommended_action": (
                follow_up.get("recommended_action") or "review_operating_brief"
            ),
            "source_snapshot_id": follow_up.get("source_snapshot_id"),
            "source_role_gap_id": follow_up.get("source_role_gap_id"),
            "dedupe_key": follow_up.get("dedupe_key"),
            "manual_only_side_effects": bool(
                context.get("manual_only_side_effects")
                or not follow_up.get("external_side_effects_allowed")
            ),
            "task_count": task_count,
            "completed_task_count": completed_task_count,
            "active_task": (
                {
                    "task_id": active_task.get("id"),
                    "title": active_task.get("title"),
                    "status": active_task.get("status"),
                    "approval_id": active_task.get("approval_id"),
                    "result": active_task.get("result") or {},
                    "error": active_task.get("error"),
                }
                if active_task
                else None
            ),
            "next_action": self._operating_follow_up_next_action(plan, active_task),
            "follow_up": follow_up,
        }

    def _operating_follow_up_next_action(
        self,
        plan: dict[str, Any],
        active_task: dict[str, Any] | None,
    ) -> str:
        if plan["status"] == "completed":
            return "review_linked_owner_console_area"
        if plan["status"] == "waiting_approval" or (
            active_task and active_task.get("status") == "waiting_approval"
        ):
            return "review_owner_approval"
        if plan["status"] in self.EXECUTABLE_PLAN_STATUSES:
            return "execute_follow_up_review"
        if plan["status"] in {"blocked", "failed"}:
            return "inspect_failure"
        return "no_action"

    def _normalize_follow_up_status_filter(self, status: str | None) -> str:
        normalized = str(status or "active").strip().lower()
        return normalized or "active"

    def _db_statuses_for_follow_up_filter(self, status: str) -> set[str] | None:
        if status in {"all", "*"}:
            return None
        if status == "active":
            return set(self.ACTIVE_PLAN_STATUSES)
        return {
            item.strip()
            for item in status.split(",")
            if item.strip()
        } or set(self.ACTIVE_PLAN_STATUSES)

    @staticmethod
    def _operating_follow_up_counts(items: list[dict[str, Any]]) -> dict[str, Any]:
        counts: dict[str, Any] = {
            "total": len(items),
            "active": 0,
            "completed": 0,
            "by_status": {},
            "by_kind": {},
            "by_target_view": {},
            "by_risk": {},
        }
        for item in items:
            status = item["status"]
            kind = item["kind"]
            target_view = item["target_view"]
            risk = item["risk_level"]
            if status in AutonomousPlanningService.ACTIVE_PLAN_STATUSES:
                counts["active"] += 1
            if status == "completed":
                counts["completed"] += 1
            counts["by_status"][status] = counts["by_status"].get(status, 0) + 1
            counts["by_kind"][kind] = counts["by_kind"].get(kind, 0) + 1
            counts["by_target_view"][target_view] = (
                counts["by_target_view"].get(target_view, 0) + 1
            )
            counts["by_risk"][risk] = counts["by_risk"].get(risk, 0) + 1
        return counts

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
    def _cadence_interval(frequency: str | None) -> timedelta:
        normalized = str(frequency or "weekly").lower()
        if normalized == "hourly":
            return timedelta(hours=1)
        if normalized == "daily":
            return timedelta(days=1)
        if normalized == "monthly":
            return timedelta(days=30)
        if normalized == "quarterly":
            return timedelta(days=90)
        return timedelta(days=7)

    @staticmethod
    def _plan_baseline_at(plan: dict[str, Any] | None) -> datetime | None:
        if not plan:
            return None
        raw_value = plan.get("completed_at") or plan.get("created_at")
        if not raw_value:
            return None
        try:
            value = datetime.fromisoformat(str(raw_value))
        except ValueError:
            return None
        if value.tzinfo is not None:
            value = value.replace(tzinfo=None)
        return value

    @staticmethod
    def _cadence_due_reason(
        *,
        due: bool,
        active_plan: bool,
        latest_plan: dict[str, Any] | None,
        frequency: str | None,
    ) -> str:
        if active_plan and latest_plan:
            return f"An active operating cadence plan already exists: {latest_plan['id']}."
        if due and latest_plan:
            return f"The last {frequency or 'weekly'} cadence plan is older than its interval."
        if due:
            return "No prior operating cadence plan exists for this role."
        return f"The last {frequency or 'weekly'} cadence plan is still fresh."

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
