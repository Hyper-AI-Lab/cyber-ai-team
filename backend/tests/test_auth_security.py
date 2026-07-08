import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from cyber_team.api.routes.auth import router as auth_router
from cyber_team.api.security import (
    Principal,
    consume_websocket_ticket,
    create_owner_access_token,
    decode_token,
    get_current_principal,
    require_owner,
)
from cyber_team.config import settings


@pytest.fixture
def auth_client(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "test-secret-key-with-at-least-32-bytes")
    monkeypatch.setattr(settings, "owner_email", "owner@example.com")
    monkeypatch.setattr(settings, "owner_password", "correct-password")
    monkeypatch.setattr(settings, "owner_password_hash", "")

    app = FastAPI()
    app.include_router(auth_router, prefix="/api/auth")

    @app.get("/protected")
    async def protected(principal: Principal = Depends(get_current_principal)):
        return {"subject": principal.subject, "email": principal.email}

    @app.get("/owner-only")
    async def owner_only(principal: Principal = Depends(require_owner)):
        return {"role": principal.role}

    return TestClient(app)


def test_login_success_returns_access_and_refresh_tokens(auth_client):
    response = auth_client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "correct-password"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["token_type"] == "bearer"
    assert decode_token(data["access_token"], expected_type="access").role == "owner"
    assert decode_token(data["refresh_token"], expected_type="refresh").role == "owner"


def test_login_rejects_invalid_credentials(auth_client):
    response = auth_client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"


def test_login_rate_limit_returns_429(auth_client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 2)

    for _ in range(2):
        response = auth_client.post(
            "/api/auth/login",
            json={"email": "owner@example.com", "password": "wrong-password"},
        )
        assert response.status_code == 401

    response = auth_client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 429
    assert response.json()["detail"] == "Rate limit exceeded"
    assert response.headers["retry-after"]


def test_protected_route_requires_access_token(auth_client):
    response = auth_client.get("/protected")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token"


def test_refresh_rejects_access_token(auth_client):
    access_token = create_owner_access_token()

    response = auth_client.post(
        "/api/auth/refresh",
        json={"refresh_token": access_token},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid token type"


def test_owner_dependency_rejects_non_owner_principal(auth_client, monkeypatch):
    app = auth_client.app
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        subject="agent-1",
        email="agent@example.com",
        role="agent",
        token_type="access",
    )

    response = auth_client.get("/owner-only")

    assert response.status_code == 403
    assert response.json()["detail"] == "Owner access required"


def test_websocket_ticket_is_short_lived_and_single_use(auth_client):
    login_response = auth_client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "correct-password"},
    )
    access_token = login_response.json()["access_token"]

    ticket_response = auth_client.post(
        "/api/auth/ws-ticket",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert ticket_response.status_code == 200
    data = ticket_response.json()
    assert data["ticket"]
    assert data["expires_at"]

    principal = consume_websocket_ticket(data["ticket"])
    assert principal is not None
    assert principal.email == "owner@example.com"
    assert principal.token_type == "websocket"
    assert consume_websocket_ticket(data["ticket"]) is None


def test_websocket_ticket_rate_limit_returns_429(auth_client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_websocket_ticket_per_minute", 1)
    login_response = auth_client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "correct-password"},
    )
    access_token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    first_response = auth_client.post("/api/auth/ws-ticket", headers=headers)
    second_response = auth_client.post("/api/auth/ws-ticket", headers=headers)

    assert first_response.status_code == 200
    assert second_response.status_code == 429
