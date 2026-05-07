"""Role catalog and role factory routes."""

from fastapi import APIRouter, Request, HTTPException, Body
from pydantic import BaseModel, Field
from typing import Optional, Any

router = APIRouter()


class RoleManifestCreate(BaseModel):
    family: str
    name: str
    description: str
    instructions_template: str
    default_tools: list[str] = Field(default_factory=list)
    memory_namespace: Optional[str] = None
    approval_policy: str = "auto"
    success_metrics: Any = Field(default_factory=list)
    is_core: bool = True
    config: dict = Field(default_factory=dict)


class RoleManifestResponse(BaseModel):
    id: str
    family: str
    name: str
    description: str
    instructions_template: str
    default_tools: list[str]
    memory_namespace: str
    approval_policy: str
    success_metrics: Any
    is_core: bool
    config: dict


@router.get("/catalog", response_model=list[RoleManifestResponse])
async def list_role_catalog(request: Request):
    mgr = request.app.state.agent_manager
    return await mgr.list_role_manifests()


@router.get("/catalog/{manifest_id}", response_model=RoleManifestResponse)
async def get_role_manifest(manifest_id: str, request: Request):
    mgr = request.app.state.agent_manager
    manifest = await mgr.get_role_manifest(manifest_id)
    if not manifest:
        raise HTTPException(404, "Role manifest not found")
    return manifest


@router.post("/catalog", response_model=RoleManifestResponse, status_code=201)
async def create_role_manifest(data: RoleManifestCreate, request: Request):
    mgr = request.app.state.agent_manager
    return await mgr.create_role_manifest(data)


@router.post("/instantiate/{manifest_id}")
async def instantiate_role(manifest_id: str, request: Request, overrides: dict = Body(default_factory=dict)):
    mgr = request.app.state.agent_manager
    return await mgr.instantiate_role(manifest_id, overrides)


@router.post("/company-builder")
async def run_company_builder(request: Request, company_profile: dict = Body(...)):
    mgr = request.app.state.agent_manager
    result = await mgr.run_company_builder(company_profile)
    return result


@router.post("/role-gap")
async def propose_new_role(request: Request, gap_description: str = Body(..., embed=True)):
    mgr = request.app.state.agent_manager
    result = await mgr.propose_new_role(gap_description)
    return result
