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


def test_validate_imap_integration_uses_inbound_email_service(monkeypatch):
    app = FastAPI()
    app.include_router(integrations_router, prefix="/api/integrations")

    class FakeCommsGateway:
        def integration_status(self):
            return []

        def last_validation_result(self):
            return None

    class FakeInboundEmailService:
        async def validate(self):
            return {
                "status": "ready",
                "checked_at": "2026-06-07T00:00:00+00:00",
                "provider": "imap",
                "results": [
                    {
                        "channel": "inbound_email",
                        "provider": "imap",
                        "status": "ready",
                        "network_check": "passed",
                    }
                ],
            }

        def integration_status(self):
            return {
                "channel": "inbound_email",
                "provider": "imap",
                "configured": True,
                "mode": "live",
            }

    app.state.comms_gateway = FakeCommsGateway()
    app.state.inbound_email_service = FakeInboundEmailService()
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

    response = TestClient(app).post("/api/integrations/validate", json={"provider": "imap"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["provider"] == "imap"


def test_validate_erpnext_integration_uses_erpnext_client(monkeypatch):
    app = FastAPI()
    app.include_router(integrations_router, prefix="/api/integrations")

    class FakeERPNext:
        async def validate(self):
            return {
                "status": "ready",
                "provider": "erpnext",
                "mode": "live",
                "configured": True,
                "detail": "ERPNext REST API token validation passed.",
            }

    app.state.comms_gateway = type(
        "FakeCommsGateway",
        (),
        {
            "integration_status": lambda _self: [],
            "last_validation_result": lambda _self: None,
        },
    )()
    app.state.erpnext = FakeERPNext()
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

    response = TestClient(app).post(
        "/api/integrations/validate",
        json={"provider": "erpnext"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["provider"] == "erpnext"
    assert body["results"][0]["mode"] == "live"
    app.state.audit_service.record_control_evidence.assert_awaited_once()


def test_integration_status_includes_erpnext_company_context(monkeypatch):
    app = FastAPI()
    app.include_router(integrations_router, prefix="/api/integrations")

    class FakeERPNext:
        def integration_status(self, _last_validation=None):
            return {
                "provider": "erpnext",
                "configured": True,
                "mode": "live",
                "detail": "ERPNext ready.",
            }

    class FakeCompanyContext:
        async def latest_snapshot(self):
            return {"id": "ctx_1"}

        async def list_sync_runs(self, limit=1):
            return [{"id": "run_1", "status": "synced"}]

        def readiness_from_snapshot(self, snapshot, latest_run=None):
            return {
                "status": "ready",
                "last_sync_at": "2026-06-14T00:00:00",
                "source_hash": "hash-1",
                "latest_run_status": latest_run["status"],
            }

    app.state.comms_gateway = type(
        "FakeCommsGateway",
        (),
        {
            "integration_status": lambda _self: [],
            "last_validation_result": lambda _self: None,
        },
    )()
    app.state.erpnext = FakeERPNext()
    app.state.company_context_sync_service = FakeCompanyContext()

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
    assert body["erpnext"]["company_context"]["status"] == "ready"
    assert body["erpnext"]["company_context"]["latest_run_status"] == "synced"
