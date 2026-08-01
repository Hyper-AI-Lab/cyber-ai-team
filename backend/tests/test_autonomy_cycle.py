from unittest.mock import AsyncMock

import pytest

from cyber_team.config import settings
from cyber_team.operations.autonomy_cycle import (
    AutonomousCompanyCycleService,
    TemporalAutonomyController,
)
from cyber_team.worker import AutonomousCompanySignalWorkflowV4


@pytest.mark.asyncio
async def test_company_cycle_runs_evidence_to_outcome_sequence():
    intelligence = AsyncMock()
    intelligence.acquire_available_evidence.return_value = {"status": "completed"}
    intelligence.discover_company_model.return_value = {
        "status": "active",
        "id": "model-1",
    }
    intelligence.research_model_unknowns.return_value = {
        "status": "completed",
        "created": 0,
    }
    strategy = AsyncMock()
    strategy.run_strategy_cycle.return_value = {"status": "completed"}
    work = AsyncMock()
    work.ensure_active_agent_mandates.return_value = {"created": 1}
    work.route_pending_events.return_value = {"processed": 2}
    work.run_all_domain_loops.return_value = {"agents": 3, "processed": 2}
    outcomes = AsyncMock()
    outcomes.assess_terminal_work.return_value = {
        "assessed": 2,
        "remediation": None,
    }
    policy = AsyncMock()
    policy.ensure_default_policies.return_value = {"created": 0}
    audit = AsyncMock()
    service = AutonomousCompanyCycleService(
        intelligence_service=intelligence,
        strategy_service=strategy,
        work_portfolio_service=work,
        outcome_learning_service=outcomes,
        action_policy_service=policy,
        audit_service=audit,
    )

    result = await service.run(trigger="test", event_ids=["event-1", "event-1"])

    assert result["status"] == "completed"
    assert result["event_ids"] == ["event-1"]
    assert result["domain_work"] == {"agents": 3, "processed": 2}
    assert result["outcomes"]["assessed"] == 2
    intelligence.discover_company_model.assert_awaited_once_with(
        acquire=False,
        activate_if_ready=True,
        actor="company_discovery_agent",
    )
    audit.record_control_evidence.assert_awaited_once()


class ExistingHandle:
    def __init__(self):
        self.updated = False
        self.signals = []

    async def describe(self):
        return {"status": "running"}

    async def update(self, callback):
        callback(None)
        self.updated = True

    async def signal(self, name, value):
        self.signals.append((name, value))


class ExistingTemporalClient:
    def __init__(self):
        self.schedules = {}
        self.workflow = ExistingHandle()

    def get_schedule_handle(self, schedule_id):
        return self.schedules.setdefault(schedule_id, ExistingHandle())

    def get_workflow_handle(self, _workflow_id):
        return self.workflow


@pytest.mark.asyncio
async def test_temporal_controller_reconciles_schedule_and_signals(monkeypatch):
    client = ExistingTemporalClient()

    async def connect(*_args, **_kwargs):
        return client

    monkeypatch.setattr(settings, "company_autonomy_temporal_schedule_enabled", True)
    monkeypatch.setattr(settings, "governor_enabled", True)
    monkeypatch.setattr(settings, "company_autonomy_signal_max_cycles", 10)
    monkeypatch.setattr(settings, "company_autonomy_signal_max_buffered_events", 100)
    controller = TemporalAutonomyController(client_factory=connect)

    status = await controller.ensure()
    signal = await controller.signal("event-1")

    assert status["status"] == "ready"
    assert status["schedule_created"] is False
    assert client.schedules[settings.company_autonomy_schedule_id].updated is True
    assert client.schedules[settings.governor_temporal_schedule_id].updated is True
    assert status["governor_schedule_id"] == settings.governor_temporal_schedule_id
    assert status["signal_max_cycles"] == 10
    assert status["signal_max_buffered_events"] == 100
    assert signal == {"status": "signaled", "event_id": "event-1"}
    assert client.workflow.signals == [("business_event_received", "event-1")]


def test_signal_workflow_config_bounds_history_and_buffer(monkeypatch):
    monkeypatch.setattr(settings, "company_autonomy_signal_max_cycles", 0)
    monkeypatch.setattr(settings, "company_autonomy_signal_max_buffered_events", 9999)

    config = TemporalAutonomyController._signal_workflow_config()

    assert config == {
        "cycle_version": AutonomousCompanyCycleService.CYCLE_VERSION,
        "max_cycles": 1,
        "max_buffered_events": 500,
    }


def test_v4_signal_workflow_deduplicates_and_bounds_buffer():
    signal_workflow = AutonomousCompanySignalWorkflowV4()
    signal_workflow._max_buffered_events = 2

    signal_workflow.business_event_received("event-1")
    signal_workflow.business_event_received("event-1")
    signal_workflow.business_event_received("event-2")
    signal_workflow.business_event_received("event-3")

    assert signal_workflow._event_ids == ["event-1", "event-2"]
