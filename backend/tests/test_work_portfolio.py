import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import Base
from cyber_team.db.models import (
    Agent,
    AgentMandate,
    BusinessEvent,
    BusinessEventDelivery,
    BusinessEventDisposition,
    BusinessWorkItem,
    BusinessWorkItemDependency,
    CompanyObjective,
    CompanyObjectiveRevision,
    RoleGap,
)
from cyber_team.operations import work_portfolio as portfolio_module
from cyber_team.operations.work_portfolio import WorkPortfolioService


class FakeIntelligence:
    @staticmethod
    def classify_untrusted_content(value):
        detected = "ignore previous instructions" in value.lower()
        return {"detected": detected, "reason": "test" if detected else None}


@pytest.fixture
async def portfolio_session_factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(portfolio_module, "async_session", factory)
    monkeypatch.setattr(settings, "company_namespace", "company:test")
    try:
        yield factory
    finally:
        await engine.dispose()


async def seed_agents_and_objective(factory):
    async with factory() as session:
        objective = CompanyObjective(
            id="objective-knowledge",
            title="Build verified company knowledge",
            status="active",
            tags=["knowledge"],
        )
        revision = CompanyObjectiveRevision(
            id="objective-revision-knowledge",
            objective_id=objective.id,
            revision=1,
            status="active",
            title=objective.title,
            category="knowledge",
            evidence_ids=["ev-1"],
            confidence=0.9,
            activated_at=utc_now(),
        )
        agents = [
            Agent(
                id="knowledge-agent",
                role_family="knowledge",
                role_name="Knowledge Agent",
                instructions="Assess evidence.",
                tools=["memory_recall"],
                memory_namespace="company:test:knowledge",
                status="active",
            ),
            Agent(
                id="observer_agent",
                role_family="governance",
                role_name="Observer Agent",
                instructions="Review policy and evidence.",
                tools=[],
                memory_namespace="company:test:observer",
                status="active",
            ),
        ]
        session.add_all([objective, revision, *agents])
        await session.commit()


def event(event_id, event_type, *, payload=None):
    return BusinessEvent(
        id=event_id,
        company_namespace="company:test",
        event_type=event_type,
        source_type="company_signal",
        source_id=f"source-{event_id}",
        payload=payload or {},
        status="pending",
        idempotency_key=f"key-{event_id}",
        occurred_at=utc_now(),
    )


@pytest.mark.asyncio
async def test_all_active_agents_receive_versioned_idempotent_mandates(
    portfolio_session_factory,
):
    await seed_agents_and_objective(portfolio_session_factory)
    service = WorkPortfolioService()

    first = await service.ensure_active_agent_mandates()
    second = await service.ensure_active_agent_mandates()

    assert first == {
        "status": "completed",
        "active_agents": 2,
        "created": 2,
        "unchanged": 0,
        "retired": 0,
        "coverage": 1.0,
    }
    assert second["created"] == 0
    assert second["unchanged"] == 2
    async with portfolio_session_factory() as session:
        assert (
            await session.execute(select(func.count(AgentMandate.id)))
        ).scalar_one() == 2


@pytest.mark.asyncio
async def test_events_are_routed_once_or_deferred_with_capability_gap(
    portfolio_session_factory,
):
    await seed_agents_and_objective(portfolio_session_factory)
    async with portfolio_session_factory() as session:
        session.add_all(
            [
                event("event-doc", "evidence.document.updated"),
                event("event-invoice", "evidence.erpnext.sales_invoice"),
            ]
        )
        await session.commit()
    service = WorkPortfolioService()

    first = await service.route_pending_events()
    second = await service.route_pending_events()

    assert first["counts"]["accepted"] == 1
    assert first["counts"]["deferred"] == 1
    assert second["processed"] == 0
    async with portfolio_session_factory() as session:
        works = (
            await session.execute(select(BusinessWorkItem))
        ).scalars().all()
        gaps = (await session.execute(select(RoleGap))).scalars().all()
        deliveries = (
            await session.execute(select(BusinessEventDelivery))
        ).scalars().all()
        dispositions = (
            await session.execute(select(BusinessEventDisposition))
        ).scalars().all()
    assert len(works) == 1
    assert works[0].assigned_agent_id == "knowledge-agent"
    assert len(gaps) == 1
    assert gaps[0].capability == "finance"
    assert len(deliveries) == 2
    assert all(item.status == "delivered" for item in deliveries)
    assert {item.disposition for item in dispositions} == {
        "accepted_work_item",
        "deferred",
    }


