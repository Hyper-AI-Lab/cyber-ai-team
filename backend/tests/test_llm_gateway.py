import sys
from types import SimpleNamespace

import pytest

from cyber_team.config import settings
from cyber_team.llm.gateway import LLMCircuitOpenError, LLMGateway


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
async def test_invoke_retries_rate_limit_then_records_recovery(monkeypatch):
    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
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
    gateway = LLMGateway()

    result = await gateway.invoke("System", "Task", agent_id="ops")

    assert result == "Recovered."
    assert calls == 2
    assert gateway.runtime_status()["status"] == "ready"
    assert gateway.runtime_status()["last_invocation"]["outcome"] == "success"


@pytest.mark.asyncio
async def test_invoke_opens_circuit_after_repeated_rate_limits(monkeypatch):
    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
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
