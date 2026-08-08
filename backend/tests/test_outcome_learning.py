from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import Base
from cyber_team.db.models import (
    Agent,
    BusinessWorkItem,
    OperationGraphEdge,
    OperationGraphNode,
    OutcomeAssessment,
    OutsourcingRequest,
)
from cyber_team.operations import outcomes as outcomes_module
from cyber_team.operations.outcomes import OutcomeLearningService


class FakePolicy:
    def __init__(self):
        self.cases = []

    async def record_validated_case(self, action_class, **kwargs):
        self.cases.append((action_class, kwargs))
        return {"action_class": action_class, "validated_cases": len(self.cases)}


@pytest.fixture
async def outcome_session_factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(outcomes_module, "async_session", factory)
    monkeypatch.setattr(settings, "company_namespace", "company:test")
    monkeypatch.setattr(settings, "operation_graph_indexing_enabled", True)
    try:
        yield factory
    finally:
        await engine.dispose()


def work_item(
    work_id,
    *,
    status="completed",
    assigned_agent_id=None,
    actual_outcome=None,
    policy_decision=None,
):
    return BusinessWorkItem(
        id=work_id,
        company_namespace="company:test",
        title=f"Work {work_id}",
        description="Evidence-backed work.",
        work_type="assessment",
        status=status,
        priority="medium",
        risk_level="low",
        assigned_agent_id=assigned_agent_id,
        payload={},
        acceptance_criteria=["outcome_recorded"],
        expected_outcome={"type": "assessment"},
        actual_outcome=actual_outcome or {},
        policy_decision=policy_decision or {},
        idempotency_key=f"key-{work_id}",
        created_by="test",
        completed_at=utc_now() if status == "completed" else None,
    )


@pytest.mark.asyncio
async def test_outcome_assessment_is_idempotent_and_graph_linked(
    outcome_session_factory,
):
    async with outcome_session_factory() as session:
        session.add(
            work_item(
                "work-success",
                actual_outcome={
                    "assessment": "Supported recommendation.",
                    "evidence_ids": ["evidence-1"],
                    "kpi_changes": {"coverage": 0.1},
                    "side_effects_executed": False,
                },
            )
        )
        await session.commit()
    memory = AsyncMock()
    memory.remember.return_value = {"id": "memory-reflection"}
    service = OutcomeLearningService(
        action_policy_service=FakePolicy(), memory_service=memory
    )

    first = await service.assess_terminal_work()
    second = await service.assess_terminal_work()

    assert first["assessed"] == 1
    assert first["items"][0]["recommendation"] == "continue"
    assert second["assessed"] == 0
    async with outcome_session_factory() as session:
        assert (
            await session.execute(select(func.count(OutcomeAssessment.id)))
        ).scalar_one() == 1
        assert (
            await session.execute(select(func.count(OperationGraphNode.id)))
        ).scalar_one() == 2
        assert (
            await session.execute(select(func.count(OperationGraphEdge.id)))
        ).scalar_one() == 1
    memory.remember.assert_awaited_once()


@pytest.mark.asyncio
async def test_unauthorized_side_effect_forces_rollback_and_policy_finding(
    outcome_session_factory,
):
    async with outcome_session_factory() as session:
        session.add(
            work_item(
                "work-side-effect",
                actual_outcome={"side_effects_executed": True},
                policy_decision={
                    "allowed": False,
                    "action_class": "external_communication",
                    "external_side_effect": True,
                },
            )
        )
        await session.commit()
    policy = FakePolicy()
    service = OutcomeLearningService(action_policy_service=policy)

    result = await service.assess_work_item("work-side-effect")

    assert result["recommendation"] == "rollback"
    assert result["guardrail_breaches"] == [
        {"type": "unauthorized_side_effect", "severity": "high"}
    ]
    assert policy.cases == []
    assert result["action_policy"] == {
        "status": "not_counted",
        "reason": "durable_validation_case_required",
    }


@pytest.mark.asyncio
async def test_repeated_agent_failures_create_one_foss_outsourcing_request(
    outcome_session_factory,
):
    async with outcome_session_factory() as session:
        session.add(
            Agent(
                id="engineering-agent",
                role_family="engineering",
                role_name="Engineering Agent",
                instructions="Build safely.",
                tools=[],
                memory_namespace="company:test:engineering",
                status="active",
            )
        )
        session.add_all(
            [
                work_item(
                    f"work-failure-{index}",
                    status="failed",
                    assigned_agent_id="engineering-agent",
                    actual_outcome={"error": "complexity_exceeded"},
                )
                for index in range(3)
            ]
        )
        await session.commit()
    service = OutcomeLearningService(action_policy_service=FakePolicy())

    first = await service.assess_terminal_work()
    second = await service.assess_terminal_work()

    assert len(first["remediation"]["created"]) == 1
    assert second["remediation"]["created"] == []
    async with outcome_session_factory() as session:
        request = (await session.execute(select(OutsourcingRequest))).scalar_one()
    assert request.source_type == "repeated_agent_failure"
    assert "open-source" in " ".join(request.foss_constraints)
    assert "no-secret" in " ".join(request.security_constraints)
