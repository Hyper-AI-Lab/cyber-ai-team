"""Supervisor review loop for adaptive role-gap operations."""

import logging
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import ApprovalRequest, RoleGap, WorkflowRun

logger = logging.getLogger(__name__)


class SupervisorReviewService:
    """Periodically reviews gaps, stale approvals, and repeated workflow failures."""

    def __init__(
        self,
        agent_manager,
        audit_service=None,
        session_factory=async_session,
    ):
        self._agent_manager = agent_manager
        self._audit = audit_service
        self._session_factory = session_factory

    async def run_once(
        self,
        *,
        now: datetime | None = None,
        actor: str = "supervisor_review_loop",
    ) -> dict:
        now = now or utc_now()
        summary = {
            "reviewed_at": now.isoformat(),
            "actor": actor,
            "role_gaps_reviewed": 0,
            "role_gaps_proposed": [],
            "role_gap_recommendations": [],
            "stale_approvals": [],
            "workflow_failure_gaps": [],
        }

        role_gap_summary = await self._review_role_gaps(now)
        summary.update(role_gap_summary)
        summary["stale_approvals"] = await self._review_stale_approvals(now)
        summary["workflow_failure_gaps"] = await self._review_workflow_failures(now)

        if self._audit:
            await self._audit.record(
                event_type="supervisor.review",
                actor=actor,
                actor_type="agent",
                resource_type="supervisor_review",
                action="run",
                metadata={
                    "role_gaps_reviewed": summary["role_gaps_reviewed"],
                    "role_gaps_proposed": summary["role_gaps_proposed"],
                    "stale_approval_count": len(summary["stale_approvals"]),
                    "workflow_failure_gap_count": len(summary["workflow_failure_gaps"]),
                },
            )
        return summary

    async def _review_role_gaps(self, now: datetime) -> dict:
        role_gaps = await self._load_active_role_gaps()
        proposed = []
        recommendations = []

        for gap in role_gaps:
            recommendation = self._recommendation_for_gap(gap)
            if gap["status"] == "open" and not gap["proposed_role"]:
                try:
                    proposal = await self._agent_manager.propose_role_for_gap(gap["id"])
                    proposed.append(gap["id"])
                    recommendation = {
                        "gap_id": gap["id"],
                        "recommendation": "role_proposed",
                        "priority": self._priority_for_gap(gap),
                        "reason": (
                            "Supervisor review generated a role proposal for unresolved "
                            "blocked work."
                        ),
                        "proposed_role": (
                            proposal.get("proposed_role", {})
                            .get("manifest_payload", {})
                            .get("name")
                        ),
                    }
                except Exception as exc:
                    logger.warning("Failed to propose role for gap %s: %s", gap["id"], exc)
                    recommendation = {
                        "gap_id": gap["id"],
                        "recommendation": "manual_review",
                        "priority": "high",
                        "reason": f"Role proposal failed: {exc}",
                    }

            recommendations.append(recommendation)
            await self._annotate_role_gap(gap["id"], recommendation, now)

        return {
            "role_gaps_reviewed": len(role_gaps),
            "role_gaps_proposed": proposed,
            "role_gap_recommendations": recommendations,
        }

    async def _review_stale_approvals(self, now: datetime) -> list[dict]:
        cutoff = now - timedelta(hours=settings.supervisor_review_stale_approval_hours)
        stale = []
        async with self._session_factory() as session:
            result = await session.execute(
                select(ApprovalRequest)
                .where(
                    ApprovalRequest.status == "pending",
                    ApprovalRequest.created_at <= cutoff,
                )
                .order_by(ApprovalRequest.created_at.asc())
            )
            approvals = result.scalars().all()

        for approval in approvals:
            item = {
                "approval_id": approval.id,
                "action_type": approval.action_type,
                "target_type": approval.target_type,
                "target_id": approval.target_id,
                "created_at": approval.created_at.isoformat(),
                "recommendation": "review_stale_approval",
                "priority": "high" if approval.risk_level == "high" else "medium",
            }
            stale.append(item)
            if approval.target_type == "role_gap" and approval.target_id:
                await self._annotate_role_gap(
                    approval.target_id,
                    {
                        "gap_id": approval.target_id,
                        "recommendation": "review_stale_approval",
                        "priority": item["priority"],
                        "reason": (
                            f"Approval {approval.id} for {approval.action_type} has been "
                            "pending longer than the configured stale approval window."
                        ),
                        "approval_id": approval.id,
                    },
                    now,
                )
        return stale

    async def _review_workflow_failures(self, now: datetime) -> list[dict]:
        cutoff = now - timedelta(hours=settings.supervisor_review_failure_lookback_hours)
        threshold = max(1, settings.supervisor_review_failure_threshold)
        async with self._session_factory() as session:
            result = await session.execute(
                select(WorkflowRun).where(
                    WorkflowRun.status == "failed",
                    WorkflowRun.completed_at.is_not(None),
                    WorkflowRun.completed_at >= cutoff,
                )
            )
            runs = result.scalars().all()

        groups: dict[tuple[str, str, str], list[WorkflowRun]] = defaultdict(list)
        for run in runs:
            groups[
                (
                    run.workflow_id,
                    run.current_node or "unknown_node",
                    self._error_signature(run.error),
                )
            ].append(run)

        created = []
        for (workflow_id, current_node, error_signature), grouped_runs in groups.items():
            if len(grouped_runs) < threshold:
                continue
            run_ids = [run.id for run in grouped_runs]
            gap = await self._agent_manager.report_role_gap(
                self._object_from_dict(
                    {
                        "title": f"Repeated workflow failure: {workflow_id}",
                        "description": (
                            f"Workflow {workflow_id} failed {len(grouped_runs)} times at "
                            f"node {current_node}. Error signature: {error_signature}."
                        ),
                        "severity": "high" if len(grouped_runs) >= threshold * 2 else "medium",
                        "source_agent_id": "supervisor",
                        "source_type": "agent",
                        "company_namespace": "company:default",
                        "capability": "workflow_reliability",
                        "requested_tools": ["process_audit"],
                        "context": {
                            "trigger": "supervisor_review",
                            "reason": "repeated_workflow_failure",
                            "workflow_id": workflow_id,
                            "current_node": current_node,
                            "error_signature": error_signature,
                            "failure_count": len(grouped_runs),
                            "run_ids": run_ids,
                            "dedupe_key": (
                                "workflow-failure:"
                                f"{workflow_id}:{current_node}:{error_signature}"
                            ),
                        },
                    }
                ),
                reporter="supervisor_review_loop",
            )
            if gap.get("status") == "open":
                gap = await self._agent_manager.propose_role_for_gap(gap["id"])
            created.append(
                {
                    "gap_id": gap["id"],
                    "workflow_id": workflow_id,
                    "current_node": current_node,
                    "failure_count": len(grouped_runs),
                    "status": gap["status"],
                }
            )
        return created

    async def _load_active_role_gaps(self) -> list[dict]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(RoleGap)
                .where(RoleGap.status.in_(["open", "proposed"]))
                .order_by(RoleGap.created_at.asc())
            )
            return [self._role_gap_to_dict(gap) for gap in result.scalars().all()]

    async def _annotate_role_gap(
        self,
        gap_id: str,
        recommendation: dict,
        now: datetime,
    ) -> None:
        async with self._session_factory() as session:
            result = await session.execute(select(RoleGap).where(RoleGap.id == gap_id))
            gap = result.scalar_one_or_none()
            if not gap:
                return
            context = dict(gap.context or {})
            entry = {
                **recommendation,
                "reviewed_at": now.isoformat(),
                "reviewer": "supervisor",
            }
            history = list(context.get("supervisor_review_history") or [])[-4:]
            context["supervisor_review"] = entry
            context["supervisor_review_history"] = [*history, entry]
            gap.context = context
            gap.updated_at = now
            await session.commit()

    @staticmethod
    def _recommendation_for_gap(gap: dict) -> dict:
        if gap["status"] == "open":
            return {
                "gap_id": gap["id"],
                "recommendation": "propose_role",
                "priority": SupervisorReviewService._priority_for_gap(gap),
                "reason": "Open role gap has no generated role proposal yet.",
            }
        if (gap.get("resolution") or {}).get("approval_required"):
            return {
                "gap_id": gap["id"],
                "recommendation": "review_pending_approval",
                "priority": "high",
                "reason": "Generated role is waiting for high-risk tool grant approval.",
                "approval_id": (gap.get("resolution") or {}).get("pending_approval_id"),
            }
        return {
            "gap_id": gap["id"],
            "recommendation": "apply_or_dismiss",
            "priority": SupervisorReviewService._priority_for_gap(gap),
            "reason": "Generated role proposal is ready for owner decision.",
        }

    @staticmethod
    def _priority_for_gap(gap: dict) -> str:
        if gap.get("severity") in {"critical", "high"}:
            return "high"
        if gap.get("severity") == "low":
            return "low"
        return "medium"

    @staticmethod
    def _error_signature(error: str | None) -> str:
        text = " ".join(str(error or "unknown_error").split())
        if len(text) > 120:
            text = text[:120].rstrip()
        return text or "unknown_error"

    @staticmethod
    def _object_from_dict(values: dict):
        return type("SupervisorReviewObject", (), values)()

    @staticmethod
    def _role_gap_to_dict(gap: RoleGap) -> dict:
        return {
            "id": gap.id,
            "title": gap.title,
            "description": gap.description,
            "status": gap.status,
            "severity": gap.severity,
            "source_agent_id": gap.source_agent_id,
            "source_type": gap.source_type,
            "company_namespace": gap.company_namespace,
            "capability": gap.capability,
            "requested_tools": gap.requested_tools or [],
            "context": gap.context or {},
            "proposed_role": gap.proposed_role or {},
            "resolution": gap.resolution or {},
            "created_at": gap.created_at.isoformat(),
            "updated_at": gap.updated_at.isoformat(),
            "resolved_at": gap.resolved_at.isoformat() if gap.resolved_at else None,
        }
