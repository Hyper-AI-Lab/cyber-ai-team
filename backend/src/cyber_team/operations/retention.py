"""Data retention and subject data lifecycle operations."""

import hashlib
import json
import logging
import uuid
import zlib
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, desc, false, func, or_, select

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import (
    ApprovalRequest,
    AuditEvent,
    AuditEventArchive,
    CommunicationLog,
    MemoryEntry,
    WorkflowRun,
)

logger = logging.getLogger(__name__)

TERMINAL_WORKFLOW_STATUSES = ("completed", "failed", "cancelled", "rejected")
AUDIT_ARCHIVE_ENCODING = "zlib+json"
AUDIT_SECURITY_PREFIXES = (
    "auth.",
    "authorization.",
    "credential.",
    "data_subject.",
    "security.",
    "session.",
)
AUDIT_GOVERNANCE_PREFIXES = (
    "action_policy.",
    "approval.",
    "control.",
    "domain_control.",
    "governor.",
    "observer.",
    "operating_model.",
    "owner.",
    "policy.",
    "retention.",
)


class RetentionService:
    def __init__(self, session_factory=async_session, memory_service=None):
        self._session_factory = session_factory
        self._memory_service = memory_service

    async def cleanup(self, *, dry_run: bool = True, now: datetime | None = None) -> dict:
        now = now or utc_now()
        batch_size = max(1, settings.retention_batch_size)

        async with self._session_factory() as session:
            conditions = self._retention_conditions(now)
            totals = {
                name: await self._count(session, model, condition)
                for name, (model, _id_column, condition) in conditions.items()
            }
            ids = {
                name: await self._select_ids(session, id_column, condition, batch_size)
                for name, (_model, id_column, condition) in conditions.items()
            }
            audit_conditions = self._audit_retention_conditions(now)
            audit_totals = {
                category: await self._count(session, AuditEvent, condition)
                for category, condition in audit_conditions.items()
            }
            audit_ids = {
                category: await self._select_ids(
                    session,
                    AuditEvent.id,
                    condition,
                    batch_size,
                )
                for category, condition in audit_conditions.items()
            }
            archive_batches: list[dict[str, Any]] = []

            if not dry_run:
                for name, (model, id_column, _condition) in conditions.items():
                    selected_ids = ids[name]
                    if selected_ids:
                        await session.execute(delete(model).where(id_column.in_(selected_ids)))
                for category, selected_ids in audit_ids.items():
                    if selected_ids:
                        archive_batches.append(
                            await self._archive_audit_events(
                                session,
                                category=category,
                                event_ids=selected_ids,
                                archived_at=now,
                            )
                        )
                session.add(
                    AuditEvent(
                        id=str(uuid.uuid4()),
                        event_type="retention.cleanup",
                        actor="system",
                        actor_type="system",
                        resource_type="retention_policy",
                        action="delete_expired_records",
                        outcome="success",
                        metadata_={
                            "counts": {
                                **{name: len(value) for name, value in ids.items()},
                                "audit_events": sum(len(value) for value in audit_ids.values()),
                            },
                            "audit_categories": {
                                name: len(value) for name, value in audit_ids.items()
                            },
                            "archive_batches": [
                                {
                                    "id": item["id"],
                                    "category": item["category"],
                                    "event_count": item["event_count"],
                                    "content_hash": item["content_hash"],
                                }
                                for item in archive_batches
                            ],
                            "dry_run": False,
                        },
                        created_at=now,
                    )
                )
                await session.commit()

        if not dry_run:
            await self._delete_memory_points(ids["memory_entries"])

        return {
            "dry_run": dry_run,
            "batch_size": batch_size,
            "cutoffs": self._cutoffs(now),
            "counts": {
                **(
                    totals
                    if dry_run
                    else {name: len(value) for name, value in ids.items()}
                ),
                "audit_events": (
                    sum(audit_totals.values())
                    if dry_run
                    else sum(len(value) for value in audit_ids.values())
                ),
            },
            "audit_categories": {
                name: {
                    "eligible": audit_totals[name],
                    "selected": len(audit_ids[name]),
                    "truncated": audit_totals[name] > len(audit_ids[name]),
                }
                for name in sorted(audit_conditions)
            },
            "archive_batches": archive_batches,
            "truncated": {
                **{name: totals[name] > len(value) for name, value in ids.items()},
                "audit_events": any(
                    audit_totals[name] > len(audit_ids[name]) for name in audit_conditions
                ),
            },
        }

    async def list_audit_archives(
        self,
        *,
        category: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 500))
        async with self._session_factory() as session:
            query = select(AuditEventArchive)
            if category:
                query = query.where(AuditEventArchive.category == category)
            archives = (
                (
                    await session.execute(
                        query.order_by(desc(AuditEventArchive.created_at)).limit(safe_limit)
                    )
                )
                .scalars()
                .all()
            )
            return [self._archive_to_dict(item) for item in archives]

    async def restore_audit_archive(
        self,
        archive_id: str,
        *,
        dry_run: bool = True,
        actor: str = "system",
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            archive = (
                await session.execute(
                    select(AuditEventArchive).where(AuditEventArchive.id == archive_id)
                )
            ).scalar_one_or_none()
            if not archive:
                raise ValueError("Audit archive not found")
            events = self._decode_archive(archive)
            event_ids = [str(item["id"]) for item in events]
            existing_ids = set(
                (
                    await session.execute(
                        select(AuditEvent.id).where(AuditEvent.id.in_(event_ids))
                    )
                ).scalars()
            )
            missing = [item for item in events if str(item["id"]) not in existing_ids]
            if not dry_run:
                for item in missing:
                    session.add(
                        AuditEvent(
                            id=str(item["id"]),
                            event_type=str(item["event_type"]),
                            actor=str(item["actor"]),
                            actor_type=str(item["actor_type"]),
                            resource_type=item.get("resource_type"),
                            resource_id=item.get("resource_id"),
                            action=item.get("action"),
                            outcome=str(item["outcome"]),
                            metadata_=item.get("metadata") or {},
                            created_at=datetime.fromisoformat(str(item["created_at"])),
                        )
                    )
                session.add(
                    AuditEvent(
                        id=str(uuid.uuid4()),
                        event_type="retention.audit_archive_restored",
                        actor=actor,
                        actor_type="user" if actor != "system" else "system",
                        resource_type="audit_archive",
                        resource_id=archive.id,
                        action="restore",
                        outcome="success",
                        metadata_={
                            "category": archive.category,
                            "content_hash": archive.content_hash,
                            "restored_count": len(missing),
                            "existing_count": len(existing_ids),
                        },
                    )
                )
                await session.commit()
            return {
                "archive": self._archive_to_dict(archive),
                "dry_run": dry_run,
                "hash_verified": True,
                "restored_count": 0 if dry_run else len(missing),
                "would_restore_count": len(missing),
                "existing_count": len(existing_ids),
            }

    async def export_subject_data(self, subject: str) -> dict:
        async with self._session_factory() as session:
            conditions = self._subject_conditions(subject, include_audit=True)
            return {
                "subject": subject,
                "memory_entries": await self._export_rows(
                    session,
                    MemoryEntry,
                    conditions["memory_entries"],
                    self._memory_to_dict,
                ),
                "communication_logs": await self._export_rows(
                    session,
                    CommunicationLog,
                    conditions["communication_logs"],
                    self._communication_to_dict,
                ),
                "approval_requests": await self._export_rows(
                    session,
                    ApprovalRequest,
                    conditions["approval_requests"],
                    self._approval_to_dict,
                ),
                "audit_events": await self._export_rows(
                    session,
                    AuditEvent,
                    conditions["audit_events"],
                    self._audit_to_dict,
                ),
            }

    async def delete_subject_data(
        self,
        subject: str,
        *,
        dry_run: bool = True,
        include_audit: bool = False,
    ) -> dict:
        conditions = self._subject_conditions(subject, include_audit=include_audit)
        async with self._session_factory() as session:
            totals = {
                name: await self._count(session, model, conditions[name])
                for name, model in self._subject_models(include_audit).items()
            }
            memory_ids = await self._select_ids(
                session,
                MemoryEntry.id,
                conditions["memory_entries"],
                max(1, settings.retention_batch_size),
            )

            if not dry_run:
                for name, model in self._subject_models(include_audit).items():
                    await session.execute(delete(model).where(conditions[name]))
                session.add(
                    AuditEvent(
                        id=str(uuid.uuid4()),
                        event_type="data_subject.deleted",
                        actor="system",
                        actor_type="system",
                        resource_type="data_subject",
                        resource_id=subject,
                        action="delete_subject_data",
                        outcome="success",
                        metadata_={
                            "counts": totals,
                            "include_audit": include_audit,
                        },
                    )
                )
                await session.commit()

        if not dry_run:
            await self._delete_memory_points(memory_ids)

        return {
            "subject": subject,
            "dry_run": dry_run,
            "include_audit": include_audit,
            "counts": totals,
            "audit_events_retained": not include_audit,
        }

    def _retention_conditions(self, now: datetime) -> dict:
        cutoffs = self._cutoff_values(now)
        expired_memory = (MemoryEntry.expires_at.is_not(None)) & (MemoryEntry.expires_at <= now)
        memory_condition = expired_memory
        memory_cutoff = cutoffs["memory_entries"]
        if memory_cutoff:
            memory_condition = or_(
                expired_memory,
                (
                    (MemoryEntry.created_at <= memory_cutoff)
                    & (MemoryEntry.memory_type != "pinned")
                ),
            )
        return {
            "memory_entries": (MemoryEntry, MemoryEntry.id, memory_condition),
            "communication_logs": (
                CommunicationLog,
                CommunicationLog.id,
                self._created_before(CommunicationLog.created_at, cutoffs["communication_logs"]),
            ),
            "workflow_runs": (
                WorkflowRun,
                WorkflowRun.id,
                self._completed_workflow_condition(cutoffs["workflow_runs"]),
            ),
            "approval_requests": (
                ApprovalRequest,
                ApprovalRequest.id,
                self._resolved_approval_condition(cutoffs["approval_requests"]),
            ),
        }

    def _audit_retention_conditions(self, now: datetime) -> dict[str, Any]:
        cutoffs = self._cutoff_values(now)
        security = self._prefix_condition(AUDIT_SECURITY_PREFIXES)
        governance = self._prefix_condition(AUDIT_GOVERNANCE_PREFIXES)
        return {
            "security": security
            & self._created_before(AuditEvent.created_at, cutoffs["audit_events_security"]),
            "governance": governance
            & ~security
            & self._created_before(AuditEvent.created_at, cutoffs["audit_events_governance"]),
            "operational": ~(security | governance)
            & self._created_before(AuditEvent.created_at, cutoffs["audit_events_operational"]),
        }

    @staticmethod
    def _prefix_condition(prefixes: tuple[str, ...]):
        return or_(*(AuditEvent.event_type.startswith(prefix) for prefix in prefixes))

    @staticmethod
    def _created_before(column, cutoff: datetime | None):
        return column <= cutoff if cutoff else false()

    @staticmethod
    def _completed_workflow_condition(cutoff: datetime | None):
        if not cutoff:
            return false()
        return (
            (WorkflowRun.completed_at.is_not(None))
            & (WorkflowRun.completed_at <= cutoff)
            & (WorkflowRun.status.in_(TERMINAL_WORKFLOW_STATUSES))
        )

    @staticmethod
    def _resolved_approval_condition(cutoff: datetime | None):
        if not cutoff:
            return false()
        return (
            (ApprovalRequest.resolved_at.is_not(None))
            & (ApprovalRequest.resolved_at <= cutoff)
            & (ApprovalRequest.status != "pending")
        )

    def _cutoffs(self, now: datetime) -> dict[str, str]:
        return {
            name: value.isoformat()
            for name, value in self._cutoff_values(now).items()
            if value is not None
        }

    @staticmethod
    def _cutoff_values(now: datetime) -> dict[str, datetime | None]:
        return {
            "memory_entries": RetentionService._cutoff(now, settings.retention_memory_days),
            "communication_logs": RetentionService._cutoff(
                now,
                settings.retention_communication_log_days,
            ),
            "workflow_runs": RetentionService._cutoff(now, settings.retention_workflow_run_days),
            "approval_requests": RetentionService._cutoff(
                now,
                settings.retention_approval_request_days,
            ),
            "audit_events_operational": RetentionService._cutoff(
                now,
                settings.retention_audit_operational_days,
            ),
            "audit_events_governance": RetentionService._cutoff(
                now,
                settings.retention_audit_governance_days,
            ),
            "audit_events_security": RetentionService._cutoff(
                now,
                settings.retention_audit_security_days,
            ),
        }

    @staticmethod
    def _cutoff(now: datetime, days: int) -> datetime | None:
        if days <= 0:
            return None
        return now - timedelta(days=days)

    @staticmethod
    async def _count(session, model, condition) -> int:
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(model).where(condition)
                )
            ).scalar_one()
        )

    @staticmethod
    async def _select_ids(session, id_column, condition, limit: int) -> list[str]:
        result = await session.execute(select(id_column).where(condition).limit(limit))
        return [str(row[0]) for row in result.all()]

    @staticmethod
    async def _export_rows(session, model, condition, serializer) -> list[dict]:
        result = await session.execute(select(model).where(condition))
        return [serializer(row) for row in result.scalars().all()]

    async def _delete_memory_points(self, memory_ids: list[str]) -> None:
        if not memory_ids or not self._memory_service:
            return
        try:
            await self._memory_service.delete_memory_points(memory_ids)
        except Exception as exc:
            logger.warning("Failed to delete retained memory vectors: %s", exc)

    async def _archive_audit_events(
        self,
        session,
        *,
        category: str,
        event_ids: list[str],
        archived_at: datetime,
    ) -> dict[str, Any]:
        events = (
            (
                await session.execute(
                    select(AuditEvent)
                    .where(AuditEvent.id.in_(event_ids))
                    .order_by(AuditEvent.created_at, AuditEvent.id)
                )
            )
            .scalars()
            .all()
        )
        if not events:
            raise RuntimeError("Audit archive selection became empty before archival")
        serialized = [self._audit_to_dict(item) for item in events]
        raw_payload = json.dumps(
            serialized,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        content_hash = hashlib.sha256(raw_payload).hexdigest()
        compressed = zlib.compress(raw_payload, level=9)
        archive = (
            await session.execute(
                select(AuditEventArchive).where(
                    AuditEventArchive.category == category,
                    AuditEventArchive.content_hash == content_hash,
                )
            )
        ).scalar_one_or_none()
        if archive is None:
            archive = AuditEventArchive(
                category=category,
                id=f"audit_archive_{uuid.uuid4().hex}",
                period_start=events[0].created_at,
                period_end=events[-1].created_at,
                event_count=len(events),
                first_event_at=events[0].created_at,
                last_event_at=events[-1].created_at,
                content_hash=content_hash,
                encoding=AUDIT_ARCHIVE_ENCODING,
                payload=compressed,
                raw_size=len(raw_payload),
                compressed_size=len(compressed),
                metadata_={
                    "source_table": "audit_events",
                    "partition": category,
                    "schema_version": 1,
                },
                created_at=archived_at,
            )
            session.add(archive)
            await session.flush()
        await session.execute(delete(AuditEvent).where(AuditEvent.id.in_(event_ids)))
        return self._archive_to_dict(archive)

    @staticmethod
    def _decode_archive(archive: AuditEventArchive) -> list[dict[str, Any]]:
        if archive.encoding != AUDIT_ARCHIVE_ENCODING:
            raise ValueError(f"Unsupported audit archive encoding: {archive.encoding}")
        try:
            raw_payload = zlib.decompress(archive.payload)
        except zlib.error as exc:
            raise ValueError("Audit archive payload is corrupt") from exc
        if hashlib.sha256(raw_payload).hexdigest() != archive.content_hash:
            raise ValueError("Audit archive content hash does not match its payload")
        decoded = json.loads(raw_payload.decode("utf-8"))
        if not isinstance(decoded, list) or len(decoded) != archive.event_count:
            raise ValueError("Audit archive event count does not match its payload")
        return decoded

    @staticmethod
    def _archive_to_dict(archive: AuditEventArchive) -> dict[str, Any]:
        return {
            "id": archive.id,
            "category": archive.category,
            "period_start": archive.period_start.isoformat(),
            "period_end": archive.period_end.isoformat(),
            "event_count": archive.event_count,
            "first_event_at": archive.first_event_at.isoformat(),
            "last_event_at": archive.last_event_at.isoformat(),
            "content_hash": archive.content_hash,
            "encoding": archive.encoding,
            "raw_size": archive.raw_size,
            "compressed_size": archive.compressed_size,
            "metadata": archive.metadata_ or {},
            "created_at": archive.created_at.isoformat(),
        }

    @staticmethod
    def _subject_conditions(subject: str, *, include_audit: bool) -> dict:
        namespaces = [
            subject,
            f"person:{subject}",
            f"entity:{subject}",
            f"customer:{subject}",
            f"agent:{subject}",
        ]
        conditions = {
            "memory_entries": or_(
                MemoryEntry.agent_id == subject,
                MemoryEntry.namespace.in_(namespaces),
            ),
            "communication_logs": or_(
                CommunicationLog.agent_id == subject,
                CommunicationLog.recipient == subject,
            ),
            "approval_requests": or_(
                ApprovalRequest.agent_id == subject,
                ApprovalRequest.requester == subject,
                ApprovalRequest.target_id == subject,
            ),
        }
        if include_audit:
            conditions["audit_events"] = or_(
                AuditEvent.actor == subject,
                AuditEvent.resource_id == subject,
            )
        return conditions

    @staticmethod
    def _subject_models(include_audit: bool) -> dict:
        models = {
            "memory_entries": MemoryEntry,
            "communication_logs": CommunicationLog,
            "approval_requests": ApprovalRequest,
        }
        if include_audit:
            models["audit_events"] = AuditEvent
        return models

    @staticmethod
    def _memory_to_dict(entry: MemoryEntry) -> dict[str, Any]:
        return {
            "id": entry.id,
            "agent_id": entry.agent_id,
            "memory_type": entry.memory_type,
            "namespace": entry.namespace,
            "content": entry.content,
            "metadata": entry.metadata_,
            "importance": entry.importance,
            "created_at": RetentionService._iso(entry.created_at),
            "expires_at": RetentionService._iso(entry.expires_at),
        }

    @staticmethod
    def _communication_to_dict(log: CommunicationLog) -> dict[str, Any]:
        return {
            "id": log.id,
            "agent_id": log.agent_id,
            "channel": log.channel,
            "direction": log.direction,
            "recipient": log.recipient,
            "content": log.content,
            "metadata": log.metadata_,
            "status": log.status,
            "idempotency_key": log.idempotency_key,
            "created_at": RetentionService._iso(log.created_at),
        }

    @staticmethod
    def _approval_to_dict(request: ApprovalRequest) -> dict[str, Any]:
        return {
            "id": request.id,
            "agent_id": request.agent_id,
            "action_type": request.action_type,
            "requester": request.requester,
            "requester_type": request.requester_type,
            "risk_level": request.risk_level,
            "target_type": request.target_type,
            "target_id": request.target_id,
            "status": request.status,
            "created_at": RetentionService._iso(request.created_at),
            "resolved_at": RetentionService._iso(request.resolved_at),
        }

    @staticmethod
    def _audit_to_dict(event: AuditEvent) -> dict[str, Any]:
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
            "created_at": RetentionService._iso(event.created_at),
        }

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return value.isoformat() if value else None
