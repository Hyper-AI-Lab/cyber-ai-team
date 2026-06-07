"""Integration status routes."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from cyber_team.api.authorization import require_authorization
from cyber_team.api.security import Principal, get_current_principal
from cyber_team.config import settings

router = APIRouter()


class IntegrationValidationRequest(BaseModel):
    provider: str = Field(default="smtp", min_length=1, max_length=64)


@router.get("/status")
async def integration_status(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "integration_status")
    comms = request.app.state.comms_gateway
    communications = comms.integration_status()
    inbound_email = getattr(request.app.state, "inbound_email_service", None)
    if inbound_email:
        communications = [*communications, inbound_email.integration_status()]
    blocking_reasons = []
    if settings.require_live_tool_executors:
        blocking_reasons = [
            {
                "channel": item.get("channel"),
                "provider": item.get("provider"),
                "mode": item.get("mode"),
                "reason": item.get("detail"),
            }
            for item in communications
            if item.get("mode") != "live"
        ]
    last_validation = comms.last_validation_result()
    if inbound_email and inbound_email.last_validation_result():
        last_validation = inbound_email.last_validation_result()
    return {
        "environment": settings.environment,
        "communications": communications,
        "simulation_enabled": settings.communications_allow_simulation,
        "require_live_tool_executors": settings.require_live_tool_executors,
        "production_blocking_readiness": bool(blocking_reasons),
        "blocking_reasons": blocking_reasons,
        "last_validation_result": last_validation or {
            "status": "blocked" if blocking_reasons else "ready",
            "checked_at": None,
            "provider": "all",
            "results": [],
        },
    }


@router.post("/validate")
async def validate_integration(
    data: IntegrationValidationRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "validate",
        "integration",
        data.provider,
        context=data.model_dump(),
    )
    inbound_email = getattr(request.app.state, "inbound_email_service", None)
    if inbound_email and data.provider.lower() in {"imap", "inbound_email"}:
        result = await inbound_email.validate()
    else:
        result = await request.app.state.comms_gateway.validate_integrations(data.provider)
    audit = getattr(request.app.state, "audit_service", None)
    if audit:
        await audit.record_control_evidence(
            control_id="integration.validation",
            control_area="soc2_availability",
            actor=principal.email,
            outcome=result["status"],
            evidence={
                "environment": settings.environment,
                "provider": result["provider"],
                "status": result["status"],
                "checked_at": result["checked_at"],
                "results": result["results"],
            },
        )
    return result
