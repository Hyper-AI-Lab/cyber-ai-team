import json
from datetime import UTC, datetime

import pytest

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


@pytest.mark.asyncio
async def test_readiness_evidence_reads_fresh_artifacts(tmp_path, monkeypatch):
    now = datetime.now(UTC).isoformat()
    artifacts = {
        "dist/restore-drills/staging/staging-restore-drill-20260623T000000Z.json": {
            "status": "passed",
            "finished_at": now,
            "row_counts": {"agents": 1},
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
                "repository": "Hyper-AI-Lab/cyber-team",
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
