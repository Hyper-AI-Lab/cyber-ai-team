"""Dashboard routes — KPIs, agent status, approval queues."""

from fastapi import APIRouter, Depends, HTTPException, Request, Query, Body
from typing import Optional
from cyber_team.api.authorization import require_authorization
from cyber_team.api.security import Principal, get_current_principal

router = APIRouter()


@router.get("/kpis")
async def get_kpis(request: Request, principal: Principal = Depends(get_current_principal)):
    await require_authorization(request, principal, "read", "dashboard")
    orchestrator = request.app.state.orchestrator
    return await orchestrator.get_kpis()


@router.get("/agent-status")
async def get_agent_status(request: Request, principal: Principal = Depends(get_current_principal)):
    await require_authorization(request, principal, "read", "agent_status")
    mgr = request.app.state.agent_manager
    return await mgr.get_all_agent_status()


@router.get("/approval-queue")
async def get_approval_queue(
    request: Request,
    status: Optional[str] = Query(None),
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "approval",
        context={"status": status},
    )
    mgr = request.app.state.agent_manager
    return await mgr.get_approval_queue(status)


@router.post("/approval/{approval_id}/approve")
async def approve_action(
    approval_id: str,
    request: Request,
    note: str = Body(default="", embed=True),
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "approve", "approval", approval_id)
    mgr = request.app.state.agent_manager
    try:
        return await mgr.resolve_approval(approval_id, "approved", note)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/approval/{approval_id}/reject")
async def reject_action(
    approval_id: str,
    request: Request,
    note: str = Body(default="", embed=True),
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "reject", "approval", approval_id)
    mgr = request.app.state.agent_manager
    try:
        return await mgr.resolve_approval(approval_id, "rejected", note)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/workflow-visualizations")
async def get_workflow_visualizations(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "workflow_visualization")
    orchestrator = request.app.state.orchestrator
    return await orchestrator.get_workflow_visualizations()


@router.get("/recent-activity")
async def get_recent_activity(
    request: Request,
    limit: int = Query(50),
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "activity",
        context={"limit": limit},
    )
    orchestrator = request.app.state.orchestrator
    return await orchestrator.get_recent_activity(limit)
