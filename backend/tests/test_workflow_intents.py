from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.agents import orchestrator as orchestrator_module
from cyber_team.agents.manager import AgentManager
from cyber_team.agents.orchestrator import Orchestrator
from cyber_team.db import Base
from cyber_team.db.models import Agent, CompanyContextSnapshot, RoleGap, Workflow, WorkflowIntent
from cyber_team.workflows.intents import WorkflowIntentService


class FakeToolRegistry:
    def __init__(self, live_tools: set[str] | None = None):
        self.live_tools = live_tools or {"memory_recall", "memory_remember"}

    def get_tool_readiness(self, tool_name: str):
        side_effect_tools = {
            "approval_resolve",
            "call_make",
            "crm_contact_update",
            "crm_deal_update",
            "crm_lead_create",
            "email_send",
            "make_call",
            "message_send",
            "send_email",
            "send_message",
            "send_sms",
            "sms_send",
            "task_create",
            "task_update",
            "ticket_create",
            "ticket_update",
        }
        if tool_name in self.live_tools:
            return {
                "state": "live",
                "readiness_reason": "Configured for test.",
                "side_effects": tool_name in side_effect_tools,
                "executor_kind": "live",
                "requires_configuration": False,
                "executable": True,
            }
        return {
            "state": "configuration_required",
            "readiness_reason": f"{tool_name} requires configuration.",
            "side_effects": tool_name in side_effect_tools,
            "executor_kind": "configuration_required",
            "requires_configuration": True,
            "executable": False,
        }


class FakeLLMGateway:
    def __init__(self, mode: str = "live"):
        self.mode = mode

    async def validate_provider(self, *, force: bool = False):
        return {
            "provider": "mistral",
            "configured": self.mode != "configuration_required",
            "mode": self.mode,
            "status": self.mode,
            "blocking": self.mode != "live",
            "detail": "LLM validation result for test.",
        }


@pytest.fixture
async def session_factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(orchestrator_module, "async_session", factory)
    try:
        yield factory
    finally:
        await engine.dispose()


def snapshot() -> CompanyContextSnapshot:
    return CompanyContextSnapshot(
        id="ctx_1",
        source="erpnext",
        source_hash="hash-1",
        company_namespace="company:acme",
        status="active",
        normalized_profile={"company_name": "Acme"},
        erpnext_summary={"counts": {"Task": 2}},
        operating_model={
            "planned_role_specs": [
                {
                    "family": "sales",
                    "name": "Sales Manager",
                    "description": "Owns revenue operations.",
                    "default_tools": ["memory_recall", "send_email"],
                    "memory_namespace": "company:acme:sales",
                    "approval_policy": "sensitive",
                    "capabilities": ["lead_follow_up"],
                    "rationale": ["ERPNext has open leads."],
                    "activation_triggers": ["lead_created"],
                }
            ],
            "adaptive_loops": [
                {
                    "id": "customer_communication_loop",
                    "owner_family": "sales",
                    "purpose": "Review customer communications.",
                    "trigger": "new lead or stale opportunity",
                    "approval_boundary": "External messages require approval.",
                }
            ],
        },
        created_by="test",
        created_at=datetime(2026, 7, 21, 10, 0, 0),
    )


def sales_agent() -> Agent:
    return Agent(
        id="sales_agent",
        role_family="sales",
        role_name="Sales Manager",
        instructions="You manage sales safely.",
        tools=["memory_recall", "send_email"],
        memory_namespace="company:acme:sales",
        approval_policy="auto",
        status="active",
        config={},
        created_at=datetime(2026, 7, 21, 10, 0, 0),
        updated_at=datetime(2026, 7, 21, 10, 0, 0),
    )


def company_builder_agent() -> Agent:
    return Agent(
        id="company_builder",
        role_family="company_builder",
        role_name="Company Builder",
        instructions="Design safe company roles.",
        tools=["memory_recall", "memory_remember"],
        memory_namespace="company:acme:company_builder",
        approval_policy="auto",
        status="active",
        config={},
        created_at=datetime(2026, 7, 21, 10, 0, 0),
        updated_at=datetime(2026, 7, 21, 10, 0, 0),
    )


