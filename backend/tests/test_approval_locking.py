from types import SimpleNamespace

import pytest

from cyber_team.agents import manager as manager_module
from cyber_team.agents.manager import AgentManager


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    def __init__(self, request):
        self.request = request
        self.statement = None
        self.committed = False

    async def execute(self, statement):
        self.statement = statement
        return FakeResult(self.request)

    async def commit(self):
        self.committed = True


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def approval_request(status="approved"):
    return SimpleNamespace(
        id="approval-1",
        status=status,
        action_type="tool:send_email",
        target_type="tool",
        target_id="send_email",
        risk_level="high",
        reviewer=None,
        review_note=None,
        resolved_at=None,
        consumed_at=None,
        expires_at=None,
    )


def patch_session(monkeypatch, request):
    session = FakeSession(request)
    monkeypatch.setattr(
        manager_module,
        "async_session",
        lambda: FakeSessionContext(session),
    )
    return session


@pytest.mark.asyncio
async def test_resolve_approval_locks_row(monkeypatch):
    session = patch_session(monkeypatch, approval_request(status="pending"))

    result = await AgentManager().resolve_approval("approval-1", "approved")

    assert result == {"id": "approval-1", "status": "approved"}
    assert session.statement._for_update_arg is not None
    assert session.committed is True


@pytest.mark.asyncio
async def test_approval_is_executable_locks_row(monkeypatch):
    session = patch_session(monkeypatch, approval_request())

    result = await AgentManager().approval_is_executable(
        "approval-1",
        target_type="tool",
        target_id="send_email",
    )

    assert result is True
    assert session.statement._for_update_arg is not None


@pytest.mark.asyncio
async def test_consume_approval_locks_row(monkeypatch):
    request = approval_request()
    session = patch_session(monkeypatch, request)

    await AgentManager().consume_approval(
        "approval-1",
        consumer="tool:send_email",
        target_type="tool",
        target_id="send_email",
    )

    assert session.statement._for_update_arg is not None
    assert request.consumed_at is not None
    assert session.committed is True
