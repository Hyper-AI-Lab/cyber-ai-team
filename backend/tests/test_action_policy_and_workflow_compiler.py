from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import Base
from cyber_team.db.models import (
    ActionClassPolicy,
    ActionPolicyValidationCase,
    Agent,
    ApprovalRequest,
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
        "acceptance_tests": [{"type": "state_key_exists", "state_key": "remember_output"}],
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
                metadata_={"decision": {"envelope": {"financial_exposure_usd": 1900.0}}},
                created_at=utc_now(),
            )
        )
        await session.commit()
    service = ActionPolicyService(client_factory=FakeOPAClient)

    decision = await service.evaluate(envelope("research", financial_exposure_usd=200.0))

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
    monkeypatch.setattr(settings, "action_policy_min_live_canaries", 1)
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
        "external_communication",
        compliant=True,
        evaluator_score=0.9,
        execution_mode="live_canary",
    )

    assert result["status"] == "active"
    assert result["auto_execute_enabled"] is True
    assert result["validated_cases"] == 10
    assert result["live_canary_cases"] == 1


@pytest.mark.asyncio
async def test_action_class_does_not_promote_without_live_canary(
    compiler_session_factory,
    monkeypatch,
):
    monkeypatch.setattr(settings, "action_policy_shadow_days", 0)
    monkeypatch.setattr(settings, "action_policy_min_validated_cases", 10)
    monkeypatch.setattr(settings, "action_policy_min_evaluator_score", 0.8)
    monkeypatch.setattr(settings, "action_policy_min_live_canaries", 1)
    async with compiler_session_factory() as session:
        session.add(
            ActionClassPolicy(
                id="policy-shadow-no-canary",
                action_class="communications",
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
                metadata_={"shadow_validated_cases": 9, "live_canary_cases": 0},
            )
        )
        await session.commit()
    service = ActionPolicyService(client_factory=FakeOPAClient)

    result = await service.record_validated_case(
        "communications",
        compliant=True,
        evaluator_score=1.0,
        execution_mode="shadow",
    )

    assert result["status"] == "shadow"
    assert result["auto_execute_enabled"] is False
    assert result["validated_cases"] == 10
    assert result["shadow_validated_cases"] == 10
    assert result["live_canary_cases"] == 0


@pytest.mark.asyncio
async def test_shadow_suite_is_durable_idempotent_and_side_effect_free(
    compiler_session_factory,
    monkeypatch,
):
    monkeypatch.setattr(settings, "action_policy_shadow_days", 0)
    monkeypatch.setattr(settings, "action_policy_min_validated_cases", 10)
    monkeypatch.setattr(settings, "action_policy_min_evaluator_score", 0.8)
    monkeypatch.setattr(settings, "action_policy_min_live_canaries", 1)
    service = ActionPolicyService(client_factory=FakeOPAClient)

    first = await service.generate_shadow_suite("communications")
    second = await service.generate_shadow_suite("communications")
    cases = await service.list_validation_cases(action_class="communications")

    assert first["case_count"] == 10
    assert first["validated_count"] == 10
    assert first["duplicate_count"] == 0
    assert second["duplicate_count"] == 10
    assert len(cases) == 10
    assert all(item["mode"] == "shadow" for item in cases)
    assert all(item["external_side_effect_executed"] is False for item in cases)
    assert all(item["compliant"] is True for item in cases)
    assert all(item["counted_at"] for item in cases)
    assert first["policy"]["status"] == "shadow"
    assert first["policy"]["validated_cases"] == 10
    assert first["policy"]["live_canary_cases"] == 0
    async with compiler_session_factory() as session:
        stored_count = await session.scalar(select(func.count(ActionPolicyValidationCase.id)))
    assert stored_count == 10


