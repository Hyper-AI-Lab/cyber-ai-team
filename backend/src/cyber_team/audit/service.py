import hashlib
import json
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from cyber_team.clock import utc_now
from cyber_team.db import async_session
from cyber_team.db.models import AuditEvent, AuditEventRollup
from cyber_team.observability.metrics import MetricsService


class AuditService:
    def __init__(self, metrics_service: MetricsService | None = None):
        self._metrics = metrics_service

    async def record(
        self,
        event_type: str,
        actor: str = "system",
        actor_type: str = "system",
        resource_type: str | None = None,
        resource_id: str | None = None,
        action: str | None = None,
        outcome: str = "success",
        metadata: dict | None = None,
    ) -> dict:
        events = await self.record_batch(
            [
                {
                    "event_type": event_type,
                    "actor": actor,
                    "actor_type": actor_type,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "action": action,
                    "outcome": outcome,
                    "metadata": metadata,
                }
            ]
        )
        return events[0]

    async def record_batch(self, entries: list[dict]) -> list[dict]:
        """Append related audit events atomically in one database transaction."""
        if not entries:
            return []
        events = []
        async with async_session() as session:
            for entry in entries:
                event = AuditEvent(
                    id=str(uuid.uuid4()),
                    event_type=entry["event_type"],
                    actor=entry.get("actor", "system"),
                    actor_type=entry.get("actor_type", "system"),
                    resource_type=entry.get("resource_type"),
                    resource_id=entry.get("resource_id"),
                    action=entry.get("action"),
                    outcome=entry.get("outcome", "success"),
                    metadata_=entry.get("metadata") or {},
                )
                session.add(event)
                events.append(event)
            await session.commit()
        if self._metrics:
            for event in events:
                self._metrics.record_audit_event(event.event_type, event.outcome)
        return [self._event_to_dict(event) for event in events]

    async def record_control_evidence(
        self,
        *,
        control_id: str,
        control_area: str,
        actor: str = "system",
        outcome: str = "success",
        evidence: dict | None = None,
    ) -> dict:
        """Append SOC2/GDPR readiness evidence to the immutable audit stream."""
        return await self.record(
            event_type="control.evidence",
            actor=actor,
            actor_type="system",
            resource_type="control",
            resource_id=control_id,
            action=control_area,
            outcome=outcome,
            metadata={
                "control_id": control_id,
                "control_area": control_area,
                "evidence": evidence or {},
            },
        )

    async def record_rollup(
        self,
        *,
        event_type: str,
        actor: str = "system",
        actor_type: str = "system",
        resource_type: str | None = None,
        action: str | None = None,
        outcome: str = "success",
        rollup_group: str = "default",
        metadata: dict | None = None,
        observed_at: datetime | None = None,
    ) -> dict:
        """Count a repeated low-signal event in one durable row per UTC hour.

        Callers must not use rollups for denials, mutations, approvals, side
        effects, failures, or owner actions. Those remain individual immutable
        audit events through :meth:`record`.
        """
        observed_at = observed_at or utc_now()
        hour_bucket = observed_at.replace(minute=0, second=0, microsecond=0)
        identity = {
            "event_type": event_type,
            "actor": actor,
            "actor_type": actor_type,
            "resource_type": resource_type,
            "action": action,
            "outcome": outcome,
            "rollup_group": rollup_group,
        }
        rollup_key = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        values = {
            "id": f"audit_rollup_{uuid.uuid4().hex}",
            "hour_bucket": hour_bucket,
            "rollup_key": rollup_key,
            "event_type": event_type,
            "actor": actor,
            "actor_type": actor_type,
            "resource_type": resource_type,
            "action": action,
            "outcome": outcome,
            "count": 1,
            "first_at": observed_at,
            "last_at": observed_at,
            "sample_metadata": metadata or {},
            "created_at": observed_at,
            "updated_at": observed_at,
        }
        async with async_session() as session:
            dialect = session.get_bind().dialect.name
            if dialect == "postgresql":
                statement = postgresql_insert(AuditEventRollup).values(**values)
                statement = statement.on_conflict_do_update(
                    index_elements=["hour_bucket", "rollup_key"],
                    set_={
                        "count": AuditEventRollup.count + 1,
                        "last_at": observed_at,
                        "sample_metadata": metadata or {},
                        "updated_at": observed_at,
                    },
                )
                await session.execute(statement)
            elif dialect == "sqlite":
                statement = sqlite_insert(AuditEventRollup).values(**values)
                statement = statement.on_conflict_do_update(
                    index_elements=["hour_bucket", "rollup_key"],
                    set_={
                        "count": AuditEventRollup.count + 1,
                        "last_at": observed_at,
                        "sample_metadata": metadata or {},
                        "updated_at": observed_at,
                    },
                )
                await session.execute(statement)
            else:
                current = (
                    await session.execute(
                        select(AuditEventRollup).where(
                            AuditEventRollup.hour_bucket == hour_bucket,
                            AuditEventRollup.rollup_key == rollup_key,
                        )
                    )
                ).scalar_one_or_none()
                if current:
                    current.count += 1
                    current.last_at = observed_at
                    current.sample_metadata = metadata or {}
                    current.updated_at = observed_at
                else:
                    session.add(AuditEventRollup(**values))
            await session.commit()
            rollup = (
                await session.execute(
                    select(AuditEventRollup).where(
                        AuditEventRollup.hour_bucket == hour_bucket,
                        AuditEventRollup.rollup_key == rollup_key,
                    )
                )
            ).scalar_one()
        if self._metrics:
            self._metrics.record_audit_event(event_type, outcome)
        return self._rollup_to_dict(rollup)

    async def list_events(
        self,
        limit: int = 100,
        event_type: str | None = None,
        actor: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        resource_id_prefix: str | None = None,
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
            if resource_id_prefix:
                query = query.where(AuditEvent.resource_id.startswith(resource_id_prefix))
            result = await session.execute(
                query.order_by(AuditEvent.created_at.desc()).limit(limit)
            )
            return [self._event_to_dict(event) for event in result.scalars().all()]

    async def list_rollups(
        self,
        *,
        limit: int = 100,
        event_type: str | None = None,
    ) -> list[dict]:
        limit = max(1, min(limit, 500))
        async with async_session() as session:
            query = select(AuditEventRollup)
            if event_type:
                query = query.where(AuditEventRollup.event_type == event_type)
            result = await session.execute(
                query.order_by(AuditEventRollup.last_at.desc()).limit(limit)
            )
            return [self._rollup_to_dict(item) for item in result.scalars().all()]

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

    @staticmethod
    def _rollup_to_dict(rollup: AuditEventRollup) -> dict:
        return {
            "id": rollup.id,
            "event_type": rollup.event_type,
            "actor": rollup.actor,
            "actor_type": rollup.actor_type,
            "resource_type": rollup.resource_type,
            "action": rollup.action,
            "outcome": rollup.outcome,
            "count": rollup.count,
            "metadata": rollup.sample_metadata,
            "hour_bucket": rollup.hour_bucket.isoformat(),
            "first_at": rollup.first_at.isoformat(),
            "last_at": rollup.last_at.isoformat(),
            "created_at": rollup.created_at.isoformat(),
            "updated_at": rollup.updated_at.isoformat(),
            "rollup": True,
        }
