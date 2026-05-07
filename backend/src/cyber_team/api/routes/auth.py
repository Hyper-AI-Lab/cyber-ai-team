"""Auth routes — login, token management."""

from fastapi import APIRouter, HTTPException
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
async def login(req: LoginRequest):
    if req.email == settings.owner_email and verify_owner_password(req.password):
        return TokenResponse(
            access_token=create_owner_access_token(),
            refresh_token=create_owner_refresh_token(),
        )
    raise HTTPException(status_code=401, detail="Invalid credentials")


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(req: RefreshRequest):
    decode_token(req.refresh_token, expected_type="refresh")
    return TokenResponse(
        access_token=create_owner_access_token(),
        refresh_token=create_owner_refresh_token(),
    )
