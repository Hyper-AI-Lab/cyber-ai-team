from types import SimpleNamespace

import pytest

from cyber_team.comms.gateway import CircuitOpenError, CommsGateway
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

    result = await CommsGateway()._with_retries(flaky_operation, provider="smtp")

    assert result == "ok"
    assert attempts["count"] == 2


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_provider_failures(monkeypatch):
    monkeypatch.setattr(settings, "communications_retry_attempts", 1)
    monkeypatch.setattr(settings, "communications_circuit_breaker_failure_threshold", 2)
    monkeypatch.setattr(settings, "communications_circuit_breaker_cooldown_seconds", 60)
    gateway = CommsGateway()

    def failing_operation():
        raise RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError):
        await gateway._with_retries(failing_operation, provider="smtp")
    with pytest.raises(RuntimeError):
        await gateway._with_retries(failing_operation, provider="smtp")

    assert gateway._circuit_snapshot("smtp")["state"] == "open"
    with pytest.raises(CircuitOpenError):
        await gateway._with_retries(lambda: "ok", provider="smtp")


def test_idempotent_response_replays_stored_provider_result():
    log = SimpleNamespace(
        id="log-1",
        status="sent",
        idempotency_key="email:welcome:customer-1",
        metadata_={
            "response": {
                "email_id": "email-1",
                "status": "sent",
                "provider": "smtp",
            }
        },
    )

    response = CommsGateway._idempotent_response(log, id_field="email_id")

    assert response["email_id"] == "email-1"
    assert response["status"] == "sent"
    assert response["idempotency_key"] == "email:welcome:customer-1"
    assert response["idempotent_replay"] is True


def test_integration_status_reports_live_messaging_providers(monkeypatch):
    monkeypatch.setattr(settings, "slack_webhook_url", "https://hooks.slack.test/abc")
    monkeypatch.setattr(settings, "telegram_bot_token", "telegram-token")
    monkeypatch.setattr(settings, "twilio_account_sid", "sid")
    monkeypatch.setattr(settings, "twilio_auth_token", "token")
    monkeypatch.setattr(settings, "twilio_phone_number", "+15550000000")
    monkeypatch.setattr(settings, "twilio_whatsapp_from_number", "+15551112222")

    status = CommsGateway().integration_status()
    modes = {
        (item["channel"], item["provider"]): item["mode"]
        for item in status
    }

    assert modes[("slack", "slack_webhook")] == "live"
    assert modes[("telegram", "telegram_bot")] == "live"
    assert modes[("whatsapp", "twilio_whatsapp")] == "live"


def test_integration_status_reports_live_asterisk_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "asterisk_ari_enabled", True)
    monkeypatch.setattr(settings, "asterisk_host", "asterisk")
    monkeypatch.setattr(settings, "asterisk_ari_user", "ari-user")
    monkeypatch.setattr(settings, "asterisk_ari_password", "ari-password")
    monkeypatch.setattr(settings, "asterisk_ari_endpoint_template", "PJSIP/{to_number}")

    status = CommsGateway().integration_status()
    asterisk = next(item for item in status if item["provider"] == "asterisk_ari")

    assert asterisk["mode"] == "live"
    assert asterisk["circuit"]["state"] == "closed"


@pytest.mark.asyncio
async def test_send_slack_message_uses_configured_webhook(monkeypatch):
    monkeypatch.setattr(settings, "slack_webhook_url", "https://hooks.slack.test/abc")
    monkeypatch.setattr(settings, "communications_allow_simulation", False)
    gateway = CommsGateway()
    calls = []
    records = []

    async def fake_reserve_comm(**kwargs):
        return None, False

    async def fake_record_comm(**kwargs):
        records.append(kwargs)

    async def fake_http_request(method, url, *, params=None, json=None):
        calls.append({"method": method, "url": url, "params": params, "json": json})
        return {"text": "ok"}

    monkeypatch.setattr(gateway, "_reserve_comm", fake_reserve_comm)
    monkeypatch.setattr(gateway, "_record_comm", fake_record_comm)
    monkeypatch.setattr(gateway, "_http_request", fake_http_request)

    result = await gateway.send_message(
        SimpleNamespace(
            platform="slack",
            recipient="#sales",
            message="Pipeline update",
            agent_id="sales",
            idempotency_key="slack:sales:update-1",
        )
    )

    assert result["status"] == "sent"
    assert result["provider"] == "slack_webhook"
    assert result["idempotency_key"] == "slack:sales:update-1"
    assert calls == [
        {
            "method": "POST",
            "url": "https://hooks.slack.test/abc",
            "params": None,
            "json": {"text": "Pipeline update", "channel": "#sales"},
        }
    ]
    assert records[0]["metadata"]["provider"] == "slack_webhook"


