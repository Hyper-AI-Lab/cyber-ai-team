from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cyber_team.api.routes.memory import router as memory_router
from cyber_team.api.security import Principal, get_current_principal


def owner_principal():
    return Principal(
        subject="owner",
        email="owner@example.com",
        role="owner",
        token_type="access",
    )


def conflict_item(status: str = "open") -> dict:
    return {
        "id": "memconf_1",
        "conflict_type": "canonical_fact_mismatch",
        "severity": "high",
        "status": status,
        "memory_id": "mem_1",
        "memory_namespace": "company:acme",
        "company_namespace": "company:acme",
        "canonical_source_type": "erpnext_company_context",
        "canonical_source_id": "ctx_1",
        "canonical_source_hash": "hash-current",
        "memory_source_hash": "hash-old",
        "claim_path": "company_name",
        "title": "Memory disagrees with ERPNext canonical fact: company_name",
        "description": "The memory entry disagrees with the canonical record.",
        "recommendation": "Prefer ERPNext until owner review is complete.",
        "memory_excerpt": "Old Co",
        "canonical_excerpt": "Acme Co",
        "evidence": {},
        "resolution": {},
        "dedupe_key": "canonical_fact_mismatch:mem_1:company_name:hash-current",
        "created_at": "2026-07-21T12:00:00",
        "updated_at": "2026-07-21T12:00:00",
        "resolved_at": None,
    }


def test_memory_canonical_conflict_routes(monkeypatch):
    app = FastAPI()
    app.include_router(memory_router, prefix="/api/memory")
    app.state.memory_conflict_service = AsyncMock()
    app.state.memory_conflict_service.scan.return_value = {
        "status": "completed",
        "conflicts_found": 1,
    }
    app.state.memory_conflict_service.list_conflicts.return_value = [
        conflict_item(),
    ]
    app.state.memory_conflict_service.resolve_conflict.return_value = conflict_item(
        "resolved"
    )

    async def mock_get_current_principal():
        return owner_principal()

    async def mock_require_authorization(*args, **kwargs):
        return None

    app.dependency_overrides[get_current_principal] = mock_get_current_principal
    monkeypatch.setattr(
        "cyber_team.api.routes.memory.require_authorization",
        mock_require_authorization,
    )
    client = TestClient(app)

    assert client.post(
        "/api/memory/conflicts/scan",
        json={"dry_run": True, "limit": 10},
    ).json()["conflicts_found"] == 1
    assert client.get(
        "/api/memory/conflicts?status=open,acknowledged&limit=5",
    ).json()[0]["id"] == "memconf_1"
    assert client.post(
        "/api/memory/conflicts/memconf_1/resolve",
        json={
            "status": "resolved",
            "resolution_strategy": "prefer_canonical",
            "note": "reviewed",
        },
    ).json()["status"] == "resolved"

    app.state.memory_conflict_service.scan.assert_awaited_once_with(
        actor="owner@example.com",
        dry_run=True,
        limit=10,
    )
    app.state.memory_conflict_service.list_conflicts.assert_awaited_once_with(
        status="open,acknowledged",
        severity=None,
        company_namespace=None,
        limit=5,
    )
    app.state.memory_conflict_service.resolve_conflict.assert_awaited_once_with(
        "memconf_1",
        status="resolved",
        resolution_strategy="prefer_canonical",
        note="reviewed",
        actor="owner@example.com",
    )
