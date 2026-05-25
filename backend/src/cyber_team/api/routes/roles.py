"""Role catalog and role factory routes."""

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from cyber_team.api.authorization import require_authorization
from cyber_team.api.security import Principal, get_current_principal

router = APIRouter()


class RoleManifestCreate(BaseModel):
    family: str
    name: str
    description: str
    instructions_template: str
    default_tools: list[str] = Field(default_factory=list)
    memory_namespace: str | None = None
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
async def list_role_catalog(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "role_manifest")
    mgr = request.app.state.agent_manager
    return await mgr.list_role_manifests()


@router.get("/catalog/{manifest_id}", response_model=RoleManifestResponse)
async def get_role_manifest(
    manifest_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "role_manifest", manifest_id)
    mgr = request.app.state.agent_manager
    manifest = await mgr.get_role_manifest(manifest_id)
    if not manifest:
        raise HTTPException(404, "Role manifest not found")
    return manifest


@router.post("/catalog", response_model=RoleManifestResponse, status_code=201)
async def create_role_manifest(
    data: RoleManifestCreate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "create",
        "role_manifest",
        context={
            "family": data.family,
            "name": data.name,
            "default_tools": data.default_tools,
        },
    )
    mgr = request.app.state.agent_manager
    return await mgr.create_role_manifest(data)


@router.post("/provision", response_model=RoleManifestResponse, status_code=201)
async def provision_role(
    data: RoleManifestCreate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "create",
        "role_manifest",
        context={
            "family": data.family,
            "name": data.name,
            "default_tools": data.default_tools,
        },
    )
    from cyber_team.agents.manager import slug_id
    mgr = request.app.state.agent_manager

    # Validate duplicate manifest name
    manifest_id = slug_id(data.name)
    existing = await mgr.get_role_manifest(manifest_id)
    if existing:
        raise HTTPException(status_code=400, detail=f"Role manifest '{data.name}' already exists.")

    # 1. Create role manifest record in PostgreSQL
    manifest = await mgr.create_role_manifest(data)

    # 2. Instantiate agent instantly
    await mgr.instantiate_role(manifest["id"])

    return manifest


@router.post("/instantiate/{manifest_id}")
async def instantiate_role(
    manifest_id: str,
    request: Request,
    overrides: dict = Body(default_factory=dict),
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "instantiate", "role_manifest", manifest_id)
    mgr = request.app.state.agent_manager
    return await mgr.instantiate_role(manifest_id, overrides)


@router.post("/company-builder")
async def run_company_builder(
    request: Request,
    company_profile: dict = Body(...),
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "run", "company_builder")
    mgr = request.app.state.agent_manager
    result = await mgr.run_company_builder(company_profile)
    return result


@router.post("/role-gap")
async def propose_new_role(
    request: Request,
    gap_description: str = Body(..., embed=True),
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "propose", "role_manifest")
    mgr = request.app.state.agent_manager
    result = await mgr.propose_new_role(gap_description)
    return result
