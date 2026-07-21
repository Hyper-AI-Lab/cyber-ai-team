from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cyber_team.api.routes.workflows import router as workflows_router
from cyber_team.api.security import Principal, get_current_principal


def test_workflow_intent_routes(monkeypatch):
    app = FastAPI()
    app.include_router(workflows_router, prefix="/api/workflows")
    app.state.workflow_intent_service = AsyncMock()
    app.state.workflow_intent_service.list_intents.return_value = {
        "items": [{"id": "intent-1"}],
        "groups": [],
        "counts": {"total": 1},
    }
    app.state.workflow_intent_service.generate_from_company_context.return_value = {
        "status": "completed",
        "created": 1,
        "intents": [{"id": "intent-1"}],
    }
    app.state.workflow_intent_service.instantiate_intent.return_value = {
        "id": "workflow-1",
        "name": "Generated workflow",
        "description": "Generated from intent",
        "graph_definition": {"entry_node": "start", "nodes": []},
        "status": "draft",
        "trigger_type": "manual",
        "trigger_config": {"workflow_intent_id": "intent-1"},
    }
    app.state.workflow_intent_service.resolve_intent.return_value = {
        "id": "intent-1",
        "status": "dismissed",
    }

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
        "cyber_team.api.routes.workflows.require_authorization",
        mock_require_authorization,
    )

    client = TestClient(app)

    assert client.get("/api/workflows/intents?status=proposed").json()["counts"]["total"] == 1
    generate = client.post(
        "/api/workflows/intents/generate",
        json={"snapshot_id": "ctx-1", "limit": 5, "instantiate_low_risk": True},
    )
    assert generate.status_code == 200
    instantiate = client.post("/api/workflows/intents/intent-1/instantiate")
    assert instantiate.status_code == 200
    assert instantiate.json()["trigger_config"]["workflow_intent_id"] == "intent-1"
    resolve = client.post(
        "/api/workflows/intents/intent-1/resolve",
        json={"status": "dismissed", "note": "Not needed"},
    )
    assert resolve.status_code == 200

    app.state.workflow_intent_service.list_intents.assert_called_once_with(
        status="proposed",
        category=None,
        source_type=None,
        company_namespace=None,
        readiness_status=None,
        limit=100,
    )
    app.state.workflow_intent_service.generate_from_company_context.assert_called_once_with(
        snapshot_id="ctx-1",
        actor="owner@example.com",
        limit=5,
        instantiate_low_risk=True,
    )
    app.state.workflow_intent_service.instantiate_intent.assert_called_once_with(
        "intent-1",
        actor="owner@example.com",
    )
    app.state.workflow_intent_service.resolve_intent.assert_called_once_with(
        "intent-1",
        status="dismissed",
        note="Not needed",
        actor="owner@example.com",
    )
