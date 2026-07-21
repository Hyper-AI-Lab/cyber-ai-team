import pytest

from cyber_team.config import settings
from cyber_team.llm.gateway import LLMGateway


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
