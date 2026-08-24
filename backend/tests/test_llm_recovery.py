from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cyber_team.operations.llm_recovery import LLMProviderRecoveryService


def blocking_health(category="rate_limited"):
    return {
        "status": category,
        "blocking": True,
        "last_failure_category": category,
        "last_failure_at": "2026-08-24T10:00:00+00:00",
    }


def make_service(*, health=None, redis_claim=True, llm_result="READY"):
    llm = AsyncMock()
    llm.invoke.return_value = llm_result
    memory = AsyncMock()
    memory.record_trace.return_value = {"id": "trace-recovery"}
    steward = AsyncMock()
    steward.llm_provider_health.return_value = health or blocking_health()
    audit = AsyncMock()
    redis = AsyncMock()
    redis.set.return_value = redis_claim
    service = LLMProviderRecoveryService(
        llm_gateway=llm,
        memory_service=memory,
        memory_steward_service=steward,
        audit_service=audit,
        redis_client=redis,
    )
    return SimpleNamespace(
        service=service,
        llm=llm,
        memory=memory,
        steward=steward,
        audit=audit,
        redis=redis,
    )


@pytest.mark.asyncio
async def test_recovery_probe_records_real_success_evidence():
    context = make_service()

    result = await context.service.run_once()

    assert result == {
        "status": "recovered",
        "attempted": True,
        "trigger_category": "rate_limited",
        "error_category": None,
        "trace_id": "trace-recovery",
        "checked_at": result["checked_at"],
    }
    context.llm.invoke.assert_awaited_once()
    trace_data = context.memory.record_trace.await_args.args[0]
    assert trace_data.source_type == "llm_provider_recovery_probe"
    assert trace_data.errors == []
    assert trace_data.metadata["result_excerpt"] == "READY"
    assert trace_data.metadata["side_effects"] is False
    assert context.redis.set.await_args.kwargs["nx"] is True
    context.audit.record.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_probe_is_deduplicated_during_cooldown():
    context = make_service(redis_claim=False)

    result = await context.service.run_once()

    assert result["status"] == "cooldown"
    assert result["attempted"] is False
    context.llm.invoke.assert_not_awaited()
    context.memory.record_trace.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_probe_does_not_retry_authentication_failure():
    context = make_service(health=blocking_health("authentication_error"))

    result = await context.service.run_once()

    assert result["status"] == "not_needed"
    context.redis.set.assert_not_awaited()
    context.llm.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_probe_persists_safe_failure_category():
    context = make_service()

    class RateLimitError(Exception):
        status_code = 429

    context.llm.invoke.side_effect = RateLimitError("provider payload omitted")

    result = await context.service.run_once()

    assert result["status"] == "failed"
    assert result["error_category"] == "rate_limited"
    trace_data = context.memory.record_trace.await_args.args[0]
    assert trace_data.errors == ["invoke:RateLimitError:rate_limited"]
    assert trace_data.metadata["failure_code"] == "rate_limited"
    assert "provider payload" not in str(trace_data.errors)
