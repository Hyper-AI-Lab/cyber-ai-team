from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import Base
from cyber_team.db.models import (
    ActionClassPolicy,
    Agent,
    AuditEvent,
    Workflow,
    WorkflowSpecification,
)
from cyber_team.operations import action_policy as policy_module
from cyber_team.operations.action_policy import ActionPolicyService
from cyber_team.tools.registry import ToolDefinition, ToolRegistry
from cyber_team.workflows import compiler as compiler_module
from cyber_team.workflows.compiler import WorkflowCompilerService


class FakeResponse:
    status_code = 200

    def __init__(self, result):
        self._result = result

    def json(self):
        return {"result": self._result}


class FakeOPAClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, _url, *, json, timeout):
        assert timeout == 2.0
        data = json["input"]
        reasons = list(data["hard_gate_reasons"])
        injection = "prompt_injection_quarantine" in reasons
        requires = bool(reasons) and not injection
        allowed = not reasons or (requires and data["approval_present"])
        return FakeResponse(
            {
                "allowed": allowed,
                "requires_approval": requires,
                "reasons": reasons,
            }
        )


class FailingOPAClient:
    async def __aenter__(self):
        raise OSError("OPA unavailable")

    async def __aexit__(self, *_args):
        return None


@pytest.fixture
async def compiler_session_factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(policy_module, "async_session", factory)
    monkeypatch.setattr(compiler_module, "async_session", factory)
    monkeypatch.setattr(settings, "company_namespace", "company:test")
    try:
        yield factory
    finally:
        await engine.dispose()


def envelope(action_class="research", **overrides):
    value = {
        "action_class": action_class,
        "actor": "knowledge-agent",
        "actor_type": "agent",
        "target_type": "tool",
        "target_id": "safe_research",
        "expected_effect": "Create an internal evidence summary.",
        "evidence_ids": ["evidence-1"],
        "confidence": 0.9,
        "reversible": True,
        "external_side_effect": False,
        "fresh_backup": True,
        "observer_status": "agreed",
        "benchmark_fresh": True,
        "memory_coverage_fresh": True,
    }
    value.update(overrides)
    return value


def workflow_spec(tool_name="safe_research", *, cycle=False, side_effect=False):
    first_dependencies = ["remember"] if cycle else []
    return {
        "schema_version": "1.0",
        "trigger": {"type": "manual", "config": {}},
        "agents": ["knowledge-agent"],
        "tools": [tool_name],
        "steps": [
            {
                "id": "research",
                "type": "tool",
                "tool_name": tool_name,
                "depends_on": first_dependencies,
                "args_template": {},
                "risk_level": "medium" if side_effect else "low",
                "action_envelope": envelope(
                    "external_communication" if side_effect else "research",
                    external_side_effect=side_effect,
                    reversible=not side_effect,
                    fresh_backup=not side_effect,
                ),
            },
            {
                "id": "remember",
                "type": "agent",
                "agent_id": "knowledge-agent",
                "task_template": "Review {research_output}",
                "depends_on": ["research"],
            },
        ],
        "acceptance_tests": [
            {"type": "state_key_exists", "state_key": "remember_output"}
        ],
        "metrics": ["evidence_coverage"],
        "approval_policy": {"mode": "policy_gated"},
    }


async def seed_agent(factory):
    async with factory() as session:
        session.add(
            Agent(
                id="knowledge-agent",
                role_family="knowledge",
                role_name="Knowledge Agent",
                instructions="Research and cite evidence.",
                tools=["safe_research"],
                memory_namespace="company:test:knowledge",
                status="active",
            )
        )
        await session.commit()


def compiler_services(*, opa_client=FakeOPAClient):
    registry = ToolRegistry()

    async def execute_tool():
        return {"status": "completed"}

    registry.register(
        ToolDefinition(
            name="safe_research",
            description="Create an internal evidence summary.",
            executor_kind="advisory",
        ),
        execute_tool,
    )
    registry.register(
        ToolDefinition(
            name="live_send",
            description="Send an external test communication.",
            category="communications",
            executor_kind="live",
            side_effects=True,
            risk_level="medium",
        ),
        execute_tool,
    )
    policy = ActionPolicyService(client_factory=opa_client)
    return policy, WorkflowCompilerService(
        tool_registry=registry,
        action_policy_service=policy,
    )


@pytest.mark.asyncio
async def test_policy_fails_closed_when_opa_is_unavailable(compiler_session_factory):
    service = ActionPolicyService(client_factory=FailingOPAClient)

    decision = await service.evaluate(envelope())

    assert decision["allowed"] is False
    assert decision["requires_approval"] is False
    assert decision["reasons"] == ["opa_unavailable_fail_closed"]