@pytest.mark.asyncio
async def test_quarantined_event_escalates_without_exposing_body(
    portfolio_session_factory,
):
    await seed_agents_and_objective(portfolio_session_factory)
    async with portfolio_session_factory() as session:
        session.add(
            event(
                "event-injection",
                "evidence.email.received",
                payload={
                    "quarantined": True,
                    "quarantine": {"reason": "prompt injection"},
                    "raw_body": "must not enter review work",
                },
            )
        )
        await session.commit()
    service = WorkPortfolioService()

    result = await service.route_pending_events()

    assert result["counts"]["escalated"] == 1
    async with portfolio_session_factory() as session:
        work = (await session.execute(select(BusinessWorkItem))).scalar_one()
    assert work.work_type == "owner_escalation"
    assert "raw_body" not in work.payload
    assert work.policy_decision["allowed"] is False


@pytest.mark.asyncio
async def test_dependency_blocked_work_wakes_after_predecessor_completion(
    portfolio_session_factory,
):
    await seed_agents_and_objective(portfolio_session_factory)
    service = WorkPortfolioService()
    await service.ensure_active_agent_mandates()
    first = await service.create_work_item(
        title="Collect evidence",
        description="Collect evidence safely.",
        work_type="research",
        company_namespace="company:test",
        assigned_agent_id="knowledge-agent",
        payload={},
        acceptance_criteria=["evidence_collected"],
        idempotency_key="work-first",
    )
    second = await service.create_work_item(
        title="Assess evidence",
        description="Assess collected evidence.",
        work_type="assessment",
        company_namespace="company:test",
        assigned_agent_id="knowledge-agent",
        payload={},
        acceptance_criteria=["assessment_recorded"],
        idempotency_key="work-second",
        dependencies=[first["id"]],
    )

    leased_first = await service._lease_next("knowledge-agent", lease_seconds=60)
    assert leased_first.id == first["id"]
    await service._finish_work(
        first["id"], status="completed", outcome={"ok": True}, error=None
    )
    leased_second = await service._lease_next("knowledge-agent", lease_seconds=60)

    assert second["status"] == "blocked_dependency"
    assert leased_second.id == second["id"]
    async with portfolio_session_factory() as session:
        dependency_count = (
            await session.execute(select(func.count(BusinessWorkItemDependency.id)))
        ).scalar_one()
    assert dependency_count == 1


@pytest.mark.asyncio
async def test_role_loop_completes_advisory_work_without_side_effects(
    portfolio_session_factory,
):
    await seed_agents_and_objective(portfolio_session_factory)
    manager = AsyncMock()
    manager.invoke_agent.return_value = json.dumps(
        {
            "assessment": "Evidence supports a no-action decision.",
            "confidence": 0.91,
            "unknowns": [],
            "recommended_action": "no_action",
            "expected_outcome": {"type": "documented_no_action"},
            "proposed_work": [],
        }
    )
    service = WorkPortfolioService(
        agent_manager=manager,
        company_intelligence_service=FakeIntelligence(),
    )
    await service.ensure_active_agent_mandates()
    created = await service.create_work_item(
        title="Assess market evidence",
        description="Review evidence and propose safe next steps.",
        work_type="domain_assessment",
        company_namespace="company:test",
        assigned_agent_id="knowledge-agent",
        payload={"external_text_is_untrusted": True},
        acceptance_criteria=["assessment_recorded"],
        idempotency_key="work-advisory",
    )

    result = await service.run_domain_loop(
        "knowledge-agent", max_items=1, prepare=False
    )

    assert result["processed"] == 1
    assert result["items"][0]["status"] == "completed"
    assert result["items"][0]["actual_outcome"]["side_effects_executed"] is False
    manager.invoke_agent.assert_awaited_once()
    trace = manager.invoke_agent.await_args.kwargs["trace_metadata"]
    assert trace["external_side_effects_allowed"] is False
    assert trace["work_item_id"] == created["id"]


