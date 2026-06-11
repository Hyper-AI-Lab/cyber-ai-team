#!/usr/bin/env python3
"""Approval-gated Cyber-Team -> ERPNext staging smoke test."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Config:
    env_file: Path
    api_base: str
    owner_email: str
    owner_password: str
    erpnext_site_name: str
    erpnext_frontend_url: str
    erpnext_api_key: str
    erpnext_api_secret: str
    evidence_dir: Path
    allow_production: bool


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def build_config(args: argparse.Namespace) -> Config:
    env_file = Path(args.env_file).resolve()
    env_values = {**load_env(env_file), **os.environ}
    published_port = env_values.get("ERPNEXT_PUBLISHED_PORT", "18100")
    return Config(
        env_file=env_file,
        api_base=args.api_base
        or env_values.get("API_BASE")
        or "http://127.0.0.1:18000",
        owner_email=env_values.get("OWNER_EMAIL", ""),
        owner_password=env_values.get("OWNER_PASSWORD", ""),
        erpnext_site_name=env_values.get("ERPNEXT_SITE_NAME", "erpnext.hyperailab.com"),
        erpnext_frontend_url=args.erpnext_url
        or env_values.get("ERPNEXT_BOOTSTRAP_FRONTEND_URL")
        or f"http://127.0.0.1:{published_port}",
        erpnext_api_key=env_values.get("ERPNEXT_API_KEY", ""),
        erpnext_api_secret=env_values.get("ERPNEXT_API_SECRET", ""),
        evidence_dir=Path(args.evidence_dir).resolve(),
        allow_production=args.allow_production,
    )


def request_json(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> tuple[int, dict[str, Any]]:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url,
        data=payload,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8")
            return response.status, json.loads(text) if text else {}
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {"detail": text}
        return exc.code, data


class CyberTeamClient:
    def __init__(self, config: Config):
        self._config = config
        self._api_base = config.api_base.rstrip("/")
        self._token: str | None = None

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def get(self, path: str) -> dict[str, Any]:
        status, data = request_json("GET", f"{self._api_base}{path}", headers=self._headers())
        if status >= 400:
            raise RuntimeError(f"GET {path} failed: {status} {data}")
        return data

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        status, data = request_json(
            "POST",
            f"{self._api_base}{path}",
            body,
            headers=self._headers(),
        )
        if status >= 400:
            raise RuntimeError(f"POST {path} failed: {status} {data}")
        return data

    def login(self) -> None:
        data = self.post(
            "/api/auth/login",
            {
                "email": self._config.owner_email,
                "password": self._config.owner_password,
            },
        )
        token = data.get("access_token")
        if not token:
            raise RuntimeError("Owner login did not return an access token.")
        self._token = str(token)

    def execute_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.post("/api/tools/execute", {"tool_name": tool_name, "params": params})

    def approve(self, approval_id: str, note: str) -> dict[str, Any]:
        return self.post(f"/api/dashboard/approval/{approval_id}/approve", {"note": note})

    def reject(self, approval_id: str, note: str) -> dict[str, Any]:
        return self.post(f"/api/dashboard/approval/{approval_id}/reject", {"note": note})


class ERPNextFixtureClient:
    def __init__(self, config: Config):
        self._base_url = config.erpnext_frontend_url.rstrip("/")
        self._site_name = config.erpnext_site_name
        self._auth = f"token {config.erpnext_api_key}:{config.erpnext_api_secret}"

    def _headers(self) -> dict[str, str]:
        return {
            "Host": self._site_name,
            "Authorization": self._auth,
        }

    @staticmethod
    def _resource_path(doctype: str, name: str | None = None) -> str:
        path = f"/api/resource/{urllib.parse.quote(doctype, safe='')}"
        if name:
            path += f"/{urllib.parse.quote(name, safe='')}"
        return path

    def get_doc(self, doctype: str, name: str) -> dict[str, Any] | None:
        status, data = request_json(
            "GET",
            f"{self._base_url}{self._resource_path(doctype, name)}",
            headers=self._headers(),
        )
        if status == 404:
            return None
        if status >= 400:
            raise RuntimeError(f"ERPNext get {doctype}/{name} failed: {status} {data}")
        result = data.get("data")
        return result if isinstance(result, dict) else {}

    def create_doc(self, doctype: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = {"doctype": doctype, **payload}
        status, data = request_json(
            "POST",
            f"{self._base_url}{self._resource_path(doctype)}",
            body,
            headers=self._headers(),
        )
        if status >= 400:
            raise RuntimeError(f"ERPNext create {doctype} failed: {status} {data}")
        result = data.get("data")
        return result if isinstance(result, dict) else {}

    def update_doc(self, doctype: str, name: str, updates: dict[str, Any]) -> dict[str, Any]:
        status, data = request_json(
            "PUT",
            f"{self._base_url}{self._resource_path(doctype, name)}",
            updates,
            headers=self._headers(),
        )
        if status >= 400:
            raise RuntimeError(f"ERPNext update {doctype}/{name} failed: {status} {data}")
        result = data.get("data")
        return result if isinstance(result, dict) else {}

    def list_docs(
        self,
        doctype: str,
        filters: list[Any] | None = None,
        fields: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = {
            "limit_page_length": str(limit),
        }
        if filters:
            query["filters"] = json.dumps(filters)
        if fields:
            query["fields"] = json.dumps(fields)
        status, data = request_json(
            "GET",
            f"{self._base_url}{self._resource_path(doctype)}?{urllib.parse.urlencode(query)}",
            headers=self._headers(),
        )
        if status >= 400:
            raise RuntimeError(f"ERPNext list {doctype} failed: {status} {data}")
        result = data.get("data")
        return result if isinstance(result, list) else []

    def ensure_smoke_item(self) -> str:
        item_code = "CYBERTEAM-SMOKE-SERVICE"
        if self.get_doc("Item", item_code):
            return item_code
        item_group = self.ensure_item_group()
        stock_uom = self.ensure_uom()
        self.create_doc(
            "Item",
            {
                "item_code": item_code,
                "item_name": "Cyber-Team Smoke Service",
                "description": "Staging-only item used by Cyber-Team ERPNext smoke tests.",
                "item_group": item_group,
                "stock_uom": stock_uom,
                "is_stock_item": 0,
            },
        )
        return item_code

    def ensure_company(self) -> str:
        company = "Cyber-Team Smoke Company"
        if self.get_doc("Company", company):
            return company
        self.ensure_warehouse_type()
        self.create_doc(
            "Company",
            {
                "company_name": company,
                "abbr": "CTSMK",
                "default_currency": "USD",
                "country": "United States",
            },
        )
        return company

    def ensure_warehouse_type(self) -> str:
        warehouse_type = "Transit"
        if self.get_doc("Warehouse Type", warehouse_type):
            return warehouse_type
        self.create_doc(
            "Warehouse Type",
            {
                "warehouse_type": warehouse_type,
            },
        )
        return warehouse_type

    def ensure_uom(self) -> str:
        if self.get_doc("UOM", "Nos"):
            return "Nos"
        self.create_doc("UOM", {"uom_name": "Nos", "enabled": 1})
        return "Nos"

    def ensure_item_group(self) -> str:
        if self.get_doc("Item Group", "All Item Groups"):
            return "All Item Groups"
        self.create_doc(
            "Item Group",
            {
                "item_group_name": "All Item Groups",
                "is_group": 1,
            },
        )
        return "All Item Groups"

    def _first_doc_name(self, doctype: str, preferred: str) -> str:
        if self.get_doc(doctype, preferred):
            return preferred
        docs = self.list_docs(doctype, fields=["name"], limit=1)
        if not docs or not docs[0].get("name"):
            raise RuntimeError(f"ERPNext has no {doctype} records available.")
        return str(docs[0]["name"])

    def archive_existing_smoke_records(self) -> dict[str, Any]:
        archived: dict[str, Any] = {"Lead": [], "Task": [], "Issue": []}
        for lead in self.list_docs(
            "Lead",
            filters=[["lead_name", "like", "Cyber-Team API ERPNext smoke%"]],
            fields=["name", "status"],
        ):
            if lead.get("status") != "Do Not Contact":
                record = self.update_doc("Lead", str(lead["name"]), {"status": "Do Not Contact"})
                archived["Lead"].append({"name": lead["name"], "final_status": record.get("status")})
        for task in self.list_docs(
            "Task",
            filters=[["subject", "like", "Cyber-Team API ERPNext smoke%"]],
            fields=["name", "status"],
        ):
            if task.get("status") != "Completed":
                record = self.update_doc("Task", str(task["name"]), {"status": "Completed"})
                archived["Task"].append({"name": task["name"], "final_status": record.get("status")})
        for issue in self.list_docs(
            "Issue",
            filters=[["subject", "like", "Cyber-Team API ERPNext smoke%"]],
            fields=["name", "status"],
        ):
            if issue.get("status") != "Closed":
                record = self.update_doc("Issue", str(issue["name"]), {"status": "Closed"})
                archived["Issue"].append({"name": issue["name"], "final_status": record.get("status")})
        return archived


def require_config(config: Config) -> None:
    missing = []
    for key, value in {
        "OWNER_EMAIL": config.owner_email,
        "OWNER_PASSWORD": config.owner_password,
        "ERPNEXT_API_KEY": config.erpnext_api_key,
        "ERPNEXT_API_SECRET": config.erpnext_api_secret,
    }.items():
        if not value:
            missing.append(key)
    if missing:
        raise RuntimeError(f"Missing required configuration: {', '.join(missing)}")


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def get_approval_id(result: dict[str, Any]) -> str:
    output = result.get("output") or {}
    approval_id = output.get("approval_id")
    assert_true(result.get("success") is False, f"Expected approval block: {result}")
    assert_true(output.get("approval_required") is True, f"Expected approval_required: {result}")
    assert_true(bool(approval_id), f"Approval id missing: {result}")
    return str(approval_id)


def execute_with_approval(
    client: CyberTeamClient,
    tool_name: str,
    params: dict[str, Any],
    note: str,
) -> tuple[dict[str, Any], str, dict[str, Any], dict[str, Any]]:
    blocked = client.execute_tool(tool_name, params)
    approval_id = get_approval_id(blocked)
    approval = client.approve(approval_id, note)
    assert_true(approval.get("status") == "approved", f"Approval failed: {approval}")
    replay = client.execute_tool(tool_name, {**params, "_approval_id": approval_id})
    assert_true(replay.get("success") is True, f"Tool replay failed: {replay}")
    consumed = client.execute_tool(tool_name, {**params, "_approval_id": approval_id})
    consumed_approval_id = get_approval_id(consumed)
    rejection = client.reject(
        consumed_approval_id,
        "Rejected by ERPNext smoke after consumed-approval replay check.",
    )
    assert_true(rejection.get("status") == "rejected", f"Replay rejection failed: {rejection}")
    return replay, approval_id, approval, consumed


def extract_record(result: dict[str, Any]) -> dict[str, Any]:
    output = result.get("output") or {}
    if isinstance(output, dict) and isinstance(output.get("record"), dict):
        return output["record"]
    return output if isinstance(output, dict) else {}


def assert_tool_live(client: CyberTeamClient, tool_names: set[str]) -> None:
    tools = client.get("/api/tools")
    states = {
        tool.get("name"): tool
        for tool in tools
        if tool.get("name") in tool_names
    }
    missing = sorted(tool_names - set(states))
    if missing:
        raise RuntimeError(f"Missing expected tools: {', '.join(missing)}")
    not_live = {
        name: tool
        for name, tool in states.items()
        if tool.get("state") != "live"
    }
    if not_live:
        raise RuntimeError(f"ERPNext smoke requires live tools: {not_live}")


def run_smoke(config: Config) -> dict[str, Any]:
    require_config(config)
    client = CyberTeamClient(config)
    erpnext = ERPNextFixtureClient(config)

    health = client.get("/health")
    environment = str(health.get("environment") or "")
    if environment == "production" and not config.allow_production:
        raise RuntimeError("Refusing to run live ERPNext write smoke against production.")

    client.login()
    required_tools = {
        "erpnext_create_lead",
        "task_create",
        "ticket_create",
        "procurement_request",
    }
    assert_tool_live(client, required_tools)

    precleaned = erpnext.archive_existing_smoke_records()
    company = erpnext.ensure_company()
    item_code = erpnext.ensure_smoke_item()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    marker = f"Cyber-Team API ERPNext smoke {stamp}"

    # Target-mismatch proof: an approved task_create approval must not execute ticket_create.
    mismatch_task_params = {
        "task_data": {
            "subject": f"{marker} mismatch approval target",
            "description": "This task should be created only after target mismatch is tested.",
        }
    }
    mismatch_blocked = client.execute_tool("task_create", mismatch_task_params)
    mismatch_approval_id = get_approval_id(mismatch_blocked)
    client.approve(mismatch_approval_id, "Approved for target mismatch smoke.")
    wrong_tool = client.execute_tool(
        "ticket_create",
        {
            "issue_data": {
                "subject": f"{marker} wrong target should not execute",
                "description": "This issue must not be created with a task approval.",
                "raised_by": f"erpnext-smoke+wrong-target-{stamp}@example.local",
            },
            "_approval_id": mismatch_approval_id,
        },
    )
    wrong_target_approval_id = get_approval_id(wrong_tool)
    client.reject(
        wrong_target_approval_id,
        "Rejected by ERPNext smoke after wrong-target approval check.",
    )
    mismatch_task = client.execute_tool(
        "task_create",
        {**mismatch_task_params, "_approval_id": mismatch_approval_id},
    )
    assert_true(mismatch_task.get("success") is True, f"Target approval did not replay: {mismatch_task}")
    mismatch_task_record = extract_record(mismatch_task)

    actions: dict[str, Any] = {
        "target_mismatch": {
            "approval_id": mismatch_approval_id,
            "created_task": mismatch_task_record.get("name"),
        }
    }

    lead_params = {
        "lead_data": {
            "lead_name": marker,
            "company_name": "Cyber-Team Staging Smoke",
            "email_id": f"erpnext-smoke+lead-{stamp}@example.local",
            "source": "Other",
        }
    }
    lead_replay, lead_approval_id, _, _ = execute_with_approval(
        client,
        "erpnext_create_lead",
        lead_params,
        "Approved by ERPNext Cyber-Team smoke for Lead creation.",
    )
    actions["erpnext_create_lead"] = {
        "approval_id": lead_approval_id,
        "record": extract_record(lead_replay),
    }

    task_params = {
        "task_data": {
            "subject": f"{marker} task",
            "description": "Staging smoke task created through Cyber-Team tool execution.",
        }
    }
    task_replay, task_approval_id, _, _ = execute_with_approval(
        client,
        "task_create",
        task_params,
        "Approved by ERPNext Cyber-Team smoke for Task creation.",
    )
    actions["task_create"] = {
        "approval_id": task_approval_id,
        "record": extract_record(task_replay),
    }

    issue_params = {
        "issue_data": {
            "subject": f"{marker} issue",
            "description": "Staging smoke issue created through Cyber-Team tool execution.",
            "raised_by": f"erpnext-smoke+issue-{stamp}@example.local",
        }
    }
    issue_replay, issue_approval_id, _, _ = execute_with_approval(
        client,
        "ticket_create",
        issue_params,
        "Approved by ERPNext Cyber-Team smoke for Issue creation.",
    )
    actions["ticket_create"] = {
        "approval_id": issue_approval_id,
        "record": extract_record(issue_replay),
    }

    material_request_params = {
        "request_data": {
            "company": company,
            "material_request_type": "Purchase",
            "transaction_date": date.today().isoformat(),
            "items": [
                {
                    "item_code": item_code,
                    "qty": 1,
                    "schedule_date": date.today().isoformat(),
                }
            ],
        }
    }
    material_replay, material_approval_id, _, _ = execute_with_approval(
        client,
        "procurement_request",
        material_request_params,
        "Approved by ERPNext Cyber-Team smoke for Material Request creation.",
    )
    actions["procurement_request"] = {
        "approval_id": material_approval_id,
        "record": extract_record(material_replay),
    }

    cleanup = cleanup_records(erpnext, actions)
    return {
        "status": "passed",
        "api_base": config.api_base,
        "environment": environment,
        "site_name": config.erpnext_site_name,
        "marker": marker,
        "completed_at": datetime.now(UTC).isoformat(),
        "actions": sanitize_actions(actions),
        "precleaned": precleaned,
        "cleanup": cleanup,
    }


def sanitize_actions(actions: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for name, data in actions.items():
        record = data.get("record") or {}
        sanitized[name] = {
            "approval_id": data.get("approval_id"),
            "doctype": record.get("doctype") or data.get("doctype"),
            "record_id": record.get("name") or data.get("created_task"),
            "status": record.get("status"),
        }
    return sanitized


def cleanup_records(
    erpnext: ERPNextFixtureClient,
    actions: dict[str, Any],
) -> dict[str, Any]:
    cleanup: dict[str, Any] = {}
    lead = (actions.get("erpnext_create_lead", {}).get("record") or {}).get("name")
    if lead:
        record = erpnext.update_doc("Lead", str(lead), {"status": "Do Not Contact"})
        cleanup["Lead"] = {"name": lead, "final_status": record.get("status")}

    task_names = [
        actions.get("target_mismatch", {}).get("created_task"),
        (actions.get("task_create", {}).get("record") or {}).get("name"),
    ]
    cleanup["Task"] = []
    for task in [name for name in task_names if name]:
        record = erpnext.update_doc("Task", str(task), {"status": "Completed"})
        cleanup["Task"].append({"name": task, "final_status": record.get("status")})

    issue = (actions.get("ticket_create", {}).get("record") or {}).get("name")
    if issue:
        record = erpnext.update_doc("Issue", str(issue), {"status": "Closed"})
        cleanup["Issue"] = {"name": issue, "final_status": record.get("status")}

    material_request = (actions.get("procurement_request", {}).get("record") or {}).get("name")
    if material_request:
        cleanup["Material Request"] = {
            "name": material_request,
            "final_status": (
                actions.get("procurement_request", {}).get("record") or {}
            ).get("status"),
            "note": "Material Request remains as a staging draft/procurement audit record.",
        }
    return cleanup


def write_evidence(config: Config, payload: dict[str, Any]) -> Path:
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = config.evidence_dir / f"cyberteam-erpnext-tool-smoke-{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        default=str(ROOT_DIR / "deploy/environments/staging.env"),
        help="Environment file containing owner and ERPNext credentials.",
    )
    parser.add_argument("--api-base", default="", help="Cyber-Team API base URL.")
    parser.add_argument("--erpnext-url", default="", help="ERPNext direct frontend URL.")
    parser.add_argument(
        "--evidence-dir",
        default=str(ROOT_DIR / "dist/erpnext/smoke"),
        help="Directory for non-secret smoke evidence.",
    )
    parser.add_argument(
        "--allow-production",
        action="store_true",
        help="Allow the live-write smoke against a production Cyber-Team environment.",
    )
    return parser.parse_args()


def main() -> int:
    config = build_config(parse_args())
    try:
        payload = run_smoke(config)
        evidence_path = write_evidence(config, payload)
    except Exception as exc:  # noqa: BLE001 - CLI boundary should report context.
        print(f"ERPNext Cyber-Team tool smoke failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": payload["status"],
                "environment": payload["environment"],
                "evidence": str(evidence_path),
                "actions": payload["actions"],
                "cleanup": payload["cleanup"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
