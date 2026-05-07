"""Communications Gateway — telephony, SMS, email, messaging."""

import uuid
import logging
import asyncio
from datetime import datetime
from typing import Optional

from sqlalchemy import select, desc

from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import CommunicationLog

logger = logging.getLogger(__name__)


class CommsGateway:
    def __init__(self):
        self._twilio_client = None

    def _get_twilio(self):
        if not self._twilio_client and settings.twilio_account_sid:
            from twilio.rest import Client
            self._twilio_client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        return self._twilio_client

    async def make_call(self, data) -> dict:
        call_id = str(uuid.uuid4())
        from_number = data.from_number or settings.twilio_phone_number

        try:
            client = self._get_twilio()
            if client:
                call = await asyncio.to_thread(
                    client.calls.create,
                    to=data.to_number,
                    from_=from_number,
                    twiml=f'<Response><Say>{data.context}</Say></Response>',
                )
                call_sid = call.sid
                status = "initiated"
            else:
                call_sid = "local-only"
                status = "simulated"
                logger.info(f"[SIMULATED CALL] to={data.to_number}, context={data.context[:100]}")
        except Exception as e:
            logger.error(f"Call failed: {e}")
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
                message = await asyncio.to_thread(
                    client.messages.create,
                    to=data.to_number,
                    from_=from_number,
                    body=data.message,
                )
                msg_sid = message.sid
                status = "sent"
            else:
                msg_sid = "local-only"
                status = "simulated"
                logger.info(f"[SIMULATED SMS] to={data.to_number}, msg={data.message[:100]}")
        except Exception as e:
            logger.error(f"SMS failed: {e}")
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

        try:
            # For now, log the email (SMTP config would be needed for real sending)
            logger.info(f"[EMAIL] to={data.to_address}, subject={data.subject}")
            status = "simulated"
        except Exception as e:
            logger.error(f"Email failed: {e}")
            status = "failed"

        await self._log_comm(
            agent_id=data.agent_id,
            channel="email",
            direction="outbound",
            recipient=data.to_address,
            content=data.body[:500],
            metadata={"subject": data.subject, "cc": data.cc},
            status=status,
        )
        return {"email_id": email_id, "status": status}

    async def send_message(self, data) -> dict:
        msg_id = str(uuid.uuid4())
        platform = data.platform

        # Platform-specific sending would be implemented here
        # For now, log and simulate
        logger.info(f"[{platform.upper()}] to={data.recipient}, msg={data.message[:100]}")

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

    async def get_logs(self, channel: Optional[str] = None, limit: int = 50) -> list[dict]:
        async with async_session() as session:
            query = select(CommunicationLog).order_by(desc(CommunicationLog.created_at)).limit(limit)
            if channel:
                query = query.where(CommunicationLog.channel == channel)
            result = await session.execute(query)
            logs = result.scalars().all()
            return [
                {
                    "id": l.id,
                    "agent_id": l.agent_id,
                    "channel": l.channel,
                    "direction": l.direction,
                    "recipient": l.recipient,
                    "content": l.content[:200],
                    "metadata": l.metadata_,
                    "status": l.status,
                    "created_at": l.created_at.isoformat(),
                }
                for l in logs
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
