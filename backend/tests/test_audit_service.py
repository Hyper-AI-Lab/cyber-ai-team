from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.audit import service as audit_module
from cyber_team.audit.service import AuditService
from cyber_team.db import Base
from cyber_team.db.models import AuditEvent, AuditEventRollup


@pytest.fixture
async def audit_session(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=[AuditEvent.__table__, AuditEventRollup.__table__],
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(audit_module, "async_session", factory)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_low_signal_audit_rollups_are_hourly_and_counted(audit_session):
    service = AuditService()
    first_at = datetime(2026, 9, 22, 10, 5, 0)

    first = await service.record_rollup(
        event_type="authorization.allowed",
        actor="owner@example.com",
        resource_type="dashboard",
        action="read",
        rollup_group="owner:dashboard:read",
        metadata={"sample_resource_id": "dashboard"},
        observed_at=first_at,
    )
    second = await service.record_rollup(
        event_type="authorization.allowed",
        actor="owner@example.com",
        resource_type="dashboard",
        action="read",
        rollup_group="owner:dashboard:read",
        metadata={"sample_resource_id": "dashboard:latest"},
        observed_at=first_at + timedelta(minutes=20),
    )
    third = await service.record_rollup(
        event_type="authorization.allowed",
        actor="owner@example.com",
        resource_type="dashboard",
        action="read",
        rollup_group="owner:dashboard:read",
        metadata={"sample_resource_id": "dashboard:next-hour"},
        observed_at=first_at + timedelta(hours=1),
    )

    assert first["count"] == 1
    assert second["count"] == 2
    assert second["metadata"]["sample_resource_id"] == "dashboard:latest"
    assert third["count"] == 1
    async with audit_session() as session:
        rollups = (await session.execute(select(AuditEventRollup))).scalars().all()
        events = (await session.execute(select(AuditEvent))).scalars().all()
    assert len(rollups) == 2
    assert events == []


@pytest.mark.asyncio
async def test_individual_audit_events_remain_immutable_rows(audit_session):
    service = AuditService()

    await service.record(
        event_type="authorization.denied",
        actor="agent@example.com",
        resource_type="tool",
        resource_id="send_email",
        action="execute",
        outcome="denied",
    )
    await service.record(
        event_type="authorization.denied",
        actor="agent@example.com",
        resource_type="tool",
        resource_id="send_email",
        action="execute",
        outcome="denied",
    )

    async with audit_session() as session:
        events = (await session.execute(select(AuditEvent))).scalars().all()
        rollups = (await session.execute(select(AuditEventRollup))).scalars().all()
    assert len(events) == 2
    assert rollups == []
