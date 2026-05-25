from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from cyber_team.api.rate_limit import reset_rate_limiter
from cyber_team.config import settings


@pytest.fixture(autouse=True)
def configure_test_environment(tmp_path, monkeypatch):
    reset_rate_limiter()
    # Override settings.data_dir to point to the pytest tmp_path to avoid modifying root /app/data
    test_data_dir = tmp_path / "app" / "data"
    test_data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "data_dir", str(test_data_dir))

    # Mock databases and APIs to keep unit tests isolated and extremely fast
    monkeypatch.setattr(settings, "opa_api_url", "http://mock-opa:8181")

@pytest.fixture
def mock_agent_manager():
    mgr = AsyncMock()
    mgr.create_role_manifest = AsyncMock()
    mgr.get_role_manifest = AsyncMock(return_value=None)
    mgr.instantiate_role = AsyncMock()
    return mgr

@pytest.fixture
def test_app_client(mock_agent_manager, monkeypatch):
    # Import the FastAPI app inside the fixture to ensure settings monkeypatch has taken effect
    from fastapi import FastAPI

    from cyber_team.api.routes.roles import router as roles_router
    from cyber_team.api.security import Principal, get_current_principal

    app = FastAPI()
    app.include_router(roles_router, prefix="/api/roles")

    # Inject mocked agent manager into app state
    app.state.agent_manager = mock_agent_manager

    # Override security dependencies
    async def mock_get_current_principal():
        return Principal(
            subject="owner",
            email="owner@example.com",
            role="owner",
            token_type="access"
        )

    app.dependency_overrides[get_current_principal] = mock_get_current_principal

    # Monkeypatch authorization check
    async def mock_require_authorization(*args, **kwargs):
        pass

    monkeypatch.setattr(
        "cyber_team.api.routes.roles.require_authorization",
        mock_require_authorization,
    )

    return TestClient(app)
