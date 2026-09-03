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

    CYCLE_VERSION = "autonomous-company-cycle-v4"
    _SUMMARY_FIELDS = (
        "id",
        "run_id",
        "status",
        "reason",
        "detail",
        "company_namespace",
        "revision",
        "source_hash",
        "confidence",
        "provenance_coverage",
        "refreshed",
        "provider",
        "passed",
        "total",
        "created",
        "updated",
        "reused",
        "superseded",
        "activated",
        "dry_run",
        "operating_model_revision_id",
        "assessment_count",
        "processed",
        "examined",
        "reconciled",
        "coverage",
        "active_agents",
        "agents",
        "permanent_gates",
        "created_at",
        "completed_at",
        "expires_at",
    )
    _REFERENCE_FIELDS = (
        "id",
        "status",
        "revision",
        "source_hash",
        "confidence",
        "provenance_coverage",
        "observer_review_id",
        "created_at",
        "activated_at",
        "reused",
    )

    def __init__(
        self,
        *,
        intelligence_service,
        strategy_service,
        work_portfolio_service,
        outcome_learning_service,
        action_policy_service,
        model_capability_service=None,
        operating_model_service=None,
        tool_registry=None,
        audit_service=None,
    ) -> None:
        self._intelligence = intelligence_service
        self._strategy = strategy_service
        self._work = work_portfolio_service
        self._outcomes = outcome_learning_service
        self._policy = action_policy_service
        self._model_capabilities = model_capability_service
        self._operating_model = operating_model_service
        self._tools = tool_registry
        self._audit = audit_service

    async def run(
        self,
        *,
        trigger: str = "scheduled",
        event_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        started_at = utc_now()
        capability_qualification = (
            await self._model_capabilities.ensure_fresh(
                actor="chief_operating_agent_scheduler"
            )
            if self._model_capabilities
            else {"status": "not_configured", "refreshed": False}
        )
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
        if self._operating_model:
            operating_model = await self._operating_model.synthesize_and_review()
            reconciliation = await self._operating_model.reconcile()
            convergence = await self._operating_model.converge_roles_and_mandates()
            discovery_obligations = (
                await self._operating_model.reconcile_discovery_obligations()
            )
            backlog_reconciliation = await self._operating_model.reconcile_backlogs()
        else:
            operating_model = {"status": "not_configured"}
            reconciliation = {"status": "not_configured"}
            convergence = {"status": "not_configured"}
            discovery_obligations = {"status": "not_configured"}
            backlog_reconciliation = {"status": "not_configured"}
        mandates = await self._work.ensure_active_agent_mandates()
        policies = await self._policy.ensure_default_policies()
        if self._tools:
            policy_qualification = (
                await self._policy.qualify_registered_action_classes(
                    self._tools.list_tool_contracts(),
                    max_cases_per_class=settings.operating_model_policy_cases_per_cycle,
                )
            )
        else:
            policy_qualification = {"status": "not_configured"}
        routing = await self._work.route_pending_events()
        domain_work = await self._work.run_all_domain_loops(max_items_per_agent=1)
        outcomes = await self._outcomes.assess_terminal_work()
        result = {
            "status": "completed",
            "cycle_version": self.CYCLE_VERSION,
            "trigger": trigger,
            "event_ids": list(dict.fromkeys(event_ids or []))[:200],
            "started_at": started_at.isoformat(),
            "completed_at": utc_now().isoformat(),
            "model_capability_qualification": self._stage_summary(
                capability_qualification
            ),
            "acquisition": self._stage_summary(acquisition),
            "discovery": self._stage_summary(discovery),
            "public_research": self._stage_summary(research),
            "strategy": self._stage_summary(strategy),
            "operating_model": self._stage_summary(operating_model),
            "reconciliation": self._stage_summary(reconciliation),
            "role_mandate_convergence": self._stage_summary(convergence),
            "discovery_obligations": self._stage_summary(discovery_obligations),
            "backlog_reconciliation": self._stage_summary(backlog_reconciliation),
            "mandates": self._stage_summary(mandates),
            "routing": self._stage_summary(routing),
            "domain_work": {
                "agents": domain_work["agents"],
                "processed": domain_work["processed"],
            },
            "outcomes": {
                "assessed": outcomes["assessed"],
                "remediation": outcomes["remediation"],
            },
            "policies": self._stage_summary(policies),
            "policy_qualification": self._stage_summary(policy_qualification),
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

    @classmethod
    def _stage_summary(cls, payload: Any) -> dict[str, Any]:
        """Return a stable activity/API envelope without replaying persisted evidence."""
        if not isinstance(payload, dict):
            return {"status": "invalid_result", "result_type": type(payload).__name__}

        summary: dict[str, Any] = {}
        for field in cls._SUMMARY_FIELDS:
            value = payload.get(field)
            if value is None or isinstance(value, (dict, list, tuple, set)):
                continue
            summary[field] = value

        for field in ("counts", "remediation"):
            value = payload.get(field)
            if isinstance(value, dict):
                summary[field] = cls._bounded_scalars(value)

        for field in (
            "approval_ids",
            "agent_ids",
            "created_plan_ids",
            "invalidated_approval_ids",
            "role_gap_ids",
        ):
            value = payload.get(field)
            if isinstance(value, list):
                summary[field] = [str(item)[:240] for item in value[:100]]
                summary[f"{field}_count"] = len(value)

        errors = payload.get("errors")
        if isinstance(errors, list):
            summary["errors"] = [cls._error_summary(item) for item in errors[:20]]
            summary["error_count"] = len(errors)

        for field in ("model", "observer_review", "review"):
            value = payload.get(field)
            if isinstance(value, dict):
                summary[field] = cls._record_reference(value)

        for field in ("items", "queries", "assessments", "decisions"):
            value = payload.get(field)
            if isinstance(value, list):
                summary[f"{field}_count"] = len(value)
                summary[f"{field}_status_counts"] = cls._status_counts(value)

        if "status" not in summary:
            summary["status"] = str(payload.get("status") or "completed")[:80]
        return summary

    @staticmethod
    def _bounded_scalars(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            str(key)[:120]: value
            for key, value in list(payload.items())[:100]
            if value is None or isinstance(value, (str, int, float, bool))
        }

    @classmethod
    def _record_reference(cls, payload: dict[str, Any]) -> dict[str, Any]:
        reference = {
            field: payload[field]
            for field in cls._REFERENCE_FIELDS
            if field in payload
            and (
                payload[field] is None
                or isinstance(payload[field], (str, int, float, bool))
            )
        }
        domain_keys = payload.get("domain_keys")
        if isinstance(domain_keys, list):
            reference["domain_keys"] = [str(item)[:120] for item in domain_keys[:25]]
            reference["domain_count"] = len(domain_keys)
        model = payload.get("model")
        if isinstance(model, dict):
            for field in ("name", "business_description"):
                value = model.get(field)
                if isinstance(value, str):
                    reference[field] = value[:500]
        return reference

    @staticmethod
    def _status_counts(items: list[Any]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in items:
            status = (
                str(item.get("status") or "unknown")
                if isinstance(item, dict)
                else "unknown"
            )
            counts[status] = counts.get(status, 0) + 1
        return dict(sorted(counts.items()))

    @staticmethod
    def _error_summary(error: Any) -> Any:
        if not isinstance(error, dict):
            return str(error)[:500]
        return {
            str(key)[:120]: value[:500] if isinstance(value, str) else value
            for key, value in list(error.items())[:20]
            if value is None or isinstance(value, (str, int, float, bool))
        }


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
