#!/usr/bin/env python3
"""Record a strict, append-only staging health/readiness soak."""

from __future__ import annotations

import argparse
import json
import os
import signal
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def load_env(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Environment file not found: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class Api:
    def __init__(self, base_url: str, timeout_seconds: float):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        token: str = "",
    ) -> tuple[int, dict[str, Any]]:
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, separators=(",", ":")).encode()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                content = response.read().decode("utf-8")
                return response.status, json.loads(content) if content else {}
        except urllib.error.HTTPError as exc:
            content = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(content)
            except json.JSONDecodeError:
                detail = {"detail": content[:500]}
            return exc.code, detail

    def sample(
        self,
        *,
        email: str,
        password: str,
        expected_version: str,
        expected_build_sha: str,
    ) -> dict[str, Any]:
        started = time.monotonic()
        status, health = self.request("GET", "/health")
        health_latency_ms = round((time.monotonic() - started) * 1000, 2)
        health_ok = (
            status == 200
            and health.get("status") == "ok"
            and (not expected_version or health.get("version") == expected_version)
            and (not expected_build_sha or health.get("build_sha") == expected_build_sha)
        )

        login_started = time.monotonic()
        login_status, login = self.request(
            "POST",
            "/api/auth/login",
            payload={"email": email, "password": password},
        )
        login_latency_ms = round((time.monotonic() - login_started) * 1000, 2)
        token = str(login.get("access_token") or "")

        readiness_started = time.monotonic()
        readiness_status, readiness = self.request(
            "GET",
            "/api/operations/readiness",
            token=token,
        )
        readiness_latency_ms = round((time.monotonic() - readiness_started) * 1000, 2)
        blockers = readiness.get("blockers") or []
        autonomy_ok, autonomy = autonomy_gate(readiness)
        readiness_ok = (
            login_status == 200
            and bool(token)
            and readiness_status == 200
            and readiness.get("status") == "ready"
            and not blockers
            and autonomy_ok
        )
        return {
            "sampled_at": utc_now(),
            "status": "passed" if health_ok and readiness_ok else "failed",
            "health": {
                "http_status": status,
                "status": health.get("status"),
                "version": health.get("version"),
                "build_sha": health.get("build_sha"),
                "latency_ms": health_latency_ms,
            },
            "login": {
                "http_status": login_status,
                "latency_ms": login_latency_ms,
            },
            "readiness": {
                "http_status": readiness_status,
                "status": readiness.get("status"),
                "blockers": blockers,
                "latency_ms": readiness_latency_ms,
            },
            "outcome_autonomy": autonomy,
        }


