from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.db import Base
from cyber_team.memory import service as memory_module
from cyber_team.memory.service import MemoryService


async def build_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_memory_startup_degrades_when_qdrant_is_unavailable(monkeypatch):
    class BrokenQdrantClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("qdrant unavailable")

    monkeypatch.setattr(memory_module, "QdrantClient", BrokenQdrantClient)

    service = MemoryService()
    await service.startup()

    assert service._qdrant is None


@pytest.mark.asyncio
async def test_memory_trace_record_and_list_filters_by_agent(monkeypatch):
    engine, session_factory = await build_session_factory()
    monkeypatch.setattr(memory_module, "async_session", session_factory)
    service = MemoryService()

    try:
        trace = await service.record_trace(
            SimpleNamespace(
                invocation_id="invoke-1",
                agent_id="agent-1",
                conversation_id=None,
                source_type="agent_invocation",
                task_excerpt="Prepare launch brief.",
                memory_namespace="company:acme:ops",
                read_policy={"limit": 5, "scope": "agent_namespace"},
                write_policy={"memory_type": "episodic"},
                recalled_memory_ids=["memory-1", "memory-2"],
                written_memory_ids=["memory-3"],
                errors=[],
                metadata={"role_name": "Operations Manager"},
            )
        )

        traces = await service.list_memory_traces(agent_id="agent-1")
        unrelated = await service.list_memory_traces(agent_id="agent-2")

        assert trace["invocation_id"] == "invoke-1"
        assert trace["recall_count"] == 2
        assert trace["write_count"] == 1
        assert traces == [trace]
        assert unrelated == []
    finally:
        await engine.dispose()
