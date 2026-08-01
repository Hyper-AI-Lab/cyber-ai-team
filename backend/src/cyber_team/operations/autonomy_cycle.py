"""Durable evidence-to-outcome company autonomy cycle and Temporal control."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleState,
)
from temporalio.common import RetryPolicy
from temporalio.service import RPCError, RPCStatusCode

from cyber_team.clock import utc_now
from cyber_team.config import settings


class AutonomousCompanyCycleService:
    """Run the complete idempotent evidence-to-outcome operating cycle."""

    CYCLE_VERSION = "autonomous-company-cycle-v3"

    def __init__(
        self,
        *,
        intelligence_service,
        strategy_service,
        work_portfolio_service,
        outcome_learning_service,
        action_policy_service,
        audit_service=None,
    ) -> None:
        self._intelligence = intelligence_service
        self._strategy = strategy_service
        self._work = work_portfolio_service
        self._outcomes = outcome_learning_service
        self._policy = action_policy_service
        self._audit = audit_service

    async def run(
        self,
        *,
        trigger: str = "scheduled",
        event_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        started_at = utc_now()
        acquisition = await self._intelligence.acquire_available_evidence()
        discovery = await self._intelligence.discover_company_model(
            acquire=False,
            activate_if_ready=True,
            actor="company_discovery_agent",
        )
        research = await self._intelligence.research_model_unknowns(discovery)
        if research.get("created"):
            discovery = await self._intelligence.discover_company_model(
                acquire=False,
                activate_if_ready=True,
                actor="company_discovery_agent",
            )
        strategy = await self._strategy.run_strategy_cycle()
        mandates = await self._work.ensure_active_agent_mandates()
        routing = await self._work.route_pending_events()
        domain_work = await self._work.run_all_domain_loops(max_items_per_agent=1)
        outcomes = await self._outcomes.assess_terminal_work()
        policies = await self._policy.ensure_default_policies()
        result = {
            "status": "completed",
            "cycle_version": self.CYCLE_VERSION,
            "trigger": trigger,
            "event_ids": list(dict.fromkeys(event_ids or []))[:200],
            "started_at": started_at.isoformat(),
            "completed_at": utc_now().isoformat(),
            "acquisition": acquisition,
            "discovery": discovery,
            "public_research": research,
            "strategy": strategy,
            "mandates": mandates,
            "routing": routing,
            "domain_work": {
                "agents": domain_work["agents"],
                "processed": domain_work["processed"],
            },
            "outcomes": {
                "assessed": outcomes["assessed"],
                "remediation": outcomes["remediation"],
            },
            "policies": policies,
        }
        if self._audit:
            await self._audit.record_control_evidence(
                control_id="autonomy.company_cycle",
                control_area="ai_governance",
                actor="chief_operating_agent",
                outcome="success",
                evidence=result,
            )
        return result


class TemporalAutonomyController:
    """Own the Temporal schedule and event-signal workflow lifecycle."""

    def __init__(self, *, client_factory=None) -> None:
        self._client_factory = client_factory or Client.connect

    async def ensure(self) -> dict[str, Any]:
        client = await self._client_factory(
            settings.temporal_url,
            namespace=settings.temporal_namespace,
        )
        schedule = self._schedule()
        schedule_created = await self._ensure_schedule(
            client,
            settings.company_autonomy_schedule_id,
            schedule,
        )
        governor_schedule_created = False
        if settings.governor_enabled:
            governor_schedule_created = await self._ensure_schedule(
                client,
                settings.governor_temporal_schedule_id,
                self._governor_schedule(),
            )

        signal_started = False
        workflow_handle = client.get_workflow_handle(
            settings.company_autonomy_signal_workflow_id
        )
        try:
            await workflow_handle.describe()
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise
            await client.start_workflow(
                "AutonomousCompanySignalWorkflowV4",
                self._signal_workflow_config(),
                id=settings.company_autonomy_signal_workflow_id,
                task_queue="cyberteam-tasks",
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=5),
                    maximum_interval=timedelta(minutes=5),
                    maximum_attempts=0,
                ),
            )
            signal_started = True
        return {
            "status": "ready",
            "schedule_id": settings.company_autonomy_schedule_id,
            "schedule_created": schedule_created,
            "governor_schedule_id": (
                settings.governor_temporal_schedule_id
                if settings.governor_enabled
                else None
            ),
            "governor_schedule_created": governor_schedule_created,
            "signal_workflow_id": settings.company_autonomy_signal_workflow_id,
            "signal_workflow_started": signal_started,
            "signal_max_cycles": settings.company_autonomy_signal_max_cycles,
            "signal_max_buffered_events": (
                settings.company_autonomy_signal_max_buffered_events
            ),
            "interval_seconds": settings.domain_loop_interval_seconds,
            "governor_interval_seconds": settings.governor_interval_seconds,
        }

    @classmethod
    async def _ensure_schedule(cls, client, schedule_id: str, schedule: Schedule) -> bool:
        handle = client.get_schedule_handle(schedule_id)
        try:
            await handle.describe()
            await handle.update(lambda _: cls._schedule_update(schedule))
            return False
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise
        await client.create_schedule(schedule_id, schedule)
        return True

    async def signal(self, event_id: str) -> dict[str, Any]:
        if not settings.company_autonomy_temporal_schedule_enabled:
            return {"status": "disabled", "event_id": event_id}
        client = await self._client_factory(
            settings.temporal_url,
            namespace=settings.temporal_namespace,
        )
        handle = client.get_workflow_handle(settings.company_autonomy_signal_workflow_id)
        await handle.signal("business_event_received", str(event_id)[:200])
        return {"status": "signaled", "event_id": event_id}

    @staticmethod
    def _signal_workflow_config() -> dict[str, Any]:
        return {
            "cycle_version": AutonomousCompanyCycleService.CYCLE_VERSION,
            "max_cycles": max(1, min(settings.company_autonomy_signal_max_cycles, 100)),
            "max_buffered_events": max(
                1,
                min(settings.company_autonomy_signal_max_buffered_events, 500),
            ),
        }

    @staticmethod
    def _schedule_update(schedule: Schedule):
        from temporalio.client import ScheduleUpdate

        return ScheduleUpdate(schedule=schedule)

    @staticmethod
    def _schedule() -> Schedule:
        interval = max(60, settings.domain_loop_interval_seconds)
        return Schedule(
            action=ScheduleActionStartWorkflow(
                "AutonomousCompanyCycleWorkflow",
                {"trigger": "scheduled", "event_ids": []},
                id="autonomous-company-cycle-scheduled",
                task_queue="cyberteam-tasks",
                execution_timeout=timedelta(minutes=45),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=10),
                    maximum_interval=timedelta(minutes=5),
                    maximum_attempts=3,
                ),
            ),
            spec=ScheduleSpec(
                intervals=[ScheduleIntervalSpec(every=timedelta(seconds=interval))]
            ),
            policy=SchedulePolicy(
                overlap=ScheduleOverlapPolicy.SKIP,
                catchup_window=timedelta(minutes=15),
                pause_on_failure=False,
            ),
            state=ScheduleState(
                note="Cyber-Team evidence-to-outcome company autonomy cycle",
                paused=False,
            ),
        )

    @staticmethod
    def _governor_schedule() -> Schedule:
        interval = max(300, settings.governor_interval_seconds)
        return Schedule(
            action=ScheduleActionStartWorkflow(
                "ExecutiveGovernorWorkflow",
                {
                    "actor": "chief_operating_agent_scheduler",
                    "dry_run": False,
                    "auto_apply_low_risk": settings.governor_auto_apply_low_risk,
                    "max_actions": settings.governor_max_actions_per_cycle,
                    "observer_review": settings.observer_review_required,
                },
                id="executive-governor-scheduled",
                task_queue="cyberteam-tasks",
                execution_timeout=timedelta(minutes=45),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=10),
                    maximum_interval=timedelta(minutes=5),
                    maximum_attempts=3,
                ),
            ),
            spec=ScheduleSpec(
                intervals=[ScheduleIntervalSpec(every=timedelta(seconds=interval))]
            ),
            policy=SchedulePolicy(
                overlap=ScheduleOverlapPolicy.SKIP,
                catchup_window=timedelta(minutes=15),
                pause_on_failure=False,
            ),
            state=ScheduleState(
                note="Cyber-Team durable Chief Operating Agent executive cycle",
                paused=False,
            ),
        )
