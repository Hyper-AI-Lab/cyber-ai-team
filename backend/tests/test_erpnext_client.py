import httpx
import pytest

from cyber_team.integrations.erpnext import ERPNextClient


@pytest.mark.asyncio
async def test_erpnext_validate_and_update_contact(monkeypatch):
    monkeypatch.setattr("cyber_team.integrations.erpnext.settings.erpnext_url", "http://erpnext")
    monkeypatch.setattr("cyber_team.integrations.erpnext.settings.erpnext_api_key", "key")
    monkeypatch.setattr("cyber_team.integrations.erpnext.settings.erpnext_api_secret", "secret")

    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path == "/api/method/ping":
            return httpx.Response(200, json={"message": "pong"})
        if request.method == "GET" and request.url.path == "/api/resource/User":
            return httpx.Response(200, json={"data": [{"name": "Administrator"}]})
        if request.method == "PUT" and request.url.path == "/api/resource/Contact/CONT-1":
            return httpx.Response(
                200,
                json={"data": {"name": "CONT-1", "email_id": "new@example.com"}},
            )
        return httpx.Response(404, json={"exc": "not found"})

    client = ERPNextClient()
    client._client = httpx.AsyncClient(
        base_url="http://erpnext",
        headers={"Authorization": "token key:secret"},
        transport=httpx.MockTransport(handler),
    )

    validation = await client.validate()
    updated = await client.update_contact("CONT-1", {"email_id": "new@example.com"})
    await client.close()

    assert validation["status"] == "ready"
    assert updated["name"] == "CONT-1"
    assert requests[-1].headers["authorization"] == "token key:secret"


@pytest.mark.asyncio
async def test_erpnext_material_request_sets_defaults(monkeypatch):
    monkeypatch.setattr("cyber_team.integrations.erpnext.settings.erpnext_url", "http://erpnext")
    monkeypatch.setattr("cyber_team.integrations.erpnext.settings.erpnext_api_key", "key")
    monkeypatch.setattr("cyber_team.integrations.erpnext.settings.erpnext_api_secret", "secret")

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = request.content
        return httpx.Response(200, json={"data": {"name": "MAT-MR-0001"}})

    client = ERPNextClient()
    client._client = httpx.AsyncClient(
        base_url="http://erpnext",
        headers={"Authorization": "token key:secret"},
        transport=httpx.MockTransport(handler),
    )

    created = await client.create_material_request(
        {"items": [{"item_code": "ITEM-001", "qty": 2}]}
    )
    await client.close()

    assert created["name"] == "MAT-MR-0001"
    assert b'"doctype":"Material Request"' in captured["json"]
    assert b'"material_request_type":"Purchase"' in captured["json"].replace(b" ", b"")
    assert b'"schedule_date"' in captured["json"]
