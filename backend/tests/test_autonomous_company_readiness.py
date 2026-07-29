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
    CompanyModelRevision,
    CompanyObjective,
    CompanyObjectiveRevision,
    CompanySource,
    OperatingKPIDefinition,
    OperatingKPIRevision,
)
from cyber_team.operations import readiness_v3 as readiness_module
from cyber_team.operations.readiness_v3 import AutonomousCompanyReadinessService


class FakeLLM:
    async def validate_provider(self):
        return {"provider": "local", "mode": "live", "detail": "Local model ready."}


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
