import json
from datetime import UTC, datetime

import pytest

from cyber_team.config import settings
from cyber_team.operations.readiness import ProductionReadinessEvidenceService


class FakeAudit:
    def __init__(self, events=None):
        self.events = events or []
        self.recorded = []

    async def list_events(self, **kwargs):
        return self.events

    async def record_control_evidence(self, **kwargs):
        self.recorded.append(kwargs)
        return {
            "id": f"evidence-{len(self.recorded)}",
            "resource_id": kwargs["control_id"],
            "outcome": kwargs["outcome"],
            "metadata": {
                "control_id": kwargs["control_id"],
                "evidence": kwargs["evidence"],
            },
        }


class FilteredAudit:
    def __init__(self, events):
        self.events = events
        self.calls = []

    async def list_events(self, **kwargs):
        self.calls.append(kwargs)
        resource_id = kwargs.get("resource_id")
        resource_id_prefix = kwargs.get("resource_id_prefix")
        events = self.events
        if resource_id:
            events = [item for item in events if item.get("resource_id") == resource_id]
        if resource_id_prefix:
            events = [
                item for item in events
                if str(item.get("resource_id") or "").startswith(resource_id_prefix)
            ]
        return events[: kwargs.get("limit", 100)]


@pytest.mark.asyncio
async def test_readiness_evidence_reads_fresh_artifacts(tmp_path, monkeypatch):
    now = datetime.now(UTC).isoformat()
    artifacts = {
        "dist/restore-drills/staging/staging-restore-drill-20260623T000000Z.json": {
            "status": "passed",
            "finished_at": now,
            "row_counts": {"agents": 1},
            "qdrant": {
                "restore_status": "ok",
                "source_points_count": 11,
                "restored_points_count": 11,
            },
        },
        "dist/erpnext/restore-drills/erpnext-restore-drill-20260623T000000Z.json": {
            "status": "passed",
            "finished_at": now,
            "row_counts": {"User": 2},
        },
        "dist/load-tests/load-smoke-20260623T000000Z.json": {
            "status": "passed",
            "completed_at": now,
            "p95_ms": 150,
            "failure_rate": 0,
        },
        "dist/business-workflows/business-workflow-smoke-20260623T000000Z.json": {
            "status": "passed",
            "completed_at": now,
            "checks": {"company_context_sync": "passed"},
        },
    }
    for relative_path, payload in artifacts.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        "cyber_team.operations.readiness.settings.environment",
        "staging",
    )

    summary = await ProductionReadinessEvidenceService(
        audit_service=FakeAudit(),
        root_dir=tmp_path,
    ).summary()

    assert summary["backup_restore"]["status"] == "ready"
    assert summary["load_test"]["status"] == "ready"
    assert summary["business_workflow_smoke"]["status"] == "ready"


@pytest.mark.asyncio
async def test_readiness_rejects_restore_artifact_without_qdrant_proof(
    tmp_path,
    monkeypatch,
):
    now = datetime.now(UTC).isoformat()
    artifacts = {
        "dist/restore-drills/staging/staging-restore-drill-now.json": {
            "status": "passed",
            "finished_at": now,
            "row_counts": {"agents": 1},
        },
        "dist/erpnext/restore-drills/erpnext-restore-drill-now.json": {
            "status": "passed",
            "finished_at": now,
            "row_counts": {"User": 2},
        },
    }
    for relative_path, payload in artifacts.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        "cyber_team.operations.readiness.settings.environment",
        "staging",
    )

    summary = await ProductionReadinessEvidenceService(
        audit_service=FakeAudit(),
        root_dir=tmp_path,
    ).summary()

    postgres_qdrant = summary["backup_restore"]["postgres_qdrant"]
    assert summary["backup_restore"]["status"] == "degraded"
    assert postgres_qdrant["status"] == "failed"
    assert postgres_qdrant["qdrant_verified"] is False
    assert "Qdrant" in postgres_qdrant["detail"]


