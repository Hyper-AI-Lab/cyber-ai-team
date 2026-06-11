from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cyber_team.api.routes.operations import router as operations_router
from cyber_team.api.security import Principal, get_current_principal


def owner_principal():
    return Principal(
        subject="owner",
        email="owner@example.com",
        role="owner",
        token_type="access",
    )


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
        return owner_principal()

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


def test_operations_readiness_reports_tool_and_integration_blockers(monkeypatch):
    app = FastAPI()
    app.include_router(operations_router, prefix="/api/operations")

    class FakeRegistry:
        def list_tool_contracts(self):
            return [
                {"name": "memory_recall", "state": "live", "side_effects": False},
                {
                    "name": "task_create",
                    "state": "unavailable",
                    "side_effects": True,
                    "readiness_reason": "No live executor",
                },
            ]

    class FakeComms:
        def integration_status(self):
            return [
                {
                    "channel": "email",
                    "provider": "smtp",
                    "mode": "simulated",
                    "detail": "SMTP is not configured.",
                }
            ]

    app.state.tool_registry = FakeRegistry()
    app.state.comms_gateway = FakeComms()
    app.state.audit_service = AsyncMock()
    app.state.audit_service.list_events.return_value = [{"id": "evidence-1"}]
    app.state.memory_service = AsyncMock()
    app.state.memory_service.list_memory_traces.return_value = [
        {"errors": ["write:failed"], "metadata": {"coverage": "error"}}
    ]
    monkeypatch.setattr(
        "cyber_team.api.routes.operations.settings.require_live_tool_executors",
        True,
    )

    async def mock_get_current_principal():
        return owner_principal()

    async def mock_require_authorization(*args, **kwargs):
        return None

    app.dependency_overrides[get_current_principal] = mock_get_current_principal
    monkeypatch.setattr(
        "cyber_team.api.routes.operations.require_authorization",
        mock_require_authorization,
    )

    response = TestClient(app).get("/api/operations/readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["tools"]["side_effect_blockers"][0]["tool_name"] == "task_create"
    assert body["integrations"]["blocking_readiness"] is True
    assert body["memory"]["recent_trace_errors"] == 1


def test_operations_readiness_keeps_optional_disabled_non_blocking(monkeypatch):
    app = FastAPI()
    app.include_router(operations_router, prefix="/api/operations")

    class FakeRegistry:
        def list_tool_contracts(self):
            return [
                {"name": "task_create", "state": "live", "side_effects": True},
                {
                    "name": "send_sms",
                    "state": "configuration_required",
                    "side_effects": True,
                    "category": "communications",
                    "readiness_reason": "SMS is intentionally disabled.",
                },
                {
                    "name": "make_call",
                    "state": "configuration_required",
                    "side_effects": True,
                    "category": "communications",
                    "readiness_reason": "Voice is intentionally disabled.",
                },
                {
                    "name": "ci_trigger",
                    "state": "configuration_required",
                    "side_effects": True,
                    "category": "devops",
                    "readiness_reason": "GitHub dispatch is not configured.",
                },
            ]

    class FakeComms:
        def integration_status(self):
            return [
                {
                    "channel": "email",
                    "provider": "smtp",
                    "mode": "live",
                    "detail": "SMTP ready.",
                },
                {
                    "channel": "sms",
                    "provider": "twilio",
                    "mode": "disabled",
                    "detail": "SMS is intentionally disabled.",
                },
            ]

    class FakeInbound:
        def integration_status(self):
            return {
                "channel": "inbound_email",
                "provider": "imap",
                "mode": "live",
                "detail": "IMAP ready.",
            }

    class FakeERPNext:
        def integration_status(self, _last_validation=None):
            return {
                "provider": "erpnext",
                "mode": "live",
                "configured": True,
                "detail": "ERPNext ready.",
            }

    app.state.tool_registry = FakeRegistry()
    app.state.comms_gateway = FakeComms()
    app.state.inbound_email_service = FakeInbound()
    app.state.erpnext = FakeERPNext()
    app.state.audit_service = AsyncMock()
    app.state.audit_service.list_events.return_value = []
    app.state.memory_service = AsyncMock()
    app.state.memory_service.list_memory_traces.return_value = []
    monkeypatch.setattr(
        "cyber_team.api.routes.operations.settings.require_live_tool_executors",
        True,
    )
    monkeypatch.setattr(
        "cyber_team.api.routes.operations.settings.required_communication_providers",
        "smtp,imap,erpnext",
    )

    async def mock_get_current_principal():
        return owner_principal()

    async def mock_require_authorization(*args, **kwargs):
        return None

    app.dependency_overrides[get_current_principal] = mock_get_current_principal
    monkeypatch.setattr(
        "cyber_team.api.routes.operations.require_authorization",
        mock_require_authorization,
    )

    response = TestClient(app).get("/api/operations/readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["tools"]["side_effect_blockers"] == []
    assert {
        item["tool_name"] for item in body["tools"]["non_blocking_side_effects"]
    } == {"send_sms", "make_call", "ci_trigger"}
    assert body["integrations"]["blocking_readiness"] is False
    assert body["integrations"]["optional_disabled"][0]["provider"] == "twilio"


def test_plan_scan_forces_manual_only_autonomy(monkeypatch):
    app = FastAPI()
    app.include_router(operations_router, prefix="/api/operations")
    app.state.autonomous_planning_service = AsyncMock()
    app.state.autonomous_planning_service.scan_and_plan.return_value = {
        "plans_created": 0,
        "execution": None,
    }
    monkeypatch.setattr(
        "cyber_team.api.routes.operations.settings.autonomy_side_effect_mode",
        "manual_only",
    )

    async def mock_get_current_principal():
        return owner_principal()

    async def mock_require_authorization(*args, **kwargs):
        return None

    app.dependency_overrides[get_current_principal] = mock_get_current_principal
    monkeypatch.setattr(
        "cyber_team.api.routes.operations.require_authorization",
        mock_require_authorization,
    )

    response = TestClient(app).post(
        "/api/operations/plans/scan",
        json={"auto_execute": True, "limit": 10},
    )

    assert response.status_code == 200
    app.state.autonomous_planning_service.scan_and_plan.assert_awaited_once()
    assert app.state.autonomous_planning_service.scan_and_plan.await_args.kwargs[
        "auto_execute"
    ] is False


def test_gdpr_subject_delete_is_audit_preserving(monkeypatch):
    app = FastAPI()
    app.include_router(operations_router, prefix="/api/operations")
    app.state.retention_service = AsyncMock()
    app.state.retention_service.delete_subject_data.return_value = {
        "subject": "customer@example.com",
        "dry_run": True,
        "include_audit": False,
        "counts": {},
        "audit_events_retained": True,
    }
    app.state.audit_service = AsyncMock()

    async def mock_get_current_principal():
        return owner_principal()

    async def mock_require_authorization(*args, **kwargs):
        return None

    app.dependency_overrides[get_current_principal] = mock_get_current_principal
    monkeypatch.setattr(
        "cyber_team.api.routes.operations.require_authorization",
        mock_require_authorization,
    )
    client = TestClient(app)

    rejected = client.post(
        "/api/operations/gdpr/subjects/customer%40example.com/delete",
        json={"dry_run": True, "audit_preserving": False},
    )
    accepted = client.post(
        "/api/operations/gdpr/subjects/customer%40example.com/delete",
        json={"dry_run": True, "audit_preserving": True},
    )

    assert rejected.status_code == 400
    assert accepted.status_code == 200
    app.state.retention_service.delete_subject_data.assert_awaited_once_with(
        "customer@example.com",
        dry_run=True,
        include_audit=False,
    )
    app.state.audit_service.record_control_evidence.assert_awaited_once()
