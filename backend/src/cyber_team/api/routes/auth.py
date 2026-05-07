"""Auth routes — login, token management."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from cyber_team.api.security import (
    create_owner_access_token,
    create_owner_refresh_token,
    decode_token,
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
    refresh_token: Optional[str] = None


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, request: Request):
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
