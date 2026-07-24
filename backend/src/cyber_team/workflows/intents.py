"""Generated workflow intents from company context, roles, and gaps."""

from __future__ import annotations

import hashlib
import json
import uuid
from types import SimpleNamespace
from typing import Any

from sqlalchemy import desc, select

from cyber_team.agents.manager import slug_id
from cyber_team.agents.orchestrator import Orchestrator
from cyber_team.clock import utc_now
from cyber_team.db import async_session
from cyber_team.db.models import (
    Agent,
    CompanyContextSnapshot,
    RoleGap,
    WorkflowIntent,
)
from cyber_team.operations.tool_readiness_policy import tool_is_required_for_readiness

ACTIVE_INTENT_STATUSES = {"proposed", "instantiated", "blocked"}
RESOLVED_INTENT_STATUSES = {"dismissed", "resolved"}
GRAPH_TOOL_NAMES = ("memory_recall", "memory_remember")
SIDE_EFFECT_APPROVAL_TOOLS = {
    "approval_resolve",
    "call_make",
    "crm_contact_update",
    "crm_deal_update",
    "crm_lead_create",
    "email_send",
    "make_call",
    "erpnext_create_lead",
    "erpnext_invoice_create",
    "message_send",
    "procurement_request",
    "payment_charge",
    "payment_refund",
    "send_email",
    "send_message",
    "send_sms",
    "sms_send",
    "task_create",
    "task_update",
    "ticket_create",
    "ticket_update",
}
KNOWN_ROLE_FAMILIES = {
    "communications",
    "company_builder",
    "engineering",
    "finance",
    "hr",
    "knowledge",
    "legal",
    "marketing",
    "operations",
    "product",
    "sales",
    "security",
    "supervisor",
    "support",
}
CAPABILITY_FAMILY_MAP = {
    "accounting": "finance",
    "analytics": "marketing",
    "company_knowledge_management": "knowledge",
    "crm": "sales",
    "document_indexing": "knowledge",
    "email": "communications",
    "knowledge": "knowledge",
    "knowledge_management": "knowledge",
    "memory_consolidation": "knowledge",
    "memory_curation": "knowledge",
    "memory_governance": "knowledge",
    "memory_operations": "knowledge",
    "memory_reliability": "knowledge",
    "messaging": "communications",
    "outbound_voice": "communications",
    "payments": "finance",
    "research": "knowledge",
    "retrieval_policy": "knowledge",
    "scheduling": "operations",
    "sms_messaging": "communications",
    "workflow_reliability": "operations",
}
KNOWLEDGE_ROLE_GAP_HINTS = {
    "company memory steward",
    "document index",
    "empty recall",
    "knowledge",
    "memory",
    "namespace",
    "recall",
    "remember",
    "research",
    "retrieval",
    "stale memory",
    "steward",
}
KNOWLEDGE_SAFE_ROLE_TOOLS = {
    "approval_request",
    "company_profile_read",
    "document_index",
    "knowledge_query",
    "memory_recall",
    "memory_remember",
    "owner_notify",
    "process_audit",
    "research_report",
    "role_gap_report",
    "web_search",
}
CORE_AGENT_NAME_HINTS = {
    "company_builder": ("company builder", "team builder"),
    "supervisor": ("supervisor", "orchestrator"),
}
CORE_AGENT_FAMILY_FALLBACKS = {
    "supervisor": ("orchestration",),
}


