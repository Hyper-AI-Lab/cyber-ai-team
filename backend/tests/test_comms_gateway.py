from types import SimpleNamespace

import pytest

from cyber_team.comms.gateway import CommsGateway
from cyber_team.config import settings


def test_integration_status_reports_simulated_email_when_smtp_missing(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "smtp_from_email", "")
    monkeypatch.setattr(settings, "communications_allow_simulation", True)

    status = CommsGateway().integration_status()
    email = next(item for item in status if item["channel"] == "email")

    assert email["provider"] == "smtp"
    assert email["configured"] is False
    assert email["mode"] == "simulated"


def test_integration_status_reports_live_email_when_smtp_configured(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from_email", "ops@example.com")
    monkeypatch.setattr(settings, "communications_allow_simulation", True)

    status = CommsGateway().integration_status()
    email = next(item for item in status if item["channel"] == "email")

    assert email["configured"] is True
    assert email["mode"] == "live"


def test_build_email_message_includes_sender_recipients_and_body(monkeypatch):
    monkeypatch.setattr(settings, "smtp_from_email", "ops@example.com")
    data = SimpleNamespace(
        to_address="customer@example.com",
        subject="Welcome",
        body="Hello from Cyber-Team.",
        cc=["owner@example.com"],
    )

    message = CommsGateway._build_email_message(data)

    assert message["From"] == "ops@example.com"
    assert message["To"] == "customer@example.com"
    assert message["Cc"] == "owner@example.com"
    assert message["Subject"] == "Welcome"
    assert "Hello from Cyber-Team." in message.get_content()


@pytest.mark.asyncio
async def test_with_retries_retries_provider_failures(monkeypatch):
    monkeypatch.setattr(settings, "communications_retry_attempts", 2)
    monkeypatch.setattr(settings, "communications_retry_backoff_seconds", 0)
    attempts = {"count": 0}

    def flaky_operation():
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("temporary provider failure")
        return "ok"

    result = await CommsGateway()._with_retries(flaky_operation)

    assert result == "ok"
    assert attempts["count"] == 2
