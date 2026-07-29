from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cyber_team.api.routes import company
from cyber_team.api.security import Principal, get_current_principal


def test_company_intelligence_routes(monkeypatch):
    app = FastAPI()
    app.include_router(company.router, prefix="/api/company")
    service = AsyncMock()
    service.list_sources.return_value = [{"id": "src-1"}]
    service.list_claims.return_value = [{"id": "claim-1"}]
    service.latest_model.return_value = {"id": "model-1", "status": "active"}
    service.discover_company_model.return_value = {"id": "model-2", "status": "draft"}
    service.create_owner_locked_claim_revision.return_value = {
        "id": "claim-2",
        "owner_locked": True,
    }
    app.state.company_intelligence_service = service

    async def owner():
        return Principal(
            subject="owner",
            email="owner@example.com",
            role="owner",
            token_type="access",
        )

    async def allow(*args, **kwargs):
        return None

    app.dependency_overrides[get_current_principal] = owner
    monkeypatch.setattr(company, "require_authorization", allow)
    client = TestClient(app)

    assert client.get("/api/company/sources").json()[0]["id"] == "src-1"
    assert client.get("/api/company/claims").json()[0]["id"] == "claim-1"
    assert client.get("/api/company/model").json()["id"] == "model-1"
    assert client.post("/api/company/discover", json={}).json()["id"] == "model-2"
    response = client.put(
        "/api/company/claims/claim-1",
        json={"value": {"value": "corrected"}, "reason": "owner correction"},
    )
    assert response.json()["owner_locked"] is True
    service.create_owner_locked_claim_revision.assert_awaited_once_with(
        "claim-1",
        value={"value": "corrected"},
        actor="owner@example.com",
        reason="owner correction",
    )
