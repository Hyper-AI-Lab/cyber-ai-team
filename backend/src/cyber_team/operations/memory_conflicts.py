"""Detect conflicts between recalled memory and canonical company records."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import desc, or_, select

from cyber_team.clock import utc_now
from cyber_team.db import async_session
from cyber_team.db.models import (
    CompanyContextSnapshot,
    MemoryCanonicalConflict,
    MemoryEntry,
)


class MemoryCanonicalConflictService:
    """Keeps memory honest against ERPNext-derived canonical company context."""

    ACTIVE_STATUSES = {"open", "acknowledged"}
    CLAIM_KEYS = ("canonical_claims", "canonical_facts")

    def __init__(self, *, audit_service=None, session_factory=async_session):
        self._audit = audit_service
        self._session_factory = session_factory

    async def scan(
        self,
        *,
        actor: str = "memory_canonical_conflict_detector",
        dry_run: bool = False,
        limit: int = 500,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(limit, 2000))
        snapshot = await self._latest_snapshot_model()
        if not snapshot:
            result = {
                "status": "missing_canonical_context",
                "dry_run": dry_run,
                "scanned_memory_count": 0,
                "conflicts_found": 0,
                "created": 0,
                "updated": 0,
                "unchanged": 0,
                "cleared": 0,
                "conflicts": [],
                "detail": "No active ERPNext company-context snapshot is available.",
            }
            await self._record_scan(actor=actor, result=result, outcome="failure")
            return result

        entries = await self._load_company_memories(snapshot.company_namespace, safe_limit)
        proposals = self._build_conflict_proposals(snapshot, entries)
        if dry_run:
            result = {
                "status": "dry_run",
                "dry_run": True,
                "snapshot_id": snapshot.id,
                "source_hash": snapshot.source_hash,
                "company_namespace": snapshot.company_namespace,
                "scanned_memory_count": len(entries),
                "conflicts_found": len(proposals),
                "created": 0,
                "updated": 0,
                "unchanged": 0,
                "cleared": 0,
                "conflicts": proposals,
            }
            await self._record_scan(actor=actor, result=result)
            return result

        upserted = await self._upsert_conflicts(snapshot, proposals)
        result = {
            "status": "completed",
            "dry_run": False,
            "snapshot_id": snapshot.id,
            "source_hash": snapshot.source_hash,
            "company_namespace": snapshot.company_namespace,
            "scanned_memory_count": len(entries),
            "conflicts_found": len(proposals),
            **upserted,
        }
        await self._record_scan(actor=actor, result=result)
        return result

    async def list_conflicts(
        self,
        *,
        status: str | None = "open",
        severity: str | None = None,
        company_namespace: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 500))
        async with self._session_factory() as session:
            query = select(MemoryCanonicalConflict)
            if status:
                statuses = [part.strip() for part in status.split(",") if part.strip()]
                if statuses:
                    query = query.where(MemoryCanonicalConflict.status.in_(statuses))
            if severity:
                query = query.where(MemoryCanonicalConflict.severity == severity)
            if company_namespace:
                query = query.where(
                    MemoryCanonicalConflict.company_namespace == company_namespace
                )
            query = query.order_by(desc(MemoryCanonicalConflict.updated_at)).limit(safe_limit)
            conflicts = (await session.execute(query)).scalars().all()
            return [self._conflict_to_dict(conflict) for conflict in conflicts]

    async def resolve_conflict(
        self,
        conflict_id: str,
        *,
        status: str = "resolved",
        resolution_strategy: str = "prefer_canonical",
        note: str = "",
        actor: str = "owner",
    ) -> dict[str, Any] | None:
        now = utc_now()
        async with self._session_factory() as session:
            conflict = await session.get(MemoryCanonicalConflict, conflict_id)
            if not conflict:
                return None
            if status not in {"open", "acknowledged", "resolved", "dismissed"}:
                raise ValueError(f"Unsupported conflict status: {status}")
            conflict.status = status
            conflict.updated_at = now
            conflict.resolution = {
                **(conflict.resolution or {}),
                "status": status,
                "resolution_strategy": resolution_strategy,
                "note": note,
                "actor": actor,
                "resolved_at": now.isoformat() if status in {"resolved", "dismissed"} else None,
            }
            conflict.resolved_at = now if status in {"resolved", "dismissed"} else None
            memory = await session.get(MemoryEntry, conflict.memory_id)
            if memory:
                metadata = dict(memory.metadata_ or {})
                metadata = self._apply_memory_resolution_metadata(
                    metadata,
                    conflict,
                    status=status,
                    resolution_strategy=resolution_strategy,
                    note=note,
                    actor=actor,
                    now=now,
                )
                memory.metadata_ = metadata
            await session.commit()
            response = self._conflict_to_dict(conflict)

        if self._audit:
            await self._audit.record(
                event_type="memory_canonical_conflict.resolved",
                actor=actor,
                actor_type="user",
                resource_type="memory_canonical_conflict",
                resource_id=conflict_id,
                action=status,
                metadata={
                    "resolution_strategy": resolution_strategy,
                    "note": note,
                    "memory_id": response.get("memory_id"),
                    "canonical_source_id": response.get("canonical_source_id"),
                },
            )
        return response

    async def readiness(self) -> dict[str, Any]:
        conflicts = await self.list_conflicts(status="open,acknowledged", limit=500)
        by_severity: dict[str, int] = {}
        by_type: dict[str, int] = {}
        for conflict in conflicts:
            by_severity[conflict["severity"]] = by_severity.get(conflict["severity"], 0) + 1
            by_type[conflict["conflict_type"]] = (
                by_type.get(conflict["conflict_type"], 0) + 1
            )
        blocking_count = sum(
            count for severity, count in by_severity.items()
            if severity in {"high", "critical"}
        )
        return {
            "status": "blocked" if blocking_count else "ready",
            "blocking": bool(blocking_count),
            "open_count": len(conflicts),
            "blocking_count": blocking_count,
            "by_severity": by_severity,
            "by_type": by_type,
            "detail": (
                "High-severity memory/canonical conflicts require owner review."
                if blocking_count
                else "No high-severity memory/canonical conflicts are open."
            ),
        }

    async def _latest_snapshot_model(self) -> CompanyContextSnapshot | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(CompanyContextSnapshot)
                .where(CompanyContextSnapshot.status == "active")
                .order_by(desc(CompanyContextSnapshot.created_at))
                .limit(1)
            )
            return result.scalar_one_or_none()

    async def _load_company_memories(
        self,
        company_namespace: str,
        limit: int,
    ) -> list[MemoryEntry]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(MemoryEntry)
                .where(
                    or_(
                        MemoryEntry.namespace == company_namespace,
                        MemoryEntry.namespace.like(f"{company_namespace}:%"),
                    )
                )
                .order_by(desc(MemoryEntry.created_at))
                .limit(limit)
            )
            return list(result.scalars().all())

    def _build_conflict_proposals(
        self,
        snapshot: CompanyContextSnapshot,
        entries: list[MemoryEntry],
    ) -> list[dict[str, Any]]:
        proposals: list[dict[str, Any]] = []
        canonical = self._canonical_payload(snapshot)
        for entry in entries:
            metadata = dict(entry.metadata_ or {})
            memory_source_hash = self._memory_source_hash(metadata)
            if (
                memory_source_hash
                and memory_source_hash != snapshot.source_hash
                and self._is_erpnext_memory(metadata)
            ):
                proposals.append(
                    self._stale_source_hash_proposal(
                        snapshot,
                        entry,
                        metadata,
                        memory_source_hash,
                    )
                )
            for claim_path, memory_value in self._canonical_claims(metadata).items():
                canonical_value = self._lookup_canonical_value(canonical, claim_path)
                if canonical_value is None:
                    continue
                if self._normalized(memory_value) == self._normalized(canonical_value):
                    continue
                proposals.append(
                    self._claim_mismatch_proposal(
                        snapshot,
                        entry,
                        metadata,
                        claim_path,
                        memory_value,
                        canonical_value,
                    )
                )
        return proposals

    async def _upsert_conflicts(
        self,
        snapshot: CompanyContextSnapshot,
        proposals: list[dict[str, Any]],
    ) -> dict[str, Any]:
        now = utc_now()
        keys = [proposal["dedupe_key"] for proposal in proposals]
        created = 0
        updated = 0
        unchanged = 0
        conflicts: list[MemoryCanonicalConflict] = []
        async with self._session_factory() as session:
            existing_by_key: dict[str, MemoryCanonicalConflict] = {}
            if keys:
                existing = await session.execute(
                    select(MemoryCanonicalConflict).where(
                        MemoryCanonicalConflict.dedupe_key.in_(keys)
                    )
                )
                existing_by_key = {
                    conflict.dedupe_key: conflict for conflict in existing.scalars().all()
                }

            for proposal in proposals:
                conflict = existing_by_key.get(proposal["dedupe_key"])
                if conflict:
                    if conflict.status not in self.ACTIVE_STATUSES:
                        unchanged += 1
                        conflicts.append(conflict)
                        continue
                    changed = self._refresh_conflict(conflict, proposal, now)
                    updated += 1 if changed else 0
                    unchanged += 0 if changed else 1
                else:
                    conflict = MemoryCanonicalConflict(
                        id=f"memconf_{uuid.uuid4().hex[:16]}",
                        created_at=now,
                        updated_at=now,
                        **proposal,
                    )
                    session.add(conflict)
                    created += 1
                conflicts.append(conflict)
                memory = await session.get(MemoryEntry, proposal["memory_id"])
                if memory and conflict.status in self.ACTIVE_STATUSES:
                    memory.metadata_ = self._mark_memory_conflicted(
                        dict(memory.metadata_ or {}),
                        conflict,
                        now,
                    )

            cleared = await self._clear_absent_conflicts(
                session,
                snapshot=snapshot,
                active_keys=set(keys),
                now=now,
            )
            await session.commit()
            return {
                "created": created,
                "updated": updated,
                "unchanged": unchanged,
                "cleared": cleared,
                "conflicts": [self._conflict_to_dict(conflict) for conflict in conflicts],
            }

    def _refresh_conflict(
        self,
        conflict: MemoryCanonicalConflict,
        proposal: dict[str, Any],
        now: datetime,
    ) -> bool:
        changed = False
        for key in (
            "severity",
            "title",
            "description",
            "recommendation",
            "memory_excerpt",
            "canonical_excerpt",
            "evidence",
        ):
            if getattr(conflict, key) != proposal[key]:
                setattr(conflict, key, proposal[key])
                changed = True
        if changed:
            conflict.updated_at = now
        return changed

    async def _clear_absent_conflicts(
        self,
        session,
        *,
        snapshot: CompanyContextSnapshot,
        active_keys: set[str],
        now: datetime,
    ) -> int:
        result = await session.execute(
            select(MemoryCanonicalConflict).where(
                MemoryCanonicalConflict.company_namespace == snapshot.company_namespace,
                MemoryCanonicalConflict.canonical_source_type == "erpnext_company_context",
                MemoryCanonicalConflict.status.in_(self.ACTIVE_STATUSES),
            )
        )
        cleared = 0
        for conflict in result.scalars().all():
            if conflict.dedupe_key in active_keys:
                continue
            conflict.status = "resolved"
            conflict.updated_at = now
            conflict.resolved_at = now
            conflict.resolution = {
                **(conflict.resolution or {}),
                "status": "resolved",
                "resolution_strategy": "cleared_by_scan",
                "resolved_at": now.isoformat(),
            }
            memory = await session.get(MemoryEntry, conflict.memory_id)
            if memory:
                memory.metadata_ = self._apply_memory_resolution_metadata(
                    dict(memory.metadata_ or {}),
                    conflict,
                    status="resolved",
                    resolution_strategy="cleared_by_scan",
                    note="Latest scan no longer finds this conflict.",
                    actor="memory_canonical_conflict_detector",
                    now=now,
                )
            cleared += 1
        return cleared

    def _stale_source_hash_proposal(
        self,
        snapshot: CompanyContextSnapshot,
        entry: MemoryEntry,
        metadata: dict[str, Any],
        memory_source_hash: str,
    ) -> dict[str, Any]:
        return {
            "conflict_type": "stale_canonical_memory",
            "severity": "medium",
            "status": "open",
            "memory_id": entry.id,
            "memory_namespace": entry.namespace,
            "company_namespace": snapshot.company_namespace,
            "canonical_source_type": "erpnext_company_context",
            "canonical_source_id": snapshot.id,
            "canonical_source_hash": snapshot.source_hash,
            "memory_source_hash": memory_source_hash,
            "claim_path": None,
            "title": "Memory references an older ERPNext company-context snapshot",
            "description": (
                "This memory was seeded from an ERPNext company-context hash that no "
                "longer matches the active canonical snapshot."
            ),
            "recommendation": (
                "Prefer the latest ERPNext company-context snapshot, refresh or replace "
                "the stale memory, and keep this memory out of agent recall until reviewed."
            ),
            "memory_excerpt": self._excerpt(entry.content, 700),
            "canonical_excerpt": self._excerpt(
                json.dumps(
                    {
                        "snapshot_id": snapshot.id,
                        "source_hash": snapshot.source_hash,
                        "company_name": (
                            snapshot.normalized_profile or {}
                        ).get("company_name"),
                    },
                    sort_keys=True,
                ),
                700,
            ),
            "evidence": {
                "memory_metadata": self._redacted_metadata(metadata),
                "memory_created_at": entry.created_at.isoformat(),
                "memory_source_hash": memory_source_hash,
                "canonical_source_hash": snapshot.source_hash,
            },
            "resolution": {},
            "dedupe_key": (
                "stale_canonical_memory:"
                f"{entry.id}:{memory_source_hash}:{snapshot.source_hash}"
            ),
        }

    def _claim_mismatch_proposal(
        self,
        snapshot: CompanyContextSnapshot,
        entry: MemoryEntry,
        metadata: dict[str, Any],
        claim_path: str,
        memory_value: Any,
        canonical_value: Any,
    ) -> dict[str, Any]:
        return {
            "conflict_type": "canonical_fact_mismatch",
            "severity": "high",
            "status": "open",
            "memory_id": entry.id,
            "memory_namespace": entry.namespace,
            "company_namespace": snapshot.company_namespace,
            "canonical_source_type": "erpnext_company_context",
            "canonical_source_id": snapshot.id,
            "canonical_source_hash": snapshot.source_hash,
            "memory_source_hash": self._memory_source_hash(metadata),
            "claim_path": claim_path,
            "title": f"Memory disagrees with ERPNext canonical fact: {claim_path}",
            "description": (
                "The memory entry carries a structured canonical claim that does not "
                "match the current ERPNext-derived company context."
            ),
            "recommendation": (
                "Block this memory from autonomous use, prefer ERPNext unless the owner "
                "confirms the canonical record is wrong, and then refresh the record or memory."
            ),
            "memory_excerpt": self._excerpt(str(memory_value), 700),
            "canonical_excerpt": self._excerpt(str(canonical_value), 700),
            "evidence": {
                "claim_path": claim_path,
                "memory_value": memory_value,
                "canonical_value": canonical_value,
                "memory_metadata": self._redacted_metadata(metadata),
                "memory_created_at": entry.created_at.isoformat(),
            },
            "resolution": {},
            "dedupe_key": (
                "canonical_fact_mismatch:"
                f"{entry.id}:{claim_path}:{snapshot.source_hash}"
            ),
        }

    def _mark_memory_conflicted(
        self,
        metadata: dict[str, Any],
        conflict: MemoryCanonicalConflict,
        now: datetime,
    ) -> dict[str, Any]:
        active = dict(metadata.get("canonical_conflicts") or {})
        active[conflict.id] = {
            "status": conflict.status,
            "conflict_type": conflict.conflict_type,
            "severity": conflict.severity,
            "canonical_source_id": conflict.canonical_source_id,
            "canonical_source_hash": conflict.canonical_source_hash,
            "detected_at": now.isoformat(),
        }
        metadata["canonical_conflicts"] = active
        metadata["canonical_conflict_status"] = "active"
        metadata["exclude_from_recall_reason"] = (
            "active_memory_canonical_conflict"
        )
        return metadata

    def _apply_memory_resolution_metadata(
        self,
        metadata: dict[str, Any],
        conflict: MemoryCanonicalConflict,
        *,
        status: str,
        resolution_strategy: str,
        note: str,
        actor: str,
        now: datetime,
    ) -> dict[str, Any]:
        active = dict(metadata.get("canonical_conflicts") or {})
        if status in self.ACTIVE_STATUSES:
            active[conflict.id] = {
                "status": status,
                "conflict_type": conflict.conflict_type,
                "severity": conflict.severity,
                "canonical_source_id": conflict.canonical_source_id,
                "canonical_source_hash": conflict.canonical_source_hash,
                "updated_at": now.isoformat(),
            }
        else:
            active.pop(conflict.id, None)
        if active:
            metadata["canonical_conflicts"] = active
            metadata["canonical_conflict_status"] = "active"
            metadata["exclude_from_recall_reason"] = "active_memory_canonical_conflict"
        else:
            metadata.pop("canonical_conflicts", None)
            metadata.pop("canonical_conflict_status", None)
            metadata.pop("exclude_from_recall_reason", None)
        if resolution_strategy == "prefer_canonical" and status == "resolved":
            metadata["canonical_superseded"] = True
            metadata["exclude_from_recall_reason"] = "canonical_record_preferred"
        metadata["canonical_conflict_last_resolution"] = {
            "conflict_id": conflict.id,
            "status": status,
            "resolution_strategy": resolution_strategy,
            "note": note,
            "actor": actor,
            "resolved_at": now.isoformat(),
        }
        return metadata

    async def _record_scan(
        self,
        *,
        actor: str,
        result: dict[str, Any],
        outcome: str = "success",
    ) -> None:
        if not self._audit:
            return
        await self._audit.record(
            event_type="memory_canonical_conflict.scan",
            actor=actor,
            actor_type="agent" if "@" not in actor else "user",
            resource_type="memory_canonical_conflict",
            resource_id=result.get("snapshot_id"),
            action="scan",
            outcome=outcome,
            metadata={
                key: value
                for key, value in result.items()
                if key not in {"conflicts"}
            },
        )
        if hasattr(self._audit, "record_control_evidence"):
            await self._audit.record_control_evidence(
                control_id="memory.canonical_conflict_scan",
                control_area="soc2_change_management",
                actor=actor,
                outcome=outcome,
                evidence={
                    key: value
                    for key, value in result.items()
                    if key not in {"conflicts"}
                },
            )

    def _canonical_payload(self, snapshot: CompanyContextSnapshot) -> dict[str, Any]:
        return {
            "normalized_profile": snapshot.normalized_profile or {},
            "erpnext_summary": snapshot.erpnext_summary or {},
            "operating_model": snapshot.operating_model or {},
        }

    def _canonical_claims(self, metadata: dict[str, Any]) -> dict[str, Any]:
        claims: dict[str, Any] = {}
        for key in self.CLAIM_KEYS:
            value = metadata.get(key)
            if isinstance(value, dict):
                claims.update(value)
        return claims

    def _lookup_canonical_value(
        self,
        canonical: dict[str, Any],
        path: str,
    ) -> Any | None:
        if not path:
            return None
        candidates = [path]
        if not path.startswith(("normalized_profile.", "erpnext_summary.", "operating_model.")):
            candidates.insert(0, f"normalized_profile.{path}")
        for candidate in candidates:
            current: Any = canonical
            found = True
            for part in candidate.split("."):
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    found = False
                    break
            if found:
                return current
        return None

    @staticmethod
    def _is_erpnext_memory(metadata: dict[str, Any]) -> bool:
        source = str(metadata.get("source") or metadata.get("canonical_source") or "")
        return source in {
            "erpnext_company_context_sync",
            "company_context_sync",
            "erpnext",
        }

    @staticmethod
    def _memory_source_hash(metadata: dict[str, Any]) -> str | None:
        value = metadata.get("source_hash") or metadata.get("canonical_source_hash")
        return str(value) if value else None

    @staticmethod
    def _normalized(value: Any) -> str:
        if isinstance(value, str):
            return " ".join(value.lower().split())
        return json.dumps(value, sort_keys=True, default=str)

    @staticmethod
    def _excerpt(value: str, limit: int = 240) -> str:
        normalized = " ".join(str(value or "").split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: max(0, limit - 3)].rstrip() + "..."

    @staticmethod
    def _redacted_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        redacted = {}
        for key, value in metadata.items():
            lowered = key.lower()
            if any(marker in lowered for marker in ("password", "secret", "token", "key")):
                redacted[key] = "[redacted]"
            else:
                redacted[key] = value
        return redacted

    @staticmethod
    def _conflict_to_dict(conflict: MemoryCanonicalConflict) -> dict[str, Any]:
        return {
            "id": conflict.id,
            "conflict_type": conflict.conflict_type,
            "severity": conflict.severity,
            "status": conflict.status,
            "memory_id": conflict.memory_id,
            "memory_namespace": conflict.memory_namespace,
            "company_namespace": conflict.company_namespace,
            "canonical_source_type": conflict.canonical_source_type,
            "canonical_source_id": conflict.canonical_source_id,
            "canonical_source_hash": conflict.canonical_source_hash,
            "memory_source_hash": conflict.memory_source_hash,
            "claim_path": conflict.claim_path,
            "title": conflict.title,
            "description": conflict.description,
            "recommendation": conflict.recommendation,
            "memory_excerpt": conflict.memory_excerpt,
            "canonical_excerpt": conflict.canonical_excerpt,
            "evidence": conflict.evidence,
            "resolution": conflict.resolution,
            "dedupe_key": conflict.dedupe_key,
            "created_at": conflict.created_at.isoformat(),
            "updated_at": conflict.updated_at.isoformat(),
            "resolved_at": conflict.resolved_at.isoformat() if conflict.resolved_at else None,
        }
