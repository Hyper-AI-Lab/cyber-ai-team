"""Memory steward loop for trace-driven memory health review."""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import desc, select

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import Agent, MemoryEntry, MemoryStewardFinding, MemoryTrace


class MemoryStewardService:
    """Reviews memory traces and records actionable memory-health findings."""

    OPEN_STATUSES = {"open", "acknowledged"}

    def __init__(
        self,
        audit_service=None,
        memory_service=None,
        agent_manager=None,
        session_factory=async_session,
    ):
        self._audit = audit_service
        self._memory = memory_service
        self._agent_manager = agent_manager
        self._session_factory = session_factory

    async def run_once(
        self,
        *,
        now: datetime | None = None,
        actor: str = "memory_steward_loop",
        apply_safe_actions: bool | None = None,
        request_approvals: bool | None = None,
        remediation_limit: int = 100,
    ) -> dict:
        now = now or utc_now()
        traces = await self._load_recent_traces(now)
        proposals = self._propose_findings(traces)
        proposals.extend(await self._stale_procedural_memory_findings(now))
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
        if settings.memory_steward_planner_enabled:
            summary["remediation_plan"] = await self.plan_remediations(
                actor="memory_steward_planner",
                apply_safe_actions=apply_safe_actions,
                request_approvals=request_approvals,
                limit=remediation_limit,
            )
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

    async def execute_action(
        self,
        finding_id: str,
        *,
        action_type: str,
        params: dict | None = None,
        actor: str = "owner",
    ) -> dict | None:
        params = dict(params or {})
        finding = await self.get_finding(finding_id)
        if not finding:
            return None

        allowed_actions = {
            action["type"] for action in finding.get("available_actions", [])
        }
        if action_type not in allowed_actions:
            raise ValueError(f"Action {action_type} is not available for this finding")

        if action_type == "seed_memory":
            action_result = await self._execute_seed_memory(finding, params)
        elif action_type == "report_role_gap":
            action_result = await self._execute_report_role_gap(finding, params, actor)
        else:
            raise ValueError(f"Unsupported memory steward action: {action_type}")

        action_record = {
            "id": f"mem_action_{uuid.uuid4().hex[:12]}",
            "action_type": action_type,
            "actor": actor,
            "status": "applied",
            "applied_at": utc_now().isoformat(),
            "params": self._safe_params(params),
            "result": action_result,
        }
        updated = await self._record_action(finding_id, action_record)
        if self._audit:
            await self._audit.record(
                event_type="memory_steward.action_applied",
                actor=actor,
                actor_type="user",
                resource_type="memory_steward_finding",
                resource_id=finding_id,
                action=action_type,
                metadata={
                    "finding_type": finding["finding_type"],
                    "result": action_result,
                },
            )
        return {
            "action": action_record,
            "finding": updated,
        }

    async def plan_remediations(
        self,
        *,
        actor: str = "memory_steward_planner",
        apply_safe_actions: bool | None = None,
        request_approvals: bool | None = None,
        limit: int = 100,
    ) -> dict:
        apply_safe = (
            settings.memory_steward_auto_apply_safe_actions
            if apply_safe_actions is None
            else apply_safe_actions
        )
        request_action_approvals = (
            settings.memory_steward_request_action_approvals
            if request_approvals is None
            else request_approvals
        )
        reviewed_at = utc_now()
        findings = await self._load_open_findings(limit)
        planned_items = []
        counts = {
            "findings_reviewed": len(findings),
            "plans_created": 0,
            "actions_applied": 0,
            "approvals_requested": 0,
            "approvals_pending": 0,
            "already_applied": 0,
            "blocked": 0,
        }

        for finding in findings:
            plan = self._build_remediation_plan(finding, reviewed_at)
            if not plan:
                continue
            existing_plan = dict(
                (finding.get("metadata") or {}).get("remediation_plan") or {}
            )
            if self._action_already_applied(finding, plan["action_type"]):
                plan["status"] = "already_applied"
                if existing_plan.get("action_type") == plan["action_type"]:
                    for key in ("approval_id", "applied_at", "result"):
                        if key in existing_plan:
                            plan[key] = existing_plan[key]
                counts["already_applied"] += 1
            elif plan["autonomous_allowed"] and apply_safe:
                try:
                    result = await self.execute_action(
                        finding["id"],
                        action_type=plan["action_type"],
                        params=plan.get("params") or {},
                        actor=actor,
                    )
                    plan["status"] = "applied"
                    plan["applied_at"] = utc_now().isoformat()
                    plan["result"] = result["action"]["result"] if result else {}
                    counts["actions_applied"] += 1
                except ValueError as exc:
                    plan["status"] = "blocked"
                    plan["reason"] = str(exc)
                    counts["blocked"] += 1
            elif not plan["autonomous_allowed"] and request_action_approvals:
                outcome = await self._handle_planned_approval_action(
                    finding,
                    plan,
                    existing_plan,
                    actor,
                )
                counts[outcome] += 1
            else:
                plan["status"] = "planned"
                counts["plans_created"] += 1

            updated = await self._record_remediation_plan(finding["id"], plan)
            planned_items.append({
                "finding_id": finding["id"],
                "finding_type": finding["finding_type"],
                "plan": updated["metadata"].get("remediation_plan", plan),
            })

        summary = {
            "reviewed_at": reviewed_at.isoformat(),
            "actor": actor,
            **counts,
            "plans": planned_items,
        }
        if self._audit:
            await self._audit.record(
                event_type="memory_steward.remediation_planned",
                actor=actor,
                actor_type="agent",
                resource_type="memory_steward",
                action="plan_remediations",
                metadata={key: value for key, value in summary.items() if key != "plans"},
            )
        return summary

    async def get_finding(self, finding_id: str) -> dict | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(MemoryStewardFinding).where(MemoryStewardFinding.id == finding_id)
            )
            finding = result.scalar_one_or_none()
            return self._finding_to_dict(finding) if finding else None

    async def _load_open_findings(self, limit: int) -> list[dict]:
        safe_limit = max(1, min(limit, 200))
        async with self._session_factory() as session:
            result = await session.execute(
                select(MemoryStewardFinding)
                .where(MemoryStewardFinding.status.in_(self.OPEN_STATUSES))
                .order_by(desc(MemoryStewardFinding.updated_at))
                .limit(safe_limit)
            )
            return [self._finding_to_dict(finding) for finding in result.scalars().all()]

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
        proposals.extend(self._missing_trace_coverage_findings(traces))
        proposals.extend(self._namespace_mismatch_findings(traces))
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

    def _missing_trace_coverage_findings(self, traces: list[dict]) -> list[dict]:
        groups: dict[str, list[dict]] = defaultdict(list)
        traced_sources = {
            "agent_invocation",
            "chat",
            "workflow_agent_activity",
            "workflow_tool_activity",
            "workflow_memory_write",
            "tool_execution",
        }
        for trace in traces:
            if trace["source_type"] not in traced_sources:
                continue
            metadata = trace["metadata"] or {}
            if metadata.get("coverage") or metadata.get("memory_coverage"):
                continue
            groups[trace["source_type"]].append(trace)

        findings = []
        for source_type, grouped in groups.items():
            findings.append({
                "finding_type": "missing_trace_coverage",
                "severity": "medium",
                "agent_id": None,
                "memory_namespace": None,
                "company_namespace": None,
                "title": f"Missing trace coverage metadata for {source_type}",
                "description": (
                    f"{len(grouped)} recent {source_type} traces are missing normalized "
                    "coverage metadata."
                ),
                "recommendation": (
                    "Ensure every agent, chat, workflow, and tool trace writes a coverage "
                    "field so owners can filter complete, empty, write-only, and failed paths."
                ),
                "trace_ids": [trace["id"] for trace in grouped],
                "evidence": {
                    "dedupe_key": f"missing_trace_coverage:{source_type}",
                    "source_type": source_type,
                    "sample_tasks": [trace["task_excerpt"] for trace in grouped[:3]],
                },
                "metadata": {"source": "memory_steward"},
            })
        return findings

    def _namespace_mismatch_findings(self, traces: list[dict]) -> list[dict]:
        grouped: dict[tuple[str | None, str, str], list[dict]] = defaultdict(list)
        for trace in traces:
            memory_namespace = trace.get("memory_namespace")
            if not memory_namespace:
                continue
            read_policy = trace["read_policy"] or {}
            metadata = trace["metadata"] or {}
            company_namespace = (
                read_policy.get("company_namespace")
                or metadata.get("company_namespace")
            )
            if not company_namespace:
                continue
            if memory_namespace == company_namespace or memory_namespace.startswith(
                f"{company_namespace}:"
            ):
                continue
            grouped[(trace["agent_id"], memory_namespace, company_namespace)].append(trace)

        findings = []
        for (
            agent_id,
            memory_namespace,
            company_namespace,
        ), traces_for_namespace in grouped.items():
            findings.append({
                "finding_type": "namespace_mismatch",
                "severity": "high",
                "agent_id": agent_id,
                "memory_namespace": memory_namespace,
                "company_namespace": company_namespace,
                "title": f"Memory namespace mismatch for {agent_id or 'unknown agent'}",
                "description": (
                    f"{len(traces_for_namespace)} recent traces used namespace "
                    f"{memory_namespace}, but policy resolved company scope "
                    f"{company_namespace}."
                ),
                "recommendation": (
                    "Align the agent memory namespace with its company namespace before "
                    "running more autonomous work."
                ),
                "trace_ids": [trace["id"] for trace in traces_for_namespace],
                "evidence": {
                    "dedupe_key": (
                        f"namespace_mismatch:{agent_id}:{memory_namespace}:{company_namespace}"
                    ),
                    "memory_namespace": memory_namespace,
                    "company_namespace": company_namespace,
                    "sample_tasks": [
                        trace["task_excerpt"] for trace in traces_for_namespace[:3]
                    ],
                },
                "metadata": {"source": "memory_steward"},
            })
        return findings

    async def _stale_procedural_memory_findings(self, now: datetime) -> list[dict]:
        stale_days = max(1, settings.memory_steward_stale_procedural_days)
        cutoff = now - timedelta(days=stale_days)
        async with self._session_factory() as session:
            result = await session.execute(
                select(MemoryEntry)
                .where(MemoryEntry.memory_type == "procedural")
                .where(MemoryEntry.created_at < cutoff)
                .order_by(MemoryEntry.created_at)
                .limit(100)
            )
            entries = result.scalars().all()

        grouped: dict[str, list[MemoryEntry]] = defaultdict(list)
        for entry in entries:
            grouped[entry.namespace].append(entry)

        findings = []
        for namespace, entries_for_namespace in grouped.items():
            company_namespace = self._company_namespace_for(namespace)
            oldest = entries_for_namespace[0]
            findings.append({
                "finding_type": "stale_procedural_memory",
                "severity": "medium",
                "agent_id": None,
                "memory_namespace": namespace,
                "company_namespace": company_namespace,
                "title": f"Stale procedural memory in {namespace}",
                "description": (
                    f"{len(entries_for_namespace)} procedural memories in {namespace} "
                    f"are older than {stale_days} days."
                ),
                "recommendation": (
                    "Review and refresh procedural memories that guide recurring "
                    "operations before relying on them for autonomous planning."
                ),
                "trace_ids": [],
                "evidence": {
                    "dedupe_key": f"stale_procedural_memory:{namespace}",
                    "namespace": namespace,
                    "threshold_days": stale_days,
                    "oldest_memory_id": oldest.id,
                    "oldest_created_at": oldest.created_at.isoformat(),
                    "memory_count": len(entries_for_namespace),
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

    async def _execute_seed_memory(self, finding: dict, params: dict) -> dict:
        if not self._memory:
            raise ValueError("Memory service is not available")
        namespace = (
            params.get("namespace")
            or finding.get("memory_namespace")
            or finding.get("company_namespace")
        )
        if not namespace:
            raise ValueError("No memory namespace is available for this finding")
        agent_id = await self._existing_agent_id(finding.get("agent_id"))
        memory_type = params.get("memory_type") or self._seed_memory_type(finding)
        content = params.get("content") or self._default_seed_content(finding)
        memory = await self._memory.remember(
            SimpleNamespace(
                agent_id=agent_id,
                memory_type=memory_type,
                namespace=namespace,
                content=content,
                metadata={
                    "source": "memory_steward_action",
                    "finding_id": finding["id"],
                    "finding_type": finding["finding_type"],
                    "trace_ids": finding.get("trace_ids", []),
                    "action_type": "seed_memory",
                },
                importance=float(params.get("importance") or 0.75),
            )
        )
        return {
            "memory_id": memory["id"],
            "namespace": namespace,
            "memory_type": memory_type,
            "agent_id": agent_id,
        }

    async def _execute_report_role_gap(
        self,
        finding: dict,
        params: dict,
        actor: str,
    ) -> dict:
        if not self._agent_manager:
            raise ValueError("Agent manager is not available")
        title = params.get("title") or f"Memory remediation: {finding['title']}"
        gap = await self._agent_manager.report_role_gap(
            SimpleNamespace(
                title=title[:200],
                description=params.get("description")
                or self._default_role_gap_description(finding),
                severity=params.get("severity") or finding["severity"],
                source_agent_id=finding.get("agent_id"),
                source_type="memory_steward",
                company_namespace=finding.get("company_namespace") or "company:default",
                capability=params.get("capability") or self._capability_for_finding(finding),
                requested_tools=params.get("requested_tools")
                or self._requested_tools_for_finding(finding),
                context={
                    "trigger": "memory_steward_action",
                    "finding_id": finding["id"],
                    "finding_type": finding["finding_type"],
                    "dedupe_key": f"memory_steward_action:{finding['id']}:role_gap",
                    "evidence": finding.get("evidence", {}),
                },
            ),
            reporter=actor,
        )
        return {
            "role_gap_id": gap["id"],
            "role_gap_status": gap["status"],
            "capability": gap.get("capability"),
        }

    async def _handle_planned_approval_action(
        self,
        finding: dict,
        plan: dict,
        existing_plan: dict,
        actor: str,
    ) -> str:
        if not self._agent_manager:
            plan["status"] = "blocked"
            plan["reason"] = "Agent manager is not available for approval routing."
            return "blocked"
        approval_id = existing_plan.get("approval_id")
        if approval_id and await self._approval_is_executable(approval_id, finding["id"]):
            try:
                result = await self.execute_action(
                    finding["id"],
                    action_type=plan["action_type"],
                    params=plan.get("params") or {},
                    actor=actor,
                )
                await self._consume_approval(approval_id, finding["id"])
            except ValueError as exc:
                plan["status"] = "blocked"
                plan["approval_id"] = approval_id
                plan["reason"] = str(exc)
                return "blocked"
            plan["status"] = "applied"
            plan["approval_id"] = approval_id
            plan["applied_at"] = utc_now().isoformat()
            plan["result"] = result["action"]["result"] if result else {}
            return "actions_applied"
        if approval_id:
            plan["status"] = "approval_pending"
            plan["approval_id"] = approval_id
            return "approvals_pending"
        approval_id = await self._request_action_approval(finding, plan, actor)
        if not approval_id:
            plan["status"] = "planned"
            plan["reason"] = "Approval routing is not available."
            return "plans_created"
        plan["status"] = "approval_requested"
        plan["approval_id"] = approval_id
        plan["requested_at"] = utc_now().isoformat()
        return "approvals_requested"

    async def _record_remediation_plan(self, finding_id: str, plan: dict) -> dict:
        now = utc_now()
        async with self._session_factory() as session:
            result = await session.execute(
                select(MemoryStewardFinding).where(MemoryStewardFinding.id == finding_id)
            )
            finding = result.scalar_one()
            metadata = dict(finding.metadata_ or {})
            history = list(metadata.get("remediation_plan_history") or [])[-9:]
            metadata["remediation_plan"] = plan
            metadata["remediation_plan_history"] = [*history, plan]
            finding.metadata_ = metadata
            finding.updated_at = now
            await session.commit()
            return self._finding_to_dict(finding)

    async def _request_action_approval(
        self,
        finding: dict,
        plan: dict,
        actor: str,
    ) -> str | None:
        request_approval = getattr(self._agent_manager, "_request_approval", None)
        if not request_approval:
            return None
        return await request_approval(
            finding.get("agent_id"),
            f"memory_steward.{plan['action_type']}",
            plan["description"],
            {
                "finding_id": finding["id"],
                "finding_type": finding["finding_type"],
                "action_type": plan["action_type"],
                "params": plan.get("params") or {},
            },
            requester=actor,
            requester_type="agent",
            risk_level=plan["risk_level"],
            target_type="memory_steward_finding",
            target_id=finding["id"],
        )

    async def _approval_is_executable(self, approval_id: str, finding_id: str) -> bool:
        approval_is_executable = getattr(self._agent_manager, "approval_is_executable", None)
        if not approval_is_executable:
            return False
        return await approval_is_executable(
            approval_id,
            target_type="memory_steward_finding",
            target_id=finding_id,
        )

    async def _consume_approval(self, approval_id: str, finding_id: str) -> None:
        consume_approval = getattr(self._agent_manager, "consume_approval", None)
        if not consume_approval:
            return
        await consume_approval(
            approval_id,
            consumer="memory_steward_planner",
            target_type="memory_steward_finding",
            target_id=finding_id,
        )

    async def _record_action(self, finding_id: str, action_record: dict) -> dict:
        now = utc_now()
        async with self._session_factory() as session:
            result = await session.execute(
                select(MemoryStewardFinding).where(MemoryStewardFinding.id == finding_id)
            )
            finding = result.scalar_one()
            metadata = dict(finding.metadata_ or {})
            action_history = list(metadata.get("action_history") or [])
            action_history.append(action_record)
            metadata["action_history"] = action_history
            metadata["last_action"] = action_record
            finding.metadata_ = metadata
            if finding.status == "open":
                finding.status = "acknowledged"
            finding.updated_at = now
            await session.commit()
            return self._finding_to_dict(finding)

    async def _existing_agent_id(self, agent_id: str | None) -> str | None:
        if not agent_id:
            return None
        async with self._session_factory() as session:
            result = await session.execute(select(Agent.id).where(Agent.id == agent_id))
            return agent_id if result.scalar_one_or_none() else None

    def _build_remediation_plan(
        self,
        finding: dict,
        reviewed_at: datetime,
    ) -> dict | None:
        actions = {action["type"] for action in finding.get("available_actions", [])}
        if finding["finding_type"] in {
            "repeated_empty_recall",
            "missing_company_shared_memory",
        } and "seed_memory" in actions:
            return {
                "id": f"mem_plan_{uuid.uuid4().hex[:12]}",
                "finding_id": finding["id"],
                "finding_type": finding["finding_type"],
                "action_type": "seed_memory",
                "status": "planned",
                "priority": self._priority_for_finding(finding),
                "risk_level": "low",
                "autonomous_allowed": True,
                "reason": (
                    "Low-risk internal memory seed can reduce repeated context misses "
                    "without touching external systems."
                ),
                "description": self._default_seed_content(finding),
                "params": {},
                "planned_at": reviewed_at.isoformat(),
            }
        if finding["finding_type"] == "memory_operation_errors" and "report_role_gap" in actions:
            return {
                "id": f"mem_plan_{uuid.uuid4().hex[:12]}",
                "finding_id": finding["id"],
                "finding_type": finding["finding_type"],
                "action_type": "report_role_gap",
                "status": "planned",
                "priority": self._priority_for_finding(finding),
                "risk_level": "medium",
                "autonomous_allowed": False,
                "reason": (
                    "Memory infrastructure or capability gaps can create operational "
                    "backlog and should be approved before escalation."
                ),
                "description": self._default_role_gap_description(finding),
                "params": {
                    "capability": self._capability_for_finding(finding),
                    "requested_tools": self._requested_tools_for_finding(finding),
                },
                "planned_at": reviewed_at.isoformat(),
            }
        return None

    @staticmethod
    def _action_already_applied(finding: dict, action_type: str) -> bool:
        metadata = finding.get("metadata") or {}
        return any(
            action.get("action_type") == action_type and action.get("status") == "applied"
            for action in metadata.get("action_history") or []
        )

    @staticmethod
    def _priority_for_finding(finding: dict) -> str:
        if finding["severity"] in {"critical", "high"}:
            return "high"
        if finding["severity"] == "medium":
            return "medium"
        return "low"

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
        response = {
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
        response["available_actions"] = MemoryStewardService._available_actions_for(
            response
        )
        return response

    @staticmethod
    def _available_actions_for(finding: dict) -> list[dict]:
        if finding["status"] not in MemoryStewardService.OPEN_STATUSES:
            return []
        actions = []
        if finding["finding_type"] in {
            "repeated_empty_recall",
            "missing_company_shared_memory",
        }:
            actions.append({
                "type": "seed_memory",
                "label": "Seed Memory",
                "description": "Write a durable memory entry that guides future recall.",
            })
        actions.append({
            "type": "report_role_gap",
            "label": "Open Gap",
            "description": "Create a role or capability gap for follow-up.",
        })
        return actions

    @staticmethod
    def _seed_memory_type(finding: dict) -> str:
        if finding["finding_type"] == "missing_company_shared_memory":
            return "semantic"
        return "procedural"

    @staticmethod
    def _default_seed_content(finding: dict) -> str:
        sample_tasks = finding.get("evidence", {}).get("sample_tasks") or []
        sample_text = "; ".join(sample_tasks[:3]) or "No sample tasks captured."
        if finding["finding_type"] == "missing_company_shared_memory":
            return (
                "Memory Steward observed that agents searched shared company memory "
                f"for {finding.get('company_namespace') or 'the company namespace'} "
                "but found no durable context. Until the company builder or owner "
                "adds specific company facts, agents should ask for missing operating "
                "context before making assumptions. Recent tasks: "
                f"{sample_text}"
            )
        return (
            "Memory Steward observed repeated empty recall for "
            f"{finding.get('agent_id') or 'an agent'} in namespace "
            f"{finding.get('memory_namespace') or 'unknown'}. Future work in this "
            "namespace should reuse durable company, role, and procedural memories "
            "when available, and should write important decisions back to memory. "
            f"Recent tasks: {sample_text}"
        )

    @staticmethod
    def _default_role_gap_description(finding: dict) -> str:
        return (
            f"{finding['description']} Recommendation: {finding['recommendation']} "
            "This gap was opened from a Memory Steward finding and should be reviewed "
            "as part of the adaptive company operating loop."
        )

    @staticmethod
    def _capability_for_finding(finding: dict) -> str:
        mapping = {
            "repeated_empty_recall": "memory_curation",
            "missing_company_shared_memory": "company_knowledge_management",
            "memory_operation_errors": "memory_operations",
        }
        return mapping.get(finding["finding_type"], "memory_reliability")

    @staticmethod
    def _requested_tools_for_finding(finding: dict) -> list[str]:
        if finding["finding_type"] == "memory_operation_errors":
            return ["memory_remember", "knowledge_query"]
        return ["memory_remember"]

    @staticmethod
    def _safe_params(params: dict) -> dict:
        blocked = ("password", "token", "secret", "key")
        return {
            key: value
            for key, value in params.items()
            if not any(term in key.lower() for term in blocked)
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
