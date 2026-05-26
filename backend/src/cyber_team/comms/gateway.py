"""Communications Gateway - telephony, SMS, email, messaging."""

import asyncio
import logging
import smtplib
import ssl
import uuid
from collections.abc import Awaitable, Callable
from email.message import EmailMessage

import httpx
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError

from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import CommunicationLog

logger = logging.getLogger(__name__)


class CommsGateway:
    def __init__(self):
        self._twilio_client = None

    def _get_twilio(self):
        if not self._twilio_client and self._twilio_configured():
            from twilio.rest import Client
            self._twilio_client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        return self._twilio_client

    def integration_status(self) -> list[dict]:
        simulation_enabled = settings.communications_allow_simulation
        twilio_ready = self._twilio_configured()
        return [
            self._status_item(
                channel="voice",
                provider="twilio",
                configured=twilio_ready,
                implementation="implemented",
                simulation_enabled=simulation_enabled,
                detail="Outbound voice uses Twilio calls when credentials are configured.",
            ),
            self._status_item(
                channel="sms",
                provider="twilio",
                configured=twilio_ready,
                implementation="implemented",
                simulation_enabled=simulation_enabled,
                detail="Outbound SMS uses Twilio when credentials are configured.",
            ),
            self._status_item(
                channel="sms",
                provider="jasmin",
                configured=self._jasmin_configured(),
                implementation="implemented",
                simulation_enabled=simulation_enabled,
                detail="Outbound SMS can use Jasmin when gateway credentials are configured.",
            ),
            self._status_item(
                channel="email",
                provider="smtp",
                configured=self._smtp_configured(),
                implementation="implemented",
                simulation_enabled=simulation_enabled,
                detail="Outbound email uses SMTP when host and sender are configured.",
            ),
            self._status_item(
                channel="slack",
                provider="slack_webhook",
                configured=self._slack_configured(),
                implementation="implemented",
                simulation_enabled=simulation_enabled,
                detail="Slack messages use an incoming webhook when configured.",
            ),
            self._status_item(
                channel="telegram",
                provider="telegram_bot",
                configured=self._telegram_configured(),
                implementation="implemented",
                simulation_enabled=simulation_enabled,
                detail="Telegram messages use the Bot API when a bot token is configured.",
            ),
            self._status_item(
                channel="whatsapp",
                provider="twilio_whatsapp",
                configured=self._twilio_whatsapp_configured(),
                implementation="implemented",
                simulation_enabled=simulation_enabled,
                detail="WhatsApp messages use Twilio when a WhatsApp sender is configured.",
            ),
            {
                "channel": "asterisk",
                "provider": "asterisk",
                "configured": bool(settings.asterisk_host),
                "mode": "profile_only",
                "implementation": "compose_profile",
                "detail": (
                    "Asterisk can be started from Docker Compose, but runtime calls use Twilio."
                ),
            },
        ]

    async def make_call(self, data) -> dict:
        call_id = str(uuid.uuid4())
        from_number = data.from_number or settings.twilio_phone_number
        key = self._idempotency_key(data)
        reserved_log, replay = await self._reserve_comm(
            agent_id=data.agent_id,
            channel="voice",
            recipient=data.to_number,
            content=data.context,
            metadata={"from": from_number, "provider": "twilio"},
            idempotency_key=key,
        )
        if replay:
            return self._idempotent_response(reserved_log, id_field="call_id")

        metadata = {"from": from_number, "provider": "twilio"}
        try:
            client = self._get_twilio()
            if client:
                call = await self._with_retries(
                    lambda: client.calls.create(
                        to=data.to_number,
                        from_=from_number,
                        twiml=f'<Response><Say>{data.context}</Say></Response>',
                    )
                )
                call_sid = call.sid
                status = "initiated"
            elif settings.communications_allow_simulation:
                call_sid = "local-only"
                status = "simulated"
                logger.info(
                    "[SIMULATED CALL] to=%s, context=%s",
                    data.to_number,
                    data.context[:100],
                )
            else:
                raise RuntimeError("Twilio voice is not configured and simulation is disabled")
            metadata["call_sid"] = call_sid
        except Exception as e:
            logger.error("Call failed: %s", e)
            call_sid = "failed"
            metadata.update({"call_sid": call_sid, "error": str(e)})
            status = "failed"

        response = self._with_idempotency(
            {"call_id": call_id, "call_sid": call_sid, "status": status},
            key,
        )
        await self._record_comm(
            agent_id=data.agent_id,
            channel="voice",
            direction="outbound",
            recipient=data.to_number,
            content=data.context,
            metadata=metadata,
            status=status,
            idempotency_key=key,
            response=response,
            reserved_log=reserved_log,
        )
        return response

    async def send_sms(self, data) -> dict:
        sms_id = str(uuid.uuid4())
        from_number = (
            data.from_number
            or settings.twilio_phone_number
            or settings.jasmin_from_number
        )
        key = self._idempotency_key(data)
        reserved_log, replay = await self._reserve_comm(
            agent_id=data.agent_id,
            channel="sms",
            recipient=data.to_number,
            content=data.message,
            metadata={"from": from_number},
            idempotency_key=key,
        )
        if replay:
            return self._idempotent_response(reserved_log, id_field="sms_id")

        metadata = {"from": from_number}
        try:
            client = self._get_twilio()
            if client:
                message = await self._with_retries(
                    lambda: client.messages.create(
                        to=data.to_number,
                        from_=from_number,
                        body=data.message,
                    )
                )
                provider = "twilio"
                provider_id = message.sid
                status = "sent"
            elif self._jasmin_configured():
                provider = "jasmin"
                provider_id = await self._send_sms_jasmin(data, from_number)
                status = "sent"
            elif settings.communications_allow_simulation:
                provider = "simulation"
                provider_id = "local-only"
                status = "simulated"
                logger.info("[SIMULATED SMS] to=%s, msg=%s", data.to_number, data.message[:100])
            else:
                raise RuntimeError("SMS provider is not configured and simulation is disabled")
            metadata.update({"provider": provider, "provider_id": provider_id})
        except Exception as e:
            logger.error("SMS failed: %s", e)
            provider_id = "failed"
            metadata.update({"provider_id": provider_id, "error": str(e)})
            status = "failed"

        response = self._with_idempotency(
            {"sms_id": sms_id, "message_sid": provider_id, "status": status},
            key,
        )
        await self._record_comm(
            agent_id=data.agent_id,
            channel="sms",
            direction="outbound",
            recipient=data.to_number,
            content=data.message,
            metadata=metadata,
            status=status,
            idempotency_key=key,
            response=response,
            reserved_log=reserved_log,
        )
        return response

    async def send_email(self, data) -> dict:
        email_id = str(uuid.uuid4())
        key = self._idempotency_key(data)
        reserved_log, replay = await self._reserve_comm(
            agent_id=data.agent_id,
            channel="email",
            recipient=data.to_address,
            content=data.body[:500],
            metadata={"subject": data.subject, "cc": data.cc, "provider": "smtp"},
            idempotency_key=key,
        )
        if replay:
            return self._idempotent_response(reserved_log, id_field="email_id")

        metadata = {"subject": data.subject, "cc": data.cc, "provider": "smtp"}
        try:
            if self._smtp_configured():
                provider_id = await self._send_email_smtp(data)
                metadata["provider_id"] = provider_id
                status = "sent"
            elif settings.communications_allow_simulation:
                logger.info("[SIMULATED EMAIL] to=%s, subject=%s", data.to_address, data.subject)
                metadata["provider_id"] = "local-only"
                status = "simulated"
            else:
                raise RuntimeError("SMTP email is not configured and simulation is disabled")
        except Exception as e:
            logger.error("Email failed: %s", e)
            metadata["error"] = str(e)
            status = "failed"

        response = self._with_idempotency(
            {"email_id": email_id, "status": status, "provider": "smtp"},
            key,
        )
        await self._record_comm(
            agent_id=data.agent_id,
            channel="email",
            direction="outbound",
            recipient=data.to_address,
            content=data.body[:500],
            metadata=metadata,
            status=status,
            idempotency_key=key,
            response=response,
            reserved_log=reserved_log,
        )
        return response

    async def send_message(self, data) -> dict:
        msg_id = str(uuid.uuid4())
        platform = data.platform.lower()
        key = self._idempotency_key(data)
        reserved_log, replay = await self._reserve_comm(
            agent_id=data.agent_id,
            channel=platform,
            recipient=data.recipient,
            content=data.message,
            metadata={"platform": platform},
            idempotency_key=key,
        )
        if replay:
            return self._idempotent_response(reserved_log, id_field="message_id")

        metadata = {"platform": platform}
        try:
            if platform == "slack" and self._slack_configured():
                provider = "slack_webhook"
                provider_id = await self._send_slack_message(data)
                status = "sent"
            elif platform == "telegram" and self._telegram_configured():
                provider = "telegram_bot"
                provider_id = await self._send_telegram_message(data)
                status = "sent"
            elif platform == "whatsapp" and self._twilio_whatsapp_configured():
                provider = "twilio_whatsapp"
                provider_id = await self._send_twilio_whatsapp(data)
                status = "sent"
            elif settings.communications_allow_simulation:
                provider = "simulation"
                provider_id = "local-only"
                status = "simulated"
                logger.info(
                    "[SIMULATED %s] to=%s, msg=%s",
                    platform.upper(),
                    data.recipient,
                    data.message[:100],
                )
            else:
                raise RuntimeError(
                    f"{platform} provider is not configured and simulation is disabled"
                )
            metadata.update({"provider": provider, "provider_id": provider_id})
        except Exception as e:
            logger.error("%s message failed: %s", platform, e)
            metadata.update({"error": str(e), "provider_id": "failed"})
            status = "failed"

        response = self._with_idempotency(
            {
                "message_id": msg_id,
                "platform": platform,
                "status": status,
                "provider": metadata.get("provider"),
            },
            key,
        )
        await self._record_comm(
            agent_id=data.agent_id,
            channel=platform,
            direction="outbound",
            recipient=data.recipient,
            content=data.message,
            metadata=metadata,
            status=status,
            idempotency_key=key,
            response=response,
            reserved_log=reserved_log,
        )
        return response

    async def get_logs(self, channel: str | None = None, limit: int = 50) -> list[dict]:
        async with async_session() as session:
            query = (
                select(CommunicationLog)
                .order_by(desc(CommunicationLog.created_at))
                .limit(limit)
            )
            if channel:
                query = query.where(CommunicationLog.channel == channel)
            result = await session.execute(query)
            logs = result.scalars().all()
            return [
                {
                    "id": log.id,
                    "agent_id": log.agent_id,
                    "channel": log.channel,
                    "direction": log.direction,
                    "recipient": log.recipient,
                    "content": log.content[:200],
                    "metadata": log.metadata_,
                    "status": log.status,
                    "idempotency_key": log.idempotency_key,
                    "created_at": log.created_at.isoformat(),
                }
                for log in logs
            ]

    async def _reserve_comm(
        self,
        agent_id,
        channel: str,
        recipient: str,
        content: str,
        metadata: dict,
        idempotency_key: str | None,
    ) -> tuple[CommunicationLog | None, bool]:
        if not idempotency_key:
            return None, False

        async with async_session() as session:
            existing = await self._get_log_by_idempotency_key(session, idempotency_key)
            if existing:
                return existing, True
            log = CommunicationLog(
                id=str(uuid.uuid4()),
                agent_id=agent_id,
                channel=channel,
                direction="outbound",
                recipient=recipient,
                content=content,
                metadata_={**metadata, "idempotency_key": idempotency_key},
                status="pending",
                idempotency_key=idempotency_key,
            )
            session.add(log)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await self._get_log_by_idempotency_key(session, idempotency_key)
                if existing:
                    return existing, True
                raise
            return log, False

    async def _record_comm(
        self,
        agent_id,
        channel: str,
        direction: str,
        recipient: str,
        content: str,
        metadata: dict,
        status: str,
        idempotency_key: str | None = None,
        response: dict | None = None,
        reserved_log: CommunicationLog | None = None,
    ) -> None:
        metadata = dict(metadata)
        if response:
            metadata["response"] = response
        if idempotency_key:
            metadata["idempotency_key"] = idempotency_key

        async with async_session() as session:
            if reserved_log:
                log = (
                    await session.execute(
                        select(CommunicationLog).where(CommunicationLog.id == reserved_log.id)
                    )
                ).scalar_one()
                log.agent_id = agent_id
                log.channel = channel
                log.direction = direction
                log.recipient = recipient
                log.content = content
                log.metadata_ = metadata
                log.status = status
            else:
                log = CommunicationLog(
                    id=str(uuid.uuid4()),
                    agent_id=agent_id,
                    channel=channel,
                    direction=direction,
                    recipient=recipient,
                    content=content,
                    metadata_=metadata,
                    status=status,
                    idempotency_key=idempotency_key,
                )
                session.add(log)
            await session.commit()

    @staticmethod
    async def _get_log_by_idempotency_key(session, idempotency_key: str):
        return (
            await session.execute(
                select(CommunicationLog).where(
                    CommunicationLog.idempotency_key == idempotency_key
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    def _idempotency_key(data) -> str | None:
        value = getattr(data, "idempotency_key", None)
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _with_idempotency(response: dict, key: str | None) -> dict:
        if not key:
            return response
        return {**response, "idempotency_key": key, "idempotent_replay": False}

    @staticmethod
    def _idempotent_response(log: CommunicationLog | None, id_field: str) -> dict:
        if not log:
            return {id_field: None, "status": "pending", "idempotent_replay": True}
        metadata = log.metadata_ or {}
        response = dict(metadata.get("response") or {})
        if not response:
            response = {id_field: log.id, "status": log.status}
        response["idempotency_key"] = log.idempotency_key
        response["idempotent_replay"] = True
        return response

    @staticmethod
    def _twilio_configured() -> bool:
        return bool(
            settings.twilio_account_sid
            and settings.twilio_auth_token
            and settings.twilio_phone_number
        )

    @staticmethod
    def _twilio_whatsapp_configured() -> bool:
        return bool(CommsGateway._twilio_configured() and settings.twilio_whatsapp_from_number)

    @staticmethod
    def _smtp_configured() -> bool:
        return bool(settings.smtp_host and settings.smtp_from_email)

    @staticmethod
    def _jasmin_configured() -> bool:
        return bool(
            settings.jasmin_host
            and settings.jasmin_username
            and settings.jasmin_password
            and settings.jasmin_from_number
        )

    @staticmethod
    def _slack_configured() -> bool:
        return bool(settings.slack_webhook_url)

    @staticmethod
    def _telegram_configured() -> bool:
        return bool(settings.telegram_bot_token)

    @staticmethod
    def _status_item(
        channel: str,
        provider: str,
        configured: bool,
        implementation: str,
        simulation_enabled: bool,
        detail: str,
    ) -> dict:
        if configured:
            mode = "live"
        elif simulation_enabled:
            mode = "simulated"
        else:
            mode = "disabled"
        return {
            "channel": channel,
            "provider": provider,
            "configured": configured,
            "mode": mode,
            "implementation": implementation,
            "detail": detail,
        }

    async def _with_retries(self, operation: Callable[[], object]) -> object:
        attempts = max(1, settings.communications_retry_attempts)
        last_error = None
        for attempt in range(attempts):
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(operation),
                    timeout=settings.communications_provider_timeout_seconds,
                )
            except Exception as exc:
                last_error = exc
                if attempt < attempts - 1:
                    await asyncio.sleep(settings.communications_retry_backoff_seconds)
        raise last_error or RuntimeError("Provider operation failed")

    async def _with_async_retries(self, operation: Callable[[], Awaitable[object]]) -> object:
        attempts = max(1, settings.communications_retry_attempts)
        last_error = None
        for attempt in range(attempts):
            try:
                return await asyncio.wait_for(
                    operation(),
                    timeout=settings.communications_provider_timeout_seconds,
                )
            except Exception as exc:
                last_error = exc
                if attempt < attempts - 1:
                    await asyncio.sleep(settings.communications_retry_backoff_seconds)
        raise last_error or RuntimeError("Provider operation failed")

    async def _send_email_smtp(self, data) -> str:
        message = self._build_email_message(data)
        await self._with_retries(lambda: self._send_email_smtp_sync(message))
        return message["Message-ID"]

    @staticmethod
    def _build_email_message(data) -> EmailMessage:
        message = EmailMessage()
        message["From"] = settings.smtp_from_email
        message["To"] = data.to_address
        if data.cc:
            message["Cc"] = ", ".join(data.cc)
        message["Subject"] = data.subject
        message["Message-ID"] = f"<{uuid.uuid4()}@cyber-team.local>"
        message.set_content(data.body)
        return message

    @staticmethod
    def _send_email_smtp_sync(message: EmailMessage) -> None:
        recipients = [message["To"]]
        if message.get("Cc"):
            recipients.extend(
                address.strip() for address in message["Cc"].split(",") if address.strip()
            )
        timeout = settings.communications_provider_timeout_seconds
        if settings.smtp_use_tls:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=timeout,
                context=context,
            ) as server:
                CommsGateway._smtp_login_if_configured(server)
                server.send_message(message, to_addrs=recipients)
            return

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=timeout) as server:
            if settings.smtp_starttls:
                server.starttls(context=ssl.create_default_context())
            CommsGateway._smtp_login_if_configured(server)
            server.send_message(message, to_addrs=recipients)

    @staticmethod
    def _smtp_login_if_configured(server) -> None:
        if settings.smtp_username or settings.smtp_password:
            server.login(settings.smtp_username, settings.smtp_password)

    async def _send_sms_jasmin(self, data, from_number: str | None) -> str:
        scheme = "https" if settings.jasmin_use_tls else "http"
        params = {
            "username": settings.jasmin_username,
            "password": settings.jasmin_password,
            "from": from_number or settings.jasmin_from_number,
            "to": data.to_number,
            "content": data.message,
        }
        response = await self._with_async_retries(
            lambda: self._http_request(
                "GET",
                f"{scheme}://{settings.jasmin_host}:{settings.jasmin_port}/send",
                params=params,
            )
        )
        return str(response.get("id") or response.get("text") or "jasmin-submitted")

    async def _send_slack_message(self, data) -> str:
        payload = {"text": data.message}
        if data.recipient:
            payload["channel"] = data.recipient
        response = await self._with_async_retries(
            lambda: self._http_request("POST", settings.slack_webhook_url, json=payload)
        )
        return str(response.get("text") or "slack-submitted")

    async def _send_telegram_message(self, data) -> str:
        response = await self._with_async_retries(
            lambda: self._http_request(
                "POST",
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                json={"chat_id": data.recipient, "text": data.message},
            )
        )
        result = response.get("result") if isinstance(response.get("result"), dict) else {}
        return str(result.get("message_id") or "telegram-submitted")

    async def _send_twilio_whatsapp(self, data) -> str:
        client = self._get_twilio()
        if not client:
            raise RuntimeError("Twilio client is not configured")
        message = await self._with_retries(
            lambda: client.messages.create(
                to=self._normalize_whatsapp_number(data.recipient),
                from_=self._normalize_whatsapp_number(settings.twilio_whatsapp_from_number),
                body=data.message,
            )
        )
        return message.sid

    @staticmethod
    def _normalize_whatsapp_number(number: str) -> str:
        return number if number.startswith("whatsapp:") else f"whatsapp:{number}"

    @staticmethod
    async def _http_request(
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
    ) -> dict:
        timeout = settings.communications_provider_timeout_seconds
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, url, params=params, json=json)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                return response.json()
            return {"text": response.text}