@pytest.mark.asyncio
async def test_readiness_rejects_qdrant_proof_without_point_counts(
    tmp_path,
    monkeypatch,
):
    now = datetime.now(UTC).isoformat()
    artifacts = {
        "dist/restore-drills/staging/staging-restore-drill-now.json": {
            "status": "passed",
            "finished_at": now,
            "qdrant": {"restore_status": "ok"},
        },
        "dist/erpnext/restore-drills/erpnext-restore-drill-now.json": {
            "status": "passed",
            "finished_at": now,
        },
    }
    for relative_path, payload in artifacts.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        "cyber_team.operations.readiness.settings.environment",
        "staging",
    )

    summary = await ProductionReadinessEvidenceService(
        audit_service=FakeAudit(),
        root_dir=tmp_path,
    ).summary()

    postgres_qdrant = summary["backup_restore"]["postgres_qdrant"]
    assert postgres_qdrant["status"] == "failed"
    assert postgres_qdrant["qdrant_verified"] is False


@pytest.mark.asyncio
async def test_readiness_accepts_checksum_verified_concurrent_qdrant_snapshot(
    tmp_path,
    monkeypatch,
):
    now = datetime.now(UTC).isoformat()
    artifacts = {
        "dist/restore-drills/staging/staging-restore-drill-now.json": {
            "status": "passed",
            "finished_at": now,
            "qdrant": {
                "restore_status": "ok",
                "verification_status": "verified",
                "checksum_verified": True,
                "source_points_count": 568,
                "source_points_before_snapshot": 567,
                "source_points_after_snapshot": 568,
                "restored_points_count": 568,
            },
        },
        "dist/erpnext/restore-drills/erpnext-restore-drill-now.json": {
            "status": "passed",
            "finished_at": now,
        },
    }
    for relative_path, payload in artifacts.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        "cyber_team.operations.readiness.settings.environment",
        "staging",
    )

    summary = await ProductionReadinessEvidenceService(
        audit_service=FakeAudit(),
        root_dir=tmp_path,
    ).summary()

    assert summary["backup_restore"]["status"] == "ready"
    assert summary["backup_restore"]["postgres_qdrant"]["qdrant_verified"] is True


@pytest.mark.asyncio
async def test_readiness_evidence_uses_configured_root(tmp_path, monkeypatch):
    now = datetime.now(UTC).isoformat()
    path = tmp_path / "dist/load-tests/load-smoke-20260623T000000Z.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": "passed",
                "completed_at": now,
                "p95_ms": 200,
                "failure_rate": 0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "cyber_team.operations.readiness.settings.readiness_evidence_root",
        str(tmp_path),
    )
    monkeypatch.setattr(
        "cyber_team.operations.readiness.settings.environment",
        "staging",
    )

    summary = await ProductionReadinessEvidenceService(
        audit_service=FakeAudit(),
    ).summary()

    assert summary["load_test"]["status"] == "ready"
    assert summary["load_test"]["evidence_path"] == str(path)


@pytest.mark.asyncio
async def test_readiness_ci_allows_manual_full_ci_while_schedule_pending(tmp_path, monkeypatch):
    path = tmp_path / "dist/ci/github-ci-latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": "ready",
                "checked_at": datetime.now(UTC).isoformat(),
                "repository": "Hyper-AI-Lab/cyber-ai-team",
                "branch": "main",
                "push": {
                    "head_sha": "current",
                    "conclusion": "success",
                    "html_url": "https://example.test/push",
                },
                "manual": {
                    "head_sha": "current",
                    "conclusion": "success",
                    "html_url": "https://example.test/manual",
                },
                "schedule": {
                    "head_sha": "previous",
                    "conclusion": "failure",
                    "html_url": "https://example.test/schedule",
                },
                "schedule_current_head": False,
                "schedule_pending_current_head": True,
                "failing_jobs": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "cyber_team.operations.readiness.settings.environment",
        "staging",
    )

    summary = await ProductionReadinessEvidenceService(
        audit_service=FakeAudit(),
        root_dir=tmp_path,
    ).summary()

    assert summary["ci"]["status"] == "ready"
    assert summary["ci"]["blocking"] is False
    assert summary["ci"]["schedule_pending_current_head"] is True
    assert "scheduled proof is pending" in summary["ci"]["detail"]


