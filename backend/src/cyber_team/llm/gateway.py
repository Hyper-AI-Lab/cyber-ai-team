"""LLM Gateway — LiteLLM with Mistral as default provider."""

import asyncio
import json
import logging
import time
from collections import OrderedDict
from datetime import timedelta

import httpx

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.llm.resilience import classify_llm_exception, llm_error_is_retryable

logger = logging.getLogger(__name__)


class LLMCircuitOpenError(RuntimeError):
    """Raised while provider calls are cooling down after repeated failures."""


class LLMGateway:
    def __init__(self):
        self._default_model = "mistral/mistral-large-latest"
        self._conversation_history: OrderedDict[str, list[dict]] = OrderedDict()
        self._max_conversations = max(1, settings.llm_history_max_conversations)
        self._max_messages = max(2, settings.llm_history_max_messages)
        self._last_validation_result: dict | None = None
        self._last_validation_at = None
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        self._last_invocation_result: dict | None = None

        # Integrate Langfuse tracing if API keys are configured
        if settings.langfuse_public_key and settings.langfuse_secret_key:
            import os

            import litellm
            os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
            os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
            os.environ["LANGFUSE_HOST"] = settings.langfuse_host

            # Register Langfuse callbacks
            litellm.success_callback = (litellm.success_callback or []) + ["langfuse"]
            litellm.failure_callback = (litellm.failure_callback or []) + ["langfuse"]

    async def invoke(
        self,
        system_prompt: str,
        user_message: str,
        agent_id: str = "default",
        conversation_id: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        model = model or self._default_model

        messages = [{"role": "system", "content": system_prompt}]

        if conversation_id and conversation_id in self._conversation_history:
            history = self._conversation_history[conversation_id]
            self._conversation_history.move_to_end(conversation_id)
            messages.extend(history[-self._max_messages:])

        messages.append({"role": "user", "content": user_message})

        self._ensure_provider_available()
        import litellm

        litellm.api_key = settings.mistral_api_key
        metadata = {
            "generation_name": f"{agent_id}-completion",
            "tags": [agent_id, settings.environment],
        }
        if conversation_id:
            metadata["trace_id"] = conversation_id
            metadata["session_id"] = conversation_id

        attempts = max(1, settings.llm_retry_attempts)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = await asyncio.wait_for(
                    litellm.acompletion(
                        model=model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        metadata=metadata,
                    ),
                    timeout=max(1.0, settings.llm_provider_timeout_seconds),
                )
                result = response.choices[0].message.content
                self._record_provider_success()
                if conversation_id:
                    self._append_history(conversation_id, user_message, result)
                logger.info(
                    "LLM invoke: agent=%s, model=%s, tokens=%s",
                    agent_id,
                    model,
                    response.usage.total_tokens,
                )
                return result
            except Exception as exc:
                last_error = exc
                category = classify_llm_exception(exc)
                if attempt >= attempts - 1 or not llm_error_is_retryable(category):
                    self._record_provider_failure(category)
                    logger.error(
                        "LLM invoke failed: agent=%s category=%s attempt=%s/%s",
                        agent_id,
                        category,
                        attempt + 1,
                        attempts,
                    )
                    raise
                backoff = max(0.0, settings.llm_retry_backoff_seconds) * (2**attempt)
                logger.warning(
                    "Retrying LLM invoke: agent=%s category=%s attempt=%s/%s",
                    agent_id,
                    category,
                    attempt + 1,
                    attempts,
                )
                if backoff:
                    await asyncio.sleep(backoff)
        raise last_error or RuntimeError("LLM provider invocation failed")

    async def validate_provider(self, *, force: bool = False) -> dict:
        now = utc_now()
        if (
            not force
            and self._last_validation_result
            and self._last_validation_at
            and now - self._last_validation_at < timedelta(minutes=5)
        ):
            return self._merge_runtime_status(self._last_validation_result, now)

        if not settings.mistral_api_key:
            result = {
                "provider": "mistral",
                "configured": False,
                "mode": "configuration_required",
                "status": "configuration_required",
                "blocking": True,
                "detail": "MISTRAL_API_KEY is required for agent LLM execution.",
                "last_checked_at": now.isoformat(),
            }
            self._last_validation_result = result
            self._last_validation_at = now
            return self._merge_runtime_status(result, now)

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    "https://api.mistral.ai/v1/models",
                    headers={"Authorization": f"Bearer {settings.mistral_api_key}"},
                )
            if response.status_code == 200:
                result = {
                    "provider": "mistral",
                    "configured": True,
                    "mode": "live",
                    "status": "live",
                    "blocking": False,
                    "detail": "Mistral API credentials validated successfully.",
                    "last_checked_at": now.isoformat(),
                }
            elif response.status_code in {401, 403}:
                result = {
                    "provider": "mistral",
                    "configured": True,
                    "mode": "configuration_required",
                    "status": "configuration_required",
                    "blocking": True,
                    "detail": "Mistral API credentials were rejected by the provider.",
                    "last_checked_at": now.isoformat(),
                }
            else:
                result = {
                    "provider": "mistral",
                    "configured": True,
                    "mode": "unavailable",
                    "status": "unavailable",
                    "blocking": True,
                    "detail": (
                        "Mistral provider validation returned HTTP "
                        f"{response.status_code}."
                    ),
                    "last_checked_at": now.isoformat(),
                }
        except Exception as exc:  # noqa: BLE001 - validation must return safe status.
            result = {
                "provider": "mistral",
                "configured": True,
                "mode": "unavailable",
                "status": "unavailable",
                "blocking": True,
                "detail": f"Mistral provider validation failed: {exc}",
                "last_checked_at": now.isoformat(),
            }
        self._last_validation_result = result
        self._last_validation_at = now
        return self._merge_runtime_status(result, now)

    def runtime_status(self) -> dict:
        now = utc_now()
        circuit_open = self._circuit_open_until > time.monotonic()
        last = dict(self._last_invocation_result or {})
        status = "ready"
        blocking = False
        detail = "No recent LLM completion failures are recorded in this process."
        if circuit_open:
            status = last.get("category") or "circuit_open"
            blocking = True
            detail = "LLM completion circuit is cooling down after repeated failures."
        elif last.get("outcome") == "failed":
            status = last.get("category") or "provider_error"
            blocking = status in {
                "authentication_error",
                "rate_limited",
                "provider_unavailable",
                "timeout",
                "circuit_open",
            }
            detail = f"The latest LLM completion failed with category {status}."
        elif last.get("outcome") == "success":
            detail = "The latest LLM completion succeeded."
        return {
            "status": status,
            "blocking": blocking,
            "detail": detail,
            "consecutive_failures": self._consecutive_failures,
            "circuit_open": circuit_open,
            "cooldown_remaining_seconds": max(
                0,
                round(self._circuit_open_until - time.monotonic()),
            ),
            "last_invocation": last or None,
            "checked_at": now.isoformat(),
        }

    def _ensure_provider_available(self) -> None:
        if self._circuit_open_until > time.monotonic():
            raise LLMCircuitOpenError(
                "LLM provider circuit breaker is open; retry after the cooldown."
            )

    def _record_provider_success(self) -> None:
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        self._last_invocation_result = {
            "outcome": "success",
            "category": None,
            "at": utc_now().isoformat(),
        }

    def _record_provider_failure(self, category: str) -> None:
        self._consecutive_failures += 1
        threshold = max(1, settings.llm_circuit_breaker_failure_threshold)
        if (
            llm_error_is_retryable(category)
            and self._consecutive_failures >= threshold
        ):
            self._circuit_open_until = time.monotonic() + max(
                1,
                settings.llm_circuit_breaker_cooldown_seconds,
            )
        self._last_invocation_result = {
            "outcome": "failed",
            "category": category,
            "at": utc_now().isoformat(),
        }

    def _merge_runtime_status(self, result: dict, now) -> dict:
        runtime = self.runtime_status()
        merged = {**result, "runtime_health": runtime}
        if result.get("mode") == "live" and runtime["blocking"]:
            category = runtime["status"]
            merged.update({
                "mode": "configuration_required"
                if category == "authentication_error"
                else "degraded",
                "status": category,
                "blocking": True,
                "detail": runtime["detail"],
                "last_checked_at": now.isoformat(),
            })
        return merged

    async def invoke_json(
        self,
        system_prompt: str,
        user_message: str,
        agent_id: str = "default",
        model: str | None = None,
    ) -> dict:
        response = await self.invoke(
            system_prompt=system_prompt + "\nAlways respond with valid JSON only.",
            user_message=user_message,
            agent_id=agent_id,
            model=model,
            temperature=0.3,
        )
        try:
            # Try to extract JSON from response (handle markdown code blocks)
            text = response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse JSON response: {response[:200]}")
            return {"raw_response": response}

    def _append_history(self, conversation_id: str, user_message: str, result: str) -> None:
        if conversation_id not in self._conversation_history:
            self._conversation_history[conversation_id] = []
        self._conversation_history.move_to_end(conversation_id)
        self._conversation_history[conversation_id].extend(
            [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": result},
            ]
        )
        self._conversation_history[conversation_id] = self._conversation_history[
            conversation_id
        ][-self._max_messages:]
        while len(self._conversation_history) > self._max_conversations:
            self._conversation_history.popitem(last=False)
