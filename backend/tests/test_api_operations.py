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
    class FakeCompanyContext:
        async def latest_snapshot(self):
            return {
                "id": "ctx_1",
                "source_hash": "hash-1",
                "company_namespace": "company:hyper_ai_lab",
                "created_at": "2026-06-14T00:00:00",
            }

        async def list_sync_runs(self, limit=1):
            return [{"id": "run_1", "status": "synced"}]

        def readiness_from_snapshot(self, snapshot, latest_run=None):
            return {
                "status": "ready",
                "required": True,
                "blocking": False,
                "stale": False,
                "last_sync_at": "2026-06-14T00:00:00",
                "source_hash": "hash-1",
            }

        async def drift_status(self):
            return {
                "enabled": True,
                "latest_drift": {"status": "unchanged"},
                "stale_role_gap_count": 0,
            }

    app.state.company_context_sync_service = FakeCompanyContext()
    app.state.autonomous_planning_service = AsyncMock()
    app.state.autonomous_planning_service.operating_cadence_status.return_value = {
        "status": "ready",
        "counts": {"cadences": 1, "due": 0, "not_due": 1, "active_plans": 0},
        "items": [],
    }
    app.state.autonomous_planning_service.list_operating_follow_ups.side_effect = [
        {"counts": {"total": 2, "by_resolution": {"unresolved": 2}}, "items": []},
        {"counts": {"total": 3, "by_resolution": {"reviewed": 2, "dismissed": 1}}, "items": []},
    ]
    app.state.autonomous_planning_service.list_owner_attention.return_value = {
        "generated_at": "2026-06-18T00:00:02+00:00",
        "filters": {"status": "active", "limit": 100},
        "counts": {
            "total": 1,
            "active": 1,
            "completed": 0,
            "overdue": 0,
            "due_soon": 0,
            "scheduler_created": 1,
            "executable": 1,
            "waiting_approval": 0,
        },
        "items": [{"plan_id": "plan_attention_1"}],
    }
    app.state.operating_cadence_scheduler_status = {
        "enabled": True,
        "status": "completed",
        "detail": "Scheduled operating cadence scan completed.",
        "actor": "operating_cadence_scheduler",
        "auto_execute": False,
        "interval_seconds": 900,
        "limit": 200,
        "last_started_at": "2026-06-18T00:00:00+00:00",
        "last_completed_at": "2026-06-18T00:00:01+00:00",
        "last_result": {
            "cadences_reviewed": 1,
            "cadences_due": 0,
            "plans_created": 0,
            "plans_existing": 0,
        },
        "last_error": None,
    }
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
    assert body["company_context"]["status"] == "ready"
    assert body["operating_cadence"]["counts"]["cadences"] == 1
    assert body["operating_follow_ups"]["counts"] == {
        "active": 2,
        "completed": 3,
        "total_visible": 5,
    }
    assert body["operating_cadence_scheduler"]["status"] == "completed"
    assert body["operating_cadence_scheduler"]["last_result"]["cadences_reviewed"] == 1
    assert body["owner_attention"]["counts"]["active"] == 1
    assert body["owner_attention"]["counts"]["scheduler_created"] == 1


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


