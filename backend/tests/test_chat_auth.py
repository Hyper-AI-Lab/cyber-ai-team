from fastapi import HTTPException

from cyber_team.api.routes import chat


class FakeWebSocket:
    def __init__(self, headers=None, query_params=None):
        self.headers = headers or {}
        self.query_params = query_params or {}


def test_websocket_principal_accepts_authorization_header(monkeypatch):
    expected = object()

    def fake_decode_token(token, expected_type=None):
        assert token == "header-token"
        assert expected_type == "access"
        return expected

    monkeypatch.setattr(chat, "decode_token", fake_decode_token)

    principal = chat._principal_from_websocket(
        FakeWebSocket(headers={"authorization": "Bearer header-token"})
    )

    assert principal is expected


def test_websocket_principal_accepts_query_token(monkeypatch):
    expected = object()

    def fake_consume_websocket_ticket(ticket):
        assert ticket == "ticket-1"
        return expected

    monkeypatch.setattr(chat, "consume_websocket_ticket", fake_consume_websocket_ticket)

    principal = chat._principal_from_websocket(
        FakeWebSocket(query_params={"ticket": "ticket-1"})
    )

    assert principal is expected


def test_websocket_principal_rejects_access_token_query_param(monkeypatch):
    def fake_decode_token(token, expected_type=None):
        raise AssertionError("access token query params must not be decoded")

    monkeypatch.setattr(chat, "decode_token", fake_decode_token)

    principal = chat._principal_from_websocket(
        FakeWebSocket(query_params={"token": "access-token"})
    )

    assert principal is None


def test_websocket_principal_rejects_invalid_token(monkeypatch):
    def fake_decode_token(token, expected_type=None):
        raise HTTPException(status_code=401, detail="invalid")

    monkeypatch.setattr(chat, "decode_token", fake_decode_token)

    principal = chat._principal_from_websocket(
        FakeWebSocket(headers={"authorization": "Bearer bad-token"})
    )

    assert principal is None
