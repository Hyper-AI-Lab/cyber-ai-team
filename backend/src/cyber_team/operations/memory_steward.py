"""Memory steward loop for trace-driven memory health review."""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from sqlalchemy import desc, select

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import MemoryStewardFinding, MemoryTrace


class MemoryStewardService:
    """Reviews memory traces and records actionable memory-health findings."""

    OPEN_STATUSES = {"open", "acknowledged"}

    def __init__(
        self,
        audit_service=None,
        session_factory=async_session,
    ):
        self._audit = audit_service
        self._session_factory = session_factory

    async def run_once(
        self,
        *,
        now: datetime | None = None,
        actor: str = "memory_steward_loop",
    ) -> dict:
        now = now or utc_now()
        traces = await self._load_recent_traces(now)
        proposals = self._propose_findings(traces)
        findings = []
        created = 0
        updated = 0

        for proposal in proposals:
            finding, was_created = await self._upsert_finding(proposal, now)
            findings.append(finding)
            if was_created:
                created += 1
            else:
                updated += 1

        summary = {
            "reviewed_at": now.isoformat(),
            "actor": actor,
            "traces_reviewed": len(traces),
            "findings_created": created,
            "findings_updated": updated,
            "findings": findings,
        }
        if self._audit:
            await self._audit.record(
                event_type="memory_steward.review",
                actor=actor,
                actor_type="agent",
                resource_type="memory_steward",
                action="run",
                metadata={
                    "traces_reviewed": len(traces),
                    "findings_created": created,
                    "findings_updated": updated,
                    "finding_types": [finding["finding_type"] for finding in findings],
                },
            )
        return summary

    async def list_findings(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        safe_limit = max(1, min(limit, 200))
        async with self._session_factory() as session:
            query = select(MemoryStewardFinding)
            if status:
                query = query.where(MemoryStewardFinding.status == status)
            query = query.order_by(desc(MemoryStewardFinding.created_at)).limit(safe_limit)
            findings = (await session.execute(query)).scalars().all()
            return [self._finding_to_dict(finding) for finding in findings]

    async def resolve_finding(
        self,
        finding_id: str,
        *,
        status: str = "resolved",
        note: str = "",
        actor: str = "owner",
    ) -> dict | None:
        now = utc_now()
        async with self._session_factory() as session:
            result = await session.execute(
                select(MemoryStewardFinding).where(MemoryStewardFinding.id == finding_id)
            )
            finding = result.scalar_one_or_none()
            if not finding:
                return None
            metadata = dict(finding.metadata_ or {})
            metadata["resolution"] = {
                "status": status,
                "note": note,
                "actor": actor,
                "resolved_at": now.isoformat(),
            }
            finding.status = status
            finding.metadata_ = metadata
            finding.updated_at = now
            finding.resolved_at = now
            await session.commit()
            response = self._finding_to_dict(finding)

        if self._audit:
            await self._audit.record(
                event_type="memory_steward.finding_resolved",
                actor=actor,
                actor_type="user",
                resource_type="memory_steward_finding",
                resource_id=finding_id,
                action=status,
                metadata={"note": note},
            )
        return response

    async def _load_recent_traces(self, now: datetime) -> list[dict]:
        cutoff = now - timedelta(hours=settings.memory_steward_trace_lookback_hours)
        limit = max(1, min(settings.memory_steward_trace_limit, 1000))
        async with self._session_factory() as session:
            result = await session.execute(
                select(MemoryTrace)
                .where(MemoryTrace.created_at >= cutoff)
                .order_by(desc(MemoryTrace.created_at))
                .limit(limit)
            )
            return [self._trace_to_dict(trace) for trace in result.scalars().all()]

    def _propose_findings(self, traces: list[dict]) -> list[dict]:
        proposals: list[dict] = []
        proposals.extend(self._empty_recall_findings(traces))
        proposals.extend(self._memory_error_findings(traces))
        proposals.extend(self._missing_company_memory_findings(traces))
        return proposals

    def _empty_recall_findings(self, traces: list[dict]) -> list[dict]:
        groups: dict[tuple[str | None, str | None], list[dict]] = defaultdict(list)
        for trace in traces:
            if trace["source_type"] != "agent_invocation":
                continue
            if trace["recall_count"] != 0:
                continue
            groups[(trace["agent_id"], trace["memory_namespace"])].append(trace)

        findings = []
        threshold = max(1, settings.memory_steward_empty_recall_threshold)
        for (agent_id, memory_namespace), grouped in groups.items():
            if len(grouped) < threshold:
                continue
            company_namespace = self._company_namespace_for(memory_namespace)
            trace_ids = [trace["id"] for trace in grouped]
            findings.append({
                "finding_type": "repeated_empty_recall",
                "severity": "high" if len(grouped) >= threshold * 2 else "medium",
                "agent_id": agent_id,
                "memory_namespace": memory_namespace,
                "company_namespace": company_namespace,
                "title": f"Repeated empty memory recall for {agent_id or 'unknown agent'}",
                "description": (
                    f"{len(grouped)} recent invocations recalled no memories for "
                    f"namespace {memory_namespace or 'unknown namespace'}."
                ),
                "recommendation": (
                    "Seed durable company or role memory, review the agent memory namespace, "
                    "or adjust recall policy if the task needs shared context."
                ),
                "trace_ids": trace_ids,
                "evidence": {
                    "dedupe_key": f"repeated_empty_recall:{agent_id}:{memory_namespace}",
                    "empty_recall_count": len(grouped),
                    "threshold": threshold,
                    "sample_tasks": [trace["task_excerpt"] for trace in grouped[:3]],
                },
                "metadata": {"source": "memory_steward"},
            })
        return findings

    def _memory_error_findings(self, traces: list[dict]) -> list[dict]:
        groups: dict[tuple[str | None, str | None], list[dict]] = defaultdict(list)
        for trace in traces:
            if not trace["errors"]:
                continue
            groups[(trace["agent_id"], trace["memory_namespace"])].append(trace)

        findings = []
        for (agent_id, memory_namespace), grouped in groups.items():
            all_errors = [error for trace in grouped for error in trace["errors"]]
            error_counts = Counter(error.split(":", 1)[0] for error in all_errors)
            severity = "high" if any("write" in error for error in all_errors) else "medium"
            company_namespace = self._company_namespace_for(memory_namespace)
            findings.append({
                "finding_type": "memory_operation_errors",
                "severity": severity,
                "agent_id": agent_id,
                "memory_namespace": memory_namespace,
                "company_namespace": company_namespace,
                "title": f"Memory operation errors for {agent_id or 'unknown agent'}",
                "description": (
                    f"{len(grouped)} recent traces include memory recall/write errors."
                ),
                "recommendation": (
                    "Inspect memory service connectivity, embedding provider behavior, "
                    "and write durability for the affected namespace."
                ),
                "trace_ids": [trace["id"] for trace in grouped],
                "evidence": {
                    "dedupe_key": f"memory_operation_errors:{agent_id}:{memory_namespace}",
                    "error_counts": dict(error_counts),
                    "sample_errors": [
                        error
                        for trace in grouped[:3]
                        for error in trace["errors"][:2]
                    ][:5],
                },
                "metadata": {"source": "memory_steward"},
            })
        return findings

    def _missing_company_memory_findings(self, traces: list[dict]) -> list[dict]:
        groups: dict[str, list[dict]] = defaultdict(list)
        for trace in traces:
            read_policy = trace["read_policy"] or {}
            company_namespace = read_policy.get("company_namespace")
            if not company_namespace:
                continue
            scope_results = read_policy.get("scope_results") or []
            company_scopes = [
                scope
                for scope in scope_results
                if str(scope.get("name", "")).startswith("company_")
            ]
            if not company_scopes:
                continue
            has_company_memory = any(
                int(scope.get("added") or scope.get("returned") or 0) > 0
                for scope in company_scopes
            )
            if has_company_memory:
                continue
            groups[company_namespace].append(trace)

        findings = []
        for company_namespace, grouped in groups.items():
            if len(grouped) < 2:
                continue
            findings.append({
                "finding_type": "missing_company_shared_memory",
                "severity": "high",
                "agent_id": None,
                "memory_namespace": None,
                "company_namespace": company_namespace,
                "title": f"Missing shared company memory for {company_namespace}",
                "description": (
                    f"{len(grouped)} recent invocations searched company-scoped memory "
                    "but found no durable shared context."
                ),
                "recommendation": (
                    "Run the company builder or add semantic/procedural seed memories for "
                    "company constitution, role map, and operating loops."
                ),
                "trace_ids": [trace["id"] for trace in grouped],
                "evidence": {
                    "dedupe_key": f"missing_company_shared_memory:{company_namespace}",
                    "company_namespace": company_namespace,
                    "sample_agents": sorted(
                        {trace["agent_id"] for trace in grouped if trace["agent_id"]}
                    )[:5],
                    "sample_tasks": [trace["task_excerpt"] for trace in grouped[:3]],
                },
                "metadata": {"source": "memory_steward"},
            })
        return findings

    async def _upsert_finding(self, proposal: dict, now: datetime) -> tuple[dict, bool]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(MemoryStewardFinding).where(
                    MemoryStewardFinding.status.in_(self.OPEN_STATUSES)
                )
            )
            existing = self._matching_open_finding(result.scalars().all(), proposal)
            if existing:
                existing.trace_ids = self._unique([
                    *(existing.trace_ids or []),
                    *proposal["trace_ids"],
                ])
                evidence = dict(existing.evidence or {})
                evidence.update(proposal["evidence"])
                evidence["last_seen_at"] = now.isoformat()
                evidence["occurrence_count"] = len(existing.trace_ids)
                existing.evidence = evidence
                existing.severity = self._highest_severity(existing.severity, proposal["severity"])
                existing.description = proposal["description"]
                existing.recommendation = proposal["recommendation"]
                existing.updated_at = now
                await session.commit()
                return self._finding_to_dict(existing), False

            finding = MemoryStewardFinding(
                id=f"mem_find_{uuid.uuid4().hex[:12]}",
                finding_type=proposal["finding_type"],
                severity=proposal["severity"],
                status="open",
                agent_id=proposal["agent_id"],
                memory_namespace=proposal["memory_namespace"],
                company_namespace=proposal["company_namespace"],
                title=proposal["title"],
                description=proposal["description"],
                recommendation=proposal["recommendation"],
                trace_ids=proposal["trace_ids"],
                evidence={
                    **proposal["evidence"],
                    "first_seen_at": now.isoformat(),
                    "last_seen_at": now.isoformat(),
                    "occurrence_count": len(proposal["trace_ids"]),
                },
                metadata_=proposal["metadata"],
                created_at=now,
                updated_at=now,
            )
            session.add(finding)
            await session.commit()
            return self._finding_to_dict(finding), True

    @staticmethod
    def _matching_open_finding(
        findings: list[MemoryStewardFinding],
        proposal: dict,
    ) -> MemoryStewardFinding | None:
        dedupe_key = proposal["evidence"].get("dedupe_key")
        for finding in findings:
            if finding.finding_type != proposal["finding_type"]:
                continue
            if (finding.evidence or {}).get("dedupe_key") == dedupe_key:
                return finding
        return None

    @staticmethod
    def _trace_to_dict(trace: MemoryTrace) -> dict:
        return {
            "id": trace.id,
            "invocation_id": trace.invocation_id,
            "agent_id": trace.agent_id,
            "conversation_id": trace.conversation_id,
            "source_type": trace.source_type,
            "task_excerpt": trace.task_excerpt,
            "memory_namespace": trace.memory_namespace,
            "read_policy": trace.read_policy or {},
            "write_policy": trace.write_policy or {},
            "recalled_memory_ids": trace.recalled_memory_ids or [],
            "written_memory_ids": trace.written_memory_ids or [],
            "recall_count": trace.recall_count,
            "write_count": trace.write_count,
            "errors": trace.errors or [],
            "metadata": trace.metadata_ or {},
            "created_at": trace.created_at.isoformat(),
        }

    @staticmethod
    def _finding_to_dict(finding: MemoryStewardFinding) -> dict:
        return {
            "id": finding.id,
            "finding_type": finding.finding_type,
            "severity": finding.severity,
            "status": finding.status,
            "agent_id": finding.agent_id,
            "memory_namespace": finding.memory_namespace,
            "company_namespace": finding.company_namespace,
            "title": finding.title,
            "description": finding.description,
            "recommendation": finding.recommendation,
            "trace_ids": finding.trace_ids or [],
            "evidence": finding.evidence or {},
            "metadata": finding.metadata_ or {},
            "created_at": finding.created_at.isoformat(),
            "updated_at": finding.updated_at.isoformat(),
            "resolved_at": finding.resolved_at.isoformat() if finding.resolved_at else None,
        }

    @staticmethod
    def _company_namespace_for(memory_namespace: str | None) -> str | None:
        if not memory_namespace:
            return None
        parts = memory_namespace.split(":")
        if len(parts) >= 2 and parts[0] == "company" and parts[1]:
            return ":".join(parts[:2])
        return None

    @staticmethod
    def _highest_severity(left: str, right: str) -> str:
        order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        return left if order.get(left, 0) >= order.get(right, 0) else right

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        seen = set()
        result = []
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result
