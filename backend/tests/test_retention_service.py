from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.config import settings
from cyber_team.db import Base
from cyber_team.db.models import (
    Agent,
    ApprovalRequest,
    AuditEvent,
    AuditEventArchive,
    CommunicationLog,
    MemoryEntry,
    Workflow,
    WorkflowRun,
)
from cyber_team.operations.retention import RetentionService


async def build_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def count_rows(session_factory, model) -> int:
    async with session_factory() as session:
        return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


def agent(agent_id: str) -> Agent:
    return Agent(
        id=agent_id,
        role_family="operations",
        role_name="Operator",
        instructions="Operate",
        tools=[],
        memory_namespace=f"agent:{agent_id}",
        approval_policy="manual",
        status="active",
        config={},
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )


class FakeMemoryService:
    def __init__(self):
        self.deleted_points: list[list[str]] = []

    async def delete_memory_points(self, memory_ids: list[str]) -> None:
        self.deleted_points.append(memory_ids)


@pytest.mark.asyncio
async def test_retention_cleanup_deletes_expired_and_old_records(monkeypatch):
    now = datetime(2026, 5, 26, 12, 0, 0)
    old = now - timedelta(days=40)
    recent = now - timedelta(days=1)
    monkeypatch.setattr(settings, "retention_memory_days", 30)
    monkeypatch.setattr(settings, "retention_communication_log_days", 30)
    monkeypatch.setattr(settings, "retention_workflow_run_days", 30)
    monkeypatch.setattr(settings, "retention_approval_request_days", 30)
    monkeypatch.setattr(settings, "retention_audit_event_days", 30)
    monkeypatch.setattr(settings, "retention_audit_operational_days", 30)
    monkeypatch.setattr(settings, "retention_audit_governance_days", 30)
    monkeypatch.setattr(settings, "retention_audit_security_days", 30)
    monkeypatch.setattr(settings, "retention_batch_size", 50)
    engine, session_factory = await build_session_factory()
    memory_service = FakeMemoryService()

    try:
        async with session_factory() as session:
            session.add(agent("agent-1"))
            session.add(
                Workflow(
                    id="workflow-1",
                    name="Retention workflow",
                    description=None,
                    graph_definition={},
                    status="active",
                    trigger_type="manual",
                    trigger_config={},
                    created_at=old,
                    updated_at=recent,
                )
            )
            session.add_all(
                [
                    WorkflowRun(
                        id="run-old",
                        workflow_id="workflow-1",
                        status="completed",
                        current_node="done",
                        state={},
                        result={},
                        error=None,
                        started_at=old,
                        completed_at=old,
                    ),
                    MemoryEntry(
                        id="memory-old",
                        agent_id="agent-1",
                        memory_type="episodic",
                        namespace="entity:customer-1",
                        content="old memory",
                        metadata_={},
                        importance=0.5,
                        created_at=old,
                        expires_at=None,
                    ),
                    MemoryEntry(
                        id="memory-pinned",
                        agent_id="agent-1",
                        memory_type="pinned",
                        namespace="entity:customer-1",
                        content="pinned memory",
                        metadata_={},
                        importance=1.0,
                        created_at=old,
                        expires_at=None,
                    ),
                    MemoryEntry(
                        id="memory-expired",
                        agent_id="agent-1",
                        memory_type="episodic",
                        namespace="entity:customer-1",
                        content="expired memory",
                        metadata_={},
                        importance=0.5,
                        created_at=recent,
                        expires_at=old,
                    ),
                    MemoryEntry(
                        id="memory-recent",
                        agent_id="agent-1",
                        memory_type="episodic",
                        namespace="entity:customer-1",
                        content="recent memory",
                        metadata_={},
                        importance=0.5,
                        created_at=recent,
                        expires_at=None,
                    ),
                    CommunicationLog(
                        id="comm-old",
                        agent_id="agent-1",
                        channel="email",
                        direction="outbound",
                        recipient="customer-1",
                        content="old email",
                        metadata_={},
                        status="sent",
                        created_at=old,
                    ),
                    CommunicationLog(
                        id="comm-recent",
                        agent_id="agent-1",
                        channel="email",
                        direction="outbound",
                        recipient="customer-2",
                        content="recent email",
                        metadata_={},
                        status="sent",
                        created_at=recent,
                    ),
                    ApprovalRequest(
                        id="approval-old",
                        agent_id="agent-1",
                        action_type="send_email",
                        action_description="old approval",
                        action_payload={},
                        requester="system",
                        requester_type="system",
                        risk_level="medium",
                        status="approved",
                        resolved_at=old,
                        created_at=old,
                    ),
                    ApprovalRequest(
                        id="approval-pending",
                        agent_id="agent-1",
                        action_type="send_email",
                        action_description="pending approval",
                        action_payload={},
                        requester="system",
                        requester_type="system",
                        risk_level="medium",
                        status="pending",
                        resolved_at=None,
                        created_at=old,
                    ),
                    AuditEvent(
                        id="audit-old",
                        event_type="old",
                        actor="system",
                        actor_type="system",
                        outcome="success",
                        metadata_={},
                        created_at=old,
                    ),
                    AuditEvent(
                        id="audit-recent",
                        event_type="recent",
                        actor="system",
                        actor_type="system",
                        outcome="success",
                        metadata_={},
                        created_at=recent,
                    ),
                ]
            )
            await session.commit()

        service = RetentionService(session_factory=session_factory, memory_service=memory_service)
        preview = await service.cleanup(dry_run=True, now=now)

        assert preview["counts"]["memory_entries"] == 2
        assert preview["counts"]["communication_logs"] == 1
        assert preview["counts"]["workflow_runs"] == 1
        assert preview["counts"]["approval_requests"] == 1
        assert preview["counts"]["audit_events"] == 1

        result = await service.cleanup(dry_run=False, now=now)

        assert result["counts"]["memory_entries"] == 2
        assert set(memory_service.deleted_points[0]) == {"memory-old", "memory-expired"}
        assert await count_rows(session_factory, MemoryEntry) == 2
        assert await count_rows(session_factory, CommunicationLog) == 1
        assert await count_rows(session_factory, WorkflowRun) == 0
        assert await count_rows(session_factory, ApprovalRequest) == 1
        assert await count_rows(session_factory, AuditEvent) == 2
        assert await count_rows(session_factory, AuditEventArchive) == 1
        archives = await service.list_audit_archives(category="operational")
        assert len(archives) == 1
        assert archives[0]["event_count"] == 1
        assert archives[0]["content_hash"]

        preview_restore = await service.restore_audit_archive(archives[0]["id"])
        assert preview_restore["hash_verified"] is True
        assert preview_restore["would_restore_count"] == 1
        assert preview_restore["restored_count"] == 0

        restored = await service.restore_audit_archive(
            archives[0]["id"],
            dry_run=False,
            actor="owner@example.com",
        )
        assert restored["restored_count"] == 1
        assert await count_rows(session_factory, AuditEventArchive) == 1
        async with session_factory() as session:
            old_event = await session.get(AuditEvent, "audit-old")
        assert old_event is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_audit_retention_is_category_partitioned_and_hash_verified(monkeypatch):
    now = datetime(2026, 5, 26, 12, 0, 0)
    old = now - timedelta(days=40)
    for field in (
        "retention_audit_operational_days",
        "retention_audit_governance_days",
        "retention_audit_security_days",
    ):
        monkeypatch.setattr(settings, field, 30)
    monkeypatch.setattr(settings, "retention_batch_size", 50)
    engine, session_factory = await build_session_factory()
    service = RetentionService(session_factory=session_factory)
    try:
        async with session_factory() as session:
            session.add_all(
                [
                    AuditEvent(
                        id="audit-security",
                        event_type="authorization.denied",
                        actor="agent",
                        actor_type="agent",
                        outcome="denied",
                        metadata_={},
                        created_at=old,
                    ),
                    AuditEvent(
                        id="audit-governance",
                        event_type="approval.requested",
                        actor="agent",
                        actor_type="agent",
                        outcome="success",
                        metadata_={},
                        created_at=old,
                    ),
                    AuditEvent(
                        id="audit-operational",
                        event_type="workflow.completed",
                        actor="workflow",
                        actor_type="system",
                        outcome="success",
                        metadata_={},
                        created_at=old,
                    ),
                ]
            )
            await session.commit()

        preview = await service.cleanup(dry_run=True, now=now)
        assert preview["counts"]["audit_events"] == 3
        assert {
            key: value["eligible"] for key, value in preview["audit_categories"].items()
        } == {"governance": 1, "operational": 1, "security": 1}

        result = await service.cleanup(dry_run=False, now=now)
        assert result["counts"]["audit_events"] == 3
        assert len(result["archive_batches"]) == 3
        archives = await service.list_audit_archives()
        assert {item["category"] for item in archives} == {
            "governance",
            "operational",
            "security",
        }
        assert all(item["event_count"] == 1 for item in archives)

        async with session_factory() as session:
            archive = (
                await session.execute(
                    select(AuditEventArchive).where(
                        AuditEventArchive.category == "security"
                    )
                )
            ).scalar_one()
            archive.payload = b"corrupt"
            await session.commit()
        with pytest.raises(ValueError, match="corrupt"):
            await service.restore_audit_archive(archive.id)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_subject_export_and_delete_use_structured_subject_matches():
    engine, session_factory = await build_session_factory()
    memory_service = FakeMemoryService()

    try:
        async with session_factory() as session:
            session.add(agent("agent-1"))
            session.add_all(
                [
                    MemoryEntry(
                        id="memory-subject",
                        agent_id="agent-1",
                        memory_type="episodic",
                        namespace="entity:customer-1",
                        content="subject memory",
                        metadata_={},
                        importance=0.5,
                        created_at=datetime(2026, 1, 1),
                        expires_at=None,
                    ),
                    CommunicationLog(
                        id="comm-subject",
                        agent_id="agent-1",
                        channel="email",
                        direction="outbound",
                        recipient="customer-1",
                        content="subject email",
                        metadata_={},
                        status="sent",
                        created_at=datetime(2026, 1, 1),
                    ),
                    ApprovalRequest(
                        id="approval-subject",
                        agent_id="agent-1",
                        action_type="send_email",
                        action_description="subject approval",
                        action_payload={},
                        requester="system",
                        requester_type="system",
                        risk_level="medium",
                        target_type="customer",
                        target_id="customer-1",
                        status="approved",
                        created_at=datetime(2026, 1, 1),
                    ),
                    AuditEvent(
                        id="audit-subject",
                        event_type="subject",
                        actor="system",
                        actor_type="system",
                        resource_type="customer",
                        resource_id="customer-1",
                        outcome="success",
                        metadata_={},
                        created_at=datetime(2026, 1, 1),
                    ),
                ]
            )
            await session.commit()

        service = RetentionService(session_factory=session_factory, memory_service=memory_service)
        exported = await service.export_subject_data("customer-1")

        assert len(exported["memory_entries"]) == 1
        assert len(exported["communication_logs"]) == 1
        assert len(exported["approval_requests"]) == 1
        assert len(exported["audit_events"]) == 1

        preview = await service.delete_subject_data("customer-1", dry_run=True)
        assert preview["counts"] == {
            "memory_entries": 1,
            "communication_logs": 1,
            "approval_requests": 1,
        }

        result = await service.delete_subject_data("customer-1", dry_run=False)

        assert result["audit_events_retained"] is True
        assert memory_service.deleted_points == [["memory-subject"]]
        assert await count_rows(session_factory, MemoryEntry) == 0
        assert await count_rows(session_factory, CommunicationLog) == 0
        assert await count_rows(session_factory, ApprovalRequest) == 0
        assert await count_rows(session_factory, AuditEvent) == 2
    finally:
        await engine.dispose()
