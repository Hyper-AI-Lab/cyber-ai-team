import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cyber_team.config import settings
from cyber_team.llm.gateway import (
    LLMCircuitOpenError,
    LLMGateway,
    LLMStructuredOutputError,
    LLMStructuredOutputTruncatedError,
)


def test_llm_history_is_bounded(monkeypatch):
    monkeypatch.setattr(settings, "llm_history_max_conversations", 2)
    monkeypatch.setattr(settings, "llm_history_max_messages", 4)

    gateway = LLMGateway()
    for index in range(3):
        conversation_id = f"conversation-{index}"
        gateway._append_history(conversation_id, "user-1", "assistant-1")
        gateway._append_history(conversation_id, "user-2", "assistant-2")
        gateway._append_history(conversation_id, "user-3", "assistant-3")

    assert list(gateway._conversation_history) == ["conversation-1", "conversation-2"]
    assert len(gateway._conversation_history["conversation-2"]) == 4
    assert gateway._conversation_history["conversation-2"][0] == {
        "role": "user",
        "content": "user-2",
    }


@pytest.mark.asyncio
async def test_validate_provider_reports_live_mistral(monkeypatch):
    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_external_zero_cost_confirmed", True)

    class FakeResponse:
        status_code = 200

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers):
            assert url == "https://api.mistral.ai/v1/models"
            assert headers["Authorization"] == "Bearer test-key"
            return FakeResponse()

    monkeypatch.setattr("cyber_team.llm.gateway.httpx.AsyncClient", FakeClient)

    result = await LLMGateway().validate_provider(force=True)

    assert result["mode"] == "live"
    assert result["blocking"] is False


@pytest.mark.asyncio
async def test_validate_provider_reports_rejected_mistral_credentials(monkeypatch):
    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_external_zero_cost_confirmed", True)

    class FakeResponse:
        status_code = 401

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers):
            return FakeResponse()

    monkeypatch.setattr("cyber_team.llm.gateway.httpx.AsyncClient", FakeClient)

    result = await LLMGateway().validate_provider(force=True)

    assert result["mode"] == "configuration_required"
    assert result["blocking"] is True


@pytest.mark.asyncio
async def test_validate_provider_uses_local_fallback_when_hosted_capacity_is_exhausted(
    monkeypatch,
):
    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_external_zero_cost_confirmed", True)
    monkeypatch.setattr(settings, "llm_local_fallback_enabled", True)
    monkeypatch.setattr(settings, "llm_local_api_base", "http://llama:8080/v1")
    monkeypatch.setattr(settings, "llm_local_model", "local/test-open-model")
    monkeypatch.setattr(settings, "llm_local_api_key", "local-test-key")

    class FakeResponse:
        def __init__(self, status_code):
            self.status_code = status_code

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers):
            if url == "https://api.mistral.ai/v1/models":
                return FakeResponse(402)
            assert url == "http://llama:8080/v1/models"
            assert headers["Authorization"] == "Bearer local-test-key"
            return FakeResponse(200)

    monkeypatch.setattr("cyber_team.llm.gateway.httpx.AsyncClient", FakeClient)

    result = await LLMGateway().validate_provider(force=True)

    assert result["provider"] == "llama_cpp"
    assert result["model"] == "local/test-open-model"
    assert result["mode"] == "local_fallback"
    assert result["status"] == "live"
    assert result["blocking"] is False
    assert result["primary_provider"]["status"] == "capacity_exhausted"


@pytest.mark.asyncio
async def test_invoke_retries_rate_limit_then_records_recovery(monkeypatch):
    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_external_zero_cost_confirmed", True)
    monkeypatch.setattr(settings, "llm_retry_attempts", 2)
    monkeypatch.setattr(settings, "llm_retry_backoff_seconds", 0)
    monkeypatch.setattr(settings, "llm_provider_timeout_seconds", 1)
    calls = 0

    class RateLimitError(Exception):
        pass

    async def fake_completion(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RateLimitError("rate limit exceeded")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Recovered."))],
            usage=SimpleNamespace(total_tokens=12),
        )

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(api_key=None, acompletion=fake_completion),
    )
    pacer = MagicMock()
    pacer.acquire = AsyncMock()
    pacer.close = AsyncMock()
    pacer.status.return_value = {"enabled": True}
    gateway = LLMGateway(hosted_pacer=pacer)

    result = await gateway.invoke("System", "Task", agent_id="ops")

    assert result == "Recovered."
    assert calls == 2
    assert pacer.acquire.await_count == 2
    assert gateway.runtime_status()["status"] == "ready"
    assert gateway.runtime_status()["last_invocation"]["outcome"] == "success"