class WorkflowIntentService:
    """Creates reviewable workflow proposals from live company operating context."""

    def __init__(
        self,
        *,
        orchestrator: Orchestrator,
        tool_registry,
        llm_gateway=None,
        audit_service=None,
        session_factory=async_session,
    ) -> None:
        self._orchestrator = orchestrator
        self._tool_registry = tool_registry
        self._llm_gateway = llm_gateway
        self._audit = audit_service
        self._session_factory = session_factory

    async def generate_from_company_context(
        self,
        *,
        snapshot_id: str | None = None,
        actor: str = "workflow_intent_service",
        limit: int = 75,
        instantiate_low_risk: bool = False,
    ) -> dict[str, Any]:
        snapshot = await self._load_snapshot(snapshot_id)
        if not snapshot:
            result = {
                "status": "missing_company_context",
                "created": 0,
                "updated": 0,
                "unchanged": 0,
                "instantiated": 0,
                "intents": [],
                "errors": ["No active company-context snapshot is available."],
            }
            await self._record("workflow_intents.generate", actor, "failure", result)
            return result

        agents = await self._load_active_agents()
        role_gaps = await self._load_role_gaps(snapshot)
        llm_readiness = await self._llm_readiness()
        proposals = self._build_proposals(snapshot, agents, role_gaps, llm_readiness)
        proposals = proposals[: max(1, min(limit, 200))]
        upserted = await self._upsert_proposals(proposals, actor=actor)

        instantiated: list[dict[str, Any]] = []
        if instantiate_low_risk:
            for intent in upserted["intents"]:
                if self._can_auto_instantiate(intent):
                    try:
                        workflow = await self.instantiate_intent(
                            intent["id"],
                            actor=actor,
                            allow_owner_review=False,
                        )
                        instantiated.append(workflow)
                    except Exception as exc:
                        intent.setdefault("errors", []).append(str(exc))

        result = {
            "status": "completed",
            "snapshot_id": snapshot.id,
            "source_hash": snapshot.source_hash,
            "company_namespace": snapshot.company_namespace,
            **upserted,
            "instantiated": len(instantiated),
            "instantiated_workflows": instantiated,
        }
        await self._record("workflow_intents.generate", actor, "success", result)
        return result

    async def list_intents(
        self,
        *,
        status: str | None = "proposed,instantiated,blocked",
        category: str | None = None,
        source_type: str | None = None,
        company_namespace: str | None = None,
        readiness_status: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(limit, 500))
        async with self._session_factory() as session:
            query = select(WorkflowIntent)
            if status:
                statuses = [part.strip() for part in status.split(",") if part.strip()]
                if statuses:
                    query = query.where(WorkflowIntent.status.in_(statuses))
            if category:
                query = query.where(WorkflowIntent.category == category)
            if source_type:
                query = query.where(WorkflowIntent.source_type == source_type)
            if company_namespace:
                query = query.where(WorkflowIntent.company_namespace == company_namespace)
            query = query.order_by(desc(WorkflowIntent.updated_at)).limit(safe_limit)
            rows = (await session.execute(query)).scalars().all()

        items = [self._intent_to_dict(row) for row in rows]
        if readiness_status:
            items = [
                item
                for item in items
                if item.get("readiness", {}).get("status") == readiness_status
            ]
        return {
            "items": items,
            "groups": self._groups(items),
            "counts": self._counts(items),
        }

    async def get_intent(self, intent_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            intent = await session.get(WorkflowIntent, intent_id)
            return self._intent_to_dict(intent) if intent else None

    async def instantiate_intent(
        self,
        intent_id: str,
        *,
        actor: str,
        allow_owner_review: bool = True,
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            intent = await session.get(WorkflowIntent, intent_id)
            if not intent:
                raise ValueError(f"Workflow intent {intent_id} not found")
            if intent.status in RESOLVED_INTENT_STATUSES:
                raise ValueError(f"Workflow intent {intent_id} is {intent.status}")
            if intent.workflow_id:
                existing = await self._orchestrator.get_workflow(intent.workflow_id)
                if existing:
                    if intent.status != "instantiated" or intent.resolved_at is None:
                        now = utc_now()
                        intent.status = "instantiated"
                        intent.resolution = {
                            **(intent.resolution or {}),
                            "instantiated_by": actor,
                            "workflow_id": existing["id"],
                            "instantiated_at": now.isoformat(),
                        }
                        intent.updated_at = now
                        intent.resolved_at = now
                        await session.commit()
                    return existing
            readiness = intent.readiness or {}
            readiness_status = readiness.get("status")
            if readiness_status in {"blocked", "configuration_required"}:
                reasons = "; ".join(readiness.get("blockers") or [])
                raise ValueError(
                    "Workflow intent is not ready to instantiate"
                    + (f": {reasons}" if reasons else "")
                )
            if readiness_status == "owner_review" and not allow_owner_review:
                raise ValueError("Workflow intent requires owner review before auto-instantiation")
            graph = intent.graph_definition or {}
            if not graph.get("entry_node") or not graph.get("nodes"):
                raise ValueError("Workflow intent has no executable graph definition")

        workflow = await self._orchestrator.create_workflow(
            SimpleNamespace(
                name=intent.title,
                description=intent.description,
                graph_definition=graph,
                trigger_type=intent.trigger_type,
                trigger_config={
                    **(intent.trigger_config or {}),
                    "workflow_intent_id": intent.id,
                    "source_type": intent.source_type,
                    "source_id": intent.source_id,
                    "source_hash": intent.source_hash,
                    "category": intent.category,
                    "created_by": actor,
                },
            )
        )

        async with self._session_factory() as session:
            fresh = await session.get(WorkflowIntent, intent_id)
            if fresh:
                fresh.workflow_id = workflow["id"]
                fresh.status = "instantiated"
                fresh.resolution = {
                    **(fresh.resolution or {}),
                    "instantiated_by": actor,
                    "workflow_id": workflow["id"],
                    "instantiated_at": utc_now().isoformat(),
                }
                fresh.updated_at = utc_now()
                fresh.resolved_at = utc_now()
                await session.commit()

        await self._record(
            "workflow_intent.instantiated",
            actor,
            "success",
            {
                "intent_id": intent_id,
                "workflow_id": workflow["id"],
                "source_type": intent.source_type,
                "source_id": intent.source_id,
            },
        )
        return workflow

    async def resolve_intent(
        self,
        intent_id: str,
        *,
        status: str,
        note: str = "",
        actor: str,
    ) -> dict[str, Any]:
        if status not in RESOLVED_INTENT_STATUSES:
            raise ValueError("Workflow intent status must be dismissed or resolved")
        async with self._session_factory() as session:
            intent = await session.get(WorkflowIntent, intent_id)
            if not intent:
                raise ValueError(f"Workflow intent {intent_id} not found")
            intent.status = status
            intent.resolution = {
                **(intent.resolution or {}),
                "status": status,
                "note": note,
                "resolved_by": actor,
                "resolved_at": utc_now().isoformat(),
            }
            intent.updated_at = utc_now()
            intent.resolved_at = utc_now()
            await session.commit()
            result = self._intent_to_dict(intent)
        await self._record(
            "workflow_intent.resolved",
            actor,
            "success",
            {"intent_id": intent_id, "status": status},
        )
        return result

    async def readiness(self) -> dict[str, Any]:
        summary = await self.list_intents(status="proposed,blocked,instantiated", limit=500)
        counts = summary["counts"]
        active_count = counts.get("total", 0)
        blocked_count = counts.get("by_readiness", {}).get("blocked", 0)
        configuration_required_count = counts.get("by_readiness", {}).get(
            "configuration_required",
            0,
        )
        optional_disabled_count = counts.get("optional_disabled_count", 0)
        missing_agent_count = counts.get("missing_agent_count", 0)
        status = "ready"
        detail = "Generated workflow intent service is ready."
        if not active_count:
            status = "waiting"
            detail = "No generated workflow intents have been created yet."
        elif blocked_count or configuration_required_count:
            status = "degraded"
            if missing_agent_count:
                detail = "Some generated workflow intents need core agents activated."
            else:
                detail = "Some generated workflow intents need required tool readiness."
        elif optional_disabled_count:
            detail = (
                "Generated workflow intents are usable; some optional external channels "
                "are disabled."
            )
        return {
            "status": status,
            "blocking": False,
            "active_count": active_count,
            "instantiated_count": counts.get("by_status", {}).get("instantiated", 0),
            "blocked_count": blocked_count,
            "configuration_required_count": configuration_required_count,
            "owner_review_count": counts.get("by_readiness", {}).get("owner_review", 0),
            "ready_count": counts.get("by_readiness", {}).get("ready", 0),
            "optional_disabled_count": optional_disabled_count,
            "optional_disabled_tool_count": counts.get("optional_disabled_tool_count", 0),
            "approval_gated_tool_count": counts.get("approval_gated_tool_count", 0),
            "configuration_required_tool_count": counts.get(
                "configuration_required_tool_count",
                0,
            ),
            "missing_agent_count": missing_agent_count,
            "by_recommended_action": counts.get("by_recommended_action", {}),
            "groups": summary["groups"],
            "detail": detail,
        }

    async def _load_snapshot(self, snapshot_id: str | None) -> CompanyContextSnapshot | None:
        async with self._session_factory() as session:
            if snapshot_id:
                return await session.get(CompanyContextSnapshot, snapshot_id)
            result = await session.execute(
                select(CompanyContextSnapshot)
                .where(CompanyContextSnapshot.status == "active")
                .order_by(desc(CompanyContextSnapshot.created_at))
                .limit(1)
            )
            return result.scalar_one_or_none()

    async def _load_active_agents(self) -> dict[str, Any]:
        async with self._session_factory() as session:
            result = await session.execute(select(Agent).where(Agent.status == "active"))
            agents = result.scalars().all()
        by_id = {agent.id: agent for agent in agents}
        by_family: dict[str, Agent] = {}
        by_name: dict[str, Agent] = {}
        for agent in agents:
            by_family.setdefault(agent.role_family, agent)
            by_name.setdefault(slug_id(agent.role_name), agent)
        return {"by_id": by_id, "by_family": by_family, "by_name": by_name, "all": agents}

    async def _load_role_gaps(self, snapshot: CompanyContextSnapshot) -> list[RoleGap]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(RoleGap)
                .where(
                    RoleGap.status.in_(["open", "proposed"]),
                    RoleGap.company_namespace == snapshot.company_namespace,
                )
                .order_by(desc(RoleGap.created_at))
                .limit(50)
            )
            return list(result.scalars().all())

    def _build_proposals(
        self,
        snapshot: CompanyContextSnapshot,
        agents: dict[str, Any],
        role_gaps: list[RoleGap],
        llm_readiness: dict[str, Any],
    ) -> list[dict[str, Any]]:
        operating_model = snapshot.operating_model or {}
        proposals = []
        for role_spec in operating_model.get("planned_role_specs") or []:
            proposal = self._proposal_from_role_spec(
                snapshot,
                role_spec,
                agents,
                llm_readiness,
            )
            if proposal:
                proposals.append(proposal)
        for loop in operating_model.get("adaptive_loops") or []:
            proposal = self._proposal_from_loop(snapshot, loop, agents, llm_readiness)
            if proposal:
                proposals.append(proposal)
        for gap in role_gaps:
            proposal = self._proposal_from_role_gap(snapshot, gap, agents, llm_readiness)
            if proposal:
                proposals.append(proposal)
        return self._dedupe_proposals(proposals)

    def _proposal_from_role_spec(
        self,
        snapshot: CompanyContextSnapshot,
        role_spec: dict[str, Any],
        agents: dict[str, Any],
        llm_readiness: dict[str, Any],
    ) -> dict[str, Any] | None:
        role_name = str(role_spec.get("name") or "").strip()
        role_family = str(role_spec.get("family") or "").strip()
        if not role_name or not role_family:
            return None
        agent = self._find_agent_for_role(role_spec, agents)
        requested_tools = self._unique_strings(role_spec.get("default_tools") or [])
        capabilities = self._unique_strings(role_spec.get("capabilities") or [])
        capability = capabilities[0] if capabilities else None
        memory_namespace = role_spec.get("memory_namespace") or snapshot.company_namespace
        graph = self._agent_operating_graph(
            agent_id=agent.id if agent else slug_id(role_name),
            role_name=role_name,
            role_family=role_family,
            memory_namespace=memory_namespace,
            company_namespace=snapshot.company_namespace,
            requested_tools=requested_tools,
            prompt=(
                f"Review the current company context as {role_name}. Identify the next "
                "safe operating action, blockers, needed owner approvals, and memory updates. "
                "Do not perform external mutations from this advisory workflow."
            ),
        )
        readiness = self._readiness(
            requested_tools=requested_tools,
            agent=agent,
            role_family=role_family,
            approval_policy=str(role_spec.get("approval_policy") or "auto"),
            llm_readiness=llm_readiness,
        )
        risk_level = self._risk_level(requested_tools, role_spec.get("approval_policy"))
        return {
            "title": f"{role_name} operating loop",
            "description": (
                f"Generated from ERPNext company context for the {role_name} role. "
                "Runs an advisory operating loop that recalls context, asks the role "
                "for next safe actions, and records the result into memory."
            ),
            "status": "proposed",
            "category": "role_capability",
            "business_function": self._business_function(role_family, capability),
            "source_type": "company_context_snapshot",
            "source_id": snapshot.id,
            "source_hash": snapshot.source_hash,
            "company_namespace": snapshot.company_namespace,
            "role_family": role_family,
            "role_name": role_name,
            "capability": capability,
            "risk_level": risk_level,
            "trigger_type": "manual",
            "trigger_config": {"source": "generated_role_capability"},
            "graph_definition": graph,
            "requested_tools": requested_tools,
            "required_agents": [agent.id] if agent else [slug_id(role_name)],
            "tool_readiness": self._tool_readiness(requested_tools),
            "readiness": readiness,
            "approval_required": self._approval_required(requested_tools, role_spec),
            "evidence": {
                "role_spec": self._redact_large(role_spec),
                "rationale": role_spec.get("rationale") or [],
                "activation_triggers": role_spec.get("activation_triggers") or [],
            },
            "dedupe_key": self._dedupe_key(
                "role",
                snapshot.source_hash,
                role_family,
                role_name,
                capability,
            ),
        }

    def _proposal_from_loop(
        self,
        snapshot: CompanyContextSnapshot,
        loop: dict[str, Any],
        agents: dict[str, Any],
        llm_readiness: dict[str, Any],
    ) -> dict[str, Any] | None:
        loop_id = str(loop.get("id") or "").strip()
        owner_family = str(loop.get("owner_family") or "supervisor").strip()
        if not loop_id:
            return None
        agent = self._agent_for_family(agents, owner_family) or self._agent_for_family(
            agents,
            "supervisor",
        )
        title = self._humanize(loop_id)
        graph = self._agent_operating_graph(
            agent_id=agent.id if agent else owner_family,
            role_name=agent.role_name if agent else title,
            role_family=owner_family,
            memory_namespace=f"{snapshot.company_namespace}:operations",
            company_namespace=snapshot.company_namespace,
            requested_tools=[],
            prompt=(
                f"Run the {title} adaptive operating loop. Purpose: "
                f"{loop.get('purpose', 'Maintain company operations.')}. Trigger: "
                f"{loop.get('trigger', 'manual review')}. Produce next safe actions, "
                "approval needs, and memory notes without external mutations."
            ),
        )
        return {
            "title": title,
            "description": str(loop.get("purpose") or "Generated adaptive operating loop."),
            "status": "proposed",
            "category": "adaptive_loop",
            "business_function": self._business_function(owner_family, None),
            "source_type": "company_context_snapshot",
            "source_id": snapshot.id,
            "source_hash": snapshot.source_hash,
            "company_namespace": snapshot.company_namespace,
            "role_family": owner_family,
            "role_name": agent.role_name if agent else None,
            "capability": loop_id,
            "risk_level": "low",
            "trigger_type": "manual",
            "trigger_config": {"source": "generated_adaptive_loop", "loop_id": loop_id},
            "graph_definition": graph,
            "requested_tools": [],
            "required_agents": [agent.id] if agent else [owner_family],
            "tool_readiness": self._tool_readiness([]),
            "readiness": self._readiness(
                requested_tools=[],
                agent=agent,
                role_family=owner_family,
                approval_policy="auto",
                llm_readiness=llm_readiness,
            ),
            "approval_required": False,
            "evidence": {
                "loop": self._redact_large(loop),
                "approval_boundary": loop.get("approval_boundary"),
            },
            "dedupe_key": self._dedupe_key("loop", snapshot.source_hash, loop_id),
        }

    def _proposal_from_role_gap(
        self,
        snapshot: CompanyContextSnapshot,
        gap: RoleGap,
        agents: dict[str, Any],
        llm_readiness: dict[str, Any],
    ) -> dict[str, Any] | None:
        agent = self._agent_for_family(agents, "company_builder") or self._agent_for_family(
            agents,
            "supervisor",
        )
        proposed_role = gap.proposed_role or {}
        manifest = proposed_role.get("manifest_payload") or proposed_role
        role_family = self._family_from_gap(gap, manifest)
        manifest_family = str(manifest.get("family") or "").strip()
        role_name = (
            manifest.get("name")
            if manifest_family == role_family
            else (gap.context or {}).get("role_name")
        ) or gap.title
        requested_tools = self._requested_tools_for_role_gap(gap, role_family)
        excluded_tools = [
            tool_name
            for tool_name in self._unique_strings(gap.requested_tools or [])
            if tool_name not in requested_tools
        ]
        graph = self._agent_operating_graph(
            agent_id=agent.id if agent else "company_builder",
            role_name=agent.role_name if agent else "Company Builder",
            role_family="company_builder",
            memory_namespace=f"{snapshot.company_namespace}:gaps",
            company_namespace=snapshot.company_namespace,
            requested_tools=requested_tools,
            prompt=(
                f"Review role gap {gap.id}: {gap.title}. Determine whether to create, "
                "defer, dismiss, or request owner approval. Preserve tool readiness and "
                "do not mutate external systems."
            ),
        )
        risk_level = self._risk_level(requested_tools, gap.severity)
        if role_family == "knowledge" and not any(
            tool_name in SIDE_EFFECT_APPROVAL_TOOLS for tool_name in requested_tools
        ):
            risk_level = "low"
        return {
            "title": f"Role gap follow-up: {gap.title}",
            "description": gap.description,
            "status": "proposed",
            "category": "role_gap",
            "business_function": self._business_function(role_family, gap.capability),
            "source_type": "role_gap",
            "source_id": gap.id,
            "source_hash": (gap.context or {}).get("source_hash") or snapshot.source_hash,
            "company_namespace": snapshot.company_namespace,
            "role_family": role_family,
            "role_name": role_name,
            "capability": gap.capability,
            "risk_level": risk_level,
            "trigger_type": "manual",
            "trigger_config": {"source": "generated_role_gap_follow_up"},
            "graph_definition": graph,
            "requested_tools": requested_tools,
            "required_agents": [agent.id] if agent else ["company_builder"],
            "tool_readiness": self._tool_readiness(requested_tools),
            "readiness": self._readiness(
                requested_tools=requested_tools,
                agent=agent,
                role_family="company_builder",
                approval_policy="auto",
                llm_readiness=llm_readiness,
            ),
            "approval_required": risk_level in {"medium", "high", "critical"},
            "evidence": {
                "role_gap": {
                    "id": gap.id,
                    "status": gap.status,
                    "severity": gap.severity,
                    "context": self._redact_large(gap.context or {}),
                    "proposed_role": self._redact_large(gap.proposed_role or {}),
                    "excluded_unsafe_requested_tools": excluded_tools,
                }
            },
            "dedupe_key": self._dedupe_key("role_gap", gap.id, gap.updated_at.isoformat()),
        }

    async def _upsert_proposals(
        self,
        proposals: list[dict[str, Any]],
        *,
        actor: str,
    ) -> dict[str, Any]:
        created = 0
        updated = 0
        unchanged = 0
        superseded = 0
        now = utc_now()
        intents: list[dict[str, Any]] = []
        async with self._session_factory() as session:
            for proposal in proposals:
                result = await session.execute(
                    select(WorkflowIntent).where(
                        WorkflowIntent.dedupe_key == proposal["dedupe_key"]
                    )
                )
                existing = result.scalar_one_or_none()
                if existing:
                    before = self._fingerprint_intent(existing)
                    if existing.status not in RESOLVED_INTENT_STATUSES:
                        self._apply_proposal(existing, proposal)
                        existing.updated_at = now
                    after = self._fingerprint_intent(existing)
                    if before == after:
                        unchanged += 1
                    else:
                        updated += 1
                    superseded += await self._supersede_replaced_intents(
                        session,
                        proposal,
                        actor=actor,
                        now=now,
                    )
                    intents.append(self._intent_to_dict(existing))
                    continue
                intent = WorkflowIntent(
                    id=str(uuid.uuid4()),
                    proposed_by=actor,
                    created_at=now,
                    updated_at=now,
                    resolution={},
                    workflow_id=None,
                    workflow_template_id=None,
                    approval_id=None,
                    **proposal,
                )
                session.add(intent)
                created += 1
                superseded += await self._supersede_replaced_intents(
                    session,
                    proposal,
                    actor=actor,
                    now=now,
                )
                intents.append(self._intent_to_dict(intent))
            await session.commit()
        return {
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "superseded": superseded,
            "intents": sorted(
                intents,
                key=lambda item: (
                    item["business_function"].lower(),
                    item["title"].lower(),
                ),
            ),
        }

    @staticmethod
    def _apply_proposal(intent: WorkflowIntent, proposal: dict[str, Any]) -> None:
        for key, value in proposal.items():
            if key == "dedupe_key":
                continue
            setattr(intent, key, value)

    @staticmethod
    async def _supersede_replaced_intents(
        session,
        proposal: dict[str, Any],
        *,
        actor: str,
        now,
    ) -> int:
        result = await session.execute(
            select(WorkflowIntent).where(
                WorkflowIntent.status.in_(ACTIVE_INTENT_STATUSES),
                WorkflowIntent.company_namespace == proposal["company_namespace"],
                WorkflowIntent.source_type == proposal["source_type"],
                WorkflowIntent.source_id == proposal["source_id"],
                WorkflowIntent.category == proposal["category"],
                WorkflowIntent.title == proposal["title"],
                WorkflowIntent.dedupe_key != proposal["dedupe_key"],
            )
        )
        stale = result.scalars().all()
        for intent in stale:
            intent.status = "resolved"
            intent.resolution = {
                **(intent.resolution or {}),
                "status": "resolved",
                "reason": "superseded_by_regenerated_intent",
                "superseded_by_dedupe_key": proposal["dedupe_key"],
                "resolved_by": actor,
                "resolved_at": now.isoformat(),
            }
            intent.updated_at = now
            intent.resolved_at = now
        return len(stale)

    def _find_agent_for_role(
        self,
        role_spec: dict[str, Any],
        agents: dict[str, Any],
    ) -> Agent | None:
        role_name = str(role_spec.get("name") or "")
        role_family = str(role_spec.get("family") or "")
        return (
            agents["by_name"].get(slug_id(role_name))
            or self._agent_for_family(agents, role_family)
            or agents["by_id"].get(str(role_spec.get("agent_id") or ""))
        )

    @staticmethod
    def _agent_for_family(agents: dict[str, Any], role_family: str) -> Agent | None:
        family = str(role_family or "").strip().lower()
        if not family:
            return None
        exact = agents["by_family"].get(family)
        if exact:
            return exact
        by_name = agents["by_name"]
        for candidate in (family, family.replace("_", "-"), family.replace("_", " ")):
            match = by_name.get(slug_id(candidate))
            if match:
                return match
        hints = CORE_AGENT_NAME_HINTS.get(family, ())
        if hints:
            candidates = [
                agent
                for agent in agents.get("all", [])
                if any(
                    hint in f"{agent.role_name} {agent.id}".replace("_", " ").lower()
                    for hint in hints
                )
            ]
            if candidates:
                return sorted(
                    candidates,
                    key=lambda agent: (
                        "baseline" in f"{agent.role_name} {agent.id}".lower(),
                        str(agent.created_at or ""),
                        agent.id,
                    ),
                )[0]
        for fallback_family in CORE_AGENT_FAMILY_FALLBACKS.get(family, ()):
            fallback = agents["by_family"].get(fallback_family)
            if fallback:
                return fallback
        return None

    def _agent_operating_graph(
        self,
        *,
        agent_id: str,
        role_name: str,
        role_family: str,
        memory_namespace: str,
        company_namespace: str,
        requested_tools: list[str],
        prompt: str,
    ) -> dict[str, Any]:
        safe_tools = ", ".join(requested_tools) if requested_tools else "advisory only"
        return {
            "entry_node": "recall_context",
            "nodes": [
                {
                    "id": "recall_context",
                    "type": "tool",
                    "tool_name": "memory_recall",
                    "args_template": {
                        "query": f"{role_name} objectives blockers approvals latest decisions",
                        "namespace": company_namespace,
                        "limit": 8,
                    },
                },
                {
                    "id": "delegate_role",
                    "type": "agent",
                    "agent_id": agent_id,
                    "task_template": (
                        f"{prompt}\n\nRole family: {role_family}\n"
                        f"Requested role tools: {safe_tools}\n"
                        "Use recalled context from {recall_context_output}. "
                        "Return concrete next actions, risk, confidence, and owner-review needs."
                    ),
                },
                {
                    "id": "record_result",
                    "type": "tool",
                    "tool_name": "memory_remember",
                    "args_template": {
                        "content": (
                            f"Generated workflow intent for {role_name} completed. "
                            "Role output: {delegate_role_output}"
                        ),
                        "memory_type": "episodic",
                        "namespace": memory_namespace,
                        "importance": 0.65,
                    },
                },
            ],
            "edges": [
                {"from": "recall_context", "to": "delegate_role"},
                {"from": "delegate_role", "to": "record_result"},
            ],
            "metadata": {
                "generated_by": "workflow_intent_service",
                "company_namespace": company_namespace,
                "role_family": role_family,
                "requested_tools": requested_tools,
                "external_mutation_allowed": False,
            },
        }

    def _readiness(
        self,
        *,
        requested_tools: list[str],
        agent: Agent | None,
        role_family: str,
        approval_policy: str,
        llm_readiness: dict[str, Any],
    ) -> dict[str, Any]:
        graph_readiness = self._tool_readiness(list(GRAPH_TOOL_NAMES))
        requested_readiness = self._tool_readiness(requested_tools)
        blockers: list[str] = []
        warnings: list[str] = []
        if not agent:
            blockers.append(f"No active agent is available for role family {role_family}.")
        for item in graph_readiness:
            if not item.get("executable"):
                blockers.append(
                    f"Required graph tool {item['tool_name']} is {item['state']}: "
                    f"{item.get('readiness_reason')}"
                )
        if agent and llm_readiness.get("mode") != "live":
            blockers.append(
                "Agent delegation requires a live LLM provider: "
                f"{llm_readiness.get('detail') or llm_readiness.get('mode')}"
            )
        configuration_required_tools = [
            item
            for item in requested_readiness
            if item.get("workflow_impact") == "configuration_required"
        ]
        optional_disabled_tools = [
            item
            for item in requested_readiness
            if item.get("workflow_impact") == "optional_disabled"
        ]
        approval_gated_tools = [
            item
            for item in requested_readiness
            if item.get("workflow_impact") == "approval_gated"
        ]
        for item in requested_readiness:
            if item.get("state") not in {"live", "advisory"} or not item.get("executable"):
                if item.get("workflow_impact") == "optional_disabled":
                    warnings.append(
                        f"Requested role tool {item['tool_name']} is optional_disabled: "
                        f"{item.get('readiness_reason')}. It is not listed in "
                        "REQUIRED_COMMUNICATION_PROVIDERS, so the advisory workflow can "
                        "still run without this external channel."
                    )
                    continue
                warnings.append(
                    f"Requested role tool {item['tool_name']} is {item['state']}: "
                    f"{item.get('readiness_reason')}"
                )
            elif item.get("side_effects"):
                warnings.append(
                    f"Requested role tool {item['tool_name']} has external side effects "
                    "and remains approval-gated."
                )
        if blockers:
            if agent and llm_readiness.get("mode") in {
                "configuration_required",
                "unavailable",
            }:
                status = "configuration_required"
                recommended_action = "validate_llm_provider"
            else:
                status = "blocked"
                recommended_action = "create_or_activate_agent"
        elif configuration_required_tools:
            status = "configuration_required"
            recommended_action = "configure_tools"
        elif warnings or approval_policy not in {"auto", "low"}:
            status = "owner_review"
            recommended_action = (
                "review_optional_providers"
                if optional_disabled_tools and not approval_gated_tools
                else "owner_review"
            )
        else:
            status = "ready"
            recommended_action = "instantiate"
        return {
            "status": status,
            "blockers": blockers,
            "warnings": warnings,
            "recommended_action": recommended_action,
            "llm_provider": llm_readiness,
            "graph_tool_readiness": graph_readiness,
            "requested_tool_readiness": requested_readiness,
            "configuration_required_tools": configuration_required_tools,
            "optional_disabled_tools": optional_disabled_tools,
            "approval_gated_tools": approval_gated_tools,
            "workflow_impact_counts": {
                "configuration_required": len(configuration_required_tools),
                "optional_disabled": len(optional_disabled_tools),
                "approval_gated": len(approval_gated_tools),
            },
            "agent": (
                {
                    "id": agent.id,
                    "role_family": agent.role_family,
                    "role_name": agent.role_name,
                    "status": agent.status,
                }
                if agent
                else None
            ),
        }

    async def _llm_readiness(self) -> dict[str, Any]:
        if not self._llm_gateway:
            return {
                "provider": "unknown",
                "configured": False,
                "mode": "configuration_required",
                "status": "configuration_required",
                "blocking": True,
                "detail": "LLM gateway is not available for agent delegation.",
            }
        validate = getattr(self._llm_gateway, "validate_provider", None)
        if not validate:
            return {
                "provider": "unknown",
                "configured": False,
                "mode": "configuration_required",
                "status": "configuration_required",
                "blocking": True,
                "detail": "LLM gateway does not expose provider validation.",
            }
        return await validate()

    def _tool_readiness(self, tools: list[str]) -> list[dict[str, Any]]:
        rows = []
        for tool_name in self._unique_strings(tools):
            readiness = self._tool_registry.get_tool_readiness(tool_name)
            row = {"tool_name": tool_name, **readiness}
            row["required_for_readiness"] = tool_is_required_for_readiness(row)
            if row.get("state") not in {"live", "advisory"} or not row.get("executable"):
                row["workflow_impact"] = (
                    "configuration_required"
                    if row["required_for_readiness"]
                    else "optional_disabled"
                )
            elif row.get("side_effects"):
                row["workflow_impact"] = "approval_gated"
            else:
                row["workflow_impact"] = "ready"
            rows.append(row)
        return rows

    def _approval_required(self, requested_tools: list[str], role_spec: dict[str, Any]) -> bool:
        policy = str(role_spec.get("approval_policy") or "auto")
        if policy not in {"auto", "low"}:
            return True
        return any(tool in SIDE_EFFECT_APPROVAL_TOOLS for tool in requested_tools)

    def _risk_level(self, requested_tools: list[str], policy_or_severity: Any) -> str:
        if any(tool in SIDE_EFFECT_APPROVAL_TOOLS for tool in requested_tools):
            return "high"
        value = str(policy_or_severity or "").lower()
        if value in {"critical", "high", "medium", "low"}:
            return value
        if value in {"sensitive", "manual", "approval"}:
            return "medium"
        return "low"

    @staticmethod
    def _business_function(role_family: Any, capability: Any) -> str:
        family = str(role_family or "").strip()
        if family:
            return family.replace("_", " ").title()
        capability_text = str(capability or "").strip()
        if capability_text:
            return capability_text.replace("_", " ").title()
        return "Unclassified"

    @staticmethod
    def _family_from_gap(gap: RoleGap, manifest: dict[str, Any] | None = None) -> str:
        context = gap.context or {}
        for candidate in (
            context.get("role_family"),
            context.get("business_function"),
        ):
            if candidate:
                normalized = str(candidate).replace(" ", "_").replace("-", "_").lower()
                if normalized in KNOWN_ROLE_FAMILIES:
                    return normalized
        capability = str(gap.capability or "").replace(" ", "_").replace("-", "_").lower()
        if capability in KNOWN_ROLE_FAMILIES:
            return capability
        if capability in CAPABILITY_FAMILY_MAP:
            return CAPABILITY_FAMILY_MAP[capability]
        text = " ".join(
            str(value or "").lower()
            for value in [gap.title, gap.description, gap.capability]
        )
        if any(hint in text for hint in KNOWLEDGE_ROLE_GAP_HINTS):
            return "knowledge"
        manifest_family = str((manifest or {}).get("family") or "").strip()
        if manifest_family:
            return manifest_family
        return "operations"

    def _requested_tools_for_role_gap(self, gap: RoleGap, role_family: str) -> list[str]:
        requested_tools = self._unique_strings(gap.requested_tools or [])
        if role_family != "knowledge":
            return requested_tools
        return [
            tool_name
            for tool_name in requested_tools
            if tool_name in KNOWLEDGE_SAFE_ROLE_TOOLS
        ]

    @staticmethod
    def _humanize(value: str) -> str:
        return value.replace("_", " ").replace("-", " ").title()

    @staticmethod
    def _dedupe_key(*parts: Any) -> str:
        payload = json.dumps([str(part) for part in parts if part is not None], sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _fingerprint_intent(intent: WorkflowIntent) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "title": intent.title,
                    "description": intent.description,
                    "status": intent.status,
                    "readiness": intent.readiness,
                    "graph_definition": intent.graph_definition,
                    "requested_tools": intent.requested_tools,
                    "required_agents": intent.required_agents,
                },
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _unique_strings(values: list[Any]) -> list[str]:
        seen: set[str] = set()
        result = []
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    @staticmethod
    def _dedupe_proposals(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        result = []
        for proposal in proposals:
            key = proposal["dedupe_key"]
            if key in seen:
                continue
            seen.add(key)
            result.append(proposal)
        return result

    @staticmethod
    def _redact_large(value: Any, *, max_chars: int = 4000) -> Any:
        text = json.dumps(value, sort_keys=True, default=str)
        if len(text) <= max_chars:
            return value
        return {"truncated": True, "preview": text[:max_chars]}

    @staticmethod
    def _can_auto_instantiate(intent: dict[str, Any]) -> bool:
        return (
            intent.get("risk_level") == "low"
            and not intent.get("approval_required")
            and (intent.get("readiness") or {}).get("status") == "ready"
            and not intent.get("workflow_id")
        )

    @staticmethod
    def _groups(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for item in items:
            function = item.get("business_function") or "Unclassified"
            group = groups.setdefault(
                function,
                {
                    "business_function": function,
                    "count": 0,
                    "ready_count": 0,
                    "owner_review_count": 0,
                    "blocked_count": 0,
                    "configuration_required_count": 0,
                    "optional_disabled_count": 0,
                    "approval_gated_tool_count": 0,
                    "configuration_required_tool_count": 0,
                    "categories": [],
                    "items": [],
                },
            )
            group["count"] += 1
            readiness_status = (item.get("readiness") or {}).get("status")
            if readiness_status == "ready":
                group["ready_count"] += 1
            elif readiness_status == "owner_review":
                group["owner_review_count"] += 1
            elif readiness_status == "configuration_required":
                group["configuration_required_count"] += 1
            elif readiness_status == "blocked":
                group["blocked_count"] += 1
            readiness = item.get("readiness") or {}
            if readiness.get("optional_disabled_tools"):
                group["optional_disabled_count"] += 1
            group["approval_gated_tool_count"] += len(readiness.get("approval_gated_tools") or [])
            group["configuration_required_tool_count"] += len(
                readiness.get("configuration_required_tools") or []
            )
            group["categories"] = WorkflowIntentService._unique_strings(
                [*group["categories"], item.get("category")]
            )
            group["items"].append(item["id"])
        return sorted(groups.values(), key=lambda group: group["business_function"].lower())

    @staticmethod
    def _counts(items: list[dict[str, Any]]) -> dict[str, Any]:
        counts: dict[str, Any] = {
            "total": len(items),
            "by_status": {},
            "by_readiness": {},
            "by_category": {},
            "by_recommended_action": {},
            "missing_agent_count": 0,
            "configuration_required_tool_count": 0,
            "optional_disabled_count": 0,
            "optional_disabled_tool_count": 0,
            "approval_gated_tool_count": 0,
        }
        for item in items:
            status = item.get("status") or "unknown"
            readiness_status = (item.get("readiness") or {}).get("status") or "unknown"
            readiness = item.get("readiness") or {}
            category = item.get("category") or "unknown"
            counts["by_status"][status] = counts["by_status"].get(status, 0) + 1
            counts["by_readiness"][readiness_status] = (
                counts["by_readiness"].get(readiness_status, 0) + 1
            )
            counts["by_category"][category] = counts["by_category"].get(category, 0) + 1
            action = readiness.get("recommended_action") or "unknown"
            counts["by_recommended_action"][action] = (
                counts["by_recommended_action"].get(action, 0) + 1
            )
            if any("No active agent" in blocker for blocker in readiness.get("blockers") or []):
                counts["missing_agent_count"] += 1
            configuration_required_tools = readiness.get("configuration_required_tools") or []
            optional_disabled_tools = readiness.get("optional_disabled_tools") or []
            approval_gated_tools = readiness.get("approval_gated_tools") or []
            counts["configuration_required_tool_count"] += len(configuration_required_tools)
            counts["optional_disabled_tool_count"] += len(optional_disabled_tools)
            counts["approval_gated_tool_count"] += len(approval_gated_tools)
            if optional_disabled_tools:
                counts["optional_disabled_count"] += 1
        return counts

    async def _record(
        self,
        event_type: str,
        actor: str,
        outcome: str,
        metadata: dict[str, Any],
    ) -> None:
        if not self._audit:
            return
        await self._audit.record(
            event_type=event_type,
            actor=actor,
            actor_type="system" if actor != "owner" else "user",
            resource_type="workflow_intent",
            action=event_type.split(".")[-1],
            outcome=outcome,
            metadata={
                key: value
                for key, value in metadata.items()
                if key not in {"intents", "instantiated_workflows"}
            },
        )

    @staticmethod
    def _intent_to_dict(intent: WorkflowIntent) -> dict[str, Any]:
        return {
            "id": intent.id,
            "title": intent.title,
            "description": intent.description,
            "status": intent.status,
            "category": intent.category,
            "business_function": intent.business_function,
            "source_type": intent.source_type,
            "source_id": intent.source_id,
            "source_hash": intent.source_hash,
            "company_namespace": intent.company_namespace,
            "role_family": intent.role_family,
            "role_name": intent.role_name,
            "capability": intent.capability,
            "risk_level": intent.risk_level,
            "trigger_type": intent.trigger_type,
            "trigger_config": intent.trigger_config or {},
            "graph_definition": intent.graph_definition or {},
            "requested_tools": intent.requested_tools or [],
            "required_agents": intent.required_agents or [],
            "tool_readiness": intent.tool_readiness or [],
            "readiness": intent.readiness or {},
            "approval_required": intent.approval_required,
            "approval_id": intent.approval_id,
            "workflow_template_id": intent.workflow_template_id,
            "workflow_id": intent.workflow_id,
            "proposed_by": intent.proposed_by,
            "evidence": intent.evidence or {},
            "resolution": intent.resolution or {},
            "dedupe_key": intent.dedupe_key,
            "created_at": intent.created_at.isoformat(),
            "updated_at": intent.updated_at.isoformat(),
            "resolved_at": intent.resolved_at.isoformat() if intent.resolved_at else None,
        }
