"""Auth routes — login, token management."""


from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from cyber_team.api.rate_limit import enforce_rate_limit
from cyber_team.api.security import (
    Principal,
    create_owner_access_token,
    create_owner_refresh_token,
    create_websocket_ticket,
    decode_token,
    get_current_principal,
    verify_owner_password,
)
from cyber_team.config import settings

router = APIRouter()


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    refresh_token: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


class WebSocketTicketResponse(BaseModel):
    ticket: str
    expires_at: datetime


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, request: Request):
    await enforce_rate_limit(
        request,
        "auth.login",
        settings.rate_limit_login_per_minute,
    )
    audit = getattr(request.app.state, "audit_service", None)
    password_matches = False
    if req.email == settings.owner_email:
        password_matches = await run_in_threadpool(verify_owner_password, req.password)
    if password_matches:
        if audit:
            await _record_login_evidence(audit, req.email, "success")
        return TokenResponse(
            access_token=create_owner_access_token(),
            refresh_token=create_owner_refresh_token(),
        )
    if audit:
        await _record_login_evidence(audit, req.email, "failed")
    metrics = getattr(request.app.state, "metrics_service", None)
    if metrics:
        metrics.record_auth_failure("login", "invalid_credentials")
    raise HTTPException(status_code=401, detail="Invalid credentials")


async def _record_login_evidence(audit, email: str, outcome: str) -> None:
    await audit.record_batch(
        [
            {
                "event_type": "auth.login",
                "actor": email,
                "actor_type": "user",
                "resource_type": "session",
                "action": "login",
                "outcome": outcome,
            },
            {
                "event_type": "control.evidence",
                "actor": email,
                "actor_type": "system",
                "resource_type": "control",
                "resource_id": "auth.login",
                "action": "soc2_access_control",
                "outcome": outcome,
                "metadata": {
                    "control_id": "auth.login",
                    "control_area": "soc2_access_control",
                    "evidence": {"event": "owner_login"},
                },
            },
        ]
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(req: RefreshRequest, request: Request):
    await enforce_rate_limit(
        request,
        "auth.refresh",
        settings.rate_limit_refresh_per_minute,
    )
    principal = decode_token(req.refresh_token, expected_type="refresh")
    audit = getattr(request.app.state, "audit_service", None)
    if audit:
        await audit.record(
            event_type="auth.refresh",
            actor=principal.email,
            actor_type="user",
            resource_type="session",
            action="refresh",
            outcome="success",
        )
        await audit.record_control_evidence(
            control_id="auth.refresh",
            control_area="soc2_access_control",
            actor=principal.email,
            outcome="success",
            evidence={"event": "token_refresh"},
        )
    return TokenResponse(
        access_token=create_owner_access_token(),
        refresh_token=create_owner_refresh_token(),
    )


@router.post("/ws-ticket", response_model=WebSocketTicketResponse)
async def create_ws_ticket(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await enforce_rate_limit(
        request,
        "auth.ws_ticket",
        settings.rate_limit_websocket_ticket_per_minute,
        subject=principal.subject,
    )
    ticket, expires_at = create_websocket_ticket(principal)
    audit = getattr(request.app.state, "audit_service", None)
    if audit:
        await audit.record(
            event_type="auth.websocket_ticket",
            actor=principal.email,
            actor_type="user",
            resource_type="session",
            action="create_websocket_ticket",
            outcome="success",
        )
        await audit.record_control_evidence(
            control_id="auth.websocket_ticket",
            control_area="soc2_access_control",
            actor=principal.email,
            outcome="success",
            evidence={"event": "websocket_ticket"},
        )
    return WebSocketTicketResponse(ticket=ticket, expires_at=expires_at)