@pytest.mark.asyncio
async def test_invoke_opens_circuit_after_repeated_rate_limits(monkeypatch):
    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_external_zero_cost_confirmed", True)
    monkeypatch.setattr(settings, "llm_retry_attempts", 1)
    monkeypatch.setattr(settings, "llm_circuit_breaker_failure_threshold", 2)
    monkeypatch.setattr(settings, "llm_circuit_breaker_cooldown_seconds", 60)
    calls = 0

    class RateLimitError(Exception):
        pass

    async def fake_completion(**kwargs):
        nonlocal calls
        calls += 1
        raise RateLimitError("rate limit exceeded")

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(api_key=None, acompletion=fake_completion),
    )
    gateway = LLMGateway()

    with pytest.raises(RateLimitError):
        await gateway.invoke("System", "First", agent_id="ops")
    with pytest.raises(RateLimitError):
        await gateway.invoke("System", "Second", agent_id="ops")
    with pytest.raises(LLMCircuitOpenError):
        await gateway.invoke("System", "Third", agent_id="ops")

    assert calls == 2
    status = gateway.runtime_status()
    assert status["status"] == "rate_limited"
    assert status["blocking"] is True
    assert status["circuit_open"] is True


@pytest.mark.asyncio
async def test_hosted_inference_is_blocked_without_zero_cost_confirmation(monkeypatch):
    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_external_zero_cost_confirmed", False)
    monkeypatch.setattr(settings, "llm_external_spend_limit_usd", 0.0)
    monkeypatch.setattr(settings, "llm_local_fallback_enabled", False)

    gateway = LLMGateway()
    status = await gateway.validate_provider(force=True)

    assert status["mode"] == "configuration_required"
    assert status["status"] == "zero_cost_confirmation_required"
    assert status["blocking"] is True
    with pytest.raises(RuntimeError, match="zero-spend policy"):
        await gateway.invoke("System", "Task", agent_id="ops")


@pytest.mark.asyncio
async def test_zero_spend_policy_routes_to_local_fallback(monkeypatch):
    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_external_zero_cost_confirmed", False)
    monkeypatch.setattr(settings, "llm_external_spend_limit_usd", 0.0)
    monkeypatch.setattr(settings, "llm_local_fallback_enabled", True)
    monkeypatch.setattr(settings, "llm_local_api_base", "http://llama:8080/v1")
    monkeypatch.setattr(settings, "llm_local_model", "local/test-open-model")
    monkeypatch.setattr(settings, "llm_local_api_key", "")
    seen = {}

    async def fake_completion(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Local result."))],
            usage=SimpleNamespace(total_tokens=8),
        )

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(api_key=None, acompletion=fake_completion),
    )

    result = await LLMGateway().invoke("System", "Task", agent_id="ops")

    assert result == "Local result."
    assert seen["model"] == "local/test-open-model"
    assert seen["api_base"] == "http://llama:8080/v1"
    assert "api_key" not in seen


@pytest.mark.asyncio
async def test_local_timeout_is_not_retried_with_identical_prompt(monkeypatch):
    monkeypatch.setattr(settings, "mistral_api_key", "")
    monkeypatch.setattr(settings, "llm_external_zero_cost_confirmed", False)
    monkeypatch.setattr(settings, "llm_external_spend_limit_usd", 0.0)
    monkeypatch.setattr(settings, "llm_local_fallback_enabled", True)
    monkeypatch.setattr(settings, "llm_local_api_base", "http://llama:8080/v1")
    monkeypatch.setattr(settings, "llm_local_model", "local/test-open-model")
    monkeypatch.setattr(settings, "llm_retry_attempts", 3)
    monkeypatch.setattr(settings, "llm_retry_backoff_seconds", 0)
    calls = 0

    async def fake_completion(**kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(api_key=None, acompletion=fake_completion),
    )

    with pytest.raises(TimeoutError):
        await LLMGateway().invoke("System", "Task", agent_id="ops")

    assert calls == 1


