from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cyber_team.api.routes.operations import router as operations_router
from cyber_team.api.security import Principal, get_current_principal


def test_run_autonomous_cycle_endpoint(monkeypatch):
    app = FastAPI()
    app.include_router(operations_router, prefix="/api/operations")
    app.state.autonomous_operations_service = AsyncMock()
    app.state.autonomous_operations_service.run_once.return_value = {
        "cycle_id": "auto_cycle_123",
        "started_at": "2026-06-02T00:00:00",
        "completed_at": "2026-06-02T00:00:01",
        "actor": "owner@example.com",
        "status": "completed",
        "memory_steward": None,
        "supervisor_review": None,
        "planner": None,
        "decisions": [],
        "errors": [],
        "counts": {},
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
        "cyber_team.api.routes.operations.require_authorization",
        mock_require_authorization,
    )

    response = TestClient(app).post(
        "/api/operations/autonomous-cycle",
        json={
            "run_memory_steward": True,
            "run_supervisor_review": False,
            "run_planner": False,
            "apply_safe_memory_actions": False,
            "request_memory_action_approvals": True,
            "memory_remediation_limit": 25,
            "auto_execute_plans": False,
            "continue_on_error": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["cycle_id"] == "auto_cycle_123"
    app.state.autonomous_operations_service.run_once.assert_called_once_with(
        actor="owner@example.com",
        run_memory_steward=True,
        run_supervisor_review=False,
        run_planner=False,
        apply_safe_memory_actions=False,
        request_memory_action_approvals=True,
        memory_remediation_limit=25,
        auto_execute_plans=False,
        continue_on_error=False,
    )
