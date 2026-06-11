"""ERPNext integration — read/write to ERPNext as canonical record source."""

import json
import logging
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx

from cyber_team.config import settings

logger = logging.getLogger(__name__)


class ERPNextClient:
    def __init__(self):
        self._base_url = settings.erpnext_url
        self._api_key = settings.erpnext_api_key
        self._api_secret = settings.erpnext_api_secret
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._api_key and self._api_secret)

    async def _get_client(self) -> httpx.AsyncClient:
        if not self._client:
            headers = {}
            if self._api_key and self._api_secret:
                headers["Authorization"] = f"token {self._api_key}:{self._api_secret}"
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=headers,
                timeout=30.0,
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()

    async def __aenter__(self) -> "ERPNextClient":
        await self._get_client()
        return self

    async def __aexit__(self, *_args):
        await self.close()

    def integration_status(
        self,
        last_validation_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        configured = self.configured
        return {
            "provider": "erpnext",
            "configured": configured,
            "mode": "live" if configured else "configuration_required",
            "site_url": f"https://{settings.erpnext_edge_domain}",
            "api_url": self._base_url,
            "site_name": settings.erpnext_site_name,
            "required": "erpnext" in settings.required_provider_names,
            "blocking": (
                "erpnext" in settings.required_provider_names and not configured
            ),
            "last_validation_result": last_validation_result,
            "detail": (
                "ERPNext API credentials are configured."
                if configured
                else "ERPNEXT_API_KEY and ERPNEXT_API_SECRET are required."
            ),
        }

    async def validate(self) -> dict[str, Any]:
        missing = []
        if not self._base_url:
            missing.append("ERPNEXT_URL")
        if not self._api_key:
            missing.append("ERPNEXT_API_KEY")
        if not self._api_secret:
            missing.append("ERPNEXT_API_SECRET")
        if missing:
            return {
                "status": "configuration_required",
                "provider": "erpnext",
                "mode": "configuration_required",
                "configured": False,
                "missing": missing,
                "site_url": f"https://{settings.erpnext_edge_domain}",
                "api_url": self._base_url,
                "detail": "ERPNext validation requires live API credentials.",
            }

        try:
            client = await self._get_client()
            ping = await client.get("/api/method/ping")
            ping.raise_for_status()
            user_check = await client.get(
                "/api/resource/User",
                params={"limit_page_length": 1},
            )
            user_check.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return {
                "status": "failed",
                "provider": "erpnext",
                "mode": "live",
                "configured": True,
                "site_url": f"https://{settings.erpnext_edge_domain}",
                "api_url": self._base_url,
                "detail": f"ERPNext returned HTTP {exc.response.status_code}.",
            }
        except httpx.HTTPError as exc:
            return {
                "status": "failed",
                "provider": "erpnext",
                "mode": "live",
                "configured": True,
                "site_url": f"https://{settings.erpnext_edge_domain}",
                "api_url": self._base_url,
                "detail": f"ERPNext network validation failed: {exc}",
            }

        return {
            "status": "ready",
            "provider": "erpnext",
            "mode": "live",
            "configured": True,
            "site_url": f"https://{settings.erpnext_edge_domain}",
            "api_url": self._base_url,
            "detail": "ERPNext REST API token validation passed.",
        }

    @staticmethod
    def _resource_path(doctype: str, name: str | None = None) -> str:
        path = f"/api/resource/{quote(doctype, safe='')}"
        if name:
            path += f"/{quote(name, safe='')}"
        return path

    @staticmethod
    def _data(response: httpx.Response) -> dict[str, Any] | list[dict[str, Any]]:
        payload = response.json()
        return payload.get("data", payload.get("message", {}))

    async def list_docs(
        self,
        doctype: str,
        filters: dict | None = None,
        fields: list | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        client = await self._get_client()
        params: dict[str, Any] = {}
        if filters:
            params["filters"] = json.dumps(filters)
        if fields:
            params["fields"] = json.dumps(fields)
        if limit:
            params["limit_page_length"] = limit
        resp = await client.get(self._resource_path(doctype), params=params)
        resp.raise_for_status()
        data = self._data(resp)
        return data if isinstance(data, list) else []

    async def create_doc(self, doctype: str, data: dict) -> dict:
        client = await self._get_client()
        payload = {"doctype": doctype, **data}
        resp = await client.post(self._resource_path(doctype), json=payload)
        resp.raise_for_status()
        result = self._data(resp)
        return result if isinstance(result, dict) else {}

    async def update_doc(self, doctype: str, name: str, updates: dict) -> dict:
        client = await self._get_client()
        resp = await client.put(self._resource_path(doctype, name), json=updates)
        resp.raise_for_status()
        result = self._data(resp)
        return result if isinstance(result, dict) else {}

    # ─── Finance ──────────────────────────────────────────────────────

    async def get_invoices(self, filters: dict = None) -> list[dict]:
        return await self.list_docs("Sales Invoice", filters=filters)

    async def create_invoice(self, invoice_data: dict) -> dict:
        return await self.create_doc("Sales Invoice", invoice_data)

    async def get_expenses(self, filters: dict = None) -> list[dict]:
        return await self.list_docs("Expense Claim", filters=filters)

    # ─── CRM ──────────────────────────────────────────────────────────

    async def get_leads(self, filters: dict = None) -> list[dict]:
        return await self.list_docs("Lead", filters=filters)

    async def create_lead(self, lead_data: dict) -> dict:
        return await self.create_doc("Lead", lead_data)

    async def update_lead(self, lead_id: str, updates: dict) -> dict:
        return await self.update_doc("Lead", lead_id, updates)

    async def get_customers(self, filters: dict = None) -> list[dict]:
        return await self.list_docs("Customer", filters=filters)

    async def get_suppliers(self, filters: dict = None) -> list[dict]:
        return await self.list_docs("Supplier", filters=filters)

    async def update_contact(self, contact_id: str, updates: dict) -> dict:
        return await self.update_doc("Contact", contact_id, updates)

    async def update_opportunity(self, opportunity_id: str, updates: dict) -> dict:
        return await self.update_doc("Opportunity", opportunity_id, updates)

    # ─── HR ───────────────────────────────────────────────────────────

    async def get_employees(self, filters: dict = None) -> list[dict]:
        return await self.list_docs("Employee", filters=filters)

    async def create_employee(self, emp_data: dict) -> dict:
        return await self.create_doc("Employee", emp_data)

    # ─── Projects ────────────────────────────────────────────────────

    async def get_projects(self, filters: dict = None) -> list[dict]:
        return await self.list_docs("Project", filters=filters)

    async def create_project(self, project_data: dict) -> dict:
        return await self.create_doc("Project", project_data)

    async def get_tasks(self, project: str = None) -> list[dict]:
        filters = {"project": project} if project else None
        return await self.list_docs("Task", filters=filters)

    async def create_task(self, task_data: dict) -> dict:
        return await self.create_doc("Task", task_data)

    async def update_task(self, task_id: str, updates: dict) -> dict:
        return await self.update_doc("Task", task_id, updates)

    # ─── Support / Procurement ───────────────────────────────────────

    async def create_issue(self, issue_data: dict) -> dict:
        return await self.create_doc("Issue", issue_data)

    async def update_issue(self, issue_id: str, updates: dict) -> dict:
        return await self.update_doc("Issue", issue_id, updates)

    async def create_material_request(self, request_data: dict) -> dict:
        payload = dict(request_data)
        payload.setdefault("material_request_type", "Purchase")
        payload.setdefault("transaction_date", date.today().isoformat())
        items = []
        for item in payload.get("items") or []:
            normalized_item = dict(item)
            normalized_item.setdefault("schedule_date", date.today().isoformat())
            items.append(normalized_item)
        payload["items"] = items
        return await self.create_doc("Material Request", payload)

    # ─── Generic ──────────────────────────────────────────────────────

    async def get_doc(self, doctype: str, name: str) -> dict:
        client = await self._get_client()
        resp = await client.get(self._resource_path(doctype, name))
        resp.raise_for_status()
        data = self._data(resp)
        return data if isinstance(data, dict) else {}

    async def search(
        self,
        doctype: str,
        filters: dict = None,
        fields: list = None,
        limit: int | None = None,
    ) -> list[dict]:
        client = await self._get_client()
        payload = {"doctype": doctype}
        if filters:
            payload["filters"] = filters
        if fields:
            payload["fields"] = fields
        if limit:
            payload["limit_page_length"] = limit
        resp = await client.post("/api/method/frappe.client.get_list", json=payload)
        resp.raise_for_status()
        return resp.json().get("message", [])