def misfamily_company_builder_agent() -> Agent:
    return Agent(
        id="review_erpnext_derived_role_company_builder_specialist",
        role_family="marketing",
        role_name="Review ERPNext-derived role: Company Builder Specialist",
        instructions="Safely review company-builder role gaps.",
        tools=["role_catalog_search", "company_profile_read", "memory_recall"],
        memory_namespace="company:acme:company_builder",
        approval_policy="auto",
        status="active",
        config={},
        created_at=datetime(2026, 7, 21, 10, 0, 0),
        updated_at=datetime(2026, 7, 21, 10, 0, 0),
    )


def misfamily_supervisor_agent() -> Agent:
    return Agent(
        id="review_erpnext_derived_role_supervisor_orchestrator_specialist",
        role_family="marketing",
        role_name="Review ERPNext-derived role: Supervisor / Orchestrator Specialist",
        instructions="Safely review supervisor operating loops.",
        tools=["agent_status_read", "agent_invoke", "memory_recall", "owner_notify"],
        memory_namespace="company:acme:supervisor",
        approval_policy="auto",
        status="active",
        config={},
        created_at=datetime(2026, 7, 21, 10, 0, 0),
        updated_at=datetime(2026, 7, 21, 10, 0, 0),
    )


@pytest.mark.asyncio
async def test_generate_workflow_intents_from_context_is_idempotent(session_factory):
    async with session_factory() as session:
        session.add(snapshot())
        session.add(sales_agent())
        session.add(
            RoleGap(
                id="gap_1",
                title="Need outbound calling",
                description="Follow-up requires a calling workflow.",
                status="open",
                severity="high",
                source_type="company_context_snapshot",
                company_namespace="company:acme",
                capability="outbound_voice",
                requested_tools=["make_call"],
                context={"source_hash": "hash-1"},
                proposed_role={},
                resolution={},
                created_at=datetime(2026, 7, 21, 10, 1, 0),
                updated_at=datetime(2026, 7, 21, 10, 1, 0),
            )
        )
        await session.commit()

    service = WorkflowIntentService(
        orchestrator=Orchestrator(agent_manager=AgentManager(), memory_service=None),
        tool_registry=FakeToolRegistry(
            live_tools={"memory_recall", "memory_remember", "send_email"},
        ),
        llm_gateway=FakeLLMGateway(),
        session_factory=session_factory,
    )

    first = await service.generate_from_company_context(actor="test")
    second = await service.generate_from_company_context(actor="test")

    assert first["status"] == "completed"
    assert first["created"] == 3
    assert second["created"] == 0
    assert second["unchanged"] == 3
    assert {item["category"] for item in first["intents"]} == {
        "adaptive_loop",
        "role_capability",
        "role_gap",
    }
    role_intent = next(item for item in first["intents"] if item["category"] == "role_capability")
    assert role_intent["readiness"]["status"] == "owner_review"
    assert role_intent["approval_required"] is True

    async with session_factory() as session:
        rows = (await session.execute(select(WorkflowIntent))).scalars().all()
        assert len(rows) == 3


