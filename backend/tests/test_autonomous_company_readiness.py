from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import Base
from cyber_team.db.models import (
    Agent,
    AgentMandate,
    BusinessEvent,
    BusinessWorkItem,
    CompanyModelRevision,
    CompanyObjective,
    CompanyObjectiveRevision,
    CompanySignal,
    CompanySource,
    DomainAutonomyControl,
    OperatingKPIDefinition,
    OperatingKPIRevision,
    OutcomeAssessment,
)
from cyber_team.operations import readiness_v3 as readiness_module
from cyber_team.operations.readiness_v3 import AutonomousCompanyReadinessService


class FakeLLM:
    async def validate_provider(self):
        return {"provider": "local", "mode": "live", "detail": "Local model ready."}


class FakeLocalFallbackLLM:
    async def validate_provider(self):
        return {
            "provider": "llama_cpp",
            "mode": "local_fallback",
            "status": "live",
            "blocking": False,
            "detail": "Hosted capacity is exhausted; local inference is active.",
        }


@pytest.fixture
async def readiness_session_factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(readiness_module, "async_session", factory)
    monkeypatch.setattr(settings, "company_autonomy_enabled", True)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_never_discovered_company_is_an_explicit_blocker(
    readiness_session_factory,
):
    result = await AutonomousCompanyReadinessService(llm_gateway=FakeLLM()).summary()

    assert result["status"] == "degraded"
    assert result["sections"]["company_model"]["status"] == "not_discovered"
    assert result["sections"]["company_model"]["critical_unknowns"]
    assert result["sections"]["strategy"]["status"] == "not_generated"


@pytest.mark.asyncio
async def test_healthy_local_fallback_satisfies_model_availability():
    result = await AutonomousCompanyReadinessService(
        llm_gateway=FakeLocalFallbackLLM()
    )._model_availability()

    assert result["provider"] == "llama_cpp"
    assert result["mode"] == "local_fallback"
    assert result["status"] == "ready"
    assert result["blocking"] is False


@pytest.mark.asyncio
async def test_stale_claim_extraction_failure_is_an_explicit_blocker(
    readiness_session_factory,
):
    async with readiness_session_factory() as session:
        source = CompanySource(
            id="source-repository",
            company_namespace="company:test",
            source_key="repository",
            source_type="document",
            name="Repository",
            status="active",
            trust_class="internal",
            sensitivity="internal",
            config={},
            cursor={},
            last_success_at=utc_now(),
        )
        session.add(source)
        session.add(
            CompanySignal(
                id="signal-failed-extraction",
                company_namespace="company:test",
                source_id=source.id,
                signal_type="document.updated",
                external_id="request.txt",
                status="pending",
                trust_class="internal",
                sensitivity="internal",
                content_hash="content-hash",
                redacted_payload={},
                injection_status="clear",
                claim_extraction_status="failed",
                claim_extraction_attempts=2,
                claim_extraction_error="llm_claim_extraction_failed:TimeoutError",
                idempotency_key="signal-failed-extraction",
                received_at=utc_now() - timedelta(hours=2),
            )
        )
        await session.commit()

    result = await AutonomousCompanyReadinessService(llm_gateway=FakeLLM()).summary()

    extraction = result["sections"]["claim_extraction"]
    assert extraction["status"] == "stale_failed"
    assert extraction["blocking"] is True
    assert extraction["stale_failed"] == 1


@pytest.mark.asyncio
async def test_stale_low_trust_extraction_is_visible_but_not_a_global_blocker(
    readiness_session_factory,
):
    async with readiness_session_factory() as session:
        source = CompanySource(
            id="source-public-research",
            company_namespace="company:test",
            source_key="public_research",
            source_type="searxng",
            name="Public research",
            status="active",
            trust_class="public_secondary",
            sensitivity="public",
            config={},
            cursor={},
            last_success_at=utc_now(),
        )
        session.add(source)
        session.add(
            CompanySignal(
                id="signal-advisory-extraction",
                company_namespace="company:test",
                source_id=source.id,
                signal_type="research.results",
                external_id="research-query",
                status="pending",
                trust_class="public_secondary",
                sensitivity="public",
                content_hash="advisory-content-hash",
                redacted_payload={},
                injection_status="clear",
                claim_extraction_status="failed",
                claim_extraction_attempts=2,
                claim_extraction_error="llm_claim_extraction_failed:TimeoutError",
                idempotency_key="signal-advisory-extraction",
                received_at=utc_now() - timedelta(hours=2),
            )
        )
        await session.commit()

    result = await AutonomousCompanyReadinessService(llm_gateway=FakeLLM()).summary()

    extraction = result["sections"]["claim_extraction"]
    assert extraction["status"] == "advisory_degraded"
    assert extraction["blocking"] is False
    assert extraction["advisory_stale_failed"] == 1


