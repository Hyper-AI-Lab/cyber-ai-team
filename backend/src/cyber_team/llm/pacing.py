"""Distributed pacing for hosted LLM completion requests."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

from redis.asyncio import Redis

from cyber_team.clock import utc_now
from cyber_team.config import settings


class LLMInferencePacingUnavailableError(ConnectionError):
    """Raised when hosted inference cannot be coordinated safely."""

    status_code = 503


class LLMInferenceQueueCapacityError(RuntimeError):
    """Raised when the coordinated hosted-inference queue is already too deep."""

    status_code = 429


_RESERVE_SLOT_SCRIPT = """
local time_parts = redis.call('TIME')
local now_ms = (tonumber(time_parts[1]) * 1000) + math.floor(tonumber(time_parts[2]) / 1000)
local interval_ms = tonumber(ARGV[1])
local max_wait_ms = tonumber(ARGV[2])
local next_ms = tonumber(redis.call('GET', KEYS[1]) or now_ms)
if next_ms < now_ms then
  next_ms = now_ms
end
local wait_ms = next_ms - now_ms
if wait_ms > max_wait_ms then
  return {-1, wait_ms}
end
local reserved_until_ms = next_ms + interval_ms
local ttl_ms = math.ceil(wait_ms + interval_ms + 60000)
redis.call('SET', KEYS[1], reserved_until_ms, 'PX', ttl_ms)
return {1, wait_ms}
"""


class HostedInferencePacer:
    """Reserve globally spaced completion slots through Redis.

    API and Temporal worker processes share the same key. A reservation is made
    before sleeping, so concurrently arriving callers form one bounded queue.
    Redis coordination failures stop hosted inference instead of allowing an
    uncoordinated request burst.
    """

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        min_interval_seconds: float | None = None,
        max_queue_wait_seconds: float | None = None,
        redis_client: Any | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.enabled = (
            settings.llm_hosted_pacing_enabled if enabled is None else enabled
        )
        self.min_interval_seconds = max(
            0.0,
            settings.llm_hosted_min_interval_seconds
            if min_interval_seconds is None
            else min_interval_seconds,
        )
        self.max_queue_wait_seconds = max(
            self.min_interval_seconds,
            settings.llm_hosted_max_queue_wait_seconds
            if max_queue_wait_seconds is None
            else max_queue_wait_seconds,
        )
        self._redis = redis_client
        self._owns_redis = redis_client is None
        self._sleep = sleep
        self._last_acquisition: dict[str, Any] | None = None

    async def acquire(self, *, provider: str, model: str) -> dict[str, Any]:
        """Reserve and wait for one hosted completion slot."""
        if not self.enabled or self.min_interval_seconds <= 0:
            result = {
                "enabled": False,
                "wait_seconds": 0.0,
                "acquired_at": utc_now().isoformat(),
            }
            self._last_acquisition = result
            return result

        redis = self._redis_client()
        interval_ms = max(1, round(self.min_interval_seconds * 1000))
        max_wait_ms = max(interval_ms, round(self.max_queue_wait_seconds * 1000))
        key = self._coordination_key(provider=provider, model=model)
        try:
            reservation = await redis.eval(
                _RESERVE_SLOT_SCRIPT,
                1,
                key,
                interval_ms,
                max_wait_ms,
            )
        except Exception as exc:
            self._last_acquisition = {
                "enabled": True,
                "outcome": "failed",
                "category": "coordination_unavailable",
                "at": utc_now().isoformat(),
            }
            raise LLMInferencePacingUnavailableError(
                "Hosted LLM pacing coordination is unavailable."
            ) from exc

        accepted = int(reservation[0]) == 1
        wait_seconds = max(0.0, int(reservation[1]) / 1000)
        if not accepted:
            self._last_acquisition = {
                "enabled": True,
                "outcome": "rejected",
                "category": "queue_capacity",
                "wait_seconds": wait_seconds,
                "at": utc_now().isoformat(),
            }
            raise LLMInferenceQueueCapacityError(
                "Hosted LLM pacing queue is at capacity; retry later."
            )
        if wait_seconds:
            await self._sleep(wait_seconds)
        result = {
            "enabled": True,
            "outcome": "acquired",
            "wait_seconds": wait_seconds,
            "acquired_at": utc_now().isoformat(),
        }
        self._last_acquisition = result
        return result

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "min_interval_seconds": self.min_interval_seconds,
            "max_queue_wait_seconds": self.max_queue_wait_seconds,
            "coordination": "redis" if self.enabled else "disabled",
            "last_acquisition": self._last_acquisition,
        }

    async def close(self) -> None:
        if self._redis is not None and self._owns_redis:
            await self._redis.aclose()
        self._redis = None

    def _redis_client(self) -> Any:
        if self._redis is None:
            self._redis = Redis.from_url(settings.redis_url, decode_responses=True)
        return self._redis

    @staticmethod
    def _coordination_key(*, provider: str, model: str) -> str:
        # Hosted limits are commonly account/organization-wide across models.
        # Keep the model argument for call-site observability without splitting
        # the provider-wide coordination scope.
        del model
        identity = provider.strip().lower()
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        return f"cyberteam:llm:hosted-pacing:{digest}"
