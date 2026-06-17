"""Agent Manager — registration, lifecycle, and configuration of agents."""

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import desc, or_, select, update
from sqlalchemy.orm import selectinload

from cyber_team.audit.service import AuditService
from cyber_team.clock import utc_now
from cyber_team.company.operating_model import OperatingModelBuilder
from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import (
    Agent,
    ApprovalRequest,
    AutonomousPlan,
    CompanyContextSnapshot,
    RoleGap,
    RoleManifest,
)
from cyber_team.llm.gateway import LLMGateway
from cyber_team.memory.protocol import AgentMemoryProtocol
from cyber_team.memory.service import MemoryService


class AgentManager:
    ACTIVE_ROLE_GAP_STATUSES = {"open", "proposed"}
    HIGH_RISK_ROLE_TOOLS = {
        "make_call",
        "send_sms",
        "send_email",
        "send_message",
        "erpnext_create_lead",
        "erpnext_invoice_create",
        "procurement_request",
        "payment_charge",
        "payment_refund",
    }
    AUTONOMOUS_GAP_BLOCKERS = (
        "blocked because",
        "blocked by",
        "cannot proceed because",
        "can't proceed because",
        "unable to proceed because",
        "cannot complete because",
        "can't complete because",
        "cannot continue because",
        "need a new role",
        "need another role",
        "need a specialist",
        "need an agent",
        "need a tool",
        "missing role",
        "missing agent",
        "missing specialist",
        "missing tool",
        "missing integration",
        "no available agent",
        "no suitable agent",
        "no configured integration",
        "not configured",
        "not available",
        "requires a specialist",
        "requires an integration",
        "requires a tool",
    )
    AUTONOMOUS_GAP_SUBJECTS = (
        "agent",
        "advisor",
        "capability",
        "client",
        "connector",
        "expert",
        "gateway",
        "integration",
        "manager",
        "provider",
        "role",
        "skill",
        "specialist",
        "tool",
    )
    TOOL_CAPABILITY_HINTS = {
        "call": "outbound_voice",
        "phone": "outbound_voice",
        "sms": "sms_messaging",
        "message": "messaging",
        "email": "email",
        "calendar": "scheduling",
        "schedule": "scheduling",
        "crm": "crm",
        "lead": "crm",
        "invoice": "accounting",
        "accounting": "accounting",
        "payment": "payments",
        "contract": "legal",
        "legal": "legal",
        "compliance": "compliance",
        "analytics": "analytics",
        "report": "analytics",
        "document": "knowledge",
        "knowledge": "knowledge",
        "support": "support",
    }
    TOOL_NAME_HINTS = {
        "call": "make_call",
        "phone": "make_call",
        "sms": "send_sms",
        "message": "send_message",
        "whatsapp": "send_message",
        "slack": "send_message",
        "email": "send_email",
        "calendar": "calendar_event_create",
        "schedule": "calendar_event_create",
        "crm": "erpnext_create_lead",
        "lead": "erpnext_create_lead",
        "invoice": "erpnext_get_invoices",
        "accounting": "erpnext_get_invoices",
        "contract": "contract_draft",
        "policy": "policy_draft",
        "analytics": "analytics_read",
        "report": "analytics_read",
        "document": "document_index",
        "knowledge": "knowledge_query",
        "support": "support_ticket_read",
    }

    TOOL_ALIASES = {
        "call_make": "make_call",
        "email_send": "send_email",
        "message_send": "send_message",
        "memory_write": "memory_remember",
        "sms_send": "send_sms",
        "crm_lead_create": "erpnext_create_lead",
        "erpnext_finance_read": "erpnext_get_invoices",
    }

    def __init__(
        self,
        memory_service: MemoryService | None = None,
        audit_service: AuditService | None = None,
        tool_registry: Any | None = None,
    ):
        self._llm = LLMGateway()
        self._memory = memory_service
        self._audit = audit_service
        self._metrics = getattr(audit_service, "_metrics", None)
        self._tool_registry = tool_registry
        self._agents_cache: dict[str, dict] = {}

    async def list_agents(self) -> list[dict]:
        async with async_session() as session:
            result = await session.execute(
                select(Agent).where(Agent.status != "deleted")
            )
            agents = result.scalars().all()
            return [self._agent_to_dict(a) for a in agents]

    async def get_agent(self, agent_id: str) -> dict | None:
        async with async_session() as session:
            result = await session.execute(
                select(Agent).where(Agent.id == agent_id)
            )
            agent = result.scalar_one_or_none()
            return self._agent_to_dict(agent) if agent else None

    async def create_agent(self, data) -> dict:
        agent_id = slug_id(data.role_name)
        tools, unsupported_tools = self._resolve_tool_names(data.tools)
        config = dict(data.config)
        if unsupported_tools:
            config["requested_tools"] = list(data.tools)
            config["unsupported_tools"] = unsupported_tools
        async with async_session() as session:
            agent = Agent(
                id=agent_id,
                role_family=data.role_family,
                role_name=data.role_name,
                instructions=data.instructions,
                tools=tools,
                memory_namespace=data.memory_namespace or f"{data.role_family}:{agent_id}",
                approval_policy=data.approval_policy,
                config=config,
            )
            session.add(agent)
            await session.commit()
            return self._agent_to_dict(agent)

    async def update_agent(self, agent_id: str, data) -> dict | None:
        async with async_session() as session:
            result = await session.execute(
                select(Agent).where(Agent.id == agent_id)
            )
            agent = result.scalar_one_or_none()
            if not agent:
                return None
            for field, value in data.model_dump(exclude_unset=True).items():
                if field == "tools":
                    tool_input = value or []
                    value, unsupported_tools = self._resolve_tool_names(tool_input)
                    config = dict(agent.config or {})
                    if unsupported_tools:
                        config["requested_tools"] = list(tool_input)
                        config["unsupported_tools"] = unsupported_tools
                    else:
                        config.pop("unsupported_tools", None)
                    agent.config = config
                setattr(agent, field, value)
            agent.updated_at = utc_now()
            await session.commit()
            return self._agent_to_dict(agent)

    async def deactivate_agent(self, agent_id: str):
        async with async_session() as session:
            await session.execute(
                update(Agent).where(Agent.id == agent_id).values(status="inactive")
            )
            await session.commit()

    async def invoke_agent(
        self,
        agent_id: str,
        task: str,
        *,
        conversation_id: str | None = None,
        source_type: str = "agent_invocation",
        trace_metadata: dict[str, Any] | None = None,
        report_role_gap: bool = True,
    ) -> str:
        agent = await self.get_agent(agent_id)
        if not agent:
            await self._report_missing_agent_gap(agent_id, task)
            raise ValueError(f"Agent {agent_id} not found")

        # Check approval policy via OPA
        needs_approval = await self._check_approval_policy(
            agent_id,
            "invoke",
            agent["approval_policy"],
        )
        if needs_approval:
            approval_id = await self._request_approval(agent_id, "invoke", task, {})
            return f"Approval requested: {approval_id}"

        invocation_id = str(uuid.uuid4())
        metadata = dict(trace_metadata or {})
        memory_protocol = AgentMemoryProtocol(self._memory, metrics_service=self._metrics)
        memory_context = await memory_protocol.prepare_invocation(
            agent=agent,
            task=task,
            invocation_id=invocation_id,
            conversation_id=conversation_id,
            source_type=source_type,
        )

        # Build prompt and invoke LLM
        system_prompt = agent["instructions"] + memory_context.prompt_context
        try:
            result = await self._llm.invoke(
                system_prompt=system_prompt,
                user_message=task,
                agent_id=agent_id,
                conversation_id=conversation_id,
            )
            if self._metrics:
                self._metrics.record_llm_invocation(agent_id, "success", source_type)
        except Exception as exc:
            if self._metrics:
                self._metrics.record_llm_invocation(agent_id, "failed", source_type)
            await memory_protocol.record_failure(
                memory_context,
                exc=exc,
                trace_metadata={
                    "role_family": agent["role_family"],
                    "role_name": agent["role_name"],
                    **metadata,
                },
            )
            raise

        if report_role_gap:
            await self._maybe_report_autonomous_role_gap(
                trigger="agent_invocation",
                source_agent_id=agent_id,
                company_namespace=memory_context.company_namespace
                or memory_context.memory_namespace,
                task=task,
                result=result,
                context={
                    "role_family": agent["role_family"],
                    "role_name": agent["role_name"],
                    "invocation_id": invocation_id,
                    **metadata,
                },
            )

        await memory_protocol.complete_invocation(
            memory_context,
            result=result,
            trace_metadata={
                "role_family": agent["role_family"],
                "role_name": agent["role_name"],
                **metadata,
            },
        )

        return result

    async def chat(
        self,
        agent_id: str | None,
        message: str,
        conversation_id: str | None = None,
    ) -> dict:
        if not conversation_id:
            conversation_id = str(uuid.uuid4())

        if agent_id:
            agent = await self.get_agent(agent_id)
            if not agent:
                raise ValueError(f"Agent {agent_id} not found")
            agent_name = agent["role_name"]
            response = await self.invoke_agent(
                agent_id,
                message,
                conversation_id=conversation_id,
                source_type="chat",
                trace_metadata={"conversation_id": conversation_id},
                report_role_gap=False,
            )
        else:
            # Route to supervisor/orchestrator
            agent_id = "supervisor"
            agent_name = "Supervisor"
            agent = {
                "id": agent_id,
                "role_family": "supervisor",
                "role_name": agent_name,
                "instructions": (
                    "You are the Cyber-Team supervisor. Coordinate between "
                    "specialist agents and help the owner."
                ),
                "memory_namespace": "company:default:supervisor",
                "approval_policy": "auto",
                "tools": [],
                "status": "active",
                "config": {},
            }
            response = await self._invoke_ephemeral_agent(
                agent,
                message,
                conversation_id=conversation_id,
                source_type="chat",
                trace_metadata={"conversation_id": conversation_id},
            )
        await self._maybe_report_autonomous_role_gap(
            trigger="chat",
            source_agent_id=agent_id,
            company_namespace=agent.get("memory_namespace"),
            task=message,
            result=response,
            context={"conversation_id": conversation_id},
        )

        return {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "message": response,
            "conversation_id": conversation_id,
        }

    async def _invoke_ephemeral_agent(
        self,
        agent: dict[str, Any],
        task: str,
        *,
        conversation_id: str | None,
        source_type: str,
        trace_metadata: dict[str, Any] | None = None,
    ) -> str:
        invocation_id = str(uuid.uuid4())
        metadata = dict(trace_metadata or {})
        memory_protocol = AgentMemoryProtocol(self._memory, metrics_service=self._metrics)
        memory_context = await memory_protocol.prepare_invocation(
            agent=agent,
            task=task,
            invocation_id=invocation_id,
            conversation_id=conversation_id,
            source_type=source_type,
        )
        try:
            result = await self._llm.invoke(
                system_prompt=agent["instructions"] + memory_context.prompt_context,
                user_message=task,
                agent_id=agent["id"],
                conversation_id=conversation_id,
            )
            if self._metrics:
                self._metrics.record_llm_invocation(agent["id"], "success", source_type)
        except Exception as exc:
            if self._metrics:
                self._metrics.record_llm_invocation(agent["id"], "failed", source_type)
            await memory_protocol.record_failure(
                memory_context,
                exc=exc,
                trace_metadata={
                    "role_family": agent["role_family"],
                    "role_name": agent["role_name"],
                    **metadata,
                },
            )
            raise
        await memory_protocol.complete_invocation(
            memory_context,
            result=result,
            trace_metadata={
                "role_family": agent["role_family"],
                "role_name": agent["role_name"],
                **metadata,
            },
        )
        return result

    # ─── Role Manifests ───────────────────────────────────────────────

    async def list_role_manifests(self) -> list[dict]:
        async with async_session() as session:
            result = await session.execute(select(RoleManifest))
            manifests = result.scalars().all()
            return [self._manifest_to_dict(m) for m in manifests]

    async def get_role_manifest(self, manifest_id: str) -> dict | None:
        async with async_session() as session:
            result = await session.execute(
                select(RoleManifest).where(RoleManifest.id == manifest_id)
            )
            m = result.scalar_one_or_none()
            return self._manifest_to_dict(m) if m else None

    async def create_role_manifest(self, data) -> dict:
        manifest_id = slug_id(data.name)
        async with async_session() as session:
            manifest = RoleManifest(
                id=manifest_id,
                family=data.family,
                name=data.name,
                description=data.description,
                instructions_template=data.instructions_template,
                default_tools=data.default_tools,
                memory_namespace=data.memory_namespace or f"{data.family}:{manifest_id}",
                approval_policy=data.approval_policy,
                success_metrics=data.success_metrics,
                is_core=data.is_core,
                config=data.config,
            )
            session.add(manifest)
            await session.commit()
            return self._manifest_to_dict(manifest)

    async def instantiate_role(self, manifest_id: str, overrides: dict | None = None) -> dict:
        if overrides is None:
            overrides = {}
        manifest = await self.get_role_manifest(manifest_id)
        if not manifest:
            raise ValueError(f"Role manifest {manifest_id} not found")
        existing = await self.get_agent(slug_id(manifest["name"]))
        if existing:
            return await self._sync_manifest_tools(existing, manifest["default_tools"])

        instructions = self._render_template(manifest["instructions_template"], overrides)
        resolved_tools, unsupported_tools = self._resolve_tool_names(manifest["default_tools"])
        tool_readiness = self._tool_readiness_report(resolved_tools)
        config = {**manifest["config"], **overrides}
        if unsupported_tools:
            config["requested_tools"] = list(manifest["default_tools"])
            config["unsupported_tools"] = unsupported_tools
        if tool_readiness:
            config["tool_readiness"] = tool_readiness
            config["unavailable_tools"] = [
                item for item in tool_readiness
                if item["state"] not in {"live", "advisory"}
            ]

        create_data = type("AgentCreate", (), {
            "role_family": manifest["family"],
            "role_name": manifest["name"],
            "instructions": instructions,
            "tools": resolved_tools,
            "memory_namespace": manifest["memory_namespace"],
            "approval_policy": manifest["approval_policy"],
            "config": config,
        })()
        return await self.create_agent(create_data)

    # ─── Company Builder ──────────────────────────────────────────────

    async def run_company_builder(self, company_profile: dict) -> dict:
        company_name = company_profile.get("name") or company_profile.get("company_name")
        company_name = company_name or settings.app_name
        dry_run = bool(company_profile.get("dry_run"))
        manifests = await self.list_role_manifests()
        available_tools = self._available_tool_names()
        operating_model = OperatingModelBuilder().build(
            company_profile,
            existing_manifests=manifests,
            available_tools=available_tools,
        )
        planned_specs = operating_model["planned_role_specs"]
        instantiated = []
        generated_manifests = []
        missing_role_specs = []
        for role_spec in planned_specs:
            manifest = self._find_manifest_for_role_spec(role_spec, manifests)
            if not manifest:
                if dry_run:
                    manifest = role_spec["manifest_payload"]
                else:
                    manifest = await self.create_role_manifest(
                        self._object_from_dict(role_spec["manifest_payload"])
                    )
                    manifests.append(manifest)
                    generated_manifests.append(manifest)
            if not manifest:
                missing_role_specs.append(role_spec)
                continue
            agent_id = slug_id(manifest["name"])
            requested_tools = manifest["default_tools"]
            if dry_run:
                tools, unsupported_tools = self._resolve_tool_names(requested_tools)
                instantiated.append(
                    {
                        "agent_id": agent_id,
                        "role_family": manifest["family"],
                        "role_name": manifest["name"],
                        "status": "planned",
                        "tools": tools,
                        "unsupported_tools": unsupported_tools,
                    }
                )
                continue
            existed = await self.get_agent(agent_id) is not None
            agent = await self.instantiate_role(
                manifest["id"],
                {
                    **company_profile,
                    "company_name": company_name,
                    "company_namespace": operating_model["company_namespace"],
                    "operating_model_version": operating_model["version"],
                    "provisioned_by": "company_builder",
                    "role_rationale": role_spec["rationale"],
                    "activation_triggers": role_spec["activation_triggers"],
                },
            )
            instantiated.append(
                {
                    "agent_id": agent["id"],
                    "role_family": agent["role_family"],
                    "role_name": agent["role_name"],
                    "status": "existing" if existed else "created",
                    "tools": agent["tools"],
                    "unsupported_tools": agent["config"].get("unsupported_tools", []),
                }
            )

        if not dry_run:
            await self._seed_company_memory(
                operating_model=operating_model,
                instantiated_agents=instantiated,
            )

        role_families = self._unique(
            [role_spec["family"] for role_spec in operating_model["planned_role_specs"]]
        )
        blueprint = {
            "recommended_roles": role_families,
            "org_structure": self._org_structure(role_families),
            "approval_policies": {
                role_spec["name"]: role_spec["approval_policy"]
                for role_spec in operating_model["planned_role_specs"]
            },
            "missing_role_families": self._unique(
                role_spec["family"] for role_spec in missing_role_specs
            ),
            "generated_role_manifest_ids": [manifest["id"] for manifest in generated_manifests],
            "capability_gaps": operating_model["capability_gaps"],
            "adaptive_loops": operating_model["adaptive_loops"],
        }
        if self._audit:
            await self._audit.record(
                event_type="company_builder.run",
                actor="owner",
                actor_type="user",
                resource_type="company_builder",
                action="run",
                outcome="success",
                metadata={
                    "company_name": company_name,
                    "recommended_roles": role_families,
                    "agents": instantiated,
                    "missing_role_specs": missing_role_specs,
                    "generated_role_manifest_ids": [
                        manifest["id"] for manifest in generated_manifests
                    ],
                    "operating_model": {
                        "version": operating_model["version"],
                        "summary": operating_model["summary"],
                        "capability_gap_count": len(operating_model["capability_gaps"]),
                    },
                    "dry_run": dry_run,
                },
            )
        return {
            "dry_run": dry_run,
            "blueprint": blueprint,
            "company_profile": company_profile,
            "instantiated_agents": instantiated,
            "operating_model": operating_model,
            "role_specs": operating_model["planned_role_specs"],
            "role_backlog": operating_model["role_backlog"],
            "capability_gaps": operating_model["capability_gaps"],
            "adaptive_loops": operating_model["adaptive_loops"],
            "memory_seed": operating_model["memory_seed"],
        }

    async def propose_new_role(self, gap_description: str) -> dict:
        prompt = f"""A gap has been identified in the current team: {gap_description}

Propose a new role to fill this gap. Return JSON with:
- "family": role family name
- "name": specific role name
- "description": what this role does
- "instructions_template": template for the agent instructions
- "default_tools": list of tool names this role needs
- "approval_policy": "auto", "sensitive", or "always"
- "success_metrics": how to measure this role's effectiveness
"""
        result = await self._llm.invoke(
            system_prompt="You are a role design specialist. Always respond with valid JSON.",
            user_message=prompt,
            agent_id="role_gap_analyzer",
        )
        return {"proposal": result}

    # ─── Role Gap Runtime Loop ───────────────────────────────────────

    async def report_role_gap(self, data, reporter: str = "system") -> dict:
        gap_id = f"gap_{uuid.uuid4().hex[:12]}"
        company_namespace = getattr(data, "company_namespace", None) or "company:default"
        severity = getattr(data, "severity", None) or "medium"
        context = dict(getattr(data, "context", None) or {})
        dedupe_key = context.get("dedupe_key")
        status = "open"
        now = utc_now()
        async with async_session() as session:
            if dedupe_key:
                result = await session.execute(
                    select(RoleGap)
                    .where(
                        RoleGap.status.in_(self.ACTIVE_ROLE_GAP_STATUSES),
                        RoleGap.company_namespace == company_namespace,
                    )
                    .order_by(desc(RoleGap.created_at))
                )
                for existing_gap in result.scalars().all():
                    if (existing_gap.context or {}).get("dedupe_key") == dedupe_key:
                        return self._role_gap_to_dict(existing_gap)
            gap = RoleGap(
                id=gap_id,
                title=getattr(data, "title"),
                description=getattr(data, "description"),
                status=status,
                severity=severity,
                source_agent_id=getattr(data, "source_agent_id", None),
                source_type=getattr(data, "source_type", None) or "system",
                company_namespace=company_namespace,
                capability=getattr(data, "capability", None),
                requested_tools=list(getattr(data, "requested_tools", None) or []),
                context=context,
                proposed_role={},
                resolution={},
                created_at=now,
                updated_at=now,
            )
            session.add(gap)
            await session.commit()
            response = self._role_gap_to_dict(gap)

        if self._audit:
            await self._audit.record(
                event_type="role_gap.reported",
                actor=reporter,
                actor_type=getattr(data, "source_type", None) or "system",
                resource_type="role_gap",
                resource_id=gap_id,
                action="report",
                metadata={
                    "title": response["title"],
                    "severity": response["severity"],
                    "source_agent_id": response["source_agent_id"],
                    "company_namespace": response["company_namespace"],
                    "capability": response["capability"],
                    "requested_tools": response["requested_tools"],
                },
            )
        return response

    async def report_tool_gap(
        self,
        tool_name: str,
        *,
        agent_id: str | None = None,
        actor: str = "system",
        actor_type: str = "system",
        reason: str = "tool_not_found",
        error: str | None = None,
        context: dict | None = None,
    ) -> dict | None:
        """Record a role/tool capability gap caused by tool execution blockage."""
        capability = self._capability_for_text(tool_name)
        severity = "high" if reason == "service_unavailable" else "medium"
        title = (
            f"Unavailable integration for {tool_name}"
            if reason == "service_unavailable"
            else f"Missing tool: {tool_name}"
        )
        description = (
            f"Tool execution was blocked because {tool_name} is unavailable."
            if reason == "service_unavailable"
            else f"Tool execution requested {tool_name}, but that tool is not registered."
        )
        if error:
            description = f"{description} Runtime detail: {error[:500]}"
        return await self._report_autonomous_role_gap(
            title=title,
            description=description,
            severity=severity,
            source_agent_id=agent_id,
            source_type=actor_type,
            capability=capability,
            requested_tools=[tool_name],
            context={
                **(context or {}),
                "trigger": "tool_execution",
                "reason": reason,
                "tool_name": tool_name,
                "error": error,
                "dedupe_key": f"{reason}:{tool_name}",
            },
            reporter=agent_id or actor,
        )

    async def list_role_gaps(self, status: str | None = None) -> list[dict]:
        async with async_session() as session:
            query = select(RoleGap)
            if status:
                query = query.where(RoleGap.status == status)
            result = await session.execute(query.order_by(desc(RoleGap.created_at)))
            return [self._role_gap_to_dict(gap) for gap in result.scalars().all()]

    async def get_role_gap(self, gap_id: str) -> dict | None:
        async with async_session() as session:
            result = await session.execute(select(RoleGap).where(RoleGap.id == gap_id))
            gap = result.scalar_one_or_none()
            return self._role_gap_to_dict(gap) if gap else None

    async def summarize_role_backlog(
        self,
        statuses: list[str] | None = None,
        source_type: str | None = None,
        limit: int = 200,
    ) -> dict:
        safe_limit = max(1, min(limit, 500))
        async with async_session() as session:
            query = select(RoleGap)
            if statuses:
                query = query.where(RoleGap.status.in_(statuses))
            if source_type:
                query = query.where(RoleGap.source_type == source_type)
            result = await session.execute(
                query.order_by(desc(RoleGap.updated_at)).limit(safe_limit)
            )
            gaps = list(result.scalars().all())
            gap_ids = [gap.id for gap in gaps]

            approvals_by_gap: dict[str, list[ApprovalRequest]] = {gap_id: [] for gap_id in gap_ids}
            if gap_ids:
                approval_result = await session.execute(
                    select(ApprovalRequest)
                    .where(
                        ApprovalRequest.action_type == "role_gap.tool_grant",
                        ApprovalRequest.target_type == "role_gap",
                        ApprovalRequest.target_id.in_(gap_ids),
                    )
                    .order_by(desc(ApprovalRequest.created_at))
                )
                approvals = list(approval_result.scalars().all())
                now = utc_now()
                expired = []
                for approval in approvals:
                    if (
                        approval.status == "pending"
                        and approval.expires_at is not None
                        and approval.expires_at < now
                    ):
                        approval.status = "expired"
                        approval.resolved_at = now
                        expired.append(approval)
                    approvals_by_gap.setdefault(str(approval.target_id), []).append(approval)
                if expired:
                    await session.commit()

            snapshot_ids = {
                str((gap.context or {}).get("snapshot_id"))
                for gap in gaps
                if (gap.context or {}).get("snapshot_id")
            }
            source_hashes = {
                str((gap.context or {}).get("source_hash"))
                for gap in gaps
                if (gap.context or {}).get("source_hash")
            }
            snapshots: dict[str, CompanyContextSnapshot] = {}
            if snapshot_ids or source_hashes:
                snapshot_result = await session.execute(
                    select(CompanyContextSnapshot).where(
                        or_(
                            CompanyContextSnapshot.id.in_(snapshot_ids or {"__none__"}),
                            CompanyContextSnapshot.source_hash.in_(source_hashes or {"__none__"}),
                        )
                    )
                )
                for snapshot in snapshot_result.scalars().all():
                    snapshots[snapshot.id] = snapshot
                    snapshots[snapshot.source_hash] = snapshot

            plan_by_snapshot: dict[str, AutonomousPlan] = {}
            if snapshot_ids:
                plan_result = await session.execute(
                    select(AutonomousPlan)
                    .options(selectinload(AutonomousPlan.tasks))
                    .where(
                        AutonomousPlan.source_type == "company_context_snapshot",
                        AutonomousPlan.source_id.in_(snapshot_ids),
                    )
                    .order_by(desc(AutonomousPlan.created_at))
                )
                for plan in plan_result.scalars().all():
                    plan_by_snapshot.setdefault(plan.source_id, plan)

            items = [
                self._role_gap_summary_item(
                    self._role_gap_to_dict(gap),
                    approvals_by_gap.get(gap.id, []),
                    snapshots,
                    plan_by_snapshot,
                )
                for gap in gaps
            ]

        groups_by_function: dict[str, dict] = {}
        counts = {
            "total": len(items),
            "open": 0,
            "proposed": 0,
            "resolved": 0,
            "dismissed": 0,
            "deferred": 0,
            "stale": 0,
            "by_status": {},
            "by_function": {},
            "by_risk": {},
        }
        for item in items:
            status = item["status"]
            function = item["business_function"]
            risk = item["risk_level"]
            counts["by_status"][status] = counts["by_status"].get(status, 0) + 1
            counts["by_function"][function] = counts["by_function"].get(function, 0) + 1
            counts["by_risk"][risk] = counts["by_risk"].get(risk, 0) + 1
            if status in counts:
                counts[status] += 1
            group = groups_by_function.setdefault(
                function,
                {
                    "business_function": function,
                    "count": 0,
                    "open_count": 0,
                    "proposed_count": 0,
                    "blocked_count": 0,
                    "approval_count": 0,
                    "expired_approval_count": 0,
                    "risk_levels": [],
                    "requested_tools": [],
                    "items": [],
                },
            )
            group["count"] += 1
            if status == "open":
                group["open_count"] += 1
            if status == "proposed":
                group["proposed_count"] += 1
            if item["recommended_action"] == "configure_tools":
                group["blocked_count"] += 1
            if item["approval"]["state"] in {"pending", "approved"}:
                group["approval_count"] += 1
            if item["approval"]["state"] == "expired":
                group["expired_approval_count"] += 1
            group["risk_levels"] = self._unique([*group["risk_levels"], risk])
            group["requested_tools"] = self._unique(
                [*group["requested_tools"], *item["requested_tools"]]
            )
            group["items"].append(item["gap_id"])

        groups = sorted(
            groups_by_function.values(),
            key=lambda group: (group["business_function"].lower(), group["count"]),
        )
        return {
            "items": items,
            "groups": groups,
            "counts": counts,
            "blocking_count": sum(
                1 for item in items if item["recommended_action"] == "configure_tools"
            ),
            "approval_count": sum(
                1 for item in items if item["approval"]["state"] in {"pending", "approved"}
            ),
            "expired_approval_count": sum(
                1 for item in items if item["approval"]["state"] == "expired"
            ),
        }

    async def batch_role_gap_action(
        self,
        gap_ids: list[str],
        *,
        action: str,
        company_profile: dict | None = None,
        approval_ids: dict[str, str] | None = None,
        note: str = "",
        requested_by: str = "owner",
    ) -> dict:
        allowed_actions = {
            "propose",
            "apply",
            "regenerate_approval",
            "defer",
            "dismiss",
        }
        if action not in allowed_actions:
            raise ValueError(
                "Action must be one of: " + ", ".join(sorted(allowed_actions))
            )
        unique_gap_ids = self._unique(gap_ids)
        results = []
        errors = []
        profile = company_profile or {}
        approval_map = approval_ids or {}

        for gap_id in unique_gap_ids:
            try:
                if action == "propose":
                    result = await self.propose_role_for_gap(gap_id, profile)
                elif action == "apply":
                    result = await self.apply_role_gap_proposal(
                        gap_id,
                        profile,
                        approval_id=approval_map.get(gap_id),
                        requested_by=requested_by,
                    )
                elif action == "regenerate_approval":
                    result = await self.regenerate_role_gap_approval(
                        gap_id,
                        profile,
                        requested_by=requested_by,
                    )
                else:
                    result = await self.resolve_role_gap(
                        gap_id,
                        status="deferred" if action == "defer" else "dismissed",
                        note=note or f"Batch {action} from owner console",
                        resolver=requested_by,
                    )
                results.append(
                    {
                        "gap_id": gap_id,
                        "status": "success",
                        "result": result,
                    }
                )
            except Exception as exc:
                errors.append(
                    {
                        "gap_id": gap_id,
                        "status": "failed",
                        "error": str(exc),
                    }
                )

        summary = await self.summarize_role_backlog(
            statuses=["open", "proposed"],
            source_type="company_context_snapshot",
            limit=500,
        )
        response = {
            "action": action,
            "requested_count": len(unique_gap_ids),
            "succeeded_count": len(results),
            "failed_count": len(errors),
            "results": results,
            "errors": errors,
            "summary": summary,
        }
        if self._audit:
            await self._audit.record(
                event_type="role_gap.batch_action",
                actor=requested_by,
                actor_type="user",
                resource_type="role_gap_batch",
                resource_id=None,
                action=action,
                outcome="degraded" if errors else "success",
                metadata={
                    "gap_ids": unique_gap_ids,
                    "succeeded_count": len(results),
                    "failed_count": len(errors),
                    "errors": errors,
                },
            )
        return response

    async def role_operating_cadence(
        self,
        *,
        company_namespace: str | None = None,
    ) -> dict:
        async with async_session() as session:
            agent_result = await session.execute(
                select(Agent)
                .where(Agent.status != "deleted")
                .order_by(Agent.role_family.asc(), Agent.role_name.asc())
            )
            agents = list(agent_result.scalars().all())

        cadences = []
        for agent in agents:
            agent_dict = self._agent_to_dict(agent)
            config = agent_dict.get("config") or {}
            agent_namespace = (
                config.get("company_namespace")
                or self._company_namespace_from_memory_namespace(
                    agent_dict.get("memory_namespace")
                )
            )
            if company_namespace and agent_namespace != company_namespace:
                continue
            cadence = config.get("activation_cadence") or self._default_operating_cadence(
                agent_dict,
                company_namespace=agent_namespace,
            )
            cadences.append(
                {
                    "agent_id": agent_dict["id"],
                    "role_name": agent_dict["role_name"],
                    "role_family": agent_dict["role_family"],
                    "status": agent_dict["status"],
                    "memory_namespace": agent_dict["memory_namespace"],
                    "company_namespace": agent_namespace,
                    "cadence": cadence,
                    "source_role_gap_id": config.get("role_gap_id"),
                    "source_snapshot_id": config.get("source_snapshot_id"),
                }
            )

        active_backlog = await self.summarize_role_backlog(
            statuses=["open", "proposed"],
            source_type="company_context_snapshot",
            limit=500,
        )
        stale_backlog = await self.summarize_role_backlog(
            statuses=["stale"],
            source_type="company_context_snapshot",
            limit=500,
        )
        owner_actions = self._operating_cadence_owner_actions(
            active_backlog=active_backlog,
            stale_backlog=stale_backlog,
            cadence_count=len(cadences),
        )
        return {
            "generated_at": utc_now().isoformat(),
            "company_namespace": company_namespace,
            "cadences": cadences,
            "counts": {
                "active_agents": len([item for item in cadences if item["status"] == "active"]),
                "cadences": len(cadences),
                "active_role_gaps": active_backlog["counts"]["total"],
                "stale_role_gaps": stale_backlog["counts"]["total"],
            },
            "backlog": {
                "active": {
                    "counts": active_backlog["counts"],
                    "groups": active_backlog["groups"],
                },
                "stale": {
                    "counts": stale_backlog["counts"],
                    "groups": stale_backlog["groups"],
                },
            },
            "recommended_owner_actions": owner_actions,
        }

    async def regenerate_role_gap_approval(
        self,
        gap_id: str,
        company_profile: dict | None = None,
        requested_by: str = "owner",
    ) -> dict:
        gap = await self.get_role_gap(gap_id)
        if not gap:
            raise ValueError(f"Role gap {gap_id} not found")
        if gap["status"] not in self.ACTIVE_ROLE_GAP_STATUSES:
            raise ValueError(f"Role gap {gap_id} is {gap['status']}")
        if not gap["proposed_role"]:
            gap = await self.propose_role_for_gap(gap_id, company_profile)
        manifest_payload = gap["proposed_role"]["manifest_payload"]
        high_risk_tools = self._role_gap_high_risk_tools(
            manifest_payload.get("default_tools", [])
        )
        if not high_risk_tools:
            raise ValueError(f"Role gap {gap_id} does not require approval")

        existing_approval = await self._latest_role_gap_tool_grant_approval(gap_id)
        if existing_approval and existing_approval["state"] == "approved":
            raise ValueError(
                f"Role gap {gap_id} already has executable approval "
                f"{existing_approval['approval_id']}"
            )
        if existing_approval and existing_approval["state"] == "pending":
            approval_id = existing_approval["approval_id"]
        else:
            approval_id = await self._request_role_gap_tool_grant_approval(
                gap,
                manifest_payload,
                high_risk_tools,
                requested_by=requested_by,
            )
        await self._mark_role_gap_approval_required(gap_id, approval_id, high_risk_tools)
        summary = await self.summarize_role_backlog(statuses=None, limit=500)
        item = next(
            (candidate for candidate in summary["items"] if candidate["gap_id"] == gap_id),
            None,
        )
        return {"approval_id": approval_id, "item": item}

    async def propose_role_for_gap(
        self,
        gap_id: str,
        company_profile: dict | None = None,
    ) -> dict:
        async with async_session() as session:
            result = await session.execute(select(RoleGap).where(RoleGap.id == gap_id))
            gap = result.scalar_one_or_none()
            if not gap:
                raise ValueError(f"Role gap {gap_id} not found")
            proposal = self._role_gap_proposal(
                self._role_gap_to_dict(gap),
                company_profile or {},
            )
            gap.proposed_role = proposal
            gap.status = "proposed"
            gap.updated_at = utc_now()
            await session.commit()
            response = self._role_gap_to_dict(gap)

        if self._audit:
            await self._audit.record(
                event_type="role_gap.proposed",
                actor="company_builder",
                actor_type="agent",
                resource_type="role_gap",
                resource_id=gap_id,
                action="propose",
                metadata={
                    "role_name": proposal["manifest_payload"]["name"],
                    "family": proposal["manifest_payload"]["family"],
                    "requested_tools": proposal["manifest_payload"]["default_tools"],
                },
            )
        return response

    async def apply_role_gap_proposal(
        self,
        gap_id: str,
        company_profile: dict | None = None,
        approval_id: str | None = None,
        requested_by: str = "owner",
    ) -> dict:
        gap = await self.get_role_gap(gap_id)
        if not gap:
            raise ValueError(f"Role gap {gap_id} not found")
        if gap["status"] not in self.ACTIVE_ROLE_GAP_STATUSES:
            raise ValueError(f"Role gap {gap_id} is {gap['status']}")
        if not gap["proposed_role"]:
            gap = await self.propose_role_for_gap(gap_id, company_profile)
        proposal = gap["proposed_role"]
        manifest_payload = proposal["manifest_payload"]
        readiness = self._role_gap_tool_readiness(
            manifest_payload.get("default_tools", [])
        )
        if not readiness["all_ready"]:
            blocking = ", ".join(
                item["tool_name"] for item in readiness["blocking_tools"]
            )
            raise ValueError(
                "Requested tools are not ready for role creation"
                + (f": {blocking}" if blocking else "")
            )
        high_risk_tools = self._role_gap_high_risk_tools(
            manifest_payload.get("default_tools", [])
        )
        approval_to_consume = None
        if high_risk_tools:
            if approval_id:
                approval_to_consume = approval_id
            else:
                existing_approval = await self._latest_role_gap_tool_grant_approval(gap_id)
                if existing_approval and existing_approval["state"] == "approved":
                    approval_to_consume = existing_approval["approval_id"]
                elif existing_approval and existing_approval["state"] == "pending":
                    return await self._role_gap_approval_required_response(
                        gap,
                        existing_approval["approval_id"],
                        high_risk_tools,
                    )
                else:
                    approval_to_consume = await self._request_role_gap_tool_grant_approval(
                        gap,
                        manifest_payload,
                        high_risk_tools,
                        requested_by=requested_by,
                    )
                    return await self._role_gap_approval_required_response(
                        gap,
                        approval_to_consume,
                        high_risk_tools,
                    )

            await self._validate_role_gap_tool_grant_approval(
                approval_to_consume,
                gap,
                manifest_payload,
                high_risk_tools,
            )
            await self.consume_approval(
                approval_to_consume,
                consumer="role_gap.apply",
                target_type="role_gap",
                target_id=gap_id,
            )

        manifest_id = slug_id(manifest_payload["name"])
        existing_manifest = await self.get_role_manifest(manifest_id)
        if existing_manifest:
            manifest = existing_manifest
        else:
            manifest = await self.create_role_manifest(
                self._object_from_dict(manifest_payload)
            )

        agent = await self.instantiate_role(
            manifest["id"],
            {
                **(company_profile or {}),
                "provisioned_by": "role_gap_loop",
                "role_gap_id": gap_id,
                "role_gap_title": gap["title"],
                "company_namespace": gap["company_namespace"],
                "source_snapshot_id": (gap.get("context") or {}).get("snapshot_id"),
                "source_hash": (gap.get("context") or {}).get("source_hash"),
                "activation_cadence": self._activation_cadence_for_role_gap(
                    gap,
                    manifest_payload,
                ),
            },
        )
        resolution = {
            "manifest_id": manifest["id"],
            "agent_id": agent["id"],
            "role_name": agent["role_name"],
            "applied_at": utc_now().isoformat(),
            "activation_cadence": self._activation_cadence_for_role_gap(
                gap,
                manifest_payload,
            ),
        }
        if approval_to_consume:
            resolution["approval_id"] = approval_to_consume
            resolution["approved_high_risk_tools"] = high_risk_tools
        response = await self._mark_role_gap_resolved(gap_id, resolution)

        if self._audit:
            await self._audit.record(
                event_type="role_gap.applied",
                actor="company_builder",
                actor_type="agent",
                resource_type="role_gap",
                resource_id=gap_id,
                action="apply",
                metadata=resolution,
            )
        return response

    async def resolve_role_gap(
        self,
        gap_id: str,
        status: str = "dismissed",
        note: str = "",
        resolver: str = "owner",
    ) -> dict:
        allowed_statuses = {"deferred", "dismissed", "resolved"}
        if status not in allowed_statuses:
            raise ValueError(f"Status must be one of: {', '.join(sorted(allowed_statuses))}")
        async with async_session() as session:
            result = await session.execute(select(RoleGap).where(RoleGap.id == gap_id))
            gap = result.scalar_one_or_none()
            if not gap:
                raise ValueError(f"Role gap {gap_id} not found")
            gap.status = status
            gap.resolution = {
                **(gap.resolution or {}),
                "note": note,
                "resolver": resolver,
                "resolved_at": utc_now().isoformat(),
            }
            gap.resolved_at = utc_now()
            gap.updated_at = utc_now()
            await session.commit()
            response = self._role_gap_to_dict(gap)

        if self._audit:
            await self._audit.record(
                event_type=f"role_gap.{status}",
                actor=resolver,
                actor_type="user",
                resource_type="role_gap",
                resource_id=gap_id,
                action=status,
                metadata={"note": note},
            )
        return response

    # ─── Approval Queue ───────────────────────────────────────────────

    async def _check_approval_policy(self, agent_id: str, action_type: str, policy: str) -> bool:
        """Check if an action requires approval via OPA policy."""
        if policy == "always":
            return True
        if policy == "auto":
            return False
        # policy == "sensitive": check OPA
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{settings.opa_api_url}/v1/data/cyberteam/approval/needs_approval",
                    json={
                        "input": {
                            "agent_id": agent_id,
                            "action_type": action_type,
                            "policy": policy,
                        }
                    },
                    timeout=5.0,
                )
                result = resp.json()
                return result.get("result", policy == "always")
        except Exception:
            # If OPA is unavailable, fall back to policy string
            return policy == "always"

    async def _request_approval(
        self,
        agent_id: str | None,
        action_type: str,
        description: str,
        payload: dict,
        requester: str = "system",
        requester_type: str = "system",
        risk_level: str = "medium",
        target_type: str | None = None,
        target_id: str | None = None,
        expires_in_minutes: int = 1440,
    ) -> str:
        approval_id = str(uuid.uuid4())
        expires_at = utc_now() + timedelta(minutes=expires_in_minutes)
        async with async_session() as session:
            req = ApprovalRequest(
                id=approval_id,
                agent_id=agent_id,
                action_type=action_type,
                action_description=description,
                action_payload=payload,
                requester=requester,
                requester_type=requester_type,
                risk_level=risk_level,
                target_type=target_type,
                target_id=target_id,
                expires_at=expires_at,
            )
            session.add(req)
            await session.commit()
        if self._audit:
            await self._audit.record(
                event_type="approval.requested",
                actor=requester,
                actor_type=requester_type,
                resource_type="approval",
                resource_id=approval_id,
                action=action_type,
                metadata={
                    "agent_id": agent_id,
                    "risk_level": risk_level,
                    "target_type": target_type,
                    "target_id": target_id,
                    "expires_at": expires_at.isoformat(),
                },
            )
        if self._metrics:
            self._metrics.record_approval_event("requested", "pending", risk_level)
        return approval_id

    async def get_approval_queue(self, status: str | None = None) -> list[dict]:
        async with async_session() as session:
            now = utc_now()
            if status in {None, "pending"}:
                expired_result = await session.execute(
                    select(ApprovalRequest).where(
                        ApprovalRequest.status == "pending",
                        ApprovalRequest.expires_at.is_not(None),
                        ApprovalRequest.expires_at < now,
                    )
                )
                expired_requests = expired_result.scalars().all()
                for req in expired_requests:
                    req.status = "expired"
                    req.resolved_at = now
                if expired_requests:
                    await session.commit()
                    if self._metrics:
                        for req in expired_requests:
                            self._metrics.record_approval_event(
                                "expired",
                                "expired",
                                req.risk_level,
                            )
            query = select(ApprovalRequest)
            if status:
                query = query.where(ApprovalRequest.status == status)
            else:
                query = query.where(ApprovalRequest.status == "pending")
            result = await session.execute(query.order_by(ApprovalRequest.created_at.desc()))
            requests = result.scalars().all()
            return [
                {
                    "id": r.id,
                    "agent_id": r.agent_id,
                    "action_type": r.action_type,
                    "action_description": r.action_description,
                    "action_payload": r.action_payload,
                    "requester": r.requester,
                    "requester_type": r.requester_type,
                    "risk_level": r.risk_level,
                    "target_type": r.target_type,
                    "target_id": r.target_id,
                    "status": r.status,
                    "reviewer": r.reviewer,
                    "review_note": r.review_note,
                    "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
                    "consumed_at": r.consumed_at.isoformat() if r.consumed_at else None,
                    "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                    "created_at": r.created_at.isoformat(),
                }
                for r in requests
            ]

    async def resolve_approval(
        self,
        approval_id: str,
        decision: str,
        note: str = "",
        reviewer: str = "owner",
    ) -> dict:
        if decision not in {"approved", "rejected"}:
            raise ValueError("Decision must be 'approved' or 'rejected'")
        async with async_session() as session:
            result = await session.execute(
                select(ApprovalRequest)
                .where(ApprovalRequest.id == approval_id)
                .with_for_update()
            )
            req = result.scalar_one_or_none()
            if not req:
                raise ValueError(f"Approval request {approval_id} not found")
            if req.status != "pending":
                raise ValueError(f"Approval request {approval_id} is already {req.status}")
            if req.expires_at and req.expires_at < utc_now():
                req.status = "expired"
                req.resolved_at = utc_now()
                await session.commit()
                if self._audit:
                    await self._audit.record(
                        event_type="approval.expired",
                        actor=reviewer,
                        actor_type="user",
                        resource_type="approval",
                        resource_id=req.id,
                        action=req.action_type,
                        outcome="blocked",
                        metadata={
                            "target_type": req.target_type,
                            "target_id": req.target_id,
                        },
                    )
                raise ValueError(f"Approval request {approval_id} has expired")
            req.status = decision
            req.reviewer = reviewer
            req.review_note = note
            req.resolved_at = utc_now()
            await session.commit()
            response = {"id": req.id, "status": decision}
        if self._audit:
            await self._audit.record(
                event_type=f"approval.{decision}",
                actor=reviewer,
                actor_type="user",
                resource_type="approval",
                resource_id=approval_id,
                action=decision,
                metadata={
                    "note": note,
                    "target_type": req.target_type,
                    "target_id": req.target_id,
                    "risk_level": req.risk_level,
                },
            )
        if self._metrics:
            self._metrics.record_approval_event(decision, "success", req.risk_level)
        return response

    async def approval_is_executable(
        self,
        approval_id: str | None,
        target_type: str | None = None,
        target_id: str | None = None,
    ) -> bool:
        if not approval_id:
            return False
        async with async_session() as session:
            req = (
                await session.execute(
                    select(ApprovalRequest)
                    .where(ApprovalRequest.id == approval_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if not req or req.status != "approved" or req.consumed_at is not None:
                return False
            if req.expires_at and req.expires_at < utc_now():
                req.status = "expired"
                req.resolved_at = utc_now()
                await session.commit()
                return False
            if target_type and req.target_type and req.target_type != target_type:
                return False
            if target_id and req.target_id and req.target_id != target_id:
                return False
            return True

    async def consume_approval(
        self,
        approval_id: str,
        consumer: str = "system",
        target_type: str | None = None,
        target_id: str | None = None,
    ) -> None:
        async with async_session() as session:
            req = (
                await session.execute(
                    select(ApprovalRequest)
                    .where(ApprovalRequest.id == approval_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if not req:
                raise ValueError(f"Approval request {approval_id} not found")
            if req.status != "approved":
                raise ValueError(f"Approval request {approval_id} is not approved")
            if req.consumed_at is not None:
                raise ValueError(f"Approval request {approval_id} was already consumed")
            if req.expires_at and req.expires_at < utc_now():
                req.status = "expired"
                req.resolved_at = utc_now()
                await session.commit()
                raise ValueError(f"Approval request {approval_id} has expired")
            if target_type and req.target_type and req.target_type != target_type:
                raise ValueError(f"Approval request {approval_id} is not valid for {target_type}")
            if target_id and req.target_id and req.target_id != target_id:
                raise ValueError(f"Approval request {approval_id} is not valid for {target_id}")
            req.consumed_at = utc_now()
            await session.commit()
        if self._audit:
            await self._audit.record(
                event_type="approval.consumed",
                actor=consumer,
                actor_type="system",
                resource_type="approval",
                resource_id=approval_id,
                action="consume",
                metadata={"target_type": target_type, "target_id": target_id},
            )
        if self._metrics:
            self._metrics.record_approval_event("consumed", "success", req.risk_level)

    # ─── Agent Status ─────────────────────────────────────────────────

    async def get_all_agent_status(self) -> list[dict]:
        agents = await self.list_agents()
        return [
            {
                "id": a["id"],
                "role_name": a["role_name"],
                "role_family": a["role_family"],
                "status": a["status"],
                "approval_policy": a["approval_policy"],
            }
            for a in agents
        ]

    # ─── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _agent_to_dict(agent: Agent) -> dict:
        return {
            "id": agent.id,
            "role_family": agent.role_family,
            "role_name": agent.role_name,
            "instructions": agent.instructions,
            "tools": agent.tools,
            "memory_namespace": agent.memory_namespace,
            "approval_policy": agent.approval_policy,
            "status": agent.status,
            "config": agent.config,
        }

    @staticmethod
    def _manifest_to_dict(m: RoleManifest) -> dict:
        return {
            "id": m.id,
            "family": m.family,
            "name": m.name,
            "description": m.description,
            "instructions_template": m.instructions_template,
            "default_tools": m.default_tools,
            "memory_namespace": m.memory_namespace,
            "approval_policy": m.approval_policy,
            "success_metrics": m.success_metrics,
            "is_core": m.is_core,
            "config": m.config,
        }

    def _resolve_tool_names(self, tool_names: list[str]) -> tuple[list[str], list[str]]:
        if not self._tool_registry:
            return list(tool_names), []
        resolved = []
        unsupported = []
        for tool_name in tool_names:
            resolved_name = self.TOOL_ALIASES.get(tool_name, tool_name)
            if self._tool_registry.get_tool(resolved_name):
                if resolved_name not in resolved:
                    resolved.append(resolved_name)
            elif tool_name not in unsupported:
                unsupported.append(tool_name)
        return resolved, unsupported

    async def _sync_manifest_tools(self, agent: dict, requested_tools: list[str]) -> dict:
        resolved_tools, unsupported_tools = self._resolve_tool_names(requested_tools)
        current_tools = list(agent.get("tools") or [])
        next_tools = list(current_tools)
        for tool_name in resolved_tools:
            if tool_name not in next_tools:
                next_tools.append(tool_name)

        config = dict(agent.get("config") or {})
        tool_readiness = self._tool_readiness_report(next_tools)
        if unsupported_tools:
            config["requested_tools"] = list(requested_tools)
            config["unsupported_tools"] = unsupported_tools
        else:
            config.pop("requested_tools", None)
            config.pop("unsupported_tools", None)
        if tool_readiness:
            config["tool_readiness"] = tool_readiness
            config["unavailable_tools"] = [
                item for item in tool_readiness
                if item["state"] not in {"live", "advisory"}
            ]
        else:
            config.pop("tool_readiness", None)
            config.pop("unavailable_tools", None)

        if next_tools == current_tools and config == (agent.get("config") or {}):
            return agent

        async with async_session() as session:
            result = await session.execute(
                select(Agent).where(Agent.id == agent["id"])
            )
            db_agent = result.scalar_one_or_none()
            if not db_agent:
                return agent
            db_agent.tools = next_tools
            db_agent.config = config
            db_agent.updated_at = utc_now()
            await session.commit()
            return self._agent_to_dict(db_agent)

    def _available_tool_names(self) -> set[str]:
        if not self._tool_registry:
            return set()
        try:
            return {tool.name for tool in self._tool_registry.list_tools()}
        except Exception:
            return set()

    def _tool_readiness_report(self, tool_names: list[str]) -> list[dict]:
        if not self._tool_registry:
            return []
        report = []
        for tool_name in tool_names:
            get_readiness = getattr(self._tool_registry, "get_tool_readiness", None)
            if not get_readiness:
                continue
            readiness = get_readiness(tool_name)
            report.append({
                "tool_name": tool_name,
                "state": readiness["state"],
                "reason": readiness["readiness_reason"],
                "side_effects": readiness["side_effects"],
                "requires_configuration": readiness["requires_configuration"],
            })
        return report

    def _role_gap_tool_readiness(self, tool_names: list[str]) -> dict:
        resolved_tools, unsupported_tools = self._resolve_tool_names(tool_names)
        items = []
        if self._tool_registry:
            get_readiness = getattr(self._tool_registry, "get_tool_readiness", None)
            for tool_name in resolved_tools:
                if get_readiness:
                    readiness = get_readiness(tool_name)
                    items.append(
                        {
                            "tool_name": tool_name,
                            "state": readiness["state"],
                            "reason": readiness["readiness_reason"],
                            "side_effects": readiness["side_effects"],
                            "requires_configuration": readiness["requires_configuration"],
                            "executable": readiness["executable"],
                        }
                    )
                else:
                    items.append(
                        {
                            "tool_name": tool_name,
                            "state": "unknown",
                            "reason": "Tool registry does not expose readiness checks.",
                            "side_effects": False,
                            "requires_configuration": False,
                            "executable": False,
                        }
                    )
        else:
            for tool_name in resolved_tools:
                items.append(
                    {
                        "tool_name": tool_name,
                        "state": "unknown",
                        "reason": "Tool registry is unavailable.",
                        "side_effects": False,
                        "requires_configuration": False,
                        "executable": False,
                    }
                )
        for tool_name in unsupported_tools:
            items.append(
                {
                    "tool_name": tool_name,
                    "state": "unavailable",
                    "reason": f"Tool not found: {tool_name}",
                    "side_effects": False,
                    "requires_configuration": False,
                    "executable": False,
                }
            )
        blocking_tools = [item for item in items if not item["executable"]]
        return {
            "items": items,
            "all_ready": not blocking_tools,
            "blocking_tools": blocking_tools,
            "requested_count": len(tool_names),
            "ready_count": len(items) - len(blocking_tools),
        }

    def _role_gap_summary_item(
        self,
        gap: dict,
        approvals: list[ApprovalRequest],
        snapshots: dict[str, CompanyContextSnapshot],
        plan_by_snapshot: dict[str, AutonomousPlan],
    ) -> dict:
        proposed_manifest = (gap.get("proposed_role") or {}).get("manifest_payload") or {}
        requested_tools = self._unique(
            [
                *list(gap.get("requested_tools") or []),
                *list(proposed_manifest.get("default_tools") or []),
            ]
        )
        high_risk_tools = self._role_gap_high_risk_tools(requested_tools)
        tool_readiness = self._role_gap_tool_readiness(requested_tools)
        approval = self._role_gap_approval_summary(
            gap["id"],
            approvals,
            high_risk_tools,
        )
        snapshot = self._role_gap_snapshot(gap, snapshots)
        plan = plan_by_snapshot.get(snapshot.id) if snapshot else None
        source_task = self._role_gap_source_task(plan)
        business_function = self._role_gap_business_function(gap, proposed_manifest)
        risk_level = self._role_gap_summary_risk(gap, high_risk_tools)
        recommended_action = self._role_gap_recommended_action(
            gap,
            high_risk_tools,
            tool_readiness,
            approval,
        )
        return {
            "gap_id": gap["id"],
            "title": gap["title"],
            "description": gap["description"],
            "business_function": business_function,
            "status": gap["status"],
            "severity": gap["severity"],
            "risk_level": risk_level,
            "requested_tools": requested_tools,
            "tool_readiness": tool_readiness,
            "source_type": gap.get("source_type"),
            "source_snapshot_id": (
                snapshot.id if snapshot else gap.get("context", {}).get("snapshot_id")
            ),
            "source_plan_id": plan.id if plan else None,
            "source_task_id": source_task.id if source_task else None,
            "source_hash": (
                snapshot.source_hash if snapshot else gap.get("context", {}).get("source_hash")
            ),
            "dedupe_key": (gap.get("context") or {}).get("dedupe_key"),
            "proposed_role": gap.get("proposed_role") or {},
            "approval": approval,
            "recommended_action": recommended_action,
            "resolution": gap.get("resolution") or {},
            "created_at": gap.get("created_at"),
            "updated_at": gap.get("updated_at"),
        }

    @staticmethod
    def _role_gap_snapshot(
        gap: dict,
        snapshots: dict[str, CompanyContextSnapshot],
    ) -> CompanyContextSnapshot | None:
        context = gap.get("context") or {}
        return snapshots.get(str(context.get("snapshot_id"))) or snapshots.get(
            str(context.get("source_hash"))
        )

    @staticmethod
    def _role_gap_source_task(plan: AutonomousPlan | None):
        if not plan:
            return None
        tasks = list(plan.tasks or [])
        for task in tasks:
            if task.task_type == "plan.owner_review":
                return task
        for task in tasks:
            if task.task_type == "company_context.report_risky_roles":
                return task
        return tasks[-1] if tasks else None

    def _role_gap_approval_summary(
        self,
        gap_id: str,
        approvals: list[ApprovalRequest],
        high_risk_tools: list[str],
    ) -> dict:
        approval = approvals[0] if approvals else None
        if not approval:
            state = "none"
            approval_id = None
            expires_at = None
            consumed_at = None
            created_at = None
        else:
            state = self._approval_state(approval)
            approval_id = approval.id
            expires_at = approval.expires_at.isoformat() if approval.expires_at else None
            consumed_at = approval.consumed_at.isoformat() if approval.consumed_at else None
            created_at = approval.created_at.isoformat()
        return {
            "state": state,
            "approval_id": approval_id,
            "required": bool(high_risk_tools),
            "high_risk_tools": high_risk_tools,
            "expires_at": expires_at,
            "consumed_at": consumed_at,
            "created_at": created_at,
            "apply_instruction": (
                {
                    "method": "POST",
                    "path": f"/api/roles/role-gaps/{gap_id}/apply",
                    "body": {"approval_id": approval_id},
                }
                if approval_id and state == "approved"
                else None
            ),
            "regenerate_instruction": {
                "method": "POST",
                "path": f"/api/roles/role-gaps/{gap_id}/approval/regenerate",
                "body": {},
            },
        }

    @staticmethod
    def _approval_state(approval: ApprovalRequest) -> str:
        if approval.consumed_at is not None:
            return "consumed"
        if (
            approval.status == "pending"
            and approval.expires_at is not None
            and approval.expires_at < utc_now()
        ):
            return "expired"
        return approval.status or "none"

    @staticmethod
    def _role_gap_business_function(gap: dict, proposed_manifest: dict) -> str:
        raw = (
            gap.get("capability")
            or proposed_manifest.get("family")
            or (gap.get("context") or {}).get("role_family")
            or "Unclassified"
        )
        return str(raw).replace("_", " ").replace("-", " ").strip().title() or "Unclassified"

    @staticmethod
    def _role_gap_summary_risk(gap: dict, high_risk_tools: list[str]) -> str:
        severity = gap.get("severity") or "medium"
        if severity == "critical":
            return "critical"
        if severity == "high" or high_risk_tools:
            return "high"
        if severity == "low":
            return "low"
        return "medium"

    @staticmethod
    def _role_gap_recommended_action(
        gap: dict,
        high_risk_tools: list[str],
        tool_readiness: dict,
        approval: dict,
    ) -> str:
        status = gap.get("status")
        if status == "resolved":
            return "completed"
        if status == "deferred":
            return "deferred"
        if status == "dismissed":
            return "dismissed"
        if status == "stale":
            return "stale"
        if not tool_readiness["all_ready"]:
            return "configure_tools"
        if not gap.get("proposed_role"):
            return "propose_role"
        if not high_risk_tools:
            return "create_role"
        approval_state = approval["state"]
        if approval_state == "approved":
            return "create_after_approval"
        if approval_state == "pending":
            return "await_approval"
        if approval_state in {"expired", "consumed", "rejected"}:
            return "regenerate_approval"
        return "request_approval"

    def _find_manifest_for_role_spec(
        self,
        role_spec: dict,
        manifests: list[dict],
    ) -> dict | None:
        target_id = role_spec.get("manifest_id") or slug_id(role_spec["name"])
        target_name = role_spec["name"].lower()
        for manifest in manifests:
            if manifest["id"] == target_id or manifest["name"].lower() == target_name:
                return manifest
        return None

    def _role_gap_proposal(self, gap: dict, company_profile: dict) -> dict:
        family = self._role_gap_family(gap)
        definition = OperatingModelBuilder.ROLE_DEFINITIONS.get(
            family,
            OperatingModelBuilder.ROLE_DEFINITIONS["operations"],
        )
        requested_tools = list(gap.get("requested_tools") or [])
        default_tools = self._unique([*definition.default_tools, *requested_tools])
        role_name = self._role_gap_role_name(gap, definition.name)
        company_name = (
            company_profile.get("name")
            or company_profile.get("company_name")
            or settings.app_name
        )
        company_namespace = gap.get("company_namespace") or "company:default"
        instructions = (
            f"You are the {role_name} for {{company_name}}. This role was created "
            f"because the system reported a capability gap: {gap['title']}. "
            f"Gap description: {gap['description']} "
            "Your first responsibility is to unblock that work safely, document the "
            "new operating procedure, use company memory before acting, and escalate "
            "any high-risk external action for approval."
        )
        manifest_payload = {
            "family": family,
            "name": role_name,
            "description": (
                f"Dynamic role generated from role gap '{gap['title']}' to cover "
                f"{gap.get('capability') or family} capability."
            ),
            "instructions_template": instructions,
            "default_tools": default_tools,
            "memory_namespace": f"{company_namespace}:gap:{slug_id(role_name)}",
            "approval_policy": self._role_gap_approval_policy(gap, definition.approval_policy),
            "success_metrics": self._unique(
                [
                    "gap_resolution_time",
                    "blocked_work_unblocked",
                    *list(definition.success_metrics),
                ]
            ),
            "is_core": False,
            "config": {
                "source": "role_gap_loop",
                "role_gap_id": gap["id"],
                "role_gap_title": gap["title"],
                "role_gap_severity": gap["severity"],
                "rationale": [
                    "Generated from a persistent role gap event.",
                    *self._role_gap_rationale(gap, family),
                ],
                "capabilities": self._unique(
                    [
                        *(definition.capabilities or []),
                        gap.get("capability"),
                    ]
                ),
                "company_name": company_name,
                "operating_model_version": "role-gap-loop-v1",
            },
        }
        return {
            "role_gap_id": gap["id"],
            "confidence": "medium",
            "family": family,
            "manifest_payload": manifest_payload,
            "rationale": manifest_payload["config"]["rationale"],
            "activation_triggers": [
                gap["title"],
                gap.get("capability"),
                *requested_tools,
            ],
        }

    def _role_gap_family(self, gap: dict) -> str:
        text = " ".join(
            str(value).lower()
            for value in [
                gap.get("title"),
                gap.get("description"),
                gap.get("capability"),
                " ".join(gap.get("requested_tools") or []),
            ]
        )
        family_signals = OperatingModelBuilder.FAMILY_SIGNALS
        best_family = "operations"
        best_score = 0
        for family, signals in family_signals.items():
            score = sum(1 for signal in signals if signal in text)
            if score > best_score:
                best_family = family
                best_score = score
        if any(term in text for term in {"phone", "call", "sms", "whatsapp", "message"}):
            return "communications"
        return best_family

    @staticmethod
    def _role_gap_role_name(gap: dict, fallback_name: str) -> str:
        title = " ".join(str(gap.get("title") or "").strip().split())
        if not title:
            title = fallback_name
        title = title[:80].strip(" .:-_")
        lower_title = title.lower()
        if any(suffix in lower_title for suffix in {"agent", "specialist", "manager", "advisor"}):
            return title
        return f"{title} Specialist"

    @staticmethod
    def _role_gap_approval_policy(gap: dict, default_policy: str) -> str:
        requested_tools = set(gap.get("requested_tools") or [])
        if (
            gap.get("severity") in {"high", "critical"}
            or requested_tools & AgentManager.HIGH_RISK_ROLE_TOOLS
        ):
            return "sensitive"
        return default_policy

    def _role_gap_high_risk_tools(self, tool_names: list[str]) -> list[str]:
        return self._unique(
            self.TOOL_ALIASES.get(tool_name, tool_name)
            for tool_name in tool_names
            if self.TOOL_ALIASES.get(tool_name, tool_name) in self.HIGH_RISK_ROLE_TOOLS
        )

    async def _latest_role_gap_tool_grant_approval(self, gap_id: str) -> dict | None:
        async with async_session() as session:
            result = await session.execute(
                select(ApprovalRequest)
                .where(
                    ApprovalRequest.action_type == "role_gap.tool_grant",
                    ApprovalRequest.target_type == "role_gap",
                    ApprovalRequest.target_id == gap_id,
                )
                .order_by(ApprovalRequest.created_at.desc())
            )
            approval = result.scalars().first()
            if not approval:
                return None
            if (
                approval.status == "pending"
                and approval.expires_at is not None
                and approval.expires_at < utc_now()
            ):
                approval.status = "expired"
                approval.resolved_at = utc_now()
                await session.commit()
            return {
                "approval_id": approval.id,
                "state": self._approval_state(approval),
                "risk_level": approval.risk_level,
                "action_payload": approval.action_payload,
                "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
                "consumed_at": (
                    approval.consumed_at.isoformat() if approval.consumed_at else None
                ),
                "created_at": approval.created_at.isoformat(),
            }

    async def _validate_role_gap_tool_grant_approval(
        self,
        approval_id: str,
        gap: dict,
        manifest_payload: dict,
        high_risk_tools: list[str],
    ) -> None:
        async with async_session() as session:
            result = await session.execute(
                select(ApprovalRequest).where(ApprovalRequest.id == approval_id)
            )
            approval = result.scalar_one_or_none()
            if not approval:
                raise ValueError(f"Approval request {approval_id} not found")
            if approval.action_type != "role_gap.tool_grant":
                raise ValueError(f"Approval request {approval_id} is not a role grant")
            if approval.target_type != "role_gap" or approval.target_id != gap["id"]:
                raise ValueError(
                    f"Approval request {approval_id} is not valid for role gap {gap['id']}"
                )
            if approval.status != "approved":
                raise ValueError(f"Approval request {approval_id} is {approval.status}")
            if approval.consumed_at is not None:
                raise ValueError(f"Approval request {approval_id} was already consumed")
            if approval.expires_at and approval.expires_at < utc_now():
                approval.status = "expired"
                approval.resolved_at = utc_now()
                await session.commit()
                raise ValueError(f"Approval request {approval_id} has expired")
            payload = approval.action_payload or {}
            if payload.get("role_gap_id") != gap["id"]:
                raise ValueError(
                    f"Approval request {approval_id} payload does not match role gap {gap['id']}"
                )
            approved_tools = set(payload.get("high_risk_tools") or [])
            requested_tools = set(high_risk_tools)
            if requested_tools - approved_tools:
                raise ValueError(
                    f"Approval request {approval_id} does not cover requested tools: "
                    + ", ".join(sorted(requested_tools - approved_tools))
                )
            if payload.get("role_name") != manifest_payload.get("name"):
                raise ValueError(
                    f"Approval request {approval_id} role name does not match current proposal"
                )

    async def _request_role_gap_tool_grant_approval(
        self,
        gap: dict,
        manifest_payload: dict,
        high_risk_tools: list[str],
        requested_by: str,
    ) -> str:
        role_name = manifest_payload["name"]
        description = (
            f"Approve generated role '{role_name}' for role gap '{gap['title']}'. "
            f"This role requests high-risk tools: {', '.join(high_risk_tools)}."
        )
        return await self._request_approval(
            "company_builder",
            "role_gap.tool_grant",
            description,
            {
                "role_gap_id": gap["id"],
                "role_gap_title": gap["title"],
                "role_name": role_name,
                "family": manifest_payload.get("family"),
                "high_risk_tools": high_risk_tools,
                "default_tools": manifest_payload.get("default_tools", []),
                "manifest_payload": manifest_payload,
                "requested_by": requested_by,
            },
            requester="company_builder",
            requester_type="agent",
            risk_level="high",
            target_type="role_gap",
            target_id=gap["id"],
            expires_in_minutes=1440,
        )

    async def _role_gap_approval_required_response(
        self,
        gap: dict,
        approval_id: str,
        high_risk_tools: list[str],
    ) -> dict:
        response = await self._mark_role_gap_approval_required(
            gap["id"],
            approval_id,
            high_risk_tools,
        )
        response["approval_required"] = True
        response["approval_id"] = approval_id
        response["high_risk_tools"] = high_risk_tools
        return response

    async def _mark_role_gap_approval_required(
        self,
        gap_id: str,
        approval_id: str,
        high_risk_tools: list[str],
    ) -> dict:
        async with async_session() as session:
            result = await session.execute(select(RoleGap).where(RoleGap.id == gap_id))
            db_gap = result.scalar_one_or_none()
            if not db_gap:
                raise ValueError(f"Role gap {gap_id} not found")
            db_gap.resolution = {
                **(db_gap.resolution or {}),
                "approval_required": True,
                "pending_approval_id": approval_id,
                "high_risk_tools": high_risk_tools,
                "approval_requested_at": utc_now().isoformat(),
            }
            db_gap.updated_at = utc_now()
            await session.commit()
            return self._role_gap_to_dict(db_gap)

    async def _mark_role_gap_resolved(self, gap_id: str, resolution: dict) -> dict:
        async with async_session() as session:
            result = await session.execute(select(RoleGap).where(RoleGap.id == gap_id))
            db_gap = result.scalar_one_or_none()
            if not db_gap:
                raise ValueError(f"Role gap {gap_id} not found")
            db_gap.status = "resolved"
            db_gap.resolution = {
                **(db_gap.resolution or {}),
                **resolution,
                "approval_required": False,
            }
            db_gap.resolved_at = utc_now()
            db_gap.updated_at = utc_now()
            await session.commit()
            return self._role_gap_to_dict(db_gap)

    @staticmethod
    def _role_gap_rationale(gap: dict, family: str) -> list[str]:
        rationale = [f"Mapped the gap to the {family} role family."]
        if gap.get("requested_tools"):
            rationale.append(
                "The gap explicitly requested tools: "
                + ", ".join(gap["requested_tools"])
                + "."
            )
        if gap.get("source_agent_id"):
            rationale.append(f"Reported by agent {gap['source_agent_id']}.")
        return rationale

    def _activation_cadence_for_role_gap(
        self,
        gap: dict,
        manifest_payload: dict,
    ) -> dict:
        family = manifest_payload.get("family") or self._role_gap_family(gap)
        default = self._cadence_profile_for_family(str(family))
        high_risk_tools = self._role_gap_high_risk_tools(
            manifest_payload.get("default_tools", [])
        )
        frequency = "daily" if gap.get("severity") in {"high", "critical"} else default["frequency"]
        if high_risk_tools:
            frequency = "daily"
        return {
            "cadence_id": f"cadence:{gap['id']}",
            "source": "role_gap_activation",
            "source_role_gap_id": gap["id"],
            "source_snapshot_id": (gap.get("context") or {}).get("snapshot_id"),
            "source_hash": (gap.get("context") or {}).get("source_hash"),
            "frequency": frequency,
            "review_window": default["review_window"],
            "signals": self._unique(
                [
                    *default["signals"],
                    gap.get("capability"),
                    *list(gap.get("requested_tools") or []),
                ]
            ),
            "checklist": default["checklist"],
            "owner_review": {
                "required_for_external_side_effects": True,
                "approval_mode": "manual_only",
                "high_risk_tools": high_risk_tools,
            },
            "evidence": {
                "role_gap_id": gap["id"],
                "role_gap_title": gap["title"],
                "business_function": self._role_gap_business_function(
                    gap,
                    manifest_payload,
                ),
            },
        }

    def _default_operating_cadence(
        self,
        agent: dict,
        *,
        company_namespace: str | None,
    ) -> dict:
        profile = self._cadence_profile_for_family(agent.get("role_family") or "operations")
        return {
            "cadence_id": f"cadence:agent:{agent['id']}",
            "source": "agent_registry",
            "source_role_gap_id": None,
            "source_snapshot_id": None,
            "source_hash": None,
            "frequency": profile["frequency"],
            "review_window": profile["review_window"],
            "signals": profile["signals"],
            "checklist": profile["checklist"],
            "owner_review": {
                "required_for_external_side_effects": True,
                "approval_mode": "manual_only",
                "high_risk_tools": [],
            },
            "evidence": {
                "agent_id": agent["id"],
                "role_name": agent["role_name"],
                "business_function": str(agent.get("role_family") or "operations")
                .replace("_", " ")
                .title(),
                "company_namespace": company_namespace,
            },
        }

    @staticmethod
    def _cadence_profile_for_family(family: str) -> dict:
        normalized = str(family or "operations").lower()
        profiles = {
            "finance": {
                "frequency": "weekly",
                "review_window": "Monday finance review",
                "signals": ["sales_invoice", "material_request", "cash_risk"],
                "checklist": [
                    "Review ERPNext invoices, payables, and finance exceptions.",
                    "Summarize risks requiring owner approval.",
                    "Propose accounting tasks without external mutation.",
                ],
            },
            "legal": {
                "frequency": "weekly",
                "review_window": "Legal/compliance review",
                "signals": ["contract", "policy", "jurisdiction", "compliance_risk"],
                "checklist": [
                    "Review new legal/compliance signals from memory and ERPNext context.",
                    "Prepare advisory drafts only.",
                    "Escalate legal decisions to the owner.",
                ],
            },
            "sales": {
                "frequency": "daily",
                "review_window": "Daily pipeline review",
                "signals": ["lead", "opportunity", "customer", "follow_up"],
                "checklist": [
                    "Review new leads and open opportunities.",
                    "Identify blocked follow-ups and missing CRM data.",
                    "Request approval for any external outreach.",
                ],
            },
            "marketing": {
                "frequency": "weekly",
                "review_window": "Growth planning review",
                "signals": ["campaign", "content", "analytics", "positioning"],
                "checklist": [
                    "Review positioning, channels, and recent market-facing tasks.",
                    "Draft content or campaign plans as advisory outputs.",
                    "Escalate publication or spend decisions.",
                ],
            },
            "support": {
                "frequency": "daily",
                "review_window": "Support queue review",
                "signals": ["issue", "ticket", "customer", "sla"],
                "checklist": [
                    "Review open support issues and stale customer responses.",
                    "Summarize blockers and proposed replies.",
                    "Request approval before sending customer communications.",
                ],
            },
            "security": {
                "frequency": "weekly",
                "review_window": "Security and control review",
                "signals": ["audit_event", "auth_failure", "tool_misuse", "secret"],
                "checklist": [
                    "Review audit/control evidence and security findings.",
                    "Identify policy drift and risky tool requests.",
                    "Escalate remediations with operational impact.",
                ],
            },
            "communications": {
                "frequency": "daily",
                "review_window": "Communications readiness review",
                "signals": ["email", "call", "message", "owner_notification"],
                "checklist": [
                    "Review inbound/outbound communication readiness.",
                    "Draft responses or outreach plans.",
                    "Require owner approval for live external messages.",
                ],
            },
            "knowledge": {
                "frequency": "weekly",
                "review_window": "Memory quality review",
                "signals": ["memory_trace", "empty_recall", "stale_memory", "namespace"],
                "checklist": [
                    "Review memory coverage and steward findings.",
                    "Propose memory consolidation or namespace fixes.",
                    "Avoid deleting audit-preserving records.",
                ],
            },
        }
        return profiles.get(
            normalized,
            {
                "frequency": "weekly",
                "review_window": "Operating review",
                "signals": ["workflow", "task", "approval", "role_gap"],
                "checklist": [
                    "Review relevant ERPNext, memory, workflow, and approval signals.",
                    "Produce an actionable plan with evidence links.",
                    "Escalate side effects and high-risk changes to the owner.",
                ],
            },
        )

    @staticmethod
    def _operating_cadence_owner_actions(
        *,
        active_backlog: dict,
        stale_backlog: dict,
        cadence_count: int,
    ) -> list[dict]:
        actions = []
        if active_backlog["counts"]["total"]:
            actions.append(
                {
                    "action": "review_active_role_backlog",
                    "priority": "high",
                    "reason": (
                        f"{active_backlog['counts']['total']} current role recommendations "
                        "are waiting for owner review."
                    ),
                }
            )
        if stale_backlog["counts"]["total"]:
            actions.append(
                {
                    "action": "archive_or_filter_stale_role_gaps",
                    "priority": "low",
                    "reason": (
                        f"{stale_backlog['counts']['total']} role recommendations came "
                        "from superseded company-context snapshots."
                    ),
                }
            )
        if cadence_count == 0:
            actions.append(
                {
                    "action": "activate_first_roles",
                    "priority": "medium",
                    "reason": "No active agent operating cadences are available yet.",
                }
            )
        return actions

    async def _report_missing_agent_gap(self, agent_id: str, task: str) -> dict | None:
        role_name = agent_id.replace("_", " ").replace("-", " ").strip().title()
        return await self._report_autonomous_role_gap(
            title=f"Missing agent: {role_name or agent_id}",
            description=(
                f"A task attempted to invoke agent '{agent_id}', but no active agent "
                f"exists for that role. Task excerpt: {self._excerpt(task)}"
            ),
            severity="high",
            source_type="system",
            capability=agent_id,
            context={
                "trigger": "missing_agent_invocation",
                "agent_id": agent_id,
                "task_excerpt": self._excerpt(task, limit=500),
                "dedupe_key": f"missing-agent:{agent_id}",
            },
            reporter="orchestrator",
        )

    async def _maybe_report_autonomous_role_gap(
        self,
        *,
        trigger: str,
        source_agent_id: str | None,
        company_namespace: str | None,
        task: str,
        result: str,
        context: dict | None = None,
    ) -> dict | None:
        combined = f"{task}\n{result}"
        if not self._looks_like_role_gap_signal(combined):
            return None

        capability = self._capability_for_text(combined)
        requested_tools = self._requested_tools_for_text(combined)
        title = self._autonomous_gap_title(combined, capability)
        dedupe_subject = source_agent_id or trigger
        dedupe_key = f"{trigger}:{dedupe_subject}:{capability}:{','.join(requested_tools)}"
        return await self._report_autonomous_role_gap(
            title=title,
            description=(
                "The runtime detected blocked work that appears to need an additional "
                "role, skill, tool, or integration.\n\n"
                f"Task excerpt: {self._excerpt(task, limit=600)}\n\n"
                f"Observed output excerpt: {self._excerpt(result, limit=600)}"
            ),
            severity="medium",
            source_agent_id=source_agent_id,
            source_type="agent" if source_agent_id else "system",
            company_namespace=company_namespace,
            capability=capability,
            requested_tools=requested_tools,
            context={
                **(context or {}),
                "trigger": trigger,
                "task_excerpt": self._excerpt(task, limit=500),
                "result_excerpt": self._excerpt(result, limit=500),
                "dedupe_key": dedupe_key,
            },
            reporter=source_agent_id or "runtime_detector",
        )

    async def _report_autonomous_role_gap(
        self,
        *,
        title: str,
        description: str,
        severity: str = "medium",
        source_agent_id: str | None = None,
        source_type: str = "system",
        company_namespace: str | None = None,
        capability: str | None = None,
        requested_tools: list[str] | None = None,
        context: dict | None = None,
        reporter: str = "runtime_detector",
    ) -> dict | None:
        data = type(
            "AutonomousRoleGap",
            (),
            {
                "title": title,
                "description": description,
                "severity": severity,
                "source_agent_id": source_agent_id,
                "source_type": source_type,
                "company_namespace": company_namespace or "company:default",
                "capability": capability,
                "requested_tools": requested_tools or [],
                "context": context or {},
            },
        )()
        try:
            return await self.report_role_gap(data, reporter=reporter)
        except Exception:
            return None

    def _looks_like_role_gap_signal(self, text: str) -> bool:
        normalized = " ".join(str(text or "").lower().split())
        if not normalized:
            return False
        has_blocker = any(phrase in normalized for phrase in self.AUTONOMOUS_GAP_BLOCKERS)
        has_subject = any(subject in normalized for subject in self.AUTONOMOUS_GAP_SUBJECTS)
        return has_blocker and has_subject

    def _capability_for_text(self, text: str) -> str:
        normalized = str(text or "").lower()
        for hint, capability in self.TOOL_CAPABILITY_HINTS.items():
            if hint in normalized:
                return capability
        for family, signals in OperatingModelBuilder.FAMILY_SIGNALS.items():
            if any(signal in normalized for signal in signals):
                return family
        return "runtime_capability"

    def _requested_tools_for_text(self, text: str) -> list[str]:
        normalized = str(text or "").lower()
        tools = [
            tool_name
            for hint, tool_name in self.TOOL_NAME_HINTS.items()
            if hint in normalized
        ]
        return self._unique(tools)

    @staticmethod
    def _autonomous_gap_title(text: str, capability: str) -> str:
        normalized = " ".join(str(text or "").split())
        if len(normalized) > 0:
            return f"Blocked work needs {capability.replace('_', ' ')} capability"
        return "Blocked work needs additional capability"

    @staticmethod
    def _company_namespace_from_memory_namespace(memory_namespace: str | None) -> str | None:
        if not memory_namespace:
            return None
        parts = memory_namespace.split(":")
        if len(parts) >= 2 and parts[0] == "company" and parts[1]:
            return ":".join(parts[:2])
        return None

    @staticmethod
    def _excerpt(text: str, limit: int = 240) -> str:
        normalized = " ".join(str(text or "").split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 3].rstrip() + "..."

    async def _seed_company_memory(
        self,
        operating_model: dict,
        instantiated_agents: list[dict],
    ) -> None:
        if not self._memory:
            return
        for seed in operating_model.get("memory_seed", []):
            try:
                await self._memory.remember(
                    type(
                        "MemW",
                        (),
                        {
                            "agent_id": None,
                            "memory_type": seed["memory_type"],
                            "namespace": seed["namespace"],
                            "content": seed["content"],
                            "metadata": {
                                "source": "company_builder",
                                "seed_id": seed["id"],
                                "company_name": operating_model["company_name"],
                                "company_namespace": operating_model["company_namespace"],
                                "operating_model_version": operating_model["version"],
                                "instantiated_agent_ids": [
                                    agent["agent_id"] for agent in instantiated_agents
                                ],
                            },
                            "importance": seed["importance"],
                        },
                    )()
                )
            except Exception:
                pass

    @staticmethod
    def _render_template(template: str, values: dict) -> str:
        rendered = template
        for key, value in values.items():
            rendered = rendered.replace("{" + key + "}", str(value))
        return rendered

    @staticmethod
    def _object_from_dict(values: dict):
        return type("DynamicObject", (), values)()

    @staticmethod
    def _unique(values) -> list:
        result = []
        for value in values:
            if value and value not in result:
                result.append(value)
        return result

    @staticmethod
    def _role_gap_to_dict(gap: RoleGap) -> dict:
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
    def _recommended_role_families(company_profile: dict) -> list[str]:
        text = " ".join(str(value).lower() for value in company_profile.values())
        families = [
            "company_builder",
            "supervisor",
            "finance",
            "legal",
            "sales",
            "marketing",
            "support",
            "product",
            "operations",
            "knowledge",
            "communications",
        ]
        if any(term in text for term in {"saas", "software", "tech", "platform", "app"}):
            families.append("engineering")
        if any(term in text for term in {"fintech", "finance", "health", "security", "legal"}):
            families.append("security")
        if any(term in text for term in {"hiring", "employees", "recruiting", "people"}):
            families.append("hr")
        return families

    @staticmethod
    def _org_structure(role_families: list[str]) -> dict:
        specialists = [
            family
            for family in role_families
            if family not in {"company_builder", "supervisor"}
        ]
        return {
            "company_builder": "provisions and evolves the AI organization",
            "supervisor": "coordinates specialist agents and escalates exceptions",
            "specialists": specialists,
        }


def slug_id(name: str) -> str:
    from slugify import slugify
    return slugify(name, separator="_", max_length=64)
