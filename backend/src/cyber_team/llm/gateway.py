"""LLM Gateway — LiteLLM with Mistral as default provider."""

import json
import logging
from collections import OrderedDict
from datetime import timedelta

import httpx

from cyber_team.clock import utc_now
from cyber_team.config import settings

logger = logging.getLogger(__name__)


class LLMGateway:
    def __init__(self):
        self._default_model = "mistral/mistral-large-latest"
        self._conversation_history: OrderedDict[str, list[dict]] = OrderedDict()
        self._max_conversations = max(1, settings.llm_history_max_conversations)
        self._max_messages = max(2, settings.llm_history_max_messages)
        self._last_validation_result: dict | None = None
        self._last_validation_at = None

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

        try:
            import litellm
            litellm.api_key = settings.mistral_api_key

            # Construct trace metadata for Langfuse
            metadata = {
                "generation_name": f"{agent_id}-completion",
                "tags": [agent_id, settings.environment],
            }
            if conversation_id:
                metadata["trace_id"] = conversation_id
                metadata["session_id"] = conversation_id

            response = await litellm.acompletion(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                metadata=metadata,
            )

            result = response.choices[0].message.content

            # Store conversation history
            if conversation_id:
                self._append_history(conversation_id, user_message, result)

            logger.info(
                "LLM invoke: agent=%s, model=%s, tokens=%s",
                agent_id,
                model,
                response.usage.total_tokens,
            )
            return result

        except Exception as e:
            logger.error(f"LLM invoke failed: {e}")
            raise

    async def validate_provider(self, *, force: bool = False) -> dict:
        now = utc_now()
        if (
            not force
            and self._last_validation_result
            and self._last_validation_at
            and now - self._last_validation_at < timedelta(minutes=5)
        ):
            return self._last_validation_result

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
            return result

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
        return result

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
