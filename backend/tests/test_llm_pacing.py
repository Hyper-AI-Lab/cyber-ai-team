from unittest.mock import AsyncMock, MagicMock

import pytest

from cyber_team.llm.pacing import (
    HostedCredentialRotator,
    HostedInferencePacer,
    LLMCredentialCoordinationUnavailableError,
    LLMCredentialPoolExhaustedError,
    LLMInferencePacingUnavailableError,
    LLMInferenceQueueCapacityError,
)
from cyber_team.llm.resilience import classify_llm_exception


@pytest.mark.asyncio
async def test_pacer_reserves_and_waits_for_shared_slot():
    redis = AsyncMock()
    redis.eval.return_value = [1, 1250]
    sleep = AsyncMock()
    pacer = HostedInferencePacer(
        enabled=True,
        min_interval_seconds=20,
        max_queue_wait_seconds=300,
        redis_client=redis,
        sleep=sleep,
    )

    result = await pacer.acquire(
        provider="mistral",
        model="mistral/mistral-medium-3-5",
    )

    assert result["outcome"] == "acquired"
    assert result["wait_seconds"] == 1.25
    sleep.assert_awaited_once_with(1.25)
    args = redis.eval.await_args.args
    assert args[1] == 1
    assert args[2].startswith("cyberteam:llm:hosted-pacing:")
    assert args[3:] == (20000, 300000)


@pytest.mark.asyncio
async def test_pacer_rejects_queue_beyond_bound_without_sleeping():
    redis = AsyncMock()
    redis.eval.return_value = [-1, 320000]
    sleep = AsyncMock()
    pacer = HostedInferencePacer(
        enabled=True,
        min_interval_seconds=20,
        max_queue_wait_seconds=300,
        redis_client=redis,
        sleep=sleep,
    )

    with pytest.raises(LLMInferenceQueueCapacityError):
        await pacer.acquire(provider="mistral", model="mistral/test")

    sleep.assert_not_awaited()
    assert pacer.status()["last_acquisition"]["category"] == "queue_capacity"


@pytest.mark.asyncio
async def test_pacer_fails_closed_when_redis_is_unavailable():
    redis = AsyncMock()
    redis.eval.side_effect = OSError("connection refused")
    pacer = HostedInferencePacer(enabled=True, redis_client=redis)

    with pytest.raises(LLMInferencePacingUnavailableError) as error:
        await pacer.acquire(provider="mistral", model="mistral/test")

    assert classify_llm_exception(error.value) == "provider_unavailable"


@pytest.mark.asyncio
async def test_disabled_pacer_does_not_touch_redis():
    redis = AsyncMock()
    pacer = HostedInferencePacer(enabled=False, redis_client=redis)

    result = await pacer.acquire(provider="mistral", model="mistral/test")

    assert result["enabled"] is False
    redis.eval.assert_not_awaited()


def test_pacer_uses_one_provider_scope_across_models():
    assert HostedInferencePacer._coordination_key(
        provider="mistral",
        model="mistral/large",
    ) == HostedInferencePacer._coordination_key(
        provider="mistral",
        model="mistral/small",
    )


@pytest.mark.asyncio
async def test_credential_rotator_uses_shared_round_robin_sequence():
    redis = AsyncMock()
    redis.eval.side_effect = [
        [slot, sequence, 5, 5]
        for sequence, slot in enumerate([1, 2, 3, 4, 5, 1, 2], start=1)
    ]
    rotator = HostedCredentialRotator(redis_client=redis)

    selections = [
        await rotator.select(provider="mistral", credential_count=5)
        for _ in range(7)
    ]

    assert [item["credential_slot"] for item in selections] == [1, 2, 3, 4, 5, 1, 2]
    assert all(item["credential_count"] == 5 for item in selections)
    assert all(item["healthy_count"] == 5 for item in selections)
    assert redis.eval.await_count == 7
    assert redis.eval.await_args.args[2].startswith(
        "cyberteam:llm:credential-round-robin:"
    )


@pytest.mark.asyncio
async def test_single_credential_does_not_require_redis_coordination():
    redis = AsyncMock()
    rotator = HostedCredentialRotator(redis_client=redis)

    selection = await rotator.select(provider="mistral", credential_count=1)

    assert selection["credential_slot"] == 1
    redis.eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_credential_rotator_fails_closed_without_redis():
    redis = AsyncMock()
    redis.eval.side_effect = OSError("connection refused")
    rotator = HostedCredentialRotator(redis_client=redis)

    with pytest.raises(LLMCredentialCoordinationUnavailableError) as error:
        await rotator.select(provider="mistral", credential_count=5)

    assert classify_llm_exception(error.value) == "provider_unavailable"
    assert rotator.status()["last_selection"]["category"] == "coordination_unavailable"


@pytest.mark.asyncio
async def test_credential_rotator_selects_only_shared_healthy_slots():
    redis = AsyncMock()
    redis.eval.return_value = [4, 12, 5, 3]
    rotator = HostedCredentialRotator(redis_client=redis)

    selection = await rotator.select(provider="mistral", credential_count=5)

    assert selection["credential_slot"] == 4
    assert selection["healthy_count"] == 3
    assert selection["sequence"] == 12
    args = redis.eval.await_args.args
    assert args[1] == 6
    assert len(args[2:]) == 6
    assert all("credential-quarantine" in key for key in args[3:])


@pytest.mark.asyncio
async def test_credential_rotator_reports_exhausted_shared_pool():
    redis = AsyncMock()
    redis.eval.return_value = [-1, 9, 5, 0]
    rotator = HostedCredentialRotator(redis_client=redis)

    with pytest.raises(LLMCredentialPoolExhaustedError):
        await rotator.select(provider="mistral", credential_count=5)

    status = rotator.status()["last_selection"]
    assert status["category"] == "capacity_exhausted"
    assert status["healthy_count"] == 0


@pytest.mark.asyncio
async def test_credential_rotator_quarantine_stores_category_not_secret(monkeypatch):
    monkeypatch.setattr(
        "cyber_team.llm.pacing.settings.llm_hosted_credential_quarantine_seconds",
        600,
    )
    redis = AsyncMock()
    rotator = HostedCredentialRotator(redis_client=redis)

    await rotator.quarantine(
        provider="mistral",
        credential_slot=2,
        category="capacity_exhausted",
    )

    args = redis.set.await_args.args
    kwargs = redis.set.await_args.kwargs
    assert "credential-quarantine" in args[0]
    assert args[0].endswith(":2")
    assert args[1] == "capacity_exhausted"
    assert kwargs == {"ex": 600}
    assert "secret-key-value" not in repr(rotator.status())


@pytest.mark.asyncio
async def test_credential_rotator_sync_health_quarantines_failures_and_recovers_others():
    redis = AsyncMock()
    pipeline = MagicMock()
    pipeline.execute = AsyncMock(return_value=[])
    redis.pipeline = MagicMock(return_value=pipeline)
    rotator = HostedCredentialRotator(redis_client=redis)

    await rotator.sync_health(
        provider="mistral",
        credential_count=3,
        failed_slots={2: "capacity_exhausted"},
        cooldown_seconds=300,
    )

    assert pipeline.delete.call_count == 2
    pipeline.set.assert_called_once()
    assert pipeline.set.call_args.args[1] == "capacity_exhausted"
    assert pipeline.set.call_args.kwargs == {"ex": 300}
    pipeline.execute.assert_awaited_once()
    assert rotator.status()["last_health_update"]["healthy_count"] == 2
