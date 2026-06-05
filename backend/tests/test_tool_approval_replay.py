from unittest.mock import AsyncMock

import pytest

from cyber_team.tools.registry import ToolRegistry


class FakeCommsGateway:
    def __init__(self):
        self.sent = []

    async def send_email(self, data):
        self.sent.append(data)
        return {"status": "sent", "recipient": data.to_address}


@pytest.mark.asyncio
async def test_approved_tool_execution_consumes_single_use_approval():
    registry = ToolRegistry()
    manager = AsyncMock()
    manager.approval_is_executable.return_value = True
    registry.set_services(comms=FakeCommsGateway(), agent_manager=manager)

    result = await registry.execute(
        "send_email",
        {
            "to_address": "customer@example.com",
            "subject": "Welcome",
            "body": "<p>Hello</p>",
            "_agent_id": "sales",
            "_approval_id": "approval-1",
        },
    )

    assert result.success is True
    assert result.output == {"status": "sent", "recipient": "customer@example.com"}
    manager.approval_is_executable.assert_awaited_once_with(
        "approval-1",
        target_type="tool",
        target_id="send_email",
    )
    manager.consume_approval.assert_awaited_once_with(
        "approval-1",
        consumer="tool:send_email",
        target_type="tool",
        target_id="send_email",
    )


@pytest.mark.asyncio
async def test_tool_execution_requests_approval_before_sensitive_side_effect():
    registry = ToolRegistry()
    manager = AsyncMock()
    manager.approval_is_executable.return_value = False
    manager._request_approval.return_value = "approval-2"
    comms = FakeCommsGateway()
    registry.set_services(comms=comms, agent_manager=manager)

    result = await registry.execute(
        "send_email",
        {
            "to_address": "customer@example.com",
            "subject": "Welcome",
            "body": "<p>Hello</p>",
            "_agent_id": "sales",
        },
    )

    assert result.success is False
    assert result.output["approval_required"] is True
    assert result.output["approval_id"] == "approval-2"
    assert result.output["tool_name"] == "send_email"
    assert result.output["risk_level"] == "high"
    assert result.output["target"] == {"type": "tool", "id": "send_email"}
    assert result.output["replay_instructions"]["path"] == "/api/tools/execute"
    assert comms.sent == []
    manager._request_approval.assert_awaited_once()


@pytest.mark.asyncio
async def test_tool_execution_does_not_mutate_caller_params():
    registry = ToolRegistry()
    manager = AsyncMock()
    manager.approval_is_executable.return_value = True
    registry.set_services(comms=FakeCommsGateway(), agent_manager=manager)
    params = {
        "to_address": "customer@example.com",
        "subject": "Welcome",
        "body": "<p>Hello</p>",
        "_agent_id": "sales",
        "_approval_id": "approval-1",
    }

    await registry.execute("send_email", params)

    assert params["_agent_id"] == "sales"
    assert params["_approval_id"] == "approval-1"
