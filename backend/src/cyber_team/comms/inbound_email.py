"""Inbound email polling and storage."""

from __future__ import annotations

import asyncio
import imaplib
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import InboundEmailMessage

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ParsedInboundEmail:
    provider: str
    mailbox: str
    provider_uid: str
    message_id: str | None
    from_address: str
    to_addresses: list[str]
    cc_addresses: list[str]
    subject: str
    text_body: str
    html_body: str | None
    snippet: str
    received_at: datetime | None
    metadata: dict[str, Any]


class InboundEmailService:
    """Poll an IMAP mailbox and persist inbound messages idempotently."""

    def __init__(self, metrics_service=None, audit_service=None) -> None:
        self._metrics = metrics_service
        self._audit = audit_service
        self._last_validation: dict[str, Any] | None = None
        self._last_poll: dict[str, Any] | None = None

    def integration_status(self) -> dict[str, Any]:
        missing = self._missing_config()
        configured = settings.inbound_email_enabled and not missing
        if configured:
            mode = "live"
            detail = f"Inbound email polls {settings.imap_mailbox} on IMAP."
        elif settings.inbound_email_enabled:
            mode = "configuration_required"
            detail = "Inbound email is enabled but IMAP settings are incomplete."
        else:
            mode = "disabled"
            detail = "Inbound email polling is disabled."
        return {
            "channel": "inbound_email",
            "provider": "imap",
            "configured": configured,
            "mode": mode,
            "implementation": "implemented",
            "detail": detail,
            "mailbox": settings.imap_mailbox,
            "address": settings.inbound_email_address,
            "missing": missing,
            "last_poll": self._last_poll,
        }

    def last_validation_result(self) -> dict[str, Any] | None:
        return self._last_validation

    def last_poll_result(self) -> dict[str, Any] | None:
        return self._last_poll

    async def validate(self) -> dict[str, Any]:
        checked_at = utc_now().isoformat() + "+00:00"
        missing = self._missing_config()
        if missing:
            result = {
                "status": "blocked",
                "checked_at": checked_at,
                "provider": "imap",
                "results": [
                    {
                        "channel": "inbound_email",
                        "provider": "imap",
                        "status": "configuration_required",
                        "configured": False,
                        "mode": "configuration_required",
                        "missing": missing,
                        "detail": (
                            "IMAP validation requires inbound email and complete "
                            "mailbox settings."
                        ),
                        "network_check": "skipped",
                    }
                ],
            }
            self._last_validation = result
            return result

        try:
            await asyncio.to_thread(self._validate_sync)
        except Exception as exc:  # noqa: BLE001 - provider errors need to be reported.
            logger.warning("IMAP validation failed: %s", exc)
            result = {
                "status": "failed",
                "checked_at": checked_at,
                "provider": "imap",
                "results": [
                    {
                        "channel": "inbound_email",
                        "provider": "imap",
                        "status": "failed",
                        "configured": True,
                        "mode": "live",
                        "missing": [],
                        "detail": str(exc),
                        "network_check": "failed",
                    }
                ],
            }
            self._last_validation = result
            return result

        result = {
            "status": "ready",
            "checked_at": checked_at,
            "provider": "imap",
            "results": [
                {
                    "channel": "inbound_email",
                    "provider": "imap",
                    "status": "ready",
                    "configured": True,
                    "mode": "live",
                    "missing": [],
                    "detail": "IMAP login, mailbox selection, and NOOP check succeeded.",
                    "network_check": "passed",
                }
            ],
        }
        self._last_validation = result
        return result

    async def poll_once(self) -> dict[str, Any]:
        started_at = utc_now()
        missing = self._missing_config()
        if missing:
            result = {
                "status": "blocked",
                "provider": "imap",
                "checked_at": started_at.isoformat() + "+00:00",
                "missing": missing,
                "fetched": 0,
                "stored": 0,
                "duplicates": 0,
                "errors": [],
            }
            self._last_poll = result
            self._record_metric("blocked", 0)
            return result

        errors: list[str] = []
        try:
            parsed_messages = await asyncio.to_thread(self._fetch_unseen_sync)
        except Exception as exc:  # noqa: BLE001 - provider errors need to be reported.
            logger.exception("Inbound email poll failed")
            errors.append(str(exc))
            result = {
                "status": "failed",
                "provider": "imap",
                "checked_at": started_at.isoformat() + "+00:00",
                "fetched": 0,
                "stored": 0,
                "duplicates": 0,
                "errors": errors,
            }
            self._last_poll = result
            self._record_metric("failed", 0)
            return result

        stored = 0
        duplicates = 0
        stored_ids: list[str] = []
        for parsed in parsed_messages:
            message_id, was_inserted = await self._store_message(parsed)
            if was_inserted:
                stored += 1
                stored_ids.append(message_id)
            else:
                duplicates += 1

        status = "ready" if not errors else "failed"
        result = {
            "status": status,
            "provider": "imap",
            "checked_at": started_at.isoformat() + "+00:00",
            "mailbox": settings.imap_mailbox,
            "fetched": len(parsed_messages),
            "stored": stored,
            "duplicates": duplicates,
            "stored_ids": stored_ids,
            "errors": errors,
        }
        self._last_poll = result
        self._record_metric(status, stored)
        if self._audit and stored:
            await self._audit.record_control_evidence(
                control_id="inbound_email.poll",
                control_area="soc2_availability",
                actor="system",
                outcome=status,
                evidence=result,
            )
        return result

    async def list_messages(
        self,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        async with async_session() as session:
            query = (
                select(InboundEmailMessage)
                .order_by(
                    desc(InboundEmailMessage.received_at),
                    desc(InboundEmailMessage.first_seen_at),
                )
                .limit(min(max(limit, 1), 200))
            )
            if status:
                query = query.where(InboundEmailMessage.status == status)
            result = await session.execute(query)
            return [self._serialize_message(message) for message in result.scalars().all()]

    async def get_message(self, message_id: str) -> dict[str, Any] | None:
        async with async_session() as session:
            message = (
                await session.execute(
                    select(InboundEmailMessage).where(InboundEmailMessage.id == message_id)
                )
            ).scalar_one_or_none()
            if not message:
                return None
            return self._serialize_message(message, include_body=True)

    async def update_status(self, message_id: str, status: str) -> dict[str, Any] | None:
        async with async_session() as session:
            message = (
                await session.execute(
                    select(InboundEmailMessage).where(InboundEmailMessage.id == message_id)
                )
            ).scalar_one_or_none()
            if not message:
                return None
            message.status = status
            message.last_seen_at = utc_now()
            await session.commit()
            await session.refresh(message)
            return self._serialize_message(message, include_body=True)

    def _validate_sync(self) -> None:
        with self._connect() as mail:
            status, _ = mail.select(settings.imap_mailbox, readonly=True)
            if status != "OK":
                raise RuntimeError(f"Could not select mailbox {settings.imap_mailbox}")
            mail.noop()

    def _fetch_unseen_sync(self) -> list[ParsedInboundEmail]:
        with self._connect() as mail:
            status, _ = mail.select(
                settings.imap_mailbox,
                readonly=not settings.inbound_email_mark_seen,
            )
            if status != "OK":
                raise RuntimeError(f"Could not select mailbox {settings.imap_mailbox}")
            status, data = mail.uid("SEARCH", None, "UNSEEN")
            if status != "OK":
                raise RuntimeError("IMAP UNSEEN search failed")
            uids = (data[0] or b"").split()
            max_messages = max(1, settings.inbound_email_max_messages_per_poll)
            selected_uids = uids[-max_messages:]
            messages: list[ParsedInboundEmail] = []
            for uid in selected_uids:
                fetch_status, msg_data = mail.uid("FETCH", uid, "(RFC822)")
                if fetch_status != "OK" or not msg_data:
                    logger.warning("IMAP fetch failed for uid=%s status=%s", uid, fetch_status)
                    continue
                raw_message = self._first_message_bytes(msg_data)
                if not raw_message:
                    continue
                messages.append(
                    self._parse_message(uid.decode("ascii", errors="replace"), raw_message)
                )
                if settings.inbound_email_mark_seen:
                    mail.uid("STORE", uid, "+FLAGS", "(\\Seen)")
            return messages

    def _connect(self):
        timeout = settings.communications_provider_timeout_seconds
        if settings.imap_use_ssl:
            mail = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port, timeout=timeout)
        else:
            mail = imaplib.IMAP4(settings.imap_host, settings.imap_port, timeout=timeout)
        mail.login(settings.imap_username, settings.imap_password)
        return mail

    async def _store_message(self, parsed: ParsedInboundEmail) -> tuple[str, bool]:
        now = utc_now()
        message = InboundEmailMessage(
            id=str(uuid.uuid4()),
            provider=parsed.provider,
            mailbox=parsed.mailbox,
            provider_uid=parsed.provider_uid,
            message_id=parsed.message_id,
            from_address=parsed.from_address,
            to_addresses=parsed.to_addresses,
            cc_addresses=parsed.cc_addresses,
            subject=parsed.subject,
            text_body=parsed.text_body,
            html_body=parsed.html_body,
            snippet=parsed.snippet,
            status="new",
            received_at=parsed.received_at,
            first_seen_at=now,
            last_seen_at=now,
            metadata_=parsed.metadata,
        )
        async with async_session() as session:
            session.add(message)
            try:
                await session.commit()
                return message.id, True
            except IntegrityError:
                await session.rollback()
                existing = (
                    await session.execute(
                        select(InboundEmailMessage).where(
                            InboundEmailMessage.provider == parsed.provider,
                            InboundEmailMessage.mailbox == parsed.mailbox,
                            InboundEmailMessage.provider_uid == parsed.provider_uid,
                        )
                    )
                ).scalar_one()
                existing.last_seen_at = now
                await session.commit()
                return existing.id, False

    def _record_metric(self, status: str, stored: int) -> None:
        if not self._metrics:
            return
        self._metrics.increment(
            "cyberteam_inbound_email_polls_total",
            {"provider": "imap", "status": status},
        )
        if stored:
            self._metrics.increment(
                "cyberteam_inbound_email_messages_total",
                {"provider": "imap", "status": "stored"},
                value=float(stored),
            )

    @staticmethod
    def _first_message_bytes(msg_data) -> bytes | None:
        for part in msg_data:
            if isinstance(part, tuple) and isinstance(part[1], bytes):
                return part[1]
        return None

    @staticmethod
    def _parse_message(uid: str, raw_message: bytes) -> ParsedInboundEmail:
        message = BytesParser(policy=policy.default).parsebytes(raw_message)
        subject = InboundEmailService._decode_header(message.get("Subject", ""))
        from_address = InboundEmailService._addresses(message.get_all("From", []))
        to_addresses = InboundEmailService._addresses(message.get_all("To", []))
        cc_addresses = InboundEmailService._addresses(message.get_all("Cc", []))
        text_body, html_body = InboundEmailService._extract_bodies(message)
        snippet = InboundEmailService._make_snippet(text_body or html_body or "")
        received_at = InboundEmailService._parse_date(message.get("Date"))
        return ParsedInboundEmail(
            provider="imap",
            mailbox=settings.imap_mailbox,
            provider_uid=uid,
            message_id=message.get("Message-ID"),
            from_address=from_address[0] if from_address else "",
            to_addresses=to_addresses,
            cc_addresses=cc_addresses,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            snippet=snippet,
            received_at=received_at,
            metadata={
                "inbound_address": settings.inbound_email_address,
                "raw_from": message.get("From"),
                "raw_to": message.get("To"),
                "raw_cc": message.get("Cc"),
            },
        )

    @staticmethod
    def _decode_header(value: str | None) -> str:
        if not value:
            return ""
        return str(make_header(decode_header(value))).strip()

    @staticmethod
    def _addresses(values: list[str]) -> list[str]:
        decoded_values = [InboundEmailService._decode_header(value) for value in values]
        return [email for _name, email in getaddresses(decoded_values) if email]

    @staticmethod
    def _extract_bodies(message: Message) -> tuple[str, str | None]:
        text_parts: list[str] = []
        html_parts: list[str] = []
        if isinstance(message, EmailMessage) and message.is_multipart():
            parts = message.walk()
        else:
            parts = [message]
        for part in parts:
            content_disposition = part.get_content_disposition()
            if content_disposition == "attachment":
                continue
            content_type = part.get_content_type()
            if content_type not in {"text/plain", "text/html"}:
                continue
            try:
                content = part.get_content()
            except Exception:  # noqa: BLE001 - malformed messages should not stop polling.
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                content = payload.decode(charset, errors="replace")
            if content_type == "text/plain":
                text_parts.append(str(content))
            else:
                html_parts.append(str(content))
        return "\n\n".join(text_parts).strip(), "\n\n".join(html_parts).strip() or None

    @staticmethod
    def _make_snippet(content: str) -> str:
        cleaned = re.sub(r"<[^>]+>", " ", content)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned[:300]

    @staticmethod
    def _parse_date(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo:
            parsed = parsed.astimezone(UTC).replace(tzinfo=None)
        return parsed

    @staticmethod
    def _serialize_message(
        message: InboundEmailMessage,
        include_body: bool = False,
    ) -> dict[str, Any]:
        payload = {
            "id": message.id,
            "provider": message.provider,
            "mailbox": message.mailbox,
            "provider_uid": message.provider_uid,
            "message_id": message.message_id,
            "from_address": message.from_address,
            "to_addresses": message.to_addresses,
            "cc_addresses": message.cc_addresses,
            "subject": message.subject,
            "snippet": message.snippet,
            "status": message.status,
            "received_at": message.received_at.isoformat() if message.received_at else None,
            "first_seen_at": message.first_seen_at.isoformat(),
            "last_seen_at": message.last_seen_at.isoformat(),
            "metadata": message.metadata_,
        }
        if include_body:
            payload["text_body"] = message.text_body
            payload["html_body"] = message.html_body
        return payload

    @staticmethod
    def _missing_config() -> list[str]:
        missing: list[str] = []
        if not settings.inbound_email_enabled:
            missing.append("INBOUND_EMAIL_ENABLED")
        if not settings.imap_host:
            missing.append("IMAP_HOST")
        if not settings.imap_username:
            missing.append("IMAP_USERNAME")
        if not settings.imap_password:
            missing.append("IMAP_PASSWORD")
        if not settings.imap_mailbox:
            missing.append("IMAP_MAILBOX")
        return missing
