"""Communications Gateway - telephony, SMS, email, messaging."""

import asyncio
import logging
import smtplib
import ssl
import uuid
from collections.abc import Callable
from email.message import EmailMessage

from sqlalchemy import desc, select

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
        twilio_ready = self._twilio_configured()
        smtp_ready = self._smtp_configured()
        simulation_enabled = settings.communications_allow_simulation
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
                detail="Outbound SMS uses Twilio messages when credentials are configured.",
            ),
            self._status_item(
                channel="email",
                provider="smtp",
                configured=smtp_ready,
                implementation="implemented",
                simulation_enabled=simulation_enabled,
                detail="Outbound email uses SMTP when host and sender are configured.",
            ),
            self._status_item(
                channel="messaging",
                provider="none",
                configured=False,
                implementation="planned",
                simulation_enabled=simulation_enabled,
                detail="Telegram, WhatsApp, and Slack provider adapters are not wired yet.",
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
            {
                "channel": "jasmin",
                "provider": "jasmin",
                "configured": bool(getattr(settings, "jasmin_host", "")),
                "mode": "profile_only",
                "implementation": "compose_profile",
                "detail": "Jasmin can be started from Docker Compose, but runtime SMS uses Twilio.",
            },
        ]

    async def make_call(self, data) -> dict:
        call_id = str(uuid.uuid4())
        from_number = data.from_number or settings.twilio_phone_number

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
        except Exception as e:
            logger.error("Call failed: %s", e)
            call_sid = "failed"
            status = "failed"

        await self._log_comm(
            agent_id=data.agent_id,
            channel="voice",
            direction="outbound",
            recipient=data.to_number,
            content=data.context,
            metadata={"call_sid": call_sid, "from": from_number},
            status=status,
        )
        return {"call_id": call_id, "call_sid": call_sid, "status": status}

    async def send_sms(self, data) -> dict:
        sms_id = str(uuid.uuid4())
        from_number = data.from_number or settings.twilio_phone_number

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
                msg_sid = message.sid
                status = "sent"
            elif settings.communications_allow_simulation:
                msg_sid = "local-only"
                status = "simulated"
                logger.info("[SIMULATED SMS] to=%s, msg=%s", data.to_number, data.message[:100])
            else:
                raise RuntimeError("Twilio SMS is not configured and simulation is disabled")
        except Exception as e:
            logger.error("SMS failed: %s", e)
            msg_sid = "failed"
            status = "failed"

        await self._log_comm(
            agent_id=data.agent_id,
            channel="sms",
            direction="outbound",
            recipient=data.to_number,
            content=data.message,
            metadata={"message_sid": msg_sid, "from": from_number},
            status=status,
        )
        return {"sms_id": sms_id, "message_sid": msg_sid, "status": status}

    async def send_email(self, data) -> dict:
        email_id = str(uuid.uuid4())
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

        await self._log_comm(
            agent_id=data.agent_id,
            channel="email",
            direction="outbound",
            recipient=data.to_address,
            content=data.body[:500],
            metadata=metadata,
            status=status,
        )
        return {"email_id": email_id, "status": status, "provider": "smtp"}

    async def send_message(self, data) -> dict:
        msg_id = str(uuid.uuid4())
        platform = data.platform

        if not settings.communications_allow_simulation:
            await self._log_comm(
                agent_id=data.agent_id,
                channel=platform,
                direction="outbound",
                recipient=data.recipient,
                content=data.message,
                metadata={"platform": platform, "error": "provider_not_configured"},
                status="failed",
            )
            return {
                "message_id": msg_id,
                "platform": platform,
                "status": "failed",
                "error": "Messaging provider is not configured and simulation is disabled",
            }

        logger.info(
            "[SIMULATED %s] to=%s, msg=%s",
            platform.upper(),
            data.recipient,
            data.message[:100],
        )

        await self._log_comm(
            agent_id=data.agent_id,
            channel=platform,
            direction="outbound",
            recipient=data.recipient,
            content=data.message,
            metadata={"platform": platform},
            status="simulated",
        )
        return {"message_id": msg_id, "platform": platform, "status": "simulated"}

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
                    "created_at": log.created_at.isoformat(),
                }
                for log in logs
            ]

    async def _log_comm(self, agent_id, channel, direction, recipient, content, metadata, status):
        async with async_session() as session:
            log = CommunicationLog(
                id=str(uuid.uuid4()),
                agent_id=agent_id,
                channel=channel,
                direction=direction,
                recipient=recipient,
                content=content,
                metadata_=metadata,
                status=status,
            )
            session.add(log)
            await session.commit()

    @staticmethod
    def _twilio_configured() -> bool:
        return bool(
            settings.twilio_account_sid
            and settings.twilio_auth_token
            and settings.twilio_phone_number
        )

    @staticmethod
    def _smtp_configured() -> bool:
        return bool(settings.smtp_host and settings.smtp_from_email)

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
