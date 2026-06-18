from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cyber_team.api import _run_operating_cadence_scheduler_once


@pytest.mark.asyncio
async def test_operating_cadence_scheduler_forces_manual_only_auto_execute(monkeypatch):
    app = SimpleNamespace(state=SimpleNamespace())
    app.state.autonomous_planning_service = AsyncMock()
    app.state.autonomous_planning_service.scan_operating_cadences.return_value = {
        "scanned_at": "2026-06-18T10:00:00+00:00",
        "cadences_reviewed": 3,
        "cadences_due": 1,
        "plans_created": 1,
        "plans_existing": 0,
        "created_plan_ids": ["plan_123"],
        "existing_plan_ids": [],
        "errors": [],
        "execution": None,
    }
    app.state.audit_service = AsyncMock()
    monkeypatch.setattr(
        "cyber_team.api.settings.operating_cadence_scheduler_auto_execute",
        True,
    )
    monkeypatch.setattr(
        "cyber_team.api.settings.autonomy_side_effect_mode",
        "manual_only",
    )
    monkeypatch.setattr(
        "cyber_team.api.settings.operating_cadence_scheduler_limit",
        25,
    )

    result = await _run_operating_cadence_scheduler_once(app)

    app.state.autonomous_planning_service.scan_operating_cadences.assert_awaited_once_with(
        actor="operating_cadence_scheduler",
        auto_execute=False,
        limit=25,
    )
    assert result["status"] == "completed"
    assert result["auto_execute"] is False
    assert result["last_result"]["plans_created"] == 1
    app.state.audit_service.record.assert_awaited_once()
    audit_call = app.state.audit_service.record.await_args.kwargs
    assert audit_call["event_type"] == "operating_cadence.scheduler_run"
    assert audit_call["outcome"] == "success"
    assert audit_call["metadata"]["auto_execute"] is False


@pytest.mark.asyncio
async def test_operating_cadence_scheduler_reports_scan_failure(monkeypatch):
    app = SimpleNamespace(state=SimpleNamespace())
    app.state.autonomous_planning_service = AsyncMock()
    app.state.autonomous_planning_service.scan_operating_cadences.side_effect = (
        RuntimeError("planner unavailable")
    )
    app.state.audit_service = AsyncMock()
    monkeypatch.setattr(
        "cyber_team.api.settings.operating_cadence_scheduler_auto_execute",
        False,
    )

    result = await _run_operating_cadence_scheduler_once(app)

    assert result["status"] == "failed"
    assert result["last_error"] == "planner unavailable"
    assert app.state.operating_cadence_scheduler_status["status"] == "failed"
    audit_call = app.state.audit_service.record.await_args.kwargs
    assert audit_call["outcome"] == "failure"
    assert audit_call["metadata"]["error"] == "planner unavailable"
