"""Chief Operating Agent governor for autonomous orchestration."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import selectinload

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import (
    Agent,
    ApprovalRequest,
    AuditEvent,
    AutonomousPlan,
    AutonomousTask,
    CompanyContextSnapshot,
    MemoryStewardFinding,
    OrchestrationGovernorDecision,
    OrchestrationGovernorRun,
    OrchestrationToolProposal,
    RoleGap,
    RoleManifest,
    WorkflowRun,
)


class OrchestrationGovernorService:
    """Observe, decide, delegate, and audit Cyber-Team operating work."""

    POLICY_VERSION = "governor-v1"
    CHIEF_AGENT_ID = "chief_operating_agent"
    CHIEF_ROLE_NAME = "Chief Operating Agent"
    ACTIVE_PLAN_STATUSES = {"planned", "running", "waiting_approval", "blocked"}
    ACTIVE_GAP_STATUSES = {"open", "proposed"}
    ACTIVE_PROPOSAL_STATUSES = {"proposed", "approval_requested", "approved"}
    SECRET_KEY_MARKERS = (
        "password",
        "secret",
        "token",
        "api_key",
        "api_secret",
        "authorization",
        "credential",
    )

    def __init__(
        self,
        *,
        agent_manager=None,
        planning_service=None,
        memory_steward_service=None,
        tool_registry=None,
        audit_service=None,
        readiness_evidence_service=None,
        comms_gateway=None,
        erpnext=None,
    ):
        self._agent_manager = agent_manager
        self._planning = planning_service
        self._memory_steward = memory_steward_service
        self._tool_registry = tool_registry
        self._audit = audit_service
        self._readiness_evidence = readiness_evidence_service
        self._comms = comms_gateway
        self._erpnext = erpnext

    async def ensure_chief_operating_agent(self) -> dict[str, Any]:
        """Ensure the governor is visible as a durable system role and agent."""
        async with async_session() as session:
            manifest = await session.get(RoleManifest, self.CHIEF_AGENT_ID)
            if not manifest:
                manifest = RoleManifest(
                    id=self.CHIEF_AGENT_ID,
                    family="orchestration",
                    name=self.CHIEF_ROLE_NAME,
                    description=(
                        "Coordinates Cyber-Team operating loops, delegates safe "
                        "internal work, and escalates risky actions to the owner."
                    ),
                    instructions_template=(
                        "You are the Chief Operating Agent for {company_name}. "
                        "Continuously observe readiness, agents, workflows, memory, "
                        "ERPNext context, role gaps, approvals, and tool readiness. "
                        "Auto-apply only low-risk internal improvements. External "
                        "writes, generated tool activation, and medium/high-risk "
                        "actions require exact owner approval."
                    ),
                    default_tools=[
                        "memory_recall",
                        "role_gap_report",
                        "approval_request",
                        "agent_status_read",
                    ],
                    memory_namespace="company:governor",
                    approval_policy="auto",
                    success_metrics={
                        "readiness_blockers": "trend_down",
                        "owner_attention_sla": "within_24h",
                        "unsafe_actions_blocked": "always",
                    },
                    is_core=True,
                    config={
                        "system_role": True,
                        "authority": "governed_orchestration",
                        "policy_version": self.POLICY_VERSION,
                    },
                )
                session.add(manifest)

            agent = await session.get(Agent, self.CHIEF_AGENT_ID)
            if not agent:
                agent = Agent(
                    id=self.CHIEF_AGENT_ID,
                    role_family="orchestration",
                    role_name=self.CHIEF_ROLE_NAME,
                    instructions=manifest.instructions_template.format(
                        company_name="Cyber-Team"
                    ),
                    tools=manifest.default_tools,
                    memory_namespace="company:governor",
                    approval_policy="auto",
                    status="active",
                    config={
                        "system_agent": True,
                        "authority": "governed_orchestration",
                        "policy_version": self.POLICY_VERSION,
                    },
                )
                session.add(agent)
            elif agent.status != "active":
                agent.status = "active"
                agent.updated_at = utc_now()

            await session.commit()
            return self._agent_to_dict(agent)

    async def run_once(
        self,
        *,
        actor: str = "chief_operating_agent",
        dry_run: bool = False,
        auto_apply_low_risk: bool | None = None,
        max_actions: int | None = None,
        continue_on_error: bool = True,
    ) -> dict[str, Any]:
        await self.ensure_chief_operating_agent()
        started_at = utc_now()
        safe_max_actions = max(1, min(max_actions or settings.governor_max_actions_per_cycle, 50))
        allow_low_risk = (
            settings.governor_auto_apply_low_risk
            if auto_apply_low_risk is None
            else bool(auto_apply_low_risk)
        )
        if settings.autonomy_side_effect_mode == "manual_only":
            # Internal DB-only plans/proposals are still allowed; external writes are not.
            external_side_effects_allowed = False
        else:
            external_side_effects_allowed = False

        snapshot = await self.build_operating_snapshot()
        snapshot_hash = self._stable_hash(snapshot)
        specs = self._decide(snapshot, max_actions=safe_max_actions)
        run_id = f"govrun_{uuid.uuid4().hex[:12]}"
        run = OrchestrationGovernorRun(
            id=run_id,
            status="running",
            actor=actor,
            policy_version=self.POLICY_VERSION,
            mode="dry_run" if dry_run else "active",
            auto_apply_low_risk=allow_low_risk,
            max_actions=safe_max_actions,
            snapshot_hash=snapshot_hash,
            operating_snapshot=snapshot,
            operating_brief=self._operating_brief(snapshot, specs),
            counts={},
            errors=[],
            started_at=started_at,
        )
        async with async_session() as session:
            session.add(run)
            await session.commit()

        decisions: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for spec in specs[:safe_max_actions]:
            try:
                decision = await self._persist_and_apply_decision(
                    run_id=run_id,
                    spec=spec,
                    actor=actor,
                    dry_run=dry_run,
                    auto_apply_low_risk=allow_low_risk,
                    external_side_effects_allowed=external_side_effects_allowed,
                )
                if decision:
                    decisions.append(decision)
            except Exception as exc:
                error = {
                    "decision_type": spec.get("decision_type"),
                    "source_type": spec.get("source_type"),
                    "source_id": spec.get("source_id"),
                    "message": str(exc),
                    "type": type(exc).__name__,
                }
                errors.append(error)
                if not continue_on_error:
                    break

        completed_at = utc_now()
        status = "degraded" if errors else "completed"
        counts = self._decision_counts(decisions)
        async with async_session() as session:
            saved_run = await session.get(OrchestrationGovernorRun, run_id)
            if saved_run:
                saved_run.status = status
                saved_run.completed_at = completed_at
                saved_run.counts = counts
                saved_run.errors = errors
                await session.commit()

        await self._record_run_audit(
            actor=actor,
            run_id=run_id,
            status=status,
            counts=counts,
            errors=errors,
            dry_run=dry_run,
        )
        return {
            "run_id": run_id,
            "status": status,
            "actor": actor,
            "policy_version": self.POLICY_VERSION,
            "mode": "dry_run" if dry_run else "active",
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "snapshot_hash": snapshot_hash,
            "operating_brief": run.operating_brief,
            "counts": counts,
            "decisions": decisions,
            "errors": errors,
            "safety": self.safety_status(),
        }

    async def build_operating_snapshot(self) -> dict[str, Any]:
        now = utc_now()
        async with async_session() as session:
            agent_counts = await self._agent_counts(session)
            role_gap_counts = await self._role_gap_counts(session)
            plan_counts = await self._plan_counts(session)
            workflow_counts = await self._workflow_counts(session)
            memory_counts = await self._memory_finding_counts(session)
            latest_context = await self._latest_company_context(session)
            recent_audit = await self._recent_audit(session)
            open_role_gaps = await self._open_role_gaps(session)

        tool_status = self._tool_status()
        owner_attention = await self._owner_attention_summary()
        production_evidence = await self._production_evidence_summary()
        integrations = self._integration_summary()
        snapshot = {
            "generated_at": now.isoformat(),
            "policy_version": self.POLICY_VERSION,
            "environment": settings.environment,
            "autonomy": self.safety_status(),
            "agents": agent_counts,
            "role_backlog": role_gap_counts,
            "role_gap_samples": open_role_gaps,
            "plans": plan_counts,
            "workflows": workflow_counts,
            "memory": memory_counts,
            "company_context": latest_context,
            "tools": tool_status,
            "owner_attention": owner_attention,
            "integrations": integrations,
            "production_evidence": production_evidence,
            "recent_audit": recent_audit,
        }
        return self._redact(snapshot)

    async def latest_run(self) -> dict[str, Any] | None:
        async with async_session() as session:
            result = await session.execute(
                select(OrchestrationGovernorRun)
                .options(selectinload(OrchestrationGovernorRun.decisions))
                .order_by(desc(OrchestrationGovernorRun.started_at))
                .limit(1)
            )
            run = result.scalar_one_or_none()
            return self._run_to_dict(run) if run else None

    async def list_runs(self, *, limit: int = 20) -> dict[str, Any]:
        safe_limit = max(1, min(limit, 200))
        async with async_session() as session:
            result = await session.execute(
                select(OrchestrationGovernorRun)
                .order_by(desc(OrchestrationGovernorRun.started_at))
                .limit(safe_limit)
            )
            runs = [self._run_to_dict(run, include_decisions=False) for run in result.scalars()]
        return {"items": runs, "count": len(runs), "limit": safe_limit}

    async def list_decisions(
        self,
        *,
        status: str | None = None,
        decision_type: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(limit, 500))
        async with async_session() as session:
            query = select(OrchestrationGovernorDecision)
            if status:
                query = query.where(OrchestrationGovernorDecision.status == status)
            if decision_type:
                query = query.where(
                    OrchestrationGovernorDecision.decision_type == decision_type
                )
            result = await session.execute(
                query.order_by(desc(OrchestrationGovernorDecision.created_at)).limit(
                    safe_limit
                )
            )
            items = [self._decision_to_dict(decision) for decision in result.scalars()]
        return {"items": items, "count": len(items), "limit": safe_limit}

    async def list_tool_proposals(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(limit, 500))
        async with async_session() as session:
            query = select(OrchestrationToolProposal)
            if status:
                query = query.where(OrchestrationToolProposal.status == status)
            result = await session.execute(
                query.order_by(desc(OrchestrationToolProposal.created_at)).limit(
                    safe_limit
                )
            )
            items = [self._tool_proposal_to_dict(item) for item in result.scalars()]
        counts: dict[str, int] = {}
        for item in items:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        return {"items": items, "count": len(items), "counts": counts, "limit": safe_limit}

    async def request_tool_proposal_approval(
        self,
        proposal_id: str,
        *,
        actor: str,
        note: str = "",
    ) -> dict[str, Any]:
        async with async_session() as session:
            proposal = await session.get(OrchestrationToolProposal, proposal_id)
            if not proposal:
                raise ValueError("Tool proposal not found")
            if proposal.status not in self.ACTIVE_PROPOSAL_STATUSES:
                raise ValueError(
                    f"Tool proposal {proposal_id} is not active: {proposal.status}"
                )
            approval = await self._find_executable_approval(
                session,
                target_type="tool_proposal",
                target_id=proposal_id,
            )
            if not approval:
                approval = ApprovalRequest(
                    id=f"appr_{uuid.uuid4().hex[:16]}",
                    agent_id=self.CHIEF_AGENT_ID,
                    action_type="tool_proposal_approval",
                    action_description=(
                        "Approve sandboxed implementation planning for tool proposal "
                        f"{proposal.title}. This does not hot-load code or mutate "
                        "external systems."
                    ),
                    action_payload={
                        "proposal_id": proposal.id,
                        "title": proposal.title,
                        "capability": proposal.capability,
                        "risk_level": proposal.risk_level,
                        "side_effects": proposal.side_effects,
                        "note": note,
                    },
                    requester=self.CHIEF_AGENT_ID,
                    requester_type="agent",
                    risk_level=proposal.risk_level,
                    target_type="tool_proposal",
                    target_id=proposal.id,
                    status="pending",
                    expires_at=utc_now() + timedelta(days=7),
                )
                session.add(approval)
                proposal.approval_id = approval.id
                proposal.status = "approval_requested"
                proposal.updated_at = utc_now()
                await session.commit()
            return {
                "status": "approval_requested",
                "approval_id": approval.id,
                "proposal": self._tool_proposal_to_dict(proposal),
            }

    async def readiness(self) -> dict[str, Any]:
        latest = await self.latest_run()
        if not settings.governor_enabled:
            return {
                "enabled": False,
                "status": "disabled",
                "blocking": False,
                "detail": "Chief Operating Agent governor is disabled.",
                "latest_run": latest,
                "safety": self.safety_status(),
            }
        if not latest:
            return {
                "enabled": True,
                "status": "waiting",
                "blocking": False,
                "detail": "Chief Operating Agent governor has not run yet.",
                "latest_run": None,
                "safety": self.safety_status(),
            }
        return {
            "enabled": True,
            "status": latest["status"],
            "blocking": latest["status"] == "failed",
            "detail": "Chief Operating Agent governor is recording operating decisions.",
            "latest_run": {
                "run_id": latest["run_id"],
                "started_at": latest["started_at"],
                "completed_at": latest["completed_at"],
                "snapshot_hash": latest["snapshot_hash"],
                "counts": latest["counts"],
            },
            "safety": self.safety_status(),
        }

    def safety_status(self) -> dict[str, Any]:
        return {
            "side_effect_mode": settings.autonomy_side_effect_mode,
            "manual_only_external_side_effects": True,
            "auto_apply_low_risk": settings.governor_auto_apply_low_risk,
            "max_actions_per_cycle": settings.governor_max_actions_per_cycle,
            "tool_creation_mode": settings.governor_tool_creation_mode,
            "generated_code_hot_loading": False,
            "policy_version": self.POLICY_VERSION,
        }

    def _decide(
        self,
        snapshot: dict[str, Any],
        *,
        max_actions: int,
    ) -> list[dict[str, Any]]:
        decisions: list[dict[str, Any]] = []
        evidence = snapshot.get("production_evidence") or {}
        alerts = evidence.get("alerts") or {}
        if alerts.get("blocking") or alerts.get("stale"):
            decisions.append(
                self._decision_spec(
                    "escalate_owner_attention",
                    "Refresh required alert email delivery evidence",
                    alerts.get("detail")
                    or "The required owner alert email proof is missing or stale.",
                    risk_level="low",
                    source_type="readiness",
                    source_id="alerts",
                    target_type="operations_alert",
                    target_id="email",
                    payload={
                        "recommended_action": "run_alert_email_test",
                        "target_view": "operations",
                        "requires_owner_authorization": True,
                    },
                )
            )

        memory = snapshot.get("memory") or {}
        if int(memory.get("open_findings") or 0) > 0:
            decisions.append(
                self._decision_spec(
                    "create_plan",
                    "Review open memory steward findings",
                    "Open memory steward findings should be reviewed and remediated.",
                    risk_level="low",
                    source_type="memory_steward",
                    source_id="open_findings",
                    target_type="memory",
                    target_id="steward_findings",
                    payload={
                        "recommended_action": "review_memory_steward_findings",
                        "target_view": "memory",
                    },
                )
            )

        context = snapshot.get("company_context") or {}
        if context.get("stale") or context.get("status") in {None, "missing"}:
            decisions.append(
                self._decision_spec(
                    "create_plan",
                    "Refresh ERPNext company context",
                    "Company context is missing or stale; sync ERPNext before "
                    "making new operating-model decisions.",
                    risk_level="low",
                    source_type="company_context",
                    source_id=context.get("snapshot_id") or "latest",
                    target_type="company_context",
                    target_id="erpnext",
                    payload={
                        "recommended_action": "sync_company_context",
                        "target_view": "agents",
                    },
                )
            )

        role_backlog = snapshot.get("role_backlog") or {}
        if int(role_backlog.get("active") or 0) > 0:
            decisions.append(
                self._decision_spec(
                    "create_plan",
                    "Review active role backlog",
                    "Role gaps are active and should be reviewed, applied, deferred, "
                    "dismissed, or converted into tool proposals.",
                    risk_level="low",
                    source_type="role_backlog",
                    source_id="active",
                    target_type="role_gap",
                    target_id="summary",
                    payload={
                        "recommended_action": "review_role_backlog",
                        "target_view": "agents",
                    },
                )
            )

        for gap in snapshot.get("role_gap_samples") or []:
            for tool_name in gap.get("missing_tools") or []:
                decisions.append(
                    self._tool_proposal_decision(gap, tool_name, "missing_tool")
                )
            for tool in gap.get("configuration_required_tools") or []:
                decisions.append(
                    self._tool_proposal_decision(
                        gap,
                        tool.get("name"),
                        "configuration_required",
                        readiness=tool,
                    )
                )

        workflows = snapshot.get("workflows") or {}
        if int(workflows.get("recent_failed") or 0) > 0:
            decisions.append(
                self._decision_spec(
                    "escalate_owner_attention",
                    "Inspect recent failed workflow runs",
                    "Recent workflow failures may indicate broken automation or "
                    "missing role/tool coverage.",
                    risk_level="medium",
                    source_type="workflow_run",
                    source_id="recent_failed",
                    target_type="workflow",
                    target_id="failed_runs",
                    payload={
                        "recommended_action": "review_failed_workflows",
                        "target_view": "workflows",
                    },
                )
            )

        if not decisions:
            decisions.append(
                self._decision_spec(
                    "observe_only",
                    "No operating intervention required",
                    "The configured operating snapshot has no deterministic governor "
                    "action beyond recording this brief.",
                    risk_level="low",
                    source_type="operating_snapshot",
                    source_id=snapshot.get("generated_at"),
                    target_type="operations",
                    target_id="brief",
                    payload={"recommended_action": "none"},
                )
            )
        return decisions[:max_actions]

    async def _persist_and_apply_decision(
        self,
        *,
        run_id: str,
        spec: dict[str, Any],
        actor: str,
        dry_run: bool,
        auto_apply_low_risk: bool,
        external_side_effects_allowed: bool,
    ) -> dict[str, Any] | None:
        idempotency_key = spec["idempotency_key"]
        async with async_session() as session:
            existing = await self._existing_decision(session, idempotency_key)
            if existing:
                return {
                    **self._decision_to_dict(existing),
                    "duplicate": True,
                    "recommended_action": "inspect_existing_decision",
                }

            decision = OrchestrationGovernorDecision(
                id=f"govdec_{uuid.uuid4().hex[:12]}",
                run_id=run_id,
                decision_type=spec["decision_type"],
                title=spec["title"],
                description=spec["description"],
                status="proposed" if dry_run else "recorded",
                risk_level=spec["risk_level"],
                source_type=spec.get("source_type"),
                source_id=spec.get("source_id"),
                target_type=spec.get("target_type"),
                target_id=spec.get("target_id"),
                action_payload=spec.get("action_payload") or {},
                result={"dry_run": dry_run},
                idempotency_key=idempotency_key,
            )
            session.add(decision)
            await session.commit()

        if dry_run:
            return await self._decision_by_key(idempotency_key)

        if spec["decision_type"] == "observe_only":
            await self._mark_decision(
                idempotency_key,
                status="completed",
                result={"action": "recorded_brief_only"},
            )
            return await self._decision_by_key(idempotency_key)

        if spec["decision_type"] == "propose_tool":
            proposal = await self._create_tool_proposal(spec, actor=actor)
            await self._mark_decision(
                idempotency_key,
                status="proposal_created",
                result={
                    "action": "tool_proposal_created",
                    "tool_proposal_id": proposal["id"],
                    "external_side_effects_allowed": external_side_effects_allowed,
                },
                tool_proposal_id=proposal["id"],
            )
            return await self._decision_by_key(idempotency_key)

        if spec["risk_level"] == "low" and auto_apply_low_risk:
            plan = await self._create_owner_attention_plan(spec, actor=actor)
            await self._mark_decision(
                idempotency_key,
                status="delegated",
                result={"action": "owner_attention_plan_created", "plan_id": plan["id"]},
                plan_id=plan["id"],
            )
            return await self._decision_by_key(idempotency_key)

        approval = await self._request_decision_approval(spec, actor=actor)
        await self._mark_decision(
            idempotency_key,
            status="approval_required",
            result={"action": "approval_requested", "approval_id": approval.id},
            approval_id=approval.id,
        )
        return await self._decision_by_key(idempotency_key)

    async def _create_owner_attention_plan(
        self,
        spec: dict[str, Any],
        *,
        actor: str,
    ) -> dict[str, Any]:
        source_id = spec["idempotency_key"]
        async with async_session() as session:
            result = await session.execute(
                select(AutonomousPlan)
                .options(selectinload(AutonomousPlan.tasks))
                .where(
                    AutonomousPlan.source_type == "governor_decision",
                    AutonomousPlan.source_id == source_id,
                    AutonomousPlan.status.in_(self.ACTIVE_PLAN_STATUSES),
                )
                .limit(1)
            )
            existing = result.scalar_one_or_none()
            if existing:
                return self._plan_to_dict(existing)

            now = utc_now()
            plan = AutonomousPlan(
                id=f"plan_{uuid.uuid4().hex[:16]}",
                title=spec["title"],
                objective=spec["description"],
                source_type="governor_decision",
                source_id=source_id,
                status="planned",
                priority=spec["risk_level"],
                created_by="chief_operating_agent",
                context={
                    "governor": {
                        "policy_version": self.POLICY_VERSION,
                        "decision_type": spec["decision_type"],
                        "source_type": spec.get("source_type"),
                        "source_id": spec.get("source_id"),
                        "target_type": spec.get("target_type"),
                        "target_id": spec.get("target_id"),
                    },
                    "owner_attention": {
                        "required": True,
                        "kind": f"governor:{spec['decision_type']}",
                        "source_actor": actor,
                        "scheduler_created": actor == "chief_operating_agent_scheduler",
                        "attention_priority": spec["risk_level"],
                        "reason": spec["description"],
                        "recommended_action": (
                            spec.get("action_payload", {}).get("recommended_action")
                            or "review_governor_decision"
                        ),
                        "target_view": (
                            spec.get("action_payload", {}).get("target_view")
                            or "operations"
                        ),
                        "badge_label": "Governor",
                        "created_at": now.isoformat(),
                        "sla_hours": 24,
                        "sla_due_at": (now + timedelta(hours=24)).isoformat(),
                    },
                },
                summary={"governor_source": source_id},
            )
            task = AutonomousTask(
                id=f"task_{uuid.uuid4().hex[:16]}",
                plan=plan,
                sequence=1,
                title=f"Owner review: {spec['title']}",
                description=spec["description"],
                task_type="plan.owner_review",
                status="planned",
                agent_id=self.CHIEF_AGENT_ID,
                target_type=spec.get("target_type"),
                target_id=spec.get("target_id"),
                action_payload={
                    "governor_decision": spec,
                    "manual_only_side_effects": True,
                    "replay_instruction": (
                        "Review this item in the owner console and use the linked "
                        "first-class action; do not replay raw payloads."
                    ),
                },
                autonomous_allowed=False,
                risk_level=spec["risk_level"],
            )
            session.add(plan)
            session.add(task)
            await session.commit()
            return self._plan_to_dict(plan)

    async def _create_tool_proposal(
        self,
        spec: dict[str, Any],
        *,
        actor: str,
    ) -> dict[str, Any]:
        payload = spec.get("action_payload") or {}
        tool_name = str(payload.get("tool_name") or "unknown_tool")
        async with async_session() as session:
            existing = await self._existing_tool_proposal(
                session,
                spec["idempotency_key"],
            )
            if existing:
                return self._tool_proposal_to_dict(existing)
            proposal = OrchestrationToolProposal(
                id=f"toolprop_{uuid.uuid4().hex[:12]}",
                title=f"Tool proposal: {tool_name}",
                capability=str(payload.get("capability") or tool_name),
                status="proposed",
                risk_level=spec["risk_level"],
                side_effects=bool(payload.get("side_effects", True)),
                source_type=spec.get("source_type"),
                source_id=spec.get("source_id"),
                purpose=(
                    f"Provide the missing or unready capability `{tool_name}` for "
                    f"{payload.get('role_gap_title') or 'a role gap'}."
                ),
                input_schema={
                    "type": "object",
                    "additionalProperties": True,
                    "description": (
                        "Owner-reviewed schema must be finalized before live "
                        "executor activation."
                    ),
                },
                output_schema={
                    "type": "object",
                    "required": ["status"],
                    "properties": {"status": {"type": "string"}},
                },
                required_credentials=payload.get("required_credentials") or [],
                executor_kind=payload.get("executor_kind") or "proposed_executor",
                tests_required=[
                    "readiness reports configuration_required until credentials exist",
                    "missing approval blocks side-effectful execution",
                    "target mismatch, expiry, consumed approval, and replay are rejected",
                    "sandbox tests prove validation and failure behavior",
                ],
                rollback_notes=(
                    "Do not hot-load generated code. Roll back by leaving this "
                    "proposal inactive or reverting the reviewed code deployment."
                ),
                readiness_checks=[
                    "credentials_present_without_secret_logging",
                    "executor_registered",
                    "approval_policy_enforced",
                    "audit_evidence_recorded",
                ],
                sandbox_mode=settings.governor_tool_creation_mode,
                sandbox_result={
                    "status": "not_executed",
                    "detail": (
                        "No live code was generated or loaded. This proposal is an "
                        "approval-gated implementation contract."
                    ),
                },
                idempotency_key=spec["idempotency_key"],
                created_by=actor,
            )
            session.add(proposal)
            await session.commit()
            return self._tool_proposal_to_dict(proposal)

    async def _request_decision_approval(
        self,
        spec: dict[str, Any],
        *,
        actor: str,
    ) -> ApprovalRequest:
        async with async_session() as session:
            approval = await self._find_executable_approval(
                session,
                target_type="governor_decision",
                target_id=spec["idempotency_key"],
            )
            if approval:
                return approval
            approval = ApprovalRequest(
                id=f"appr_{uuid.uuid4().hex[:16]}",
                agent_id=self.CHIEF_AGENT_ID,
                action_type=f"governor:{spec['decision_type']}",
                action_description=spec["description"],
                action_payload={
                    "governor_decision": spec,
                    "replay_instruction": (
                        "Approve only through the owner console. Execution must "
                        "verify target_type=governor_decision and target_id matches "
                        "this decision idempotency key."
                    ),
                },
                requester=actor,
                requester_type="agent",
                risk_level=spec["risk_level"],
                target_type="governor_decision",
                target_id=spec["idempotency_key"],
                status="pending",
                expires_at=utc_now() + timedelta(days=7),
            )
            session.add(approval)
            await session.commit()
            return approval

    async def _agent_counts(self, session) -> dict[str, Any]:
        result = await session.execute(
            select(Agent.status, func.count()).group_by(Agent.status)
        )
        by_status = {status: count for status, count in result.all()}
        role_result = await session.execute(
            select(Agent.role_family, func.count()).group_by(Agent.role_family)
        )
        return {
            "total": sum(by_status.values()),
            "active": by_status.get("active", 0),
            "by_status": by_status,
            "by_role_family": {role: count for role, count in role_result.all()},
            "chief_operating_agent_present": self.CHIEF_AGENT_ID in {
                item[0]
                for item in (
                    await session.execute(select(Agent.id).where(Agent.id == self.CHIEF_AGENT_ID))
                ).all()
            },
        }

    async def _role_gap_counts(self, session) -> dict[str, Any]:
        result = await session.execute(
            select(RoleGap.status, func.count()).group_by(RoleGap.status)
        )
        by_status = {status: count for status, count in result.all()}
        active = sum(by_status.get(status, 0) for status in self.ACTIVE_GAP_STATUSES)
        return {"total": sum(by_status.values()), "active": active, "by_status": by_status}

    async def _plan_counts(self, session) -> dict[str, Any]:
        result = await session.execute(
            select(AutonomousPlan.status, func.count()).group_by(AutonomousPlan.status)
        )
        by_status = {status: count for status, count in result.all()}
        active = sum(by_status.get(status, 0) for status in self.ACTIVE_PLAN_STATUSES)
        return {"total": sum(by_status.values()), "active": active, "by_status": by_status}

    async def _workflow_counts(self, session) -> dict[str, Any]:
        result = await session.execute(
            select(WorkflowRun.status, func.count()).group_by(WorkflowRun.status)
        )
        by_status = {status: count for status, count in result.all()}
        recent = await session.execute(
            select(WorkflowRun)
            .order_by(desc(WorkflowRun.started_at))
            .limit(25)
        )
        recent_failed = [
            run.id
            for run in recent.scalars().all()
            if run.status == "failed" or run.error
        ]
        return {
            "total": sum(by_status.values()),
            "recent_failed": len(recent_failed),
            "recent_failed_ids": recent_failed[:10],
            "by_status": by_status,
        }

    async def _memory_finding_counts(self, session) -> dict[str, Any]:
        result = await session.execute(
            select(MemoryStewardFinding.status, func.count()).group_by(
                MemoryStewardFinding.status
            )
        )
        by_status = {status: count for status, count in result.all()}
        severity = await session.execute(
            select(MemoryStewardFinding.severity, func.count())
            .where(MemoryStewardFinding.status == "open")
            .group_by(MemoryStewardFinding.severity)
        )
        return {
            "total": sum(by_status.values()),
            "open_findings": by_status.get("open", 0),
            "by_status": by_status,
            "open_by_severity": {item: count for item, count in severity.all()},
        }

    async def _latest_company_context(self, session) -> dict[str, Any]:
        result = await session.execute(
            select(CompanyContextSnapshot)
            .order_by(desc(CompanyContextSnapshot.created_at))
            .limit(1)
        )
        snapshot = result.scalar_one_or_none()
        if not snapshot:
            return {
                "status": "missing",
                "stale": True,
                "snapshot_id": None,
                "detail": "No company-context snapshot is recorded.",
            }
        age = utc_now() - snapshot.created_at
        stale = age > timedelta(hours=settings.erpnext_drift_stale_after_hours)
        return {
            "status": snapshot.status,
            "stale": stale,
            "snapshot_id": snapshot.id,
            "source": snapshot.source,
            "source_hash": snapshot.source_hash,
            "company_namespace": snapshot.company_namespace,
            "created_at": snapshot.created_at.isoformat(),
            "age_hours": round(age.total_seconds() / 3600, 2),
            "counts": snapshot.erpnext_summary.get("counts", {}),
        }

    async def _recent_audit(self, session) -> dict[str, Any]:
        result = await session.execute(
            select(AuditEvent)
            .order_by(desc(AuditEvent.created_at))
            .limit(25)
        )
        events = result.scalars().all()
        failures = [event for event in events if event.outcome == "failure"]
        return {
            "recent_count": len(events),
            "recent_failures": len(failures),
            "failure_event_types": [event.event_type for event in failures[:10]],
        }

    async def _open_role_gaps(self, session) -> list[dict[str, Any]]:
        result = await session.execute(
            select(RoleGap)
            .where(RoleGap.status.in_(self.ACTIVE_GAP_STATUSES))
            .order_by(desc(RoleGap.created_at))
            .limit(25)
        )
        samples = []
        for gap in result.scalars().all():
            readiness = self._gap_tool_readiness(gap.requested_tools or [])
            samples.append(
                {
                    "gap_id": gap.id,
                    "title": gap.title,
                    "status": gap.status,
                    "severity": gap.severity,
                    "capability": gap.capability,
                    "source_type": gap.source_type,
                    "requested_tools": gap.requested_tools or [],
                    "missing_tools": readiness["missing_tools"],
                    "configuration_required_tools": readiness["configuration_required_tools"],
                    "unavailable_tools": readiness["unavailable_tools"],
                }
            )
        return samples

    def _tool_status(self) -> dict[str, Any]:
        if not self._tool_registry or not hasattr(self._tool_registry, "list_tool_contracts"):
            return {
                "registry_available": False,
                "total": 0,
                "counts_by_state": {},
                "side_effects_not_live": [],
            }
        contracts = self._tool_registry.list_tool_contracts()
        counts: dict[str, int] = {}
        side_effects_not_live = []
        for contract in contracts:
            state = contract.get("state") or "unknown"
            counts[state] = counts.get(state, 0) + 1
            if contract.get("side_effects") and state != "live":
                side_effects_not_live.append(
                    {
                        "name": contract.get("name"),
                        "state": state,
                        "readiness_reason": contract.get("readiness_reason"),
                        "requires_configuration": contract.get("requires_configuration"),
                    }
                )
        return {
            "registry_available": True,
            "total": len(contracts),
            "counts_by_state": counts,
            "side_effects_not_live": side_effects_not_live[:50],
        }

    def _gap_tool_readiness(self, tools: list[str]) -> dict[str, Any]:
        missing = []
        configuration_required = []
        unavailable = []
        for tool_name in tools:
            readiness = self._tool_readiness(tool_name)
            if readiness["state"] == "missing":
                missing.append(tool_name)
            elif readiness.get("requires_configuration") or readiness["state"] == (
                "configuration_required"
            ):
                configuration_required.append(readiness)
            elif readiness["state"] not in {"live", "advisory"}:
                unavailable.append(readiness)
        return {
            "missing_tools": missing,
            "configuration_required_tools": configuration_required,
            "unavailable_tools": unavailable,
        }

    def _tool_readiness(self, tool_name: str) -> dict[str, Any]:
        if not self._tool_registry:
            return {"name": tool_name, "state": "missing"}
        tool = None
        if hasattr(self._tool_registry, "get_tool"):
            tool = self._tool_registry.get_tool(tool_name)
        if not tool and hasattr(self._tool_registry, "list_tool_contracts"):
            for contract in self._tool_registry.list_tool_contracts():
                if contract.get("name") == tool_name:
                    return dict(contract)
        if not tool:
            return {"name": tool_name, "state": "missing"}
        if hasattr(self._tool_registry, "get_tool_readiness"):
            return {
                "name": tool_name,
                **(self._tool_registry.get_tool_readiness(tool_name) or {}),
            }
        return {"name": tool_name, "state": "unknown"}

    async def _owner_attention_summary(self) -> dict[str, Any]:
        if not self._planning or not hasattr(self._planning, "list_owner_attention"):
            return {"status": "unavailable", "counts": {}}
        try:
            result = await self._planning.list_owner_attention(status="active", limit=100)
            return {"status": "ready", "counts": result.get("counts", {})}
        except Exception as exc:
            return {"status": "degraded", "detail": str(exc), "counts": {}}

    async def _production_evidence_summary(self) -> dict[str, Any]:
        if not self._readiness_evidence:
            return {}
        try:
            return await self._readiness_evidence.summary()
        except Exception as exc:
            return {"status": "degraded", "detail": str(exc)}

    def _integration_summary(self) -> dict[str, Any]:
        comms = []
        if self._comms and hasattr(self._comms, "integration_status"):
            comms = self._comms.integration_status()
        erpnext = None
        if self._erpnext and hasattr(self._erpnext, "integration_status"):
            erpnext = self._erpnext.integration_status()
        return {
            "communications": comms,
            "erpnext": erpnext,
            "required_providers": sorted(settings.required_provider_names),
        }

    def _decision_spec(
        self,
        decision_type: str,
        title: str,
        description: str,
        *,
        risk_level: str,
        source_type: str | None,
        source_id: str | None,
        target_type: str | None,
        target_id: str | None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        action_payload = payload or {}
        basis = {
            "decision_type": decision_type,
            "source_type": source_type,
            "source_id": source_id,
            "target_type": target_type,
            "target_id": target_id,
            "payload": action_payload,
        }
        return {
            "decision_type": decision_type,
            "title": title,
            "description": description,
            "risk_level": risk_level,
            "source_type": source_type,
            "source_id": source_id,
            "target_type": target_type,
            "target_id": target_id,
            "action_payload": action_payload,
            "idempotency_key": "governor:"
            + hashlib.sha256(
                json.dumps(basis, sort_keys=True, default=str).encode()
            ).hexdigest()[:40],
        }

    def _tool_proposal_decision(
        self,
        gap: dict[str, Any],
        tool_name: str | None,
        reason: str,
        *,
        readiness: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        safe_tool_name = tool_name or "unknown_tool"
        side_effects = bool((readiness or {}).get("side_effects", True))
        risk_level = "medium" if side_effects else "low"
        return self._decision_spec(
            "propose_tool",
            f"Propose capability for {safe_tool_name}",
            (
                f"Role gap `{gap.get('title')}` requested `{safe_tool_name}`, "
                f"which is currently {reason.replace('_', ' ')}."
            ),
            risk_level=risk_level,
            source_type="role_gap",
            source_id=gap.get("gap_id"),
            target_type="tool",
            target_id=safe_tool_name,
            payload={
                "tool_name": safe_tool_name,
                "capability": gap.get("capability") or safe_tool_name,
                "role_gap_id": gap.get("gap_id"),
                "role_gap_title": gap.get("title"),
                "reason": reason,
                "side_effects": side_effects,
                "required_credentials": self._required_credentials_for_tool(safe_tool_name),
                "executor_kind": (readiness or {}).get("executor_kind")
                or "proposed_executor",
                "readiness": readiness or {},
            },
        )

    @staticmethod
    def _required_credentials_for_tool(tool_name: str) -> list[str]:
        lowered = tool_name.lower()
        if "email" in lowered:
            return ["SMTP_* or provider-specific email credentials"]
        if "sms" in lowered or "call" in lowered or "voice" in lowered:
            return ["telephony provider credentials"]
        if "erpnext" in lowered or "crm" in lowered or "ticket" in lowered:
            return ["ERPNEXT_API_KEY", "ERPNEXT_API_SECRET"]
        if "github" in lowered or "ci" in lowered:
            return ["GITHUB_TOKEN", "GITHUB_REPOSITORY"]
        return []

    async def _existing_decision(self, session, idempotency_key: str):
        result = await session.execute(
            select(OrchestrationGovernorDecision)
            .where(OrchestrationGovernorDecision.idempotency_key == idempotency_key)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _existing_tool_proposal(self, session, idempotency_key: str):
        result = await session.execute(
            select(OrchestrationToolProposal)
            .where(OrchestrationToolProposal.idempotency_key == idempotency_key)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _decision_by_key(self, idempotency_key: str) -> dict[str, Any] | None:
        async with async_session() as session:
            decision = await self._existing_decision(session, idempotency_key)
            return self._decision_to_dict(decision) if decision else None

    async def _mark_decision(
        self,
        idempotency_key: str,
        *,
        status: str,
        result: dict[str, Any],
        approval_id: str | None = None,
        plan_id: str | None = None,
        tool_proposal_id: str | None = None,
    ) -> None:
        async with async_session() as session:
            decision = await self._existing_decision(session, idempotency_key)
            if not decision:
                return
            decision.status = status
            decision.result = result
            decision.approval_id = approval_id or decision.approval_id
            decision.plan_id = plan_id or decision.plan_id
            decision.tool_proposal_id = tool_proposal_id or decision.tool_proposal_id
            decision.resolved_at = utc_now()
            await session.commit()

    async def _find_executable_approval(
        self,
        session,
        *,
        target_type: str,
        target_id: str,
    ) -> ApprovalRequest | None:
        now = utc_now()
        result = await session.execute(
            select(ApprovalRequest)
            .where(
                ApprovalRequest.target_type == target_type,
                ApprovalRequest.target_id == target_id,
                ApprovalRequest.status.in_(["pending", "approved"]),
                ApprovalRequest.consumed_at.is_(None),
            )
            .order_by(desc(ApprovalRequest.created_at))
            .limit(1)
        )
        approval = result.scalar_one_or_none()
        if not approval:
            return None
        if approval.expires_at and approval.expires_at <= now:
            return None
        return approval

    async def _record_run_audit(
        self,
        *,
        actor: str,
        run_id: str,
        status: str,
        counts: dict[str, Any],
        errors: list[dict[str, Any]],
        dry_run: bool,
    ) -> None:
        if not self._audit:
            return
        await self._audit.record(
            event_type="orchestration_governor.run",
            actor=actor,
            actor_type="agent",
            resource_type="orchestration_governor",
            resource_id=run_id,
            action="run",
            outcome=status,
            metadata={
                "policy_version": self.POLICY_VERSION,
                "dry_run": dry_run,
                "counts": counts,
                "errors": errors,
            },
        )
        await self._audit.record_control_evidence(
            control_id="autonomy.governor_run",
            control_area="soc2_change_management",
            actor=actor,
            outcome=status,
            evidence={
                "run_id": run_id,
                "policy_version": self.POLICY_VERSION,
                "counts": counts,
                "manual_only_external_side_effects": True,
            },
        )

    @staticmethod
    def _decision_counts(decisions: list[dict[str, Any]]) -> dict[str, Any]:
        counts = {
            "total": len(decisions),
            "by_type": {},
            "by_status": {},
            "tool_proposals": 0,
            "plans_delegated": 0,
            "approvals_requested": 0,
            "duplicates": 0,
        }
        for decision in decisions:
            decision_type = decision.get("decision_type") or "unknown"
            status = decision.get("status") or "unknown"
            counts["by_type"][decision_type] = counts["by_type"].get(decision_type, 0) + 1
            counts["by_status"][status] = counts["by_status"].get(status, 0) + 1
            if decision.get("tool_proposal_id"):
                counts["tool_proposals"] += 1
            if decision.get("plan_id"):
                counts["plans_delegated"] += 1
            if decision.get("approval_id"):
                counts["approvals_requested"] += 1
            if decision.get("duplicate"):
                counts["duplicates"] += 1
        return counts

    @staticmethod
    def _stable_hash(value: dict[str, Any]) -> str:
        payload = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def _operating_brief(
        self,
        snapshot: dict[str, Any],
        specs: list[dict[str, Any]],
    ) -> str:
        blockers = (snapshot.get("production_evidence") or {}).get("alerts", {})
        active_gaps = (snapshot.get("role_backlog") or {}).get("active", 0)
        memory_findings = (snapshot.get("memory") or {}).get("open_findings", 0)
        decision_titles = [spec["title"] for spec in specs[:5]]
        return (
            "Chief Operating Agent reviewed the current operating snapshot. "
            f"Active role gaps: {active_gaps}; open memory findings: {memory_findings}; "
            f"alert evidence status: {blockers.get('status', 'unknown')}. "
            "Recommended decisions: "
            + ("; ".join(decision_titles) if decision_titles else "none")
            + "."
        )

    def _redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            redacted = {}
            for key, item in value.items():
                key_text = str(key).lower()
                if any(marker in key_text for marker in self.SECRET_KEY_MARKERS):
                    redacted[key] = "[redacted]"
                else:
                    redacted[key] = self._redact(item)
            return redacted
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        return value

    @staticmethod
    def _agent_to_dict(agent: Agent) -> dict[str, Any]:
        return {
            "id": agent.id,
            "role_family": agent.role_family,
            "role_name": agent.role_name,
            "tools": agent.tools or [],
            "memory_namespace": agent.memory_namespace,
            "approval_policy": agent.approval_policy,
            "status": agent.status,
            "config": agent.config or {},
            "created_at": agent.created_at.isoformat(),
            "updated_at": agent.updated_at.isoformat(),
        }

    def _run_to_dict(
        self,
        run: OrchestrationGovernorRun,
        *,
        include_decisions: bool = True,
    ) -> dict[str, Any]:
        response = {
            "run_id": run.id,
            "status": run.status,
            "actor": run.actor,
            "policy_version": run.policy_version,
            "mode": run.mode,
            "auto_apply_low_risk": run.auto_apply_low_risk,
            "max_actions": run.max_actions,
            "snapshot_hash": run.snapshot_hash,
            "operating_snapshot": run.operating_snapshot or {},
            "operating_brief": run.operating_brief,
            "counts": run.counts or {},
            "errors": run.errors or [],
            "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "safety": self.safety_status(),
        }
        if include_decisions:
            decisions = sorted(run.decisions, key=lambda item: item.created_at)
            response["decisions"] = [
                self._decision_to_dict(decision) for decision in decisions
            ]
        return response

    @staticmethod
    def _decision_to_dict(decision: OrchestrationGovernorDecision) -> dict[str, Any]:
        return {
            "id": decision.id,
            "run_id": decision.run_id,
            "decision_type": decision.decision_type,
            "title": decision.title,
            "description": decision.description,
            "status": decision.status,
            "risk_level": decision.risk_level,
            "source_type": decision.source_type,
            "source_id": decision.source_id,
            "target_type": decision.target_type,
            "target_id": decision.target_id,
            "action_payload": decision.action_payload or {},
            "result": decision.result or {},
            "error": decision.error,
            "approval_id": decision.approval_id,
            "plan_id": decision.plan_id,
            "tool_proposal_id": decision.tool_proposal_id,
            "idempotency_key": decision.idempotency_key,
            "created_at": decision.created_at.isoformat(),
            "resolved_at": decision.resolved_at.isoformat()
            if decision.resolved_at
            else None,
        }

    @staticmethod
    def _tool_proposal_to_dict(proposal: OrchestrationToolProposal) -> dict[str, Any]:
        return {
            "id": proposal.id,
            "title": proposal.title,
            "capability": proposal.capability,
            "status": proposal.status,
            "risk_level": proposal.risk_level,
            "side_effects": proposal.side_effects,
            "source_type": proposal.source_type,
            "source_id": proposal.source_id,
            "purpose": proposal.purpose,
            "input_schema": proposal.input_schema or {},
            "output_schema": proposal.output_schema or {},
            "required_credentials": proposal.required_credentials or [],
            "executor_kind": proposal.executor_kind,
            "tests_required": proposal.tests_required or [],
            "rollback_notes": proposal.rollback_notes,
            "readiness_checks": proposal.readiness_checks or [],
            "sandbox_mode": proposal.sandbox_mode,
            "sandbox_result": proposal.sandbox_result or {},
            "approval_id": proposal.approval_id,
            "idempotency_key": proposal.idempotency_key,
            "created_by": proposal.created_by,
            "created_at": proposal.created_at.isoformat(),
            "updated_at": proposal.updated_at.isoformat(),
        }

    @staticmethod
    def _plan_to_dict(plan: AutonomousPlan) -> dict[str, Any]:
        tasks = sorted(plan.tasks, key=lambda item: item.sequence)
        return {
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
            "tasks": [
                {
                    "id": task.id,
                    "title": task.title,
                    "task_type": task.task_type,
                    "status": task.status,
                    "target_type": task.target_type,
                    "target_id": task.target_id,
                    "approval_id": task.approval_id,
                }
                for task in tasks
            ],
            "created_at": plan.created_at.isoformat(),
            "updated_at": plan.updated_at.isoformat(),
            "completed_at": plan.completed_at.isoformat() if plan.completed_at else None,
        }
