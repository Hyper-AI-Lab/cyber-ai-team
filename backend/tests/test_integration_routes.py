from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cyber_team.api.routes.integrations import router as integrations_router
from cyber_team.api.security import Principal, get_current_principal


def test_integration_status_route_returns_comms_status(monkeypatch):
    app = FastAPI()
    app.include_router(integrations_router, prefix="/api/integrations")

    class FakeCommsGateway:
        def integration_status(self):
            return [
                {
                    "channel": "email",
                    "provider": "smtp",
                    "configured": False,
                    "mode": "simulated",
                }
            ]

        def last_validation_result(self):
            return None

    app.state.comms_gateway = FakeCommsGateway()

    async def mock_get_current_principal():
        return Principal(
            subject="owner",
            email="owner@example.com",
            role="owner",
            token_type="access",
        )

    async def mock_require_authorization(*args, **kwargs):
        return None

    app.dependency_overrides[get_current_principal] = mock_get_current_principal
    monkeypatch.setattr(
        "cyber_team.api.routes.integrations.require_authorization",
        mock_require_authorization,
    )

    response = TestClient(app).get("/api/integrations/status")

    assert response.status_code == 200
    body = response.json()
    assert body["communications"][0]["channel"] == "email"
    assert body["communications"][0]["mode"] == "simulated"


def test_validate_integration_route_records_control_evidence(monkeypatch):
    app = FastAPI()
    app.include_router(integrations_router, prefix="/api/integrations")

    class FakeCommsGateway:
        async def validate_integrations(self, provider):
            return {
                "status": "blocked",
                "checked_at": "2026-06-05T00:00:00+00:00",
                "provider": provider,
                "results": [
                    {
                        "channel": "email",
                        "provider": provider,
                        "status": "configuration_required",
                        "missing": ["SMTP_HOST"],
                        "detail": "SMTP validation requires host.",
                    }
                ],
            }

    app.state.comms_gateway = FakeCommsGateway()
    app.state.audit_service = type(
        "FakeAudit",
        (),
        {"record_control_evidence": AsyncMock()},
    )()

    async def mock_get_current_principal():
        return Principal(
            subject="owner",
            email="owner@example.com",
            role="owner",
            token_type="access",
        )

    async def mock_require_authorization(*args, **kwargs):
        return None

    app.dependency_overrides[get_current_principal] = mock_get_current_principal
    monkeypatch.setattr(
        "cyber_team.api.routes.integrations.require_authorization",
        mock_require_authorization,
    )

    response = TestClient(app).post("/api/integrations/validate", json={"provider": "smtp"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    app.state.audit_service.record_control_evidence.assert_awaited_once()
    kwargs = app.state.audit_service.record_control_evidence.await_args.kwargs
    assert kwargs["control_id"] == "integration.validation"
    assert kwargs["outcome"] == "blocked"
    assert kwargs["evidence"]["provider"] == "smtp"