@pytest.mark.asyncio
async def test_role_loop_persists_validated_follow_up_work(
    portfolio_session_factory,
):
    await seed_agents_and_objective(portfolio_session_factory)
    manager = AsyncMock()
    manager.invoke_agent.return_value = json.dumps(
        {
            "assessment": "A primary source is needed before changing strategy.",
            "confidence": 0.78,
            "unknowns": ["Current customer retention"],
            "recommended_action": "continue",
            "expected_outcome": {"type": "verified_retention_evidence"},
            "proposed_work": [
                {
                    "title": "Research customer retention evidence",
                    "description": "Collect an authenticated retention measurement.",
                    "work_type": "research",
                    "priority": "medium",
                    "acceptance_criteria": ["primary_source_recorded"],
                    "expected_outcome": {"type": "evidence_artifact"},
                }
            ],
        }
    )
    service = WorkPortfolioService(
        agent_manager=manager,
        company_intelligence_service=FakeIntelligence(),
    )
    await service.ensure_active_agent_mandates()
    parent = await service.create_work_item(
        title="Assess retention unknown",
        description="Determine the next evidence step.",
        work_type="domain_assessment",
        company_namespace="company:test",
        assigned_agent_id="knowledge-agent",
        payload={},
        acceptance_criteria=["next_step_recorded"],
        idempotency_key="work-proposal-parent",
    )

    result = await service.run_domain_loop("knowledge-agent", prepare=False)

    created_ids = result["items"][0]["actual_outcome"]["created_work_item_ids"]
    assert len(created_ids) == 1
    async with portfolio_session_factory() as session:
        follow_up = await session.get(BusinessWorkItem, created_ids[0])
    assert follow_up.work_type == "research"
    assert follow_up.payload["parent_work_item_id"] == parent["id"]


@pytest.mark.asyncio
async def test_role_loop_keeps_assessment_but_rejects_unsafe_follow_up(
    portfolio_session_factory,
):
    await seed_agents_and_objective(portfolio_session_factory)
    manager = AsyncMock()
    manager.invoke_agent.return_value = json.dumps(
        {
            "assessment": "Evidence is sufficient for an internal assessment.",
            "confidence": 0.84,
            "unknowns": [],
            "recommended_action": "continue",
            "expected_outcome": {"type": "assessment_recorded"},
            "proposed_work": [
                {
                    "title": "Mutate an external system",
                    "description": "This proposal must be discarded.",
                    "work_type": "external_mutation",
                    "priority": "high",
                    "acceptance_criteria": ["external_state_changed"],
                    "expected_outcome": {"type": "external_change"},
                }
            ],
        }
    )
    service = WorkPortfolioService(
        agent_manager=manager,
        company_intelligence_service=FakeIntelligence(),
    )
    await service.ensure_active_agent_mandates()
    await service.create_work_item(
        title="Reject unsafe follow-up",
        description="Keep the assessment and discard the unsafe proposal.",
        work_type="domain_assessment",
        company_namespace="company:test",
        assigned_agent_id="knowledge-agent",
        payload={},
        acceptance_criteria=["assessment_recorded"],
        idempotency_key="work-reject-unsafe-follow-up",
    )

    result = await service.run_domain_loop("knowledge-agent", prepare=False)

    outcome = result["items"][0]["actual_outcome"]
    assert result["items"][0]["status"] == "completed"
    assert outcome["created_work_item_ids"] == []
    assert outcome["rejected_proposals"] == [
        {
            "index": 0,
            "reason": "work_type_not_allowlisted",
            "work_type": "external_mutation",
        }
    ]