@pytest.mark.asyncio
async def test_retryable_hosted_failure_routes_to_local_fallback(monkeypatch):
    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_external_zero_cost_confirmed", True)
    monkeypatch.setattr(settings, "llm_local_fallback_enabled", True)
    monkeypatch.setattr(settings, "llm_local_api_base", "http://llama:8080/v1")
    monkeypatch.setattr(settings, "llm_local_model", "local/test-open-model")
    monkeypatch.setattr(settings, "llm_local_api_key", "local-test-key")
    monkeypatch.setattr(settings, "llm_local_max_tokens", 256)
    monkeypatch.setattr(settings, "llm_retry_attempts", 1)
    monkeypatch.setattr(settings, "llm_local_timeout_seconds", 1)
    seen = []

    class RateLimitError(Exception):
        pass

    async def fake_completion(**kwargs):
        seen.append(kwargs)
        if kwargs["model"].startswith("mistral/"):
            raise RateLimitError("rate limit exceeded")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Local recovery."))],
            usage=SimpleNamespace(total_tokens=7),
        )

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(api_key=None, acompletion=fake_completion),
    )

    gateway = LLMGateway()
    result = await gateway.invoke("System", "Task", agent_id="ops")

    assert result == "Local recovery."
    assert [call["model"] for call in seen] == [
        "mistral/mistral-large-latest",
        "local/test-open-model",
    ]
    assert seen[1]["api_key"] == "local-test-key"
    assert seen[1]["max_tokens"] == 256
    assert seen[1]["messages"][-1]["content"].endswith("/no_think")
    assert gateway.runtime_status()["last_invocation"]["provider"] == "llama_cpp"


@pytest.mark.asyncio
async def test_hosted_capacity_exhaustion_routes_immediately_to_local_fallback(monkeypatch):
    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_external_zero_cost_confirmed", True)
    monkeypatch.setattr(settings, "llm_local_fallback_enabled", True)
    monkeypatch.setattr(settings, "llm_local_api_base", "http://llama:8080/v1")
    monkeypatch.setattr(settings, "llm_local_model", "local/test-open-model")
    monkeypatch.setattr(settings, "llm_local_api_key", "local-test-key")
    monkeypatch.setattr(settings, "llm_retry_attempts", 3)
    monkeypatch.setattr(settings, "llm_local_timeout_seconds", 1)
    seen = []

    class CapacityError(Exception):
        status_code = 402

    async def fake_completion(**kwargs):
        seen.append(kwargs["model"])
        if kwargs["model"].startswith("mistral/"):
            raise CapacityError("payment required")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Local recovery."))],
            usage=SimpleNamespace(total_tokens=7),
        )

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(api_key=None, acompletion=fake_completion),
    )

    gateway = LLMGateway()
    result = await gateway.invoke("System", "Task", agent_id="ops")

    assert result == "Local recovery."
    assert seen == ["mistral/mistral-large-latest", "local/test-open-model"]
    assert gateway.runtime_status()["last_invocation"]["provider"] == "llama_cpp"


@pytest.mark.asyncio
async def test_non_retryable_hosted_failure_does_not_use_local_fallback(monkeypatch):
    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_external_zero_cost_confirmed", True)
    monkeypatch.setattr(settings, "llm_local_fallback_enabled", True)
    monkeypatch.setattr(settings, "llm_retry_attempts", 1)
    calls = 0

    class AuthenticationError(Exception):
        status_code = 401

    async def fake_completion(**kwargs):
        nonlocal calls
        calls += 1
        raise AuthenticationError("unauthorized")

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(api_key=None, acompletion=fake_completion),
    )

    with pytest.raises(AuthenticationError):
        await LLMGateway().invoke("System", "Task", agent_id="ops")

    assert calls == 1


@pytest.mark.asyncio
async def test_invoke_json_forwards_structured_output_token_budget():
    gateway = LLMGateway()
    gateway.invoke = AsyncMock(return_value='{"claims": []}')

    result = await gateway.invoke_json(
        "Extract claims.",
        "Evidence payload.",
        agent_id="company_discovery_agent",
        max_tokens=128,
        json_schema={"type": "object", "required": ["claims"]},
    )

    assert result == {"claims": []}
    assert gateway.invoke.await_args.kwargs["max_tokens"] == 128
    assert gateway.invoke.await_args.kwargs["json_schema"]["required"] == ["claims"]


