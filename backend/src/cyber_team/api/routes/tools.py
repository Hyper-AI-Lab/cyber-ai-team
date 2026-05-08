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
    await require_authorization(request, principal, "read", "tool", context={"category": category})
    registry = request.app.state.tool_registry
    tools = registry.list_tools(category)
    return [
        {
            "name": t.name,
            "description": t.description,
            "parameters": [p.model_dump() for p in t.parameters],
            "category": t.category,
            "requires_approval": t.requires_approval,
        }
        for t in tools
    ]


@router.post("/execute")
async def execute_tool(
    data: ToolExecuteRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "execute", "tool", data.tool_name)
    registry = request.app.state.tool_registry
    params = dict(data.params)
    params["_actor"] = principal.email
    params["_actor_type"] = principal.role
    result = await registry.execute(data.tool_name, params)
    return result.model_dump()
