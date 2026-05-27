import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "promotion_policy.py"
SPEC = importlib.util.spec_from_file_location("promotion_policy", SCRIPT_PATH)
promotion_policy = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(promotion_policy)


def release_manifest(**checks):
    return {
        "version": "2026.05.27-1",
        "git_commit": "abc123",
        "git_branch": "main",
        "checks": {
            "quality_gate": "1",
            "migration_rehearsal": "1",
            "compose_smoke": "1",
            "images_built": "1",
            "image_scan": "1",
            **checks,
        },
        "images": {
            "core": "cyber-team-core:2026.05.27-1",
            "ui": "cyber-team-ui:2026.05.27-1",
        },
    }


def write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_required_release_checks_block_incomplete_manifest():
    manifest = release_manifest(compose_smoke="0")

    with pytest.raises(promotion_policy.PromotionPolicyError, match="compose_smoke"):
        promotion_policy.validate_required_checks(
            manifest,
            ["quality_gate", "compose_smoke"],
        )


def test_dry_run_does_not_require_production_approval_without_file(tmp_path):
    manifest_path = tmp_path / "release.json"
    manifest = release_manifest()
    write_json(manifest_path, manifest)

    approval = promotion_policy.validate_approval(
        release_manifest=manifest,
        release_manifest_path=manifest_path,
        environment="production",
        require_approval=True,
        approval_file=None,
        dry_run=True,
    )

    assert approval is None


def test_production_approval_file_must_match_release_digest(tmp_path):
    manifest_path = tmp_path / "release.json"
    manifest = release_manifest()
    write_json(manifest_path, manifest)
    approval_path = tmp_path / "approval.json"
    write_json(
        approval_path,
        {
            "environment": "production",
            "version": "2026.05.27-1",
            "approved_by": "ops@example.com",
            "change_ticket": "CHG-123",
            "approved_at": "2026-05-27T12:00:00Z",
            "release_manifest_sha256": "wrong-digest",
        },
    )

    with pytest.raises(promotion_policy.PromotionPolicyError, match="sha256"):
        promotion_policy.validate_approval(
            release_manifest=manifest,
            release_manifest_path=manifest_path,
            environment="production",
            require_approval=True,
            approval_file=approval_path,
            dry_run=False,
        )


def test_write_promotion_record_contains_release_and_approval(tmp_path):
    manifest_path = tmp_path / "release.json"
    manifest = release_manifest()
    write_json(manifest_path, manifest)
    approved_at = datetime.now(UTC).replace(microsecond=0)
    approval = {
        "approved_by": "ops@example.com",
        "change_ticket": "CHG-456",
        "approved_at": approved_at.isoformat().replace("+00:00", "Z"),
    }

    record_path = promotion_policy.write_promotion_record(
        record_dir=tmp_path / "records",
        release_manifest=manifest,
        release_manifest_path=manifest_path,
        deployment_manifest=tmp_path / "production.json",
        environment="production",
        approval=approval,
        compose_project_name="cyberteam-production",
        services=["postgres", "core", "ui"],
        backup_file="/backups/cyberteam.dump",
        smoke_test=True,
        dry_run=False,
        now=approved_at + timedelta(minutes=1),
    )

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["environment"] == "production"
    assert record["version"] == "2026.05.27-1"
    assert record["release_manifest_sha256"] == promotion_policy.sha256_file(manifest_path)
    assert record["approval"]["change_ticket"] == "CHG-456"
    assert record["services"] == ["postgres", "core", "ui"]
