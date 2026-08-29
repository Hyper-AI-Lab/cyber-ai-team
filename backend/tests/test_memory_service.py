import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.config import settings
from cyber_team.db import Base
from cyber_team.db.models import MemoryEntry
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
async def test_memory_recall_uses_current_qdrant_query_points_api(monkeypatch):
    engine, session_factory = await build_session_factory()
    monkeypatch.setattr(memory_module, "async_session", session_factory)

    class FakeQdrant:
        def __init__(self):
            self.calls = []

        def query_points(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                points=[
                    SimpleNamespace(
                        id="memory-1",
                        score=0.91,
                        payload={
                            "content": "Evidence-linked operating context",
                            "memory_type": "semantic",
                            "namespace": "company:test",
                            "agent_id": "agent-1",
                            "importance": 0.8,
                        },
                    )
                ]
            )

    service = MemoryService()
    qdrant = FakeQdrant()
    service._qdrant = qdrant

    async def fake_embed(_text):
        return [0.1] * memory_module.VECTOR_SIZE

    monkeypatch.setattr(service, "_embed", fake_embed)
    try:
        async with session_factory() as session:
            session.add(
                MemoryEntry(
                    id="memory-1",
                    agent_id="agent-1",
                    memory_type="semantic",
                    namespace="company:test",
                    content="Evidence-linked operating context",
                    metadata_={},
                    importance=0.8,
                )
            )
            await session.commit()
        result = await service.recall(
            SimpleNamespace(
                query="operating context",
                agent_id="agent-1",
                memory_type="semantic",
                namespace="company:test",
                limit=5,
            )
        )

        assert result[0]["id"] == "memory-1"
        assert result[0]["score"] == 0.91
        assert qdrant.calls[0]["query"][0] == 0.1
        assert qdrant.calls[0]["limit"] == 5
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_qdrant_recall_hydrates_canonical_agent_outcome(monkeypatch):
    engine, session_factory = await build_session_factory()
    monkeypatch.setattr(memory_module, "async_session", session_factory)

    class FakeQdrant:
        def query_points(self, **_kwargs):
            return SimpleNamespace(
                points=[
                    SimpleNamespace(
                        id="memory-agent-result",
                        score=0.93,
                        payload={
                            "content": "Task: investigate the unresolved role gap",
                            "memory_type": "episodic",
                            "namespace": "company:test:security",
                            "agent_id": "security-agent",
                            "importance": 0.8,
                        },
                    ),
                    SimpleNamespace(
                        id="orphaned-vector",
                        score=0.91,
                        payload={
                            "content": "Stale vector-only content",
                            "memory_type": "episodic",
                            "namespace": "company:test:security",
                            "agent_id": "security-agent",
                            "importance": 0.7,
                        },
                    ),
                ]
            )

    service = MemoryService()
    service._qdrant = FakeQdrant()

    async def fake_embed(_text):
        return [0.1] * memory_module.VECTOR_SIZE

    monkeypatch.setattr(service, "_embed", fake_embed)
    try:
        async with session_factory() as session:
            session.add(
                MemoryEntry(
                    id="memory-agent-result",
                    agent_id="security-agent",
                    memory_type="episodic",
                    namespace="company:test:security",
                    content=(
                        "Task: investigate the unresolved role gap | "
                        "Result: Security has three active agents."
                    ),
                    metadata_={
                        "type": "agent_invocation_summary",
                        "result_excerpt": "Security has three active agents.",
                    },
                    importance=0.8,
                )
            )
            await session.commit()

        result = await service.recall_with_policy(
            SimpleNamespace(
                query="current Security role state",
                agent_id="security-agent",
                memory_namespace="company:test:security",
                role_family="security",
                role_name="Security Agent",
                limit=2,
            )
        )

        assert [item["id"] for item in result["items"]] == ["memory-agent-result"]
        assert result["items"][0]["content"] == (
            "Agent outcome: Security has three active agents."
        )
        assert "Task: investigate" not in result["items"][0]["content"]
        assert result["policy"]["excluded_conflicted_memory_ids"] == [
            "orphaned-vector"
        ]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_embedding_failure_falls_back_to_postgres(monkeypatch):
    engine, session_factory = await build_session_factory()
    monkeypatch.setattr(memory_module, "async_session", session_factory)

    class FakeQdrant:
        def query_points(self, **_kwargs):
            raise AssertionError("query should not run without an embedding")

    service = MemoryService()
    service._qdrant = FakeQdrant()

    async def failed_embed(_text):
        raise RuntimeError("embedding provider unavailable")

    monkeypatch.setattr(service, "_embed", failed_embed)
    try:
        async with session_factory() as session:
            session.add(
                MemoryEntry(
                    id="memory-postgres-fallback",
                    agent_id="security-agent",
                    memory_type="semantic",
                    namespace="company:test:security",
                    content="current Security role state",
                    metadata_={},
                    importance=0.9,
                )
            )
            await session.commit()

        result = await service.recall(
            SimpleNamespace(
                query="current Security role state",
                agent_id="security-agent",
                memory_type="semantic",
                namespace="company:test:security",
                limit=5,
            )
        )

        assert [item["id"] for item in result] == ["memory-postgres-fallback"]
        assert result[0]["score"] == 1.0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_embedding_uses_shared_hosted_credential_rotation(monkeypatch):
    keys = [f"embedding-key-{index}" for index in range(1, 6)]
    monkeypatch.setattr(settings, "mistral_api_key", "")
    for index, key in enumerate(keys, start=1):
        monkeypatch.setattr(settings, f"mistral_api_key_{index}", key)
    embedding = AsyncMock(
        return_value=SimpleNamespace(data=[{"embedding": [0.1] * memory_module.VECTOR_SIZE}])
    )
    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(aembedding=embedding),
    )
    rotator = SimpleNamespace(
        select=AsyncMock(
            return_value={
                "credential_index": 3,
                "credential_slot": 4,
                "credential_count": 5,
            }
        ),
        close=AsyncMock(),
    )
    service = MemoryService(credential_rotator=rotator)

    result = await service._embed("Company operating evidence")

    assert result == [0.1] * memory_module.VECTOR_SIZE
    rotator.select.assert_awaited_once_with(provider="mistral", credential_count=5)
    assert embedding.await_args.kwargs["api_key"] == keys[3]


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
                metadata={"role_name": "Operations Manager", "coverage": "read_write"},
            )
        )
        tool_trace = await service.record_trace(
            SimpleNamespace(
                invocation_id="tool-1",
                agent_id="agent-1",
                conversation_id="conversation-1",
                source_type="tool_execution",
                task_excerpt="Execute memory recall.",
                memory_namespace="company:acme:ops",
                read_policy={"tool_name": "memory_recall"},
                write_policy={"tool_name": "memory_recall"},
                recalled_memory_ids=["memory-1"],
                written_memory_ids=[],
                errors=[],
                metadata={
                    "tool_name": "memory_recall",
                    "workflow_run_id": "workflow-1",
                    "coverage": "read",
                },
            )
        )

        traces = await service.list_memory_traces(agent_id="agent-1", limit=10)
        unrelated = await service.list_memory_traces(agent_id="agent-2")
        tool_traces = await service.list_memory_traces(
            source_type="tool_execution",
            conversation_id="conversation-1",
            workflow_run_id="workflow-1",
            tool_name="memory_recall",
            memory_namespace="company:acme:ops",
            coverage="read",
        )

        assert trace["invocation_id"] == "invoke-1"
        assert trace["recall_count"] == 2
        assert trace["write_count"] == 1
        assert {item["id"] for item in traces} == {trace["id"], tool_trace["id"]}
        assert unrelated == []
        assert tool_traces == [tool_trace]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_policy_recall_queries_agent_and_company_scopes(monkeypatch):
    engine, session_factory = await build_session_factory()
    monkeypatch.setattr(memory_module, "async_session", session_factory)
    service = MemoryService()
    calls = []

    async def fake_recall(data):
        calls.append(data)
        if data.namespace == "company:acme:ops":
            return [
                {
                    "id": "private-1",
                    "content": "Private operations note.",
                    "memory_type": "episodic",
                    "namespace": data.namespace,
                    "agent_id": data.agent_id,
                    "importance": 0.8,
                    "score": 1.0,
                }
            ]
        if data.namespace == "company:acme":
            return [
                {
                    "id": "company-1",
                    "content": "Company constitution.",
                    "memory_type": "semantic",
                    "namespace": data.namespace,
                    "agent_id": None,
                    "importance": 0.95,
                    "score": 1.0,
                }
            ]
        if data.namespace == "company:acme:roles":
            return [
                {
                    "id": "company-1",
                    "content": "Duplicate company constitution.",
                    "memory_type": "semantic",
                    "namespace": data.namespace,
                    "agent_id": None,
                    "importance": 0.9,
                    "score": 0.8,
                }
            ]
        return []

    try:
        service.recall = fake_recall

        result = await service.recall_with_policy(
            SimpleNamespace(
                query="launch operations",
                agent_id="ops_agent",
                memory_namespace="company:acme:ops",
                role_family="operations",
                role_name="Operations Manager",
                limit=8,
            )
        )

        assert [call.namespace for call in calls][:3] == [
            "company:acme:ops",
            "company:acme",
            "company:acme:roles",
        ]
        assert result["policy"]["company_namespace"] == "company:acme"
        assert result["policy"]["strategy"] == "agent-private-plus-company-shared"
        assert result["policy"]["scope_results"][0]["name"] == "agent_private"
        assert [memory["id"] for memory in result["items"]] == ["private-1", "company-1"]
        assert result["items"][0]["scope"] == "agent_private"
        assert result["items"][1]["scope"] == "company_constitution"
    finally:
        await engine.dispose()
