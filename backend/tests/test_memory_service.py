import pytest

from cyber_team.memory import service as memory_module
from cyber_team.memory.service import MemoryService


@pytest.mark.asyncio
async def test_memory_startup_degrades_when_qdrant_is_unavailable(monkeypatch):
    class BrokenQdrantClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("qdrant unavailable")

    monkeypatch.setattr(memory_module, "QdrantClient", BrokenQdrantClient)

    service = MemoryService()
    await service.startup()

    assert service._qdrant is None