@pytest.mark.asyncio
async def test_live_canary_replays_approved_payload_and_promotes_from_durable_case(
    compiler_session_factory,
    monkeypatch,
):
    monkeypatch.setattr(settings, "action_policy_shadow_days", 0)
    monkeypatch.setattr(settings, "action_policy_min_validated_cases", 1)
    monkeypatch.setattr(settings, "action_policy_min_evaluator_score", 0.8)
    monkeypatch.setattr(settings, "action_policy_min_live_canaries", 1)
    service = ActionPolicyService(client_factory=FakeOPAClient)
    canary_envelope = envelope(
        "communications",
        actor="communications-agent",
        target_id="send_email",
        expected_effect="Send one synthetic canary email.",
        external_side_effect=True,
        recipients=1,
        data_sensitivity="synthetic",
        confidence=0.95,
    )
    params = {
        "to_address": "owner-canary@example.com",
        "subject": "[Cyber-Team Canary] policy validation",
        "body": "Synthetic policy canary.",
    }
    staged = await service.stage_live_canary_case(
        "communications",
        scenario_key="one_recipient_email",
        action_envelope=canary_envelope,
        payload_summary={"recipient_count": 1},
        execution_request={
            "agent_id": "communications-agent",
            "tool_name": "send_email",
            "params": params,
        },
        observer_review={"id": "observer-1", "status": "agreed"},
        actor="owner@example.com",
        validation_case_id="actcase-live-email",
    )
    normalized = service.normalize_envelope(canary_envelope)
    binding = ToolRegistry._approval_binding("send_email", params, normalized)
    async with compiler_session_factory() as session:
        stored = await session.get(ActionPolicyValidationCase, staged["id"])
        assert stored.execution_request == {
            "agent_id": "communications-agent",
            "tool_name": "send_email",
            "params_hash": ToolRegistry._stable_hash(params),
        }
        session.add(
            ApprovalRequest(
                id="approval-live-email",
                agent_id="communications-agent",
                action_type="tool:send_email",
                action_description="Execute synthetic canary.",
                action_payload={
                    "tool_name": "send_email",
                    "params": params,
                    "approval_binding": binding,
                },
                requester="communications-agent",
                requester_type="agent",
                risk_level="high",
                target_type="tool",
                target_id="send_email",
                status="approved",
                reviewer="owner@example.com",
                resolved_at=utc_now(),
            )
        )
        await session.commit()
    attached = await service.attach_live_canary_approval(
        staged["id"],
        approval_id="approval-live-email",
        approval_binding=binding,
        actor="owner@example.com",
    )
    replay = await service.get_live_canary_execution_request(staged["id"])
    executed = await service.record_live_canary_execution(
        staged["id"],
        execution_result={"success": True, "output": {"status": "sent"}},
        actor="owner@example.com",
    )
    adjudicated = await service.adjudicate_live_canary(
        staged["id"],
        compliant=True,
        evaluator_score=1.0,
        note="Owner confirmed delivery.",
        actor="owner@example.com",
    )
    cases = await service.list_validation_cases(action_class="communications")

    assert attached["status"] == "awaiting_owner_approval"
    assert replay["execution_request"]["params"] == params
    assert executed["status"] == "pending_owner_adjudication"
    assert adjudicated["status"] == "validated"
    assert adjudicated["policy"]["status"] == "active"
    assert adjudicated["policy"]["live_canary_cases"] == 1
    assert len(cases[0]["events"]) == 5
    assert "execution_request" not in cases[0]
    assert cases[0]["external_side_effect_executed"] is True


@pytest.mark.asyncio
async def test_live_canary_refreshes_expired_approval_without_new_case(
    compiler_session_factory,
):
    service = ActionPolicyService(client_factory=FakeOPAClient)
    canary_envelope = envelope(
        "erpnext",
        actor="product-agent",
        target_id="task_create",
        external_side_effect=True,
        data_sensitivity="synthetic",
    )
    params = {"task_data": {"subject": "[CYBERTEAM-CANARY] retry"}}
    execution_request = {
        "agent_id": "product-agent",
        "tool_name": "task_create",
        "params": params,
    }
    observer_review = {"id": "observer-retry", "status": "agreed"}
    staged = await service.stage_live_canary_case(
        "erpnext",
        scenario_key="synthetic_task_retry",
        action_envelope=canary_envelope,
        payload_summary={"record_count": 1},
        execution_request=execution_request,
        observer_review=observer_review,
        actor="owner@example.com",
    )
    binding = ToolRegistry._approval_binding(
        "task_create",
        params,
        service.normalize_envelope(canary_envelope),
    )
    async with compiler_session_factory() as session:
        session.add(
            ApprovalRequest(
                id="approval-expired-canary",
                agent_id="product-agent",
                action_type="tool:task_create",
                action_description="Execute synthetic canary.",
                action_payload={
                    "tool_name": "task_create",
                    "params": params,
                    "approval_binding": binding,
                },
                requester="product-agent",
                requester_type="agent",
                risk_level="medium",
                target_type="tool",
                target_id="task_create",
                status="expired",
                expires_at=utc_now() - timedelta(minutes=1),
                resolved_at=utc_now(),
            )
        )
        await session.commit()
    await service.attach_live_canary_approval(
        staged["id"],
        approval_id="approval-expired-canary",
        approval_binding=binding,
        actor="owner@example.com",
    )

    retried = await service.stage_live_canary_case(
        "erpnext",
        scenario_key="synthetic_task_retry",
        action_envelope=canary_envelope,
        payload_summary={"record_count": 1},
        execution_request=execution_request,
        observer_review=observer_review,
        actor="owner@example.com",
    )
    cases = await service.list_validation_cases(action_class="erpnext")

    assert retried["duplicate"] is True
    assert retried["id"] == staged["id"]
    assert retried["status"] == "approval_required"
    assert retried["approval_id"] is None
    assert len(cases) == 1
    assert cases[0]["events"][-1]["event_type"] == "approval_refresh_required"
    async with compiler_session_factory() as session:
        stored = await session.get(ActionPolicyValidationCase, staged["id"])
        assert "approval_binding" not in stored.execution_request


