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