@pytest.mark.asyncio
async def test_role_gap_intent_keeps_memory_steward_in_knowledge_family(session_factory):
    ctx = snapshot()
    ctx.operating_model = {"planned_role_specs": [], "adaptive_loops": []}
    async with session_factory() as session:
        session.add(ctx)
        session.add(company_builder_agent())
        session.add(
            RoleGap(
                id="gap_memory",
                title="Memory remediation: Company Memory Steward",
                description=(
                    "Repeated empty recall and stale namespace coverage require a "
                    "knowledge stewardship follow-up, not customer communications."
                ),
                status="proposed",
                severity="medium",
                source_type="memory_steward",
                company_namespace="company:acme",
                capability="memory_operations",
                requested_tools=[
                    "send_email",
                    "send_sms",
                    "make_call",
                    "send_message",
                    "memory_remember",
                    "knowledge_query",
                ],
                context={
                    "role_family": "knowledge",
                    "source_hash": "hash-1",
                },
                proposed_role={
                    "manifest_payload": {
                        "family": "communications",
                        "name": "Unsafe Communications Steward",
                        "default_tools": [
                            "send_email",
                            "send_sms",
                            "make_call",
                            "send_message",
                        ],
                    }
                },
                resolution={},
                created_at=datetime(2026, 7, 21, 10, 1, 0),
                updated_at=datetime(2026, 7, 21, 10, 1, 0),
            )
        )
        await session.commit()

    service = WorkflowIntentService(
        orchestrator=Orchestrator(agent_manager=AgentManager(), memory_service=None),
        tool_registry=FakeToolRegistry(
            live_tools={"memory_recall", "memory_remember", "knowledge_query"},
        ),
        llm_gateway=FakeLLMGateway(),
        session_factory=session_factory,
    )

    generated = await service.generate_from_company_context(actor="test")

    intent = generated["intents"][0]
    assert intent["category"] == "role_gap"
    assert intent["business_function"] == "Knowledge"
    assert intent["role_family"] == "knowledge"
    assert intent["role_name"] == "Memory remediation: Company Memory Steward"
    assert intent["requested_tools"] == ["memory_remember", "knowledge_query"]
    assert intent["approval_required"] is False
    assert intent["risk_level"] == "low"
    assert intent["evidence"]["role_gap"]["excluded_unsafe_requested_tools"] == [
        "send_email",
        "send_sms",
        "make_call",
        "send_message",
    ]


@pytest.mark.asyncio
async def test_ready_low_risk_intent_instantiates_workflow(session_factory):
    ctx = snapshot()
    ctx.operating_model["planned_role_specs"][0]["default_tools"] = ["memory_recall"]
    ctx.operating_model["planned_role_specs"][0]["approval_policy"] = "auto"
    async with session_factory() as session:
        session.add(ctx)
        session.add(sales_agent())
        await session.commit()

    orchestrator = Orchestrator(agent_manager=AgentManager(), memory_service=None)
    service = WorkflowIntentService(
        orchestrator=orchestrator,
        tool_registry=FakeToolRegistry(),
        llm_gateway=FakeLLMGateway(),
        session_factory=session_factory,
    )

    generated = await service.generate_from_company_context(actor="test")
    ready = next(item for item in generated["intents"] if item["category"] == "role_capability")
    assert ready["readiness"]["status"] == "ready"

    workflow = await service.instantiate_intent(ready["id"], actor="owner@example.com")

    assert workflow["name"] == "Sales Manager operating loop"
    assert workflow["trigger_config"]["workflow_intent_id"] == ready["id"]
    async with session_factory() as session:
        intent = await session.get(WorkflowIntent, ready["id"])
        workflows = (await session.execute(select(Workflow))).scalars().all()
        assert intent is not None
        assert intent.status == "instantiated"
        assert intent.workflow_id == workflow["id"]
        assert len(workflows) == 1


@pytest.mark.asyncio
async def test_intent_blocks_when_required_agent_is_missing(session_factory):
    async with session_factory() as session:
        session.add(snapshot())
        await session.commit()

    service = WorkflowIntentService(
        orchestrator=Orchestrator(agent_manager=AgentManager(), memory_service=None),
        tool_registry=FakeToolRegistry(
            live_tools={"memory_recall", "memory_remember", "send_email"},
        ),
        llm_gateway=FakeLLMGateway(),
        session_factory=session_factory,
    )

    generated = await service.generate_from_company_context(actor="test")
    blocked = next(item for item in generated["intents"] if item["category"] == "role_capability")

    assert blocked["readiness"]["status"] == "blocked"
    assert "No active agent" in blocked["readiness"]["blockers"][0]
    with pytest.raises(ValueError, match="not ready"):
        await service.instantiate_intent(blocked["id"], actor="owner@example.com")


