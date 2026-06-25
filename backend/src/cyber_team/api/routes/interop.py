"""Interoperability adapter routes."""

from fastapi import APIRouter, Depends, Request

from cyber_team.api.authorization import require_authorization
from cyber_team.api.security import Principal, get_current_principal

router = APIRouter()


@router.get("/summary")
async def interop_summary(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "interop")
    service = request.app.state.interop_service
    return await service.summary()


@router.get("/mcp/tools")
async def mcp_tool_catalog(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "interop_mcp_tools")
    service = request.app.state.interop_service
    return service.mcp_tool_catalog()


@router.get("/a2a/agent-cards")
async def a2a_agent_cards(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "interop_a2a_agent_cards")
    service = request.app.state.interop_service
    return await service.a2a_agent_cards()
