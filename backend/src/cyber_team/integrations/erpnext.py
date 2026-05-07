"""ERPNext integration — read/write to ERPNext as canonical record source."""

import json
import logging
from typing import Optional

import httpx

from cyber_team.config import settings

logger = logging.getLogger(__name__)


class ERPNextClient:
    def __init__(self):
        self._base_url = settings.erpnext_url
        self._api_key = settings.erpnext_api_key
        self._api_secret = settings.erpnext_api_secret
        self._client: Optional[httpx.AsyncClient] = None

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

    # ─── Finance ──────────────────────────────────────────────────────

    async def get_invoices(self, filters: dict = None) -> list[dict]:
        client = await self._get_client()
        params = {}
        if filters:
            params["filters"] = json.dumps(filters)
        resp = await client.get("/api/resource/Sales Invoice", params=params)
        resp.raise_for_status()
        return resp.json().get("data", [])

    async def create_invoice(self, invoice_data: dict) -> dict:
        client = await self._get_client()
        resp = await client.post("/api/resource/Sales Invoice", json=invoice_data)
        resp.raise_for_status()
        return resp.json().get("data", {})

    async def get_expenses(self, filters: dict = None) -> list[dict]:
        client = await self._get_client()
        params = {}
        if filters:
            params["filters"] = json.dumps(filters)
        resp = await client.get("/api/resource/Expense Claim", params=params)
        resp.raise_for_status()
        return resp.json().get("data", [])

    # ─── CRM ──────────────────────────────────────────────────────────

    async def get_leads(self, filters: dict = None) -> list[dict]:
        client = await self._get_client()
        params = {}
        if filters:
            params["filters"] = json.dumps(filters)
        resp = await client.get("/api/resource/Lead", params=params)
        resp.raise_for_status()
        return resp.json().get("data", [])

    async def create_lead(self, lead_data: dict) -> dict:
        client = await self._get_client()
        resp = await client.post("/api/resource/Lead", json=lead_data)
        resp.raise_for_status()
        return resp.json().get("data", {})

    async def update_lead(self, lead_id: str, updates: dict) -> dict:
        client = await self._get_client()
        resp = await client.put(f"/api/resource/Lead/{lead_id}", json=updates)
        resp.raise_for_status()
        return resp.json().get("data", {})

    async def get_customers(self, filters: dict = None) -> list[dict]:
        client = await self._get_client()
        params = {}
        if filters:
            params["filters"] = json.dumps(filters)
        resp = await client.get("/api/resource/Customer", params=params)
        resp.raise_for_status()
        return resp.json().get("data", [])

    # ─── HR ───────────────────────────────────────────────────────────

    async def get_employees(self, filters: dict = None) -> list[dict]:
        client = await self._get_client()
        params = {}
        if filters:
            params["filters"] = json.dumps(filters)
        resp = await client.get("/api/resource/Employee", params=params)
        resp.raise_for_status()
        return resp.json().get("data", [])

    async def create_employee(self, emp_data: dict) -> dict:
        client = await self._get_client()
        resp = await client.post("/api/resource/Employee", json=emp_data)
        resp.raise_for_status()
        return resp.json().get("data", {})

    # ─── Projects ────────────────────────────────────────────────────

    async def get_projects(self, filters: dict = None) -> list[dict]:
        client = await self._get_client()
        params = {}
        if filters:
            params["filters"] = json.dumps(filters)
        resp = await client.get("/api/resource/Project", params=params)
        resp.raise_for_status()
        return resp.json().get("data", [])

    async def create_project(self, project_data: dict) -> dict:
        client = await self._get_client()
        resp = await client.post("/api/resource/Project", json=project_data)
        resp.raise_for_status()
        return resp.json().get("data", {})

    async def get_tasks(self, project: str = None) -> list[dict]:
        client = await self._get_client()
        params = {}
        if project:
            params["filters"] = json.dumps({"project": project})
        resp = await client.get("/api/resource/Task", params=params)
        resp.raise_for_status()
        return resp.json().get("data", [])

    # ─── Generic ──────────────────────────────────────────────────────

    async def get_doc(self, doctype: str, name: str) -> dict:
        client = await self._get_client()
        resp = await client.get(f"/api/resource/{doctype}/{name}")
        resp.raise_for_status()
        return resp.json().get("data", {})

    async def search(self, doctype: str, filters: dict = None, fields: list = None) -> list[dict]:
        client = await self._get_client()
        payload = {"doctype": doctype}
        if filters:
            payload["filters"] = filters
        if fields:
            payload["fields"] = fields
        resp = await client.post("/api/method/frappe.client.get_list", json=payload)
        resp.raise_for_status()
        return resp.json().get("message", [])
