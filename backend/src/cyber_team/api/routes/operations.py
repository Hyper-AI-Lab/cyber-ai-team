"""Autonomous operations routes."""

import time
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from cyber_team.api.authorization import require_authorization
from cyber_team.api.security import Principal, get_current_principal
from cyber_team.config import settings
from cyber_team.operations.readiness import ProductionReadinessEvidenceService

router = APIRouter()
READINESS_CACHE_TTL_SECONDS = 5.0


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
    include_company_context: bool = True
    include_operating_cadence: bool = True
    auto_execute: bool = True
    limit: int = Field(default=50, ge=1, le=200)


class CompanyContextSyncRequest(BaseModel):
    dry_run: bool = False
    apply_low_risk: bool = True
    run_planner: bool = True
    source: str = Field(default="erpnext", pattern="^erpnext$")


class CompanyContextDriftScanRequest(BaseModel):
    dry_run: bool = False
    apply_low_risk: bool = True
    run_planner: bool = True


class OperatingCadenceScanRequest(BaseModel):
    company_namespace: str | None = None
    auto_execute: bool = True
    limit: int = Field(default=200, ge=1, le=500)


class OwnerAttentionNotifyRequest(BaseModel):
    dry_run: bool = False
    limit: int = Field(default=25, ge=1, le=200)


class AlertEmailTestRequest(BaseModel):
    dry_run: bool = False
    note: str = Field(default="", max_length=1000)


class GovernorRunRequest(BaseModel):
    dry_run: bool = False
    auto_apply_low_risk: bool | None = None
    max_actions: int | None = Field(default=None, ge=1, le=50)
    continue_on_error: bool = True
    mode: str = Field(default="standard", pattern="^(standard|executive)$")
    force_reflection: bool = False
    force_benchmark_refresh: bool = False
    owner_instruction: str = Field(default="", max_length=4000)
    observer_review: bool = True
    synthetic_large_impact: bool = False


class ToolProposalApprovalRequest(BaseModel):
    note: str = Field(default="", max_length=2000)


class CompanyObjectivesRequest(BaseModel):
    objectives: list[dict[str, Any]] = Field(default_factory=list, min_length=1)


class AutonomyPolicyRequest(BaseModel):
    mode: str | None = Field(default=None, max_length=60)
    resource_policy: str | None = Field(default=None, max_length=60)
    paused: bool | None = None
    thresholds: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)


class GovernorInstructionRequest(BaseModel):
    instruction: str = Field(..., min_length=1, max_length=4000)
    dry_run: bool = False
    observer_review: bool = True


class GovernorPauseRequest(BaseModel):
    reason: str = Field(default="", max_length=2000)


class ExecutiveBenchmarkRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=120)
    title: str = Field(..., min_length=1, max_length=240)
    description: str = Field(default="", max_length=4000)
    kpi_keys: list[str] = Field(default_factory=list, max_length=20)
    rule: dict[str, Any]
    severity: str = Field(default="medium", max_length=20)
    status: str = Field(default="active", max_length=30)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObserverRunRequest(BaseModel):
    run_id: str | None = Field(default=None, max_length=80)
    owner_instruction: str = Field(default="", max_length=4000)


class OutsourcingRequestCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=240)
    complexity_reason: str = Field(default="", max_length=4000)
    task_spec: dict[str, Any] = Field(default_factory=dict)
    context_pack: dict[str, Any] = Field(default_factory=dict)
    acceptance_tests: list[str] = Field(default_factory=list, max_length=50)
    foss_constraints: list[str] = Field(default_factory=list, max_length=50)
    security_constraints: list[str] = Field(default_factory=list, max_length=50)
    files_involved: list[str] = Field(default_factory=list, max_length=100)
    expected_artifact: str = Field(default="", max_length=4000)
    replay_instructions: str = Field(default="", max_length=4000)
    source_type: str | None = Field(default=None, max_length=80)
    source_id: str | None = Field(default=None, max_length=200)


class OutsourcingResolveRequest(BaseModel):
    status: str = Field(default="resolved", max_length=30)
    note: str = Field(default="", max_length=4000)
    artifact_reference: str = Field(default="", max_length=500)
    acceptance_verified: bool = False


class CredentialRotationEvidenceRequest(BaseModel):
    scope: str = Field(default="staging", pattern="^[a-z0-9_.:-]{1,80}$")
    secret_names: list[str] = Field(default_factory=list, max_length=50)
    evidence_reference: str = Field(default="owner-console", max_length=500)
    note: str = Field(default="", max_length=2000)
    rotated_at: str | None = Field(default=None, max_length=100)


class OperatingCadenceFollowUpResolveRequest(BaseModel):
    action: str = Field(default="reviewed", pattern="^(reviewed|deferred|dismissed)$")
    note: str = Field(default="", max_length=2000)
    defer_until: str | None = None


class RetentionCleanupRequest(BaseModel):
    dry_run: bool = True


class SubjectDeleteRequest(BaseModel):
    dry_run: bool = True
    audit_preserving: bool = True


