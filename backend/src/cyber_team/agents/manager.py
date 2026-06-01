"""Agent Manager — registration, lifecycle, and configuration of agents."""

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update

from cyber_team.audit.service import AuditService
from cyber_team.clock import utc_now
from cyber_team.company.operating_model import OperatingModelBuilder
from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import Agent, ApprovalRequest, RoleManifest
from cyber_team.llm.gateway import LLMGateway
from cyber_team.memory.service import MemoryService


class AgentManager:
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

    async def invoke_agent(self, agent_id: str, task: str) -> str:
        agent = await self.get_agent(agent_id)
        if not agent:
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

        # Retrieve relevant memories for context
        memory_context = ""
        if self._memory:
            try:
                mem_results = await self._memory.recall(
                    type(
                        "MemQ",
                        (),
                        {
                            "query": task,
                            "agent_id": agent_id,
                            "namespace": agent["memory_namespace"],
                            "memory_type": None,
                            "limit": 5,
                        },
                    )()
                )
                if mem_results:
                    memory_context = "\n\nRelevant memories:\n" + "\n".join(
                        f"- {m['content']}" for m in mem_results[:5]
                    )
            except Exception:
                pass  # Memory lookup failure shouldn't block invocation

        # Build prompt and invoke LLM
        system_prompt = agent["instructions"] + memory_context
        result = await self._llm.invoke(
            system_prompt=system_prompt,
            user_message=task,
            agent_id=agent_id,
        )

        # Store invocation in episodic memory
        if self._memory:
            try:
                await self._memory.remember(
                    type(
                        "MemW",
                        (),
                        {
                            "agent_id": agent_id,
                            "memory_type": "episodic",
                            "namespace": agent["memory_namespace"],
                            "content": f"Task: {task[:200]} | Result: {result[:200]}",
                            "metadata": {"type": "invocation"},
                            "importance": 0.6,
                        },
                    )()
                )
            except Exception:
                pass

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
            system_prompt = agent["instructions"]
            agent_name = agent["role_name"]
        else:
            # Route to supervisor/orchestrator
            system_prompt = (
                "You are the Cyber-Team supervisor. Coordinate between "
                "specialist agents and help the owner."
            )
            agent_id = "supervisor"
            agent_name = "Supervisor"

        response = await self._llm.invoke(
            system_prompt=system_prompt,
            user_message=message,
            agent_id=agent_id,
            conversation_id=conversation_id,
        )

        return {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "message": response,
            "conversation_id": conversation_id,
        }

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

        create_data = type("AgentCreate", (), {
            "role_family": manifest["family"],
            "role_name": manifest["name"],
            "instructions": instructions,
            "tools": manifest["default_tools"],
            "memory_namespace": manifest["memory_namespace"],
            "approval_policy": manifest["approval_policy"],
            "config": {**manifest["config"], **overrides},
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
        return approval_id

    async def get_approval_queue(self, status: str | None = None) -> list[dict]:
        async with async_session() as session:
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
        if unsupported_tools:
            config["requested_tools"] = list(requested_tools)
            config["unsupported_tools"] = unsupported_tools
        else:
            config.pop("requested_tools", None)
            config.pop("unsupported_tools", None)

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
            if value not in result:
                result.append(value)
        return result

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
