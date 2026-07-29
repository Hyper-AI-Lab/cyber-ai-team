import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import Base
from cyber_team.db.models import (
    Agent,
    CompanyClaim,
    CompanyModelRevision,
    CompanyObjectiveRevision,
    OperatingKPIDefinition,
    OperatingKPIRevision,
    StrategicExperiment,
)
from cyber_team.operations import strategy as strategy_module
from cyber_team.operations.strategy import (
    CompanyStrategyService,
    KPIFormula,
    KPIFormulaError,
)


class FakeAudit:
    def __init__(self):
        self.evidence = []

    async def record_control_evidence(self, **kwargs):
        self.evidence.append(kwargs)
        return kwargs


class EvidenceBoundStrategyLLM:
    async def invoke_json(self, **kwargs):
        del kwargs["system_prompt"], kwargs["agent_id"]
        return __import__("json").loads(kwargs["user_message"])["example"]


class RepairingStrategyLLM:
    def __init__(self):
        self.calls = 0

    async def invoke_json(self, **kwargs):
        self.calls += 1
        payload = __import__("json").loads(kwargs["user_message"])
        if self.calls == 1:
            candidate = payload["example"]
            candidate["kpis"][0]["formula"] = "count(raw_records)"
            candidate["kpis"][0]["bindings"] = {}
            return candidate
        assert payload["validation_errors"]
        return payload["example"]


class InvalidStrategyLLM:
    def __init__(self):
        self.calls = 0

    async def invoke_json(self, **kwargs):
        self.calls += 1
        del kwargs
        return {
            "objectives": [],
            "kpis": [
                {
                    "key": "unsafe",
                    "title": "Unsafe KPI",
                    "formula": "count(raw_records)",
                    "bindings": {},
                    "comparison": "max",
                    "confidence": 1,
                }
            ],
            "experiments": [],
        }


@pytest.fixture
async def strategy_session_factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(strategy_module, "async_session", factory)
    monkeypatch.setattr(settings, "company_namespace", "company:test")
    try:
        yield factory
    finally:
        await engine.dispose()


async def seed_active_model(factory):
    async with factory() as session:
        claim = CompanyClaim(
            id="claim-offering",
            company_namespace="company:test",
            subject="company",
            predicate="offering_candidate",
            value={"name": "AI Company OS"},
            epistemic_state="inferred",
            confidence=0.8,
            trust_class="canonical",
            sensitivity="internal",
            evidence_ids=["evidence-offering"],
            claim_hash="claim-hash-offering",
            owner_locked=False,
            valid_from=utc_now(),
            created_by="company_discovery_agent",
        )
        model = CompanyModelRevision(
            id="model-active",
            company_namespace="company:test",
            revision=1,
            status="active",
            model={
                "business_description": None,
                "offerings": [{"name": "AI Company OS"}],
                "customer_segments": [],
                "value_propositions": [],
                "channels": [],
                "jurisdictions": ["Germany"],
                "resources": [],
                "constraints": [],
                "risks": [],
            },
            claim_ids=[claim.id],
            unknowns=["business_description", "customer_segments", "channels"],
            disputes=[],
            provenance_coverage=0.8,
            confidence=0.8,
            source_hash="model-source-hash",
            owner_locks={},
            created_by="company_discovery_agent",
            activated_at=utc_now(),
        )
        session.add_all([claim, model])
        await session.commit()


def test_kpi_formula_dsl_evaluates_allowlisted_arithmetic():
    bindings = {
        "completed_projects": "erpnext.project.completed",
        "total_projects": "erpnext.project.total",
    }
    value = KPIFormula.evaluate(
        "round(completed_projects / max(total_projects, 1) * 100, 2)",
        bindings,
        {"erpnext.project.completed": 3, "erpnext.project.total": 4},
    )

    assert value == 75.0


@pytest.mark.parametrize(
    ("formula", "bindings"),
    [
        ("__import__('os').system('id')", {}),
        ("customer.__class__", {"customer": "erpnext.customer.active"}),
        ("customers", {"customers": "raw_sql.customer_count"}),
        ("missing_metric + 1", {}),
    ],
)
def test_kpi_formula_dsl_rejects_code_and_unapproved_bindings(formula, bindings):
    with pytest.raises(KPIFormulaError):
        KPIFormula.validate(formula, bindings)


