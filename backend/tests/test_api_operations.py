from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cyber_team.api.routes.operations import router as operations_router
from cyber_team.api.security import Principal, get_current_principal
from cyber_team.clock import utc_now


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


def test_governor_routes_call_service(monkeypatch):
    app = FastAPI()
    app.include_router(operations_router, prefix="/api/operations")
    app.state.orchestration_governor_service = AsyncMock()
    app.state.orchestration_governor_service.run_once.return_value = {
        "run_id": "govrun_1",
        "status": "completed",
    }
    app.state.orchestration_governor_service.latest_run.return_value = {
        "run_id": "govrun_1",
        "status": "completed",
    }
    app.state.orchestration_governor_service.list_runs.return_value = {
        "items": [{"run_id": "govrun_1"}],
    }
    app.state.orchestration_governor_service.list_decisions.return_value = {
        "items": [{"id": "govdec_1"}],
    }
    app.state.orchestration_governor_service.list_tool_proposals.return_value = {
        "items": [{"id": "toolprop_1"}],
    }
    app.state.orchestration_governor_service.request_tool_proposal_approval.return_value = {
        "status": "approval_requested",
        "approval_id": "approval_1",
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
    client = TestClient(app)

    assert client.post(
        "/api/operations/governor/run",
        json={"dry_run": True, "max_actions": 3},
    ).json()["run_id"] == "govrun_1"
    assert client.get("/api/operations/governor/latest").json()["run_id"] == "govrun_1"
    assert client.get("/api/operations/governor/runs?limit=5").json()["items"][0][
        "run_id"
    ] == "govrun_1"
    assert client.get(
        "/api/operations/governor/decisions?status=delegated&limit=5"
    ).json()["items"][0]["id"] == "govdec_1"
    assert client.get(
        "/api/operations/governor/tool-proposals?status=proposed&limit=5"
    ).json()["items"][0]["id"] == "toolprop_1"
    assert client.post(
        "/api/operations/governor/tool-proposals/toolprop_1/approval",
        json={"note": "review"},
    ).json()["approval_id"] == "approval_1"

    app.state.orchestration_governor_service.run_once.assert_awaited_once_with(
        actor="owner@example.com",
        dry_run=True,
        auto_apply_low_risk=None,
        max_actions=3,
        continue_on_error=True,
    )
    app.state.orchestration_governor_service.list_runs.assert_awaited_once_with(limit=5)
    app.state.orchestration_governor_service.list_decisions.assert_awaited_once_with(
        status="delegated",
        decision_type=None,
        limit=5,
    )
    app.state.orchestration_governor_service.list_tool_proposals.assert_awaited_once_with(
        status="proposed",
        limit=5,
    )
    (
        app.state.orchestration_governor_service.request_tool_proposal_approval
        .assert_awaited_once_with(
            "toolprop_1",
            actor="owner@example.com",
            note="review",
        )
    )


def test_executive_brief_email_routes(monkeypatch):
    app = FastAPI()
    app.include_router(operations_router, prefix="/api/operations")
    app.state.executive_brief_email_service = AsyncMock()
    app.state.executive_brief_email_service.status.return_value = {
        "enabled": True,
        "status": "ready",
        "channel": "email",
        "recipient": "owner@example.com",
    }
    app.state.executive_brief_email_service.run_once.return_value = {
        "status": "dry_run",
        "detail": "Executive brief email dry run completed.",
        "dry_run": True,
        "force": True,
        "brief_summary": {"latest_run_id": "exegov_1"},
    }
    app.state.executive_brief_email_status = {
        "enabled": True,
        "status": "idle",
        "last_completed_at": None,
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
    client = TestClient(app)

    status_response = client.get("/api/operations/executive-brief/email/status")
    send_response = client.post(
        "/api/operations/executive-brief/email",
        json={"dry_run": True, "force": True},
    )

    assert status_response.status_code == 200
    assert status_response.json()["runtime"]["status"] == "idle"
    assert send_response.status_code == 200
    assert send_response.json()["brief_summary"]["latest_run_id"] == "exegov_1"
    app.state.executive_brief_email_service.status.assert_awaited_once()
    app.state.executive_brief_email_service.run_once.assert_awaited_once_with(
        actor="owner@example.com",
        dry_run=True,
        force=True,
    )


def test_executive_cadence_route_combines_runtime_and_durable_history(monkeypatch):
    app = FastAPI()
    app.include_router(operations_router, prefix="/api/operations")
    recent_sent = utc_now().isoformat()
    app.state.audit_service = AsyncMock()

    async def list_events(*, event_type=None, limit=20, **_kwargs):
        if event_type == "executive_brief.email":
            return [
                {
                    "id": "audit_brief_1",
                    "event_type": "executive_brief.email",
                    "outcome": "sent",
                    "created_at": recent_sent,
                    "metadata": {"idempotency_key": "executive-brief:2026-07-21"},
                }
            ]
        if event_type == "orchestration_governor.scheduler_run":
            return [
                {
                    "id": "audit_gov_1",
                    "event_type": "orchestration_governor.scheduler_run",
                    "outcome": "success",
                    "created_at": recent_sent,
                    "metadata": {"run_id": "exegov_1"},
                }
            ]
        return []

    app.state.audit_service.list_events.side_effect = list_events
    app.state.executive_company_os_service = AsyncMock()
    app.state.executive_company_os_service.list_runs.return_value = {
        "items": [
            {
                "run_id": "exegov_1",
                "status": "completed",
                "snapshot_hash": "hash-1",
                "counts": {
                    "executions": 3,
                    "completed": 2,
                    "approval_required": 1,
                    "blocked": 0,
                    "outsourcing_required": 0,
                    "benchmark_failed": 0,
                },
            }
        ],
        "count": 1,
    }
    app.state.executive_company_os_service.list_observer_reviews.return_value = {
        "items": [{"id": "obs_1", "status": "agreed", "created_at": recent_sent}],
        "count": 1,
    }
    app.state.owner_attention_notification_service = AsyncMock()
    app.state.owner_attention_notification_service.status.return_value = {
        "enabled": True,
        "status": "ready",
        "detail": "Owner attention notifications can send live email.",
    }
    app.state.executive_brief_email_service = AsyncMock()
    app.state.executive_brief_email_service.status.return_value = {
        "enabled": True,
        "status": "ready",
        "channel": "email",
        "recipient": "owner@example.com",
        "cooldown_hours": 20,
        "last_event": {
            "id": "audit_brief_1",
            "outcome": "sent",
            "created_at": recent_sent,
        },
    }
    app.state.orchestration_governor_scheduler_status = {
        "enabled": True,
        "status": "completed",
        "detail": "Chief Operating Agent governor run completed.",
        "actor": "chief_operating_agent_scheduler",
        "interval_seconds": 3600,
        "last_started_at": "2026-07-21T00:00:00+00:00",
        "last_completed_at": "2026-07-21T00:01:00+00:00",
        "last_result": {"run_id": "exegov_1"},
        "last_error": None,
    }
    app.state.operating_cadence_scheduler_status = {
        "enabled": True,
        "status": "completed",
        "interval_seconds": 900,
        "last_completed_at": "2026-07-21T00:02:00+00:00",
    }
    app.state.owner_attention_notification_status = {
        "enabled": True,
        "status": "skipped",
        "interval_seconds": 900,
        "last_completed_at": "2026-07-21T00:03:00+00:00",
    }
    app.state.executive_brief_email_status = {
        "enabled": True,
        "status": "skipped",
        "interval_seconds": 86400,
        "last_completed_at": "2026-07-21T00:04:00+00:00",
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

    response = TestClient(app).get("/api/operations/executive-cadence")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["counts"]["loops"] == 4
    assert body["counts"]["enabled"] == 4
    assert body["latest_executive_run"]["run_id"] == "exegov_1"
    assert body["latest_observer_review"]["id"] == "obs_1"
    assert body["idempotency"]["latest_snapshot_hash"] == "hash-1"
    assert body["idempotency"]["brief_cooldown"]["cooldown_active"] is True
    assert body["low_risk_remediation"]["completed"] == 2
    assert body["low_risk_remediation"]["approval_required"] == 1
    brief_loop = next(
        item for item in body["loops"] if item["loop_id"] == "executive_brief_email"
    )
    assert brief_loop["durable_history"]["recent_counts"]["sent"] == 1


def test_executive_company_os_routes_call_service(monkeypatch):
    app = FastAPI()
    app.include_router(operations_router, prefix="/api/operations")
    app.state.orchestration_governor_service = AsyncMock()
    app.state.orchestration_governor_service.latest_run.return_value = None
    app.state.executive_company_os_service = AsyncMock()
    app.state.executive_company_os_service.run_executive_cycle.return_value = {
        "run_id": "exegov_1",
        "status": "completed",
        "counts": {"executions": 1},
    }
    app.state.executive_company_os_service.executive_brief.return_value = {
        "latest_run": {"run_id": "exegov_1"},
        "objectives": {"count": 1, "items": []},
        "kpis": {"count": 1, "items": []},
        "benchmarks": {"latest_results": {"count": 1, "items": []}},
        "observer": {"latest_review": None},
    }
    app.state.executive_company_os_service.operation_graph.return_value = {
        "nodes": [{"id": "node_1"}],
        "edges": [],
        "count": 1,
    }
    app.state.executive_company_os_service.list_observer_reviews.return_value = {
        "items": [{"id": "obs_1"}],
    }
    app.state.executive_company_os_service.list_outsourcing_requests.return_value = {
        "items": [{"id": "out_1"}],
    }
    app.state.executive_company_os_service.get_policy.return_value = {
        "id": "default",
        "paused": False,
    }
    app.state.executive_company_os_service.resource_policy_status.return_value = {
        "status": "ready",
    }
    app.state.executive_company_os_service.update_policy.return_value = {
        "id": "default",
        "paused": True,
    }
    app.state.executive_company_os_service.pause.return_value = {
        "id": "default",
        "paused": True,
    }
    app.state.executive_company_os_service.resume.return_value = {
        "id": "default",
        "paused": False,
    }
    app.state.executive_company_os_service.run_observer_review.return_value = {
        "id": "obs_2",
        "status": "agreed",
    }
    app.state.executive_company_os_service.list_objectives.return_value = {
        "items": [{"id": "objective_1"}],
        "count": 1,
    }
    app.state.executive_company_os_service.replace_objectives.return_value = {
        "items": [{"id": "objective_1"}],
        "count": 1,
    }
    app.state.executive_company_os_service.list_reflections.return_value = {
        "items": [{"id": "refl_1"}],
    }
    app.state.executive_company_os_service.list_benchmarks.return_value = {
        "items": [{"id": "bench_1"}],
    }
    app.state.executive_company_os_service.create_benchmark.return_value = {
        "id": "bench_2",
    }
    app.state.executive_company_os_service.list_benchmark_results.return_value = {
        "items": [{"id": "benchres_1"}],
    }
    app.state.executive_company_os_service.create_outsourcing_request.return_value = {
        "id": "out_2",
    }
    app.state.executive_company_os_service.resolve_outsourcing_request.return_value = {
        "id": "out_2",
        "status": "resolved",
    }
    app.state.executive_company_os_service.deduplicate_outsourcing_requests.return_value = {
        "duplicate_count": 2,
        "group_count": 1,
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
    client = TestClient(app)

    assert client.post(
        "/api/operations/governor/run",
        json={
            "mode": "executive",
            "dry_run": True,
            "owner_instruction": "Summarize objectives",
        },
    ).json()["run_id"] == "exegov_1"
    assert client.get("/api/operations/executive-brief").json()["latest_run"][
        "run_id"
    ] == "exegov_1"
    assert client.get("/api/operations/operation-graph?limit=5").json()["count"] == 1
    assert client.get("/api/operations/observer/reviews").json()["items"][0][
        "id"
    ] == "obs_1"
    assert client.get("/api/operations/outsourcing-requests").json()["items"][0][
        "id"
    ] == "out_1"
    assert client.post(
        "/api/operations/outsourcing-requests/deduplicate",
        json={"dry_run": False},
    ).json()["duplicate_count"] == 2
    assert client.get("/api/operations/resource-policy").json()["status"] == "ready"
    assert client.post(
        "/api/operations/governor/instruct",
        json={"instruction": "Review the KPI scorecard."},
    ).json()["run_id"] == "exegov_1"
    assert client.post("/api/operations/governor/pause", json={"reason": "test"}).json()[
        "paused"
    ] is True
    assert client.post(
        "/api/operations/observer/run",
        json={"run_id": "exegov_1"},
    ).json()["status"] == "agreed"

    app.state.executive_company_os_service.run_executive_cycle.assert_any_await(
        actor="owner@example.com",
        dry_run=True,
        auto_apply_low_risk=None,
        max_actions=None,
        force_reflection=False,
        force_benchmark_refresh=False,
        owner_instruction="Summarize objectives",
        observer_review=True,
        synthetic_large_impact=False,
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


def test_operations_readiness_caches_snapshot_until_refresh(monkeypatch):
    app = FastAPI()
    app.include_router(operations_router, prefix="/api/operations")

    class FakeRegistry:
        def __init__(self):
            self.calls = 0

        def list_tool_contracts(self):
            self.calls += 1
            return [{"name": "memory_recall", "state": "live", "side_effects": False}]

    class FakeComms:
        def integration_status(self):
            return []

    registry = FakeRegistry()
    app.state.tool_registry = registry
    app.state.comms_gateway = FakeComms()
    app.state.audit_service = AsyncMock()
    app.state.audit_service.list_events.return_value = []
    app.state.memory_service = AsyncMock()
    app.state.memory_service.list_memory_traces.return_value = []
    monkeypatch.setattr(
        "cyber_team.api.routes.operations.settings.required_communication_providers",
        "",
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
    assert client.get("/api/operations/readiness").status_code == 200
    assert client.get("/api/operations/readiness").status_code == 200
    assert registry.calls == 1

    assert client.get("/api/operations/readiness?refresh=true").status_code == 200
    assert registry.calls == 2


def test_operations_readiness_keeps_optional_disabled_non_blocking(monkeypatch):
    app = FastAPI()
    app.include_router(operations_router, prefix="/api/operations")

    class FakeRegistry:
        def list_tool_contracts(self):
            return [
                {"name": "task_create", "state": "live", "side_effects": True},
                {
                    "name": "sms_send",
                    "state": "configuration_required",
                    "side_effects": True,
                    "category": "communications",
                    "readiness_reason": "SMS is intentionally disabled.",
                },
                {
                    "name": "call_make",
                    "state": "configuration_required",
                    "side_effects": True,
                    "category": "communications",
                    "readiness_reason": "Voice is intentionally disabled.",
                },
                {
                    "name": "message_send",
                    "state": "configuration_required",
                    "side_effects": True,
                    "category": "communications",
                    "readiness_reason": "Messaging is intentionally disabled.",
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
    app.state.team_activation_service = AsyncMock()
    app.state.team_activation_service.coverage_summary.return_value = {
        "status": "active",
        "latest_run": {"id": "teamact_1", "status": "completed"},
        "active_agent_count": 3,
        "active_grant_count": 6,
        "pending_or_blocked_grant_count": 1,
        "blocking": False,
    }
    app.state.workflow_template_service = AsyncMock()
    app.state.workflow_template_service.list_templates.return_value = [
        {"id": "wft_company_context_review_1_0_0"},
    ]
    app.state.orchestrator = AsyncMock()
    app.state.orchestrator.list_workflows.return_value = [
        {
            "id": "workflow_1",
            "trigger_config": {"template_id": "wft_company_context_review_1_0_0"},
        }
    ]
    app.state.interop_service = AsyncMock()
    app.state.interop_service.summary.return_value = {
        "status": "available",
        "mcp": {"tool_counts": {"total": 3}},
        "a2a": {"agent_counts": {"total": 2}},
    }
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
    } == {"sms_send", "call_make", "message_send", "ci_trigger"}
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
    assert body["team_activation"]["status"] == "active"
    assert body["workflow_templates"]["core_template_count"] == 1
    assert body["workflow_templates"]["core_workflow_count"] == 1
    assert body["interop"]["status"] == "available"


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


def test_alert_email_delivery_records_control_evidence(monkeypatch):
    app = FastAPI()
    app.include_router(operations_router, prefix="/api/operations")

    class FakeComms:
        def __init__(self):
            self.sent = []

        async def send_email(self, data):
            self.sent.append(data)
            return {
                "email_id": "email-1",
                "status": "sent",
                "provider": "smtp",
            }

    class FakeEvidence:
        def __init__(self):
            self.calls = []

        async def record_alert_test(self, *, actor, response, dry_run):
            self.calls.append(
                {
                    "actor": actor,
                    "response": response,
                    "dry_run": dry_run,
                }
            )
            return {"id": "evidence-1", "outcome": "success"}

    comms = FakeComms()
    evidence = FakeEvidence()
    app.state.comms_gateway = comms
    app.state.readiness_evidence_service = evidence
    monkeypatch.setattr(
        "cyber_team.api.routes.operations.settings.owner_email",
        "owner@example.com",
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
        "/api/operations/alerts/test-email",
        json={"dry_run": False, "note": "release gate"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["recipient"] == "owner@example.com"
    assert comms.sent[0].to_address == "owner@example.com"
    assert comms.sent[0].agent_id is None
    assert "release gate" in comms.sent[0].body
    assert evidence.calls == [
        {
            "actor": "owner@example.com",
            "response": {
                "email_id": "email-1",
                "status": "sent",
                "provider": "smtp",
            },
            "dry_run": False,
        }
    ]


def test_credential_rotation_evidence_endpoint_records_owner_evidence(monkeypatch):
    app = FastAPI()
    app.include_router(operations_router, prefix="/api/operations")

    class FakeEvidence:
        def __init__(self):
            self.kwargs = None

        async def record_credential_rotation_evidence(self, **kwargs):
            self.kwargs = kwargs
            return {
                "id": "evidence-2",
                "metadata": {"evidence": {"secret_names": ["SMTP_PASSWORD"]}},
            }

    evidence = FakeEvidence()
    app.state.readiness_evidence_service = evidence

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
        "/api/operations/security/credential-rotation/evidence",
        json={
            "scope": "staging",
            "secret_names": ["SMTP_PASSWORD", "not_allowed=value"],
            "evidence_reference": "vault-change-123",
            "note": "Rotated through owner runbook.",
            "rotated_at": "2026-06-23T00:00:00Z",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "recorded"
    assert evidence.kwargs == {
        "actor": "owner@example.com",
        "scope": "staging",
        "secret_names": ["SMTP_PASSWORD", "not_allowed=value"],
        "evidence_reference": "vault-change-123",
        "note": "Rotated through owner runbook.",
        "rotated_at": "2026-06-23T00:00:00Z",
    }


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
