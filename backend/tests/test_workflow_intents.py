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
        if tool_name in self.live_tools:
            return {
                "state": "live",
                "readiness_reason": "Configured for test.",
                "side_effects": tool_name in {"send_email", "make_call"},
                "executor_kind": "live",
                "requires_configuration": False,
                "executable": True,
            }
        return {
            "state": "configuration_required",
            "readiness_reason": f"{tool_name} requires configuration.",
            "side_effects": tool_name in {"send_email", "make_call"},
            "executor_kind": "configuration_required",
            "requires_configuration": True,
            "executable": False,
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
        session_factory=session_factory,
    )

    generated = await service.generate_from_company_context(actor="test")
    blocked = next(item for item in generated["intents"] if item["category"] == "role_capability")

    assert blocked["readiness"]["status"] == "blocked"
    assert "No active agent" in blocked["readiness"]["blockers"][0]
    with pytest.raises(ValueError, match="not ready"):
        await service.instantiate_intent(blocked["id"], actor="owner@example.com")
