from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cyber_team.api.routes.comms import router as comms_router
from cyber_team.api.security import Principal, get_current_principal
from cyber_team.comms.inbound_email import InboundEmailService
from cyber_team.config import settings


@pytest.mark.asyncio
async def test_validate_imap_reports_configuration_required(monkeypatch):
    monkeypatch.setattr(settings, "inbound_email_enabled", False)
    monkeypatch.setattr(settings, "imap_host", "")
    monkeypatch.setattr(settings, "imap_username", "")
    monkeypatch.setattr(settings, "imap_password", "")

    result = await InboundEmailService().validate()

    assert result["status"] == "blocked"
    assert result["provider"] == "imap"
    assert result["results"][0]["status"] == "configuration_required"
    assert "INBOUND_EMAIL_ENABLED" in result["results"][0]["missing"]


@pytest.mark.asyncio
async def test_validate_imap_performs_login_select_and_noop(monkeypatch):
    monkeypatch.setattr(settings, "inbound_email_enabled", True)
    monkeypatch.setattr(settings, "imap_host", "imap.example.com")
    monkeypatch.setattr(settings, "imap_port", 993)
    monkeypatch.setattr(settings, "imap_username", "ops@example.com")
    monkeypatch.setattr(settings, "imap_password", "imap-password")
    monkeypatch.setattr(settings, "imap_use_ssl", True)
    monkeypatch.setattr(settings, "imap_mailbox", "INBOX")
    calls = []

    class FakeIMAP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            calls.append(("close",))

        def login(self, username, password):
            calls.append(("login", username, password))

        def select(self, mailbox, readonly=False):
            calls.append(("select", mailbox, readonly))
            return "OK", [b"1"]

        def noop(self):
            calls.append(("noop",))
            return "OK", [b""]

    monkeypatch.setattr("cyber_team.comms.inbound_email.imaplib.IMAP4_SSL", FakeIMAP)

    result = await InboundEmailService().validate()

    assert result["status"] == "ready"
    assert ("login", "ops@example.com", "imap-password") in calls
    assert ("select", "INBOX", True) in calls
    assert ("noop",) in calls


def test_fetch_unseen_messages_parses_headers_and_body(monkeypatch):
    monkeypatch.setattr(settings, "inbound_email_enabled", True)
    monkeypatch.setattr(settings, "imap_host", "imap.example.com")
    monkeypatch.setattr(settings, "imap_port", 993)
    monkeypatch.setattr(settings, "imap_username", "ops@example.com")
    monkeypatch.setattr(settings, "imap_password", "imap-password")
    monkeypatch.setattr(settings, "imap_use_ssl", True)
    monkeypatch.setattr(settings, "imap_mailbox", "INBOX")
    monkeypatch.setattr(settings, "inbound_email_address", "contact@example.com")
    monkeypatch.setattr(settings, "inbound_email_mark_seen", False)
    monkeypatch.setattr(settings, "inbound_email_max_messages_per_poll", 5)

    message = EmailMessage()
    message["From"] = "Customer <customer@example.com>"
    message["To"] = "Contact <contact@example.com>"
    message["Subject"] = "Need help"
    message["Message-ID"] = "<message-1@example.com>"
    message["Date"] = "Sun, 07 Jun 2026 10:30:00 +0000"
    message.set_content("Hello Cyber-Team, please help.")
    raw = message.as_bytes()
    calls = []

    class FakeIMAP:
        def __init__(self, host, port, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

        def login(self, username, password):
            pass

        def select(self, mailbox, readonly=False):
            calls.append(("select", mailbox, readonly))
            return "OK", [b"1"]

        def uid(self, command, *args):
            calls.append(("uid", command, args))
            if command == "SEARCH":
                return "OK", [b"42"]
            if command == "FETCH":
                return "OK", [(b"42 (RFC822)", raw)]
            raise AssertionError(command)

    monkeypatch.setattr("cyber_team.comms.inbound_email.imaplib.IMAP4_SSL", FakeIMAP)

    messages = InboundEmailService()._fetch_unseen_sync()

    assert ("select", "INBOX", True) in calls
    assert len(messages) == 1
    parsed = messages[0]
    assert parsed.provider_uid == "42"
    assert parsed.message_id == "<message-1@example.com>"
    assert parsed.from_address == "customer@example.com"
    assert parsed.to_addresses == ["contact@example.com"]
    assert parsed.subject == "Need help"
    assert "please help" in parsed.text_body
    assert parsed.snippet == "Hello Cyber-Team, please help."


def test_inbound_email_routes_list_poll_and_update(monkeypatch):
    app = FastAPI()
    app.include_router(comms_router, prefix="/api/comms")
    service = SimpleNamespace(
        list_messages=AsyncMock(return_value=[{"id": "msg-1", "status": "new"}]),
        get_message=AsyncMock(return_value={"id": "msg-1", "status": "new"}),
        update_status=AsyncMock(return_value={"id": "msg-1", "status": "triaged"}),
        poll_once=AsyncMock(return_value={"status": "ready", "stored": 1}),
    )
    app.state.inbound_email_service = service
    app.state.comms_gateway = SimpleNamespace(get_logs=AsyncMock(return_value=[]))

    async def mock_get_current_principal():
        return Principal(
            subject="owner",
            email="owner@example.com",
            role="owner",
            token_type="access",
        )

    async def mock_require_authorization(*args, **kwargs):
        return None

    app.dependency_overrides[get_current_principal] = mock_get_current_principal
    monkeypatch.setattr(
        "cyber_team.api.routes.comms.require_authorization",
        mock_require_authorization,
    )

    client = TestClient(app)

    assert client.get("/api/comms/inbound-email?status=new").json() == [
        {"id": "msg-1", "status": "new"}
    ]
    assert client.get("/api/comms/inbound-email/msg-1").json()["id"] == "msg-1"
    assert client.patch(
        "/api/comms/inbound-email/msg-1/status",
        json={"status": "triaged"},
    ).json()["status"] == "triaged"
    assert client.post("/api/comms/inbound-email/poll", json={}).json()["stored"] == 1
