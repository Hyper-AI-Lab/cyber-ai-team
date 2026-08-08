from types import SimpleNamespace

import pytest

from cyber_team.agents import manager as manager_module
from cyber_team.agents.manager import AgentManager
from cyber_team.clock import utc_now


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeScalarList:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class FakeListResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return FakeScalarList(self._values)


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


class FakeQueueSession:
    def __init__(self, requests):
        self.requests = requests
        self.commits = 0
        self.execute_calls = 0

    async def execute(self, statement):
        self.execute_calls += 1
        if self.execute_calls == 1:
            return FakeListResult(
                [
                    request
                    for request in self.requests
                    if request.status == "pending"
                    and request.expires_at is not None
                    and request.expires_at < utc_now()
                ]
            )
        return FakeListResult([request for request in self.requests if request.status == "pending"])

    async def commit(self):
        self.commits += 1


class FakeReusableApprovalSession:
    def __init__(self, approvals):
        self.approvals = approvals
        self.added = []
        self.commits = 0

    async def execute(self, statement):
        return FakeListResult(self.approvals)

    def add(self, request):
        self.added.append(request)
        self.approvals.append(request)

    async def commit(self):
        self.commits += 1


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
        action_description="Send email",
        action_payload={},
        agent_id=None,
        requester="system",
        requester_type="system",
        target_type="tool",
        target_id="send_email",
        risk_level="high",
        reviewer=None,
        review_note=None,
        resolved_at=None,
        consumed_at=None,
        expires_at=None,
        created_at=utc_now(),
    )


def patch_session(monkeypatch, request):
    session = FakeSession(request)
    monkeypatch.setattr(
        manager_module,
        "async_session",
        lambda: FakeSessionContext(session),
    )
    return session


def patch_queue_session(monkeypatch, requests):
    session = FakeQueueSession(requests)
    monkeypatch.setattr(
        manager_module,
        "async_session",
        lambda: FakeSessionContext(session),
    )
    return session


def patch_reusable_approval_session(monkeypatch, approvals):
    session = FakeReusableApprovalSession(approvals)
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
async def test_get_approval_queue_expires_stale_pending_requests(monkeypatch):
    expired = approval_request(status="pending")
    expired.id = "expired-approval"
    expired.expires_at = utc_now() - manager_module.timedelta(minutes=1)
    fresh = approval_request(status="pending")
    fresh.id = "fresh-approval"
    fresh.expires_at = utc_now() + manager_module.timedelta(minutes=10)
    session = patch_queue_session(monkeypatch, [expired, fresh])

    queue = await AgentManager().get_approval_queue()

    assert [item["id"] for item in queue] == ["fresh-approval"]
    assert expired.status == "expired"
    assert expired.resolved_at is not None
    assert session.commits == 1


@pytest.mark.asyncio
async def test_request_approval_reuses_pending_targeted_request(monkeypatch):
    pending = approval_request(status="pending")
    pending.id = "approval-pending"
    pending.action_type = "memory_steward.report_role_gap"
    pending.target_type = "memory_steward_finding"
    pending.target_id = "finding-1"
    pending.expires_at = utc_now() + manager_module.timedelta(minutes=10)
    session = patch_reusable_approval_session(monkeypatch, [pending])

    approval_id = await AgentManager()._request_approval(
        None,
        "memory_steward.report_role_gap",
        "Review finding",
        {"finding_id": "finding-1"},
        requester="planner",
        requester_type="agent",
        target_type="memory_steward_finding",
        target_id="finding-1",
    )

    assert approval_id == "approval-pending"
    assert session.added == []
    assert session.commits == 0


@pytest.mark.asyncio
async def test_request_approval_reissues_after_expired_targeted_request(monkeypatch):
    expired = approval_request(status="pending")
    expired.id = "approval-expired"
    expired.action_type = "memory_steward.report_role_gap"
    expired.target_type = "memory_steward_finding"
    expired.target_id = "finding-1"
    expired.expires_at = utc_now() - manager_module.timedelta(minutes=1)
    session = patch_reusable_approval_session(monkeypatch, [expired])

    approval_id = await AgentManager()._request_approval(
        None,
        "memory_steward.report_role_gap",
        "Review finding",
        {"finding_id": "finding-1"},
        requester="planner",
        requester_type="agent",
        target_type="memory_steward_finding",
        target_id="finding-1",
    )

    assert approval_id != "approval-expired"
    assert expired.status == "expired"
    assert expired.resolved_at is not None
    assert len(session.added) == 1
    assert session.commits == 2


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
async def test_approval_binding_rejects_modified_payload(monkeypatch):
    request = approval_request()
    request.action_payload = {"approval_binding": {"request_hash": "approved-payload-hash"}}
    patch_session(monkeypatch, request)

    result = await AgentManager().approval_is_executable(
        "approval-1",
        target_type="tool",
        target_id="send_email",
        binding_hash="modified-payload-hash",
    )

    assert result is False
    assert request.consumed_at is None


@pytest.mark.asyncio
async def test_consume_approval_requires_exact_payload_binding(monkeypatch):
    request = approval_request()
    request.action_payload = {"approval_binding": {"request_hash": "approved-payload-hash"}}
    patch_session(monkeypatch, request)

    with pytest.raises(ValueError, match="payload binding does not match"):
        await AgentManager().consume_approval(
            "approval-1",
            consumer="tool:send_email",
            target_type="tool",
            target_id="send_email",
            binding_hash="modified-payload-hash",
        )

    assert request.consumed_at is None


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
