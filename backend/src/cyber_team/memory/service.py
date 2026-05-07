"""4-Layer Memory Service — pinned, workflow, retrieval, canonical records."""

import uuid
import logging
import asyncio
from datetime import datetime
from typing import Optional

from sqlalchemy import select, and_, desc
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue

from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import MemoryEntry

logger = logging.getLogger(__name__)

COLLECTION_NAME = "cyberteam_memory"
VECTOR_SIZE = 1024  # mistral-embed dimension


class MemoryService:
    def __init__(self):
        self._qdrant: Optional[QdrantClient] = None

    async def startup(self):
        self._qdrant = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
        )
        # Ensure collection exists
        try:
            self._qdrant.get_collection(COLLECTION_NAME)
        except Exception:
            self._qdrant.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
            logger.info(f"Created Qdrant collection: {COLLECTION_NAME}")
        logger.info("Memory service started")

    async def shutdown(self):
        if self._qdrant:
            self._qdrant.close()

    # ─── Core Memory Operations ──────────────────────────────────────

    async def remember(self, data) -> dict:
        entry_id = str(uuid.uuid4())
        now = datetime.utcnow()

        # Store in PostgreSQL (canonical)
        async with async_session() as session:
            entry = MemoryEntry(
                id=entry_id,
                agent_id=data.agent_id,
                memory_type=data.memory_type,
                namespace=data.namespace,
                content=data.content,
                metadata_=data.metadata,
                importance=data.importance,
                created_at=now,
                expires_at=data.metadata.get("expires_at") if data.metadata else None,
            )
            session.add(entry)
            await session.commit()

        # Store in Qdrant (semantic retrieval)
        try:
            embedding = await self._embed(data.content)
            await asyncio.to_thread(
                self._qdrant.upsert,
                collection_name=COLLECTION_NAME,
                points=[
                    PointStruct(
                        id=entry_id,
                        vector=embedding,
                        payload={
                            "agent_id": data.agent_id,
                            "memory_type": data.memory_type,
                            "namespace": data.namespace,
                            "content": data.content[:500],
                            "importance": data.importance,
                            "created_at": now.isoformat(),
                        },
                    )
                ],
            )
        except Exception as e:
            logger.warning(f"Failed to store in Qdrant: {e}")

        return {
            "id": entry_id,
            "agent_id": data.agent_id,
            "memory_type": data.memory_type,
            "namespace": data.namespace,
            "content": data.content,
            "metadata": data.metadata,
            "importance": data.importance,
        }

    async def recall(self, data) -> list[dict]:
        results = []

        # Semantic search via Qdrant
        try:
            query_vector = await self._embed(data.query)
            qdrant_filter = None
            conditions = []
            if data.agent_id:
                conditions.append(FieldCondition(key="agent_id", match=MatchValue(value=data.agent_id)))
            if data.memory_type:
                conditions.append(FieldCondition(key="memory_type", match=MatchValue(value=data.memory_type)))
            if data.namespace:
                conditions.append(FieldCondition(key="namespace", match=MatchValue(value=data.namespace)))
            if conditions:
                qdrant_filter = Filter(must=conditions)

            hits = await asyncio.to_thread(
                self._qdrant.search,
                collection_name=COLLECTION_NAME,
                query_vector=query_vector,
                query_filter=qdrant_filter,
                limit=data.limit,
            )
            for hit in hits:
                results.append({
                    "id": str(hit.id),
                    "content": hit.payload.get("content", ""),
                    "score": hit.score,
                    "memory_type": hit.payload.get("memory_type"),
                    "namespace": hit.payload.get("namespace"),
                    "agent_id": hit.payload.get("agent_id"),
                    "importance": hit.payload.get("importance", 0.5),
                })
        except Exception as e:
            logger.warning(f"Qdrant search failed, falling back to DB: {e}")
            # Fallback to PostgreSQL text search
            async with async_session() as session:
                query = select(MemoryEntry).where(
                    MemoryEntry.content.ilike(f"%{data.query}%")
                )
                if data.agent_id:
                    query = query.where(MemoryEntry.agent_id == data.agent_id)
                if data.memory_type:
                    query = query.where(MemoryEntry.memory_type == data.memory_type)
                query = query.order_by(desc(MemoryEntry.importance)).limit(data.limit)
                db_results = (await session.execute(query)).scalars().all()
                for r in db_results:
                    results.append({
                        "id": r.id,
                        "content": r.content,
                        "score": 1.0,
                        "memory_type": r.memory_type,
                        "namespace": r.namespace,
                        "agent_id": r.agent_id,
                        "importance": r.importance,
                    })

        return results

    async def get_entity_profile(self, entity_id: str) -> dict:
        async with async_session() as session:
            result = await session.execute(
                select(MemoryEntry).where(
                    and_(
                        MemoryEntry.namespace == f"entity:{entity_id}",
                        MemoryEntry.memory_type == "entity",
                    )
                )
            )
            entries = result.scalars().all()
            profile = {"entity_id": entity_id, "facts": []}
            for e in entries:
                profile["facts"].append({
                    "content": e.content,
                    "metadata": e.metadata_,
                    "importance": e.importance,
                    "created_at": e.created_at.isoformat(),
                })
            return profile

    async def get_agent_memory(self, agent_id: str) -> list[dict]:
        async with async_session() as session:
            result = await session.execute(
                select(MemoryEntry)
                .where(MemoryEntry.agent_id == agent_id)
                .order_by(desc(MemoryEntry.importance))
                .limit(100)
            )
            entries = result.scalars().all()
            return [
                {
                    "id": e.id,
                    "memory_type": e.memory_type,
                    "namespace": e.namespace,
                    "content": e.content,
                    "importance": e.importance,
                    "created_at": e.created_at.isoformat(),
                }
                for e in entries
            ]

    async def delete_memory(self, memory_id: str):
        async with async_session() as session:
            result = await session.execute(
                select(MemoryEntry).where(MemoryEntry.id == memory_id)
            )
            entry = result.scalar_one_or_none()
            if entry:
                await session.delete(entry)
                await session.commit()

        try:
            await asyncio.to_thread(
                self._qdrant.delete,
                collection_name=COLLECTION_NAME,
                points_selector=[memory_id],
            )
        except Exception as e:
            logger.warning(f"Failed to delete from Qdrant: {e}")

    # ─── Memory Steward Operations ────────────────────────────────────

    async def consolidate_memories(self, agent_id: str, namespace: str) -> dict:
        """Summarize and consolidate older memories for an agent/namespace."""
        async with async_session() as session:
            result = await session.execute(
                select(MemoryEntry).where(
                    and_(
                        MemoryEntry.agent_id == agent_id,
                        MemoryEntry.namespace == namespace,
                        MemoryEntry.memory_type == "episodic",
                    )
                ).order_by(MemoryEntry.created_at).limit(50)
            )
            entries = result.scalars().all()

            if not entries:
                return {"consolidated": 0}

            # Combine content for summarization
            combined = "\n".join([f"- {e.content}" for e in entries])

            # Create a summary memory entry
            summary_data = type("MemoryWrite", (), {
                "agent_id": agent_id,
                "memory_type": "semantic",
                "namespace": namespace,
                "content": f"Consolidated summary: {combined[:2000]}",
                "metadata": {"consolidated_from": len(entries)},
                "importance": 0.7,
            })()
            await self.remember(summary_data)

            # Mark old entries as consolidated (reduce importance)
            for e in entries:
                e.importance = max(0.1, e.importance - 0.3)
            await session.commit()

            return {"consolidated": len(entries)}

    # ─── Embedding ────────────────────────────────────────────────────

    async def _embed(self, text: str) -> list[float]:
        """Generate embedding using Mistral embed model."""
        try:
            import litellm
            litellm.api_key = settings.mistral_api_key
            response = await litellm.aembedding(
                model="mistral/mistral-embed",
                input=[text],
            )
            return response.data[0]["embedding"]
        except Exception as e:
            logger.warning(f"Embedding failed, using random vector: {e}")
            import random
            return [random.gauss(0, 0.1) for _ in range(VECTOR_SIZE)]