@pytest.mark.asyncio
async def test_strategy_cycle_creates_probationary_revisions_idempotently(
    strategy_session_factory,
):
    await seed_active_model(strategy_session_factory)
    audit = FakeAudit()
    service = CompanyStrategyService(
        llm_gateway=EvidenceBoundStrategyLLM(),
        audit_service=audit,
    )

    first = await service.run_strategy_cycle()
    second = await service.run_strategy_cycle()

    assert first["status"] == "completed"
    assert len(first["objectives"]) == 2
    assert len(first["kpis"]) == 2
    assert len(first["experiments"]) == 1
    assert all(item["status"] == "probation" for item in first["objectives"])
    assert all(item["duplicate"] for item in second["objectives"])
    assert all(item["duplicate"] for item in second["kpis"])
    assert all(item["duplicate"] for item in second["experiments"])
    async with strategy_session_factory() as session:
        strategy_agent = await session.get(Agent, service.STRATEGY_AGENT_ID)
        objective_count = (
            await session.execute(select(func.count(CompanyObjectiveRevision.id)))
        ).scalar_one()
        kpi_count = (
            await session.execute(select(func.count(OperatingKPIRevision.id)))
        ).scalar_one()
        experiment_count = (
            await session.execute(select(func.count(StrategicExperiment.id)))
        ).scalar_one()
    assert strategy_agent is not None
    assert strategy_agent.config["side_effect_authority"] == "none"
    assert objective_count == 2
    assert kpi_count == 2
    assert experiment_count == 1
    assert audit.evidence[-1]["outcome"] == "success"


@pytest.mark.asyncio
async def test_strategy_observation_ignores_legacy_governor_kpi_revisions(
    strategy_session_factory,
):
    await seed_active_model(strategy_session_factory)
    async with strategy_session_factory() as session:
        definition = OperatingKPIDefinition(
            id="legacy-kpi",
            key="readiness_blockers",
            title="Readiness blockers",
            description="Populated directly from the executive snapshot.",
            unit="count",
            comparison="max",
            target_value=0,
            source="executive_snapshot",
            status="active",
        )
        revision = OperatingKPIRevision(
            id="legacy-kpi-revision",
            kpi_definition_id=definition.id,
            revision=1,
            status="active",
            formula="readiness_blockers",
            measurement_bindings={"readiness_blockers": "executive_snapshot"},
            target_value=0,
            objective_revision_ids=[],
            evidence_ids=[],
            confidence=1,
            owner_locked=False,
            created_by="v3_migration",
            activated_at=utc_now(),
        )
        session.add_all([definition, revision])
        await session.commit()

    result = await CompanyStrategyService(
        llm_gateway=EvidenceBoundStrategyLLM()
    ).run_strategy_cycle()

    assert result["status"] == "completed"
    assert all(item["kpi_key"] != "readiness_blockers" for item in result["observations"])


def test_target_revision_change_is_bounded_to_twenty_percent():
    assert CompanyStrategyService._bounded_number(100, 250) == 120
    assert CompanyStrategyService._bounded_number(100, 10) == 80
    assert CompanyStrategyService._bounded_number(None, 250) == 250


@pytest.mark.asyncio
async def test_strategy_cycle_blocks_without_active_company_model(
    strategy_session_factory,
):
    result = await CompanyStrategyService().run_strategy_cycle()

    assert result["status"] == "blocked"
    assert "company model" in result["reason"].lower()


@pytest.mark.asyncio
async def test_strategy_cycle_does_not_invent_rule_only_strategy_when_llm_missing(
    strategy_session_factory,
):
    await seed_active_model(strategy_session_factory)

    result = await CompanyStrategyService().run_strategy_cycle()

    assert result["status"] == "blocked"
    assert result["reason"] == "strategy_advisory_unavailable"
    assert "No strategy advisory LLM" in result["detail"]


@pytest.mark.asyncio
async def test_strategy_cycle_repairs_invalid_structured_output_once(
    strategy_session_factory,
):
    await seed_active_model(strategy_session_factory)
    llm = RepairingStrategyLLM()

    result = await CompanyStrategyService(llm_gateway=llm).run_strategy_cycle()

    assert result["status"] == "completed"
    assert llm.calls == 2


@pytest.mark.asyncio
async def test_strategy_cycle_fails_closed_after_bounded_repair(
    strategy_session_factory,
):
    await seed_active_model(strategy_session_factory)
    llm = InvalidStrategyLLM()

    result = await CompanyStrategyService(llm_gateway=llm).run_strategy_cycle()

    assert result["status"] == "blocked"
    assert result["reason"] == "strategy_advisory_unavailable"
    assert "Only min, max, abs, and round" in result["detail"]
    assert llm.calls == 2
