"""Audit routes — structured event trail for governance."""

from fastapi import APIRouter, Query, Request
from typing import Optional

router = APIRouter()


@router.get("/events")
async def list_audit_events(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    event_type: Optional[str] = None,
    actor: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
):
    audit = request.app.state.audit_service
    return await audit.list_events(
        limit=limit,
        event_type=event_type,
        actor=actor,
        resource_type=resource_type,
        resource_id=resource_id,
    )