@pytest.mark.asyncio
async def test_ready_control_plane_has_fresh_sources_model_strategy_and_mandates(
    readiness_session_factory,
):
    now = utc_now()
    async with readiness_session_factory() as session:
        for key in ("erpnext", "owner_instructions", "repository"):
            session.add(
                CompanySource(
                    id=f"source-{key}",
                    company_namespace="company:test",
                    source_key=key,
                    source_type=key,
                    name=key,
                    status="active",
                    trust_class="canonical",
                    sensitivity="internal",
                    config={},
                    cursor={},
                    last_success_at=now,
                )
            )
        session.add(
            CompanyModelRevision(
                id="model-1",
                company_namespace="company:test",
                revision=1,
                status="active",
                model={
                    "business_description": "Evidence-backed company.",
                    "offerings": [{"name": "Company OS"}],
                    "customer_segments": ["founders"],
                    "jurisdictions": ["Germany"],
                },
                claim_ids=["claim-1"],
                unknowns=[],
                disputes=[],
                provenance_coverage=1.0,
                confidence=0.9,
                source_hash="model-hash",
                owner_locks={},
                activated_at=now,
            )
        )
        objective = CompanyObjective(
            id="objective-1", title="Serve founders", status="active"
        )
        objective_revision = CompanyObjectiveRevision(
            id="objective-revision-1",
            objective_id=objective.id,
            revision=1,
            status="probation",
            title=objective.title,
            category="business",
            evidence_ids=["evidence-1"],
            confidence=0.9,
            activated_at=now,
        )
        kpi = OperatingKPIDefinition(
            id="kpi-1",
            key="validated_segments",
            title="Validated segments",
            status="active",
        )
        kpi_revision = OperatingKPIRevision(
            id="kpi-revision-1",
            kpi_definition_id=kpi.id,
            revision=1,
            status="probation",
            formula="validated_segments",
            measurement_bindings={"validated_segments": "erpnext.customer.total"},
            target_value=1,
            objective_revision_ids=[objective_revision.id],
            evidence_ids=["evidence-1"],
            confidence=0.9,
            activated_at=now,
        )
        agent = Agent(
            id="knowledge-agent",
            role_family="knowledge",
            role_name="Knowledge Agent",
            instructions="Maintain evidence.",
            tools=[],
            memory_namespace="company:test:knowledge",
            status="active",
        )
        mandate = AgentMandate(
            id="mandate-1",
            agent_id=agent.id,
            version=1,
            status="active",
            objective_ids=[objective_revision.id],
            authority={},
            budget={},
            inputs=[],
            outputs=[],
            kpi_keys=[kpi.key],
            cadence={},
            escalation_rules=[],
            activated_at=now,
        )
        session.add_all(
            [
                objective,
                objective_revision,
                kpi,
                kpi_revision,
                agent,
                mandate,
            ]
        )
        await session.commit()

    result = await AutonomousCompanyReadinessService(llm_gateway=FakeLLM()).summary()

    assert result["status"] == "ready"
    assert result["sections"]["company_model"]["confidence"] == 0.9
    assert result["sections"]["source_freshness"]["stale_required"] == []
    assert result["sections"]["mandates"]["missing_mandates"] == 0
    assert result["sections"]["strategy"]["status"] == "ready"


@pytest.mark.asyncio
async def test_business_events_only_block_after_processing_window(
    readiness_session_factory,
    monkeypatch,
):
    monkeypatch.setattr(settings, "business_event_readiness_stale_after_seconds", 1800)
    now = utc_now()
    event = BusinessEvent(
        id="event-1",
        company_namespace="company:test",
        event_type="evidence.repository.changed",
        source_type="repository",
        source_id="README.md",
        payload={},
        status="pending",
        idempotency_key="event-1",
        occurred_at=now,
        created_at=now,
    )
    async with readiness_session_factory() as session:
        session.add(event)
        await session.commit()

    fresh = await AutonomousCompanyReadinessService(llm_gateway=FakeLLM()).summary()
    fresh_events = fresh["sections"]["business_events"]
    assert fresh_events["status"] == "processing"
    assert fresh_events["blocking"] is False
    assert fresh_events["in_processing_window"] == 1
    assert fresh_events["stale_unexplained"] == 0

    async with readiness_session_factory() as session:
        stored = await session.get(BusinessEvent, event.id)
        assert stored is not None
        stored.created_at = now - timedelta(seconds=1801)
        await session.commit()

    stale = await AutonomousCompanyReadinessService(llm_gateway=FakeLLM()).summary()
    stale_events = stale["sections"]["business_events"]
    assert stale_events["status"] == "stale_pending"
    assert stale_events["blocking"] is True
    assert stale_events["in_processing_window"] == 0
    assert stale_events["stale_unexplained"] == 1


