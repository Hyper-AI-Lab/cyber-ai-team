"""Workflow management routes."""

from fastapi import APIRouter, HTTPException, Request, Body
from pydantic import BaseModel, Field
from typing import Optional

router = APIRouter()


class WorkflowCreate(BaseModel):
    name: str
    description: Optional[str] = None
    graph_definition: dict
    trigger_type: str = "manual"
    trigger_config: dict = Field(default_factory=dict)


class WorkflowResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    graph_definition: dict
    status: str
    trigger_type: str
    trigger_config: dict


class WorkflowRunResponse(BaseModel):
    id: str
    workflow_id: str
    status: str
    current_node: Optional[str]
    state: dict
    result: Optional[dict]
    error: Optional[str]


@router.get("/", response_model=list[WorkflowResponse])
async def list_workflows(request: Request):
    orchestrator = request.app.state.orchestrator
    return await orchestrator.list_workflows()


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(workflow_id: str, request: Request):
    orchestrator = request.app.state.orchestrator
    wf = await orchestrator.get_workflow(workflow_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return wf


@router.post("/", response_model=WorkflowResponse, status_code=201)
async def create_workflow(data: WorkflowCreate, request: Request):
    orchestrator = request.app.state.orchestrator
    return await orchestrator.create_workflow(data)


@router.post("/{workflow_id}/run", response_model=WorkflowRunResponse)
async def run_workflow(workflow_id: str, request: Request, input_data: dict = Body(default_factory=dict)):
    orchestrator = request.app.state.orchestrator
    return await orchestrator.run_workflow(workflow_id, input_data)


@router.get("/{workflow_id}/runs", response_model=list[WorkflowRunResponse])
async def list_workflow_runs(workflow_id: str, request: Request):
    orchestrator = request.app.state.orchestrator
    return await orchestrator.list_workflow_runs(workflow_id)


@router.get("/runs/{run_id}", response_model=WorkflowRunResponse)
async def get_workflow_run(run_id: str, request: Request):
    orchestrator = request.app.state.orchestrator
    run = await orchestrator.get_workflow_run(run_id)
    if not run:
        raise HTTPException(404, "Workflow run not found")
    return run


@router.post("/runs/{run_id}/resume", response_model=WorkflowRunResponse)
async def resume_workflow_run(run_id: str, request: Request):
    orchestrator = request.app.state.orchestrator
    try:
        return await orchestrator.resume_workflow_run(run_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
