#!/usr/bin/env python3
"""Record latest GitHub Actions push and scheduled CI evidence."""

from __future__ import annotations

import json
import os
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


def github_get(path: str, token: str) -> dict:
    url = f"https://api.github.com{path}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "cyber-team-readiness-evidence",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def latest_run(
    repository: str,
    token: str,
    event: str,
    branch: str,
) -> tuple[dict | None, list[dict]]:
    query = urllib.parse.urlencode(
        {
            "branch": branch,
            "event": event,
            "per_page": 1,
        }
    )
    payload = github_get(f"/repos/{repository}/actions/runs?{query}", token)
    runs = payload.get("workflow_runs") or []
    if not runs:
        return None, []
    run = runs[0]
    failing_jobs = []
    if run.get("conclusion") not in {None, "success"}:
        jobs = github_get(
            f"/repos/{repository}/actions/runs/{run['id']}/jobs?per_page=100",
            token,
        )
        failing_jobs = [
            {
                "name": job.get("name"),
                "status": job.get("status"),
                "conclusion": job.get("conclusion"),
                "html_url": job.get("html_url"),
            }
            for job in jobs.get("jobs", [])
            if job.get("conclusion") not in {None, "success", "skipped"}
        ]
    return (
        {
            "id": run.get("id"),
            "name": run.get("name"),
            "event": run.get("event"),
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
            "head_sha": run.get("head_sha"),
            "html_url": run.get("html_url"),
        },
        failing_jobs,
    )


def branch_head(repository: str, token: str, branch: str) -> str | None:
    payload = github_get(
        f"/repos/{repository}/branches/{urllib.parse.quote(branch, safe='')}",
        token,
    )
    commit = payload.get("commit") or {}
    sha = commit.get("sha")
    return str(sha) if sha else None


def write_evidence(payload: dict) -> Path:
    evidence_dir = Path(os.environ.get("CI_EVIDENCE_DIR", ROOT / "dist/ci"))
    evidence_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = evidence_dir / f"github-ci-{timestamp}.json"
    latest = evidence_dir / "github-ci-latest.json"
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.write_text(content, encoding="utf-8")
    latest.write_text(content, encoding="utf-8")
    return path


def main() -> int:
    env_file = Path(os.environ.get("CYBERTEAM_ENV_FILE", ROOT / "deploy/environments/staging.env"))
    load_env(env_file)
    token = os.environ.get("GITHUB_TOKEN", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    branch = os.environ.get("GITHUB_DEFAULT_REF", "main")
    checked_at = datetime.now(UTC).isoformat()
    payload = {
        "status": "configuration_required",
        "checked_at": checked_at,
        "repository": repository,
        "branch": branch,
        "push": None,
        "schedule": None,
        "manual": None,
        "failing_jobs": [],
    }
    if not token or not repository:
        payload["detail"] = "GITHUB_TOKEN and GITHUB_REPOSITORY are required."
        path = write_evidence(payload)
        print(f"GitHub CI evidence recorded: {path}")
        return 1 if os.environ.get("CI_EVIDENCE_REQUIRE_READY") == "1" else 0
    try:
        current_head = branch_head(repository, token, branch)
        push, push_failures = latest_run(repository, token, "push", branch)
        schedule, schedule_failures = latest_run(repository, token, "schedule", branch)
        manual, manual_failures = latest_run(repository, token, "workflow_dispatch", branch)
    except urllib.error.HTTPError as exc:
        payload.update({"status": "failed", "detail": f"GitHub API error: {exc.code}"})
        path = write_evidence(payload)
        print(f"GitHub CI evidence recorded: {path}")
        return 1
    except Exception as exc:
        payload.update({"status": "failed", "detail": str(exc)})
        path = write_evidence(payload)
        print(f"GitHub CI evidence recorded: {path}")
        return 1

    current_head = current_head or (push.get("head_sha") if push else None)
    push_current = bool(push and push.get("head_sha") == current_head)
    push_success = bool(push_current and push.get("conclusion") == "success")
    schedule_current = bool(schedule and schedule.get("head_sha") == current_head)
    schedule_success = bool(schedule_current and schedule.get("conclusion") == "success")
    manual_success = bool(
        manual
        and manual.get("head_sha") == current_head
        and manual.get("conclusion") == "success"
    )
    schedule_pending_current_head = bool(
        (push_success or manual_success) and not schedule_success
    )
    ready = manual_success or (push_success and schedule_success)
    failing_jobs = (
        [
            *(push_failures if push_current else []),
            *manual_failures,
        ]
        if schedule_pending_current_head
        else [
            *(push_failures if push_current else []),
            *(schedule_failures if schedule_current else []),
            *manual_failures,
        ]
    )
    detail = "Latest manual full CI run is successful for the current branch head."
    if push_success and schedule_success:
        detail = "Latest push and scheduled CI runs are successful for the current head."
    elif push_success and manual_success and schedule_pending_current_head:
        detail = (
            "Latest push and manual full CI runs are successful; the next scheduled "
            "run for this head is pending GitHub's cron."
        )
    elif manual_success and not push_success:
        detail = (
            "Latest manual full CI run is successful for the current branch head; "
            "the latest push run is older, skipped, or still pending."
        )
    elif not ready:
        detail = "Latest push or scheduled/manual CI run is missing or unsuccessful."
    payload.update(
        {
            "status": "ready" if ready else "degraded",
            "current_head": current_head,
            "push": push,
            "schedule": schedule,
            "manual": manual,
            "push_current_head": push_current,
            "schedule_current_head": schedule_current,
            "schedule_pending_current_head": schedule_pending_current_head,
            "failing_jobs": failing_jobs,
            "detail": detail,
        }
    )
    path = write_evidence(payload)
    print(f"GitHub CI evidence recorded: {path}")
    return 0 if ready or os.environ.get("CI_EVIDENCE_REQUIRE_READY") != "1" else 1


if __name__ == "__main__":
    raise SystemExit(main())
