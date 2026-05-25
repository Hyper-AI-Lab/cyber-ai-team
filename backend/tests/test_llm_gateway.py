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