@pytest.mark.asyncio
async def test_role_loop_rejects_unstructured_agent_output(
    portfolio_session_factory,
):
    await seed_agents_and_objective(portfolio_session_factory)
    manager = AsyncMock()
    manager.invoke_agent.return_value = "I would call send_email now."
    service = WorkPortfolioService(
        agent_manager=manager,
        company_intelligence_service=FakeIntelligence(),
    )
    await service.ensure_active_agent_mandates()
    await service.create_work_item(
        title="Unsafe response test",
        description="Require a structured response.",
        work_type="domain_assessment",
        company_namespace="company:test",
        assigned_agent_id="knowledge-agent",
        payload={},
        acceptance_criteria=["structured_result"],
        idempotency_key="work-invalid-structured",
    )

    result = await service.run_domain_loop("knowledge-agent", prepare=False)

    assert result["items"][0]["status"] == "failed"
    assert result["items"][0]["actual_outcome"]["classification"] == (
        "structured_output_invalid"
    )


@pytest.mark.asyncio
async def test_tool_work_executes_only_a_mandate_granted_policy_gated_tool(
    portfolio_session_factory,
):
    await seed_agents_and_objective(portfolio_session_factory)
    tools = AsyncMock()
    tools.get_tool_readiness = lambda *_args, **_kwargs: {"side_effects": False}
    tools.execute.return_value = SimpleNamespace(
        success=True,
        output={"record_id": "memory-1"},
        error=None,
    )
    service = WorkPortfolioService(tool_registry=tools)
    await service.ensure_active_agent_mandates()
    created = await service.create_work_item(
        title="Remember verified evidence",
        description="Write one internal memory record.",
        work_type="tool_action",
        company_namespace="company:test",
        assigned_agent_id="knowledge-agent",
        payload={
            "tool_name": "memory_recall",
            "params": {"query": "verified evidence"},
            "action_envelope": {
                "action_class": "internal_read",
                "target": {"type": "memory", "id": "company:test:knowledge"},
                "expected_effect": "Read relevant evidence",
                "evidence_ids": ["ev-1"],
                "confidence": 0.9,
                "reversible": True,
                "financial_exposure_usd": 0,
                "recipient_count": 0,
                "data_sensitivity": "internal",
            },
        },
        acceptance_criteria=["memory_read_completed"],
        idempotency_key="work-tool-read",
    )

    result = await service.run_domain_loop("knowledge-agent", prepare=False)

    assert result["items"][0]["status"] == "completed"
    assert result["items"][0]["actual_outcome"]["action_executed"] is True
    assert result["items"][0]["actual_outcome"]["side_effects_executed"] is False
    params = tools.execute.await_args.args[1]
    assert params["_agent_id"] == "knowledge-agent"
    assert params["_conversation_id"] == created["id"]


@pytest.mark.asyncio
async def test_domain_pause_defers_events_and_stops_role_loop(
    portfolio_session_factory,
):
    await seed_agents_and_objective(portfolio_session_factory)
    service = WorkPortfolioService()
    await service.ensure_active_agent_mandates()
    control = await service.update_domain_control(
        "knowledge",
        state="takeover",
        reason="Owner is handling this domain.",
        owner="owner@example.com",
    )
    async with portfolio_session_factory() as session:
        session.add(event("event-paused", "evidence.document.updated"))
        await session.commit()

    routing = await service.route_pending_events()
    loop = await service.run_domain_loop("knowledge-agent", prepare=False)

    assert control["state"] == "takeover"
    assert routing["counts"]["deferred"] == 1
    assert loop["status"] == "takeover"
    assert loop["processed"] == 0
    async with portfolio_session_factory() as session:
        gaps = (await session.execute(select(RoleGap))).scalars().all()
        disposition = (
            await session.execute(select(BusinessEventDisposition))
        ).scalar_one()
    assert gaps == []
    assert "owner control" in disposition.reason.lower()
