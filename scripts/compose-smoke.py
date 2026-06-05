#!/usr/bin/env python3
"""Smoke-test a running Cyber-Team Docker Compose stack."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request


API_BASE = os.environ.get("API_BASE", "http://localhost:8000").rstrip("/")
UI_BASE = os.environ.get("UI_BASE", "http://localhost:3001").rstrip("/")
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "owner@example.com")
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD", "changeme-owner-password")
TIMEOUT_SECONDS = int(os.environ.get("COMPOSE_SMOKE_TIMEOUT_SECONDS", "240"))
REQUIRE_LIVE_TOOL_EXECUTORS = os.environ.get(
    "REQUIRE_LIVE_TOOL_EXECUTORS",
    "false",
).lower() in {"1", "true", "yes", "on"}


def request_json(
    method: str,
    url: str,
    body: dict | None = None,
    token: str | None = None,
) -> tuple[int, dict]:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = response.read().decode("utf-8")
            return response.status, json.loads(data) if data else {}
    except urllib.error.HTTPError as exc:
        data = exc.read().decode("utf-8")
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            parsed = {"detail": data}
        return exc.code, parsed


def request_text(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=15) as response:
        return response.status, response.read().decode("utf-8", errors="replace")


def wait_for_json(url: str, expected_status: int = 200) -> dict:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            status, data = request_json("GET", url)
            if status == expected_status:
                return data
            last_error = RuntimeError(f"{url} returned {status}: {data}")
        except Exception as exc:  # noqa: BLE001 - show final startup failure context.
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def wait_for_ui(url: str) -> str:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            status, body = request_text(url)
            if status == 200 and ("Cyber-Team" in body or "__next" in body):
                return body
            last_error = RuntimeError(f"{url} returned {status}")
        except Exception as exc:  # noqa: BLE001 - show final startup failure context.
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    print(f"Waiting for API health at {API_BASE}/health")
    health = wait_for_json(f"{API_BASE}/health")
    assert_true(health.get("status") == "ok", f"Unexpected health response: {health}")
    readiness = wait_for_json(f"{API_BASE}/ready")
    assert_true(readiness.get("status") == "ready", f"Unexpected readiness response: {readiness}")

    print(f"Waiting for UI at {UI_BASE}")
    wait_for_ui(UI_BASE)

    print("Logging in as owner")
    status, login = request_json(
        "POST",
        f"{API_BASE}/api/auth/login",
        {"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
    )
    assert_true(status == 200, f"Login failed: {status} {login}")
    access_token = login.get("access_token")
    assert_true(bool(access_token), "Login did not return an access token")

    print("Reading dashboard KPIs")
    status, kpis = request_json("GET", f"{API_BASE}/api/dashboard/kpis", token=access_token)
    assert_true(status == 200, f"KPI request failed: {status} {kpis}")
    for key in ["total_agents", "total_workflows", "pending_approvals", "running_workflows"]:
        assert_true(key in kpis, f"Missing KPI key {key}: {kpis}")

    print("Reading integration status")
    status, integrations = request_json(
        "GET",
        f"{API_BASE}/api/integrations/status",
        token=access_token,
    )
    assert_true(status == 200, f"Integration status failed: {status} {integrations}")
    assert_true(
        any(item.get("channel") == "email" for item in integrations.get("communications", [])),
        f"Unexpected integration payload: {integrations}",
    )

    print("Minting one-time WebSocket ticket")
    status, ticket_response = request_json(
        "POST",
        f"{API_BASE}/api/auth/ws-ticket",
        token=access_token,
    )
    assert_true(status == 200, f"WebSocket ticket failed: {status} {ticket_response}")
    assert_true(bool(ticket_response.get("ticket")), "Ticket response did not include ticket")

    print("Reading tool readiness")
    status, tools = request_json("GET", f"{API_BASE}/api/tools", token=access_token)
    assert_true(status == 200, f"Tool readiness failed: {status} {tools}")
    email_tool = next((tool for tool in tools if tool.get("name") == "send_email"), None)
    assert_true(email_tool is not None, f"send_email tool missing from readiness payload: {tools}")
    email_requires_configuration = email_tool.get("state") in {
        "configuration_required",
        "unavailable",
    }

    tool_params = {
        "to_address": "smoke-test@example.com",
        "subject": "Cyber-Team smoke test",
        "body": "<p>This message is generated by the Compose smoke test.</p>",
    }
    print("Requesting approval for approval-gated email tool")
    status, first_tool = request_json(
        "POST",
        f"{API_BASE}/api/tools/execute",
        {"tool_name": "send_email", "params": tool_params},
        token=access_token,
    )
    assert_true(status == 200, f"Initial tool request failed: {status} {first_tool}")
    assert_true(first_tool.get("success") is False, f"Expected approval block: {first_tool}")

    if REQUIRE_LIVE_TOOL_EXECUTORS and email_requires_configuration:
        output = first_tool.get("output") or {}
        assert_true(output.get("blocked") is True, f"Expected readiness block: {first_tool}")
        assert_true(
            output.get("state") in {"configuration_required", "unavailable"},
            f"Unexpected readiness block state: {first_tool}",
        )
        print("Side-effect email tool is readiness-blocked as expected in proof mode")
        print("Compose smoke test passed.")
        return 0

    approval_id = (first_tool.get("output") or {}).get("approval_id")
    assert_true(bool(approval_id), f"Approval id missing from tool response: {first_tool}")

    print("Confirming approval appears in queue")
    status, queue = request_json(
        "GET",
        f"{API_BASE}/api/dashboard/approval-queue?status=pending",
        token=access_token,
    )
    assert_true(status == 200, f"Approval queue failed: {status} {queue}")
    assert_true(
        any(item.get("id") == approval_id for item in queue),
        f"Approval {approval_id} not found in pending queue",
    )

    print("Approving requested tool action")
    status, approval = request_json(
        "POST",
        f"{API_BASE}/api/dashboard/approval/{approval_id}/approve",
        {"note": "Approved by Compose smoke test"},
        token=access_token,
    )
    assert_true(status == 200, f"Approval failed: {status} {approval}")
    assert_true(approval.get("status") == "approved", f"Unexpected approval response: {approval}")

    print("Replaying tool execution with one-time approval")
    replay_params = {**tool_params, "_approval_id": approval_id}
    status, replay = request_json(
        "POST",
        f"{API_BASE}/api/tools/execute",
        {"tool_name": "send_email", "params": replay_params},
        token=access_token,
    )
    assert_true(status == 200, f"Tool replay failed: {status} {replay}")
    assert_true(replay.get("success") is True, f"Expected tool replay success: {replay}")
    assert_true((replay.get("output") or {}).get("status") in {"simulated", "sent"}, replay)

    print("Checking communication log side effect")
    status, logs = request_json(
        "GET",
        f"{API_BASE}/api/comms/logs?channel=email&limit=5",
        token=access_token,
    )
    assert_true(status == 200, f"Communication logs failed: {status} {logs}")
    assert_true(
        any(item.get("recipient") == "smoke-test@example.com" for item in logs),
        "Expected smoke-test email log was not found",
    )

    print("Compose smoke test passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - CLI boundary should report any failure.
        print(f"Compose smoke test failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