def _provider_key(item: dict[str, Any]) -> str:
    return str(item.get("provider") or item.get("channel") or "").lower()


def _annotate_provider_status(item: dict[str, Any]) -> dict[str, Any]:
    annotated = dict(item)
    provider = _provider_key(annotated)
    required = provider in settings.required_provider_names
    mode = annotated.get("mode")
    live = mode == "live"
    optional_disabled = not required and not live
    blocking = settings.require_live_tool_executors and required and not live
    annotated.update(
        {
            "required": required,
            "optional_disabled": optional_disabled,
            "blocking": blocking,
        }
    )
    return annotated


def _tool_is_required_for_readiness(tool: dict[str, Any]) -> bool:
    required = settings.required_provider_names
    name = str(tool.get("name") or "")
    category = str(tool.get("category") or "")
    if category == "erpnext" or name.startswith("erpnext_"):
        return "erpnext" in required
    if name in {
        "crm_contact_update",
        "crm_deal_update",
        "task_create",
        "task_update",
        "ticket_create",
        "ticket_update",
        "procurement_request",
    }:
        return "erpnext" in required
    if name == "send_email":
        return "smtp" in required or "email" in required
    if name == "send_sms":
        return "sms" in required or "twilio" in required or "jasmin" in required
    if name == "make_call":
        return "voice" in required or "twilio" in required or "asterisk" in required
    if name == "send_message":
        return bool({"slack", "telegram", "whatsapp"} & required)
    if name == "ci_trigger":
        return "github" in required or "github_ci" in required or "ci" in required
    return True


def _readiness_evidence_service(request: Request) -> ProductionReadinessEvidenceService:
    service = getattr(request.app.state, "readiness_evidence_service", None)
    if service:
        return service
    return ProductionReadinessEvidenceService(
        audit_service=getattr(request.app.state, "audit_service", None),
    )


def _clear_operations_readiness_cache(request: Request) -> None:
    request.app.state.operations_readiness_cache = None


def _governor_service(request: Request):
    service = getattr(request.app.state, "orchestration_governor_service", None)
    if not service:
        raise HTTPException(503, "Chief Operating Agent governor is not available")
    return service


def _executive_service(request: Request):
    service = getattr(request.app.state, "executive_company_os_service", None)
    if not service:
        raise HTTPException(503, "Executive Company OS service is not available")
    return service


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


@router.post("/governor/run")
async def run_orchestration_governor(
    data: GovernorRunRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "run",
        "orchestration_governor",
        "cycle",
        context=data.model_dump(),
    )
    if data.mode == "executive":
        result = await _executive_service(request).run_executive_cycle(
            actor=principal.email,
            dry_run=data.dry_run,
            auto_apply_low_risk=data.auto_apply_low_risk,
            max_actions=data.max_actions,
            force_reflection=data.force_reflection,
            force_benchmark_refresh=data.force_benchmark_refresh,
            owner_instruction=data.owner_instruction,
            observer_review=data.observer_review,
            synthetic_large_impact=data.synthetic_large_impact,
        )
    else:
        result = await _governor_service(request).run_once(
            actor=principal.email,
            dry_run=data.dry_run,
            auto_apply_low_risk=data.auto_apply_low_risk,
            max_actions=data.max_actions,
            continue_on_error=data.continue_on_error,
        )
    _clear_operations_readiness_cache(request)
    return result


@router.get("/governor/latest")
async def latest_orchestration_governor_run(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "orchestration_governor",
        "latest",
    )
    latest = await _governor_service(request).latest_run()
    executive = getattr(request.app.state, "executive_company_os_service", None)
    executive_latest = await executive.latest_run() if executive else None
    executive_brief = (
        await executive.executive_brief()
        if executive and executive_latest
        else None
    )
    if latest or executive_latest:
        payload = latest or {}
        return {
            **payload,
            "executive": executive_latest,
            "objective_summary": (
                executive_brief.get("objectives") if executive_brief else None
            ),
            "kpi_summary": executive_brief.get("kpis") if executive_brief else None,
            "benchmark_summary": (
                executive_brief.get("benchmarks") if executive_brief else None
            ),
            "observer_review": (
                executive_brief.get("observer", {}).get("latest_review")
                if executive_brief
                else None
            ),
            "operation_graph_links": {
                "latest": "/api/operations/operation-graph",
                "reflections": "/api/operations/governor/reflections",
                "observer": "/api/operations/observer/reviews",
            },
        }
    return {
        "status": "waiting",
        "detail": "Chief Operating Agent governor has not run yet.",
    }


@router.get("/governor/runs")
async def list_orchestration_governor_runs(
    request: Request,
    limit: int = 20,
    principal: Principal = Depends(get_current_principal),
):
    safe_limit = max(1, min(limit, 200))
    await require_authorization(
        request,
        principal,
        "read",
        "orchestration_governor_run",
        context={"limit": safe_limit},
    )
    return await _governor_service(request).list_runs(limit=safe_limit)