def test_operating_cadence_routes(monkeypatch):
    app = FastAPI()
    app.include_router(operations_router, prefix="/api/operations")
    app.state.autonomous_planning_service = AsyncMock()
    app.state.autonomous_planning_service.operating_cadence_status.return_value = {
        "status": "ready",
        "counts": {"cadences": 1, "due": 1, "not_due": 0, "active_plans": 0},
        "items": [{"cadence_id": "cadence:agent:finance", "due": True}],
    }
    app.state.autonomous_planning_service.scan_operating_cadences.return_value = {
        "cadences_due": 1,
        "plans_created": 1,
        "created_plan_ids": ["plan_1"],
    }
    app.state.autonomous_planning_service.list_operating_follow_ups.return_value = {
        "generated_at": "2026-06-18T00:00:00",
        "filters": {
            "status": "completed",
            "kind": "erpnext_review",
            "target_view": "integrations",
            "company_namespace": "company:acme",
            "limit": 25,
        },
        "counts": {"total": 1, "active": 0, "completed": 1},
        "items": [{"plan_id": "plan_follow_up_1", "kind": "erpnext_review"}],
    }
    app.state.autonomous_planning_service.resolve_operating_follow_up.return_value = {
        "id": "plan_follow_up_1",
        "status": "completed",
        "summary": {"owner_resolution": {"action": "dismissed"}},
    }
    app.state.autonomous_planning_service.list_owner_attention.return_value = {
        "generated_at": "2026-06-18T00:00:02",
        "filters": {"status": "active", "limit": 25},
        "counts": {"total": 1, "active": 1, "scheduler_created": 1},
        "items": [{"plan_id": "plan_1", "kind": "scheduled_operating_cadence"}],
    }
    app.state.owner_attention_notification_service = AsyncMock()
    app.state.owner_attention_notification_service.run_once.return_value = {
        "status": "ready",
        "counts": {"reviewed": 1, "sent": 1, "skipped": 0, "failed": 0},
    }
    app.state.owner_attention_notification_service.status.return_value = {
        "enabled": True,
        "status": "ready",
        "channel": "email",
    }
    app.state.owner_attention_notification_status = {
        "enabled": True,
        "status": "idle",
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
    client = TestClient(app)

    status_response = client.get(
        "/api/operations/operating-cadence/status?company_namespace=company%3Aacme&limit=25"
    )
    scan_response = client.post(
        "/api/operations/operating-cadence/scan",
        json={
            "company_namespace": "company:acme",
            "auto_execute": True,
            "limit": 25,
        },
    )
    follow_up_response = client.get(
        "/api/operations/operating-cadence/follow-ups"
        "?company_namespace=company%3Aacme"
        "&status=completed"
        "&kind=erpnext_review"
        "&target_view=integrations"
        "&limit=25"
    )
    resolve_response = client.post(
        "/api/operations/operating-cadence/follow-ups/plan_follow_up_1/resolve",
        json={"action": "dismissed", "note": "No longer relevant."},
    )
    attention_response = client.get(
        "/api/operations/owner-attention?status=active&limit=25"
    )
    notify_response = client.post(
        "/api/operations/owner-attention/notify",
        json={"dry_run": False, "limit": 25},
    )
    notify_status_response = client.get(
        "/api/operations/owner-attention/notifications/status"
    )

    assert status_response.status_code == 200
    assert status_response.json()["counts"]["due"] == 1
    assert scan_response.status_code == 200
    assert scan_response.json()["plans_created"] == 1
    assert follow_up_response.status_code == 200
    assert follow_up_response.json()["items"][0]["plan_id"] == "plan_follow_up_1"
    assert resolve_response.status_code == 200
    assert resolve_response.json()["summary"]["owner_resolution"]["action"] == "dismissed"
    assert attention_response.status_code == 200
    assert attention_response.json()["items"][0]["plan_id"] == "plan_1"
    assert notify_response.status_code == 200
    assert notify_response.json()["counts"]["sent"] == 1
    assert notify_status_response.status_code == 200
    assert notify_status_response.json()["runtime"]["status"] == "idle"
    app.state.autonomous_planning_service.operating_cadence_status.assert_awaited_once_with(
        company_namespace="company:acme",
        limit=25,
    )
    app.state.autonomous_planning_service.scan_operating_cadences.assert_awaited_once_with(
        actor="owner@example.com",
        company_namespace="company:acme",
        auto_execute=False,
        limit=25,
    )
    app.state.autonomous_planning_service.list_operating_follow_ups.assert_awaited_once_with(
        status="completed",
        kind="erpnext_review",
        target_view="integrations",
        company_namespace="company:acme",
        limit=25,
    )
    app.state.autonomous_planning_service.resolve_operating_follow_up.assert_awaited_once_with(
        "plan_follow_up_1",
        action="dismissed",
        note="No longer relevant.",
        actor="owner@example.com",
        defer_until=None,
    )
    app.state.autonomous_planning_service.list_owner_attention.assert_awaited_once_with(
        status="active",
        limit=25,
    )
    app.state.owner_attention_notification_service.run_once.assert_awaited_once_with(
        actor="owner@example.com",
        limit=25,
        dry_run=False,
    )
    app.state.owner_attention_notification_service.status.assert_awaited_once()


def test_company_context_sync_routes(monkeypatch):
    app = FastAPI()
    app.include_router(operations_router, prefix="/api/operations")
    app.state.company_context_sync_service = AsyncMock()
    app.state.company_context_sync_service.sync_from_erpnext.return_value = {
        "status": "synced",
        "snapshot": {"id": "ctx_1"},
    }
    app.state.company_context_sync_service.scan_for_erpnext_drift.return_value = {
        "status": "unchanged",
        "drift": {"detected": False},
    }
    app.state.company_context_sync_service.drift_status.return_value = {
        "enabled": True,
        "latest_drift": {"status": "unchanged"},
    }
    app.state.company_context_sync_service.get_latest_context.return_value = {
        "snapshot": {"id": "ctx_1"},
        "freshness": {"status": "ready"},
    }
    app.state.company_context_sync_service.list_sync_runs.return_value = [
        {"id": "run_1", "status": "synced"}
    ]

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

    sync_response = client.post(
        "/api/operations/company-context/sync",
        json={
            "dry_run": False,
            "apply_low_risk": True,
            "run_planner": True,
            "source": "erpnext",
        },
    )
    latest_response = client.get("/api/operations/company-context")
    runs_response = client.get("/api/operations/company-context/sync-runs?limit=5")
    drift_response = client.post(
        "/api/operations/company-context/drift-scan",
        json={
            "dry_run": False,
            "apply_low_risk": True,
            "run_planner": True,
        },
    )
    drift_status_response = client.get("/api/operations/company-context/drift-status")

    assert sync_response.status_code == 200
    assert sync_response.json()["snapshot"]["id"] == "ctx_1"
    assert latest_response.status_code == 200
    assert latest_response.json()["freshness"]["status"] == "ready"
    assert runs_response.status_code == 200
    assert runs_response.json()[0]["status"] == "synced"
    assert drift_response.status_code == 200
    assert drift_response.json()["status"] == "unchanged"
    assert drift_status_response.status_code == 200
    assert drift_status_response.json()["latest_drift"]["status"] == "unchanged"
    app.state.company_context_sync_service.sync_from_erpnext.assert_awaited_once_with(
        actor="owner@example.com",
        dry_run=False,
        apply_low_risk=True,
        run_planner=True,
    )
    app.state.company_context_sync_service.scan_for_erpnext_drift.assert_awaited_once_with(
        actor="owner@example.com",
        dry_run=False,
        apply_low_risk=True,
        run_planner=True,
    )


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
