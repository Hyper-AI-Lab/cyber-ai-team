from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.db import Base
from cyber_team.db.models import (
    CompanyContextSnapshot,
    MemoryCanonicalConflict,
    MemoryEntry,
)
from cyber_team.memory.service import MemoryService
from cyber_team.operations.memory_conflicts import MemoryCanonicalConflictService


async def build_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def company_snapshot(
    *,
    snapshot_id: str = "ctx_current",
    source_hash: str = "hash-current",
    company_name: str = "Hyper AI Lab",
) -> CompanyContextSnapshot:
    return CompanyContextSnapshot(
        id=snapshot_id,
        source="erpnext",
        source_hash=source_hash,
        company_namespace="company:hyper_ai_lab",
        status="active",
        normalized_profile={
            "company_name": company_name,
            "default_currency": "USD",
            "source_site": "erpnext.hyperailab.com",
        },
        erpnext_summary={"counts": {"Task": 2}, "statuses": {"Task": {"Open": 1}}},
        operating_model={"summary": "Live company operating model."},
        created_by="test",
        created_at=datetime(2026, 7, 21, 12, 0, 0),
    )


def memory_entry(
    memory_id: str,
    *,
    content: str = "ERPNext company profile snapshot: Hyper AI Lab",
    metadata: dict | None = None,
    namespace: str = "company:hyper_ai_lab",
) -> MemoryEntry:
    return MemoryEntry(
        id=memory_id,
        agent_id=None,
        memory_type="semantic",
        namespace=namespace,
        content=content,
        metadata_=metadata or {},
        importance=0.9,
        created_at=datetime(2026, 7, 21, 12, 1, 0),
    )


@pytest.mark.asyncio
async def test_scan_creates_stale_hash_conflict_and_is_idempotent():
    engine, session_factory = await build_session_factory()
    try:
        async with session_factory() as session:
            session.add(company_snapshot())
            session.add(
                memory_entry(
                    "mem_stale",
                    metadata={
                        "source": "erpnext_company_context_sync",
                        "source_hash": "hash-old",
                        "company_namespace": "company:hyper_ai_lab",
                    },
                )
            )
            await session.commit()

        service = MemoryCanonicalConflictService(session_factory=session_factory)
        first = await service.scan(actor="test")
        second = await service.scan(actor="test")

        assert first["status"] == "completed"
        assert first["created"] == 1
        assert first["conflicts_found"] == 1
        assert first["conflicts"][0]["conflict_type"] == "stale_canonical_memory"
        assert second["created"] == 0
        assert second["conflicts_found"] == 1

        async with session_factory() as session:
            conflicts = (
                await session.execute(select(MemoryCanonicalConflict))
            ).scalars().all()
            memory = await session.get(MemoryEntry, "mem_stale")
            assert len(conflicts) == 1
            assert memory is not None
            assert memory.metadata_["canonical_conflict_status"] == "active"
            assert memory.metadata_["exclude_from_recall_reason"] == (
                "active_memory_canonical_conflict"
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_scan_detects_structured_canonical_claim_mismatch():
    engine, session_factory = await build_session_factory()
    try:
        async with session_factory() as session:
            session.add(company_snapshot(company_name="Hyper AI Lab"))
            session.add(
                memory_entry(
                    "mem_claim",
                    metadata={
                        "source": "erpnext_company_context_sync",
                        "source_hash": "hash-current",
                        "company_namespace": "company:hyper_ai_lab",
                        "canonical_claims": {"company_name": "Old Company"},
                    },
                )
            )
            await session.commit()

        service = MemoryCanonicalConflictService(session_factory=session_factory)
        result = await service.scan(actor="test")

        assert result["created"] == 1
        conflict = result["conflicts"][0]
        assert conflict["conflict_type"] == "canonical_fact_mismatch"
        assert conflict["severity"] == "high"
        assert conflict["claim_path"] == "company_name"
        assert conflict["memory_excerpt"] == "Old Company"
        assert conflict["canonical_excerpt"] == "Hyper AI Lab"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_resolve_prefer_canonical_supersedes_memory_recall():
    engine, session_factory = await build_session_factory()
    try:
        async with session_factory() as session:
            session.add(company_snapshot())
            session.add(
                memory_entry(
                    "mem_claim",
                    content="ERPNext company profile snapshot: Old Company",
                    metadata={
                        "source": "erpnext_company_context_sync",
                        "source_hash": "hash-current",
                        "company_namespace": "company:hyper_ai_lab",
                        "canonical_claims": {"company_name": "Old Company"},
                    },
                )
            )
            await session.commit()

        service = MemoryCanonicalConflictService(session_factory=session_factory)
        scan = await service.scan(actor="test")
        conflict_id = scan["conflicts"][0]["id"]
        resolved = await service.resolve_conflict(
            conflict_id,
            status="resolved",
            resolution_strategy="prefer_canonical",
            actor="owner@example.com",
        )

        assert resolved is not None
        assert resolved["status"] == "resolved"
        async with session_factory() as session:
            memory = await session.get(MemoryEntry, "mem_claim")
            assert memory is not None
            assert memory.metadata_["canonical_superseded"] is True
            assert memory.metadata_["exclude_from_recall_reason"] == (
                "canonical_record_preferred"
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_recall_with_policy_excludes_active_canonical_conflict(monkeypatch):
    engine, session_factory = await build_session_factory()
    monkeypatch.setattr("cyber_team.memory.service.async_session", session_factory)
    try:
        async with session_factory() as session:
            session.add(
                memory_entry(
                    "mem_blocked",
                    content="Hyper AI Lab outdated canonical fact",
                    metadata={
                        "canonical_conflict_status": "active",
                        "exclude_from_recall_reason": "active_memory_canonical_conflict",
                    },
                    namespace="company:hyper_ai_lab:ops",
                )
            )
            session.add(
                memory_entry(
                    "mem_allowed",
                    content="Hyper AI Lab current operating note",
                    metadata={"source": "owner_note"},
                    namespace="company:hyper_ai_lab:ops",
                )
            )
            await session.commit()

        memory = MemoryService()
        result = await memory.recall_with_policy(
            SimpleNamespace(
                query="Hyper AI Lab",
                agent_id=None,
                memory_namespace="company:hyper_ai_lab:ops",
                role_family="operations",
                role_name="Operations Agent",
                limit=5,
            )
        )

        assert [item["id"] for item in result["items"]] == ["mem_allowed"]
        assert result["policy"]["excluded_conflicted_memory_ids"] == ["mem_blocked"]
        assert result["policy"]["excluded_conflicted_count"] == 1
        assert result["errors"] == ["canonical_conflict:excluded:1"]
    finally:
        await engine.dispose()
