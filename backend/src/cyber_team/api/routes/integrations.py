"""Integration status routes."""

from fastapi import APIRouter, Depends, Request

from cyber_team.api.authorization import require_authorization
from cyber_team.api.security import Principal, get_current_principal
from cyber_team.config import settings

router = APIRouter()


@router.get("/status")
async def integration_status(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "integration_status")
    comms = request.app.state.comms_gateway
    return {
        "environment": settings.environment,
        "communications": comms.integration_status(),
        "simulation_enabled": settings.communications_allow_simulation,
    }
