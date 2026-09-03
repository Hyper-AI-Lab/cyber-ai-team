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


class LLMCredentialCoordinationUnavailableError(ConnectionError):
    """Raised when a multi-key hosted credential pool cannot rotate safely."""

    status_code = 503


class LLMCredentialPoolExhaustedError(RuntimeError):
    """Raised when every configured hosted credential is quarantined."""

    status_code = 402


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


_SELECT_HEALTHY_CREDENTIAL_SCRIPT = """
local sequence = tonumber(redis.call('INCR', KEYS[1]))
local healthy = {}
for index = 2, #KEYS do
  if not redis.call('GET', KEYS[index]) then
    table.insert(healthy, index - 1)
  end
end
if #healthy == 0 then
  return {-1, sequence, #KEYS - 1, 0}
end
local selected = healthy[((sequence - 1) % #healthy) + 1]
return {selected, sequence, #KEYS - 1, #healthy}
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


class HostedCredentialRotator:
    """Select healthy hosted credentials through one shared round robin.

    Core, Worker, and embedding callers use the same Redis sequence and
    quarantine markers. Credential values never enter Redis.
    """

    def __init__(self, *, redis_client: Any | None = None) -> None:
        self._redis = redis_client
        self._owns_redis = redis_client is None
        self._last_selection: dict[str, Any] | None = None
        self._last_health_update: dict[str, Any] | None = None

    async def select(self, *, provider: str, credential_count: int) -> dict[str, Any]:
        count = int(credential_count)
        if count < 1:
            raise ValueError("At least one hosted LLM credential is required.")
        if count == 1:
            result = self._selection(index=0, count=1)
            self._last_selection = result
            return result

        redis = self._redis_client()
        keys = [self._coordination_key(provider)] + [
            self._quarantine_key(provider, slot) for slot in range(1, count + 1)
        ]
        try:
            selected_slot, sequence, configured_count, healthy_count = [
                int(value)
                for value in await redis.eval(
                    _SELECT_HEALTHY_CREDENTIAL_SCRIPT,
                    len(keys),
                    *keys,
                )
            ]
        except Exception as exc:
            self._last_selection = {
                "strategy": "redis_healthy_round_robin",
                "outcome": "failed",
                "category": "coordination_unavailable",
                "credential_count": count,
                "at": utc_now().isoformat(),
            }
            raise LLMCredentialCoordinationUnavailableError(
                "Hosted LLM credential rotation coordination is unavailable."
            ) from exc

        if selected_slot < 1:
            self._last_selection = {
                "strategy": "redis_healthy_round_robin",
                "outcome": "failed",
                "category": "capacity_exhausted",
                "credential_count": configured_count,
                "healthy_count": healthy_count,
                "at": utc_now().isoformat(),
            }
            raise LLMCredentialPoolExhaustedError(
                "All configured hosted LLM credentials are quarantined."
            )

        result = self._selection(
            index=selected_slot - 1,
            count=configured_count,
            healthy_count=healthy_count,
            sequence=sequence,
        )
        self._last_selection = result
        return result

    async def quarantine(
        self,
        *,
        provider: str,
        credential_slot: int,
        category: str,
        cooldown_seconds: int | None = None,
    ) -> None:
        """Temporarily remove one credential slot without storing its value."""
        slot = max(1, int(credential_slot))
        cooldown = max(
            1,
            int(
                settings.llm_hosted_credential_quarantine_seconds
                if cooldown_seconds is None
                else cooldown_seconds
            ),
        )
        try:
            await self._redis_client().set(
                self._quarantine_key(provider, slot),
                category,
                ex=cooldown,
            )
        except Exception as exc:
            raise LLMCredentialCoordinationUnavailableError(
                "Hosted LLM credential quarantine coordination is unavailable."
            ) from exc
        self._last_health_update = {
            "outcome": "quarantined",
            "credential_slot": slot,
            "category": category,
            "cooldown_seconds": cooldown,
            "at": utc_now().isoformat(),
        }

    async def sync_health(
        self,
        *,
        provider: str,
        credential_count: int,
        failed_slots: dict[int, str],
        cooldown_seconds: int | None = None,
    ) -> None:
        """Publish validation health so every process uses the same eligible pool."""
        count = max(0, int(credential_count))
        if count <= 1:
            return
        cooldown = max(
            1,
            int(
                settings.llm_hosted_credential_quarantine_seconds
                if cooldown_seconds is None
                else cooldown_seconds
            ),
        )
        redis = self._redis_client()
        try:
            pipeline = redis.pipeline(transaction=True)
            for slot in range(1, count + 1):
                key = self._quarantine_key(provider, slot)
                category = failed_slots.get(slot)
                if category:
                    pipeline.set(key, category, ex=cooldown)
                else:
                    pipeline.delete(key)
            await pipeline.execute()
        except Exception as exc:
            raise LLMCredentialCoordinationUnavailableError(
                "Hosted LLM credential health coordination is unavailable."
            ) from exc
        self._last_health_update = {
            "outcome": "synchronized",
            "credential_count": count,
            "healthy_count": count - len(failed_slots),
            "quarantined_slots": sorted(failed_slots),
            "cooldown_seconds": cooldown,
            "at": utc_now().isoformat(),
        }

    def status(self) -> dict[str, Any]:
        return {
            "strategy": "redis_healthy_round_robin",
            "coordination": "redis",
            "last_selection": self._last_selection,
            "last_health_update": self._last_health_update,
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
    def _selection(
        *,
        index: int,
        count: int,
        healthy_count: int | None = None,
        sequence: int | None = None,
    ) -> dict[str, Any]:
        return {
            "strategy": "redis_healthy_round_robin",
            "outcome": "selected",
            "credential_slot": index + 1,
            "credential_index": index,
            "credential_count": count,
            "healthy_count": healthy_count if healthy_count is not None else count,
            "sequence": sequence,
            "selected_at": utc_now().isoformat(),
        }

    @staticmethod
    def _coordination_key(provider: str) -> str:
        identity = provider.strip().lower()
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        return f"cyberteam:llm:credential-round-robin:{digest}"

    @staticmethod
    def _quarantine_key(provider: str, credential_slot: int) -> str:
        identity = provider.strip().lower()
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        return f"cyberteam:llm:credential-quarantine:{digest}:{int(credential_slot)}"
