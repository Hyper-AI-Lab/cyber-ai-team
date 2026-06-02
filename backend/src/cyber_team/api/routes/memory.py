"""Memory management routes."""


from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import status as http_status
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


class MemoryStewardFindingResponse(BaseModel):
    id: str
    finding_type: str
    severity: str
    status: str
    agent_id: str | None = None
    memory_namespace: str | None = None
    company_namespace: str | None = None
    title: str
    description: str
    recommendation: str
    trace_ids: list[str] = Field(default_factory=list)
    evidence: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)
    available_actions: list[dict] = Field(default_factory=list)
    created_at: str
    updated_at: str
    resolved_at: str | None = None


class MemoryStewardRunResponse(BaseModel):
    reviewed_at: str
    actor: str
    traces_reviewed: int
    findings_created: int
    findings_updated: int
    findings: list[MemoryStewardFindingResponse] = Field(default_factory=list)
    remediation_plan: dict | None = None


class MemoryStewardResolve(BaseModel):
    status: Literal["resolved", "acknowledged", "open"] = "resolved"
    note: str = ""


class MemoryStewardActionRequest(BaseModel):
    action_type: Literal["seed_memory", "report_role_gap"]
    params: dict = Field(default_factory=dict)


class MemoryStewardActionResponse(BaseModel):
    action: dict
    finding: MemoryStewardFindingResponse


class MemoryStewardPlanRequest(BaseModel):
    apply_safe_actions: bool | None = None
    request_approvals: bool | None = None
    limit: int = Field(default=100, ge=1, le=200)


class MemoryStewardPlanResponse(BaseModel):
    reviewed_at: str
    actor: str
    findings_reviewed: int
    plans_created: int
    actions_applied: int
    approvals_requested: int
    approvals_pending: int
    already_applied: int
    blocked: int
    plans: list[dict] = Field(default_factory=list)


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


@router.post("/steward/run", response_model=MemoryStewardRunResponse)
async def run_memory_steward(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "execute",
        "memory_steward",
        "run",
    )
    return await request.app.state.memory_steward_service.run_once(
        actor=principal.email,
    )


@router.post("/steward/plan", response_model=MemoryStewardPlanResponse)
async def plan_memory_steward_remediations(
    data: MemoryStewardPlanRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "plan",
        "memory_steward",
        "remediations",
        context={
            "apply_safe_actions": data.apply_safe_actions,
            "request_approvals": data.request_approvals,
            "limit": data.limit,
        },
    )
    return await request.app.state.memory_steward_service.plan_remediations(
        actor=principal.email,
        apply_safe_actions=data.apply_safe_actions,
        request_approvals=data.request_approvals,
        limit=data.limit,
    )


@router.get("/steward/findings", response_model=list[MemoryStewardFindingResponse])
async def list_memory_steward_findings(
    request: Request,
    status: str | None = "open",
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "memory_steward_finding",
        status,
        context={"limit": limit},
    )
    return await request.app.state.memory_steward_service.list_findings(
        status=status,
        limit=limit,
    )


@router.post(
    "/steward/findings/{finding_id}/resolve",
    response_model=MemoryStewardFindingResponse,
)
async def resolve_memory_steward_finding(
    finding_id: str,
    data: MemoryStewardResolve,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        data.status,
        "memory_steward_finding",
        finding_id,
    )
    finding = await request.app.state.memory_steward_service.resolve_finding(
        finding_id,
        status=data.status,
        note=data.note,
        actor=principal.email,
    )
    if finding is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Memory steward finding not found",
        )
    return finding


@router.post(
    "/steward/findings/{finding_id}/actions",
    response_model=MemoryStewardActionResponse,
)
async def execute_memory_steward_action(
    finding_id: str,
    data: MemoryStewardActionRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "execute",
        "memory_steward_finding",
        finding_id,
        context={"action_type": data.action_type},
    )
    try:
        result = await request.app.state.memory_steward_service.execute_action(
            finding_id,
            action_type=data.action_type,
            params=data.params,
            actor=principal.email,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Memory steward finding not found",
        )
    return result


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "delete", "memory", memory_id)
    svc: MemoryService = request.app.state.memory_service
    await svc.delete_memory(memory_id)
