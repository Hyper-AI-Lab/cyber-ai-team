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
