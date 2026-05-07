import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from cyber_team.db import async_session
from cyber_team.db.models import AuditEvent


class AuditService:
    async def record(
        self,
        event_type: str,
        actor: str = "system",
        actor_type: str = "system",
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        action: Optional[str] = None,
        outcome: str = "success",
        metadata: Optional[dict] = None,
    ) -> dict:
        event_id = str(uuid.uuid4())
        async with async_session() as session:
            event = AuditEvent(
                id=event_id,
                event_type=event_type,
                actor=actor,
                actor_type=actor_type,
                resource_type=resource_type,
                resource_id=resource_id,
                action=action,
                outcome=outcome,
                metadata_=metadata or {},
            )
            session.add(event)
            await session.commit()
            return self._event_to_dict(event)

    async def list_events(
        self,
        limit: int = 100,
        event_type: Optional[str] = None,
        actor: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
    ) -> list[dict]:
        limit = max(1, min(limit, 500))
        async with async_session() as session:
            query = select(AuditEvent)
            if event_type:
                query = query.where(AuditEvent.event_type == event_type)
            if actor:
                query = query.where(AuditEvent.actor == actor)
            if resource_type:
                query = query.where(AuditEvent.resource_type == resource_type)
            if resource_id:
                query = query.where(AuditEvent.resource_id == resource_id)
            result = await session.execute(query.order_by(AuditEvent.created_at.desc()).limit(limit))
            return [self._event_to_dict(event) for event in result.scalars().all()]

    @staticmethod
    def _event_to_dict(event: AuditEvent) -> dict:
        return {
            "id": event.id,
            "event_type": event.event_type,
            "actor": event.actor,
            "actor_type": event.actor_type,
            "resource_type": event.resource_type,
            "resource_id": event.resource_id,
            "action": event.action,
            "outcome": event.outcome,
            "metadata": event.metadata_,
            "created_at": event.created_at.isoformat(),
        }