@pytest.mark.asyncio
async def test_live_canary_requires_reconciliation_for_consumed_approval(
    compiler_session_factory,
):
    service = ActionPolicyService(client_factory=FakeOPAClient)
    canary_envelope = envelope(
        "communications",
        actor="communications-agent",
        target_id="send_email",
        external_side_effect=True,
        recipients=1,
        data_sensitivity="synthetic",
    )
    params = {
        "to_address": "owner-canary@example.com",
        "subject": "[Cyber-Team Canary] reconciliation",
        "body": "Synthetic policy canary.",
    }
    execution_request = {
        "agent_id": "communications-agent",
        "tool_name": "send_email",
        "params": params,
    }
    observer_review = {"id": "observer-reconciliation", "status": "agreed"}
    staged = await service.stage_live_canary_case(
        "communications",
        scenario_key="consumed_approval_reconciliation",
        action_envelope=canary_envelope,
        payload_summary={"recipient_count": 1},
        execution_request=execution_request,
        observer_review=observer_review,
        actor="owner@example.com",
    )
    binding = ToolRegistry._approval_binding(
        "send_email",
        params,
        service.normalize_envelope(canary_envelope),
    )
    async with compiler_session_factory() as session:
        session.add(
            ApprovalRequest(
                id="approval-consumed-canary",
                agent_id="communications-agent",
                action_type="tool:send_email",
                action_description="Execute synthetic canary.",
                action_payload={
                    "tool_name": "send_email",
                    "params": params,
                    "approval_binding": binding,
                },
                requester="communications-agent",
                requester_type="agent",
                risk_level="high",
                target_type="tool",
                target_id="send_email",
                status="approved",
                reviewer="owner@example.com",
                resolved_at=utc_now(),
                consumed_at=utc_now(),
            )
        )
        await session.commit()
    await service.attach_live_canary_approval(
        staged["id"],
        approval_id="approval-consumed-canary",
        approval_binding=binding,
        actor="owner@example.com",
    )

    retried = await service.stage_live_canary_case(
        "communications",
        scenario_key="consumed_approval_reconciliation",
        action_envelope=canary_envelope,
        payload_summary={"recipient_count": 1},
        execution_request=execution_request,
        observer_review=observer_review,
        actor="owner@example.com",
    )

    assert retried["duplicate"] is True
    assert retried["status"] == "execution_reconciliation_required"
    assert retried["approval_id"] == "approval-consumed-canary"


@pytest.mark.asyncio
async def test_live_canary_rejects_tampered_approval_binding(
    compiler_session_factory,
):
    service = ActionPolicyService(client_factory=FakeOPAClient)
    canary_envelope = envelope(
        "erpnext",
        actor="product-agent",
        target_id="task_create",
        external_side_effect=True,
        data_sensitivity="synthetic",
    )
    staged = await service.stage_live_canary_case(
        "erpnext",
        scenario_key="synthetic_task",
        action_envelope=canary_envelope,
        payload_summary={"record_count": 1},
        execution_request={
            "agent_id": "product-agent",
            "tool_name": "task_create",
            "params": {"task_data": {"subject": "[CYBERTEAM-CANARY] test"}},
        },
        observer_review={"id": "observer-2", "status": "agreed"},
        actor="owner@example.com",
    )

    with pytest.raises(ValueError, match="parameters do not match"):
        await service.attach_live_canary_approval(
            staged["id"],
            approval_id="approval-tampered",
            approval_binding={
                "params_hash": "wrong",
                "action_envelope_hash": service._hash(
                    service.normalize_envelope(canary_envelope)
                ),
                "request_hash": "wrong",
            },
            actor="owner@example.com",
        )


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
        assert (await session.execute(select(func.count(Workflow.id)))).scalar_one() == 1
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
    assert any(error.startswith("dependency_cycle") for error in result["sandbox_result"]["errors"])