@pytest.mark.asyncio
async def test_readiness_ci_accepts_current_manual_when_push_was_skipped(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "dist/ci/github-ci-latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": "ready",
                "checked_at": datetime.now(UTC).isoformat(),
                "repository": "Hyper-AI-Lab/cyber-ai-team",
                "branch": "main",
                "current_head": "current",
                "push": {
                    "head_sha": "previous",
                    "conclusion": "success",
                    "html_url": "https://example.test/push",
                },
                "manual": {
                    "head_sha": "current",
                    "conclusion": "success",
                    "html_url": "https://example.test/manual",
                },
                "schedule": {
                    "head_sha": "previous",
                    "conclusion": "success",
                    "html_url": "https://example.test/schedule",
                },
                "push_current_head": False,
                "schedule_current_head": False,
                "schedule_pending_current_head": True,
                "failing_jobs": [],
                "detail": (
                    "Latest manual full CI run is successful for the current branch head; "
                    "the latest push run is older, skipped, or still pending."
                ),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "cyber_team.operations.readiness.settings.environment",
        "staging",
    )

    summary = await ProductionReadinessEvidenceService(
        audit_service=FakeAudit(),
        root_dir=tmp_path,
    ).summary()

    assert summary["ci"]["status"] == "ready"
    assert summary["ci"]["blocking"] is False
    assert summary["ci"]["current_head"] == "current"
    assert summary["ci"]["push_current_head"] is False
    assert "manual full CI run is successful" in summary["ci"]["detail"]


@pytest.mark.asyncio
async def test_alert_and_credential_evidence_do_not_store_secret_values():
    audit = FakeAudit()
    service = ProductionReadinessEvidenceService(audit_service=audit)

    await service.record_alert_test(
        actor="owner@example.com",
        response={"email_id": "email-1", "status": "sent", "provider": "smtp"},
        dry_run=False,
    )
    await service.record_credential_rotation_evidence(
        actor="owner@example.com",
        scope="staging",
        secret_names=["SMTP_PASSWORD", "SMTP_PASSWORD=secret-value"],
        evidence_reference="vault-change-123",
        note="Rotated by owner.",
        rotated_at="2026-06-23T00:00:00Z",
    )

    assert audit.recorded[0]["control_id"] == "alert_delivery.email"
    assert audit.recorded[0]["evidence"]["response_status"] == "sent"
    assert audit.recorded[1]["control_id"] == "credential_rotation.staging"
    assert audit.recorded[1]["evidence"]["secret_names"] == ["SMTP_PASSWORD"]
    assert "secret-value" not in json.dumps(audit.recorded)


def test_secret_inventory_requires_each_configured_mistral_pool_slot(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "mistral")
    monkeypatch.setattr(settings, "llm_hosted_credential_required_count", 5)
    for index in range(1, 6):
        monkeypatch.setattr(settings, f"mistral_api_key_{index}", f"pool-key-{index}")

    inventory = ProductionReadinessEvidenceService(
        audit_service=FakeAudit()
    )._secret_inventory()
    pool_checks = [item for item in inventory if item.name.startswith("MISTRAL_API_KEY_")]

    assert [item.name for item in pool_checks] == [
        "MISTRAL_API_KEY_1",
        "MISTRAL_API_KEY_2",
        "MISTRAL_API_KEY_3",
        "MISTRAL_API_KEY_4",
        "MISTRAL_API_KEY_5",
    ]
    assert all(item.required and item.configured for item in pool_checks)


@pytest.mark.asyncio
async def test_alert_evidence_is_not_evicted_by_unrelated_audit_volume(
    tmp_path,
    monkeypatch,
):
    now = datetime.now(UTC).isoformat()
    alert = {
        "id": "alert-proof",
        "event_type": "control.evidence",
        "resource_type": "control",
        "resource_id": "alert_delivery.email",
        "outcome": "success",
        "created_at": now,
        "metadata": {
            "control_id": "alert_delivery.email",
            "evidence": {"response_status": "sent", "provider": "smtp"},
        },
    }
    noise = [
        {
            "id": f"noise-{index}",
            "event_type": "control.evidence",
            "resource_type": "control",
            "resource_id": f"autonomy.cycle.{index}",
            "outcome": "success",
            "created_at": now,
            "metadata": {},
        }
        for index in range(500)
    ]
    audit = FilteredAudit(noise + [alert])
    monkeypatch.setattr(
        "cyber_team.operations.readiness.settings.environment",
        "staging",
    )

    summary = await ProductionReadinessEvidenceService(
        audit_service=audit,
        root_dir=tmp_path,
    ).summary()

    assert summary["alerts"]["status"] == "ready"
    assert summary["alerts"]["last_delivery_test"] == now
    assert any(
        call.get("resource_id") == "alert_delivery.email" and call.get("limit") == 1
        for call in audit.calls
    )
