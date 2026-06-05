"""Autonomous operations routes."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from cyber_team.api.authorization import require_authorization
from cyber_team.api.security import Principal, get_current_principal
from cyber_team.config import settings

router = APIRouter()


class AutonomousCycleRequest(BaseModel):
    run_memory_steward: bool | None = None
    run_supervisor_review: bool | None = None
    run_planner: bool | None = None
    apply_safe_memory_actions: bool | None = None
    request_memory_action_approvals: bool | None = None
    memory_remediation_limit: int = Field(default=100, ge=1, le=200)
    auto_execute_plans: bool | None = None
    continue_on_error: bool = True


class AutonomousCycleResponse(BaseModel):
    cycle_id: str
    started_at: str
    completed_at: str | None
    actor: str
    status: str
    memory_steward: dict[str, Any] | None = None
    supervisor_review: dict[str, Any] | None = None
    planner: dict[str, Any] | None = None
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


class PlanScanRequest(BaseModel):
    include_role_gaps: bool = True
    include_memory_findings: bool = True
    auto_execute: bool = True
    limit: int = Field(default=50, ge=1, le=200)


class RetentionCleanupRequest(BaseModel):
    dry_run: bool = True


class SubjectDeleteRequest(BaseModel):
    dry_run: bool = True
    audit_preserving: bool = True


@router.post("/autonomous-cycle", response_model=AutonomousCycleResponse)
async def run_autonomous_cycle(
    data: AutonomousCycleRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "run",
        "autonomous_operations",
        "cycle",
        context=data.model_dump(),
    )
    service = request.app.state.autonomous_operations_service
    return await service.run_once(
        actor=principal.email,
        run_memory_steward=data.run_memory_steward,
        run_supervisor_review=data.run_supervisor_review,
        run_planner=data.run_planner,
        apply_safe_memory_actions=data.apply_safe_memory_actions,
        request_memory_action_approvals=data.request_memory_action_approvals,
        memory_remediation_limit=data.memory_remediation_limit,
        auto_execute_plans=data.auto_execute_plans,
        continue_on_error=data.continue_on_error,
    )


@router.get("/plans")
async def list_autonomous_plans(
    request: Request,
    status: str | None = None,
    source_type: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "autonomous_plan",
        context={"status": status, "source_type": source_type, "limit": limit},
    )
    planner = request.app.state.autonomous_planning_service
    return await planner.list_plans(status=status, source_type=source_type, limit=limit)


@router.post("/plans/scan")
async def scan_autonomous_plans(
    data: PlanScanRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "scan",
        "autonomous_plan",
        context=data.model_dump(),
    )
    planner = request.app.state.autonomous_planning_service
    payload = data.model_dump()
    if settings.autonomy_side_effect_mode == "manual_only":
        payload["auto_execute"] = False
    return await planner.scan_and_plan(actor=principal.email, **payload)


@router.get("/plans/{plan_id}")
async def get_autonomous_plan(
    plan_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "autonomous_plan", plan_id)
    planner = request.app.state.autonomous_planning_service
    plan = await planner.get_plan(plan_id)
    if not plan:
        raise HTTPException(404, "Autonomous plan not found")
    return plan


@router.get("/readiness")
async def operations_readiness(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "operations_readiness",
        "production",
    )
    registry = request.app.state.tool_registry
    tools = registry.list_tool_contracts()
    tool_counts: dict[str, int] = {}
    side_effect_blockers = []
    for tool in tools:
        state = tool.get("state") or "unknown"
        tool_counts[state] = tool_counts.get(state, 0) + 1
        if tool.get("side_effects") and state != "live":
            side_effect_blockers.append({
                "tool_name": tool["name"],
                "state": state,
                "reason": tool.get("readiness_reason"),
            })

    comms = request.app.state.comms_gateway.integration_status()
    integration_blockers = []
    if settings.require_live_tool_executors:
        for item in comms:
            if item.get("mode") != "live":
                integration_blockers.append({
                    "channel": item.get("channel"),
                    "provider": item.get("provider"),
                    "mode": item.get("mode"),
                    "reason": item.get("detail"),
                })

    evidence = await request.app.state.audit_service.list_events(
        event_type="control.evidence",
        limit=50,
    )
    traces = await request.app.state.memory_service.list_memory_traces(limit=50)
    trace_errors = [
        trace for trace in traces
        if trace.get("errors") or trace.get("metadata", {}).get("coverage") == "error"
    ]
    blockers = side_effect_blockers + integration_blockers
    status = "ready" if not blockers else "degraded"
    return {
        "status": status,
        "environment": settings.environment,
        "version": {
            "app_version": settings.app_version,
            "build_sha": settings.build_sha,
        },
        "autonomy": {
            "side_effect_mode": settings.autonomy_side_effect_mode,
            "manual_only": settings.autonomy_side_effect_mode == "manual_only",
            "planner_auto_execute_safe_tasks": (
                settings.autonomous_planner_auto_execute_safe_tasks
                and settings.autonomy_side_effect_mode != "manual_only"
            ),
        },
        "tools": {
            "total": len(tools),
            "counts_by_state": tool_counts,
            "side_effect_blockers": side_effect_blockers,
        },
        "integrations": {
            "communications": comms,
            "blocking_readiness": bool(integration_blockers),
            "blocking_reasons": integration_blockers,
        },
        "memory": {
            "recent_traces_reviewed": len(traces),
            "recent_trace_errors": len(trace_errors),
        },
        "controls": {
            "recent_evidence_count": len(evidence),
            "recent_evidence": evidence,
        },
        "requirements": {
            "require_live_tool_executors": settings.require_live_tool_executors,
            "communications_simulation_allowed": settings.communications_allow_simulation,
        },
        "blockers": blockers,
    }


@router.get("/decision-timeline")
async def decision_timeline(
    request: Request,
    limit: int = 50,
    principal: Principal = Depends(get_current_principal),
):
    safe_limit = max(1, min(limit, 200))
    await require_authorization(
        request,
        principal,
        "read",
        "decision_timeline",
        "owner_console",
        context={"limit": safe_limit},
    )
    traces = await request.app.state.memory_service.list_memory_traces(limit=safe_limit)
    events = await request.app.state.audit_service.list_events(limit=safe_limit)
    items = [
        {
            "id": trace["id"],
            "kind": "memory_trace",
            "created_at": trace["created_at"],
            "title": trace.get("task_excerpt") or trace.get("source_type"),
            "source_type": trace.get("source_type"),
            "agent_id": trace.get("agent_id"),
            "conversation_id": trace.get("conversation_id"),
            "workflow_run_id": trace.get("metadata", {}).get("workflow_run_id"),
            "tool_name": trace.get("metadata", {}).get("tool_name"),
            "coverage": trace.get("metadata", {}).get("coverage")
            or trace.get("metadata", {}).get("memory_coverage"),
            "status": "error" if trace.get("errors") else "recorded",
            "metadata": trace.get("metadata", {}),
        }
        for trace in traces
    ]
    items.extend(
        {
            "id": event["id"],
            "kind": "audit_event",
            "created_at": event["created_at"],
            "title": event.get("event_type"),
            "source_type": event.get("resource_type"),
            "agent_id": event.get("metadata", {}).get("agent_id"),
            "conversation_id": event.get("metadata", {}).get("conversation_id"),
            "workflow_run_id": (
                event.get("resource_id")
                if event.get("resource_type") == "workflow_run"
                else event.get("metadata", {}).get("workflow_run_id")
            ),
            "tool_name": (
                event.get("resource_id")
                if event.get("resource_type") == "tool"
                else event.get("metadata", {}).get("tool_name")
            ),
            "coverage": None,
            "status": event.get("outcome"),
            "metadata": event.get("metadata", {}),
        }
        for event in events
    )
    return sorted(items, key=lambda item: item["created_at"], reverse=True)[:safe_limit]


@router.post("/retention/cleanup")
async def run_retention_cleanup(
    data: RetentionCleanupRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "execute",
        "retention_policy",
        "cleanup",
        context=data.model_dump(),
    )
    result = await request.app.state.retention_service.cleanup(dry_run=data.dry_run)
    await request.app.state.audit_service.record_control_evidence(
        control_id="retention.cleanup",
        control_area="gdpr_retention",
        actor=principal.email,
        outcome="success",
        evidence=result,
    )
    return result


@router.get("/gdpr/subjects/{subject}/export")
async def export_subject_data(
    subject: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "export",
        "data_subject",
        subject,
    )
    result = await request.app.state.retention_service.export_subject_data(subject)
    await request.app.state.audit_service.record_control_evidence(
        control_id="gdpr.subject_export",
        control_area="gdpr_dsr",
        actor=principal.email,
        outcome="success",
        evidence={
            "subject": subject,
            "counts": {
                key: len(value)
                for key, value in result.items()
                if isinstance(value, list)
            },
        },
    )
    return result


@router.post("/gdpr/subjects/{subject}/delete")
async def delete_subject_data(
    subject: str,
    data: SubjectDeleteRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    if not data.audit_preserving:
        raise HTTPException(
            status_code=400,
            detail=(
                "Owner-console subject deletion is audit-preserving; audit events "
                "cannot be deleted through this API."
            ),
        )
    await require_authorization(
        request,
        principal,
        "delete",
        "data_subject",
        subject,
        context=data.model_dump(),
    )
    result = await request.app.state.retention_service.delete_subject_data(
        subject,
        dry_run=data.dry_run,
        include_audit=False,
    )
    await request.app.state.audit_service.record_control_evidence(
        control_id="gdpr.subject_delete",
        control_area="gdpr_dsr",
        actor=principal.email,
        outcome="success",
        evidence=result,
    )
    return result


@router.post("/plans/{plan_id}/execute")
async def execute_autonomous_plan(
    plan_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "execute", "autonomous_plan", plan_id)
    planner = request.app.state.autonomous_planning_service
    return await planner.execute_plan(plan_id, actor=principal.email)