@pytest.mark.asyncio
async def test_permanent_gate_requires_approval_even_below_threshold(
    compiler_session_factory,
):
    service = ActionPolicyService(client_factory=FakeOPAClient)
    blocked = await service.evaluate(envelope("payment"))
    approved = await service.evaluate(envelope("payment"), approval_present=True)

    assert blocked["requires_approval"] is True
    assert blocked["allowed"] is False
    assert "permanent_owner_gate" in blocked["reasons"]
    assert approved["allowed"] is True


@pytest.mark.asyncio
async def test_policy_calculates_daily_financial_exposure_from_audit_history(
    compiler_session_factory,
):
    async with compiler_session_factory() as session:
        session.add(
            AuditEvent(
                id="audit-financial-exposure",
                event_type="action_policy.evaluated",
                actor="finance-agent",
                actor_type="agent",
                resource_type="action_envelope",
                action="reversible_purchase",
                outcome="success",
                metadata_={
                    "decision": {
                        "envelope": {"financial_exposure_usd": 1900.0}
                    }
                },
                created_at=utc_now(),
            )
        )
        await session.commit()
    service = ActionPolicyService(client_factory=FakeOPAClient)

    decision = await service.evaluate(
        envelope("research", financial_exposure_usd=200.0)
    )

    assert decision["allowed"] is False
    assert "financial_daily_limit_exceeded" in decision["reasons"]
    assert decision["envelope"]["financial_daily_usd"] == 2100.0


@pytest.mark.asyncio
async def test_action_class_promotes_only_after_all_shadow_gates(
    compiler_session_factory,
    monkeypatch,
):
    monkeypatch.setattr(settings, "action_policy_shadow_days", 7)
    monkeypatch.setattr(settings, "action_policy_min_validated_cases", 10)
    monkeypatch.setattr(settings, "action_policy_min_evaluator_score", 0.8)
    async with compiler_session_factory() as session:
        session.add(
            ActionClassPolicy(
                id="policy-shadow",
                action_class="external_communication",
                version=1,
                status="shadow",
                permanent_gate=False,
                auto_execute_enabled=False,
                thresholds=ActionPolicyService.default_thresholds(),
                validated_cases=9,
                hard_policy_compliance=1.0,
                evaluator_score=0.9,
                high_severity_findings=0,
                shadow_started_at=utc_now() - timedelta(days=8),
            )
        )
        await session.commit()
    service = ActionPolicyService(client_factory=FakeOPAClient)

    result = await service.record_validated_case(
        "external_communication", compliant=True, evaluator_score=0.9
    )

    assert result["status"] == "active"
    assert result["auto_execute_enabled"] is True
    assert result["validated_cases"] == 10


@pytest.mark.asyncio
async def test_safe_spec_activates_and_deduplicates(compiler_session_factory):
    await seed_agent(compiler_session_factory)
    _policy, compiler = compiler_services()

    first = await compiler.propose(
        spec_key="knowledge_research",
        title="Knowledge research loop",
        specification=workflow_spec(),
        source_type="business_work_item",
    )
    second = await compiler.propose(
        spec_key="knowledge_research",
        title="Knowledge research loop",
        specification=workflow_spec(),
        source_type="business_work_item",
    )

    assert first["status"] == "active"
    assert first["sandbox_result"]["valid"] is True
    assert second["duplicate"] is True
    async with compiler_session_factory() as session:
        assert (
            await session.execute(select(func.count(Workflow.id)))
        ).scalar_one() == 1
        assert (
            await session.execute(select(func.count(WorkflowSpecification.id)))
        ).scalar_one() == 1


@pytest.mark.asyncio
async def test_side_effect_spec_stops_at_owner_approval(compiler_session_factory):
    await seed_agent(compiler_session_factory)
    _policy, compiler = compiler_services()

    result = await compiler.propose(
        spec_key="external_send",
        title="External communication",
        specification=workflow_spec("live_send", side_effect=True),
        source_type="business_work_item",
    )

    assert result["status"] == "approval_required"
    assert result["sandbox_result"]["approval_required"] is True
    assert result["sandbox_result"]["workflow_id"] is None


@pytest.mark.asyncio
async def test_dependency_cycle_is_blocked(compiler_session_factory):
    await seed_agent(compiler_session_factory)
    _policy, compiler = compiler_services()

    result = await compiler.propose(
        spec_key="cycle",
        title="Cyclic workflow",
        specification=workflow_spec(cycle=True),
        source_type="test",
    )

    assert result["status"] == "blocked"
    assert any(
        error.startswith("dependency_cycle")
        for error in result["sandbox_result"]["errors"]
    )
