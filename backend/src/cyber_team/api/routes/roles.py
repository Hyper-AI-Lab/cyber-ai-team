"""Role catalog and role factory routes."""

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from cyber_team.api.authorization import require_authorization
from cyber_team.api.security import Principal, get_current_principal

router = APIRouter()


class RoleManifestCreate(BaseModel):
    family: str
    name: str
    description: str
    instructions_template: str
    default_tools: list[str] = Field(default_factory=list)
    memory_namespace: str | None = None
    approval_policy: str = "auto"
    success_metrics: Any = Field(default_factory=list)
    is_core: bool = True
    config: dict = Field(default_factory=dict)


class RoleManifestResponse(BaseModel):
    id: str
    family: str
    name: str
    description: str
    instructions_template: str
    default_tools: list[str]
    memory_namespace: str
    approval_policy: str
    success_metrics: Any
    is_core: bool
    config: dict


class RoleGapReport(BaseModel):
    title: str
    description: str
    severity: str = Field(default="medium", pattern="^(low|medium|high|critical)$")
    source_agent_id: str | None = None
    source_type: str = "owner"
    company_namespace: str | None = None
    capability: str | None = None
    requested_tools: list[str] = Field(default_factory=list)
    context: dict = Field(default_factory=dict)


class RoleGapProposalRequest(BaseModel):
    company_profile: dict = Field(default_factory=dict)
    approval_id: str | None = None


class RoleGapApprovalRegenerateRequest(BaseModel):
    company_profile: dict = Field(default_factory=dict)


class RoleGapResolveRequest(BaseModel):
    status: str = Field(default="dismissed", pattern="^(deferred|dismissed|resolved)$")
    note: str = ""


class RoleGapBatchActionRequest(BaseModel):
    gap_ids: list[str] = Field(min_length=1, max_length=100)
    action: str = Field(
        pattern="^(propose|apply|regenerate_approval|defer|dismiss)$",
    )
    company_profile: dict = Field(default_factory=dict)
    approval_ids: dict[str, str] = Field(default_factory=dict)
    note: str = ""


class TeamActivationRunRequest(BaseModel):
    dry_run: bool = False
    apply_safe_roles: bool = True
    request_high_risk_grants: bool = True
    source_snapshot_id: str | None = None


class SupervisorReviewResponse(BaseModel):
    reviewed_at: str
    actor: str
    role_gaps_reviewed: int
    role_gaps_proposed: list[str]
    role_gap_recommendations: list[dict]
    stale_approvals: list[dict]
    workflow_failure_gaps: list[dict]


def _status_filter(value: str | None) -> list[str] | None:
    if not value or value.strip().lower() == "all":
        return None
    return [
        item.strip()
        for item in value.split(",")
        if item.strip()
    ]


@router.get("/catalog", response_model=list[RoleManifestResponse])
async def list_role_catalog(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "role_manifest")
    mgr = request.app.state.agent_manager
    return await mgr.list_role_manifests()


@router.get("/catalog/{manifest_id}", response_model=RoleManifestResponse)
async def get_role_manifest(
    manifest_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "role_manifest", manifest_id)
    mgr = request.app.state.agent_manager
    manifest = await mgr.get_role_manifest(manifest_id)
    if not manifest:
        raise HTTPException(404, "Role manifest not found")
    return manifest


@router.post("/catalog", response_model=RoleManifestResponse, status_code=201)
async def create_role_manifest(
    data: RoleManifestCreate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "create",
        "role_manifest",
        context={
            "family": data.family,
            "name": data.name,
            "default_tools": data.default_tools,
        },
    )
    mgr = request.app.state.agent_manager
    return await mgr.create_role_manifest(data)


@router.post("/provision", response_model=RoleManifestResponse, status_code=201)
async def provision_role(
    data: RoleManifestCreate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "create",
        "role_manifest",
        context={
            "family": data.family,
            "name": data.name,
            "default_tools": data.default_tools,
        },
    )
    from cyber_team.agents.manager import slug_id
    mgr = request.app.state.agent_manager

    # Validate duplicate manifest name
    manifest_id = slug_id(data.name)
    existing = await mgr.get_role_manifest(manifest_id)
    if existing:
        raise HTTPException(status_code=400, detail=f"Role manifest '{data.name}' already exists.")

    # 1. Create role manifest record in PostgreSQL
    manifest = await mgr.create_role_manifest(data)

    # 2. Instantiate agent instantly
    await mgr.instantiate_role(manifest["id"])

    return manifest


@router.post("/instantiate/{manifest_id}")
async def instantiate_role(
    manifest_id: str,
    request: Request,
    overrides: dict = Body(default_factory=dict),
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "instantiate", "role_manifest", manifest_id)
    mgr = request.app.state.agent_manager
    return await mgr.instantiate_role(manifest_id, overrides)


@router.post("/company-builder")
async def run_company_builder(
    request: Request,
    company_profile: dict = Body(...),
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "run", "company_builder")
    mgr = request.app.state.agent_manager
    result = await mgr.run_company_builder(company_profile)
    return result


@router.get("/role-gaps")
async def list_role_gaps(
    request: Request,
    status: str | None = None,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "role_gap",
        context={"status": status},
    )
    mgr = request.app.state.agent_manager
    return await mgr.list_role_gaps(status)


