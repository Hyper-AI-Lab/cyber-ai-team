"""Autonomous operations routes."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from cyber_team.api.authorization import require_authorization
from cyber_team.api.security import Principal, get_current_principal

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
    return await planner.scan_and_plan(actor=principal.email, **data.model_dump())


@router.post("/plans/{plan_id}/execute")
async def execute_autonomous_plan(
    plan_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "execute", "autonomous_plan", plan_id)
    planner = request.app.state.autonomous_planning_service
    return await planner.execute_plan(plan_id, actor=principal.email)
