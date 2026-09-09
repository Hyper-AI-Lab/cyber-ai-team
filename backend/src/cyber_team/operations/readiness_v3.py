"""Readiness aggregation for the evidence-to-outcome company control plane."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import desc, exists, func, select

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import (
    ActionClassPolicy,
    Agent,
    AgentMandate,
    AutonomousActionCandidate,
    BusinessEvent,
    BusinessEventDelivery,
    BusinessWorkItem,
    CompanyModelRevision,
    CompanyObjectiveRevision,
    CompanySignal,
    CompanySource,
    DiscoveryObligation,
    DomainAutonomyControl,
    LifecycleAssessment,
    OperatingDomain,
    OperatingDomainRevision,
    OperatingKPIRevision,
    OperatingModelReconciliationRun,
    OperatingModelRevision,
    OutcomeAssessment,
    WorkflowSpecification,
)


class AutonomousCompanyReadinessService:
    """Explain autonomy readiness without hiding unknown or transitional state."""

    REQUIRED_SIGNAL_TRUST_CLASSES = {
        "owner_locked",
        "canonical",
        "authenticated",
        "internal",
    }
    CRITICAL_MODEL_FIELDS = {
        "business_description",
        "offerings",
        "customer_segments",
        "jurisdictions",
    }

    def __init__(self, *, llm_gateway=None, model_capability_service=None):
        self._llm = llm_gateway
        self._model_capabilities = model_capability_service

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
            extraction_counts = await self._counts(
                session,
                CompanySignal.claim_extraction_status,
            )
            signal_counts = await self._counts(session, CompanySignal.status)
            signal_disposition_counts = {
                str(key): int(value)
                for key, value in (
                    await session.execute(
                        select(CompanySignal.disposition, func.count())
                        .where(CompanySignal.disposition.is_not(None))
                        .group_by(CompanySignal.disposition)
                    )
                ).all()
            }
            extraction_stale_before = utc_now() - timedelta(
                seconds=max(
                    1,
                    settings.business_event_readiness_stale_after_seconds,
                )
            )
            required_stale_extraction_failures = int(
                (
                    await session.execute(
                        select(func.count(CompanySignal.id)).where(
                            CompanySignal.status == "pending",
                            CompanySignal.claim_extraction_status == "failed",
                            CompanySignal.trust_class.in_(
                                self.REQUIRED_SIGNAL_TRUST_CLASSES
                            ),
                            CompanySignal.received_at <= extraction_stale_before,
                        )
                    )
                ).scalar_one()
            )
            advisory_stale_extraction_failures = int(
                (
                    await session.execute(
                        select(func.count(CompanySignal.id)).where(
                            CompanySignal.status == "pending",
                            CompanySignal.claim_extraction_status == "failed",
                            CompanySignal.trust_class.notin_(
                                self.REQUIRED_SIGNAL_TRUST_CLASSES
                            ),
                            CompanySignal.received_at <= extraction_stale_before,
                        )
                    )
                ).scalar_one()
            )
            expired_extraction_leases = int(
                (
                    await session.execute(
                        select(func.count(CompanySignal.id)).where(
                            CompanySignal.status == "pending",
                            CompanySignal.claim_extraction_status == "processing",
                            CompanySignal.claim_extraction_lease_expires_at <= utc_now(),
                        )
                    )
                ).scalar_one()
            )
            scheduled_extraction_retries = int(
                (
                    await session.execute(
                        select(func.count(CompanySignal.id)).where(
                            CompanySignal.status == "pending",
                            CompanySignal.claim_extraction_status == "failed",
                            CompanySignal.claim_extraction_available_at > utc_now(),
                        )
                    )
                ).scalar_one()
            )
            stale_pending_signals = int(
                (
                    await session.execute(
                        select(func.count(CompanySignal.id)).where(
                            CompanySignal.status == "pending",
                            CompanySignal.received_at <= extraction_stale_before,
                        )
                    )
                ).scalar_one()
            )
            required_stale_pending_signals = int(
                (
                    await session.execute(
                        select(func.count(CompanySignal.id)).where(
                            CompanySignal.status == "pending",
                            CompanySignal.trust_class.in_(
                                self.REQUIRED_SIGNAL_TRUST_CLASSES
                            ),
                            CompanySignal.received_at <= extraction_stale_before,
                        )
                    )
                ).scalar_one()
            )
            advisory_stale_pending_signals = max(
                0,
                stale_pending_signals - required_stale_pending_signals,
            )
            undispositioned_processed_signals = int(
                (
                    await session.execute(
                        select(func.count(CompanySignal.id)).where(
                            CompanySignal.status.in_({"processed", "quarantined"}),
                            CompanySignal.disposition.is_(None),
                        )
                    )
                ).scalar_one()
            )
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
            pending_events = event_counts.get("pending", 0)
            processing_window_seconds = max(
                1,
                settings.business_event_readiness_stale_after_seconds,
            )
            stale_before = utc_now() - timedelta(seconds=processing_window_seconds)
            stale_pending_events = int(
                (
                    await session.execute(
                        select(func.count(BusinessEvent.id)).where(
                            BusinessEvent.status == "pending",
                            BusinessEvent.created_at <= stale_before,
                        )
                    )
                ).scalar_one()
            )
            delivery_counts = await self._counts(session, BusinessEventDelivery.status)
            work_counts = await self._counts(session, BusinessWorkItem.status)
            domain_work_rows = (
                await session.execute(
                    select(Agent.role_family, func.count(BusinessWorkItem.id))
                    .join(
                        BusinessWorkItem,
                        BusinessWorkItem.assigned_agent_id == Agent.id,
                    )
                    .where(
                        BusinessWorkItem.status.in_(
                            {
                                "proposed",
                                "ready",
                                "leased",
                                "blocked_dependency",
                                "unassigned",
                                "waiting_approval",
                            }
                        )
                    )
                    .group_by(Agent.role_family)
                )
            ).all()
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
            action_candidate_counts = await self._counts(
                session,
                AutonomousActionCandidate.status,
            )
            stale_action_candidates = int(
                (
                    await session.execute(
                        select(func.count(AutonomousActionCandidate.id)).where(
                            AutonomousActionCandidate.status == "proposed",
                            AutonomousActionCandidate.created_at <= stale_before,
                        )
                    )
                ).scalar_one()
            )
            terminal_work = int(
                (
                    await session.execute(
                        select(func.count(BusinessWorkItem.id)).where(
                            BusinessWorkItem.status.in_(
                                {"completed", "failed", "blocked", "cancelled"}
                            )
                        )
                    )
                ).scalar_one()
            )
            unassessed_filter = (
                BusinessWorkItem.status.in_(
                    {"completed", "failed", "blocked", "cancelled"}
                ),
                ~exists(
                    select(OutcomeAssessment.id).where(
                        OutcomeAssessment.work_item_id == BusinessWorkItem.id
                    )
                ),
            )
            unassessed_work = int(
                (
                    await session.execute(
                        select(func.count(BusinessWorkItem.id)).where(*unassessed_filter)
                    )
                ).scalar_one()
            )
            stale_outcome_before = utc_now() - timedelta(
                seconds=max(1, settings.outcome_readiness_stale_after_seconds)
            )
            stale_unassessed_work = int(
                (
                    await session.execute(
                        select(func.count(BusinessWorkItem.id)).where(
                            *unassessed_filter,
                            BusinessWorkItem.updated_at <= stale_outcome_before,
                        )
                    )
                ).scalar_one()
            )
            oldest_unassessed_at = (
                await session.execute(
                    select(func.min(BusinessWorkItem.updated_at)).where(
                        *unassessed_filter
                    )
                )
            ).scalar_one_or_none()
            workflow_counts = await self._counts(session, WorkflowSpecification.status)
            policy_counts = await self._counts(session, ActionClassPolicy.status)
            policies = (
                await session.execute(
                    select(ActionClassPolicy).where(
                        ActionClassPolicy.status != "superseded"
                    )
                )
            ).scalars().all()
            operating_model = (
                await session.execute(
                    select(OperatingModelRevision)
                    .where(OperatingModelRevision.status == "active")
                    .order_by(desc(OperatingModelRevision.revision))
                    .limit(1)
                )
            ).scalar_one_or_none()
            operating_model_candidate = (
                await session.execute(
                    select(OperatingModelRevision)
                    .order_by(desc(OperatingModelRevision.revision))
                    .limit(1)
                )
            ).scalar_one_or_none()
            operating_domains = (
                await session.execute(select(OperatingDomain))
            ).scalars().all()
            operating_domain_revisions = (
                await session.execute(select(OperatingDomainRevision))
            ).scalars().all()
            latest_reconciliation = await self._latest(
                session,
                OperatingModelReconciliationRun,
                OperatingModelReconciliationRun.created_at,
            )
            discovery_obligations = (
                await session.execute(select(DiscoveryObligation))
            ).scalars().all()
            lifecycle_assessment_counts = await self._counts(
                session,
                LifecycleAssessment.lifecycle_status,
            )
            active_agent_rows = (
                await session.execute(
                    select(Agent.id, Agent.role_family).where(Agent.status == "active")
                )
            ).all()
            active_mandates = (
                await session.execute(
                    select(AgentMandate).where(AgentMandate.status == "active")
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
        signal_plane_blocking = bool(
            required_stale_pending_signals or undispositioned_processed_signals
        )
        signal_plane = {
            "status": (
                "undispositioned"
                if undispositioned_processed_signals
                else "stale_pending"
                if required_stale_pending_signals
                else "advisory_degraded"
                if advisory_stale_pending_signals
                else "processing"
                if signal_counts.get("pending", 0)
                else "ready"
            ),
            "blocking": signal_plane_blocking,
            "counts": signal_counts,
            "disposition_counts": signal_disposition_counts,
            "stale_pending": stale_pending_signals,
            "required_stale_pending": required_stale_pending_signals,
            "advisory_stale_pending": advisory_stale_pending_signals,
            "undispositioned_processed": undispositioned_processed_signals,
            "processing_window_seconds": max(
                1, settings.business_event_readiness_stale_after_seconds
            ),
            "detail": (
                "Processed company signals are missing a terminal disposition."
                if undispositioned_processed_signals
                else "Trusted company signals exceeded the evidence-processing window."
                if required_stale_pending_signals
                else "Low-trust evidence is retrying without blocking canonical operations."
                if advisory_stale_pending_signals
                else "Company signals are being processed within the allowed window."
                if signal_counts.get("pending", 0)
                else "Every company signal has a finite recorded disposition."
            ),
        }
        extraction_blocking = bool(
            required_stale_extraction_failures or expired_extraction_leases
        )
        claim_extraction = {
            "status": (
                "expired_lease"
                if expired_extraction_leases
                else "stale_failed"
                if required_stale_extraction_failures
                else "advisory_degraded"
                if advisory_stale_extraction_failures
                else "retrying"
                if extraction_counts.get("failed", 0)
                else "ready"
            ),
            "blocking": extraction_blocking,
            "counts": extraction_counts,
            "stale_failed": required_stale_extraction_failures,
            "required_stale_failed": required_stale_extraction_failures,
            "advisory_stale_failed": advisory_stale_extraction_failures,
            "expired_leases": expired_extraction_leases,
            "scheduled_retries": scheduled_extraction_retries,
            "detail": (
                "A claim-extraction lease expired before completion and must be reclaimed."
                if expired_extraction_leases
                else "Required-source claim extraction failures exceeded the processing window."
                if required_stale_extraction_failures
                else (
                    "Low-trust evidence extraction is degraded without blocking "
                    "canonical operations."
                )
                if advisory_stale_extraction_failures
                else "Claim extraction failures remain retryable within the processing window."
                if extraction_counts.get("failed", 0)
                else "Evidence claim extraction is healthy."
            ),
        }
        mandate_gap = max(0, active_agents - mandated_agents)
        mandates = {
            "status": "ready" if mandate_gap == 0 else "degraded",
            "blocking": mandate_gap > 0,
            "active_agents": active_agents,
            "mandated_agents": mandated_agents,
            "missing_mandates": mandate_gap,
        }
        in_processing_window = max(0, pending_events - stale_pending_events)
        events = {
            "status": (
                "stale_pending"
                if stale_pending_events
                else "processing"
                if pending_events
                else "ready"
            ),
            "blocking": stale_pending_events > 0,
            "counts": event_counts,
            "outbox_counts": delivery_counts,
            "pending": pending_events,
            "in_processing_window": in_processing_window,
            "stale_unexplained": stale_pending_events,
            "unexplained": stale_pending_events,
            "processing_window_seconds": processing_window_seconds,
            "detail": (
                "Pending events exceeded the allowed processing window."
                if stale_pending_events
                else "Pending events are within the normal processing window."
                if pending_events
                else "Every business event has a recorded disposition."
            ),
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
                    "shadow_validated_cases": int(
                        (item.metadata_ or {}).get("shadow_validated_cases") or 0
                    ),
                    "live_canary_cases": int(
                        (item.metadata_ or {}).get("live_canary_cases") or 0
                    ),
                    "required_live_canaries": settings.action_policy_min_live_canaries,
                    "evaluator_score": item.evaluator_score,
                    "minimum_evaluator_score": (
                        settings.action_policy_min_evaluator_score
                    ),
                    "hard_policy_compliance": item.hard_policy_compliance,
                    "high_severity_findings": item.high_severity_findings,
                    "permanent_gate": item.permanent_gate,
                }
                for item in policies
            ],
        }
        backlog_limit = max(
            1,
            min(int(settings.autonomy_domain_max_nonterminal_work_items), 1_000),
        )
        domain_backlogs: dict[str, int] = {}
        for role_family, count in domain_work_rows:
            domain = self._canonical_family(str(role_family))
            domain_backlogs[domain] = domain_backlogs.get(domain, 0) + int(count or 0)
        saturated_domains = sorted(
            domain
            for domain, count in domain_backlogs.items()
            if count >= backlog_limit
        )
        recovery_domains = sorted(
            item.domain
            for item in domain_controls
            if item.state == "paused"
            and item.owner == "autonomy_grounding_circuit_breaker"
        )
        portfolio_blocking = bool(saturated_domains or recovery_domains)
        outcome_learning = {
            "status": (
                "stale_backlog"
                if stale_unassessed_work
                else "processing"
                if unassessed_work
                else "ready"
            ),
            "blocking": stale_unassessed_work > 0,
            "terminal_work": terminal_work,
            "assessed_work": max(0, terminal_work - unassessed_work),
            "unassessed_work": unassessed_work,
            "stale_unassessed_work": stale_unassessed_work,
            "oldest_unassessed_at": (
                oldest_unassessed_at.isoformat() if oldest_unassessed_at else None
            ),
            "latest_assessment_at": self._timestamp(latest_outcome),
            "processing_window_seconds": max(
                1, settings.outcome_readiness_stale_after_seconds
            ),
            "detail": (
                "Terminal work has exceeded the outcome-assessment processing window."
                if stale_unassessed_work
                else "Terminal work is waiting within the outcome-assessment window."
                if unassessed_work
                else "Every terminal work item has a durable outcome assessment."
            ),
        }
        model_availability = await self._model_availability()
        latest_domain_revisions: dict[str, OperatingDomainRevision] = {}
        for revision in sorted(
            operating_domain_revisions,
            key=lambda item: item.revision,
            reverse=True,
        ):
            latest_domain_revisions.setdefault(revision.domain_id, revision)
        agent_ids_by_domain: dict[str, set[str]] = {}
        for agent_id, family in active_agent_rows:
            domain = self._canonical_family(str(family))
            agent_ids_by_domain.setdefault(domain, set()).add(agent_id)
        mandates_by_agent = {item.agent_id: item for item in active_mandates}
        effective_domains = [
            item for item in operating_domains if item.lifecycle_state in {"shadow", "active"}
        ]
        active_domains = [
            item for item in operating_domains if item.lifecycle_state == "active"
        ]
        missing_specifications = sorted(
            item.domain_key
            for item in active_domains
            if item.id not in latest_domain_revisions
        )
        missing_domain_agents = sorted(
            item.domain_key
            for item in active_domains
            if not agent_ids_by_domain.get(item.domain_key)
        )
        missing_domain_mandates = sorted(
            item.domain_key
            for item in active_domains
            if not any(
                agent_id in mandates_by_agent
                for agent_id in agent_ids_by_domain.get(item.domain_key, set())
            )
        )
        missing_domain_objectives = sorted(
            item.domain_key
            for item in active_domains
            if not any(
                mandates_by_agent[agent_id].objective_ids
                for agent_id in agent_ids_by_domain.get(item.domain_key, set())
                if agent_id in mandates_by_agent
            )
        )
        operating_model_invariants = sorted(
            set(missing_specifications)
            | set(missing_domain_agents)
            | set(missing_domain_mandates)
            | set(missing_domain_objectives)
        )
        operating_model_blocking = bool(
            settings.company_autonomy_enabled
            and model
            and (not operating_model or operating_model_invariants)
        )
        operating_model_section = {
            "status": (
                "invariant_failed"
                if operating_model_invariants
                else "reconciling"
                if operating_model and any(
                    item.lifecycle_state in {"proposed", "shadow", "retiring"}
                    for item in operating_domains
                )
                else "ready"
                if operating_model
                else "observer_review"
                if operating_model_candidate
                and operating_model_candidate.status == "owner_review"
                else "not_synthesized"
            ),
            "blocking": operating_model_blocking,
            "revision_id": operating_model.id if operating_model else None,
            "candidate_revision_id": (
                operating_model_candidate.id if operating_model_candidate else None
            ),
            "candidate_status": (
                operating_model_candidate.status if operating_model_candidate else None
            ),
            "confidence": operating_model.confidence if operating_model else 0.0,
            "desired_domains": list(operating_model.domain_keys or [])
            if operating_model
            else [],
            "effective_domain_count": len(effective_domains),
            "lifecycle_counts": self._value_counts(
                item.lifecycle_state for item in operating_domains
            ),
            "missing_specifications": missing_specifications,
            "missing_agents": missing_domain_agents,
            "missing_mandates": missing_domain_mandates,
            "missing_objectives": missing_domain_objectives,
            "latest_reconciliation": (
                {
                    "id": latest_reconciliation.id,
                    "status": latest_reconciliation.status,
                    "dry_run": latest_reconciliation.dry_run,
                    "completed_at": (
                        latest_reconciliation.completed_at.isoformat()
                        if latest_reconciliation.completed_at
                        else None
                    ),
                }
                if latest_reconciliation
                else None
            ),
            "lifecycle_assessments": lifecycle_assessment_counts,
            "detail": (
                "Active domains violate one or more operating-model invariants."
                if operating_model_invariants
                else "Desired and actual domains are converging through bounded lifecycle states."
                if operating_model
                else "No Observer-approved desired operating model is active."
            ),
        }
        obligation_counts = self._value_counts(
            item.status for item in discovery_obligations
        )
        active_obligations = [
            item
            for item in discovery_obligations
            if item.status not in {"resolved", "superseded"}
        ]
        owner_blockers = [
            item.id
            for item in active_obligations
            if item.status == "owner_review" and item.blocking
        ]
        model_unknowns = {
            self._canonical_unknown(item) for item in (model.unknowns or [])
        } if model else set()
        # A terminal obligation is still a durable disposition. Resolved and
        # superseded records explain why discovery stopped; excluding them makes
        # an immutable older model look undispositioned after newer evidence has
        # already closed the unknown.
        disposed_unknowns = {item.predicate for item in discovery_obligations}
        undispositioned_unknowns = sorted(model_unknowns - disposed_unknowns)
        discovery_blocking = bool(owner_blockers or undispositioned_unknowns)
        discovery_section = {
            "status": (
                "owner_review"
                if owner_blockers
                else "undispositioned"
                if undispositioned_unknowns
                else "discovering"
                if active_obligations
                else "ready"
            ),
            "blocking": discovery_blocking,
            "counts": obligation_counts,
            "active": len(active_obligations),
            "owner_review_blockers": owner_blockers,
            "undispositioned_unknowns": undispositioned_unknowns,
            "detail": (
                "Private or authoritative facts require owner evidence after source exhaustion."
                if owner_blockers
                else "Company-model unknowns lack durable discovery obligations."
                if undispositioned_unknowns
                else "Unknowns are being resolved through bounded permitted sources."
                if active_obligations
                else "Every company-model unknown has a terminal discovery disposition."
            ),
        }
        sections = {
            "company_model": company_model,
            "operating_model": operating_model_section,
            "discovery_obligations": discovery_section,
            "source_freshness": source_freshness,
            "company_signals": signal_plane,
            "claim_extraction": claim_extraction,
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
                "status": (
                    "recovery_required"
                    if recovery_domains
                    else "backlog_saturated"
                    if saturated_domains
                    else "bounded"
                ),
                "blocking": portfolio_blocking,
                "counts": work_counts,
                "domain_backlogs": domain_backlogs,
                "backlog_limit": backlog_limit,
                "saturated_domains": saturated_domains,
                "recovery_required_domains": recovery_domains,
                "latest_outcome_at": self._timestamp(latest_outcome),
                "detail": (
                    "Grounding circuit-breaker recovery is required for: "
                    + ", ".join(recovery_domains)
                    if recovery_domains
                    else "Generated work backlog reached its configured limit for: "
                    + ", ".join(saturated_domains)
                    if saturated_domains
                    else "Every domain work backlog is within its configured bound."
                ),
            },
            "outcome_learning": outcome_learning,
            "action_candidates": {
                "status": (
                    "stale_proposed"
                    if stale_action_candidates
                    else "active"
                    if action_candidate_counts
                    else "idle"
                ),
                "blocking": stale_action_candidates > 0,
                "counts": action_candidate_counts,
                "stale_proposed": stale_action_candidates,
                "approval_required": action_candidate_counts.get("approval_required", 0),
                "executed": action_candidate_counts.get("executed", 0),
                "blocked": action_candidate_counts.get("blocked", 0),
                "contract_version": "autonomous-action-candidate-v1",
                "detail": (
                    "One or more action candidates were not reviewed within the window."
                    if stale_action_candidates
                    else "Typed action candidates are durably reviewed and tracked."
                    if action_candidate_counts
                    else "No domain action candidate has been proposed yet."
                ),
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

    @staticmethod
    def _canonical_family(value: str) -> str:
        aliases = {
            "compliance": "security",
            "research": "knowledge",
            "project_management": "product",
            "people": "hr",
            "observer": "governance",
        }
        normalized = str(value or "operations").lower().replace(" ", "_")
        return aliases.get(normalized, normalized)

    @staticmethod
    def _canonical_unknown(value: Any) -> str:
        if isinstance(value, dict):
            value = value.get("predicate") or value.get("field") or ""
        return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")

    @staticmethod
    def _value_counts(values) -> dict[str, int]:
        counts: dict[str, int] = {}
        for value in values:
            key = str(value)
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    async def _model_availability(self) -> dict[str, Any]:
        if not self._llm:
            return {
                "status": "unavailable",
                "blocking": True,
                "detail": "LLM gateway is unavailable.",
            }
        result = await self._llm.validate_provider()
        infrastructure_blocking = bool(
            result.get("blocking", result.get("mode") != "live")
        )
        capabilities = (
            await self._model_capabilities.summary()
            if self._model_capabilities and not infrastructure_blocking
            else None
        )
        blocking = bool(
            infrastructure_blocking
            or (capabilities and capabilities.get("blocking"))
        )
        return {
            **result,
            "status": (
                "ready"
                if not blocking
                else "not_qualified"
                if capabilities and capabilities.get("blocking")
                else result.get("mode")
            ),
            "blocking": blocking,
            "infrastructure": {
                "status": "ready" if not infrastructure_blocking else result.get("mode"),
                "blocking": infrastructure_blocking,
            },
            "capabilities": capabilities,
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
