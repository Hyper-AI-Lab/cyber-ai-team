from unittest.mock import AsyncMock

import pytest

from cyber_team.agents.manager import AgentManager, slug_id
from cyber_team.company.operating_model import OperatingModelBuilder


class FakeTool:
    def __init__(self, name: str):
        self.name = name


class FakeToolRegistry:
    def __init__(self, tool_names: set[str]):
        self._tool_names = tool_names

    def list_tools(self):
        return [FakeTool(name) for name in sorted(self._tool_names)]

    def get_tool(self, name: str):
        if name in self._tool_names:
            return FakeTool(name)
        return None

    def get_tool_readiness(self, name: str):
        if name in self._tool_names:
            return {
                "state": "live",
                "readiness_reason": "test tool is ready",
                "side_effects": name in AgentManager.HIGH_RISK_ROLE_TOOLS,
                "executor_kind": "live",
                "requires_configuration": False,
                "executable": True,
            }
        return {
            "state": "unavailable",
            "readiness_reason": f"Tool not found: {name}",
            "side_effects": False,
            "executor_kind": "unavailable",
            "requires_configuration": False,
            "executable": False,
        }


class FakeMemoryService:
    def __init__(self):
        self.entries = []

    async def remember(self, data):
        entry = {
            "agent_id": data.agent_id,
            "memory_type": data.memory_type,
            "namespace": data.namespace,
            "content": data.content,
            "metadata": data.metadata,
            "importance": data.importance,
        }
        self.entries.append(entry)
        return entry


class FakeTraceMemoryService:
    def __init__(self):
        self.policy_requests = []
        self.recall_requests = []
        self.entries = []
        self.traces = []

    async def recall_with_policy(self, data):
        self.policy_requests.append(data)
        return {
            "items": [
                {
                    "id": "memory-1",
                    "content": "The launch plan should prioritize customer interviews.",
                    "memory_type": "semantic",
                    "namespace": data.memory_namespace,
                    "agent_id": data.agent_id,
                    "importance": 0.9,
                    "score": 1.0,
                    "scope": "agent_private",
                },
                {
                    "id": "memory-company",
                    "content": "Acme agents must preserve company context across roles.",
                    "memory_type": "semantic",
                    "namespace": "company:acme",
                    "agent_id": None,
                    "importance": 0.95,
                    "score": 1.0,
                    "scope": "company_constitution",
                },
            ],
            "policy": {
                "version": "memory-policy-v1",
                "strategy": "agent-private-plus-company-shared",
                "agent_id": data.agent_id,
                "role_family": data.role_family,
                "role_name": data.role_name,
                "memory_namespace": data.memory_namespace,
                "company_namespace": "company:acme",
                "limit": data.limit,
                "scopes": [
                    {"name": "agent_private", "namespace": data.memory_namespace},
                    {"name": "company_constitution", "namespace": "company:acme"},
                ],
                "scope_results": [
                    {
                        "name": "agent_private",
                        "namespace": data.memory_namespace,
                        "returned": 1,
                        "added": 1,
                    },
                    {
                        "name": "company_constitution",
                        "namespace": "company:acme",
                        "returned": 1,
                        "added": 1,
                    },
                ],
                "returned": 2,
            },
            "errors": [],
        }

    async def recall(self, data):
        self.recall_requests.append(data)
        return [
            {
                "id": "memory-1",
                "content": "The launch plan should prioritize customer interviews.",
                "memory_type": "semantic",
                "namespace": data.namespace,
                "agent_id": data.agent_id,
                "importance": 0.9,
                "score": 1.0,
            }
        ]

    async def remember(self, data):
        entry = {
            "id": "memory-2",
            "agent_id": data.agent_id,
            "memory_type": data.memory_type,
            "namespace": data.namespace,
            "content": data.content,
            "metadata": data.metadata,
            "importance": data.importance,
        }
        self.entries.append(entry)
        return entry

    async def record_trace(self, data):
        trace = {
            "invocation_id": data.invocation_id,
            "agent_id": data.agent_id,
            "memory_namespace": data.memory_namespace,
            "read_policy": data.read_policy,
            "write_policy": data.write_policy,
            "recalled_memory_ids": data.recalled_memory_ids,
            "written_memory_ids": data.written_memory_ids,
            "recall_count": data.recall_count,
            "write_count": data.write_count,
            "errors": data.errors,
            "metadata": data.metadata,
        }
        self.traces.append(trace)
        return trace


