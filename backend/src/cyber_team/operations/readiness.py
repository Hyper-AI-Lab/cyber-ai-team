"""Production-readiness evidence aggregation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from cyber_team.clock import utc_now
from cyber_team.config import settings


def _default_root_dir() -> Path:
    if settings.readiness_evidence_root.strip():
        return Path(settings.readiness_evidence_root).expanduser()
    path = Path(__file__).resolve()
    for parent in path.parents:
        if (parent / "backend").exists() and (parent / "frontend").exists():
            return parent
    return path.parents[4]


@dataclass(frozen=True)
class SecretCheck:
    name: str
    configured: bool
    placeholder: bool
    required: bool
    category: str


class ProductionReadinessEvidenceService:
    """Build owner-console readiness facts from evidence and configuration."""

    RESTORE_STALE_DAYS = 30
    LOAD_STALE_DAYS = 30
    ALERT_STALE_DAYS = 30
    WORKFLOW_STALE_DAYS = 30
    ROTATION_STALE_DAYS = 90

    def __init__(self, audit_service=None, root_dir: Path | None = None) -> None:
        self._audit = audit_service
        self._root = root_dir or _default_root_dir()

    async def summary(self) -> dict[str, Any]:
        evidence = await self._control_evidence()
        return {
            "ci": self._ci_status(evidence),
            "alerts": self._alert_status(evidence),
            "backup_restore": self._backup_restore_status(),
            "credential_rotation": self._credential_rotation_status(evidence),
            "load_test": self._load_test_status(),
            "business_workflow_smoke": self._business_workflow_status(),
        }

    async def record_alert_test(
        self,
        *,
        actor: str,
        response: dict[str, Any],
        dry_run: bool,
    ) -> dict[str, Any] | None:
        if not self._audit:
            return None
        status = response.get("status") or "unknown"
        outcome = "success" if dry_run or status == "sent" else "failed"
        return await self._audit.record_control_evidence(
            control_id="alert_delivery.email",
            control_area="operations_alerting",
            actor=actor,
            outcome=outcome,
            evidence={
                "primary_channel": "email",
                "dry_run": dry_run,
                "response_status": status,
                "provider": response.get("provider"),
                "email_id": response.get("email_id"),
                "idempotent_replay": response.get("idempotent_replay", False),
            },
        )

    async def record_credential_rotation_evidence(
        self,
        *,
        actor: str,
        scope: str,
        secret_names: list[str],
        evidence_reference: str,
        note: str,
        rotated_at: str | None = None,
    ) -> dict[str, Any]:
        if not self._audit:
            return {
                "status": "unavailable",
                "detail": "Audit service is not available.",
            }
        sanitized_names = sorted(
            {
                name.strip().upper()
                for name in secret_names
                if name.strip() and "=" not in name and len(name.strip()) <= 100
            }
        )
        return await self._audit.record_control_evidence(
            control_id=f"credential_rotation.{scope}",
            control_area="security_credential_rotation",
            actor=actor,
            outcome="success",
            evidence={
                "scope": scope,
                "secret_names": sanitized_names,
                "evidence_reference": evidence_reference,
                "note": note,
                "rotated_at": rotated_at,
            },
        )

    async def _control_evidence(self) -> list[dict[str, Any]]:
        if not self._audit:
            return []
        return await self._audit.list_events(event_type="control.evidence", limit=200)

    def _ci_status(self, evidence: list[dict[str, Any]]) -> dict[str, Any]:
        latest = self._latest_json(["dist/ci/github-ci-latest.json"])
        event = self._latest_control(evidence, "ci.github_actions")
        payload = latest or self._control_payload(event)
        status = payload.get("status") if payload else "not_recorded"
        push = payload.get("push") if payload else None
        scheduled = payload.get("schedule") if payload else None
        manual = payload.get("manual") if payload else None
        schedule_pending = bool(payload.get("schedule_pending_current_head")) if payload else False
        current_head = payload.get("current_head") if payload else None
        if push and scheduled:
            current_head = current_head or push.get("head_sha")
            push_current = push.get("head_sha") == current_head
            schedule_current = scheduled.get("head_sha") == current_head
            manual_current = bool(manual and manual.get("head_sha") == current_head)
            push_success = push_current and push.get("conclusion") == "success"
            schedule_success = (
                schedule_current and scheduled.get("conclusion") == "success"
            )
            manual_success = bool(
                manual
                and manual_current
                and manual.get("conclusion") == "success"
            )
            status = (
                "ready"
                if manual_success or (push_success and schedule_success)
                else "degraded"
            )
        blocking = self._proof_required() and status != "ready"
        detail = "GitHub CI evidence has not been recorded or is not successful."
        if status == "ready":
            if payload and payload.get("detail") and not payload.get("push_current_head", True):
                detail = payload["detail"]
            elif schedule_pending:
                detail = (
                    "Latest push and manual full CI evidence is successful; "
                    "scheduled proof is pending the next GitHub cron."
                )
            elif payload and payload.get("detail"):
                detail = payload["detail"]
            else:
                detail = "Latest push and scheduled CI evidence is successful."
        return {
            "status": status,
            "blocking": blocking,
            "checked_at": payload.get("checked_at") if payload else None,
            "repository": payload.get("repository") if payload else settings.github_repository,
            "push": push,
            "schedule": scheduled,
            "manual": manual,
            "current_head": current_head,
            "push_current_head": payload.get("push_current_head") if payload else None,
            "schedule_current_head": payload.get("schedule_current_head") if payload else None,
            "schedule_pending_current_head": schedule_pending,
            "failing_jobs": payload.get("failing_jobs", []) if payload else [],
            "evidence_path": latest.get("_path") if latest else None,
            "detail": detail,
        }

    def _alert_status(self, evidence: list[dict[str, Any]]) -> dict[str, Any]:
        event = self._latest_control(evidence, "alert_delivery.email")
        payload = self._control_payload(event)
        stale = self._is_stale(event.get("created_at") if event else None, self.ALERT_STALE_DAYS)
        ready = bool(
            event
            and event.get("outcome") == "success"
            and payload.get("response_status") in {"sent", "simulated"}
            and not stale
        )
        return {
            "status": "ready" if ready else ("stale" if stale else "not_recorded"),
            "primary_channel": "email",
            "blocking": self._proof_required() and not ready,
            "last_delivery_test": event.get("created_at") if event else None,
            "stale": stale,
            "response_status": payload.get("response_status"),
            "provider": payload.get("provider") or "smtp",
            "detail": (
                "Owner email alert delivery has recent evidence."
                if ready
                else "Run the owner alert email test to prove alert delivery."
            ),
        }

    def _backup_restore_status(self) -> dict[str, Any]:
        postgres = self._artifact_status(
            name="postgres_qdrant",
            patterns=["dist/restore-drills/staging/staging-restore-drill-*.json"],
            stale_days=self.RESTORE_STALE_DAYS,
        )
        erpnext = self._artifact_status(
            name="erpnext",
            patterns=["dist/erpnext/restore-drills/erpnext-restore-drill-*.json"],
            stale_days=self.RESTORE_STALE_DAYS,
        )
        ready = postgres["status"] == "ready" and erpnext["status"] == "ready"
        return {
            "status": "ready" if ready else "degraded",
            "blocking": self._proof_required() and not ready,
            "stale_after_days": self.RESTORE_STALE_DAYS,
            "postgres_qdrant": postgres,
            "erpnext": erpnext,
            "detail": (
                "PostgreSQL/Qdrant and ERPNext restore drills are fresh."
                if ready
                else "Fresh PostgreSQL/Qdrant and ERPNext restore-drill evidence is required."
            ),
        }

    def _credential_rotation_status(self, evidence: list[dict[str, Any]]) -> dict[str, Any]:
        inventory = self._secret_inventory()
        missing = [item.name for item in inventory if item.required and not item.configured]
        placeholders = [item.name for item in inventory if item.required and item.placeholder]
        event = self._latest_control_prefix(evidence, "credential_rotation.")
        stale = self._is_stale(
            event.get("created_at") if event else None,
            self.ROTATION_STALE_DAYS,
        )
        blockers = [
            {"secret_name": name, "reason": "missing"}
            for name in missing
        ] + [
            {"secret_name": name, "reason": "placeholder"}
            for name in placeholders
        ]
        if blockers:
            status = "blocked"
        elif not event:
            status = "review_required"
        elif stale:
            status = "stale"
        else:
            status = "ready"
        return {
            "status": status,
            "blocking": self._proof_required() and bool(blockers),
            "stale": stale,
            "stale_after_days": self.ROTATION_STALE_DAYS,
            "last_evidence_at": event.get("created_at") if event else None,
            "inventory": [
                {
                    "name": item.name,
                    "configured": item.configured,
                    "placeholder": item.placeholder,
                    "required": item.required,
                    "category": item.category,
                }
                for item in inventory
            ],
            "blockers": blockers,
            "detail": (
                "Required secrets are configured and have recent rotation evidence."
                if status == "ready"
                else "Record credential rotation evidence after operator-managed secret rotation."
            ),
        }

    def _load_test_status(self) -> dict[str, Any]:
        return self._artifact_status(
            name="conservative_load",
            patterns=["dist/load-tests/load-smoke-*.json"],
            stale_days=self.LOAD_STALE_DAYS,
        )

    def _business_workflow_status(self) -> dict[str, Any]:
        return self._artifact_status(
            name="business_workflow_smoke",
            patterns=["dist/business-workflows/business-workflow-smoke-*.json"],
            stale_days=self.WORKFLOW_STALE_DAYS,
        )

    def _artifact_status(
        self,
        *,
        name: str,
        patterns: list[str],
        stale_days: int,
    ) -> dict[str, Any]:
        payload = self._latest_json(patterns)
        if not payload:
            return {
                "name": name,
                "status": "not_recorded",
                "blocking": self._proof_required(),
                "stale": True,
                "evidence_path": None,
                "detail": f"No {name} evidence artifact was found.",
            }
        timestamp = (
            payload.get("finished_at")
            or payload.get("completed_at")
            or payload.get("checked_at")
            or payload.get("started_at")
        )
        stale = self._is_stale(timestamp, stale_days)
        passed = payload.get("status") in {"passed", "ready", "success"}
        return {
            "name": name,
            "status": "ready" if passed and not stale else ("stale" if stale else "failed"),
            "blocking": self._proof_required() and (not passed or stale),
            "stale": stale,
            "stale_after_days": stale_days,
            "last_run_at": timestamp,
            "evidence_path": payload.get("_path"),
            "summary": self._artifact_summary(payload),
            "detail": (
                f"{name} evidence is fresh and passing."
                if passed and not stale
                else f"{name} evidence is missing, stale, or failed."
            ),
        }

    def _latest_json(self, patterns: list[str]) -> dict[str, Any] | None:
        candidates: list[Path] = []
        for pattern in patterns:
            candidates.extend(self._root.glob(pattern))
        existing = [path for path in candidates if path.is_file()]
        if not existing:
            return None
        latest = max(existing, key=lambda path: path.stat().st_mtime)
        try:
            payload = json.loads(latest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {
                "status": "failed",
                "_path": str(latest),
                "detail": "Evidence artifact could not be parsed.",
            }
        if isinstance(payload, dict):
            payload["_path"] = str(latest)
            return payload
        return {"status": "failed", "_path": str(latest)}

    @staticmethod
    def _latest_control(
        evidence: list[dict[str, Any]],
        control_id: str,
    ) -> dict[str, Any] | None:
        return next(
            (
                event for event in evidence
                if event.get("resource_id") == control_id
                or event.get("metadata", {}).get("control_id") == control_id
            ),
            None,
        )

    @staticmethod
    def _latest_control_prefix(
        evidence: list[dict[str, Any]],
        prefix: str,
    ) -> dict[str, Any] | None:
        return next(
            (
                event for event in evidence
                if str(event.get("resource_id") or "").startswith(prefix)
                or str(event.get("metadata", {}).get("control_id") or "").startswith(prefix)
            ),
            None,
        )

    @staticmethod
    def _control_payload(event: dict[str, Any] | None) -> dict[str, Any]:
        if not event:
            return {}
        return event.get("metadata", {}).get("evidence") or {}

    @staticmethod
    def _artifact_summary(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in payload.items()
            if key
            in {
                "status",
                "duration_seconds",
                "backup_size_bytes",
                "row_counts",
                "p95_ms",
                "failure_rate",
                "checks",
                "repository",
                "workflow",
            }
        }

    @staticmethod
    def _is_stale(value: str | None, days: int) -> bool:
        if not value:
            return True
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return True
        if parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None)
        return parsed < utc_now() - timedelta(days=days)

    @staticmethod
    def _proof_required() -> bool:
        return settings.environment.lower() in {"staging", "production"}

    @staticmethod
    def _placeholder(value: str) -> bool:
        lowered = value.lower()
        return (
            not value
            or "changeme" in lowered
            or "replace-with" in lowered
            or lowered.startswith("your-")
        )

    def _secret_inventory(self) -> list[SecretCheck]:
        required = settings.required_provider_names
        checks = [
            self._secret("SECRET_KEY", settings.secret_key, True, "runtime"),
            self._secret(
                "OWNER_PASSWORD_HASH",
                settings.owner_password_hash,
                settings.environment.lower() == "production",
                "auth",
            ),
            self._secret(
                "OWNER_PASSWORD",
                settings.owner_password,
                settings.environment.lower() != "production",
                "auth",
            ),
            self._secret("POSTGRES_PASSWORD", settings.postgres_password, True, "datastore"),
            self._secret("REDIS_PASSWORD", settings.redis_password, True, "datastore"),
            self._secret(
                "ERPNEXT_ADMIN_PASSWORD",
                settings.erpnext_admin_password,
                "erpnext" in required,
                "erpnext",
            ),
            self._secret(
                "ERPNEXT_MARIADB_ROOT_PASSWORD",
                settings.erpnext_mariadb_root_password,
                "erpnext" in required,
                "erpnext",
            ),
            self._secret(
                "ERPNEXT_DB_PASSWORD",
                settings.erpnext_db_password,
                "erpnext" in required,
                "erpnext",
            ),
            self._secret(
                "ERPNEXT_API_KEY",
                settings.erpnext_api_key,
                "erpnext" in required,
                "erpnext",
            ),
            self._secret(
                "ERPNEXT_API_SECRET",
                settings.erpnext_api_secret,
                "erpnext" in required,
                "erpnext",
            ),
            self._secret(
                "SMTP_PASSWORD",
                settings.smtp_password,
                "smtp" in required,
                "email",
            ),
            self._secret(
                "IMAP_PASSWORD",
                settings.imap_password,
                "imap" in required,
                "email",
            ),
            self._secret("MISTRAL_API_KEY", settings.mistral_api_key, False, "llm"),
            self._secret("GITHUB_TOKEN", settings.github_token, "github" in required, "devops"),
        ]
        return checks

    def _secret(
        self,
        name: str,
        value: str | bool,
        required: bool,
        category: str,
    ) -> SecretCheck:
        string_value = str(value or "")
        configured = bool(value)
        return SecretCheck(
            name=name,
            configured=configured,
            placeholder=self._placeholder(string_value) if configured else False,
            required=required,
            category=category,
        )
