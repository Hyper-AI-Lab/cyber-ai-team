from unittest.mock import AsyncMock

import pytest

from cyber_team.llm.pacing import (
    HostedCredentialRotator,
    HostedInferencePacer,
    LLMCredentialCoordinationUnavailableError,
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
        model="mistral/mistral-large-latest",
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
    redis.incr.side_effect = [1, 2, 3, 4, 5, 6, 7]
    rotator = HostedCredentialRotator(redis_client=redis)

    selections = [
        await rotator.select(provider="mistral", credential_count=5)
        for _ in range(7)
    ]

    assert [item["credential_slot"] for item in selections] == [1, 2, 3, 4, 5, 1, 2]
    assert all(item["credential_count"] == 5 for item in selections)
    assert redis.incr.await_count == 7
    assert redis.incr.await_args.args[0].startswith(
        "cyberteam:llm:credential-round-robin:"
    )


@pytest.mark.asyncio
async def test_single_credential_does_not_require_redis_coordination():
    redis = AsyncMock()
    rotator = HostedCredentialRotator(redis_client=redis)

    selection = await rotator.select(provider="mistral", credential_count=1)

    assert selection["credential_slot"] == 1
    redis.incr.assert_not_awaited()


@pytest.mark.asyncio
async def test_credential_rotator_fails_closed_without_redis():
    redis = AsyncMock()
    redis.incr.side_effect = OSError("connection refused")
    rotator = HostedCredentialRotator(redis_client=redis)

    with pytest.raises(LLMCredentialCoordinationUnavailableError) as error:
        await rotator.select(provider="mistral", credential_count=5)

    assert classify_llm_exception(error.value) == "provider_unavailable"
    assert rotator.status()["last_selection"]["category"] == "coordination_unavailable"
