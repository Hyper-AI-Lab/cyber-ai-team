"""Readiness aggregation for the evidence-to-outcome company control plane."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import desc, func, select

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import (
    ActionClassPolicy,
    Agent,
    AgentMandate,
    BusinessEvent,
    BusinessEventDelivery,
    BusinessWorkItem,
    CompanyModelRevision,
    CompanyObjectiveRevision,
    CompanySource,
    DomainAutonomyControl,
    OperatingKPIRevision,
    OutcomeAssessment,
    WorkflowSpecification,
)


class AutonomousCompanyReadinessService:
    """Explain v3 autonomy readiness without hiding unknown or stale state."""

    CRITICAL_MODEL_FIELDS = {
        "business_description",
        "offerings",
        "customer_segments",
        "jurisdictions",
    }

    def __init__(self, *, llm_gateway=None):
        self._llm = llm_gateway

    async def summary(self) -> dict[str, Any]:
        async with async_session() as session:
            model = (
                await session.execute(
                    select(CompanyModelRevision)
                    .where(CompanyModelRevision.status == "active")
                    .order_by(desc(CompanyModelRevision.revision))
                    .limit(1)
                )
            ).scalar_one_or_none()
            sources = (
                await session.execute(
                    select(CompanySource).where(CompanySource.status == "active")
                )
            ).scalars().all()
            active_agents = int(
                (
                    await session.execute(
                        select(func.count(Agent.id)).where(Agent.status == "active")
                    )
                ).scalar_one()
            )
            mandated_agents = int(
                (
                    await session.execute(
                        select(func.count(func.distinct(AgentMandate.agent_id)))
                        .join(Agent, Agent.id == AgentMandate.agent_id)
                        .where(
                            AgentMandate.status == "active",
                            Agent.status == "active",
                        )
                    )
                ).scalar_one()
            )
            event_counts = await self._counts(session, BusinessEvent.status)
            delivery_counts = await self._counts(session, BusinessEventDelivery.status)
            work_counts = await self._counts(session, BusinessWorkItem.status)
            domain_controls = (
                await session.execute(select(DomainAutonomyControl))
            ).scalars().all()
            latest_objective = (
                await session.execute(
                    select(CompanyObjectiveRevision)
                    .where(
                        CompanyObjectiveRevision.status.in_({"active", "probation"})
                    )
                    .order_by(desc(CompanyObjectiveRevision.created_at))
                    .limit(1)
                )
            ).scalar_one_or_none()
            latest_kpi = (
                await session.execute(
                    select(OperatingKPIRevision)
                    .where(OperatingKPIRevision.status.in_({"active", "probation"}))
                    .order_by(desc(OperatingKPIRevision.created_at))
                    .limit(1)
                )
            ).scalar_one_or_none()
            latest_outcome = await self._latest(
                session, OutcomeAssessment, OutcomeAssessment.created_at
            )
            workflow_counts = await self._counts(session, WorkflowSpecification.status)
            policy_counts = await self._counts(session, ActionClassPolicy.status)
            policies = (
                await session.execute(
                    select(ActionClassPolicy).where(
                        ActionClassPolicy.status != "superseded"
                    )
                )
            ).scalars().all()

        critical_unknowns = sorted(
            field
            for field in self.CRITICAL_MODEL_FIELDS
            if not model or field in (model.unknowns or [])
        )
        company_model = {
            "status": (
                "ready"
                if model and not critical_unknowns
                else "incomplete"
                if model
                else "not_discovered"
            ),
            "blocking": settings.company_autonomy_enabled and not bool(model),
            "revision_id": model.id if model else None,
            "revision": model.revision if model else None,
            "confidence": model.confidence if model else 0.0,
            "provenance_coverage": model.provenance_coverage if model else 0.0,
            "unknown_count": len(model.unknowns or []) if model else None,
            "critical_unknowns": critical_unknowns,
            "dispute_count": len(model.disputes or []) if model else None,
            "activated_at": (
                model.activated_at.isoformat()
                if model and model.activated_at
                else None
            ),
            "detail": (
                "Active company model is evidence-backed."
                if model
                else "No evidence-backed company model has passed activation gates."
            ),
        }
        source_freshness = self._source_freshness(sources)
        mandate_gap = max(0, active_agents - mandated_agents)
        mandates = {
            "status": "ready" if mandate_gap == 0 else "degraded",
            "blocking": mandate_gap > 0,
            "active_agents": active_agents,
            "mandated_agents": mandated_agents,
            "missing_mandates": mandate_gap,
        }
        unexplained = event_counts.get("pending", 0)
        events = {
            "status": "ready" if unexplained == 0 else "pending_work",
            "blocking": unexplained > 0,
            "counts": event_counts,
            "outbox_counts": delivery_counts,
            "unexplained": unexplained,
        }
        strategy = {
            "status": (
                "ready" if latest_objective and latest_kpi else "not_generated"
            ),
            "blocking": settings.company_autonomy_enabled
            and (not latest_objective or not latest_kpi),
            "latest_objective_at": self._timestamp(latest_objective),
            "latest_kpi_at": self._timestamp(latest_kpi),
        }
        workflows = {
            "status": "ready" if workflow_counts.get("active", 0) else "waiting",
            "blocking": False,
            "counts": workflow_counts,
        }
        probation = {
            "status": "active" if policy_counts.get("shadow", 0) else "stable",
            "blocking": False,
            "policy_counts": policy_counts,
            "action_classes": [
                {
                    "action_class": item.action_class,
                    "status": item.status,
                    "validated_cases": item.validated_cases,
                    "required_cases": settings.action_policy_min_validated_cases,
                    "evaluator_score": item.evaluator_score,
                    "permanent_gate": item.permanent_gate,
                }
                for item in policies
            ],
        }
        model_availability = await self._model_availability()
        sections = {
            "company_model": company_model,
            "source_freshness": source_freshness,
            "mandates": mandates,
            "domain_controls": {
                "status": (
                    "owner_controlled"
                    if any(item.state != "active" for item in domain_controls)
                    else "active"
                ),
                "blocking": False,
                "items": [
                    {
                        "domain": item.domain,
                        "state": item.state,
                        "reason": item.reason,
                        "owner": item.owner,
                        "updated_at": item.updated_at.isoformat(),
                    }
                    for item in domain_controls
                ],
            },
            "business_events": events,
            "work_portfolio": {
                "status": "ready",
                "blocking": False,
                "counts": work_counts,
                "latest_outcome_at": self._timestamp(latest_outcome),
            },
            "strategy": strategy,
            "workflow_compiler": workflows,
            "action_probation": probation,
            "model_availability": model_availability,
            "tool_sandbox": {
                "status": "ready" if settings.tool_sandbox_enabled else "operator_required",
                "blocking": False,
                "enabled": settings.tool_sandbox_enabled,
                "image": settings.tool_sandbox_image,
                "runtime_hot_loading": False,
            },
        }
        blockers = [
            {"area": key, "reason": value.get("detail") or value.get("status")}
            for key, value in sections.items()
            if value.get("blocking")
        ]
        return {
            "status": "ready" if not blockers else "degraded",
            "blocking": bool(blockers),
            "enabled": settings.company_autonomy_enabled,
            "sections": sections,
            "blockers": blockers,
        }

    async def _model_availability(self) -> dict[str, Any]:
        if not self._llm:
            return {
                "status": "unavailable",
                "blocking": True,
                "detail": "LLM gateway is unavailable.",
            }
        result = await self._llm.validate_provider()
        return {
            **result,
            "status": "ready" if result.get("mode") == "live" else result.get("mode"),
            "blocking": result.get("mode") != "live",
        }

    @staticmethod
    async def _counts(session, column) -> dict[str, int]:
        rows = (await session.execute(select(column, func.count()).group_by(column))).all()
        return {str(key): int(value) for key, value in rows}

    @staticmethod
    async def _latest(session, model, order_column):
        return (
            await session.execute(
                select(model).order_by(desc(order_column)).limit(1)
            )
        ).scalar_one_or_none()

    @staticmethod
    def _timestamp(item) -> str | None:
        return item.created_at.isoformat() if item else None

    @staticmethod
    def _source_freshness(sources) -> dict[str, Any]:
        now = utc_now()
        max_age = timedelta(days=7)
        items = []
        stale_required = []
        for source in sources:
            stale = not source.last_success_at or now - source.last_success_at > max_age
            required = source.source_key in {"erpnext", "owner_instructions", "repository"}
            item = {
                "source_key": source.source_key,
                "source_type": source.source_type,
                "required": required,
                "stale": stale,
                "last_success_at": (
                    source.last_success_at.isoformat() if source.last_success_at else None
                ),
                "last_error": source.last_error,
            }
            items.append(item)
            if required and stale:
                stale_required.append(source.source_key)
        return {
            "status": "ready" if not stale_required else "stale",
            "blocking": bool(stale_required),
            "stale_required": stale_required,
            "items": items,
            "detail": (
                "All required evidence sources are fresh."
                if not stale_required
                else "Required evidence acquisition has not completed recently."
            ),
        }
