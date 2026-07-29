"""Evidence-backed company intelligence APIs."""

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from cyber_team.api.authorization import require_authorization
from cyber_team.api.security import Principal, get_current_principal

router = APIRouter()
webhook_router = APIRouter()


class DiscoveryRequest(BaseModel):
    acquire: bool = True
    activate_if_ready: bool = True
    company_namespace: str | None = Field(default=None, max_length=200)


class OwnerClaimRevisionRequest(BaseModel):
    value: dict[str, Any]
    reason: str = Field(..., min_length=1, max_length=2000)


class CompanyResearchRequest(BaseModel):
    query: str = Field(..., min_length=3, max_length=500)


@router.get("/sources")
async def list_company_sources(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "company_source")
    return await request.app.state.company_intelligence_service.list_sources()


@router.post("/sources/acquire")
async def acquire_company_evidence(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "acquire", "company_evidence")
    result = await request.app.state.company_intelligence_service.acquire_available_evidence()
    await _signal_autonomy(request, "company-evidence-acquired")
    return result


@router.post("/research")
async def research_company_evidence(
    data: CompanyResearchRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "research", "company_evidence")
    result = await request.app.state.company_intelligence_service.research(data.query)
    if result.get("id"):
        await _signal_autonomy(request, result["id"])
    return result


@router.get("/signals")
async def list_company_signals(
    request: Request,
    status: str | None = None,
    limit: int = 100,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "company_signal",
        context={"status": status, "limit": limit},
    )
    return await request.app.state.company_intelligence_service.list_signals(
        status=status,
        limit=limit,
    )


@router.get("/evidence")
async def list_company_evidence(
    request: Request,
    limit: int = 100,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "company_evidence")
    return await request.app.state.company_intelligence_service.list_evidence(limit=limit)


@router.get("/claims")
async def list_company_claims(
    request: Request,
    state: str | None = None,
    active_only: bool = True,
    limit: int = 200,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "company_claim")
    return await request.app.state.company_intelligence_service.list_claims(
        state=state,
        active_only=active_only,
        limit=limit,
    )


@router.put("/claims/{claim_id}")
async def revise_company_claim(
    claim_id: str,
    data: OwnerClaimRevisionRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "revise",
        "company_claim",
        claim_id,
        context={"owner_locked": True},
    )
    service = request.app.state.company_intelligence_service
    result = await service.create_owner_locked_claim_revision(
        claim_id,
        value=data.value,
        actor=principal.email,
        reason=data.reason,
    )
    if not result:
        raise HTTPException(404, "Company claim not found")
    await _signal_autonomy(request, result["id"])
    return result


@router.post("/discover")
async def discover_company_model(
    data: DiscoveryRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "discover",
        "company_model",
        context=data.model_dump(),
    )
    return await request.app.state.company_intelligence_service.discover_company_model(
        company_namespace=data.company_namespace,
        acquire=data.acquire,
        activate_if_ready=data.activate_if_ready,
        actor=principal.email,
    )


@router.get("/model")
async def get_company_model(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "company_model")
    result = await request.app.state.company_intelligence_service.latest_model()
    if not result:
        raise HTTPException(404, "No company model revision exists")
    return result


@router.get("/model/revisions")
async def list_company_model_revisions(
    request: Request,
    limit: int = 50,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "company_model_revision")
    return await request.app.state.company_intelligence_service.list_model_revisions(limit=limit)


@webhook_router.post("/erpnext")
async def receive_erpnext_webhook(
    request: Request,
    signature: str | None = Header(default=None, alias="X-Frappe-Webhook-Signature"),
):
    body = await request.body()
    service = request.app.state.company_intelligence_service
    if not service.verify_erpnext_webhook(body, signature):
        raise HTTPException(401, "Invalid ERPNext webhook signature")
    try:
        result = await service.ingest_erpnext_webhook(body)
        await _signal_autonomy(request, result.get("signal_id") or "erpnext-webhook")
        return result
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(400, "Invalid ERPNext webhook payload") from exc


async def _signal_autonomy(request: Request, event_id: str) -> None:
    controller = getattr(request.app.state, "temporal_autonomy_controller", None)
    if not controller:
        return
    try:
        await controller.signal(event_id)
    except Exception:  # noqa: BLE001 - durable polling reconciles missed wake-ups.
        return