@pytest.mark.asyncio
async def test_intent_requires_live_llm_provider_for_agent_delegation(session_factory):
    async with session_factory() as session:
        session.add(snapshot())
        session.add(sales_agent())
        await session.commit()

    service = WorkflowIntentService(
        orchestrator=Orchestrator(agent_manager=AgentManager(), memory_service=None),
        tool_registry=FakeToolRegistry(
            live_tools={"memory_recall", "memory_remember", "send_email"},
        ),
        llm_gateway=FakeLLMGateway(mode="configuration_required"),
        session_factory=session_factory,
    )

    generated = await service.generate_from_company_context(actor="test")
    intent = next(item for item in generated["intents"] if item["category"] == "role_capability")

    assert intent["readiness"]["status"] == "configuration_required"
    assert intent["readiness"]["recommended_action"] == "validate_llm_provider"
    assert intent["readiness"]["llm_provider"]["mode"] == "configuration_required"
    with pytest.raises(ValueError, match="not ready"):
        await service.instantiate_intent(intent["id"], actor="owner@example.com")


@pytest.mark.asyncio
async def test_core_agent_name_hints_unblock_existing_builder_and_supervisor_agents(
    session_factory,
):
    ctx = snapshot()
    ctx.operating_model = {
        "planned_role_specs": [],
        "adaptive_loops": [
            {
                "id": "risk_review_loop",
                "owner_family": "supervisor",
                "purpose": "Review operational risks.",
            }
        ],
    }
    async with session_factory() as session:
        session.add(ctx)
        session.add(misfamily_company_builder_agent())
        session.add(misfamily_supervisor_agent())
        session.add(
            RoleGap(
                id="gap_sales",
                title="Review ERPNext-derived role: Sales & CRM Agent",
                description="Sales role needs owner review.",
                status="proposed",
                severity="medium",
                source_type="company_context_snapshot",
                company_namespace="company:acme",
                capability="crm",
                requested_tools=["memory_recall"],
                context={"source_hash": "hash-1"},
                proposed_role={},
                resolution={},
                created_at=datetime(2026, 7, 21, 10, 1, 0),
                updated_at=datetime(2026, 7, 21, 10, 1, 0),
            )
        )
        await session.commit()

    service = WorkflowIntentService(
        orchestrator=Orchestrator(agent_manager=AgentManager(), memory_service=None),
        tool_registry=FakeToolRegistry(),
        llm_gateway=FakeLLMGateway(),
        session_factory=session_factory,
    )

    generated = await service.generate_from_company_context(actor="test")
    role_gap = next(item for item in generated["intents"] if item["category"] == "role_gap")
    supervisor_loop = next(
        item for item in generated["intents"] if item["category"] == "adaptive_loop"
    )

    assert role_gap["readiness"]["status"] == "ready"
    assert role_gap["readiness"]["agent"]["id"] == (
        "review_erpnext_derived_role_company_builder_specialist"
    )
    assert supervisor_loop["readiness"]["status"] == "ready"
    assert supervisor_loop["readiness"]["agent"]["id"] == (
        "review_erpnext_derived_role_supervisor_orchestrator_specialist"
    )