@pytest.mark.asyncio
async def test_invoke_json_rejects_truncated_output_without_logging_content(caplog):
    gateway = LLMGateway()
    sensitive_fragment = "private-email-body-marker"
    gateway.invoke = AsyncMock(
        return_value='{"claims":[{"value":"' + sensitive_fragment
    )

    with pytest.raises(LLMStructuredOutputTruncatedError):
        await gateway.invoke_json("Extract claims.", "Evidence payload.")

    assert sensitive_fragment not in caplog.text
    assert "LLMStructuredOutputTruncatedError" in caplog.text


@pytest.mark.asyncio
async def test_invoke_json_rejects_malformed_complete_output():
    gateway = LLMGateway()
    gateway.invoke = AsyncMock(return_value="not-json")

    with pytest.raises(LLMStructuredOutputError):
        await gateway.invoke_json("Return JSON.", "Evidence payload.")


@pytest.mark.asyncio
async def test_local_invoke_uses_llama_cpp_schema_constrained_response_format(
    monkeypatch,
):
    monkeypatch.setattr(settings, "llm_provider", "llama_cpp")
    monkeypatch.setattr(settings, "llm_api_base", "http://llama:8080/v1")
    monkeypatch.setattr(settings, "llm_default_model", "local/test-open-model")
    monkeypatch.setattr(settings, "llm_local_max_tokens", 256)
    seen = {}

    async def fake_completion(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"claims": []}'))],
            usage=SimpleNamespace(total_tokens=8),
        )

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(api_key=None, acompletion=fake_completion),
    )
    schema = {"type": "object", "required": ["claims"]}

    result = await LLMGateway().invoke_json(
        "Extract claims.",
        "Evidence.",
        json_schema=schema,
    )

    assert result == {"claims": []}
    assert seen["response_format"] == {"type": "json_object", "schema": schema}


@pytest.mark.asyncio
async def test_hosted_invoke_uses_strict_json_schema_response_format(monkeypatch):
    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_external_zero_cost_confirmed", True)
    seen = {}

    async def fake_completion(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"claims": []}'))],
            usage=SimpleNamespace(total_tokens=8),
        )

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(api_key=None, acompletion=fake_completion),
    )
    schema = {
        "type": "object",
        "properties": {"claims": {"type": "array"}},
        "required": ["claims"],
        "additionalProperties": False,
    }

    result = await LLMGateway().invoke_json(
        "Extract claims.",
        "Evidence.",
        json_schema=schema,
    )

    assert result == {"claims": []}
    assert seen["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "cyber_team_response",
            "strict": True,
            "schema": schema,
        },
    }


@pytest.mark.asyncio
async def test_capability_gate_routes_to_qualified_local_model(monkeypatch):
    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_external_zero_cost_confirmed", True)
    monkeypatch.setattr(settings, "llm_local_fallback_enabled", True)
    monkeypatch.setattr(settings, "llm_local_api_base", "http://llama:8080/v1")
    monkeypatch.setattr(settings, "llm_local_model", "local/qualified-model")
    monkeypatch.setattr(settings, "model_capability_enforcement_enabled", True)
    providers = []
    completion_models = []

    async def checker(*, task_type, provider, model):
        providers.append((task_type, provider, model))
        if provider != "llama_cpp":
            raise RuntimeError("primary model is not qualified")

    async def fake_completion(**kwargs):
        completion_models.append(kwargs["model"])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Qualified."))],
            usage=SimpleNamespace(total_tokens=8),
        )

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(api_key=None, acompletion=fake_completion),
    )
    gateway = LLMGateway()
    gateway.set_capability_checker(checker)

    result = await gateway.invoke(
        "System",
        "Task",
        capability_task="domain_planning",
    )

    assert result == "Qualified."
    assert [item[1] for item in providers] == ["mistral", "llama_cpp"]
    assert completion_models == ["local/qualified-model"]


@pytest.mark.asyncio
async def test_capability_gate_fails_closed_before_model_invocation(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "llama_cpp")
    monkeypatch.setattr(settings, "llm_api_base", "http://llama:8080/v1")
    monkeypatch.setattr(settings, "model_capability_enforcement_enabled", True)
    completion = AsyncMock()

    async def checker(**kwargs):
        raise RuntimeError("task capability is missing")

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(api_key=None, acompletion=completion),
    )
    gateway = LLMGateway()
    gateway.set_capability_checker(checker)

    with pytest.raises(RuntimeError, match="capability is missing"):
        await gateway.invoke(
            "System",
            "Task",
            capability_task="observer_review",
        )

    completion.assert_not_awaited()
