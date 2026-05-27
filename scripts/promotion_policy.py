#!/usr/bin/env python3
"""Promotion manifest, release-check, and approval helpers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_SERVICES = ["postgres", "redis", "qdrant", "temporal", "opa", "core", "worker", "ui"]
DEFAULT_REQUIRED_CHECKS = [
    "quality_gate",
    "migration_rehearsal",
    "compose_smoke",
    "images_built",
    "image_scan",
]


class PromotionPolicyError(RuntimeError):
    """Raised when a promotion policy check fails."""


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise PromotionPolicyError(f"Expected JSON object in {path}")
    return value


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "passed", "pass"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_timestamp(value: str, field_name: str) -> datetime:
    if not value:
        raise PromotionPolicyError(f"{field_name} is required")
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise PromotionPolicyError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _string_list(value: Any, default: list[str]) -> list[str]:
    if value is None:
        return default
    if isinstance(value, str):
        return [part for part in value.split() if part]
    if isinstance(value, list) and all(isinstance(part, str) for part in value):
        return value
    raise PromotionPolicyError("Expected a string list")


def _path_from_config(root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path


def load_deployment_config(
    *,
    root: Path,
    environment: str,
    deployment_manifest: Path | None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    environ = environ or os.environ
    manifest: dict[str, Any] = {}
    if deployment_manifest and deployment_manifest.exists():
        manifest = load_json(deployment_manifest)

    manifest_environment = str(manifest.get("environment", environment))
    if manifest_environment != environment:
        raise PromotionPolicyError(
            f"Deployment manifest environment {manifest_environment!r} does not match {environment!r}"
        )

    env_prefix = environment.upper().replace("-", "_")
    env_file = (
        environ.get("PROMOTE_ENV_FILE")
        or environ.get(f"{env_prefix}_ENV_FILE")
        or manifest.get("env_file")
        or f"deploy/environments/{environment}.env"
    )
    backup_dir = (
        environ.get("BACKUP_DIR")
        or manifest.get("backup_dir")
        or f"backups/{environment}"
    )
    record_dir = (
        environ.get("PROMOTION_RECORD_DIR")
        or manifest.get("promotion_record_dir")
        or f"dist/promotions/{environment}"
    )

    config = {
        "environment": environment,
        "compose_project_name": environ.get("COMPOSE_PROJECT_NAME")
        or manifest.get("compose_project_name")
        or f"cyberteam-{environment}",
        "env_file": str(_path_from_config(root, str(env_file))),
        "backup_dir": str(_path_from_config(root, str(backup_dir))),
        "promotion_record_dir": str(_path_from_config(root, str(record_dir))),
        "require_approval": truthy(
            environ.get("PROMOTION_REQUIRE_APPROVAL", manifest.get("require_approval", False))
        ),
        "run_backup": truthy(environ.get("RUN_BACKUP", manifest.get("run_backup", True))),
        "run_compose_smoke": truthy(
            environ.get("RUN_COMPOSE_SMOKE", manifest.get("run_compose_smoke", True))
        ),
        "required_release_checks": _string_list(
            environ.get("REQUIRED_RELEASE_CHECKS")
            or manifest.get("required_release_checks"),
            DEFAULT_REQUIRED_CHECKS,
        ),
        "services": _string_list(
            environ.get("PROMOTE_SERVICES") or manifest.get("services"),
            DEFAULT_SERVICES,
        ),
    }
    return config


def validate_required_checks(
    release_manifest: dict[str, Any],
    required_checks: list[str],
    *,
    allow_incomplete: bool = False,
) -> None:
    checks = release_manifest.get("checks")
    if not isinstance(checks, dict):
        raise PromotionPolicyError("Release manifest is missing checks")

    failed = [check for check in required_checks if not truthy(checks.get(check))]
    if failed and not allow_incomplete:
        raise PromotionPolicyError(
            "Release manifest is missing required passing checks: " + ", ".join(failed)
        )


def validate_release_manifest(
    *,
    manifest_path: Path,
    required_checks: list[str],
    allow_incomplete_checks: bool = False,
) -> dict[str, Any]:
    release_manifest = load_json(manifest_path)
    for field in ["version", "git_commit", "images"]:
        if field not in release_manifest:
            raise PromotionPolicyError(f"Release manifest is missing {field}")
    images = release_manifest["images"]
    if not isinstance(images, dict) or not images.get("core") or not images.get("ui"):
        raise PromotionPolicyError("Release manifest must include images.core and images.ui")
    validate_required_checks(
        release_manifest,
        required_checks,
        allow_incomplete=allow_incomplete_checks,
    )
    return release_manifest


def approval_from_environment(environ: dict[str, str] | None = None) -> dict[str, str]:
    environ = environ or os.environ
    return {
        "approved_by": environ.get("PROMOTION_APPROVER", ""),
        "change_ticket": environ.get("PROMOTION_CHANGE_TICKET", ""),
        "approved_at": environ.get("PROMOTION_APPROVED_AT", ""),
        "source": "environment",
    }


def validate_approval(
    *,
    release_manifest: dict[str, Any],
    release_manifest_path: Path,
    environment: str,
    require_approval: bool,
    approval_file: Path | None,
    dry_run: bool,
    environ: dict[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    if not require_approval:
        return None
    if dry_run and not approval_file:
        return None

    approval: dict[str, Any]
    if approval_file:
        approval = load_json(approval_file)
        approval["source"] = str(approval_file)
    else:
        approval = approval_from_environment(environ)

    if approval.get("environment") and approval["environment"] != environment:
        raise PromotionPolicyError("Approval environment does not match deployment environment")
    if approval.get("version") and approval["version"] != release_manifest["version"]:
        raise PromotionPolicyError("Approval version does not match release version")
    if not approval.get("approved_by"):
        raise PromotionPolicyError("Promotion approval requires approved_by or PROMOTION_APPROVER")
    if not approval.get("change_ticket"):
        raise PromotionPolicyError(
            "Promotion approval requires change_ticket or PROMOTION_CHANGE_TICKET"
        )
    parse_timestamp(str(approval.get("approved_at", "")), "approved_at")

    expected_digest = approval.get("release_manifest_sha256")
    if expected_digest and expected_digest != sha256_file(release_manifest_path):
        raise PromotionPolicyError("Approval release_manifest_sha256 does not match manifest")

    expires_at = approval.get("expires_at")
    if expires_at:
        current_time = now or datetime.now(UTC)
        if parse_timestamp(str(expires_at), "expires_at") <= current_time:
            raise PromotionPolicyError("Promotion approval has expired")

    return approval


def write_promotion_record(
    *,
    record_dir: Path,
    release_manifest: dict[str, Any],
    release_manifest_path: Path,
    deployment_manifest: Path | None,
    environment: str,
    approval: dict[str, Any] | None,
    compose_project_name: str,
    services: list[str],
    backup_file: str,
    smoke_test: bool,
    dry_run: bool,
    now: datetime | None = None,
) -> Path:
    timestamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    version = str(release_manifest["version"])
    record_path = record_dir / f"{version}-{timestamp}.json"
    if dry_run:
        return record_path

    record_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "environment": environment,
        "version": version,
        "git_commit": release_manifest.get("git_commit"),
        "git_branch": release_manifest.get("git_branch"),
        "images": release_manifest.get("images"),
        "release_manifest": str(release_manifest_path),
        "release_manifest_sha256": sha256_file(release_manifest_path),
        "deployment_manifest": str(deployment_manifest) if deployment_manifest else None,
        "compose_project_name": compose_project_name,
        "services": services,
        "backup_file": backup_file or None,
        "smoke_test": smoke_test,
        "approval": approval,
        "promoted_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    with record_path.open("w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return record_path


def emit_shell(config: dict[str, Any]) -> None:
    fields = {
        "PROMOTE_ENVIRONMENT": config["environment"],
        "COMPOSE_PROJECT_NAME": config["compose_project_name"],
        "PROMOTE_ENV_FILE": config["env_file"],
        "BACKUP_DIR": config["backup_dir"],
        "PROMOTION_RECORD_DIR": config["promotion_record_dir"],
        "PROMOTION_REQUIRE_APPROVAL": "1" if config["require_approval"] else "0",
        "RUN_BACKUP": "1" if config["run_backup"] else "0",
        "RUN_COMPOSE_SMOKE": "1" if config["run_compose_smoke"] else "0",
        "REQUIRED_RELEASE_CHECKS": " ".join(config["required_release_checks"]),
        "PROMOTE_SERVICES": " ".join(config["services"]),
    }
    for key, value in fields.items():
        print(f"{key}={shlex.quote(str(value))}")


def cmd_emit_config(args: argparse.Namespace) -> int:
    config = load_deployment_config(
        root=args.root,
        environment=args.environment,
        deployment_manifest=args.deployment_manifest,
    )
    emit_shell(config)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    config = load_deployment_config(
        root=args.root,
        environment=args.environment,
        deployment_manifest=args.deployment_manifest,
    )
    release_manifest = validate_release_manifest(
        manifest_path=args.release_manifest,
        required_checks=config["required_release_checks"],
        allow_incomplete_checks=args.allow_incomplete_checks,
    )
    validate_approval(
        release_manifest=release_manifest,
        release_manifest_path=args.release_manifest,
        environment=args.environment,
        require_approval=config["require_approval"],
        approval_file=args.approval_file,
        dry_run=args.dry_run,
    )
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    config = load_deployment_config(
        root=args.root,
        environment=args.environment,
        deployment_manifest=args.deployment_manifest,
    )
    release_manifest = validate_release_manifest(
        manifest_path=args.release_manifest,
        required_checks=config["required_release_checks"],
        allow_incomplete_checks=args.allow_incomplete_checks,
    )
    approval = validate_approval(
        release_manifest=release_manifest,
        release_manifest_path=args.release_manifest,
        environment=args.environment,
        require_approval=config["require_approval"],
        approval_file=args.approval_file,
        dry_run=args.dry_run,
    )
    path = write_promotion_record(
        record_dir=Path(config["promotion_record_dir"]),
        release_manifest=release_manifest,
        release_manifest_path=args.release_manifest,
        deployment_manifest=args.deployment_manifest,
        environment=args.environment,
        approval=approval,
        compose_project_name=config["compose_project_name"],
        services=config["services"],
        backup_file=args.backup_file,
        smoke_test=config["run_compose_smoke"],
        dry_run=args.dry_run,
    )
    print(path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--root", type=Path, required=True)
        subparser.add_argument("--environment", required=True)
        subparser.add_argument("--deployment-manifest", type=Path)

    emit_config = subparsers.add_parser("emit-config")
    add_common(emit_config)
    emit_config.set_defaults(func=cmd_emit_config)

    def add_release_common(subparser: argparse.ArgumentParser) -> None:
        add_common(subparser)
        subparser.add_argument("--release-manifest", type=Path, required=True)
        subparser.add_argument("--approval-file", type=Path)
        subparser.add_argument("--allow-incomplete-checks", action="store_true")
        subparser.add_argument("--dry-run", action="store_true")

    validate = subparsers.add_parser("validate")
    add_release_common(validate)
    validate.set_defaults(func=cmd_validate)

    record = subparsers.add_parser("record")
    add_release_common(record)
    record.add_argument("--backup-file", default="")
    record.set_defaults(func=cmd_record)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PromotionPolicyError as exc:
        print(f"Promotion policy error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