@router.get("/role-gaps/summary")
async def role_gap_summary(
    request: Request,
    status: str | None = "open,proposed",
    source_type: str | None = None,
    limit: int = 200,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "role_gap",
        context={
            "status": status,
            "source_type": source_type,
            "limit": limit,
            "view": "summary",
        },
    )
    mgr = request.app.state.agent_manager
    return await mgr.summarize_role_backlog(
        statuses=_status_filter(status),
        source_type=source_type,
        limit=limit,
    )


@router.get("/operating-cadence")
async def role_operating_cadence(
    request: Request,
    company_namespace: str | None = None,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "role_operating_cadence",
        context={"company_namespace": company_namespace},
    )
    mgr = request.app.state.agent_manager
    return await mgr.role_operating_cadence(company_namespace=company_namespace)


@router.get("/team-activation/latest")
async def latest_team_activation_run(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "team_activation")
    service = request.app.state.team_activation_service
    return await service.latest_run()


@router.get("/team-activation/runs")
async def list_team_activation_runs(
    request: Request,
    limit: int = 20,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "team_activation",
        context={"limit": limit},
    )
    service = request.app.state.team_activation_service
    return await service.list_runs(limit=limit)


@router.get("/team-activation/coverage")
async def team_activation_coverage(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "team_activation")
    service = request.app.state.team_activation_service
    return await service.coverage_summary()


@router.post("/team-activation/run")
async def run_team_activation(
    data: TeamActivationRunRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "run",
        "team_activation",
        context={
            "dry_run": data.dry_run,
            "apply_safe_roles": data.apply_safe_roles,
            "request_high_risk_grants": data.request_high_risk_grants,
            "source_snapshot_id": data.source_snapshot_id,
        },
    )
    service = request.app.state.team_activation_service
    return await service.run_activation(
        actor=principal.email,
        dry_run=data.dry_run,
        apply_safe_roles=data.apply_safe_roles,
        request_high_risk_grants=data.request_high_risk_grants,
        source_snapshot_id=data.source_snapshot_id,
    )


@router.post("/role-gaps", status_code=201)
async def report_role_gap(
    data: RoleGapReport,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "create",
        "role_gap",
        context={
            "severity": data.severity,
            "capability": data.capability,
            "requested_tools": data.requested_tools,
        },
    )
    mgr = request.app.state.agent_manager
    data.source_type = data.source_type or principal.role
    return await mgr.report_role_gap(data, reporter=principal.email)


@router.post("/role-gaps/supervisor-review", response_model=SupervisorReviewResponse)
async def run_supervisor_role_gap_review(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "review", "role_gap")
    review_service = request.app.state.supervisor_review_service
    return await review_service.run_once(actor=principal.email)


@router.get("/role-gaps/{gap_id}")
async def get_role_gap(
    gap_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "role_gap", gap_id)
    mgr = request.app.state.agent_manager
    gap = await mgr.get_role_gap(gap_id)
    if not gap:
        raise HTTPException(404, "Role gap not found")
    return gap


@router.post("/role-gaps/{gap_id}/proposal")
async def propose_role_gap(
    gap_id: str,
    data: RoleGapProposalRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "propose", "role_gap", gap_id)
    mgr = request.app.state.agent_manager
    try:
        return await mgr.propose_role_for_gap(gap_id, data.company_profile)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/role-gaps/{gap_id}/approval/regenerate")
async def regenerate_role_gap_approval(
    gap_id: str,
    data: RoleGapApprovalRegenerateRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "approve", "role_gap", gap_id)
    mgr = request.app.state.agent_manager
    try:
        return await mgr.regenerate_role_gap_approval(
            gap_id,
            data.company_profile,
            requested_by=principal.email,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/role-gaps/batch")
async def role_gap_batch_action(
    data: RoleGapBatchActionRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        data.action,
        "role_gap_batch",
        context={
            "gap_ids": data.gap_ids,
            "action": data.action,
        },
    )
    mgr = request.app.state.agent_manager
    return await mgr.batch_role_gap_action(
        data.gap_ids,
        action=data.action,
        company_profile=data.company_profile,
        approval_ids=data.approval_ids,
        note=data.note,
        requested_by=principal.email,
    )


@router.post("/role-gaps/{gap_id}/apply")
async def apply_role_gap(
    gap_id: str,
    data: RoleGapProposalRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "apply", "role_gap", gap_id)
    mgr = request.app.state.agent_manager
    try:
        return await mgr.apply_role_gap_proposal(
            gap_id,
            data.company_profile,
            approval_id=data.approval_id,
            requested_by=principal.email,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/role-gaps/{gap_id}/resolve")
async def resolve_role_gap(
    gap_id: str,
    data: RoleGapResolveRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, data.status, "role_gap", gap_id)
    mgr = request.app.state.agent_manager
    try:
        return await mgr.resolve_role_gap(
            gap_id,
            status=data.status,
            note=data.note,
            resolver=principal.email,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/role-gap")
async def propose_new_role(
    request: Request,
    gap_description: str = Body(..., embed=True),
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "propose", "role_manifest")
    mgr = request.app.state.agent_manager
    gap = await mgr.report_role_gap(
        RoleGapReport(
            title=gap_description[:120] or "Role gap",
            description=gap_description,
            severity="medium",
            source_type=principal.role,
        ),
        reporter=principal.email,
    )
    gap = await mgr.propose_role_for_gap(gap["id"])
    return {
        "gap_id": gap["id"],
        "status": gap["status"],
        "gap": gap,
        "proposal": gap["proposed_role"],
    }