@pytest.mark.asyncio
async def test_optional_provider_tools_are_not_configuration_blockers(session_factory):
    ctx = snapshot()
    ctx.operating_model["planned_role_specs"][0]["default_tools"] = [
        "memory_recall",
        "sms_send",
        "call_make",
        "message_send",
    ]
    ctx.operating_model["planned_role_specs"][0]["approval_policy"] = "auto"
    async with session_factory() as session:
        session.add(ctx)
        session.add(sales_agent())
        await session.commit()

    service = WorkflowIntentService(
        orchestrator=Orchestrator(agent_manager=AgentManager(), memory_service=None),
        tool_registry=FakeToolRegistry(live_tools={"memory_recall", "memory_remember"}),
        llm_gateway=FakeLLMGateway(),
        session_factory=session_factory,
    )

    generated = await service.generate_from_company_context(actor="test")
    intent = next(item for item in generated["intents"] if item["category"] == "role_capability")

    assert intent["readiness"]["status"] == "owner_review"
    assert intent["readiness"]["recommended_action"] == "review_optional_providers"
    assert intent["approval_required"] is True
    assert intent["risk_level"] == "high"
    assert {
        item["tool_name"] for item in intent["readiness"]["optional_disabled_tools"]
    } == {"sms_send", "call_make", "message_send"}
    assert intent["readiness"]["configuration_required_tools"] == []
    assert intent["readiness"]["workflow_impact_counts"] == {
        "configuration_required": 0,
        "optional_disabled": 3,
        "approval_gated": 0,
    }


@pytest.mark.asyncio
async def test_regeneration_resolves_superseded_active_intents(session_factory):
    ctx = snapshot()
    ctx.operating_model = {"planned_role_specs": [], "adaptive_loops": []}
    async with session_factory() as session:
        session.add(ctx)
        session.add(misfamily_company_builder_agent())
        session.add(
            RoleGap(
                id="gap_sales",
                title="Review ERPNext-derived role: Sales & CRM Agent",
                description="Sales role needs owner review.",
                status="proposed",
                severity="medium",
                source_type="company_context_snapshot",
                company_namespace="company:acme",
                capability="crm",
                requested_tools=["memory_recall"],
                context={"source_hash": "hash-1"},
                proposed_role={},
                resolution={},
                created_at=datetime(2026, 7, 21, 10, 1, 0),
                updated_at=datetime(2026, 7, 21, 10, 2, 0),
            )
        )
        session.add(
            WorkflowIntent(
                id="old_intent",
                title="Role gap follow-up: Review ERPNext-derived role: Sales & CRM Agent",
                description="Old stale role-gap follow-up.",
                status="proposed",
                category="role_gap",
                business_function="Sales",
                source_type="role_gap",
                source_id="gap_sales",
                source_hash="hash-1",
                company_namespace="company:acme",
                role_family="sales",
                role_name="Review ERPNext-derived role: Sales & CRM Agent",
                capability="crm",
                risk_level="medium",
                trigger_type="manual",
                trigger_config={},
                graph_definition={},
                requested_tools=["memory_recall"],
                required_agents=["company_builder"],
                tool_readiness=[],
                readiness={
                    "status": "blocked",
                    "blockers": ["No active agent is available for role family company_builder."],
                },
                approval_required=True,
                evidence={},
                resolution={},
                dedupe_key="old-dedupe",
                proposed_by="test",
                created_at=datetime(2026, 7, 21, 10, 1, 0),
                updated_at=datetime(2026, 7, 21, 10, 1, 0),
            )
        )
        await session.commit()

    service = WorkflowIntentService(
        orchestrator=Orchestrator(agent_manager=AgentManager(), memory_service=None),
        tool_registry=FakeToolRegistry(),
        llm_gateway=FakeLLMGateway(),
        session_factory=session_factory,
    )

    generated = await service.generate_from_company_context(actor="test")

    assert generated["created"] == 1
    assert generated["superseded"] == 1
    assert generated["intents"][0]["readiness"]["status"] == "ready"
    async with session_factory() as session:
        old_intent = await session.get(WorkflowIntent, "old_intent")
        active = (
            await session.execute(
                select(WorkflowIntent).where(
                    WorkflowIntent.status.in_(["proposed", "instantiated", "blocked"])
                )
            )
        ).scalars().all()
    assert old_intent is not None
    assert old_intent.status == "resolved"
    assert old_intent.resolution["reason"] == "superseded_by_regenerated_intent"
    assert len(active) == 1
