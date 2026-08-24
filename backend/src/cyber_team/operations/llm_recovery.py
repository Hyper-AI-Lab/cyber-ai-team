"""Bounded, read-only recovery probing for transient hosted LLM failures."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from redis.asyncio import Redis

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.llm.resilience import (
    RETRYABLE_LLM_ERRORS,
    classify_llm_exception,
)
from cyber_team.memory.protocol import PROTOCOL_VERSION


class LLMProviderRecoveryService:
    """Recover persisted readiness with a real, tightly bounded completion.

    A Redis cooldown claim provides cross-replica deduplication. The probe has no
    tools and no business side effects; it exists only to prove that a transient
    provider condition has cleared and to persist that evidence as a memory trace.
    """

    RECOVERABLE_STATUSES = RETRYABLE_LLM_ERRORS | {"circuit_open"}

    def __init__(
        self,
        *,
        llm_gateway,
        memory_service,
        memory_steward_service,
        audit_service=None,
        redis_client: Any | None = None,
    ) -> None:
        self._llm = llm_gateway
        self._memory = memory_service
        self._memory_steward = memory_steward_service
        self._audit = audit_service
        self._redis = redis_client
        self._owns_redis = redis_client is None

    async def run_once(self) -> dict[str, Any]:
        checked_at = utc_now()
        health = await self._memory_steward.llm_provider_health(now=checked_at)
        category = str(health.get("last_failure_category") or "")
        if not health.get("blocking") or category not in self.RECOVERABLE_STATUSES:
            return {
                "status": "not_needed",
                "attempted": False,
                "provider_health": health,
                "checked_at": checked_at.isoformat(),
            }

        claimed = await self._claim_cooldown()
        if not claimed:
            return {
                "status": "cooldown",
                "attempted": False,
                "provider_health": health,
                "checked_at": checked_at.isoformat(),
            }

        invocation_id = f"llm_recovery_{uuid.uuid4().hex}"
        task = "Confirm hosted inference availability with the word READY."
        try:
            result = await self._llm.invoke(
                system_prompt=(
                    "You are a read-only provider availability probe. Do not request "
                    "or use tools. Reply with the single word READY."
                ),
                user_message=task,
                agent_id="llm_provider_recovery_probe",
                temperature=0.0,
                max_tokens=8,
            )
            trace = await self._record_trace(
                invocation_id=invocation_id,
                task=task,
                result=str(result),
                errors=[],
                metadata={
                    "outcome": "success",
                    "recovered_failure_category": category,
                    "recovered_failure_at": health.get("last_failure_at"),
                },
            )
            status = "recovered"
            outcome = "success"
            error_category = None
        except Exception as exc:  # noqa: BLE001 - probe evidence must survive failure.
            error_category = classify_llm_exception(exc)
            trace = await self._record_trace(
                invocation_id=invocation_id,
                task=task,
                result="",
                errors=[f"invoke:{type(exc).__name__}:{error_category}"],
                metadata={
                    "outcome": "failed",
                    "failure_domain": "llm_provider",
                    "failure_code": error_category,
                    "failure_retryable": error_category
                    in self.RECOVERABLE_STATUSES,
                    "trigger_failure_category": category,
                    "trigger_failure_at": health.get("last_failure_at"),
                },
            )
            status = "failed"
            outcome = "failed"

        response = {
            "status": status,
            "attempted": True,
            "trigger_category": category,
            "error_category": error_category,
            "trace_id": trace.get("id"),
            "checked_at": checked_at.isoformat(),
        }
        if self._audit:
            await self._audit.record(
                event_type="llm_provider.recovery_probe",
                actor="llm_provider_recovery_probe",
                actor_type="system",
                resource_type="llm_provider",
                action="probe",
                outcome=outcome,
                metadata=response,
            )
        return response

    async def close(self) -> None:
        if self._redis is not None and self._owns_redis:
            await self._redis.aclose()
        self._redis = None

    async def _claim_cooldown(self) -> bool:
        redis = self._redis_client()
        try:
            claimed = await redis.set(
                "cyberteam:llm:provider-recovery-probe",
                uuid.uuid4().hex,
                ex=max(30, settings.llm_recovery_probe_cooldown_seconds),
                nx=True,
            )
        except Exception as exc:
            raise ConnectionError(
                "LLM recovery coordination is unavailable."
            ) from exc
        return bool(claimed)

    async def _record_trace(
        self,
        *,
        invocation_id: str,
        task: str,
        result: str,
        errors: list[str],
        metadata: dict[str, Any],
    ) -> dict:
        return await self._memory.record_trace(
            SimpleNamespace(
                invocation_id=invocation_id,
                agent_id="llm_provider_recovery_probe",
                conversation_id=None,
                source_type="llm_provider_recovery_probe",
                task_excerpt=task,
                memory_namespace=settings.company_namespace,
                read_policy={
                    "version": "llm-recovery-probe-v1",
                    "strategy": "no-memory-read",
                },
                write_policy={
                    "version": "llm-recovery-probe-v1",
                    "strategy": "trace-only",
                },
                recalled_memory_ids=[],
                written_memory_ids=[],
                recall_count=0,
                write_count=0,
                errors=errors,
                metadata={
                    "protocol_version": PROTOCOL_VERSION,
                    "result_excerpt": result[:80],
                    "coverage": "not_applicable",
                    "memory_coverage": "not_applicable",
                    "side_effects": False,
                    **metadata,
                },
            )
        )

    def _redis_client(self) -> Any:
        if self._redis is None:
            self._redis = Redis.from_url(settings.redis_url, decode_responses=True)
        return self._redis
