"""Tool registry routes — list and execute tools."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from typing import Optional
from cyber_team.api.authorization import require_authorization
from cyber_team.api.security import Principal, get_current_principal

router = APIRouter()


class ToolExecuteRequest(BaseModel):
    tool_name: str
    params: dict = Field(default_factory=dict)


@router.get("/")
async def list_tools(
    request: Request,
    category: Optional[str] = None,
    principal: Principal = Depends(get_current_principal),
):
    registry = request.app.state.tool_registry
    allowed_tools = await _allowed_tools_for_principal(request, principal)
    await require_authorization(
        request,
        principal,
        "read",
        "tool",
        context={"category": category, "allowed_tools": allowed_tools or []},
    )
    return registry.list_tool_contracts(category=category, allowed_tools=allowed_tools)


@router.post("/execute")
async def execute_tool(
    data: ToolExecuteRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    allowed_tools = await _allowed_tools_for_principal(request, principal)
    await require_authorization(
        request,
        principal,
        "execute",
        "tool",
        data.tool_name,
        context={"allowed_tools": allowed_tools or []},
    )
    registry = request.app.state.tool_registry
    params = dict(data.params)
    params["_actor"] = principal.email
    params["_actor_type"] = principal.role
    if principal.role == "agent":
        params["_agent_id"] = principal.subject
    result = await registry.execute(data.tool_name, params)
    return result.model_dump()


async def _allowed_tools_for_principal(
    request: Request,
    principal: Principal,
) -> Optional[list[str]]:
    if principal.role != "agent":
        return None
    agent = await request.app.state.agent_manager.get_agent(principal.subject)
    return agent["tools"] if agent else []
