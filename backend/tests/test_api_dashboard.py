from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cyber_team.api.routes.dashboard import router as dashboard_router
from cyber_team.api.security import Principal, get_current_principal


def _owner() -> Principal:
    return Principal(
        subject="owner",
        email="owner@example.com",
        role="owner",
        token_type="access",
    )


def _client(monkeypatch) -> tuple[TestClient, AsyncMock, FastAPI]:
    app = FastAPI()
    app.include_router(dashboard_router, prefix="/api/dashboard")
    orchestrator = AsyncMock()
    orchestrator.get_kpis.return_value = {
        "total_agents": 2,
        "total_workflows": 3,
        "pending_approvals": 1,
        "running_workflows": 0,
    }
    app.state.orchestrator = orchestrator

    async def principal():
        return _owner()

    async def authorize(*args, **kwargs):
        return None

    app.dependency_overrides[get_current_principal] = principal
    monkeypatch.setattr(
        "cyber_team.api.routes.dashboard.require_authorization",
        authorize,
    )
    return TestClient(app), orchestrator, app


def test_dashboard_kpis_are_cached_after_authorization(monkeypatch):
    client, orchestrator, _ = _client(monkeypatch)

    first = client.get("/api/dashboard/kpis")
    second = client.get("/api/dashboard/kpis")

    assert first.status_code == 200
    assert second.json() == first.json()
    assert orchestrator.get_kpis.await_count == 1


def test_dashboard_kpis_serve_stale_payload_while_refreshing(monkeypatch):
    client, orchestrator, app = _client(monkeypatch)
    first = client.get("/api/dashboard/kpis")
    app.state.dashboard_kpi_cache["expires_at"] = 0

    class DeferredTask:
        def done(self):
            return False

        def add_done_callback(self, callback):
            self.callback = callback

    scheduled = []

    def defer(coroutine):
        scheduled.append(coroutine)
        return DeferredTask()

    monkeypatch.setattr("cyber_team.api.routes.dashboard.asyncio.create_task", defer)
    second = client.get("/api/dashboard/kpis")

    assert second.status_code == 200
    assert second.json() == first.json()
    assert orchestrator.get_kpis.await_count == 1
    assert len(scheduled) == 1
    scheduled[0].close()
