"""Provider-neutral LiteLLM gateway with zero-spend and local fallback policy."""

import asyncio
import json
import logging
import time
from collections import OrderedDict
from datetime import timedelta

import httpx

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.llm.resilience import (
    classify_llm_exception,
    llm_error_allows_local_fallback,
    llm_error_is_retryable,
)

logger = logging.getLogger(__name__)


class LLMCircuitOpenError(RuntimeError):
    """Raised while provider calls are cooling down after repeated failures."""


class LLMGateway:
    def __init__(self):
        self._provider = settings.llm_provider.strip() or "mistral"
        self._default_model = settings.llm_default_model.strip() or (
            "mistral/mistral-large-latest"
        )
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
        route = self._primary_route(model)
        if not route["local"] and not settings.llm_external_inference_allowed:
            if settings.llm_local_fallback_enabled:
                route = self._local_route()
                model = route["model"]
            else:
                raise RuntimeError(
                    "External LLM inference is blocked by the zero-spend policy. "
                    "Confirm a zero-cost provider or enable the local fallback."
                )

        messages = [{"role": "system", "content": system_prompt}]

        if conversation_id and conversation_id in self._conversation_history:
            history = self._conversation_history[conversation_id]
            self._conversation_history.move_to_end(conversation_id)
            messages.extend(history[-self._max_messages:])

        messages.append({"role": "user", "content": user_message})
        if route["local"]:
            messages = self._prepare_local_messages(messages)
            max_tokens = min(max_tokens, max(64, settings.llm_local_max_tokens))

        try:
            self._ensure_provider_available()
        except LLMCircuitOpenError:
            if not route["local"] and settings.llm_local_fallback_enabled:
                return await self._invoke_local_fallback(
                    messages=messages,
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    user_message=user_message,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    trigger="circuit_open",
                )
            raise
        import litellm
        metadata = {
            "generation_name": f"{agent_id}-completion",
            "tags": [agent_id, settings.environment, route["provider"]],
            "provider": route["provider"],
            "model": model,
        }
        if conversation_id:
            metadata["trace_id"] = conversation_id
            metadata["session_id"] = conversation_id

        attempts = max(1, settings.llm_retry_attempts)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                request = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "metadata": metadata,
                }
                if route["api_key"]:
                    request["api_key"] = route["api_key"]
                if route["api_base"]:
                    request["api_base"] = route["api_base"]
                response = await asyncio.wait_for(
                    litellm.acompletion(**request),
                    timeout=max(
                        1.0,
                        settings.llm_local_timeout_seconds
                        if route["local"]
                        else settings.llm_provider_timeout_seconds,
                    ),
                )
                result = response.choices[0].message.content
                self._record_provider_success(route=route)
                if conversation_id:
                    self._append_history(conversation_id, user_message, result)
                logger.info(
                    "LLM invoke: agent=%s, model=%s, tokens=%s",
                    agent_id,
                    model,
                    getattr(getattr(response, "usage", None), "total_tokens", None),
                )
                return result
            except Exception as exc:
                last_error = exc
                category = classify_llm_exception(exc)
                if attempt >= attempts - 1 or not llm_error_is_retryable(category):
                    self._record_provider_failure(category, route=route)
                    logger.error(
                        "LLM invoke failed: agent=%s category=%s attempt=%s/%s",
                        agent_id,
                        category,
                        attempt + 1,
                        attempts,
                    )
                    if (
                        not route["local"]
                        and settings.llm_local_fallback_enabled
                        and llm_error_allows_local_fallback(category)
                    ):
                        return await self._invoke_local_fallback(
                            messages=messages,
                            agent_id=agent_id,
                            conversation_id=conversation_id,
                            user_message=user_message,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            trigger=category,
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

    async def _invoke_local_fallback(
        self,
        *,
        messages: list[dict[str, str]],
        agent_id: str,
        conversation_id: str | None,
        user_message: str,
        temperature: float,
        max_tokens: int,
        trigger: str,
    ) -> str:
        """Invoke the retained local open model after a retryable hosted failure."""
        import litellm

        route = self._local_route()
        local_messages = self._prepare_local_messages(messages)
        metadata = {
            "generation_name": f"{agent_id}-completion-local-fallback",
            "tags": [agent_id, settings.environment, route["provider"], "fallback"],
            "provider": route["provider"],
            "model": route["model"],
            "fallback_trigger": trigger,
        }
        if conversation_id:
            metadata["trace_id"] = conversation_id
            metadata["session_id"] = conversation_id
        request = {
            "model": route["model"],
            "messages": local_messages,
            "temperature": temperature,
            "max_tokens": min(max_tokens, max(64, settings.llm_local_max_tokens)),
            "metadata": metadata,
            "api_base": route["api_base"],
        }
        if route["api_key"]:
            request["api_key"] = route["api_key"]
        logger.warning(
            "Routing LLM invoke to local fallback: agent=%s trigger=%s",
            agent_id,
            trigger,
        )
        try:
            response = await asyncio.wait_for(
                litellm.acompletion(**request),
                timeout=max(1.0, settings.llm_local_timeout_seconds),
            )
        except Exception as exc:
            category = classify_llm_exception(exc)
            self._record_provider_failure(category, route=route)
            logger.error(
                "Local LLM fallback failed: agent=%s category=%s",
                agent_id,
                category,
            )
            raise
        result = response.choices[0].message.content
        self._record_provider_success(route=route)
        if conversation_id:
            self._append_history(conversation_id, user_message, result)
        logger.info(
            "Local LLM fallback succeeded: agent=%s model=%s tokens=%s",
            agent_id,
            route["model"],
            getattr(getattr(response, "usage", None), "total_tokens", None),
        )
        return result

    @staticmethod
    def _prepare_local_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
        local_messages = [dict(message) for message in messages]
        if local_messages and "/no_think" not in str(
            local_messages[-1].get("content") or ""
        ):
            local_messages[-1]["content"] = (
                str(local_messages[-1].get("content") or "") + "\n/no_think"
            )
        return local_messages

    async def validate_provider(self, *, force: bool = False) -> dict:
        now = utc_now()
        if (
            not force
            and self._last_validation_result
            and self._last_validation_at
            and now - self._last_validation_at < timedelta(minutes=5)
        ):
            return self._merge_runtime_status(self._last_validation_result, now)

        route = self._primary_route(self._default_model)
        zero_cost_blocked = not route["local"] and not settings.llm_external_inference_allowed
        if zero_cost_blocked and settings.llm_local_fallback_enabled:
            route = self._local_route()
            zero_cost_blocked = False

        if zero_cost_blocked:
            result = {
                "provider": route["provider"],
                "model": route["model"],
                "configured": bool(route["api_key"]),
                "mode": "configuration_required",
                "status": "zero_cost_confirmation_required",
                "blocking": True,
                "detail": (
                    "Hosted inference is blocked until zero-cost use is explicitly "
                    "confirmed or a positive owner-approved spend limit is configured."
                ),
                "hosted": True,
                "zero_cost_confirmed": False,
                "last_checked_at": now.isoformat(),
            }
            self._last_validation_result = result
            self._last_validation_at = now
            return self._merge_runtime_status(result, now)

        if not route["local"] and not route["api_key"]:
            result = {
                "provider": route["provider"],
                "model": route["model"],
                "configured": False,
                "mode": "configuration_required",
                "status": "configuration_required",
                "blocking": True,
                "detail": "An API key is required for the selected hosted LLM provider.",
                "hosted": True,
                "last_checked_at": now.isoformat(),
            }
            self._last_validation_result = result
            self._last_validation_at = now
            return self._merge_runtime_status(result, now)

        result = await self._validate_route(route, now=now)
        if (
            result.get("blocking")
            and not route["local"]
            and settings.llm_local_fallback_enabled
        ):
            fallback = await self._validate_route(self._local_route(), now=now)
            if not fallback.get("blocking"):
                result = {
                    **fallback,
                    "mode": "local_fallback",
                    "status": "live",
                    "blocking": False,
                    "detail": (
                        "Hosted LLM is unavailable; isolated local open-model inference "
                        "is active."
                    ),
                    "primary_provider": {
                        "provider": route["provider"],
                        "model": route["model"],
                        "mode": result.get("mode"),
                        "status": result.get("status"),
                        "detail": result.get("detail"),
                    },
                }
        self._last_validation_result = result
        self._last_validation_at = now
        return self._merge_runtime_status(result, now)

    async def _validate_route(self, route: dict, *, now) -> dict:
        provider_label = "Local model" if route["local"] else "Hosted LLM"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(route["models_url"], headers=route["headers"])
            if response.status_code == 200:
                return {
                    "provider": route["provider"],
                    "model": route["model"],
                    "configured": True,
                    "mode": "live",
                    "status": "live",
                    "blocking": False,
                    "detail": (
                        "Local open-model inference is reachable."
                        if route["local"]
                        else "Hosted LLM credentials and zero-spend policy validated."
                    ),
                    "hosted": not route["local"],
                    "zero_cost_confirmed": (
                        True if route["local"] else settings.llm_external_zero_cost_confirmed
                    ),
                    "last_checked_at": now.isoformat(),
                }
            if response.status_code in {401, 403}:
                return {
                    "provider": route["provider"],
                    "model": route["model"],
                    "configured": True,
                    "mode": "configuration_required",
                    "status": "configuration_required",
                    "blocking": True,
                    "detail": f"{provider_label} credentials were rejected.",
                    "last_checked_at": now.isoformat(),
                }
            status = "capacity_exhausted" if response.status_code == 402 else "unavailable"
            return {
                "provider": route["provider"],
                "model": route["model"],
                "configured": True,
                "mode": "unavailable",
                "status": status,
                "blocking": True,
                "detail": f"{provider_label} validation returned HTTP {response.status_code}.",
                "last_checked_at": now.isoformat(),
            }
        except Exception as exc:  # noqa: BLE001 - validation must return safe status.
            return {
                "provider": route["provider"],
                "model": route["model"],
                "configured": True,
                "mode": "unavailable",
                "status": "unavailable",
                "blocking": True,
                "detail": f"{provider_label} validation failed: {type(exc).__name__}.",
                "last_checked_at": now.isoformat(),
            }

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
            "provider": self._provider,
            "model": self._default_model,
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

    def _record_provider_success(self, *, route: dict | None = None) -> None:
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        self._last_invocation_result = {
            "outcome": "success",
            "category": None,
            "at": utc_now().isoformat(),
            "provider": (route or {}).get("provider", self._provider),
            "model": (route or {}).get("model", self._default_model),
        }

    def _record_provider_failure(self, category: str, *, route: dict | None = None) -> None:
        self._consecutive_failures += 1
        threshold = max(1, settings.llm_circuit_breaker_failure_threshold)
        if (
            llm_error_allows_local_fallback(category)
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
            "provider": (route or {}).get("provider", self._provider),
            "model": (route or {}).get("model", self._default_model),
        }

    def _primary_route(self, model: str) -> dict:
        local = settings.llm_provider_is_local
        api_base = settings.llm_api_base.strip()
        if local and not api_base:
            api_base = settings.llm_local_api_base.strip()
        models_url = (
            f"{api_base.rstrip('/')}/models"
            if api_base
            else "https://api.mistral.ai/v1/models"
        )
        api_key = settings.llm_effective_api_key
        return {
            "provider": self._provider,
            "model": model,
            "api_base": api_base,
            "api_key": api_key,
            "models_url": models_url,
            "headers": ({"Authorization": f"Bearer {api_key}"} if api_key else {}),
            "local": local,
        }

    def _local_route(self) -> dict:
        api_base = settings.llm_local_api_base.strip()
        api_key = settings.llm_local_api_key.strip()
        return {
            "provider": "llama_cpp",
            "model": settings.llm_local_model.strip() or "local/open-model",
            "api_base": api_base,
            "api_key": api_key,
            "models_url": f"{api_base.rstrip('/')}/models",
            "headers": ({"Authorization": f"Bearer {api_key}"} if api_key else {}),
            "local": True,
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
        max_tokens: int = 4096,
    ) -> dict:
        response = await self.invoke(
            system_prompt=system_prompt + "\nAlways respond with valid JSON only.",
            user_message=user_message,
            agent_id=agent_id,
            model=model,
            temperature=0.3,
            max_tokens=max_tokens,
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