@router.get("/governor/decisions")
async def list_orchestration_governor_decisions(
    request: Request,
    status: str | None = None,
    decision_type: str | None = None,
    limit: int = 100,
    principal: Principal = Depends(get_current_principal),
):
    safe_limit = max(1, min(limit, 500))
    await require_authorization(
        request,
        principal,
        "read",
        "orchestration_governor_decision",
        context={
            "status": status,
            "decision_type": decision_type,
            "limit": safe_limit,
        },
    )
    return await _governor_service(request).list_decisions(
        status=status,
        decision_type=decision_type,
        limit=safe_limit,
    )


@router.get("/governor/tool-proposals")
async def list_orchestration_tool_proposals(
    request: Request,
    status: str | None = None,
    limit: int = 100,
    principal: Principal = Depends(get_current_principal),
):
    safe_limit = max(1, min(limit, 500))
    await require_authorization(
        request,
        principal,
        "read",
        "orchestration_tool_proposal",
        context={"status": status, "limit": safe_limit},
    )
    return await _governor_service(request).list_tool_proposals(
        status=status,
        limit=safe_limit,
    )


@router.post("/governor/tool-proposals/{proposal_id}/approval")
async def request_orchestration_tool_proposal_approval(
    proposal_id: str,
    data: ToolProposalApprovalRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "approve",
        "orchestration_tool_proposal",
        proposal_id,
        context=data.model_dump(),
    )
    try:
        return await _governor_service(request).request_tool_proposal_approval(
            proposal_id,
            actor=principal.email,
            note=data.note,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/company-objectives")
async def get_company_objectives(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "company_objectives")
    return await _executive_service(request).list_objectives()


@router.put("/company-objectives")
async def update_company_objectives(
    data: CompanyObjectivesRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "update",
        "company_objectives",
        context={"count": len(data.objectives)},
    )
    result = await _executive_service(request).replace_objectives(
        actor=principal.email,
        objectives=data.objectives,
    )
    _clear_operations_readiness_cache(request)
    return result


@router.get("/executive-brief")
async def get_executive_brief(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "executive_brief")
    return await _executive_service(request).executive_brief()


@router.get("/operation-graph")
async def get_operation_graph(
    request: Request,
    node_type: str | None = None,
    source_type: str | None = None,
    risk_level: str | None = None,
    limit: int = 100,
    principal: Principal = Depends(get_current_principal),
):
    safe_limit = max(1, min(limit, 500))
    await require_authorization(
        request,
        principal,
        "read",
        "operation_graph",
        context={
            "node_type": node_type,
            "source_type": source_type,
            "risk_level": risk_level,
            "limit": safe_limit,
        },
    )
    return await _executive_service(request).operation_graph(
        node_type=node_type,
        source_type=source_type,
        risk_level=risk_level,
        limit=safe_limit,
    )


@router.get("/governor/reflections")
async def list_governor_reflections(
    request: Request,
    limit: int = 50,
    principal: Principal = Depends(get_current_principal),
):
    safe_limit = max(1, min(limit, 200))
    await require_authorization(
        request,
        principal,
        "read",
        "executive_reflection",
        context={"limit": safe_limit},
    )
    return await _executive_service(request).list_reflections(limit=safe_limit)


@router.get("/governor/benchmarks")
async def list_governor_benchmarks(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "executive_benchmark")
    return await _executive_service(request).list_benchmarks()


@router.post("/governor/benchmarks")
async def create_governor_benchmark(
    data: ExecutiveBenchmarkRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "upsert",
        "executive_benchmark",
        data.key,
        context=data.model_dump(),
    )
    try:
        result = await _executive_service(request).create_benchmark(
            actor=principal.email,
            data=data.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _clear_operations_readiness_cache(request)
    return result


@router.get("/governor/benchmark-results")
async def list_governor_benchmark_results(
    request: Request,
    limit: int = 100,
    principal: Principal = Depends(get_current_principal),
):
    safe_limit = max(1, min(limit, 500))
    await require_authorization(
        request,
        principal,
        "read",
        "executive_benchmark_result",
        context={"limit": safe_limit},
    )
    return await _executive_service(request).list_benchmark_results(limit=safe_limit)


@router.get("/governor/autonomy-policy")
async def get_governor_autonomy_policy(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "autonomy_policy")
    return await _executive_service(request).get_policy()


@router.put("/governor/autonomy-policy")
async def update_governor_autonomy_policy(
    data: AutonomyPolicyRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "update",
        "autonomy_policy",
        "default",
        context=data.model_dump(exclude_none=True),
    )
    result = await _executive_service(request).update_policy(
        actor=principal.email,
        updates=data.model_dump(exclude_none=True),
    )
    _clear_operations_readiness_cache(request)
    return result


@router.post("/governor/instruct")
async def instruct_governor(
    data: GovernorInstructionRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "instruct",
        "executive_governor",
        context={
            "dry_run": data.dry_run,
            "observer_review": data.observer_review,
        },
    )
    result = await _executive_service(request).run_executive_cycle(
        actor=principal.email,
        dry_run=data.dry_run,
        owner_instruction=data.instruction,
        observer_review=data.observer_review,
    )
    _clear_operations_readiness_cache(request)
    return result


@router.post("/governor/pause")
async def pause_governor(
    data: GovernorPauseRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "pause",
        "executive_governor",
        context={"reason": data.reason},
    )
    result = await _executive_service(request).pause(
        actor=principal.email,
        reason=data.reason,
    )
    _clear_operations_readiness_cache(request)
    return result


@router.post("/governor/resume")
async def resume_governor(
    data: GovernorPauseRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "resume",
        "executive_governor",
        context={"reason": data.reason},
    )
    result = await _executive_service(request).resume(
        actor=principal.email,
        reason=data.reason,
    )
    _clear_operations_readiness_cache(request)
    return result


@router.get("/observer/reviews")
async def list_observer_reviews(
    request: Request,
    limit: int = 100,
    principal: Principal = Depends(get_current_principal),
):
    safe_limit = max(1, min(limit, 500))
    await require_authorization(
        request,
        principal,
        "read",
        "observer_review",
        context={"limit": safe_limit},
    )
    return await _executive_service(request).list_observer_reviews(limit=safe_limit)


@router.post("/observer/run")
async def run_observer_review(
    data: ObserverRunRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "run",
        "observer_review",
        context=data.model_dump(),
    )
    result = await _executive_service(request).run_observer_review(
        actor=principal.email,
        run_id=data.run_id,
        owner_instruction=data.owner_instruction,
    )
    _clear_operations_readiness_cache(request)
    return result


@router.get("/outsourcing-requests")
async def list_outsourcing_requests(
    request: Request,
    status: str | None = None,
    limit: int = 100,
    principal: Principal = Depends(get_current_principal),
):
    safe_limit = max(1, min(limit, 500))
    await require_authorization(
        request,
        principal,
        "read",
        "outsourcing_request",
        context={"status": status, "limit": safe_limit},
    )
    return await _executive_service(request).list_outsourcing_requests(
        status=status,
        limit=safe_limit,
    )


@router.post("/outsourcing-requests")
async def create_outsourcing_request(
    data: OutsourcingRequestCreate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "create",
        "outsourcing_request",
        context={"title": data.title},
    )
    try:
        result = await _executive_service(request).create_outsourcing_request(
            actor=principal.email,
            data=data.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _clear_operations_readiness_cache(request)
    return result


@router.post("/outsourcing-requests/{request_id}/resolve")
async def resolve_outsourcing_request(
    request_id: str,
    data: OutsourcingResolveRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "resolve",
        "outsourcing_request",
        request_id,
        context=data.model_dump(),
    )
    try:
        result = await _executive_service(request).resolve_outsourcing_request(
            request_id,
            actor=principal.email,
            resolution=data.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    _clear_operations_readiness_cache(request)
    return result


@router.get("/resource-policy")
async def get_resource_policy(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "resource_policy")
    return await _executive_service(request).resource_policy_status()


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


@router.get("/owner-attention")
async def list_owner_attention(
    request: Request,
    status: str | None = "active",
    limit: int = 50,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "owner_attention",
        context={"status": status, "limit": limit},
    )
    planner = request.app.state.autonomous_planning_service
    return await planner.list_owner_attention(status=status, limit=limit)


@router.post("/owner-attention/notify")
async def notify_owner_attention(
    data: OwnerAttentionNotifyRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "notify",
        "owner_attention",
        context=data.model_dump(),
    )
    service = getattr(request.app.state, "owner_attention_notification_service", None)
    if not service:
        raise HTTPException(503, "Owner attention notification service is not available")
    return await service.run_once(
        actor=principal.email,
        limit=data.limit,
        dry_run=data.dry_run,
    )


@router.get("/owner-attention/notifications/status")
async def owner_attention_notification_status(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "owner_attention_notification",
    )
    service = getattr(request.app.state, "owner_attention_notification_service", None)
    if not service:
        return {
            "enabled": False,
            "status": "unavailable",
            "detail": "Owner attention notification service is not available.",
        }
    status = await service.status()
    runtime_status = getattr(
        request.app.state,
        "owner_attention_notification_status",
        {},
    )
    return {
        **status,
        "runtime": runtime_status,
    }


@router.post("/alerts/test-email")
async def test_alert_email_delivery(
    data: AlertEmailTestRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "test",
        "operations_alert",
        "email",
        context={"dry_run": data.dry_run, "note": data.note},
    )
    comms = getattr(request.app.state, "comms_gateway", None)
    if not comms:
        raise HTTPException(503, "Communications gateway is not available")
    if not settings.owner_email:
        raise HTTPException(400, "OWNER_EMAIL is required for alert delivery tests")
    email = SimpleNamespace(
        to_address=settings.owner_email,
        subject="[Cyber-Team] Alert delivery test",
        body=(
            "Cyber-Team alert delivery test.\n\n"
            "This proves the required owner email alert channel can deliver."
            + (f"\n\nOwner note: {data.note}" if data.note else "")
        ),
        cc=[],
        agent_id=None,
        idempotency_key=None if not data.dry_run else "alert-test:dry-run",
    )
    if data.dry_run:
        response = {
            "email_id": None,
            "status": "simulated",
            "provider": "dry_run",
        }
    else:
        response = await comms.send_email(email)
    evidence = await _readiness_evidence_service(request).record_alert_test(
        actor=principal.email,
        response=response,
        dry_run=data.dry_run,
    )
    _clear_operations_readiness_cache(request)
    return {
        "status": "ready" if response.get("status") in {"sent", "simulated"} else "failed",
        "dry_run": data.dry_run,
        "recipient": settings.owner_email,
        "response": response,
        "evidence": evidence,
    }


@router.post("/security/credential-rotation/evidence")
async def record_credential_rotation_evidence(
    data: CredentialRotationEvidenceRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "record",
        "credential_rotation_evidence",
        data.scope,
        context={
            "scope": data.scope,
            "secret_names": data.secret_names,
            "evidence_reference": data.evidence_reference,
        },
    )
    evidence = await _readiness_evidence_service(
        request,
    ).record_credential_rotation_evidence(
        actor=principal.email,
        scope=data.scope,
        secret_names=data.secret_names,
        evidence_reference=data.evidence_reference,
        note=data.note,
        rotated_at=data.rotated_at,
    )
    _clear_operations_readiness_cache(request)
    return {"status": "recorded", "evidence": evidence}


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


@router.post("/company-context/sync")
async def sync_company_context(
    data: CompanyContextSyncRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "sync",
        "company_context",
        data.source,
        context=data.model_dump(),
    )
    service = request.app.state.company_context_sync_service
    return await service.sync_from_erpnext(
        actor=principal.email,
        dry_run=data.dry_run,
        apply_low_risk=data.apply_low_risk,
        run_planner=data.run_planner,
    )


@router.get("/company-context")
async def get_company_context(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "company_context", "latest")
    service = request.app.state.company_context_sync_service
    return await service.get_latest_context()


@router.get("/company-context/sync-runs")
async def list_company_context_sync_runs(
    request: Request,
    limit: int = 20,
    principal: Principal = Depends(get_current_principal),
):
    safe_limit = max(1, min(limit, 200))
    await require_authorization(
        request,
        principal,
        "read",
        "company_context_sync_run",
        context={"limit": safe_limit},
    )
    service = request.app.state.company_context_sync_service
    return await service.list_sync_runs(limit=safe_limit)


@router.post("/company-context/drift-scan")
async def scan_company_context_drift(
    data: CompanyContextDriftScanRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "scan",
        "company_context_drift",
        "erpnext",
        context=data.model_dump(),
    )
    service = request.app.state.company_context_sync_service
    return await service.scan_for_erpnext_drift(
        actor=principal.email,
        dry_run=data.dry_run,
        apply_low_risk=data.apply_low_risk,
        run_planner=data.run_planner,
    )


@router.get("/company-context/drift-status")
async def get_company_context_drift_status(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "company_context_drift",
        "erpnext",
    )
    service = request.app.state.company_context_sync_service
    return await service.drift_status()


@router.get("/operating-cadence/status")
async def get_operating_cadence_status(
    request: Request,
    company_namespace: str | None = None,
    limit: int = 200,
    principal: Principal = Depends(get_current_principal),
):
    safe_limit = max(1, min(limit, 500))
    await require_authorization(
        request,
        principal,
        "read",
        "operating_cadence",
        company_namespace or "all",
        context={"limit": safe_limit},
    )
    planner = request.app.state.autonomous_planning_service
    return await planner.operating_cadence_status(
        company_namespace=company_namespace,
        limit=safe_limit,
    )


@router.get("/operating-cadence/follow-ups")
async def list_operating_cadence_follow_ups(
    request: Request,
    status: str | None = "active",
    kind: str | None = None,
    target_view: str | None = None,
    company_namespace: str | None = None,
    limit: int = 100,
    principal: Principal = Depends(get_current_principal),
):
    safe_limit = max(1, min(limit, 500))
    await require_authorization(
        request,
        principal,
        "read",
        "operating_cadence_follow_up",
        company_namespace or "all",
        context={
            "status": status,
            "kind": kind,
            "target_view": target_view,
            "limit": safe_limit,
        },
    )
    planner = request.app.state.autonomous_planning_service
    return await planner.list_operating_follow_ups(
        status=status,
        kind=kind,
        target_view=target_view,
        company_namespace=company_namespace,
        limit=safe_limit,
    )


@router.post("/operating-cadence/follow-ups/{plan_id}/resolve")
async def resolve_operating_cadence_follow_up(
    plan_id: str,
    data: OperatingCadenceFollowUpResolveRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        data.action,
        "operating_cadence_follow_up",
        plan_id,
        context=data.model_dump(),
    )
    planner = request.app.state.autonomous_planning_service
    try:
        return await planner.resolve_operating_follow_up(
            plan_id,
            action=data.action,
            note=data.note,
            actor=principal.email,
            defer_until=data.defer_until,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/operating-cadence/scan")
async def scan_operating_cadences(
    data: OperatingCadenceScanRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "scan",
        "operating_cadence",
        data.company_namespace or "all",
        context=data.model_dump(),
    )
    payload = data.model_dump()
    if settings.autonomy_side_effect_mode == "manual_only":
        payload["auto_execute"] = False
    planner = request.app.state.autonomous_planning_service
    return await planner.scan_operating_cadences(
        actor=principal.email,
        **payload,
    )


@router.get("/readiness")
async def operations_readiness(
    request: Request,
    refresh: bool = False,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "operations_readiness",
        "production",
    )
    cache = getattr(request.app.state, "operations_readiness_cache", None)
    now = time.monotonic()
    if (
        not refresh
        and isinstance(cache, dict)
        and cache.get("expires_at", 0) > now
        and isinstance(cache.get("payload"), dict)
    ):
        return cache["payload"]

    registry = request.app.state.tool_registry
    tools = registry.list_tool_contracts()
    tool_counts: dict[str, int] = {}
    side_effect_blockers = []
    non_blocking_side_effects = []
    for tool in tools:
        state = tool.get("state") or "unknown"
        tool_counts[state] = tool_counts.get(state, 0) + 1
        if tool.get("side_effects") and state != "live":
            entry = {
                "tool_name": tool["name"],
                "state": state,
                "reason": tool.get("readiness_reason"),
            }
            if _tool_is_required_for_readiness(tool):
                side_effect_blockers.append(entry)
            else:
                non_blocking_side_effects.append(entry)

    comms = [
        _annotate_provider_status(item)
        for item in request.app.state.comms_gateway.integration_status()
    ]
    inbound_email = getattr(request.app.state, "inbound_email_service", None)
    if inbound_email:
        comms.append(_annotate_provider_status(inbound_email.integration_status()))
    erpnext = getattr(request.app.state, "erpnext", None)
    erpnext_status = None
    if erpnext:
        erpnext_status = _annotate_provider_status(
            erpnext.integration_status(
                getattr(request.app.state, "erpnext_last_validation_result", None)
            )
        )
    elif "erpnext" in settings.required_provider_names:
        erpnext_status = _annotate_provider_status(
            {
                "provider": "erpnext",
                "configured": False,
                "mode": "configuration_required",
                "detail": "ERPNext client is not available.",
            }
        )
    provider_items = [*comms]
    if erpnext_status:
        provider_items.append(erpnext_status)
    integration_blockers = [
        {
            "channel": item.get("channel"),
            "provider": item.get("provider"),
            "mode": item.get("mode"),
            "required": item.get("required"),
            "reason": item.get("detail") or item.get("readiness_reason"),
        }
        for item in provider_items
        if item.get("blocking")
    ]
    optional_disabled = [
        item for item in provider_items if item.get("optional_disabled")
    ]
    company_context_service = getattr(
        request.app.state,
        "company_context_sync_service",
        None,
    )
    company_context_status = {
        "status": "unavailable",
        "required": "erpnext" in settings.required_provider_names,
        "blocking": "erpnext" in settings.required_provider_names,
        "stale": True,
        "detail": "Company context sync service is not available.",
    }
    if company_context_service:
        latest_snapshot = await company_context_service.latest_snapshot()
        latest_runs = await company_context_service.list_sync_runs(limit=1)
        company_context_status = company_context_service.readiness_from_snapshot(
            latest_snapshot,
            latest_run=latest_runs[0] if latest_runs else None,
        )
        company_context_status["drift_detection"] = await company_context_service.drift_status()
    company_context_blockers = []
    if company_context_status.get("blocking"):
        company_context_blockers.append(
            {
                "provider": "erpnext",
                "mode": company_context_status.get("status"),
                "required": company_context_status.get("required"),
                "reason": company_context_status.get("detail"),
            }
        )

    planner = getattr(request.app.state, "autonomous_planning_service", None)
    operating_cadence_status = {
        "status": "unavailable",
        "detail": "Autonomous planner is not available.",
        "counts": {
            "cadences": 0,
            "due": 0,
            "not_due": 0,
            "active_plans": 0,
        },
    }
    if planner and hasattr(planner, "operating_cadence_status"):
        try:
            operating_cadence_status = await planner.operating_cadence_status(limit=200)
        except Exception as exc:
            operating_cadence_status = {
                "status": "degraded",
                "detail": str(exc),
                "counts": {
                    "cadences": 0,
                    "due": 0,
                    "not_due": 0,
                    "active_plans": 0,
                },
            }
    operating_follow_ups_status = {
        "status": "unavailable",
        "detail": "Autonomous planner follow-up queue is not available.",
        "counts": {
            "active": 0,
            "completed": 0,
            "total_visible": 0,
        },
        "active": None,
        "completed": None,
    }
    if planner and hasattr(planner, "list_operating_follow_ups"):
        try:
            active_follow_ups = await planner.list_operating_follow_ups(
                status="active",
                limit=200,
            )
            completed_follow_ups = await planner.list_operating_follow_ups(
                status="completed",
                limit=200,
            )
            operating_follow_ups_status = {
                "status": "ready",
                "detail": "Operating cadence follow-up queue is available.",
                "counts": {
                    "active": active_follow_ups.get("counts", {}).get("total", 0),
                    "completed": completed_follow_ups.get("counts", {}).get("total", 0),
                    "total_visible": (
                        active_follow_ups.get("counts", {}).get("total", 0)
                        + completed_follow_ups.get("counts", {}).get("total", 0)
                    ),
                },
                "active": active_follow_ups.get("counts", {}),
                "completed": completed_follow_ups.get("counts", {}),
            }
        except Exception as exc:
            operating_follow_ups_status = {
                "status": "degraded",
                "detail": str(exc),
                "counts": {
                    "active": 0,
                    "completed": 0,
                    "total_visible": 0,
                },
                "active": None,
                "completed": None,
            }

    owner_attention_status = {
        "generated_at": None,
        "filters": {"status": "active", "limit": 100},
        "counts": {
            "total": 0,
            "active": 0,
            "completed": 0,
            "overdue": 0,
            "due_soon": 0,
            "scheduler_created": 0,
            "executable": 0,
            "waiting_approval": 0,
        },
        "items": [],
        "status": "unavailable",
        "detail": "Owner attention queue is not available.",
    }
    if planner and hasattr(planner, "list_owner_attention"):
        try:
            owner_attention_status = await planner.list_owner_attention(
                status="active",
                limit=100,
            )
            owner_attention_status["status"] = "ready"
            owner_attention_status["detail"] = "Owner attention queue is available."
        except Exception as exc:
            owner_attention_status = {
                **owner_attention_status,
                "status": "degraded",
                "detail": str(exc),
            }

    operating_cadence_scheduler_status = getattr(
        request.app.state,
        "operating_cadence_scheduler_status",
        {
            "enabled": False,
            "status": "unavailable",
            "detail": "Operating cadence scheduler status is not available.",
            "actor": "operating_cadence_scheduler",
            "auto_execute": False,
            "interval_seconds": None,
            "limit": None,
            "last_started_at": None,
            "last_completed_at": None,
            "last_result": None,
            "last_error": None,
        },
    )
    owner_attention_notification_runtime = getattr(
        request.app.state,
        "owner_attention_notification_status",
        {
            "enabled": False,
            "status": "unavailable",
            "detail": "Owner attention notification worker status is not available.",
            "actor": "owner_attention_notifier",
            "channel": "email",
            "interval_seconds": None,
            "limit": None,
            "last_started_at": None,
            "last_completed_at": None,
            "last_result": None,
            "last_error": None,
        },
    )
    owner_attention_notification_service = getattr(
        request.app.state,
        "owner_attention_notification_service",
        None,
    )
    if owner_attention_notification_service:
        owner_attention_notification_status = (
            await owner_attention_notification_service.status()
        )
    else:
        owner_attention_notification_status = {
            "enabled": False,
            "status": "unavailable",
            "detail": "Owner attention notification service is not available.",
        }
    owner_attention_notification_status = {
        **owner_attention_notification_status,
        "runtime": owner_attention_notification_runtime,
    }

    team_activation_service = getattr(request.app.state, "team_activation_service", None)
    if team_activation_service:
        team_activation_status = await team_activation_service.coverage_summary()
    else:
        team_activation_status = {
            "status": "unavailable",
            "latest_run": None,
            "active_agent_count": 0,
            "active_grant_count": 0,
            "pending_or_blocked_grant_count": 0,
            "blocking": True,
            "detail": "Team activation service is not available.",
        }

    workflow_template_service = getattr(request.app.state, "workflow_template_service", None)
    if workflow_template_service:
        templates = await workflow_template_service.list_templates(
            status="active",
            is_core=True,
        )
        workflows = await request.app.state.orchestrator.list_workflows()
        template_workflows = [
            workflow
            for workflow in workflows
            if (workflow.get("trigger_config") or {}).get("template_id")
        ]
        workflow_templates_status = {
            "status": "ready" if templates and template_workflows else "degraded",
            "core_template_count": len(templates),
            "core_workflow_count": len(template_workflows),
            "template_ids": [template["id"] for template in templates],
            "workflow_ids": [workflow["id"] for workflow in template_workflows],
            "blocking": not templates or not template_workflows,
        }
    else:
        workflow_templates_status = {
            "status": "unavailable",
            "core_template_count": 0,
            "core_workflow_count": 0,
            "template_ids": [],
            "workflow_ids": [],
            "blocking": True,
            "detail": "Workflow template service is not available.",
        }

    interop_service = getattr(request.app.state, "interop_service", None)
    if interop_service:
        interop_status = await interop_service.summary()
        interop_status["blocking"] = False
    else:
        interop_status = {
            "status": "unavailable",
            "blocking": True,
            "detail": "Interop adapter service is not available.",
        }

    governor_service = getattr(
        request.app.state,
        "orchestration_governor_service",
        None,
    )
    if governor_service:
        governor_status = await governor_service.readiness()
    else:
        governor_status = {
            "enabled": False,
            "status": "unavailable",
            "blocking": False,
            "detail": "Chief Operating Agent governor service is not available.",
            "latest_run": None,
            "safety": {
                "manual_only_external_side_effects": True,
                "generated_code_hot_loading": False,
            },
        }
    governor_status = {
        **governor_status,
        "scheduler": getattr(
            request.app.state,
            "orchestration_governor_scheduler_status",
            {
                "enabled": False,
                "status": "unavailable",
                "detail": "Chief Operating Agent governor scheduler status is unavailable.",
                "last_started_at": None,
                "last_completed_at": None,
                "last_result": None,
                "last_error": None,
            },
        ),
    }
    executive_service = getattr(request.app.state, "executive_company_os_service", None)
    if executive_service:
        executive_status = await executive_service.readiness()
    else:
        executive_status = {
            "status": "unavailable",
            "blocking": False,
            "enabled": False,
            "detail": "Executive Company OS service is not available.",
            "resource_policy": {"status": "unavailable", "blocking": True},
            "observer": {"status": "unavailable", "agent_present": False},
            "operation_graph": {
                "indexing_enabled": settings.operation_graph_indexing_enabled,
                "latest_node_present": False,
            },
            "benchmark_freshness": {"status": "waiting", "stale": True},
            "reflection_freshness": {"status": "waiting", "stale": True},
            "outsourcing": {"status": "unavailable", "open_count": 0},
        }

    production_evidence = await _readiness_evidence_service(request).summary()

    evidence = await request.app.state.audit_service.list_events(
        event_type="control.evidence",
        limit=50,
    )
    traces = await request.app.state.memory_service.list_memory_traces(limit=50)
    trace_errors = [
        trace for trace in traces
        if trace.get("errors") or trace.get("metadata", {}).get("coverage") == "error"
    ]
    operational_blockers = [
        {
            "area": area,
            "mode": item.get("status"),
            "reason": item.get("detail"),
        }
        for area, item in production_evidence.items()
        if item.get("blocking")
    ]
    runtime_blockers = [
        {
            "area": "team_activation",
            "mode": team_activation_status.get("status"),
            "reason": "Safe AI team activation has not completed successfully.",
        }
        if team_activation_status.get("blocking")
        else None,
        {
            "area": "workflow_templates",
            "mode": workflow_templates_status.get("status"),
            "reason": "Core workflow templates or their manual workflows are missing.",
        }
        if workflow_templates_status.get("blocking")
        else None,
        {
            "area": "interop",
            "mode": interop_status.get("status"),
            "reason": "MCP/A2A adapter surfaces are unavailable.",
        }
        if interop_status.get("blocking")
        else None,
        {
            "area": "orchestration_governor",
            "mode": governor_status.get("status"),
            "reason": governor_status.get("detail"),
        }
        if governor_status.get("blocking")
        else None,
        {
            "area": "executive_autonomy",
            "mode": executive_status.get("status"),
            "reason": "Executive Company OS readiness is blocked.",
        }
        if executive_status.get("blocking")
        else None,
    ]
    blockers = (
        side_effect_blockers
        + integration_blockers
        + company_context_blockers
        + operational_blockers
        + [blocker for blocker in runtime_blockers if blocker]
    )
    status = "ready" if not blockers else "degraded"
    payload = {
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
            "non_blocking_side_effects": non_blocking_side_effects,
        },
        "integrations": {
            "communications": comms,
            "erpnext": erpnext_status,
            "required_providers": sorted(settings.required_provider_names),
            "required_blockers": integration_blockers,
            "optional_disabled": optional_disabled,
            "blocking_readiness": bool(integration_blockers),
            "blocking_reasons": integration_blockers,
        },
        "company_context": company_context_status,
        "operating_cadence": operating_cadence_status,
        "operating_cadence_scheduler": operating_cadence_scheduler_status,
        "operating_follow_ups": operating_follow_ups_status,
        "owner_attention": owner_attention_status,
        "owner_attention_notifications": owner_attention_notification_status,
        "team_activation": team_activation_status,
        "workflow_templates": workflow_templates_status,
        "interop": interop_status,
        "governor": governor_status,
        "executive_autonomy": executive_status,
        "resource_policy": executive_status.get("resource_policy"),
        "observer": executive_status.get("observer"),
        "operation_graph": executive_status.get("operation_graph"),
        "benchmark_freshness": executive_status.get("benchmark_freshness"),
        "reflection_freshness": executive_status.get("reflection_freshness"),
        "outsourcing": executive_status.get("outsourcing"),
        "ci": production_evidence["ci"],
        "alerts": production_evidence["alerts"],
        "backup_restore": production_evidence["backup_restore"],
        "credential_rotation": production_evidence["credential_rotation"],
        "load_test": production_evidence["load_test"],
        "business_workflow_smoke": production_evidence["business_workflow_smoke"],
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
    request.app.state.operations_readiness_cache = {
        "expires_at": time.monotonic() + READINESS_CACHE_TTL_SECONDS,
        "payload": payload,
    }
    return payload


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
