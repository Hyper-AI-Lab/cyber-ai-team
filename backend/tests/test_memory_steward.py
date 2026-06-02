from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.config import settings
from cyber_team.db import Base
from cyber_team.db.models import MemoryStewardFinding, MemoryTrace
from cyber_team.operations.memory_steward import MemoryStewardService


async def build_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def memory_trace(
    trace_id: str,
    *,
    agent_id: str = "ops_agent",
    namespace: str = "company:acme:ops",
    recall_count: int = 0,
    errors: list[str] | None = None,
    created_at: datetime | None = None,
    scope_results: list[dict] | None = None,
) -> MemoryTrace:
    now = created_at or datetime(2026, 6, 2, 12, 0, 0)
    return MemoryTrace(
        id=trace_id,
        invocation_id=f"invoke-{trace_id}",
        agent_id=agent_id,
        conversation_id=None,
        source_type="agent_invocation",
        task_excerpt="Prepare launch operations.",
        memory_namespace=namespace,
        read_policy={
            "company_namespace": "company:acme",
            "scope_results": scope_results
            if scope_results is not None
            else [
                {
                    "name": "company_constitution",
                    "namespace": "company:acme",
                    "returned": 0,
                    "added": 0,
                }
            ],
        },
        write_policy={"memory_type": "episodic"},
        recalled_memory_ids=[],
        written_memory_ids=["memory-write-1"],
        recall_count=recall_count,
        write_count=1,
        errors=errors or [],
        metadata_={"memory_coverage": "empty" if recall_count == 0 else "hit"},
        created_at=now,
    )


@pytest.mark.asyncio
async def test_memory_steward_creates_and_dedupes_findings(monkeypatch):
    now = datetime(2026, 6, 2, 12, 0, 0)
    engine, session_factory = await build_session_factory()
    monkeypatch.setattr(settings, "memory_steward_empty_recall_threshold", 2)
    monkeypatch.setattr(settings, "memory_steward_trace_lookback_hours", 24)
    monkeypatch.setattr(settings, "memory_steward_trace_limit", 100)

    try:
        async with session_factory() as session:
            session.add_all([
                memory_trace("trace-1", created_at=now - timedelta(minutes=3)),
                memory_trace("trace-2", created_at=now - timedelta(minutes=2)),
                memory_trace(
                    "trace-3",
                    errors=["write:RuntimeError:database unavailable"],
                    created_at=now - timedelta(minutes=1),
                ),
            ])
            await session.commit()

        steward = MemoryStewardService(session_factory=session_factory)
        first = await steward.run_once(now=now, actor="test")
        second = await steward.run_once(now=now, actor="test")

        assert first["traces_reviewed"] == 3
        assert first["findings_created"] == 3
        assert second["findings_created"] == 0
        assert second["findings_updated"] == 3

        findings = await steward.list_findings(status="open")
        finding_types = {finding["finding_type"] for finding in findings}
        assert finding_types == {
            "repeated_empty_recall",
            "memory_operation_errors",
            "missing_company_shared_memory",
        }

        empty_recall = next(
            finding
            for finding in findings
            if finding["finding_type"] == "repeated_empty_recall"
        )
        assert empty_recall["evidence"]["empty_recall_count"] == 3
        assert empty_recall["evidence"]["threshold"] == 2

        resolved = await steward.resolve_finding(
            empty_recall["id"],
            status="resolved",
            note="Seeded memory.",
            actor="owner@example.com",
        )
        assert resolved is not None
        assert resolved["status"] == "resolved"

        async with session_factory() as session:
            db_finding = (
                await session.execute(
                    select(MemoryStewardFinding).where(
                        MemoryStewardFinding.id == empty_recall["id"]
                    )
                )
            ).scalar_one()
            assert db_finding.metadata_["resolution"]["note"] == "Seeded memory."
    finally:
        await engine.dispose()
