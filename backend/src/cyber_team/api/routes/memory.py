"""Memory management routes."""


from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from cyber_team.api.authorization import require_authorization
from cyber_team.api.security import Principal, get_current_principal
from cyber_team.memory.service import MemoryService

router = APIRouter()


class MemoryWrite(BaseModel):
    agent_id: str | None = None
    memory_type: str
    namespace: str
    content: str
    metadata: dict = Field(default_factory=dict)
    importance: float = 0.5


class MemoryQuery(BaseModel):
    query: str
    namespace: str | None = None
    agent_id: str | None = None
    memory_type: str | None = None
    limit: int = 10


class MemoryRecallItem(BaseModel):
    id: str
    agent_id: str | None = None
    memory_type: str
    namespace: str
    content: str
    metadata: dict = Field(default_factory=dict)
    importance: float
    score: float | None = None


class MemoryResponse(BaseModel):
    id: str
    agent_id: str | None
    memory_type: str
    namespace: str
    content: str
    metadata: dict = Field(default_factory=dict)
    importance: float


class MemoryTraceResponse(BaseModel):
    id: str
    invocation_id: str
    agent_id: str | None = None
    conversation_id: str | None = None
    source_type: str
    task_excerpt: str
    memory_namespace: str | None = None
    read_policy: dict = Field(default_factory=dict)
    write_policy: dict = Field(default_factory=dict)
    recalled_memory_ids: list[str] = Field(default_factory=list)
    written_memory_ids: list[str] = Field(default_factory=list)
    recall_count: int
    write_count: int
    errors: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    created_at: str


@router.post("/remember", response_model=MemoryResponse)
async def remember(
    data: MemoryWrite,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "write",
        "memory_namespace",
        data.namespace,
        context={"agent_id": data.agent_id, "memory_type": data.memory_type},
    )
    svc: MemoryService = request.app.state.memory_service
    return await svc.remember(data)


@router.post("/recall", response_model=list[MemoryRecallItem])
async def recall(
    data: MemoryQuery,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "memory_namespace",
        data.namespace,
        context={"agent_id": data.agent_id, "memory_type": data.memory_type},
    )
    svc: MemoryService = request.app.state.memory_service
    return await svc.recall(data)


@router.get("/entity/{entity_id}")
async def get_entity_profile(
    entity_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "entity_profile", entity_id)
    svc: MemoryService = request.app.state.memory_service
    return await svc.get_entity_profile(entity_id)


@router.get("/agent/{agent_id}")
async def get_agent_memory(
    agent_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "agent_memory", agent_id)
    svc: MemoryService = request.app.state.memory_service
    return await svc.get_agent_memory(agent_id)


@router.get("/traces", response_model=list[MemoryTraceResponse])
async def list_memory_traces(
    request: Request,
    agent_id: str | None = None,
    invocation_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "memory_trace",
        invocation_id,
        context={"agent_id": agent_id, "limit": limit},
    )
    svc: MemoryService = request.app.state.memory_service
    return await svc.list_memory_traces(
        agent_id=agent_id,
        invocation_id=invocation_id,
        limit=limit,
    )


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "delete", "memory", memory_id)
    svc: MemoryService = request.app.state.memory_service
    await svc.delete_memory(memory_id)
