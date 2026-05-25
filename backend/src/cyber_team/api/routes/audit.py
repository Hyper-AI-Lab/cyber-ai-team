"""Audit routes — structured event trail for governance."""


from fastapi import APIRouter, Depends, Query, Request

from cyber_team.api.authorization import require_authorization
from cyber_team.api.security import Principal, get_current_principal

router = APIRouter()


@router.get("/events")
async def list_audit_events(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    event_type: str | None = None,
    actor: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "audit_event",
        context={
            "event_type": event_type,
            "actor": actor,
            "resource_type": resource_type,
            "resource_id": resource_id,
        },
    )
    audit = request.app.state.audit_service
    return await audit.list_events(
        limit=limit,
        event_type=event_type,
        actor=actor,
        resource_type=resource_type,
        resource_id=resource_id,
    )
