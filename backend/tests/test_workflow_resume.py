from types import SimpleNamespace

import pytest

from cyber_team.agents import orchestrator as orchestrator_module
from cyber_team.agents.orchestrator import Orchestrator
from cyber_team.db.models import ApprovalRequest, WorkflowRun


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value


class FakeSession:
    def __init__(self, run, approval):
        self.run = run
        self.approval = approval
        self.commits = 0

    async def execute(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is WorkflowRun:
            return FakeResult(self.run)
        if entity is ApprovalRequest:
            return FakeResult(self.approval)
        raise AssertionError(f"Unexpected statement entity: {entity}")

    async def commit(self):
        self.commits += 1


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeWorkflowHandle:
    def __init__(self):
        self.signals = []

    async def signal(self, signal_name, status):
        self.signals.append((signal_name, status))


class FakeTemporalClient:
    def __init__(self, handle):
        self.handle = handle

    def get_workflow_handle(self, run_id):
        assert run_id == "run-1"
        return self.handle


def workflow_run(status="waiting_approval"):
    return SimpleNamespace(
        id="run-1",
        workflow_id="workflow-1",
        status=status,
        current_node="approve_step",
        state={"approve_step_approval_id": "approval-1"},
        result=None,
        error=None,
        completed_at=None,
    )


def approval(status="approved", note=""):
    return SimpleNamespace(id="approval-1", status=status, review_note=note)


def patch_resume_dependencies(monkeypatch, run, approval_request):
    session = FakeSession(run, approval_request)
    handle = FakeWorkflowHandle()

    monkeypatch.setattr(
        orchestrator_module,
        "async_session",
        lambda: FakeSessionContext(session),
    )

    class FakeClient:
        @staticmethod
        async def connect(*args, **kwargs):
            return FakeTemporalClient(handle)

    monkeypatch.setattr(orchestrator_module, "Client", FakeClient)
    return session, handle


@pytest.mark.asyncio
async def test_resume_workflow_run_signals_approved_decision(monkeypatch):
    run = workflow_run()
    session, handle = patch_resume_dependencies(monkeypatch, run, approval("approved"))

    result = await Orchestrator(None, None).resume_workflow_run("run-1")

    assert handle.signals == [("approval_signal", "approved")]
    assert result["status"] == "waiting_approval"
    assert session.commits == 0


@pytest.mark.asyncio
async def test_resume_workflow_run_rejected_decision_marks_run_rejected(monkeypatch):
    run = workflow_run()
    session, handle = patch_resume_dependencies(
        monkeypatch,
        run,
        approval("rejected", note="Too risky"),
    )

    result = await Orchestrator(None, None).resume_workflow_run("run-1")

    assert handle.signals == [("approval_signal", "rejected")]
    assert result["status"] == "rejected"
    assert result["error"] == "Too risky"
    assert run.completed_at is not None
    assert session.commits == 1


@pytest.mark.asyncio
async def test_resume_workflow_run_rejects_pending_approval(monkeypatch):
    run = workflow_run()
    patch_resume_dependencies(monkeypatch, run, approval("pending"))

    with pytest.raises(ValueError, match="is still pending"):
        await Orchestrator(None, None).resume_workflow_run("run-1")


@pytest.mark.asyncio
async def test_resume_workflow_run_requires_waiting_state(monkeypatch):
    run = workflow_run(status="running")
    patch_resume_dependencies(monkeypatch, run, approval("approved"))

    with pytest.raises(ValueError, match="is not waiting for approval"):
        await Orchestrator(None, None).resume_workflow_run("run-1")
