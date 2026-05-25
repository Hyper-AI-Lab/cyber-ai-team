"""Agent management routes."""


from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from cyber_team.agents.manager import AgentManager
from cyber_team.api.authorization import require_authorization
from cyber_team.api.security import Principal, get_current_principal

router = APIRouter()


class AgentCreate(BaseModel):
    role_family: str
    role_name: str
    instructions: str
    tools: list[str] = Field(default_factory=list)
    memory_namespace: str | None = None
    approval_policy: str = "auto"
    config: dict = Field(default_factory=dict)


class AgentUpdate(BaseModel):
    instructions: str | None = None
    tools: list[str] | None = None
    approval_policy: str | None = None
    status: str | None = None
    config: dict | None = None


class AgentResponse(BaseModel):
    id: str
    role_family: str
    role_name: str
    instructions: str
    tools: list[str]
    memory_namespace: str
    approval_policy: str
    status: str
    config: dict


@router.get("/", response_model=list[AgentResponse])
async def list_agents(request: Request, principal: Principal = Depends(get_current_principal)):
    await require_authorization(request, principal, "read", "agent")
    mgr: AgentManager = request.app.state.agent_manager
    return await mgr.list_agents()


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "agent", agent_id)
    mgr: AgentManager = request.app.state.agent_manager
    agent = await mgr.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return agent


@router.post("/", response_model=AgentResponse, status_code=201)
async def create_agent(
    data: AgentCreate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "create",
        "agent",
        context={
            "role_family": data.role_family,
            "role_name": data.role_name,
            "tools": data.tools,
        },
    )
    mgr: AgentManager = request.app.state.agent_manager
    return await mgr.create_agent(data)


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: str,
    data: AgentUpdate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "update", "agent", agent_id)
    mgr: AgentManager = request.app.state.agent_manager
    agent = await mgr.update_agent(agent_id, data)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return agent


@router.delete("/{agent_id}", status_code=204)
async def deactivate_agent(
    agent_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "delete", "agent", agent_id)
    mgr: AgentManager = request.app.state.agent_manager
    await mgr.deactivate_agent(agent_id)


@router.post("/{agent_id}/invoke")
async def invoke_agent(
    agent_id: str,
    request: Request,
    task: str = Body(..., embed=True),
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "invoke", "agent", agent_id)
    mgr: AgentManager = request.app.state.agent_manager
    try:
        result = await mgr.invoke_agent(agent_id, task)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"result": result}