class FakeLLM:
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    async def invoke(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def role_gap_with_proposal(default_tools: list[str]) -> dict:
    return {
        "id": "gap_123",
        "title": "Need outbound calls",
        "description": "Sales work is blocked until agents can call customers.",
        "status": "proposed",
        "severity": "high",
        "source_agent_id": "sales",
        "source_type": "agent",
        "company_namespace": "company:acme",
        "capability": "outbound_voice",
        "requested_tools": default_tools,
        "context": {},
        "resolution": {},
        "proposed_role": {
            "manifest_payload": {
                "family": "communications",
                "name": "Outbound Calling Specialist",
                "description": "Handles outbound calls.",
                "instructions_template": "Call customers safely.",
                "default_tools": default_tools,
                "memory_namespace": "company:acme:gap:outbound_calling_specialist",
                "approval_policy": "sensitive",
                "success_metrics": [],
                "is_core": False,
                "config": {},
            }
        },
    }


def test_operating_model_builds_dynamic_roles_from_company_context():
    model = OperatingModelBuilder().build(
        {
            "name": "HyperAI Lab",
            "industry": "AI SaaS with privacy and compliance requirements",
            "stage": "launch and growth",
            "product": "AI company operations platform",
            "target_customers": "B2B clients and founders",
            "channels": "email, phone, SMS, CRM, calendar, WhatsApp",
            "goals": "acquire clients and automate operations",
            "jurisdictions": "US, EU, Germany",
        },
        available_tools={
            "access_audit",
            "agent_invoke",
            "agent_status_read",
            "analytics_read",
            "approval_request",
            "approval_resolve",
            "brand_monitor",
            "call_receive",
            "company_profile_read",
            "compliance_check",
            "content_create",
            "crm_lead_search",
            "document_index",
            "email_read",
            "erpnext_create_lead",
            "erpnext_get_invoices",
            "knowledge_query",
            "make_call",
            "memory_recall",
            "memory_remember",
            "message_read",
            "owner_notify",
            "process_audit",
            "regulation_search",
            "role_catalog_search",
            "role_instantiate",
            "send_email",
            "send_message",
            "send_sms",
            "sms_read",
            "social_post_draft",
        },
    )

    planned_names = {role["name"] for role in model["planned_role_specs"]}
    loop_ids = {loop["id"] for loop in model["adaptive_loops"]}
    gap_integrations = {
        gap.get("integration")
        for gap in model["capability_gaps"]
        if gap["type"] == "integration_gap"
    }

    assert model["version"] == "autonomous-company-os-v2.0"
    assert "Company Memory Steward" in planned_names
    assert "Outbound Calling Specialist" in planned_names
    assert "Integration Architect" in planned_names
    assert "Compliance Sentinel" in planned_names
    assert "Growth Experiment Designer" in planned_names
    assert "customer_communication_loop" in loop_ids
    assert "role_gap_monitoring_loop" in loop_ids
    assert "calendar" in gap_integrations
    assert model["memory_seed"][0]["id"] == "company_constitution"


def test_operating_model_defers_untriggered_roles_without_losing_the_backlog():
    model = OperatingModelBuilder().build(
        {
            "name": "Quiet Research Studio",
            "industry": "research",
        },
        available_tools={"memory_recall", "memory_remember"},
    )

    planned_names = {role["name"] for role in model["planned_role_specs"]}
    backlog_families = {role["family"] for role in model["role_backlog"]}

    assert "Company Memory Steward" in planned_names
    assert "Outbound Calling Specialist" not in planned_names
    assert "engineering" in backlog_families
    assert "hr" in backlog_families
    assert model["recommended_next_questions"]


def test_role_gap_proposal_maps_gap_to_dynamic_manifest_payload():
    manager = AgentManager()
    proposal = manager._role_gap_proposal(
        {
            "id": "gap_123",
            "title": "Need outbound client calls",
            "description": "Sales work is blocked until the team can call clients.",
            "status": "open",
            "severity": "high",
            "source_agent_id": "sales_agent",
            "source_type": "agent",
            "company_namespace": "company:acme",
            "capability": "outbound_voice",
            "requested_tools": ["make_call", "send_sms"],
            "context": {},
            "proposed_role": {},
            "resolution": {},
        },
        {"name": "Acme"},
    )

    manifest = proposal["manifest_payload"]

    assert proposal["family"] == "communications"
    assert manifest["name"] == "Need outbound client calls Specialist"
    assert manifest["approval_policy"] == "sensitive"
    assert "make_call" in manifest["default_tools"]
    assert manifest["memory_namespace"] == "company:acme:gap:need_outbound_client_calls_specialist"
    assert manifest["config"]["role_gap_id"] == "gap_123"


@pytest.mark.asyncio
async def test_missing_agent_invocation_reports_autonomous_role_gap():
    manager = AgentManager()
    manager.get_agent = AsyncMock(return_value=None)
    manager.report_role_gap = AsyncMock(return_value={"id": "gap_missing_agent"})

    with pytest.raises(ValueError, match="Agent outbound_caller not found"):
        await manager.invoke_agent("outbound_caller", "Call the warm lead.")

    report_data = manager.report_role_gap.await_args.args[0]
    assert report_data.title == "Missing agent: Outbound Caller"
    assert report_data.severity == "high"
    assert report_data.capability == "outbound_caller"
    assert report_data.context["dedupe_key"] == "missing-agent:outbound_caller"


@pytest.mark.asyncio
async def test_agent_invocation_records_memory_trace():
    memory = FakeTraceMemoryService()
    manager = AgentManager(memory_service=memory)
    manager._llm = FakeLLM("Launch brief complete.")
    manager.get_agent = AsyncMock(
        return_value={
            "id": "ops_agent",
            "role_family": "operations",
            "role_name": "Operations Manager",
            "instructions": "Run company operations.",
            "tools": [],
            "memory_namespace": "company:acme:ops",
            "approval_policy": "auto",
            "status": "active",
            "config": {},
        }
    )
    manager._check_approval_policy = AsyncMock(return_value=False)
    manager._maybe_report_autonomous_role_gap = AsyncMock()

    result = await manager.invoke_agent("ops_agent", "Prepare the launch brief.")

    assert result == "Launch brief complete."
    assert memory.policy_requests[0].memory_namespace == "company:acme:ops"
    assert memory.policy_requests[0].role_family == "operations"
    assert "Memory protocol context" in manager._llm.calls[0]["system_prompt"]
    assert "customer interviews" in manager._llm.calls[0]["system_prompt"]
    assert "company context across roles" in manager._llm.calls[0]["system_prompt"]
    assert memory.entries[0]["metadata"]["type"] == "agent_invocation_summary"
    assert memory.entries[0]["metadata"]["protocol_version"] == "agent-memory-protocol-v1"
    assert memory.entries[0]["metadata"]["traceable"] is True
    assert memory.entries[0]["metadata"]["recalled_memory_ids"] == [
        "memory-1",
        "memory-company",
    ]
    assert memory.traces[0]["recalled_memory_ids"] == ["memory-1", "memory-company"]
    assert memory.traces[0]["written_memory_ids"] == ["memory-2"]
    assert memory.traces[0]["read_policy"]["strategy"] == "agent-private-plus-company-shared"
    assert memory.traces[0]["read_policy"]["scope_results"][0]["added"] == 1
    assert memory.traces[0]["write_policy"]["version"] == "memory-write-policy-v1"
    assert memory.traces[0]["write_policy"]["memory_type"] == "episodic"
    assert (
        memory.traces[0]["write_policy"]["strategy"]
        == "durable-episodic-invocation-summary"
    )
    assert memory.traces[0]["metadata"]["protocol_version"] == "agent-memory-protocol-v1"
    assert memory.traces[0]["metadata"]["role_name"] == "Operations Manager"
    assert memory.traces[0]["metadata"]["memory_coverage"] == "hit"


@pytest.mark.asyncio
async def test_agent_invocation_records_failure_trace_when_llm_fails():
    memory = FakeTraceMemoryService()
    manager = AgentManager(memory_service=memory)
    manager._llm = FakeLLM(RuntimeError("provider unavailable"))
    manager.get_agent = AsyncMock(
        return_value={
            "id": "ops_agent",
            "role_family": "operations",
            "role_name": "Operations Manager",
            "instructions": "Run company operations.",
            "tools": [],
            "memory_namespace": "company:acme:ops",
            "approval_policy": "auto",
            "status": "active",
            "config": {},
        }
    )
    manager._check_approval_policy = AsyncMock(return_value=False)
    manager._maybe_report_autonomous_role_gap = AsyncMock()

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await manager.invoke_agent("ops_agent", "Prepare the launch brief.")

    assert memory.entries == []
    assert memory.traces[0]["recalled_memory_ids"] == ["memory-1", "memory-company"]
    assert memory.traces[0]["written_memory_ids"] == []
    assert memory.traces[0]["write_count"] == 0
    assert memory.traces[0]["metadata"]["memory_coverage"] == "hit"
    assert memory.traces[0]["metadata"]["result_excerpt"] == ""
    assert memory.traces[0]["errors"] == [
        "invoke:RuntimeError:provider unavailable",
    ]
    manager._maybe_report_autonomous_role_gap.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_blocked_language_reports_autonomous_role_gap():
    manager = AgentManager()
    manager._llm = FakeLLM(
        "I cannot proceed because a legal specialist role is missing for contract review."
    )
    manager.report_role_gap = AsyncMock(return_value={"id": "gap_legal"})

    response = await manager.chat(None, "Review this customer contract.", "conversation-1")

    assert response["agent_id"] == "supervisor"
    report_data = manager.report_role_gap.await_args.args[0]
    assert report_data.source_agent_id == "supervisor"
    assert report_data.capability == "legal"
    assert report_data.context["trigger"] == "chat"
    assert report_data.context["conversation_id"] == "conversation-1"


@pytest.mark.asyncio
async def test_apply_role_gap_requests_approval_for_high_risk_generated_tools():
    manager = AgentManager(tool_registry=FakeToolRegistry({"make_call", "memory_recall"}))
    gap = role_gap_with_proposal(["make_call", "memory_recall"])
    manager.get_role_gap = AsyncMock(return_value=gap)
    manager._latest_role_gap_tool_grant_approval = AsyncMock(return_value=None)
    manager._request_role_gap_tool_grant_approval = AsyncMock(return_value="approval-1")
    manager._mark_role_gap_approval_required = AsyncMock(
        return_value={
            **gap,
            "resolution": {
                "approval_required": True,
                "pending_approval_id": "approval-1",
                "high_risk_tools": ["make_call"],
            },
        }
    )
    manager.create_role_manifest = AsyncMock()
    manager.instantiate_role = AsyncMock()

    result = await manager.apply_role_gap_proposal(
        "gap_123",
        {"name": "Acme"},
        requested_by="owner@example.com",
    )

    assert result["approval_required"] is True
    assert result["approval_id"] == "approval-1"
    assert result["high_risk_tools"] == ["make_call"]
    manager.create_role_manifest.assert_not_called()
    manager.instantiate_role.assert_not_called()


@pytest.mark.asyncio
async def test_apply_role_gap_consumes_approved_tool_grant_before_creating_role():
    manager = AgentManager(tool_registry=FakeToolRegistry({"make_call", "memory_recall"}))
    gap = role_gap_with_proposal(["make_call", "memory_recall"])
    manifest = {
        "id": "outbound_calling_specialist",
        "family": "communications",
        "name": "Outbound Calling Specialist",
        "default_tools": ["make_call", "memory_recall"],
        "memory_namespace": "company:acme:gap:outbound_calling_specialist",
        "approval_policy": "sensitive",
        "config": {},
    }
    agent = {
        "id": "outbound_calling_specialist",
        "role_name": "Outbound Calling Specialist",
    }
    manager.get_role_gap = AsyncMock(return_value=gap)
    manager._latest_role_gap_tool_grant_approval = AsyncMock(
        return_value={"approval_id": "approval-1", "state": "approved"}
    )
    manager._validate_role_gap_tool_grant_approval = AsyncMock()
    manager.consume_approval = AsyncMock()
    manager.get_role_manifest = AsyncMock(return_value=None)
    manager.create_role_manifest = AsyncMock(return_value=manifest)
    manager.instantiate_role = AsyncMock(return_value=agent)
    manager._mark_role_gap_resolved = AsyncMock(
        return_value={
            **gap,
            "status": "resolved",
            "resolution": {
                "approval_id": "approval-1",
                "approved_high_risk_tools": ["make_call"],
            },
        }
    )

    result = await manager.apply_role_gap_proposal("gap_123", {"name": "Acme"})

    assert result["status"] == "resolved"
    manager._validate_role_gap_tool_grant_approval.assert_awaited_once()
    manager.consume_approval.assert_awaited_once_with(
        "approval-1",
        consumer="role_gap.apply",
        target_type="role_gap",
        target_id="gap_123",
    )
    manager.create_role_manifest.assert_awaited_once()
    manager.instantiate_role.assert_awaited_once_with(
        "outbound_calling_specialist",
        {
            "name": "Acme",
            "provisioned_by": "role_gap_loop",
            "role_gap_id": "gap_123",
            "role_gap_title": "Need outbound calls",
            "company_namespace": "company:acme",
        },
    )


@pytest.mark.asyncio
async def test_agent_manager_company_builder_creates_dynamic_manifests_and_memory():
    tool_names = {
        "agent_invoke",
        "agent_status_read",
        "approval_request",
        "approval_resolve",
        "call_receive",
        "company_profile_read",
        "document_index",
        "email_read",
        "knowledge_query",
        "make_call",
        "memory_recall",
        "memory_remember",
        "message_read",
        "owner_notify",
        "process_audit",
        "role_catalog_search",
        "role_instantiate",
        "send_email",
        "send_message",
        "send_sms",
        "sms_read",
    }
    memory = FakeMemoryService()
    manager = AgentManager(memory_service=memory, tool_registry=FakeToolRegistry(tool_names))
    manifests = []
    agents = {}

    async def list_role_manifests():
        return list(manifests)

    async def get_role_manifest(manifest_id: str):
        return next((manifest for manifest in manifests if manifest["id"] == manifest_id), None)

    async def create_role_manifest(data):
        manifest = {
            "id": slug_id(data.name),
            "family": data.family,
            "name": data.name,
            "description": data.description,
            "instructions_template": data.instructions_template,
            "default_tools": data.default_tools,
            "memory_namespace": data.memory_namespace,
            "approval_policy": data.approval_policy,
            "success_metrics": data.success_metrics,
            "is_core": data.is_core,
            "config": data.config,
        }
        manifests.append(manifest)
        return manifest

    async def get_agent(agent_id: str):
        return agents.get(agent_id)

    async def create_agent(data):
        agent_id = slug_id(data.role_name)
        tools, unsupported_tools = manager._resolve_tool_names(data.tools)
        config = dict(data.config)
        if unsupported_tools:
            config["unsupported_tools"] = unsupported_tools
        agent = {
            "id": agent_id,
            "role_family": data.role_family,
            "role_name": data.role_name,
            "instructions": data.instructions,
            "tools": tools,
            "memory_namespace": data.memory_namespace,
            "approval_policy": data.approval_policy,
            "status": "active",
            "config": config,
        }
        agents[agent_id] = agent
        return agent

    manager.list_role_manifests = list_role_manifests
    manager.get_role_manifest = get_role_manifest
    manager.create_role_manifest = create_role_manifest
    manager.get_agent = get_agent
    manager.create_agent = create_agent

    result = await manager.run_company_builder(
        {
            "name": "VoiceOps AI",
            "industry": "AI SaaS",
            "target_customers": "B2B clients",
            "channels": "phone, SMS, email, CRM",
            "goals": "launch and acquire clients",
        }
    )

    generated_names = {manifest["name"] for manifest in manifests}
    instantiated_names = {agent["role_name"] for agent in result["instantiated_agents"]}

    assert "Company Memory Steward" in generated_names
    assert "Outbound Calling Specialist" in generated_names
    assert "Company Memory Steward" in instantiated_names
    assert "Outbound Calling Specialist" in instantiated_names
    assert result["operating_model"]["summary"]["dynamic_role_count"] >= 2
    assert result["blueprint"]["adaptive_loops"]
    assert len(memory.entries) == len(result["memory_seed"])
    assert memory.entries[0]["metadata"]["source"] == "company_builder"
