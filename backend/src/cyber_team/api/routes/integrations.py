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
    communications = comms.integration_status()
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
    return {
        "environment": settings.environment,
        "communications": communications,
        "simulation_enabled": settings.communications_allow_simulation,
        "require_live_tool_executors": settings.require_live_tool_executors,
        "production_blocking_readiness": bool(blocking_reasons),
        "blocking_reasons": blocking_reasons,
        "last_validation_result": {
            "status": "blocked" if blocking_reasons else "ready",
            "checked_at": None,
        },
    }
