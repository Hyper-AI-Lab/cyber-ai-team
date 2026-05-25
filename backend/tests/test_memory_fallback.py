from types import SimpleNamespace

import pytest

from cyber_team.memory import service as memory_module
from cyber_team.memory.service import MemoryService


class FakeScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class FakeResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return FakeScalarResult(self._values)

    def scalar_one_or_none(self):
        return self._values[0] if self._values else None


class FakeSession:
    def __init__(self, values=None):
        self.values = values or []
        self.statement = None
        self.deleted = []
        self.commits = 0

    async def execute(self, statement):
        self.statement = statement
        return FakeResult(self.values)

    async def delete(self, entry):
        self.deleted.append(entry)

    async def commit(self):
        self.commits += 1


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def patch_session(monkeypatch, session):
    monkeypatch.setattr(
        memory_module,
        "async_session",
        lambda: FakeSessionContext(session),
    )


@pytest.mark.asyncio
async def test_postgres_fallback_recall_filters_by_namespace(monkeypatch):
    session = FakeSession(
        [
            SimpleNamespace(
                id="memory-1",
                content="launch checklist",
                memory_type="semantic",
                namespace="tenant-a",
                agent_id="agent-1",
                importance=0.9,
            )
        ]
    )
    patch_session(monkeypatch, session)

    service = MemoryService()
    result = await service.recall(
        SimpleNamespace(
            query="launch",
            agent_id="agent-1",
            memory_type="semantic",
            namespace="tenant-a",
            limit=5,
        )
    )

    compiled = str(session.statement.compile(compile_kwargs={"literal_binds": True}))
    assert "memory_entries.namespace = 'tenant-a'" in compiled
    assert result == [
        {
            "id": "memory-1",
            "content": "launch checklist",
            "score": 1.0,
            "memory_type": "semantic",
            "namespace": "tenant-a",
            "agent_id": "agent-1",
            "importance": 0.9,
        }
    ]


@pytest.mark.asyncio
async def test_delete_memory_removes_postgres_entry_and_qdrant_point(monkeypatch):
    entry = SimpleNamespace(id="memory-1")
    session = FakeSession([entry])
    patch_session(monkeypatch, session)

    class FakeQdrant:
        def __init__(self):
            self.deleted = []

        def delete(self, **kwargs):
            self.deleted.append(kwargs)

    qdrant = FakeQdrant()
    service = MemoryService()
    service._qdrant = qdrant

    await service.delete_memory("memory-1")

    assert session.deleted == [entry]
    assert session.commits == 1
    assert qdrant.deleted == [
        {
            "collection_name": "cyberteam_memory",
            "points_selector": ["memory-1"],
        }
    ]
