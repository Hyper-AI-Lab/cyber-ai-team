"""Data retention and subject data lifecycle operations."""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, false, func, or_, select

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import (
    ApprovalRequest,
    AuditEvent,
    CommunicationLog,
    MemoryEntry,
    WorkflowRun,
)

logger = logging.getLogger(__name__)

TERMINAL_WORKFLOW_STATUSES = ("completed", "failed", "cancelled", "rejected")


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

            if not dry_run:
                for name, (model, id_column, _condition) in conditions.items():
                    selected_ids = ids[name]
                    if selected_ids:
                        await session.execute(delete(model).where(id_column.in_(selected_ids)))
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
                            "counts": {name: len(value) for name, value in ids.items()},
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
            "counts": totals if dry_run else {name: len(value) for name, value in ids.items()},
            "truncated": {name: totals[name] > len(value) for name, value in ids.items()},
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
            "audit_events": (
                AuditEvent,
                AuditEvent.id,
                self._created_before(AuditEvent.created_at, cutoffs["audit_events"]),
            ),
        }

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
            "audit_events": RetentionService._cutoff(now, settings.retention_audit_event_days),
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
