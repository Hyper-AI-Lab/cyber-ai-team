"""Dashboard routes — KPIs, agent status, approval queues."""

import asyncio
import logging
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from cyber_team.api.authorization import require_authorization
from cyber_team.api.rate_limit import enforce_rate_limit
from cyber_team.api.security import Principal, get_current_principal
from cyber_team.config import settings

router = APIRouter()
DASHBOARD_KPI_CACHE_TTL_SECONDS = 5.0
logger = logging.getLogger(__name__)


def _dashboard_refresh_finished(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Background dashboard KPI refresh failed")


async def _refresh_dashboard_kpis(request: Request) -> dict:
    payload = await request.app.state.orchestrator.get_kpis()
    request.app.state.dashboard_kpi_cache = {
        "expires_at": time.monotonic() + DASHBOARD_KPI_CACHE_TTL_SECONDS,
        "payload": payload,
    }
    return payload


@router.get("/kpis")
async def get_kpis(request: Request, principal: Principal = Depends(get_current_principal)):
    await require_authorization(request, principal, "read", "dashboard")
    cache = getattr(request.app.state, "dashboard_kpi_cache", None)
    now = time.monotonic()
    if isinstance(cache, dict) and isinstance(cache.get("payload"), dict):
        if cache.get("expires_at", 0) > now:
            return cache["payload"]
        refresh_task = getattr(
            request.app.state,
            "dashboard_kpi_refresh_task",
            None,
        )
        if not refresh_task or refresh_task.done():
            refresh_task = asyncio.create_task(_refresh_dashboard_kpis(request))
            refresh_task.add_done_callback(_dashboard_refresh_finished)
            request.app.state.dashboard_kpi_refresh_task = refresh_task
        return cache["payload"]
    return await _refresh_dashboard_kpis(request)


@router.get("/agent-status")
async def get_agent_status(request: Request, principal: Principal = Depends(get_current_principal)):
    await require_authorization(request, principal, "read", "agent_status")
    mgr = request.app.state.agent_manager
    return await mgr.get_all_agent_status()


@router.get("/approval-queue")
async def get_approval_queue(
    request: Request,
    status: str | None = Query(None),
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
    await enforce_rate_limit(
        request,
        "approval.resolve",
        settings.rate_limit_approval_per_minute,
        subject=principal.subject,
    )
    await require_authorization(request, principal, "approve", "approval", approval_id)
    mgr = request.app.state.agent_manager
    try:
        res = await mgr.resolve_approval(approval_id, "approved", note)

        # Automatically resume associated WorkflowRun if exists
        from sqlalchemy import select

        from cyber_team.db import async_session
        from cyber_team.db.models import ApprovalRequest, WorkflowRun

        async with async_session() as session:
            app_req = (
                await session.execute(
                    select(ApprovalRequest).where(ApprovalRequest.id == approval_id)
                )
            ).scalar_one_or_none()
            run_id = None
            if app_req and app_req.target_type == "workflow_run":
                run_id = app_req.target_id

            if not run_id:
                runs_res = await session.execute(
                    select(WorkflowRun).where(WorkflowRun.status == "waiting_approval")
                )
                for r in runs_res.scalars().all():
                    state_dict = r.state or {}
                    if approval_id in state_dict.values():
                        run_id = r.id
                        break

            if run_id:
                orchestrator = request.app.state.orchestrator
                await orchestrator.resume_workflow_run(run_id)

        return res
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/approval/{approval_id}/reject")
async def reject_action(
    approval_id: str,
    request: Request,
    note: str = Body(default="", embed=True),
    principal: Principal = Depends(get_current_principal),
):
    await enforce_rate_limit(
        request,
        "approval.resolve",
        settings.rate_limit_approval_per_minute,
        subject=principal.subject,
    )
    await require_authorization(request, principal, "reject", "approval", approval_id)
    mgr = request.app.state.agent_manager
    try:
        res = await mgr.resolve_approval(approval_id, "rejected", note)

        # Automatically resume associated WorkflowRun if exists to signal rejection
        from sqlalchemy import select

        from cyber_team.db import async_session
        from cyber_team.db.models import ApprovalRequest, WorkflowRun

        async with async_session() as session:
            app_req = (
                await session.execute(
                    select(ApprovalRequest).where(ApprovalRequest.id == approval_id)
                )
            ).scalar_one_or_none()
            run_id = None
            if app_req and app_req.target_type == "workflow_run":
                run_id = app_req.target_id

            if not run_id:
                runs_res = await session.execute(
                    select(WorkflowRun).where(WorkflowRun.status == "waiting_approval")
                )
                for r in runs_res.scalars().all():
                    state_dict = r.state or {}
                    if approval_id in state_dict.values():
                        run_id = r.id
                        break

            if run_id:
                orchestrator = request.app.state.orchestrator
                await orchestrator.resume_workflow_run(run_id)

        return res
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