@pytest.mark.asyncio
async def test_send_telegram_message_uses_bot_api(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "telegram-token")
    monkeypatch.setattr(settings, "communications_allow_simulation", False)
    gateway = CommsGateway()
    calls = []

    async def fake_reserve_comm(**kwargs):
        return None, False

    async def fake_record_comm(**kwargs):
        return None

    async def fake_http_request(method, url, *, params=None, json=None):
        calls.append({"method": method, "url": url, "params": params, "json": json})
        return {"result": {"message_id": 42}}

    monkeypatch.setattr(gateway, "_reserve_comm", fake_reserve_comm)
    monkeypatch.setattr(gateway, "_record_comm", fake_record_comm)
    monkeypatch.setattr(gateway, "_http_request", fake_http_request)

    result = await gateway.send_message(
        SimpleNamespace(
            platform="telegram",
            recipient="12345",
            message="Status update",
            agent_id="support",
            idempotency_key=None,
        )
    )

    assert result["status"] == "sent"
    assert result["provider"] == "telegram_bot"
    assert calls[0]["url"] == "https://api.telegram.org/bottelegram-token/sendMessage"
    assert calls[0]["json"] == {"chat_id": "12345", "text": "Status update"}


@pytest.mark.asyncio
async def test_send_sms_uses_jasmin_when_twilio_missing(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "")
    monkeypatch.setattr(settings, "twilio_auth_token", "")
    monkeypatch.setattr(settings, "twilio_phone_number", "")
    monkeypatch.setattr(settings, "jasmin_host", "jasmin")
    monkeypatch.setattr(settings, "jasmin_port", 1401)
    monkeypatch.setattr(settings, "jasmin_username", "api-user")
    monkeypatch.setattr(settings, "jasmin_password", "api-password")
    monkeypatch.setattr(settings, "jasmin_from_number", "CYBER")
    monkeypatch.setattr(settings, "jasmin_use_tls", False)
    monkeypatch.setattr(settings, "communications_allow_simulation", False)
    gateway = CommsGateway()
    calls = []

    async def fake_reserve_comm(**kwargs):
        return None, False

    async def fake_record_comm(**kwargs):
        return None

    async def fake_http_request(method, url, *, params=None, json=None):
        calls.append({"method": method, "url": url, "params": params, "json": json})
        return {"text": "Success \"abc123\""}

    monkeypatch.setattr(gateway, "_reserve_comm", fake_reserve_comm)
    monkeypatch.setattr(gateway, "_record_comm", fake_record_comm)
    monkeypatch.setattr(gateway, "_http_request", fake_http_request)

    result = await gateway.send_sms(
        SimpleNamespace(
            to_number="+15551230000",
            message="Hello",
            from_number=None,
            agent_id="support",
            idempotency_key=None,
        )
    )

    assert result["status"] == "sent"
    assert result["message_sid"] == 'Success "abc123"'
    assert calls[0]["url"] == "http://jasmin:1401/send"
    assert calls[0]["params"]["username"] == "api-user"
    assert calls[0]["params"]["from"] == "CYBER"


@pytest.mark.asyncio
async def test_make_call_uses_asterisk_when_twilio_missing(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "")
    monkeypatch.setattr(settings, "twilio_auth_token", "")
    monkeypatch.setattr(settings, "twilio_phone_number", "")
    monkeypatch.setattr(settings, "asterisk_ari_enabled", True)
    monkeypatch.setattr(settings, "asterisk_ari_use_tls", False)
    monkeypatch.setattr(settings, "asterisk_host", "asterisk")
    monkeypatch.setattr(settings, "asterisk_port", 8088)
    monkeypatch.setattr(settings, "asterisk_ari_user", "ari-user")
    monkeypatch.setattr(settings, "asterisk_ari_password", "ari-password")
    monkeypatch.setattr(settings, "asterisk_ari_app", "cyberteam")
    monkeypatch.setattr(settings, "asterisk_ari_endpoint_template", "PJSIP/{to_number}")
    monkeypatch.setattr(settings, "asterisk_caller_id", "Cyber-Team")
    monkeypatch.setattr(settings, "communications_allow_simulation", False)
    gateway = CommsGateway()
    calls = []

    async def fake_reserve_comm(**kwargs):
        return None, False

    async def fake_record_comm(**kwargs):
        return None

    async def fake_http_request(method, url, *, params=None, json=None, auth=None):
        calls.append(
            {"method": method, "url": url, "params": params, "json": json, "auth": auth}
        )
        return {"id": "asterisk-channel-1"}

    monkeypatch.setattr(gateway, "_reserve_comm", fake_reserve_comm)
    monkeypatch.setattr(gateway, "_record_comm", fake_record_comm)
    monkeypatch.setattr(gateway, "_http_request", fake_http_request)

    result = await gateway.make_call(
        SimpleNamespace(
            to_number="1001",
            context="Please call the owner.",
            from_number=None,
            agent_id="communications",
            idempotency_key=None,
        )
    )

    assert result["status"] == "initiated"
    assert result["provider"] == "asterisk_ari"
    assert result["call_sid"] == "asterisk-channel-1"
    assert calls[0]["url"] == "http://asterisk:8088/ari/channels"
    assert calls[0]["auth"] == ("ari-user", "ari-password")
    assert calls[0]["params"]["endpoint"] == "PJSIP/1001"
