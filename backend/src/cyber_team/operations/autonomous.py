"""Coordinated autonomous operations cycle."""

from __future__ import annotations

import uuid
from typing import Any

from cyber_team.clock import utc_now
from cyber_team.config import settings


class AutonomousOperationsService:
    """Runs the system's adaptive operating loops as one inspectable cycle."""

    def __init__(
        self,
        *,
        supervisor_review_service,
        memory_steward_service,
        audit_service=None,
    ):
        self._supervisor = supervisor_review_service
        self._memory_steward = memory_steward_service
        self._audit = audit_service

    async def run_once(
        self,
        *,
        actor: str = "autonomous_operations_loop",
        run_memory_steward: bool | None = None,
        run_supervisor_review: bool | None = None,
        apply_safe_memory_actions: bool | None = None,
        request_memory_action_approvals: bool | None = None,
        memory_remediation_limit: int = 100,
        continue_on_error: bool = True,
    ) -> dict[str, Any]:
        cycle_id = f"auto_cycle_{uuid.uuid4().hex[:12]}"
        started_at = utc_now()
        run_memory = (
            settings.memory_steward_enabled
            if run_memory_steward is None
            else run_memory_steward
        )
        run_supervisor = (
            settings.supervisor_review_enabled
            if run_supervisor_review is None
            else run_supervisor_review
        )
        summary: dict[str, Any] = {
            "cycle_id": cycle_id,
            "started_at": started_at.isoformat(),
            "completed_at": None,
            "actor": actor,
            "status": "running",
            "memory_steward": None,
            "supervisor_review": None,
            "decisions": [],
            "errors": [],
            "counts": {
                "memory_findings_created": 0,
                "memory_findings_updated": 0,
                "memory_actions_applied": 0,
                "memory_approvals_requested": 0,
                "memory_plans_created": 0,
                "memory_blocks": 0,
                "role_gaps_reviewed": 0,
                "role_gaps_proposed": 0,
                "workflow_failure_gaps": 0,
                "stale_approvals": 0,
            },
        }

        if not run_memory and not run_supervisor:
            summary["status"] = "skipped"
            summary["completed_at"] = utc_now().isoformat()
            await self._record_cycle(summary)
            return summary

        try:
            if run_memory:
                await self._run_step(
                    summary,
                    step_name="memory_steward",
                    runner=lambda: self._memory_steward.run_once(
                        actor=f"{actor}:memory_steward",
                        apply_safe_actions=apply_safe_memory_actions,
                        request_approvals=request_memory_action_approvals,
                        remediation_limit=memory_remediation_limit,
                    ),
                    summarizer=self._summarize_memory_steward,
                    continue_on_error=continue_on_error,
                )

            if run_supervisor:
                await self._run_step(
                    summary,
                    step_name="supervisor_review",
                    runner=lambda: self._supervisor.run_once(
                        actor=f"{actor}:supervisor_review",
                    ),
                    summarizer=self._summarize_supervisor_review,
                    continue_on_error=continue_on_error,
                )
        except Exception:
            summary["completed_at"] = utc_now().isoformat()
            summary["status"] = "failed"
            await self._record_cycle(summary)
            raise

        summary["completed_at"] = utc_now().isoformat()
        summary["status"] = "degraded" if summary["errors"] else "completed"
        await self._record_cycle(summary)
        return summary

    async def _run_step(
        self,
        summary: dict[str, Any],
        *,
        step_name: str,
        runner,
        summarizer,
        continue_on_error: bool,
    ) -> None:
        try:
            result = await runner()
        except Exception as exc:
            error = {
                "step": step_name,
                "type": type(exc).__name__,
                "message": str(exc),
            }
            summary["errors"].append(error)
            if not continue_on_error:
                raise
            return
        summary[step_name] = result
        summarizer(summary, result)

    @staticmethod
    def _summarize_memory_steward(summary: dict[str, Any], result: dict[str, Any]) -> None:
        counts = summary["counts"]
        counts["memory_findings_created"] = int(result.get("findings_created") or 0)
        counts["memory_findings_updated"] = int(result.get("findings_updated") or 0)
        plan = result.get("remediation_plan") or {}
        counts["memory_actions_applied"] = int(plan.get("actions_applied") or 0)
        counts["memory_approvals_requested"] = int(plan.get("approvals_requested") or 0)
        counts["memory_plans_created"] = int(plan.get("plans_created") or 0)
        counts["memory_blocks"] = int(plan.get("blocked") or 0)
        if result.get("findings_created") or result.get("findings_updated"):
            summary["decisions"].append({
                "step": "memory_steward",
                "decision": "memory_findings_reviewed",
                "findings_created": counts["memory_findings_created"],
                "findings_updated": counts["memory_findings_updated"],
            })
        if plan:
            summary["decisions"].append({
                "step": "memory_steward",
                "decision": "memory_remediation_planned",
                "actions_applied": counts["memory_actions_applied"],
                "approvals_requested": counts["memory_approvals_requested"],
                "plans_created": counts["memory_plans_created"],
                "blocked": counts["memory_blocks"],
            })

    @staticmethod
    def _summarize_supervisor_review(summary: dict[str, Any], result: dict[str, Any]) -> None:
        counts = summary["counts"]
        role_gaps_proposed = result.get("role_gaps_proposed") or []
        workflow_failure_gaps = result.get("workflow_failure_gaps") or []
        stale_approvals = result.get("stale_approvals") or []
        counts["role_gaps_reviewed"] = int(result.get("role_gaps_reviewed") or 0)
        counts["role_gaps_proposed"] = len(role_gaps_proposed)
        counts["workflow_failure_gaps"] = len(workflow_failure_gaps)
        counts["stale_approvals"] = len(stale_approvals)
        if role_gaps_proposed:
            summary["decisions"].append({
                "step": "supervisor_review",
                "decision": "role_proposals_generated",
                "role_gap_ids": role_gaps_proposed,
            })
        if workflow_failure_gaps:
            summary["decisions"].append({
                "step": "supervisor_review",
                "decision": "workflow_failure_gaps_reported",
                "gap_count": len(workflow_failure_gaps),
            })
        if stale_approvals:
            summary["decisions"].append({
                "step": "supervisor_review",
                "decision": "stale_approvals_flagged",
                "approval_count": len(stale_approvals),
            })

    async def _record_cycle(self, summary: dict[str, Any]) -> None:
        if not self._audit:
            return
        await self._audit.record(
            event_type="autonomous_operations.cycle",
            actor=summary["actor"],
            actor_type="agent",
            resource_type="autonomous_operations",
            resource_id=summary["cycle_id"],
            action="run",
            outcome=summary["status"],
            metadata={
                "started_at": summary["started_at"],
                "completed_at": summary["completed_at"],
                "counts": summary["counts"],
                "decisions": summary["decisions"],
                "errors": summary["errors"],
            },
        )