@pytest.mark.asyncio
async def test_work_portfolio_reports_backlog_and_grounding_recovery_blockers(
    readiness_session_factory,
    monkeypatch,
):
    monkeypatch.setattr(settings, "autonomy_domain_max_nonterminal_work_items", 1)
    now = utc_now()
    async with readiness_session_factory() as session:
        agent = Agent(
            id="observer_agent",
            role_family="governance",
            role_name="Observer Agent",
            instructions="Review evidence.",
            tools=[],
            memory_namespace="company:test:observer",
            status="active",
        )
        session.add_all(
            [
                agent,
                BusinessWorkItem(
                    id="work-governance",
                    company_namespace="company:test",
                    title="Review evidence",
                    work_type="analysis",
                    status="ready",
                    assigned_agent_id=agent.id,
                    payload={},
                    acceptance_criteria=[],
                    expected_outcome={},
                    actual_outcome={},
                    policy_decision={},
                    idempotency_key="work-governance",
                    created_at=now,
                ),
                DomainAutonomyControl(
                    domain="governance",
                    state="paused",
                    reason="Grounding conflict requires recovery.",
                    owner="autonomy_grounding_circuit_breaker",
                ),
            ]
        )
        await session.commit()

    result = await AutonomousCompanyReadinessService(llm_gateway=FakeLLM()).summary()
    portfolio = result["sections"]["work_portfolio"]

    assert portfolio["status"] == "recovery_required"
    assert portfolio["blocking"] is True
    assert portfolio["domain_backlogs"] == {"governance": 1}
    assert portfolio["saturated_domains"] == ["governance"]
    assert portfolio["recovery_required_domains"] == ["governance"]
    assert any(item["area"] == "work_portfolio" for item in result["blockers"])


@pytest.mark.asyncio
async def test_stale_unassessed_work_blocks_outcome_learning_readiness(
    readiness_session_factory,
    monkeypatch,
):
    monkeypatch.setattr(settings, "outcome_readiness_stale_after_seconds", 60)
    now = utc_now()
    async with readiness_session_factory() as session:
        session.add_all(
            [
                BusinessWorkItem(
                    id="work-assessed",
                    company_namespace="company:test",
                    title="Assessed work",
                    work_type="analysis",
                    status="completed",
                    payload={},
                    acceptance_criteria=[],
                    expected_outcome={},
                    actual_outcome={},
                    policy_decision={},
                    idempotency_key="work-assessed",
                    updated_at=now - timedelta(minutes=5),
                    completed_at=now - timedelta(minutes=5),
                ),
                BusinessWorkItem(
                    id="work-unassessed",
                    company_namespace="company:test",
                    title="Unassessed work",
                    work_type="analysis",
                    status="completed",
                    payload={},
                    acceptance_criteria=[],
                    expected_outcome={},
                    actual_outcome={},
                    policy_decision={},
                    idempotency_key="work-unassessed",
                    updated_at=now - timedelta(minutes=5),
                    completed_at=now - timedelta(minutes=5),
                ),
                OutcomeAssessment(
                    id="outcome-assessed",
                    work_item_id="work-assessed",
                    status="recorded",
                    expected_outcome={},
                    actual_outcome={},
                    kpi_changes={},
                    guardrail_breaches=[],
                    costs={},
                    failures=[],
                    attribution_confidence=1.0,
                    evaluator_score=1.0,
                    recommendation="continue",
                    evidence_ids=[],
                    idempotency_key="outcome-assessed",
                ),
            ]
        )
        await session.commit()

    result = await AutonomousCompanyReadinessService(llm_gateway=FakeLLM()).summary()
    learning = result["sections"]["outcome_learning"]

    assert learning["status"] == "stale_backlog"
    assert learning["blocking"] is True
    assert learning["terminal_work"] == 2
    assert learning["assessed_work"] == 1
    assert learning["unassessed_work"] == 1
    assert learning["stale_unassessed_work"] == 1
    assert any(item["area"] == "outcome_learning" for item in result["blockers"])
