"""Communications Gateway - telephony, SMS, email, messaging."""

import asyncio
import logging
import smtplib
import ssl
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.message import EmailMessage
from threading import Lock

import httpx
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError

from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import CommunicationLog

logger = logging.getLogger(__name__)


class CircuitOpenError(RuntimeError):
    pass


class CommsGateway:
    def __init__(self, metrics_service=None):
        self._twilio_client = None
        self._metrics = metrics_service
        self._circuit_lock = Lock()
        self._circuit_breakers: dict[str, dict[str, float | int]] = {}
        self._last_validation_result: dict | None = None

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
            self._status_item(
                channel="voice",
                provider="asterisk_ari",
                configured=self._asterisk_configured(),
                implementation="implemented",
                simulation_enabled=simulation_enabled,
                detail=(
                    "Asterisk ARI can originate calls into a configured Stasis app when enabled."
                ),
            ),
        ]

    def last_validation_result(self) -> dict | None:
        return self._last_validation_result

    async def validate_integrations(self, provider: str = "smtp") -> dict:
        normalized_provider = (provider or "smtp").strip().lower()
        if normalized_provider not in {"smtp", "all"}:
            result = self._validation_result(
                status="failed",
                checked_at=self._checked_at(),
                provider=normalized_provider,
                results=[
                    {
                        "channel": "unknown",
                        "provider": normalized_provider,
                        "status": "failed",
                        "configured": False,
                        "mode": "unavailable",
                        "missing": [],
                        "detail": "No validation check is registered for this provider.",
                    }
                ],
            )
            self._last_validation_result = result
            return result

        checked_at = self._checked_at()
        status_items = self.integration_status()
        if normalized_provider == "all":
            results = []
            for item in status_items:
                if item["provider"] == "smtp":
                    results.append(await self._validate_smtp(checked_at))
                else:
                    results.append(self._configuration_validation(item, checked_at))
        else:
            results = [await self._validate_smtp(checked_at)]

        result = self._validation_result(
            status=self._overall_validation_status(results),
            checked_at=checked_at,
            provider=normalized_provider,
            results=results,
        )
        self._last_validation_result = result
        return result

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
            response = self._idempotent_response(reserved_log, id_field="call_id")
            self._record_delivery_metric(
                "voice",
                response.get("provider"),
                response["status"],
                True,
            )
            return response

        metadata = {"from": from_number}
        try:
            client = self._get_twilio()
            if client:
                provider = "twilio"
                metadata["provider"] = provider
                call = await self._with_retries(
                    lambda: client.calls.create(
                        to=data.to_number,
                        from_=from_number,
                        twiml=f'<Response><Say>{data.context}</Say></Response>',
                    ),
                    provider=provider,
                )
                call_sid = call.sid
                status = "initiated"
            elif self._asterisk_configured():
                provider = "asterisk_ari"
                metadata["provider"] = provider
                call_sid = await self._send_asterisk_call(data, from_number)
                status = "initiated"
            elif settings.communications_allow_simulation:
                provider = "simulation"
                metadata["provider"] = provider
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
            {
                "call_id": call_id,
                "call_sid": call_sid,
                "status": status,
                "provider": metadata.get("provider"),
            },
            key,
        )
        self._record_delivery_metric("voice", metadata.get("provider"), status)
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
            response = self._idempotent_response(reserved_log, id_field="sms_id")
            self._record_delivery_metric("sms", response.get("provider"), response["status"], True)
            return response

        metadata = {"from": from_number}
        try:
            client = self._get_twilio()
            if client:
                message = await self._with_retries(
                    lambda: client.messages.create(
                        to=data.to_number,
                        from_=from_number,
                        body=data.message,
                    ),
                    provider="twilio",
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
            {
                "sms_id": sms_id,
                "message_sid": provider_id,
                "status": status,
                "provider": metadata.get("provider"),
            },
            key,
        )
        self._record_delivery_metric("sms", metadata.get("provider"), status)
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
            response = self._idempotent_response(reserved_log, id_field="email_id")
            self._record_delivery_metric(
                "email",
                response.get("provider"),
                response["status"],
                True,
            )
            return response

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
        self._record_delivery_metric("email", metadata.get("provider"), status)
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
            response = self._idempotent_response(reserved_log, id_field="message_id")
            self._record_delivery_metric(
                platform,
                response.get("provider"),
                response["status"],
                True,
            )
            return response

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
        self._record_delivery_metric(platform, metadata.get("provider"), status)
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
    def _asterisk_configured() -> bool:
        return bool(
            settings.asterisk_ari_enabled
            and settings.asterisk_host
            and settings.asterisk_ari_user
            and settings.asterisk_ari_password
            and settings.asterisk_ari_endpoint_template
        )

    @staticmethod
    def _smtp_configured() -> bool:
        has_incomplete_auth_pair = bool(settings.smtp_username) ^ bool(settings.smtp_password)
        return bool(
            settings.smtp_host
            and settings.smtp_from_email
            and not has_incomplete_auth_pair
        )

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

    def _status_item(
        self,
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
            "circuit": self._circuit_snapshot(provider),
        }

    @staticmethod
    def _checked_at() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _validation_result(
        *,
        status: str,
        checked_at: str,
        provider: str,
        results: list[dict],
    ) -> dict:
        return {
            "status": status,
            "checked_at": checked_at,
            "provider": provider,
            "results": results,
        }

    @staticmethod
    def _overall_validation_status(results: list[dict]) -> str:
        statuses = {item["status"] for item in results}
        if "failed" in statuses:
            return "failed"
        if "configuration_required" in statuses:
            return "blocked"
        return "ready"

    def _configuration_validation(self, item: dict, checked_at: str) -> dict:
        if item.get("configured"):
            status = "ready"
            detail = item.get("detail") or "Provider configuration is present."
        else:
            status = "configuration_required"
            detail = item.get("detail") or "Provider configuration is missing."
        return {
            "channel": item.get("channel"),
            "provider": item.get("provider"),
            "status": status,
            "checked_at": checked_at,
            "configured": bool(item.get("configured")),
            "mode": item.get("mode"),
            "missing": self._missing_fields_for_provider(str(item.get("provider") or "")),
            "detail": detail,
            "network_check": "not_supported",
        }

    async def _validate_smtp(self, checked_at: str) -> dict:
        missing = self._smtp_missing_fields()
        if missing:
            return {
                "channel": "email",
                "provider": "smtp",
                "status": "configuration_required",
                "checked_at": checked_at,
                "configured": False,
                "mode": "simulated" if settings.communications_allow_simulation else "disabled",
                "missing": missing,
                "detail": "SMTP validation requires host, sender, and complete auth settings.",
                "network_check": "skipped",
            }

        try:
            await self._with_retries(self._validate_smtp_sync, provider="smtp")
        except Exception as exc:
            logger.warning("SMTP validation failed: %s", exc)
            return {
                "channel": "email",
                "provider": "smtp",
                "status": "failed",
                "checked_at": checked_at,
                "configured": True,
                "mode": "live",
                "missing": [],
                "detail": str(exc),
                "network_check": "failed",
            }

        return {
            "channel": "email",
            "provider": "smtp",
            "status": "ready",
            "checked_at": checked_at,
            "configured": True,
            "mode": "live",
            "missing": [],
            "detail": "SMTP connection, TLS/auth handshake, and NOOP check succeeded.",
            "network_check": "passed",
        }

    @staticmethod
    def _missing_fields_for_provider(provider: str) -> list[str]:
        if provider == "smtp":
            return CommsGateway._smtp_missing_fields()
        if provider == "twilio":
            return [
                name
                for name, value in {
                    "TWILIO_ACCOUNT_SID": settings.twilio_account_sid,
                    "TWILIO_AUTH_TOKEN": settings.twilio_auth_token,
                    "TWILIO_PHONE_NUMBER": settings.twilio_phone_number,
                }.items()
                if not value
            ]
        if provider == "jasmin":
            return [
                name
                for name, value in {
                    "JASMIN_HOST": settings.jasmin_host,
                    "JASMIN_USERNAME": settings.jasmin_username,
                    "JASMIN_PASSWORD": settings.jasmin_password,
                    "JASMIN_FROM_NUMBER": settings.jasmin_from_number,
                }.items()
                if not value
            ]
        if provider == "slack_webhook" and not settings.slack_webhook_url:
            return ["SLACK_WEBHOOK_URL"]
        if provider == "telegram_bot" and not settings.telegram_bot_token:
            return ["TELEGRAM_BOT_TOKEN"]
        if provider == "twilio_whatsapp" and not settings.twilio_whatsapp_from_number:
            return ["TWILIO_WHATSAPP_FROM_NUMBER"]
        if provider == "asterisk_ari":
            return [
                name
                for name, value in {
                    "ASTERISK_ARI_ENABLED": settings.asterisk_ari_enabled,
                    "ASTERISK_HOST": settings.asterisk_host,
                    "ASTERISK_ARI_USER": settings.asterisk_ari_user,
                    "ASTERISK_ARI_PASSWORD": settings.asterisk_ari_password,
                    "ASTERISK_ARI_ENDPOINT_TEMPLATE": settings.asterisk_ari_endpoint_template,
                }.items()
                if not value
            ]
        return []

    @staticmethod
    def _smtp_missing_fields() -> list[str]:
        missing = [
            name
            for name, value in {
                "SMTP_HOST": settings.smtp_host,
                "SMTP_FROM_EMAIL": settings.smtp_from_email,
            }.items()
            if not value
        ]
        if bool(settings.smtp_username) ^ bool(settings.smtp_password):
            if not settings.smtp_username:
                missing.append("SMTP_USERNAME")
            if not settings.smtp_password:
                missing.append("SMTP_PASSWORD")
        return missing

    def _circuit_snapshot(self, provider: str) -> dict:
        now = time.time()
        with self._circuit_lock:
            state = self._circuit_breakers.get(provider, {})
            opened_until = float(state.get("opened_until", 0))
            failures = int(state.get("failures", 0))
        return {
            "state": "open" if opened_until > now else "closed",
            "failures": failures,
            "opened_until": opened_until if opened_until > now else None,
        }

    def _ensure_provider_available(self, provider: str) -> None:
        snapshot = self._circuit_snapshot(provider)
        if snapshot["state"] == "open":
            raise CircuitOpenError(f"Circuit breaker is open for provider {provider}")

    def _record_provider_success(self, provider: str) -> None:
        should_record_metric = False
        with self._circuit_lock:
            previous = self._circuit_breakers.get(provider, {})
            if not previous:
                return
            should_record_metric = bool(
                int(previous.get("failures", 0)) > 0
                or float(previous.get("opened_until", 0)) > 0
            )
            self._circuit_breakers[provider] = {"failures": 0, "opened_until": 0}
        if should_record_metric and self._metrics:
            self._metrics.record_circuit_breaker_state(provider, "closed")

    def _record_provider_failure(self, provider: str) -> None:
        threshold = max(1, settings.communications_circuit_breaker_failure_threshold)
        cooldown = max(1, settings.communications_circuit_breaker_cooldown_seconds)
        opened = False
        with self._circuit_lock:
            state = self._circuit_breakers.setdefault(
                provider,
                {"failures": 0, "opened_until": 0},
            )
            failures = int(state.get("failures", 0)) + 1
            state["failures"] = failures
            if failures >= threshold:
                state["opened_until"] = time.time() + cooldown
                opened = True
        if opened and self._metrics:
            self._metrics.record_circuit_breaker_state(provider, "open")

    def _record_delivery_metric(
        self,
        channel: str,
        provider: str | None,
        status: str,
        idempotent_replay: bool = False,
    ) -> None:
        if self._metrics:
            self._metrics.record_communication_delivery(
                channel=channel,
                provider=provider or "unknown",
                status=status,
                idempotent_replay=idempotent_replay,
            )

    async def _with_retries(self, operation: Callable[[], object], provider: str) -> object:
        self._ensure_provider_available(provider)
        attempts = max(1, settings.communications_retry_attempts)
        last_error = None
        for attempt in range(attempts):
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(operation),
                    timeout=settings.communications_provider_timeout_seconds,
                )
                self._record_provider_success(provider)
                return result
            except Exception as exc:
                last_error = exc
                if attempt < attempts - 1:
                    await asyncio.sleep(settings.communications_retry_backoff_seconds)
        self._record_provider_failure(provider)
        raise last_error or RuntimeError("Provider operation failed")

    async def _with_async_retries(
        self,
        operation: Callable[[], Awaitable[object]],
        provider: str,
    ) -> object:
        self._ensure_provider_available(provider)
        attempts = max(1, settings.communications_retry_attempts)
        last_error = None
        for attempt in range(attempts):
            try:
                result = await asyncio.wait_for(
                    operation(),
                    timeout=settings.communications_provider_timeout_seconds,
                )
                self._record_provider_success(provider)
                return result
            except Exception as exc:
                last_error = exc
                if attempt < attempts - 1:
                    await asyncio.sleep(settings.communications_retry_backoff_seconds)
        self._record_provider_failure(provider)
        raise last_error or RuntimeError("Provider operation failed")

    async def _send_email_smtp(self, data) -> str:
        message = self._build_email_message(data)
        await self._with_retries(
            lambda: self._send_email_smtp_sync(message),
            provider="smtp",
        )
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
    def _validate_smtp_sync() -> None:
        timeout = settings.communications_provider_timeout_seconds
        if settings.smtp_use_tls:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=timeout,
                context=context,
            ) as server:
                server.ehlo()
                CommsGateway._smtp_login_if_configured(server)
                server.noop()
            return

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=timeout) as server:
            server.ehlo()
            if settings.smtp_starttls:
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            CommsGateway._smtp_login_if_configured(server)
            server.noop()

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
            ),
            provider="jasmin",
        )
        return str(response.get("id") or response.get("text") or "jasmin-submitted")

    async def _send_asterisk_call(self, data, from_number: str | None) -> str:
        scheme = "https" if settings.asterisk_ari_use_tls else "http"
        endpoint = settings.asterisk_ari_endpoint_template.format(to_number=data.to_number)
        payload = {
            "endpoint": endpoint,
            "app": settings.asterisk_ari_app,
            "appArgs": data.context[:1000],
            "callerId": from_number or settings.asterisk_caller_id,
            "timeout": int(settings.communications_provider_timeout_seconds),
        }
        response = await self._with_async_retries(
            lambda: self._http_request(
                "POST",
                f"{scheme}://{settings.asterisk_host}:{settings.asterisk_port}/ari/channels",
                params=payload,
                auth=(settings.asterisk_ari_user, settings.asterisk_ari_password),
            ),
            provider="asterisk_ari",
        )
        return str(response.get("id") or response.get("text") or "asterisk-originated")

    async def _send_slack_message(self, data) -> str:
        payload = {"text": data.message}
        if data.recipient:
            payload["channel"] = data.recipient
        response = await self._with_async_retries(
            lambda: self._http_request("POST", settings.slack_webhook_url, json=payload),
            provider="slack_webhook",
        )
        return str(response.get("text") or "slack-submitted")

    async def _send_telegram_message(self, data) -> str:
        response = await self._with_async_retries(
            lambda: self._http_request(
                "POST",
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                json={"chat_id": data.recipient, "text": data.message},
            ),
            provider="telegram_bot",
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
            ),
            provider="twilio_whatsapp",
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
        auth: tuple[str, str] | None = None,
    ) -> dict:
        timeout = settings.communications_provider_timeout_seconds
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                method,
                url,
                params=params,
                json=json,
                auth=auth,
            )
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                return response.json()
            return {"text": response.text}
