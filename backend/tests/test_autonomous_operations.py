import pytest

from cyber_team.operations.autonomous import AutonomousOperationsService


class FakeMemorySteward:
    def __init__(self, calls, result=None, error=None):
        self.calls = calls
        self.result = result or {}
        self.error = error

    async def run_once(self, **kwargs):
        self.calls.append(("memory_steward", kwargs))
        if self.error:
            raise self.error
        return self.result


class FakeSupervisorReview:
    def __init__(self, calls, result=None, error=None):
        self.calls = calls
        self.result = result or {}
        self.error = error

    async def run_once(self, **kwargs):
        self.calls.append(("supervisor_review", kwargs))
        if self.error:
            raise self.error
        return self.result


class FakeAudit:
    def __init__(self):
        self.events = []

    async def record(self, **kwargs):
        self.events.append(kwargs)


@pytest.mark.asyncio
async def test_autonomous_cycle_runs_memory_then_supervisor_and_records_audit():
    calls = []
    audit = FakeAudit()
    service = AutonomousOperationsService(
        memory_steward_service=FakeMemorySteward(
            calls,
            {
                "findings_created": 1,
                "findings_updated": 2,
                "remediation_plan": {
                    "actions_applied": 1,
                    "approvals_requested": 1,
                    "plans_created": 2,
                    "blocked": 0,
                },
            },
        ),
        supervisor_review_service=FakeSupervisorReview(
            calls,
            {
                "role_gaps_reviewed": 2,
                "role_gaps_proposed": ["gap_1"],
                "workflow_failure_gaps": [{"gap_id": "gap_2"}],
                "stale_approvals": [{"approval_id": "approval_1"}],
            },
        ),
        audit_service=audit,
    )

    result = await service.run_once(actor="owner@example.com")

    assert result["status"] == "completed"
    assert [call[0] for call in calls] == ["memory_steward", "supervisor_review"]
    assert result["counts"] == {
        "memory_findings_created": 1,
        "memory_findings_updated": 2,
        "memory_actions_applied": 1,
        "memory_approvals_requested": 1,
        "memory_plans_created": 2,
        "memory_blocks": 0,
        "role_gaps_reviewed": 2,
        "role_gaps_proposed": 1,
        "workflow_failure_gaps": 1,
        "stale_approvals": 1,
    }
    assert {decision["decision"] for decision in result["decisions"]} == {
        "memory_findings_reviewed",
        "memory_remediation_planned",
        "role_proposals_generated",
        "workflow_failure_gaps_reported",
        "stale_approvals_flagged",
    }
    assert audit.events[0]["event_type"] == "autonomous_operations.cycle"
    assert audit.events[0]["outcome"] == "completed"


@pytest.mark.asyncio
async def test_autonomous_cycle_degrades_and_continues_after_step_failure():
    calls = []
    service = AutonomousOperationsService(
        memory_steward_service=FakeMemorySteward(
            calls,
            error=RuntimeError("memory unavailable"),
        ),
        supervisor_review_service=FakeSupervisorReview(
            calls,
            {
                "role_gaps_reviewed": 0,
                "role_gaps_proposed": [],
                "workflow_failure_gaps": [],
                "stale_approvals": [],
            },
        ),
    )

    result = await service.run_once(actor="owner@example.com")

    assert result["status"] == "degraded"
    assert [call[0] for call in calls] == ["memory_steward", "supervisor_review"]
    assert result["errors"] == [
        {
            "step": "memory_steward",
            "type": "RuntimeError",
            "message": "memory unavailable",
        }
    ]


@pytest.mark.asyncio
async def test_autonomous_cycle_records_failed_cycle_before_reraising():
    calls = []
    audit = FakeAudit()
    service = AutonomousOperationsService(
        memory_steward_service=FakeMemorySteward(
            calls,
            error=RuntimeError("memory unavailable"),
        ),
        supervisor_review_service=FakeSupervisorReview(calls),
        audit_service=audit,
    )

    with pytest.raises(RuntimeError, match="memory unavailable"):
        await service.run_once(continue_on_error=False)

    assert [call[0] for call in calls] == ["memory_steward"]
    assert audit.events[0]["outcome"] == "failed"
    assert audit.events[0]["metadata"]["errors"] == [
        {
            "step": "memory_steward",
            "type": "RuntimeError",
            "message": "memory unavailable",
        }
    ]


@pytest.mark.asyncio
async def test_autonomous_cycle_can_skip_all_steps():
    calls = []
    audit = FakeAudit()
    service = AutonomousOperationsService(
        memory_steward_service=FakeMemorySteward(calls),
        supervisor_review_service=FakeSupervisorReview(calls),
        audit_service=audit,
    )

    result = await service.run_once(
        run_memory_steward=False,
        run_supervisor_review=False,
    )

    assert result["status"] == "skipped"
    assert calls == []
    assert audit.events[0]["outcome"] == "skipped"
