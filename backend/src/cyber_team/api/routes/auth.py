"""Auth routes — login, token management."""


from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

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
    if req.email == settings.owner_email and verify_owner_password(req.password):
        if audit:
            await audit.record(
                event_type="auth.login",
                actor=req.email,
                actor_type="user",
                resource_type="session",
                action="login",
                outcome="success",
            )
        return TokenResponse(
            access_token=create_owner_access_token(),
            refresh_token=create_owner_refresh_token(),
        )
    if audit:
        await audit.record(
            event_type="auth.login",
            actor=req.email,
            actor_type="user",
            resource_type="session",
            action="login",
            outcome="failed",
        )
    raise HTTPException(status_code=401, detail="Invalid credentials")


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
    return WebSocketTicketResponse(ticket=ticket, expires_at=expires_at)
