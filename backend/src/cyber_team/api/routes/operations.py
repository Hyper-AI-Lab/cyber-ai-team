"""Autonomous operations routes."""

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from cyber_team.api.authorization import require_authorization
from cyber_team.api.security import Principal, get_current_principal

router = APIRouter()


class AutonomousCycleRequest(BaseModel):
    run_memory_steward: bool | None = None
    run_supervisor_review: bool | None = None
    apply_safe_memory_actions: bool | None = None
    request_memory_action_approvals: bool | None = None
    memory_remediation_limit: int = Field(default=100, ge=1, le=200)
    continue_on_error: bool = True


class AutonomousCycleResponse(BaseModel):
    cycle_id: str
    started_at: str
    completed_at: str | None
    actor: str
    status: str
    memory_steward: dict[str, Any] | None = None
    supervisor_review: dict[str, Any] | None = None
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


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
        apply_safe_memory_actions=data.apply_safe_memory_actions,
        request_memory_action_approvals=data.request_memory_action_approvals,
        memory_remediation_limit=data.memory_remediation_limit,
        continue_on_error=data.continue_on_error,
    )