def autonomy_gate(readiness: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Require the complete evidence-to-outcome control loop on every soak sample."""
    autonomous = readiness.get("autonomous_company") or {}
    sections = autonomous.get("sections") or {}
    signals = sections.get("company_signals") or {}
    extraction = sections.get("claim_extraction") or {}
    mandates = sections.get("mandates") or {}
    events = sections.get("business_events") or {}
    portfolio = sections.get("work_portfolio") or {}
    outcomes = sections.get("outcome_learning") or {}
    candidates = sections.get("action_candidates") or {}
    model = sections.get("model_availability") or {}
    capabilities = model.get("capabilities") or {}
    delivery = sections.get("temporal_delivery") or {}
    checks = {
        "autonomous_company": autonomous.get("status") == "ready",
        "signals_finite": signals.get("status") == "ready"
        and signals.get("stale_pending") == 0
        and signals.get("undispositioned_processed") == 0,
        "extraction_bounded": extraction.get("status") == "ready"
        and extraction.get("expired_leases") == 0
        and extraction.get("stale_failed") == 0,
        "mandates_complete": mandates.get("status") == "ready"
        and mandates.get("missing_mandates") == 0,
        "events_finite": events.get("status") == "ready"
        and events.get("stale_unexplained") == 0
        and events.get("unexplained") == 0,
        "portfolio_bounded": portfolio.get("status") in {"ready", "bounded"}
        and not portfolio.get("saturated_domains")
        and not portfolio.get("recovery_required_domains"),
        "outcomes_current": outcomes.get("status") == "ready"
        and outcomes.get("stale_unassessed_work") == 0,
        "model_task_qualified": model.get("status") == "ready"
        and capabilities.get("status") == "ready"
        and capabilities.get("qualified") == capabilities.get("required"),
        "action_candidates_current": candidates.get("stale_proposed") == 0,
        "durable_delivery": delivery.get("status") == "ready",
    }
    return all(checks.values()), {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "signal_counts": signals.get("counts") or {},
        "signal_dispositions": signals.get("disposition_counts") or {},
        "outcome_counts": {
            "terminal_work": outcomes.get("terminal_work"),
            "assessed_work": outcomes.get("assessed_work"),
            "unassessed_work": outcomes.get("unassessed_work"),
            "stale_unassessed_work": outcomes.get("stale_unassessed_work"),
        },
        "action_candidate_counts": candidates.get("counts") or {},
        "model": {
            "provider": model.get("provider"),
            "name": model.get("model"),
            "status": model.get("status"),
            "capability_status": capabilities.get("status"),
            "qualified": capabilities.get("qualified"),
            "required": capabilities.get("required"),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default="deploy/environments/staging.env")
    parser.add_argument("--api-base", default="")
    parser.add_argument("--duration-seconds", type=int, default=86400)
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--output-dir", default="dist/soak")
    parser.add_argument("--expected-version", default="")
    parser.add_argument("--expected-build-sha", default="")
    parser.add_argument("--run-id", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.duration_seconds < 1 or args.interval_seconds < 1:
        raise ValueError("Duration and interval must be positive integers.")
    load_env(Path(args.env_file))
    email = os.environ.get("OWNER_EMAIL", "")
    password = os.environ.get("OWNER_PASSWORD", "")
    if not email or not password:
        raise RuntimeError("OWNER_EMAIL and OWNER_PASSWORD are required.")

    api_base = (
        args.api_base
        or os.environ.get("NEXT_PUBLIC_API_URL")
        or "https://cyberteam.hyperailab.com"
    )
    expected_version = args.expected_version or os.environ.get("APP_VERSION", "")
    run_id = args.run_id or datetime.now(UTC).strftime("staging-soak-%Y%m%dT%H%M%SZ")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    observations_path = output_dir / f"{run_id}.jsonl"
    state_path = output_dir / f"{run_id}.state.json"
    summary_path = output_dir / f"{run_id}.summary.json"
    if any(path.exists() for path in (observations_path, state_path, summary_path)):
        raise FileExistsError(f"Soak evidence already exists for run id {run_id}.")

    stop_requested = False

    def stop(_signum, _frame) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    api = Api(api_base, args.timeout_seconds)
    started_at = utc_now()
    started_monotonic = time.monotonic()
    samples: list[dict[str, Any]] = []
    while True:
        sample_started = time.monotonic()
        try:
            sample = api.sample(
                email=email,
                password=password,
                expected_version=expected_version,
                expected_build_sha=args.expected_build_sha,
            )
        except Exception as exc:  # noqa: BLE001 - evidence must survive probe failures
            sample = {
                "sampled_at": utc_now(),
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
            }
        samples.append(sample)
        with observations_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(sample, separators=(",", ":")) + "\n")

        elapsed = time.monotonic() - started_monotonic
        passed = sum(item["status"] == "passed" for item in samples)
        state = {
            "schema_version": "1.0",
            "run_id": run_id,
            "status": "running",
            "started_at": started_at,
            "updated_at": utc_now(),
            "duration_seconds": args.duration_seconds,
            "interval_seconds": args.interval_seconds,
            "elapsed_seconds": round(elapsed, 2),
            "sample_count": len(samples),
            "passed_samples": passed,
            "failed_samples": len(samples) - passed,
            "expected_version": expected_version,
            "expected_build_sha": args.expected_build_sha,
            "observations_path": str(observations_path),
        }
        atomic_json(state_path, state)
        if stop_requested or elapsed >= args.duration_seconds:
            break
        sleep_for = min(
            args.interval_seconds - (time.monotonic() - sample_started),
            args.duration_seconds - elapsed,
        )
        if sleep_for > 0:
            time.sleep(sleep_for)

    completed_at = utc_now()
    failed_samples = [item for item in samples if item["status"] != "passed"]
    latencies = [
        float(section["latency_ms"])
        for sample in samples
        for section in (
            sample.get("health") or {},
            sample.get("login") or {},
            sample.get("readiness") or {},
        )
        if section.get("latency_ms") is not None
    ]
    summary = {
        **state,
        "status": "interrupted" if stop_requested else ("failed" if failed_samples else "passed"),
        "completed_at": completed_at,
        "failed_samples": len(failed_samples),
        "max_latency_ms": max(latencies) if latencies else None,
        "mean_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "state_path": str(state_path),
        "summary_path": str(summary_path),
    }
    atomic_json(summary_path, summary)
    atomic_json(state_path, summary)
    print(summary_path)
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
