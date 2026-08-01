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
    CompanySignal,
    CompanySource,
    DomainAutonomyControl,
    MemoryEntry,
    MemoryStewardFinding,
    MemoryTrace,
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
async def test_completed_audit_event_is_documented_as_no_action(
    portfolio_session_factory,
):
    await seed_agents_and_objective(portfolio_session_factory)
    async with portfolio_session_factory() as session:
        session.add(
            event(
                "event-audit-completed",
                "evidence.audit.event",
                payload={"outcome": "completed"},
            )
        )
        await session.commit()

    result = await WorkPortfolioService().route_pending_events()

    assert result["counts"]["no_action"] == 1
    async with portfolio_session_factory() as session:
        works = (await session.execute(select(BusinessWorkItem))).scalars().all()
        disposition = (
            await session.execute(select(BusinessEventDisposition))
        ).scalar_one()
    assert works == []
    assert disposition.disposition == "no_action"


@pytest.mark.asyncio
async def test_historical_successful_audit_work_is_reconciled_but_failure_remains(
    portfolio_session_factory,
):
    await seed_agents_and_objective(portfolio_session_factory)
    now = utc_now()
    source = CompanySource(
        id="source-cyber-team",
        company_namespace="company:test",
        source_key="cyber_team",
        source_type="internal",
        name="Cyber-Team",
        trust_class="internal",
        sensitivity="internal",
    )
    async with portfolio_session_factory() as session:
        session.add(source)
        for suffix, outcome in (("success", "success"), ("failed", "failed")):
            signal = CompanySignal(
                id=f"signal-{suffix}",
                company_namespace="company:test",
                source_id=source.id,
                signal_type="audit.event",
                status="processed",
                trust_class="internal",
                sensitivity="internal",
                content_hash=f"hash-{suffix}",
                redacted_payload={
                    "event_type": "workflow.execute",
                    "outcome": outcome,
                },
                idempotency_key=f"signal-key-{suffix}",
            )
            audit_event = event(
                f"event-{suffix}",
                "evidence.audit.event",
            )
            audit_event.signal_id = signal.id
            audit_event.status = "accepted"
            work = BusinessWorkItem(
                id=f"work-{suffix}",
                company_namespace="company:test",
                title="Assess audit event",
                work_type="domain_assessment",
                status="ready",
                assigned_agent_id="observer_agent",
                event_id=audit_event.id,
                idempotency_key=f"work-key-{suffix}",
            )
            delivery = BusinessEventDelivery(
                id=f"delivery-{suffix}",
                event_id=audit_event.id,
                destination="work_portfolio",
                status="delivered",
                available_at=now,
                delivered_at=now,
            )
            disposition = BusinessEventDisposition(
                id=f"disposition-{suffix}",
                event_id=audit_event.id,
                sequence=1,
                status="accepted",
                disposition="accepted_work_item",
                reason="Assigned for assessment.",
                work_item_id=work.id,
                actor="business_event_router",
            )
            session.add_all([signal, audit_event, work, delivery, disposition])
        await session.commit()

    result = await WorkPortfolioService().reconcile_internal_audit_feedback()

    assert result == {"status": "completed", "examined": 2, "reconciled": 1}
    async with portfolio_session_factory() as session:
        success_work = await session.get(BusinessWorkItem, "work-success")
        failed_work = await session.get(BusinessWorkItem, "work-failed")
        success_event = await session.get(BusinessEvent, "event-success")
        success_dispositions = (
            await session.execute(
                select(BusinessEventDisposition)
                .where(BusinessEventDisposition.event_id == "event-success")
                .order_by(BusinessEventDisposition.sequence)
            )
        ).scalars().all()
    assert success_work.status == "completed"
    assert success_work.actual_outcome["classification"] == (
        "informational_audit_no_action"
    )
    assert failed_work.status == "ready"
    assert success_event.status == "resolved"
    assert [item.disposition for item in success_dispositions] == [
        "accepted_work_item",
        "no_action",
    ]


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
    assert trace["authoritative_context_hash"]
    task = manager.invoke_agent.await_args.args[1]
    assert "AUTHORITATIVE CURRENT OPERATING CONTEXT" in task
    context = result["items"][0]["actual_outcome"]["authoritative_context"]
    assert context["role_family"] == "knowledge"
    assert context["active_family_agent_count"] == 1
    assert context["unresolved_role_gaps"] == []
    assert result["items"][0]["actual_outcome"]["grounding"]["status"] == "passed"


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
async def test_role_loop_blocks_stale_role_gap_claim_against_live_state(
    portfolio_session_factory,
):
    await seed_agents_and_objective(portfolio_session_factory)
    async with portfolio_session_factory() as session:
        session.add(
            Agent(
                id="security-agent",
                role_family="security",
                role_name="Security & Compliance Agent",
                instructions="Assess current security evidence.",
                tools=["access_audit"],
                memory_namespace="company:test:security",
                status="active",
            )
        )
        await session.commit()
    manager = AsyncMock()
    manager.invoke_agent.return_value = json.dumps(
        {
            "assessment": (
                "The Security & Compliance Agent role remains unfulfilled and the "
                "role gap persists."
            ),
            "confidence": 0.9,
            "unknowns": [],
            "recommended_action": "revise",
            "expected_outcome": {"type": "role_activation"},
            "proposed_work": [
                {
                    "title": "Resolve Security Agent role gap",
                    "description": "Deploy the missing role.",
                    "work_type": "capability_proposal",
                    "priority": "high",
                    "acceptance_criteria": ["role_deployed"],
                    "expected_outcome": {"type": "role_activation"},
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
        title="Assess security benchmark failures",
        description="Use current operating state.",
        work_type="domain_assessment",
        company_namespace="company:test",
        assigned_agent_id="security-agent",
        payload={},
        acceptance_criteria=["current_state_used"],
        idempotency_key="work-stale-security-role",
    )

    result = await service.run_domain_loop("security-agent", prepare=False)

    item = result["items"][0]
    assert item["status"] == "blocked"
    assert item["actual_outcome"]["created_work_item_ids"] == []
    assert item["actual_outcome"]["grounding"]["status"] == "blocked"
    assert {
        finding["type"]
        for finding in item["actual_outcome"]["grounding"]["findings"]
    } == {
        "authoritative_role_state_conflict",
        "unsupported_role_gap_proposal",
    }
    assert item["actual_outcome"]["completion_contract"]["satisfied"] is False
    async with portfolio_session_factory() as session:
        assert (
            await session.execute(select(func.count(BusinessWorkItem.id)))
        ).scalar_one() == 1


@pytest.mark.asyncio
async def test_grounding_conflicts_quarantine_memory_and_pause_repeated_domain(
    portfolio_session_factory,
    monkeypatch,
):
    await seed_agents_and_objective(portfolio_session_factory)
    async with portfolio_session_factory() as session:
        session.add(
            Agent(
                id="security-agent",
                role_family="security",
                role_name="Security Agent",
                instructions="Assess security evidence.",
                tools=["security_scan"],
                memory_namespace="company:test:security",
                status="active",
            )
        )
        await session.commit()
    monkeypatch.setattr(settings, "autonomy_grounding_conflict_threshold", 2)
    monkeypatch.setattr(settings, "autonomy_grounding_conflict_lookback_hours", 24)
    manager = AsyncMock()
    manager.invoke_agent.return_value = json.dumps(
        {
            "assessment": "The Security role remains unfulfilled and unresolved.",
            "confidence": 0.9,
            "unknowns": [],
            "recommended_action": "escalate",
            "expected_outcome": {"type": "role_activation"},
            "proposed_work": [],
        }
    )
    audit = AsyncMock()
    service = WorkPortfolioService(
        agent_manager=manager,
        audit_service=audit,
        company_intelligence_service=FakeIntelligence(),
    )
    await service.ensure_active_agent_mandates()

    first = await service.create_work_item(
        title="First stale role assessment",
        description="Use current role state.",
        work_type="analysis",
        company_namespace="company:test",
        assigned_agent_id="security-agent",
        payload={},
        acceptance_criteria=["current_state_used"],
        idempotency_key="grounding-conflict-first",
    )
    async with portfolio_session_factory() as session:
        session.add_all(
            [
                MemoryEntry(
                    id="memory-stale-recalled",
                    agent_id="security-agent",
                    memory_type="episodic",
                    namespace="company:test:security",
                    content="The Security role remains unfulfilled.",
                    metadata_={},
                ),
                MemoryEntry(
                    id="memory-stale-written-one",
                    agent_id="security-agent",
                    memory_type="episodic",
                    namespace="company:test:security",
                    content="The role gap persists for Security.",
                    metadata_={},
                ),
                MemoryEntry(
                    id="memory-valid",
                    agent_id="security-agent",
                    memory_type="episodic",
                    namespace="company:test:security",
                    content="Three Security agents are currently active.",
                    metadata_={},
                ),
                MemoryTrace(
                    id="trace-grounding-one",
                    invocation_id="invocation-grounding-one",
                    agent_id="security-agent",
                    conversation_id=first["id"],
                    source_type="agent_mandate_loop",
                    task_excerpt="Assess current Security role state.",
                    memory_namespace="company:test:security",
                    recalled_memory_ids=["memory-stale-recalled", "memory-valid"],
                    written_memory_ids=["memory-stale-written-one"],
                    recall_count=2,
                    write_count=1,
                ),
            ]
        )
        await session.commit()

    first_result = await service.run_domain_loop("security-agent", prepare=False)

    first_remediation = first_result["items"][0]["actual_outcome"]["grounding"][
        "memory_remediation"
    ]
    first_preflight = first_result["items"][0]["actual_outcome"][
        "authoritative_context"
    ]["memory_preflight"]
    assert first_result["items"][0]["status"] == "blocked"
    assert first_preflight["quarantined_memory_ids"] == [
        "memory-stale-recalled",
        "memory-stale-written-one",
    ]
    assert first_preflight["agent_conflict_count"] == 0
    assert first_remediation["occurrence_count"] == 2
    assert first_remediation["agent_conflict_count"] == 1
    assert first_remediation["circuit_breaker_tripped"] is False
    assert first_remediation["quarantined_memory_ids"] == []
    async with portfolio_session_factory() as session:
        stale = await session.get(MemoryEntry, "memory-stale-recalled")
        valid = await session.get(MemoryEntry, "memory-valid")
        finding = (
            await session.execute(select(MemoryStewardFinding))
        ).scalar_one()
        control = await session.get(DomainAutonomyControl, "security")
    assert stale.metadata_["canonical_superseded"] is True
    assert stale.metadata_["exclude_from_recall_reason"] == (
        "authoritative_role_state_conflict"
    )
    assert "canonical_superseded" not in valid.metadata_
    assert finding.finding_type == "authoritative_memory_conflict"
    assert finding.evidence["occurrence_count"] == 2
    assert finding.evidence["agent_conflict_count"] == 1
    assert control is None

    second = await service.create_work_item(
        title="Second stale role assessment",
        description="Use current role state again.",
        work_type="analysis",
        company_namespace="company:test",
        assigned_agent_id="security-agent",
        payload={},
        acceptance_criteria=["current_state_used"],
        idempotency_key="grounding-conflict-second",
    )
    async with portfolio_session_factory() as session:
        session.add_all(
            [
                MemoryEntry(
                    id="memory-stale-written-two",
                    agent_id="security-agent",
                    memory_type="episodic",
                    namespace="company:test:security",
                    content="The Security role is missing.",
                    metadata_={},
                ),
                MemoryTrace(
                    id="trace-grounding-two",
                    invocation_id="invocation-grounding-two",
                    agent_id="security-agent",
                    conversation_id=second["id"],
                    source_type="agent_mandate_loop",
                    task_excerpt="Reassess current Security role state.",
                    memory_namespace="company:test:security",
                    recalled_memory_ids=["memory-stale-recalled"],
                    written_memory_ids=["memory-stale-written-two"],
                    recall_count=1,
                    write_count=1,
                ),
            ]
        )
        await session.commit()

    second_result = await service.run_domain_loop("security-agent", prepare=False)

    second_remediation = second_result["items"][0]["actual_outcome"]["grounding"][
        "memory_remediation"
    ]
    assert second_result["items"][0]["status"] == "blocked"
    assert second_remediation["occurrence_count"] == 4
    assert second_remediation["agent_conflict_count"] == 2
    assert second_remediation["circuit_breaker_tripped"] is True
    assert second_remediation["domain_state"] == "paused"
    async with portfolio_session_factory() as session:
        control = await session.get(DomainAutonomyControl, "security")
        finding = (
            await session.execute(select(MemoryStewardFinding))
        ).scalar_one()
    assert control.state == "paused"
    assert control.owner == "autonomy_grounding_circuit_breaker"
    assert finding.severity == "high"
    assert finding.evidence["occurrence_count"] == 4
    assert finding.evidence["agent_conflict_count"] == 2
    assert audit.record_control_evidence.await_count == 4


@pytest.mark.asyncio
async def test_memory_preflight_heals_stale_memory_when_agent_reasoning_is_correct(
    portfolio_session_factory,
):
    await seed_agents_and_objective(portfolio_session_factory)
    async with portfolio_session_factory() as session:
        session.add_all(
            [
                Agent(
                    id="security-agent",
                    role_family="security",
                    role_name="Security Agent",
                    instructions="Assess security evidence.",
                    tools=["security_scan"],
                    memory_namespace="company:test:security",
                    status="active",
                ),
                MemoryEntry(
                    id="memory-stale-preflight",
                    agent_id="security-agent",
                    memory_type="episodic",
                    namespace="company:test:security",
                    content="The Security role gap persists.",
                    metadata_={},
                ),
            ]
        )
        await session.commit()
    manager = AsyncMock()
    manager.invoke_agent.return_value = json.dumps(
        {
            "assessment": (
                "Security has one active agent and no unresolved role gap in the "
                "authoritative current operating context."
            ),
            "confidence": 0.99,
            "unknowns": [],
            "recommended_action": "no_action",
            "expected_outcome": {"type": "documented_no_action"},
            "proposed_work": [],
        }
    )
    audit = AsyncMock()
    service = WorkPortfolioService(
        agent_manager=manager,
        audit_service=audit,
        company_intelligence_service=FakeIntelligence(),
    )
    await service.ensure_active_agent_mandates()
    await service.create_work_item(
        title="Verify Security role state",
        description="Reject stale memory using current state.",
        work_type="analysis",
        company_namespace="company:test",
        assigned_agent_id="security-agent",
        payload={},
        acceptance_criteria=["current_state_used"],
        idempotency_key="grounding-preflight-correct-reasoning",
    )

    result = await service.run_domain_loop("security-agent", prepare=False)

    item = result["items"][0]
    preflight = item["actual_outcome"]["authoritative_context"][
        "memory_preflight"
    ]
    assert item["status"] == "completed"
    assert item["actual_outcome"]["grounding"]["status"] == "passed"
    assert preflight["quarantined_memory_ids"] == ["memory-stale-preflight"]
    assert preflight["agent_conflict_count"] == 0
    assert preflight["circuit_breaker_tripped"] is False
    async with portfolio_session_factory() as session:
        memory = await session.get(MemoryEntry, "memory-stale-preflight")
        finding = (
            await session.execute(select(MemoryStewardFinding))
        ).scalar_one()
        control = await session.get(DomainAutonomyControl, "security")
    assert memory.metadata_["canonical_superseded"] is True
    assert finding.evidence["agent_conflict_count"] == 0
    assert control is None
    audit.record_control_evidence.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_generated_work_cancels_descendants_and_records_evidence(
    portfolio_session_factory,
):
    await seed_agents_and_objective(portfolio_session_factory)
    audit = AsyncMock()
    service = WorkPortfolioService(audit_service=audit)
    await service.ensure_active_agent_mandates()
    parent = await service.create_work_item(
        title="Invalid generated premise",
        description="Parent work.",
        work_type="analysis",
        company_namespace="company:test",
        assigned_agent_id="knowledge-agent",
        payload={},
        acceptance_criteria=["reviewed"],
        idempotency_key="cancel-parent",
    )
    child = await service.create_work_item(
        title="Invalid child",
        description="Child work.",
        work_type="analysis",
        company_namespace="company:test",
        assigned_agent_id="knowledge-agent",
        payload={"parent_work_item_id": parent["id"]},
        acceptance_criteria=["reviewed"],
        idempotency_key="cancel-child",
    )
    grandchild = await service.create_work_item(
        title="Invalid grandchild",
        description="Grandchild work.",
        work_type="analysis",
        company_namespace="company:test",
        assigned_agent_id="knowledge-agent",
        payload={"parent_work_item_id": child["id"]},
        acceptance_criteria=["reviewed"],
        idempotency_key="cancel-grandchild",
    )
    unrelated = await service.create_work_item(
        title="Valid independent work",
        description="Unrelated work.",
        work_type="analysis",
        company_namespace="company:test",
        assigned_agent_id="knowledge-agent",
        payload={},
        acceptance_criteria=["reviewed"],
        idempotency_key="cancel-unrelated",
    )

    result = await service.cancel_work_item(
        parent["id"],
        reason="The generated work was based on stale role state.",
        actor="owner@example.com",
        include_descendants=True,
    )

    assert result["cancelled_count"] == 3
    assert result["cancelled_ids"] == sorted(
        [parent["id"], child["id"], grandchild["id"]]
    )
    async with portfolio_session_factory() as session:
        cancelled = [
            await session.get(BusinessWorkItem, item_id)
            for item_id in result["cancelled_ids"]
        ]
        independent = await session.get(BusinessWorkItem, unrelated["id"])
    assert all(item.status == "cancelled" for item in cancelled)
    assert all(
        item.actual_outcome["classification"]
        == "owner_cancelled_invalid_generated_work"
        for item in cancelled
    )
    assert independent.status == "ready"
    audit.record_control_evidence.assert_awaited_once()


@pytest.mark.asyncio
async def test_role_loop_normalizes_reviewed_safe_work_type_aliases(
    portfolio_session_factory,
):
    await seed_agents_and_objective(portfolio_session_factory)
    manager = AsyncMock()
    manager.invoke_agent.return_value = json.dumps(
        {
            "assessment": "Product evidence needs an internal planning pass.",
            "confidence": 0.82,
            "unknowns": [],
            "recommended_action": "continue",
            "expected_outcome": {"type": "prioritized_backlog"},
            "proposed_work": [
                {
                    "title": "Prioritize the product backlog",
                    "description": "Rank internal work without external mutation.",
                    "work_type": "strategic_planning",
                    "priority": "medium",
                    "acceptance_criteria": ["prioritized_backlog_recorded"],
                    "expected_outcome": {"type": "prioritized_backlog"},
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
        title="Assess product state",
        description="Create a safe internal backlog.",
        work_type="domain_assessment",
        company_namespace="company:test",
        assigned_agent_id="knowledge-agent",
        payload={},
        acceptance_criteria=["prioritized_backlog_recorded"],
        idempotency_key="work-safe-alias",
    )

    result = await service.run_domain_loop("knowledge-agent", prepare=False)

    outcome = result["items"][0]["actual_outcome"]
    assert result["items"][0]["status"] == "completed"
    assert outcome["completion_contract"]["satisfied"] is True
    async with portfolio_session_factory() as session:
        child = await session.get(BusinessWorkItem, outcome["created_work_item_ids"][0])
    assert child.work_type == "planning"


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
    assert result["items"][0]["status"] == "blocked"
    assert outcome["created_work_item_ids"] == []
    assert outcome["completion_contract"] == {
        "follow_up_required": True,
        "requested_follow_up": True,
        "accepted_follow_up_count": 0,
        "satisfied": False,
    }
    assert outcome["rejected_proposals"] == [
        {
            "index": 0,
            "reason": "work_type_not_allowlisted",
            "work_type": "external_mutation",
        }
    ]


@pytest.mark.asyncio
async def test_role_loop_blocks_when_proposal_depth_prevents_required_follow_up(
    portfolio_session_factory,
):
    await seed_agents_and_objective(portfolio_session_factory)
    manager = AsyncMock()
    manager.invoke_agent.return_value = json.dumps(
        {
            "assessment": "More bounded research is required.",
            "confidence": 0.8,
            "unknowns": ["Verified source"],
            "recommended_action": "continue",
            "expected_outcome": {"type": "evidence_artifact"},
            "proposed_work": [
                {
                    "title": "Collect one more source",
                    "description": "Continue the evidence chain.",
                    "work_type": "research",
                    "priority": "low",
                    "acceptance_criteria": ["source_recorded"],
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
    await service.create_work_item(
        title="Stop recursive decomposition",
        description="The depth circuit breaker must be truthful.",
        work_type="research",
        company_namespace="company:test",
        assigned_agent_id="knowledge-agent",
        payload={"proposal_depth": 3},
        acceptance_criteria=["source_recorded"],
        idempotency_key="work-depth-limit",
    )

    result = await service.run_domain_loop("knowledge-agent", prepare=False)

    outcome = result["items"][0]["actual_outcome"]
    assert result["items"][0]["status"] == "blocked"
    assert outcome["created_work_item_ids"] == []
    assert outcome["rejected_proposals"][0]["reason"] == "proposal_depth_limit"


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
