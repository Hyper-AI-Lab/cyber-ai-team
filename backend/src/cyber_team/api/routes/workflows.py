"""Workflow management routes."""


from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from cyber_team.api.authorization import require_authorization
from cyber_team.api.security import Principal, get_current_principal

router = APIRouter()


class WorkflowCreate(BaseModel):
    name: str
    description: str | None = None
    graph_definition: dict
    trigger_type: str = "manual"
    trigger_config: dict = Field(default_factory=dict)


class WorkflowResponse(BaseModel):
    id: str
    name: str
    description: str | None
    graph_definition: dict
    status: str
    trigger_type: str
    trigger_config: dict


class WorkflowRunResponse(BaseModel):
    id: str
    workflow_id: str
    status: str
    current_node: str | None
    state: dict
    result: dict | None
    error: str | None


class WorkflowTemplateInstantiateRequest(BaseModel):
    pass


class WorkflowIntentGenerateRequest(BaseModel):
    snapshot_id: str | None = None
    limit: int = Field(default=75, ge=1, le=200)
    instantiate_low_risk: bool = False


class WorkflowIntentResolveRequest(BaseModel):
    status: str = Field(default="dismissed", pattern="^(dismissed|resolved)$")
    note: str = ""


@router.get("/", response_model=list[WorkflowResponse])
async def list_workflows(request: Request, principal: Principal = Depends(get_current_principal)):
    await require_authorization(request, principal, "read", "workflow")
    orchestrator = request.app.state.orchestrator
    return await orchestrator.list_workflows()


@router.get("/templates")
async def list_workflow_templates(
    request: Request,
    status: str | None = "active",
    category: str | None = None,
    is_core: bool | None = None,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "workflow_template",
        context={"status": status, "category": category, "is_core": is_core},
    )
    service = request.app.state.workflow_template_service
    return await service.list_templates(status=status, category=category, is_core=is_core)


@router.get("/templates/{template_id}")
async def get_workflow_template(
    template_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "workflow_template", template_id)
    service = request.app.state.workflow_template_service
    template = await service.get_template(template_id)
    if not template:
        raise HTTPException(404, "Workflow template not found")
    return template


@router.post("/templates/{template_id}/instantiate", response_model=WorkflowResponse)
async def instantiate_workflow_template(
    template_id: str,
    request: Request,
    _data: WorkflowTemplateInstantiateRequest | None = None,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "instantiate",
        "workflow_template",
        template_id,
    )
    service = request.app.state.workflow_template_service
    try:
        return await service.instantiate_template(template_id, actor=principal.email)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/intents")
async def list_workflow_intents(
    request: Request,
    status: str | None = "proposed,instantiated,blocked",
    category: str | None = None,
    source_type: str | None = None,
    company_namespace: str | None = None,
    readiness_status: str | None = None,
    limit: int = 100,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "workflow_intent",
        context={
            "status": status,
            "category": category,
            "source_type": source_type,
            "company_namespace": company_namespace,
            "readiness_status": readiness_status,
        },
    )
    service = request.app.state.workflow_intent_service
    return await service.list_intents(
        status=status,
        category=category,
        source_type=source_type,
        company_namespace=company_namespace,
        readiness_status=readiness_status,
        limit=limit,
    )


@router.post("/intents/generate")
async def generate_workflow_intents(
    data: WorkflowIntentGenerateRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "generate",
        "workflow_intent",
        context={
            "snapshot_id": data.snapshot_id,
            "instantiate_low_risk": data.instantiate_low_risk,
        },
    )
    service = request.app.state.workflow_intent_service
    return await service.generate_from_company_context(
        snapshot_id=data.snapshot_id,
        actor=principal.email,
        limit=data.limit,
        instantiate_low_risk=data.instantiate_low_risk,
    )


@router.get("/intents/{intent_id}")
async def get_workflow_intent(
    intent_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "workflow_intent", intent_id)
    service = request.app.state.workflow_intent_service
    intent = await service.get_intent(intent_id)
    if not intent:
        raise HTTPException(404, "Workflow intent not found")
    return intent


@router.post("/intents/{intent_id}/instantiate", response_model=WorkflowResponse)
async def instantiate_workflow_intent(
    intent_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "instantiate",
        "workflow_intent",
        intent_id,
    )
    service = request.app.state.workflow_intent_service
    try:
        return await service.instantiate_intent(intent_id, actor=principal.email)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/intents/{intent_id}/resolve")
async def resolve_workflow_intent(
    intent_id: str,
    data: WorkflowIntentResolveRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "resolve",
        "workflow_intent",
        intent_id,
    )
    service = request.app.state.workflow_intent_service
    try:
        return await service.resolve_intent(
            intent_id,
            status=data.status,
            note=data.note,
            actor=principal.email,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "workflow", workflow_id)
    orchestrator = request.app.state.orchestrator
    wf = await orchestrator.get_workflow(workflow_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return wf


@router.post("/", response_model=WorkflowResponse, status_code=201)
async def create_workflow(
    data: WorkflowCreate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "create",
        "workflow",
        context={"name": data.name, "trigger_type": data.trigger_type},
    )
    orchestrator = request.app.state.orchestrator
    return await orchestrator.create_workflow(data)


@router.post("/{workflow_id}/run", response_model=WorkflowRunResponse)
async def run_workflow(
    workflow_id: str,
    request: Request,
    input_data: dict = Body(default_factory=dict),
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "run", "workflow", workflow_id)
    orchestrator = request.app.state.orchestrator
    try:
        return await orchestrator.run_workflow(workflow_id, input_data)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{workflow_id}/runs", response_model=list[WorkflowRunResponse])
async def list_workflow_runs(
    workflow_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "workflow_run",
        context={"workflow_id": workflow_id},
    )
    orchestrator = request.app.state.orchestrator
    return await orchestrator.list_workflow_runs(workflow_id)


@router.get("/runs/{run_id}", response_model=WorkflowRunResponse)
async def get_workflow_run(
    run_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "workflow_run", run_id)
    orchestrator = request.app.state.orchestrator
    run = await orchestrator.get_workflow_run(run_id)
    if not run:
        raise HTTPException(404, "Workflow run not found")
    return run


@router.post("/runs/{run_id}/resume", response_model=WorkflowRunResponse)
async def resume_workflow_run(
    run_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "resume", "workflow_run", run_id)
    orchestrator = request.app.state.orchestrator
    try:
        return await orchestrator.resume_workflow_run(run_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
