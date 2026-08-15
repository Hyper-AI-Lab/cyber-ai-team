#!/usr/bin/env python3
"""Focused staging acceptance for the evidence-to-outcome autonomy chain."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TERMINAL_WORK = {"completed", "failed", "blocked", "cancelled"}


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
    def __init__(self, base: str, *, timeout_seconds: float = 900.0) -> None:
        self.base = base.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.token = ""

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any] | list[Any]]:
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=body,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                content = response.read().decode()
                return response.status, json.loads(content) if content else {}
        except urllib.error.HTTPError as exc:
            content = exc.read().decode()
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = {"detail": content}
            return exc.code, parsed

    def login(self, email: str, password: str) -> None:
        status, result = self.request(
            "POST",
            "/api/auth/login",
            {"email": email, "password": password},
        )
        token = result.get("access_token") if isinstance(result, dict) else None
        if status != 200 or not token:
            raise RuntimeError(f"Owner login failed: HTTP {status}")
        self.token = str(token)


def require(checks: dict[str, Any], key: str, condition: bool, detail: Any) -> None:
    checks[key] = {"status": "passed" if condition else "failed", "detail": detail}
    if not condition:
        raise RuntimeError(f"{key} failed: {detail}")


def mandate_items(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return list(payload.get("items") or payload.get("mandates") or [])


def choose_agent(
    mandates: list[dict[str, Any]],
    *,
    tool_name: str,
    preferred_agent: str,
) -> str:
    eligible = [
        item
        for item in mandates
        if item.get("status") == "active"
        and tool_name in ((item.get("authority") or {}).get("read_tools") or [])
    ]
    preferred = next(
        (item for item in eligible if item.get("agent_id") == preferred_agent),
        None,
    )
    selected = preferred or (eligible[0] if eligible else None)
    if not selected:
        raise RuntimeError(f"No active mandate grants {tool_name}")
    return str(selected["agent_id"])


def typed_candidate_description(candidate: dict[str, Any]) -> str:
    return (
        "Evaluate this staging acceptance evidence against the active mandate. Return a "
        "grounded assessment and exactly one proposed_work item with work_type "
        "action_candidate. Copy the following typed action_candidate exactly; it is a "
        "proposal for deterministic Observer and policy review, not permission to "
        "execute: "
        + json.dumps(candidate, sort_keys=True, separators=(",", ":"))
    )


def create_candidate_work(
    api: Api,
    *,
    run_key: str,
    suffix: str,
    agent_id: str,
    candidate: dict[str, Any],
    risk_level: str,
) -> str:
    evidence_id = candidate["evidence_ids"][0]
    status, result = api.request(
        "POST",
        "/api/operations/work-items",
        {
            "title": f"Outcome autonomy {suffix} acceptance {run_key}",
            "description": typed_candidate_description(candidate),
            "work_type": "domain_assessment",
            "assigned_agent_id": agent_id,
            "payload": {
                "source_id": evidence_id,
                "evidence_ids": [evidence_id],
                "acceptance_run": run_key,
                "external_side_effects": False,
            },
            "acceptance_criteria": [
                "typed_action_candidate_recorded",
                "observer_review_recorded",
                "policy_decision_recorded",
            ],
            "priority": "low" if risk_level == "low" else "medium",
            "risk_level": risk_level,
            "idempotency_key": f"outcome-autonomy:{run_key}:{suffix}",
        },
    )
    if status != 200 or not isinstance(result, dict) or not result.get("id"):
        raise RuntimeError(f"Could not create {suffix} work: HTTP {status} {result}")
    return str(result["id"])


def run_agent_once(api: Api, agent_id: str) -> dict[str, Any]:
    status, result = api.request(
        "POST",
        "/api/operations/domain-loops/run",
        {"agent_id": agent_id, "max_items": 1},
    )
    if status != 200 or not isinstance(result, dict):
        raise RuntimeError(f"Domain loop failed: HTTP {status} {result}")
    return result


def candidate_from_parent(api: Api, parent_work_id: str) -> dict[str, Any]:
    status, payload = api.request("GET", "/api/operations/action-candidates?limit=200")
    items = payload.get("items") if isinstance(payload, dict) else []
    candidate = next(
        (item for item in items or [] if item.get("parent_work_item_id") == parent_work_id),
        None,
    )
    if status != 200 or not candidate:
        raise RuntimeError(f"No action candidate was produced for {parent_work_id}")
    return candidate


def assess_work(api: Api, work_id: str) -> dict[str, Any]:
    status, result = api.request(
        "POST",
        f"/api/operations/outcomes/{urllib.parse.quote(work_id)}/assess",
    )
    if status != 200 or not isinstance(result, dict):
        raise RuntimeError(f"Outcome assessment failed: HTTP {status} {result}")
    return result


def main() -> int:
    env_path = Path(
        os.environ.get("CYBERTEAM_ENV_FILE", ROOT / "deploy/environments/staging.env")
    )
    load_env(env_path)
    api = Api(
        os.environ.get("API_BASE")
        or os.environ.get("NEXT_PUBLIC_API_URL")
        or "https://cyberteam.hyperailab.com",
    )
    owner_email = os.environ.get("OWNER_EMAIL", "")
    owner_password = os.environ.get("OWNER_PASSWORD", "")
    if not owner_email or not owner_password:
        print("OWNER_EMAIL and OWNER_PASSWORD are required.", file=sys.stderr)
        return 1

    run_key = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    evidence: dict[str, Any] = {
        "schema_version": "1.0",
        "status": "failed",
        "started_at": datetime.now(UTC).isoformat(),
        "api_base": api.base,
        "checks": {},
        "artifacts": {},
    }
    checks = evidence["checks"]
    gated_execution_work_id = ""
    try:
        status, health = api.request("GET", "/health")
        require(
            checks,
            "release_health",
            status == 200 and isinstance(health, dict) and health.get("status") == "ok",
            health,
        )
        api.login(owner_email, owner_password)
        checks["owner_login"] = {"status": "passed"}

        status, capabilities = api.request("GET", "/api/operations/model-capabilities")
        summary = capabilities.get("summary") if isinstance(capabilities, dict) else {}
        require(
            checks,
            "task_qualified_inference",
            status == 200
            and summary.get("status") == "ready"
            and summary.get("qualified") == summary.get("required"),
            summary,
        )

        status, readiness = api.request("GET", "/api/operations/readiness")
        autonomous = readiness.get("autonomous_company") if isinstance(readiness, dict) else {}
        sections = (autonomous or {}).get("sections") or {}
        signal_health = sections.get("company_signals") or {}
        extraction_health = sections.get("claim_extraction") or {}
        require(
            checks,
            "finite_signal_dispositions",
            status == 200
            and signal_health.get("stale_pending") == 0
            and signal_health.get("undispositioned_processed") == 0,
            signal_health,
        )
        require(
            checks,
            "bounded_claim_extraction",
            extraction_health.get("expired_leases") == 0
            and extraction_health.get("stale_failed") == 0,
            extraction_health,
        )

        status, mandate_payload = api.request("GET", "/api/operations/agent-mandates")
        mandates = mandate_items(mandate_payload)
        require(checks, "mandates_available", status == 200 and bool(mandates), len(mandates))
        safe_agent = choose_agent(
            mandates,
            tool_name="company_profile_read",
            preferred_agent="company_discovery_agent",
        )
        gated_agent = choose_agent(
            mandates,
            tool_name="task_create",
            preferred_agent=(
                "review_erpnext_derived_role_product_project_management_agent"
            ),
        )

        safe_evidence = f"acceptance:{run_key}:safe-internal-read"
        safe_work_id = create_candidate_work(
            api,
            run_key=run_key,
            suffix="safe",
            agent_id=safe_agent,
            risk_level="low",
            candidate={
                "tool_name": "company_profile_read",
                "params": {},
                "action_class": "internal_read",
                "expected_effect": "Read the canonical company profile for planning.",
                "evidence_ids": [safe_evidence],
                "confidence": 0.95,
                "reversible": True,
                "financial_exposure_usd": 0,
                "financial_daily_usd": 0,
                "recipients": 0,
                "data_sensitivity": "internal",
                "external_side_effect": False,
                "fresh_backup": True,
                "benchmark_fresh": True,
                "memory_coverage_fresh": True,
            },
        )
        safe_proposal_run = run_agent_once(api, safe_agent)
        safe_candidate = candidate_from_parent(api, safe_work_id)
        require(
            checks,
            "safe_candidate_governed",
            safe_candidate.get("status") == "ready"
            and bool(safe_candidate.get("observer_review_id"))
            and bool(safe_candidate.get("policy_decision")),
            {
                "candidate_id": safe_candidate.get("id"),
                "status": safe_candidate.get("status"),
                "processed": safe_proposal_run.get("processed"),
            },
        )
        safe_execution_run = run_agent_once(api, safe_agent)
        safe_candidate = candidate_from_parent(api, safe_work_id)
        safe_execution_work_id = str(safe_candidate.get("execution_work_item_id") or "")
        require(
            checks,
            "safe_internal_action_executed",
            safe_candidate.get("status") == "executed"
            and bool(safe_execution_work_id)
            and not safe_candidate.get("external_side_effect"),
            {
                "candidate_id": safe_candidate.get("id"),
                "status": safe_candidate.get("status"),
                "execution_work_item_id": safe_execution_work_id,
                "processed": safe_execution_run.get("processed"),
            },
        )
        safe_outcome = assess_work(api, safe_execution_work_id)
        safe_candidate = candidate_from_parent(api, safe_work_id)
        outcome_id = str((safe_candidate.get("result") or {}).get("outcome_assessment_id") or "")
        require(
            checks,
            "candidate_outcome_linked",
            safe_outcome.get("assessed") in {0, 1}
            and bool(outcome_id)
            and (safe_candidate.get("result") or {}).get("outcome_recommendation"),
            {
                "candidate_id": safe_candidate.get("id"),
                "outcome_assessment_id": outcome_id,
                "reflection_id": (safe_outcome.get("reflection") or {}).get("id"),
            },
        )
        status, graph = api.request(
            "GET",
            "/api/operations/operation-graph?source_type="
            "autonomous_action_candidate&limit=100",
        )
        nodes = graph.get("nodes") if isinstance(graph, dict) else []
        node = next(
            (item for item in nodes or [] if item.get("source_id") == safe_candidate.get("id")),
            None,
        )
        edges = graph.get("edges") if isinstance(graph, dict) else []
        linked_edges = {
            item.get("edge_type")
            for item in edges or []
            if node and item.get("source_node_id") == node.get("id")
        }
        require(
            checks,
            "operation_graph_trace",
            status == 200 and node is not None and {"compiled_to", "measured_by"} <= linked_edges,
            {"node_id": node.get("id") if node else None, "edge_types": sorted(linked_edges)},
        )

        gated_evidence = f"acceptance:{run_key}:large-impact-erpnext"
        gated_work_id = create_candidate_work(
            api,
            run_key=run_key,
            suffix="gated",
            agent_id=gated_agent,
            risk_level="medium",
            candidate={
                "tool_name": "task_create",
                "params": {
                    "task_data": {
                        "subject": f"Cyber-Team gated acceptance {run_key}",
                    }
                },
                "action_class": "erpnext",
                "expected_effect": "Create one staging-only ERPNext task after approval.",
                "evidence_ids": [gated_evidence],
                "confidence": 0.95,
                "reversible": True,
                "financial_exposure_usd": 501,
                "financial_daily_usd": 501,
                "recipients": 0,
                "data_sensitivity": "internal",
                "external_side_effect": True,
                "fresh_backup": True,
                "benchmark_fresh": True,
                "memory_coverage_fresh": True,
            },
        )
        gated_proposal_run = run_agent_once(api, gated_agent)
        gated_candidate = candidate_from_parent(api, gated_work_id)
        gated_execution_work_id = str(
            gated_candidate.get("execution_work_item_id") or ""
        )
        reasons = (gated_candidate.get("policy_decision") or {}).get("reasons") or []
        require(
            checks,
            "large_impact_action_gated",
            gated_candidate.get("status") == "approval_required"
            and "financial_action_limit_exceeded" in reasons
            and bool(gated_execution_work_id),
            {
                "candidate_id": gated_candidate.get("id"),
                "status": gated_candidate.get("status"),
                "policy_reasons": reasons,
                "processed": gated_proposal_run.get("processed"),
            },
        )
        cancel_status, cancelled = api.request(
            "POST",
            f"/api/operations/work-items/{gated_execution_work_id}/cancel",
            {
                "reason": "Staging large-impact gate proven; no ERPNext mutation authorized.",
                "include_descendants": False,
            },
        )
        gated_candidate = candidate_from_parent(api, gated_work_id)
        require(
            checks,
            "gated_action_cleanup",
            cancel_status == 200
            and isinstance(cancelled, dict)
            and gated_candidate.get("status") == "blocked"
            and gated_candidate.get("error") == "owner_cancelled_linked_work",
            {
                "http": cancel_status,
                "candidate_status": gated_candidate.get("status"),
                "cancelled": cancelled,
            },
        )
        assess_work(api, gated_work_id)
        assess_work(api, gated_execution_work_id)
        evidence["artifacts"] = {
            "safe_parent_work_id": safe_work_id,
            "safe_candidate_id": safe_candidate.get("id"),
            "safe_execution_work_id": safe_execution_work_id,
            "safe_outcome_assessment_id": outcome_id,
            "gated_parent_work_id": gated_work_id,
            "gated_candidate_id": gated_candidate.get("id"),
            "gated_execution_work_id": gated_execution_work_id,
        }
        evidence["status"] = "passed"
    except Exception as exc:  # noqa: BLE001 - evidence must survive a failed gate.
        evidence["error"] = str(exc)
    finally:
        if gated_execution_work_id and evidence["status"] != "passed":
            api.request(
                "POST",
                f"/api/operations/work-items/{gated_execution_work_id}/cancel",
                {
                    "reason": "Outcome autonomy acceptance cleanup after failed gate.",
                    "include_descendants": False,
                },
            )
        evidence["completed_at"] = datetime.now(UTC).isoformat()
        output_dir = Path(
            os.environ.get(
                "OUTCOME_AUTONOMY_EVIDENCE_DIR",
                ROOT / "dist/outcome-autonomy",
            )
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"outcome-autonomy-smoke-{run_key}.json"
        output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        print(f"Outcome autonomy smoke evidence: {output}")
    return 0 if evidence["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
