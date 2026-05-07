"""LLM Gateway — LiteLLM with Mistral as default provider."""

import json
import logging
from typing import Optional
from cyber_team.config import settings

logger = logging.getLogger(__name__)


class LLMGateway:
    def __init__(self):
        self._default_model = "mistral/mistral-large-latest"
        self._conversation_history: dict[str, list[dict]] = {}

    async def invoke(
        self,
        system_prompt: str,
        user_message: str,
        agent_id: str = "default",
        conversation_id: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        model = model or self._default_model

        messages = [{"role": "system", "content": system_prompt}]

        if conversation_id and conversation_id in self._conversation_history:
            messages.extend(self._conversation_history[conversation_id])

        messages.append({"role": "user", "content": user_message})

        try:
            import litellm
            litellm.api_key = settings.mistral_api_key

            response = await litellm.acompletion(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            result = response.choices[0].message.content

            # Store conversation history
            if conversation_id:
                if conversation_id not in self._conversation_history:
                    self._conversation_history[conversation_id] = []
                self._conversation_history[conversation_id].append(
                    {"role": "user", "content": user_message}
                )
                self._conversation_history[conversation_id].append(
                    {"role": "assistant", "content": result}
                )

            logger.info(f"LLM invoke: agent={agent_id}, model={model}, tokens={response.usage.total_tokens}")
            return result

        except Exception as e:
            logger.error(f"LLM invoke failed: {e}")
            raise

    async def invoke_json(
        self,
        system_prompt: str,
        user_message: str,
        agent_id: str = "default",
        model: Optional[str] = None,
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
