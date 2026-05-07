"""Tool registry routes — list and execute tools."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from typing import Optional

router = APIRouter()


class ToolExecuteRequest(BaseModel):
    tool_name: str
    params: dict = Field(default_factory=dict)


@router.get("/")
async def list_tools(request: Request, category: Optional[str] = None):
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
async def execute_tool(data: ToolExecuteRequest, request: Request):
    registry = request.app.state.tool_registry
    result = await registry.execute(data.tool_name, data.params)
    return result.model_dump()
