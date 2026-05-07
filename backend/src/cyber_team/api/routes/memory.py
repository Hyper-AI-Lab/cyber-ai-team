"""Memory management routes."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from typing import Optional

router = APIRouter()


class MemoryWrite(BaseModel):
    agent_id: Optional[str] = None
    memory_type: str
    namespace: str
    content: str
    metadata: dict = Field(default_factory=dict)
    importance: float = 0.5


class MemoryQuery(BaseModel):
    query: str
    namespace: Optional[str] = None
    agent_id: Optional[str] = None
    memory_type: Optional[str] = None
    limit: int = 10


class MemoryRecallItem(BaseModel):
    id: str
    agent_id: Optional[str] = None
    memory_type: str
    namespace: str
    content: str
    metadata: dict = Field(default_factory=dict)
    importance: float
    score: Optional[float] = None


class MemoryResponse(BaseModel):
    id: str
    agent_id: Optional[str]
    memory_type: str
    namespace: str
    content: str
    metadata: dict = Field(default_factory=dict)
    importance: float


@router.post("/remember", response_model=MemoryResponse)
async def remember(data: MemoryWrite, request: Request):
    svc: "MemoryService" = request.app.state.memory_service
    return await svc.remember(data)


@router.post("/recall", response_model=list[MemoryRecallItem])
async def recall(data: MemoryQuery, request: Request):
    svc: "MemoryService" = request.app.state.memory_service
    return await svc.recall(data)


@router.get("/entity/{entity_id}")
async def get_entity_profile(entity_id: str, request: Request):
    svc: "MemoryService" = request.app.state.memory_service
    return await svc.get_entity_profile(entity_id)


@router.get("/agent/{agent_id}")
async def get_agent_memory(agent_id: str, request: Request):
    svc: "MemoryService" = request.app.state.memory_service
    return await svc.get_agent_memory(agent_id)


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(memory_id: str, request: Request):
    svc: "MemoryService" = request.app.state.memory_service
    await svc.delete_memory(memory_id)
