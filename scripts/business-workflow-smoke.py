#!/usr/bin/env python3
"""Safe staging business workflow smoke for Cyber-Team + ERPNext."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.token = ""

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read().decode("utf-8")
                return response.status, json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = {"detail": body}
            return exc.code, parsed

    def login(self, email: str, password: str) -> None:
        status, payload = self.request(
            "POST",
            "/api/auth/login",
            {"email": email, "password": password},
        )
        if status != 200 or not payload.get("access_token"):
            raise RuntimeError(f"Owner login failed: {status} {payload}")
        self.token = payload["access_token"]


def assert_check(checks: dict[str, str], name: str, condition: bool, detail: str = "") -> None:
    checks[name] = "passed" if condition else f"failed: {detail}"
    if not condition:
        raise RuntimeError(f"{name} failed: {detail}")


def write_evidence(payload: dict) -> Path:
    evidence_dir = Path(
        os.environ.get("BUSINESS_SMOKE_EVIDENCE_DIR", ROOT / "dist/business-workflows")
    )
    evidence_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = evidence_dir / f"business-workflow-smoke-{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main() -> int:
    env_file = Path(os.environ.get("CYBERTEAM_ENV_FILE", ROOT / "deploy/environments/staging.env"))
    load_env(env_file)
    api_base = os.environ.get("API_BASE") or os.environ.get(
        "NEXT_PUBLIC_API_URL",
        "https://cyberteam.hyperailab.com",
    )
    owner_email = os.environ.get("OWNER_EMAIL", "")
    owner_password = os.environ.get("OWNER_PASSWORD", "")
    if not owner_email or not owner_password:
        print("OWNER_EMAIL and OWNER_PASSWORD are required.", file=sys.stderr)
        return 1

    started_at = datetime.now(UTC).isoformat()
    checks: dict[str, str] = {}
    payload = {
        "status": "failed",
        "started_at": started_at,
        "completed_at": None,
        "api_base": api_base,
        "checks": checks,
    }
    try:
        api = Api(api_base)
        status, health = api.request("GET", "/health")
        assert_check(checks, "health", status == 200 and health.get("status") == "ok", str(health))
        api.login(owner_email, owner_password)
        checks["owner_login"] = "passed"

        status, integrations = api.request("GET", "/api/integrations/status")
        assert_check(checks, "integrations_status", status == 200, str(integrations))
        erpnext_mode = integrations.get("erpnext", {}).get("mode")
        assert_check(checks, "erpnext_readiness", erpnext_mode == "live", str(erpnext_mode))

        status, sync = api.request(
            "POST",
            "/api/operations/company-context/sync",
            {"dry_run": True, "apply_low_risk": False, "run_planner": False, "source": "erpnext"},
        )
        assert_check(checks, "company_context_sync_dry_run", status == 200, str(sync))

        status, role_summary = api.request(
            "GET",
            "/api/roles/role-gaps/summary?status=open,proposed&limit=25",
        )
        assert_check(checks, "role_backlog_summary", status == 200, str(role_summary))

        status, notify = api.request(
            "POST",
            "/api/operations/owner-attention/notify",
            {"dry_run": True, "limit": 25},
        )
        assert_check(checks, "owner_attention_notify_dry_run", status == 200, str(notify))

        status, blocked = api.request(
            "POST",
            "/api/tools/execute",
            {
                "tool_name": "send_email",
                "params": {
                    "to_address": owner_email,
                    "subject": "Should not send",
                    "body": "Invalid approval smoke.",
                    "_approval_id": "stale-approval-smoke",
                },
            },
        )
        blocked_safely = status in {200, 400} and (
            blocked.get("approval_required")
            or blocked.get("blocked")
            or blocked.get("success") is False
            or "approval" in json.dumps(blocked).lower()
        )
        assert_check(checks, "invalid_approval_blocks_side_effect", blocked_safely, str(blocked))
        payload["status"] = "passed"
    except Exception as exc:
        payload["error"] = str(exc)
    finally:
        payload["completed_at"] = datetime.now(UTC).isoformat()
        path = write_evidence(payload)
        print(f"Business workflow smoke evidence: {path}")
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
