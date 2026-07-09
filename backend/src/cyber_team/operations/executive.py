"""Autonomous Executive Company OS v2 services."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import desc, func, select

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import (
    Agent,
    ApprovalRequest,
    AutonomousExecutionRecord,
    AutonomyPolicy,
    CompanyObjective,
    ExecutiveBenchmarkDefinition,
    ExecutiveBenchmarkResult,
    ExecutiveReflection,
    ObserverReview,
    OperatingKPIDefinition,
    OperatingKPIObservation,
    OperationGraphEdge,
    OperationGraphNode,
    OrchestrationGovernorRun,
    OrchestrationToolProposal,
    OutsourcingRequest,
    RoleManifest,
)


class ExecutiveCompanyOSService:
    """Observe, benchmark, critique, and safely execute company operations."""

    POLICY_ID = "default"
    POLICY_VERSION = "executive-company-os-v2"
    OBSERVER_AGENT_ID = "observer_agent"
    OBSERVER_ROLE_NAME = "Observer Agent"
    SECRET_MARKERS = (
        "password",
        "secret",
        "token",
        "api_key",
        "api_secret",
        "authorization",
        "credential",
    )
    PROMPT_INJECTION_MARKERS = (
        "ignore previous",
        "ignore all previous",
        "bypass approval",
        "disable approval",
        "disable safety",
        "reveal secret",
        "show secret",
        "drop table",
        "delete audit",
        "override policy",
    )
    ALLOWED_LICENSE_MARKERS = {
        "apache-2.0",
        "bsd-2-clause",
        "bsd-3-clause",
        "isc",
        "lgpl",
        "mit",
        "mpl-2.0",
        "python-2.0",
        "unlicense",
        "zlib",
    }
    PAID_COST_MARKERS = {
        "paid",
        "subscription",
        "saas_only",
        "saas-only",
        "commercial_only",
        "commercial-only",
        "requires_paid_account",
    }
    SECRET_ASSIGNMENT_PATTERN = re.compile(
        r"(?i)(password|secret|token|api[_-]?key|api[_-]?secret|authorization|credential)"
        r"(\s*[:=]\s*)"
        r"([^\s,;]+)"
    )
    BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")

    def __init__(
        self,
        *,
        governor_service=None,
        agent_manager=None,
        memory_service=None,
        audit_service=None,
        tool_registry=None,
        planning_service=None,
        readiness_evidence_service=None,
    ) -> None:
        self._governor = governor_service
        self._agent_manager = agent_manager
        self._memory = memory_service
        self._audit = audit_service
        self._tool_registry = tool_registry
        self._planning = planning_service
        self._readiness_evidence = readiness_evidence_service

    async def ensure_observer_agent(self) -> dict[str, Any]:
        """Ensure the independent Observer is visible and read-only."""
        async with async_session() as session:
            manifest = await session.get(RoleManifest, self.OBSERVER_AGENT_ID)
            if not manifest:
                manifest = RoleManifest(
                    id=self.OBSERVER_AGENT_ID,
                    family="governance",
                    name=self.OBSERVER_ROLE_NAME,
                    description=(
                        "Independently critiques executive governor decisions, "
                        "memory coverage, benchmark freshness, prompt-injection "
                        "patterns, and policy compliance."
                    ),
                    instructions_template=(
                        "You are the Observer Agent for {company_name}. Review the "
                        "Chief Operating Agent's evidence and decisions. You may "
                        "critique, block, and escalate. You never execute side "
                        "effects directly."
                    ),
                    default_tools=[
                        "memory_recall",
                        "audit_read",
                        "operation_graph_read",
                        "readiness_read",
                    ],
                    memory_namespace="company:observer",
                    approval_policy="manual",
                    success_metrics={
                        "unsafe_actions_caught": "always",
                        "false_positive_escalations": "trend_down",
                    },
                    is_core=True,
                    config={
                        "system_role": True,
                        "authority": "read_only_critique",
                        "policy_version": self.POLICY_VERSION,
                    },
                )
                session.add(manifest)

            agent = await session.get(Agent, self.OBSERVER_AGENT_ID)
            if not agent:
                agent = Agent(
                    id=self.OBSERVER_AGENT_ID,
                    role_family="governance",
                    role_name=self.OBSERVER_ROLE_NAME,
                    instructions=(
                        manifest.instructions_template.format(
                            company_name="Cyber-Team"
                        )
                    ),
                    tools=manifest.default_tools,
                    memory_namespace="company:observer",
                    approval_policy="manual",
                    status="active",
                    config={
                        "system_agent": True,
                        "side_effect_authority": "none",
                        "policy_version": self.POLICY_VERSION,
                    },
                )
                session.add(agent)
            elif agent.status != "active":
                agent.status = "active"
                agent.updated_at = utc_now()

            await session.commit()
            return self._agent_to_dict(agent)

    async def ensure_default_policy(self) -> dict[str, Any]:
        thresholds = self._default_thresholds()
        async with async_session() as session:
            policy = await session.get(AutonomyPolicy, self.POLICY_ID)
            if not policy:
                policy = AutonomyPolicy(
                    id=self.POLICY_ID,
                    mode=settings.governor_autonomy_mode,
                    resource_policy=settings.autonomy_resource_policy,
                    paused=False,
                    thresholds=thresholds,
                    policy=self._default_policy_body(),
                    updated_by="system",
                )
                session.add(policy)
                await session.commit()
            return self._policy_to_dict(policy)

    async def ensure_default_objectives(self, *, actor: str = "system") -> dict[str, Any]:
        defaults = [
            {
                "id": "objective_operational_continuity",
                "title": "Maintain autonomous operating continuity",
                "description": (
                    "Keep Cyber-Team, ERPNext, memory, approvals, readiness, and "
                    "agent work coordinated without routine owner intervention."
                ),
                "priority": "high",
                "target": {"readiness_status": "ready"},
                "tags": ["operations", "autonomy"],
            },
            {
                "id": "objective_owner_visibility",
                "title": "Keep owner visibility complete",
                "description": (
                    "Surface objectives, KPIs, benchmarks, observer critiques, "
                    "operation graph history, blocked actions, and outsourcing "
                    "requests in the owner console."
                ),
                "priority": "high",
                "target": {"owner_attention_sla_hours": 24},
                "tags": ["owner_console", "governance"],
            },
            {
                "id": "objective_foss_only",
                "title": "Prefer free and open-source resources",
                "description": (
                    "Block paid, SaaS-only, or proprietary tool proposals unless "
                    "they are explicitly marked optional future work."
                ),
                "priority": "high",
                "target": {"resource_policy": "foss_only"},
                "tags": ["foss", "cost_control"],
            },
        ]
        async with async_session() as session:
            existing = {
                row[0]
                for row in (
                    await session.execute(select(CompanyObjective.id))
                ).all()
            }
            for item in defaults:
                if item["id"] in existing:
                    continue
                session.add(
                    CompanyObjective(
                        id=item["id"],
                        title=item["title"],
                        description=item["description"],
                        status="active",
                        priority=item["priority"],
                        target=item["target"],
                        tags=item["tags"],
                        created_by=actor,
                    )
                )
            await session.commit()
        return await self.list_objectives()

    async def ensure_default_benchmarks(self, *, actor: str = "system") -> dict[str, Any]:
        definitions = [
            {
                "key": "readiness_ready",
                "title": "Readiness should remain ready",
                "description": "Overall production-readiness status should not be blocked.",
                "kpi_keys": ["readiness_blockers"],
                "rule": {"comparison": "max", "threshold": 0},
                "severity": "high",
            },
            {
                "key": "memory_findings_bounded",
                "title": "Open memory steward findings remain bounded",
                "description": "Memory issues should be low enough for reliable recall.",
                "kpi_keys": ["open_memory_findings"],
                "rule": {"comparison": "max", "threshold": 5},
                "severity": "medium",
            },
            {
                "key": "role_backlog_bounded",
                "title": "Role backlog remains reviewable",
                "description": "Role gaps should not grow without owner-visible review.",
                "kpi_keys": ["active_role_gaps"],
                "rule": {"comparison": "max", "threshold": 10},
                "severity": "medium",
            },
            {
                "key": "workflow_failures_zero",
                "title": "Recent workflow failures stay at zero",
                "description": "Repeated workflow failures should trigger attention.",
                "kpi_keys": ["recent_workflow_failures"],
                "rule": {"comparison": "max", "threshold": 0},
                "severity": "medium",
            },
            {
                "key": "tool_blockers_zero",
                "title": "Required tool blockers stay at zero",
                "description": "Side-effectful unready tools should become explicit proposals.",
                "kpi_keys": ["side_effect_tool_blockers"],
                "rule": {"comparison": "max", "threshold": 0},
                "severity": "medium",
            },
        ]
        async with async_session() as session:
            existing = {
                row[0]
                for row in (
                    await session.execute(select(ExecutiveBenchmarkDefinition.key))
                ).all()
            }
            for definition in definitions:
                if definition["key"] not in existing:
                    session.add(
                        ExecutiveBenchmarkDefinition(
                            id=f"bench_{uuid.uuid4().hex[:16]}",
                            created_by=actor,
                            status="active",
                            metadata_={"source": "executive_company_os_defaults"},
                            **definition,
                        )
                    )
            await session.commit()
        return await self.list_benchmarks()

    async def list_objectives(self) -> dict[str, Any]:
        async with async_session() as session:
            result = await session.execute(
                select(CompanyObjective).order_by(
                    CompanyObjective.priority.desc(),
                    CompanyObjective.created_at,
                )
            )
            items = [self._objective_to_dict(item) for item in result.scalars()]
        return {"items": items, "count": len(items)}

    async def replace_objectives(
        self,
        *,
        actor: str,
        objectives: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not objectives:
            raise ValueError("At least one objective is required")
        now = utc_now()
        async with async_session() as session:
            current = {
                item.id: item
                for item in (
                    await session.execute(select(CompanyObjective))
                ).scalars()
            }
            seen: set[str] = set()
            for index, item in enumerate(objectives, start=1):
                title = str(item.get("title") or "").strip()
                if not title:
                    raise ValueError("Objective title is required")
                objective_id = str(
                    item.get("id")
                    or f"objective_{hashlib.sha256(title.encode()).hexdigest()[:16]}"
                )
                seen.add(objective_id)
                objective = current.get(objective_id)
                payload = {
                    "title": title[:240],
                    "description": str(item.get("description") or "")[:4000],
                    "status": str(item.get("status") or "active")[:30],
                    "priority": str(item.get("priority") or "medium")[:20],
                    "target": item.get("target") or {},
                    "tags": item.get("tags") or [],
                    "updated_at": now,
                }
                if objective:
                    for key, value in payload.items():
                        setattr(objective, key, value)
                else:
                    session.add(
                        CompanyObjective(
                            id=objective_id,
                            created_by=actor,
                            created_at=now + timedelta(microseconds=index),
                            **payload,
                        )
                    )
            for objective_id, objective in current.items():
                if objective_id not in seen:
                    objective.status = "archived"
                    objective.updated_at = now
            await session.commit()
        await self._record_audit(
            event_type="executive_objectives.updated",
            actor=actor,
            resource_id="company_objectives",
            metadata={"count": len(objectives)},
        )
        return await self.list_objectives()

    async def get_policy(self) -> dict[str, Any]:
        return await self.ensure_default_policy()

    async def update_policy(
        self,
        *,
        actor: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        await self.ensure_default_policy()
        async with async_session() as session:
            policy = await session.get(AutonomyPolicy, self.POLICY_ID)
            if not policy:
                raise RuntimeError("Autonomy policy could not be loaded")
            if "mode" in updates:
                policy.mode = str(updates["mode"])[:60]
            if "resource_policy" in updates:
                policy.resource_policy = str(updates["resource_policy"])[:60]
            if "paused" in updates:
                policy.paused = bool(updates["paused"])
            thresholds = dict(policy.thresholds or {})
            thresholds.update(updates.get("thresholds") or {})
            policy.thresholds = self._sanitize_thresholds(thresholds)
            body = dict(policy.policy or {})
            body.update(updates.get("policy") or {})
            policy.policy = self._redact(body)
            policy.updated_by = actor
            policy.updated_at = utc_now()
            await session.commit()
            output = self._policy_to_dict(policy)
        await self._record_audit(
            event_type="executive_autonomy_policy.updated",
            actor=actor,
            resource_id=self.POLICY_ID,
            metadata={
                "mode": output["mode"],
                "paused": output["paused"],
                "resource_policy": output["resource_policy"],
            },
        )
        return output

    async def pause(self, *, actor: str, reason: str = "") -> dict[str, Any]:
        return await self.update_policy(
            actor=actor,
            updates={"paused": True, "policy": {"pause_reason": reason}},
        )

    async def resume(self, *, actor: str, reason: str = "") -> dict[str, Any]:
        return await self.update_policy(
            actor=actor,
            updates={"paused": False, "policy": {"resume_reason": reason}},
        )

    async def run_executive_cycle(
        self,
        *,
        actor: str = "chief_operating_agent",
        dry_run: bool = False,
        auto_apply_low_risk: bool | None = None,
        max_actions: int | None = None,
        force_reflection: bool = False,
        force_benchmark_refresh: bool = False,
        owner_instruction: str | None = None,
        observer_review: bool = True,
        synthetic_large_impact: bool = False,
    ) -> dict[str, Any]:
        await self.ensure_observer_agent()
        await self.ensure_default_policy()
        await self.ensure_default_objectives(actor=actor)
        await self.ensure_default_benchmarks(actor=actor)
        policy = await self.get_policy()
        safe_max_actions = max(1, min(max_actions or settings.governor_max_actions_per_cycle, 50))
        apply_low_risk = (
            settings.governor_auto_apply_low_risk
            if auto_apply_low_risk is None
            else bool(auto_apply_low_risk)
        )
        started_at = utc_now()
        snapshot = await self._build_executive_snapshot()
        snapshot_hash = self._stable_hash(snapshot)
        mode = "executive_dry_run" if dry_run else "executive"
        if policy["paused"]:
            mode = "executive_paused"
        run_id = f"exegov_{uuid.uuid4().hex[:12]}"
        actions = self._propose_actions(
            snapshot,
            owner_instruction=owner_instruction,
            synthetic_large_impact=synthetic_large_impact,
        )[:safe_max_actions]
        brief = self._operating_brief(snapshot, actions, policy)
        run = OrchestrationGovernorRun(
            id=run_id,
            status="running",
            actor=actor,
            policy_version=self.POLICY_VERSION,
            mode=mode,
            auto_apply_low_risk=apply_low_risk,
            max_actions=safe_max_actions,
            snapshot_hash=snapshot_hash,
            operating_snapshot=snapshot,
            operating_brief=brief,
            counts={},
            errors=[],
            started_at=started_at,
        )
        async with async_session() as session:
            session.add(run)
            await session.commit()

        run_node = await self._upsert_graph_node(
            node_type="executive_governor_run",
            title="Executive governor cycle",
            summary=brief,
            source_type="executive_governor",
            source_id=run_id,
            agent_id="chief_operating_agent",
            risk_level="low",
            confidence=1.0,
            impact_score=0.0,
            tags=["executive", "governor", "run"],
            metadata={
                "snapshot_hash": snapshot_hash,
                "policy": policy,
                "dry_run": dry_run,
                "force_reflection": force_reflection,
                "force_benchmark_refresh": force_benchmark_refresh,
            },
            idempotency_key=f"operation_graph:run:{run_id}",
        )
        owner_instruction_node = await self._record_owner_instruction_context(
            run_node_id=run_node["id"],
            actor=actor,
            owner_instruction=owner_instruction,
        )
        observations = await self._record_kpi_observations(run_id, snapshot)
        benchmark_results = await self._record_benchmark_results(
            run_id,
            observations,
            force_refresh=force_benchmark_refresh,
        )
        for result in benchmark_results:
            node = await self._upsert_graph_node(
                node_type="benchmark_result",
                title=f"Benchmark {result['benchmark_key']}: {result['status']}",
                summary=result["detail"],
                source_type="benchmark",
                source_id=result["id"],
                risk_level="low",
                confidence=1.0,
                impact_score=0.0,
                tags=["benchmark", result["status"]],
                metadata=result,
                idempotency_key=f"operation_graph:benchmark:{result['id']}",
            )
            await self._create_graph_edge(
                run_node["id"],
                node["id"],
                "produced_benchmark_result",
            )

        review = None
        if observer_review and settings.observer_enabled:
            review = await self._run_observer_review(
                run_id=run_id,
                snapshot=snapshot,
                actions=actions,
                benchmark_results=benchmark_results,
                owner_instruction=owner_instruction,
            )
            node = await self._upsert_graph_node(
                node_type="observer_review",
                title=f"Observer review: {review['status']}",
                summary=review["critique"],
                source_type="observer_review",
                source_id=review["id"],
                agent_id=self.OBSERVER_AGENT_ID,
                risk_level="low",
                confidence=review["confidence"],
                impact_score=0.0,
                tags=["observer", review["status"]],
                metadata=review,
                idempotency_key=f"operation_graph:observer:{review['id']}",
            )
            await self._create_graph_edge(run_node["id"], node["id"], "critiqued_by")

        executions = []
        errors = []
        observer_blocks = bool(
            review
            and review["status"] in {"disagreed", "escalated"}
            and review["unresolved_objections"]
        )
        for action in actions:
            try:
                execution = await self._execute_action(
                    run_id=run_id,
                    run_node_id=run_node["id"],
                    actor=actor,
                    action=action,
                    policy=policy,
                    dry_run=dry_run,
                    auto_apply_low_risk=apply_low_risk,
                    paused=policy["paused"],
                    observer_blocks=observer_blocks,
                )
                executions.append(execution)
            except Exception as exc:
                errors.append(
                    {
                        "action_type": action.get("action_type"),
                        "title": action.get("title"),
                        "message": str(exc),
                        "type": type(exc).__name__,
                    }
                )

        reflection = await self._record_reflection(
            run_id=run_id,
            snapshot=snapshot,
            executions=executions,
            review=review,
            benchmark_results=benchmark_results,
            force=force_reflection,
        )
        node = await self._upsert_graph_node(
            node_type="executive_reflection",
            title="Executive reflection",
            summary=reflection["summary"],
            source_type="executive_reflection",
            source_id=reflection["id"],
            agent_id="chief_operating_agent",
            risk_level="low",
            confidence=0.9,
            impact_score=0.0,
            tags=["reflection", "memory"],
            metadata=reflection,
            idempotency_key=f"operation_graph:reflection:{reflection['id']}",
        )
        await self._create_graph_edge(run_node["id"], node["id"], "reflected_in")

        status = "completed"
        if errors:
            status = "degraded"
        if policy["paused"]:
            status = "paused"
        elif observer_blocks:
            status = "blocked"
        counts = self._run_counts(
            executions,
            benchmark_results=benchmark_results,
            review=review,
            errors=errors,
        )
        async with async_session() as session:
            saved = await session.get(OrchestrationGovernorRun, run_id)
            if saved:
                saved.status = status
                saved.completed_at = utc_now()
                saved.counts = counts
                saved.errors = errors
                await session.commit()

        await self._write_operation_memory(
            run_id=run_id,
            brief=brief,
            counts=counts,
            review=review,
            reflection=reflection,
        )
        await self._record_run_audit(
            actor=actor,
            run_id=run_id,
            status=status,
            counts=counts,
            errors=errors,
            dry_run=dry_run,
        )
        return {
            "run_id": run_id,
            "status": status,
            "actor": actor,
            "policy_version": self.POLICY_VERSION,
            "mode": mode,
            "started_at": started_at.isoformat(),
            "completed_at": utc_now().isoformat(),
            "snapshot_hash": snapshot_hash,
            "operating_brief": brief,
            "objective_summary": await self._objective_summary(),
            "kpi_summary": self._kpi_summary(observations),
            "benchmark_summary": self._benchmark_summary(benchmark_results),
            "reflection_summary": reflection,
            "observer_review": review,
            "consensus_state": self._consensus_state(review),
            "autonomous_executions": executions,
            "blocked_actions": [
                item
                for item in executions
                if item["status"] in {"approval_required", "blocked", "outsourcing_required"}
            ],
            "approvals_created": [
                item
                for item in executions
                if item["status"] == "approval_required" and item.get("approval_id")
            ],
            "outsourcing_requests": [
                item
                for item in executions
                if item["status"] == "outsourcing_required"
            ],
            "operation_graph": {
                "run_node_id": run_node["id"],
                "owner_instruction_node_id": (
                    owner_instruction_node["id"] if owner_instruction_node else None
                ),
            },
            "counts": counts,
            "errors": errors,
            "resource_policy": await self.resource_policy_status(),
        }

    async def executive_brief(self) -> dict[str, Any]:
        latest = await self.latest_run()
        policy = await self.get_policy()
        return {
            "latest_run": latest,
            "objectives": await self.list_objectives(),
            "policy": policy,
            "kpis": await self.latest_kpis(),
            "benchmarks": {
                "definitions": await self.list_benchmarks(),
                "latest_results": await self.list_benchmark_results(limit=20),
            },
            "operation_graph": await self.operation_graph(limit=20),
            "reflections": await self.list_reflections(limit=5),
            "observer": await self.observer_status(),
            "outsourcing": await self.list_outsourcing_requests(status="open", limit=20),
            "resource_policy": await self.resource_policy_status(),
            "readiness": await self.readiness(),
        }

    async def latest_run(self) -> dict[str, Any] | None:
        async with async_session() as session:
            result = await session.execute(
                select(OrchestrationGovernorRun)
                .where(OrchestrationGovernorRun.policy_version == self.POLICY_VERSION)
                .order_by(desc(OrchestrationGovernorRun.started_at))
                .limit(1)
            )
            run = result.scalar_one_or_none()
            return self._run_to_dict(run) if run else None

    async def list_runs(self, *, limit: int = 20) -> dict[str, Any]:
        safe_limit = max(1, min(limit, 200))
        async with async_session() as session:
            result = await session.execute(
                select(OrchestrationGovernorRun)
                .where(OrchestrationGovernorRun.policy_version == self.POLICY_VERSION)
                .order_by(desc(OrchestrationGovernorRun.started_at))
                .limit(safe_limit)
            )
            items = [self._run_to_dict(run) for run in result.scalars()]
        return {"items": items, "count": len(items), "limit": safe_limit}

    async def operation_graph(
        self,
        *,
        node_type: str | None = None,
        source_type: str | None = None,
        risk_level: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(limit, 500))
        async with async_session() as session:
            query = select(OperationGraphNode)
            if node_type:
                query = query.where(OperationGraphNode.node_type == node_type)
            if source_type:
                query = query.where(OperationGraphNode.source_type == source_type)
            if risk_level:
                query = query.where(OperationGraphNode.risk_level == risk_level)
            result = await session.execute(
                query.order_by(desc(OperationGraphNode.created_at)).limit(safe_limit)
            )
            nodes = [self._graph_node_to_dict(node) for node in result.scalars()]
            node_ids = [node["id"] for node in nodes]
            edges = []
            if node_ids:
                edge_result = await session.execute(
                    select(OperationGraphEdge)
                    .where(
                        (OperationGraphEdge.source_node_id.in_(node_ids))
                        | (OperationGraphEdge.target_node_id.in_(node_ids))
                    )
                    .order_by(desc(OperationGraphEdge.created_at))
                    .limit(safe_limit * 2)
                )
                edges = [
                    self._graph_edge_to_dict(edge)
                    for edge in edge_result.scalars()
                ]
        return {"nodes": nodes, "edges": edges, "count": len(nodes), "limit": safe_limit}

    async def list_reflections(self, *, limit: int = 50) -> dict[str, Any]:
        safe_limit = max(1, min(limit, 200))
        async with async_session() as session:
            result = await session.execute(
                select(ExecutiveReflection)
                .order_by(desc(ExecutiveReflection.created_at))
                .limit(safe_limit)
            )
            items = [self._reflection_to_dict(item) for item in result.scalars()]
        return {"items": items, "count": len(items), "limit": safe_limit}

    async def list_benchmarks(self) -> dict[str, Any]:
        async with async_session() as session:
            result = await session.execute(
                select(ExecutiveBenchmarkDefinition).order_by(
                    ExecutiveBenchmarkDefinition.key
                )
            )
            items = [self._benchmark_to_dict(item) for item in result.scalars()]
        return {"items": items, "count": len(items)}

    async def create_benchmark(
        self,
        *,
        actor: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        key = str(data.get("key") or "").strip().lower().replace(" ", "_")
        if not key:
            raise ValueError("Benchmark key is required")
        rule = data.get("rule") or {}
        if "comparison" not in rule or "threshold" not in rule:
            raise ValueError("Benchmark rule must include comparison and threshold")
        async with async_session() as session:
            existing = (
                await session.execute(
                    select(ExecutiveBenchmarkDefinition)
                    .where(ExecutiveBenchmarkDefinition.key == key)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing:
                existing.title = str(data.get("title") or existing.title)[:240]
                existing.description = str(data.get("description") or "")[:4000]
                existing.kpi_keys = data.get("kpi_keys") or existing.kpi_keys
                existing.rule = rule
                existing.severity = str(data.get("severity") or "medium")[:20]
                existing.status = str(data.get("status") or "active")[:30]
                existing.metadata_ = self._redact(data.get("metadata") or {})
                existing.updated_at = utc_now()
                item = existing
            else:
                item = ExecutiveBenchmarkDefinition(
                    id=f"bench_{uuid.uuid4().hex[:16]}",
                    key=key,
                    title=str(data.get("title") or key)[:240],
                    description=str(data.get("description") or "")[:4000],
                    kpi_keys=data.get("kpi_keys") or [],
                    rule=rule,
                    severity=str(data.get("severity") or "medium")[:20],
                    status=str(data.get("status") or "active")[:30],
                    created_by=actor,
                    metadata_=self._redact(data.get("metadata") or {}),
                )
                session.add(item)
            await session.commit()
            output = self._benchmark_to_dict(item)
        await self._record_audit(
            event_type="executive_benchmark.upserted",
            actor=actor,
            resource_id=key,
            metadata={"severity": output["severity"]},
        )
        return output

    async def list_benchmark_results(self, *, limit: int = 100) -> dict[str, Any]:
        safe_limit = max(1, min(limit, 500))
        async with async_session() as session:
            result = await session.execute(
                select(ExecutiveBenchmarkResult)
                .order_by(desc(ExecutiveBenchmarkResult.created_at))
                .limit(safe_limit)
            )
            items = [self._benchmark_result_to_dict(item) for item in result.scalars()]
        counts: dict[str, int] = {}
        for item in items:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        return {"items": items, "count": len(items), "counts": counts, "limit": safe_limit}

    async def latest_kpis(self) -> dict[str, Any]:
        async with async_session() as session:
            result = await session.execute(
                select(OperatingKPIObservation)
                .order_by(desc(OperatingKPIObservation.observed_at))
                .limit(100)
            )
            observations = [self._kpi_observation_to_dict(item) for item in result.scalars()]
        latest: dict[str, dict[str, Any]] = {}
        for item in observations:
            latest.setdefault(item["kpi_key"], item)
        return {"items": list(latest.values()), "count": len(latest)}

    async def list_observer_reviews(self, *, limit: int = 100) -> dict[str, Any]:
        safe_limit = max(1, min(limit, 500))
        async with async_session() as session:
            result = await session.execute(
                select(ObserverReview)
                .order_by(desc(ObserverReview.created_at))
                .limit(safe_limit)
            )
            items = [self._observer_review_to_dict(item) for item in result.scalars()]
        return {"items": items, "count": len(items), "limit": safe_limit}

    async def run_observer_review(
        self,
        *,
        actor: str,
        run_id: str | None = None,
        owner_instruction: str | None = None,
    ) -> dict[str, Any]:
        await self.ensure_observer_agent()
        snapshot = await self._build_executive_snapshot()
        actions = self._propose_actions(snapshot, owner_instruction=owner_instruction)
        benchmark_results = (await self.list_benchmark_results(limit=20))["items"]
        review = await self._run_observer_review(
            run_id=run_id,
            snapshot=snapshot,
            actions=actions,
            benchmark_results=benchmark_results,
            owner_instruction=owner_instruction,
        )
        await self._record_audit(
            event_type="observer.review",
            actor=actor,
            resource_id=review["id"],
            metadata={
                "status": review["status"],
                "run_id": run_id,
                "findings": len(review["findings"]),
            },
        )
        return review

    async def observer_status(self) -> dict[str, Any]:
        async with async_session() as session:
            agent = await session.get(Agent, self.OBSERVER_AGENT_ID)
            latest = (
                await session.execute(
                    select(ObserverReview)
                    .order_by(desc(ObserverReview.created_at))
                    .limit(1)
                )
            ).scalar_one_or_none()
        return {
            "enabled": settings.observer_enabled,
            "review_required": settings.observer_review_required,
            "agent_present": bool(agent),
            "status": "ready" if agent and agent.status == "active" else "waiting",
            "latest_review": self._observer_review_to_dict(latest) if latest else None,
            "side_effect_authority": "none",
        }

    async def create_outsourcing_request(
        self,
        *,
        actor: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        title = str(data.get("title") or "").strip()
        if not title:
            raise ValueError("Outsourcing request title is required")
        task_spec = self._redact(data.get("task_spec") or {})
        dedupe_tool = str(task_spec.get("tool_or_skill") or title).strip()
        source_type = data.get("source_type")
        source_id = data.get("source_id")
        request_id = f"out_{uuid.uuid4().hex[:16]}"
        async with async_session() as session:
            query = select(OutsourcingRequest).where(OutsourcingRequest.status == "open")
            if source_type is None:
                query = query.where(OutsourcingRequest.source_type.is_(None))
            else:
                query = query.where(OutsourcingRequest.source_type == source_type)
            if source_id is None:
                query = query.where(OutsourcingRequest.source_id.is_(None))
            else:
                query = query.where(OutsourcingRequest.source_id == source_id)
            existing = (await session.execute(query)).scalars().all()
            for candidate in existing:
                candidate_tool = str(
                    (candidate.task_spec or {}).get("tool_or_skill")
                    or candidate.title
                ).strip()
                if candidate_tool == dedupe_tool:
                    return self._outsourcing_to_dict(candidate)
            item = OutsourcingRequest(
                id=request_id,
                title=title[:240],
                status=str(data.get("status") or "open")[:30],
                complexity_reason=str(data.get("complexity_reason") or "")[:4000],
                task_spec=task_spec,
                context_pack=self._redact(data.get("context_pack") or {}),
                acceptance_tests=data.get("acceptance_tests") or [],
                foss_constraints=data.get("foss_constraints") or self._foss_constraints(),
                security_constraints=data.get("security_constraints")
                or self._security_constraints(),
                files_involved=data.get("files_involved") or [],
                expected_artifact=str(data.get("expected_artifact") or "")[:4000],
                replay_instructions=str(data.get("replay_instructions") or "")[:4000],
                source_type=source_type,
                source_id=source_id,
                created_by=actor,
                resolution={},
            )
            session.add(item)
            await session.commit()
            output = self._outsourcing_to_dict(item)
        await self._record_audit(
            event_type="outsourcing_request.created",
            actor=actor,
            resource_id=request_id,
            metadata={"title": title, "source_type": data.get("source_type")},
        )
        return output

    async def list_outsourcing_requests(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(limit, 500))
        async with async_session() as session:
            query = select(OutsourcingRequest)
            if status:
                query = query.where(OutsourcingRequest.status == status)
            result = await session.execute(
                query.order_by(desc(OutsourcingRequest.created_at)).limit(safe_limit)
            )
            items = [self._outsourcing_to_dict(item) for item in result.scalars()]
        counts: dict[str, int] = {}
        for item in items:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        return {"items": items, "count": len(items), "counts": counts, "limit": safe_limit}

    async def deduplicate_outsourcing_requests(
        self,
        *,
        actor: str,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        async with async_session() as session:
            result = await session.execute(
                select(OutsourcingRequest)
                .where(OutsourcingRequest.status == "open")
                .order_by(OutsourcingRequest.created_at, OutsourcingRequest.id)
            )
            items = list(result.scalars())
            groups: dict[tuple[str | None, str | None, str], list[OutsourcingRequest]] = {}
            for item in items:
                key = self._outsourcing_dedupe_key(item)
                groups.setdefault(key, []).append(item)

            duplicate_groups = []
            duplicates: list[OutsourcingRequest] = []
            for (source_type, source_id, tool), group in groups.items():
                if len(group) <= 1:
                    continue
                canonical = group[0]
                duplicate_items = group[1:]
                duplicates.extend(duplicate_items)
                duplicate_groups.append(
                    {
                        "source_type": source_type,
                        "source_id": source_id,
                        "tool_or_skill": tool,
                        "canonical_request_id": canonical.id,
                        "duplicate_request_ids": [item.id for item in duplicate_items],
                        "duplicate_count": len(duplicate_items),
                    }
                )

            if not dry_run:
                now = utc_now()
                for item in duplicates:
                    canonical_id = next(
                        group["canonical_request_id"]
                        for group in duplicate_groups
                        if item.id in group["duplicate_request_ids"]
                    )
                    item.status = "deduplicated"
                    item.resolution = {
                        "status": "deduplicated",
                        "canonical_request_id": canonical_id,
                        "reason": (
                            "Exact duplicate open outsourcing request for the same "
                            "source and tool/skill."
                        ),
                        "resolved_by": actor,
                    }
                    item.updated_at = now
                    item.resolved_at = now
                await session.commit()

        if duplicate_groups and not dry_run:
            await self._record_audit(
                event_type="outsourcing_request.deduplicated",
                actor=actor,
                resource_id="outsourcing_requests",
                metadata={
                    "duplicate_count": len(duplicates),
                    "group_count": len(duplicate_groups),
                },
            )
        return {
            "dry_run": dry_run,
            "group_count": len(duplicate_groups),
            "duplicate_count": len(duplicates),
            "groups": duplicate_groups,
            "status": "ready" if not duplicates else "duplicates_found",
        }

    async def resolve_outsourcing_request(
        self,
        request_id: str,
        *,
        actor: str,
        resolution: dict[str, Any],
    ) -> dict[str, Any]:
        async with async_session() as session:
            item = await session.get(OutsourcingRequest, request_id)
            if not item:
                raise ValueError("Outsourcing request not found")
            status = str(resolution.get("status") or "resolved")[:30]
            item.status = status
            item.resolution = self._redact(resolution)
            item.updated_at = utc_now()
            item.resolved_at = utc_now()
            await session.commit()
            output = self._outsourcing_to_dict(item)
        await self._record_audit(
            event_type="outsourcing_request.resolved",
            actor=actor,
            resource_id=request_id,
            metadata={"status": output["status"]},
        )
        return output

    @staticmethod
    def _outsourcing_dedupe_key(
        item: OutsourcingRequest,
    ) -> tuple[str | None, str | None, str]:
        tool = str((item.task_spec or {}).get("tool_or_skill") or item.title).strip()
        return (item.source_type, item.source_id, tool)

    async def resource_policy_status(self) -> dict[str, Any]:
        policy = await self.get_policy()
        blockers = []
        warnings = []
        notices = []
        proposals = []
        async with async_session() as session:
            result = await session.execute(
                select(OrchestrationToolProposal)
                .order_by(desc(OrchestrationToolProposal.created_at))
                .limit(200)
            )
            proposals = [self._proposal_resource_view(item) for item in result.scalars()]
        for proposal in proposals:
            analysis = proposal["resource_analysis"]
            if analysis["paid_or_saas_only"] and proposal["status"] != "future_optional":
                blockers.append(
                    {
                        "proposal_id": proposal["id"],
                        "title": proposal["title"],
                        "reason": "Paid or SaaS-only resource proposal is not allowed.",
                    }
                )
            if analysis["license_unknown"]:
                warnings.append(
                    {
                        "proposal_id": proposal["id"],
                        "title": proposal["title"],
                        "reason": "License is not declared; owner review is required.",
                    }
                )
            if analysis["data_sharing_risk"]:
                notices.append(
                    {
                        "proposal_id": proposal["id"],
                        "title": proposal["title"],
                        "reason": (
                            "Proposal declares external data-sharing risk; owner "
                            "approval is still required before activation."
                        ),
                    }
                )
        status = "ready" if not blockers else "blocked"
        return {
            "status": status,
            "blocking": bool(blockers),
            "policy": policy["resource_policy"],
            "foss_only": policy["resource_policy"] == "foss_only",
            "blockers": blockers,
            "warnings": warnings,
            "notices": notices,
            "proposal_count": len(proposals),
            "checked_at": utc_now().isoformat(),
            "detail": (
                "FOSS-only resource policy is satisfied."
                if not blockers
                else "One or more resource proposals violate the FOSS-only policy."
            ),
        }

    async def readiness(self) -> dict[str, Any]:
        latest = await self.latest_run()
        policy = await self.get_policy()
        observer = await self.observer_status()
        resource_policy = await self.resource_policy_status()
        graph = await self.operation_graph(limit=1)
        benchmarks = await self.list_benchmark_results(limit=20)
        outsourcing = await self.list_outsourcing_requests(status="open", limit=20)
        stale_benchmark = not benchmarks["items"]
        blocking = bool(resource_policy["blocking"])
        if settings.observer_review_required:
            blocking = blocking or observer["status"] != "ready"
        status = (
            "ready"
            if not blocking and latest
            else "waiting"
            if not blocking
            else "blocked"
        )
        return {
            "status": status,
            "blocking": blocking,
            "enabled": settings.governor_enabled,
            "mode": policy["mode"],
            "paused": policy["paused"],
            "resource_policy": resource_policy,
            "observer": observer,
            "operation_graph": {
                "indexing_enabled": settings.operation_graph_indexing_enabled,
                "latest_node_present": bool(graph["nodes"]),
            },
            "benchmark_freshness": {
                "status": "ready" if not stale_benchmark else "waiting",
                "stale": stale_benchmark,
                "latest_count": benchmarks["count"],
            },
            "reflection_freshness": await self._reflection_freshness(),
            "outsourcing": {
                "open_count": outsourcing["count"],
                "status": "ready" if outsourcing["count"] == 0 else "attention",
                "blocking": False,
            },
            "latest_run": latest,
            "thresholds": policy["thresholds"],
        }

    async def _build_executive_snapshot(self) -> dict[str, Any]:
        governor_snapshot = {}
        if self._governor and hasattr(self._governor, "build_operating_snapshot"):
            governor_snapshot = await self._governor.build_operating_snapshot()
        evidence = {}
        if self._readiness_evidence:
            try:
                evidence = await self._readiness_evidence.summary()
            except Exception as exc:
                evidence = {"status": "degraded", "detail": str(exc)}
        policy = await self.get_policy()
        snapshot = {
            "generated_at": utc_now().isoformat(),
            "policy_version": self.POLICY_VERSION,
            "environment": settings.environment,
            "autonomy_policy": policy,
            "governor_snapshot": governor_snapshot,
            "readiness_evidence": evidence,
            "objectives": await self.list_objectives(),
            "resource_policy": await self.resource_policy_status(),
        }
        return self._redact(snapshot)

    def _propose_actions(
        self,
        snapshot: dict[str, Any],
        *,
        owner_instruction: str | None = None,
        synthetic_large_impact: bool = False,
    ) -> list[dict[str, Any]]:
        actions = []
        instruction = (owner_instruction or "").strip()
        if instruction:
            lower = instruction.lower()
            instruction_summary = self._sanitize_instruction_text(instruction)
            instruction_source_id = self._stable_hash(
                {"instruction": instruction_summary}
            )[:16]
            suspicious = [
                marker
                for marker in self.PROMPT_INJECTION_MARKERS
                if marker in lower
            ]
            if suspicious:
                actions.append(
                    self._action_spec(
                        action_type="escalate_owner_attention",
                        title="Owner instruction requires safety review",
                        summary=(
                            "The instruction contains prompt-injection or policy "
                            "override markers and must be reviewed before action."
                        ),
                        risk_level="high",
                        confidence=0.5,
                        impact={"financial_usd": 0, "recipients": 0},
                        source_type="owner_instruction",
                        source_id=instruction_source_id,
                        payload={
                            "instruction_summary": instruction_summary[:500],
                            "suspicious_markers": suspicious,
                        },
                    )
                )
            else:
                actions.append(
                    self._action_spec(
                        action_type="seed_memory",
                        title="Record owner instruction as executive memory",
                        summary="Owner instruction is safe to record for future recall.",
                        risk_level="low",
                        confidence=0.9,
                        impact={"financial_usd": 0, "recipients": 0},
                        source_type="owner_instruction",
                        source_id=instruction_source_id,
                        payload={"instruction_summary": instruction_summary[:1000]},
                    )
                )
        if synthetic_large_impact:
            actions.append(
                self._action_spec(
                    action_type="synthetic_large_impact",
                    title="Synthetic large-impact action",
                    summary="Used by staging acceptance to prove threshold blocking.",
                    risk_level="high",
                    confidence=0.91,
                    impact={
                        "financial_usd": settings.governor_financial_action_limit_usd
                        + 1
                    },
                    source_type="staging_acceptance",
                    source_id="large_impact_block",
                    payload={"external_write": True},
                )
            )
        governor = snapshot.get("governor_snapshot") or {}
        memory = governor.get("memory") or {}
        if int(memory.get("open_findings") or 0) > 0:
            actions.append(
                self._action_spec(
                    action_type="create_plan",
                    title="Create plan for memory steward findings",
                    summary="Open memory findings can reduce the orchestrator's recall quality.",
                    risk_level="low",
                    confidence=0.88,
                    impact={"financial_usd": 0, "recipients": 0},
                    source_type="memory_steward",
                    source_id="open_findings",
                    payload={"target_view": "memory"},
                )
            )
        role_backlog = governor.get("role_backlog") or {}
        if int(role_backlog.get("active") or 0) > 0:
            actions.append(
                self._action_spec(
                    action_type="propose_role",
                    title="Review active company role backlog",
                    summary="Role gaps are available for owner-visible action.",
                    risk_level="low",
                    confidence=0.86,
                    impact={"financial_usd": 0, "recipients": 0},
                    source_type="role_backlog",
                    source_id="active",
                    payload={"target_view": "agents"},
                )
            )
        for gap in governor.get("role_gap_samples") or []:
            for tool_name in gap.get("missing_tools") or []:
                if tool_name:
                    actions.append(
                        self._action_spec(
                            action_type="request_outsourcing",
                            title=f"Outsource complex capability design: {tool_name}",
                            summary=(
                                "The requested capability needs implementation or "
                                "provider work beyond safe autonomous hot-loading."
                            ),
                            risk_level="medium",
                            confidence=0.82,
                            impact={"financial_usd": 0, "recipients": 0},
                            source_type="role_gap",
                            source_id=gap.get("gap_id"),
                            target_type="tool",
                            target_id=tool_name,
                            payload={
                                "tool_name": tool_name,
                                "role_gap": gap,
                                "complexity_reason": (
                                    "Missing or unready executor requires external "
                                    "code/tool work under FOSS and security constraints."
                                ),
                            },
                        )
                    )
            for readiness in gap.get("configuration_required_tools") or []:
                if not isinstance(readiness, dict):
                    continue
                tool_name = readiness.get("name")
                if tool_name:
                    actions.append(
                        self._action_spec(
                            action_type="request_provider_configuration",
                            title=f"Configure provider or credentials for {tool_name}",
                            summary=(
                                readiness.get("readiness_reason")
                                or "The requested capability has a registered executor "
                                "but needs provider credentials or configuration."
                            ),
                            risk_level="low",
                            confidence=0.9,
                            impact={"financial_usd": 0, "recipients": 0},
                            source_type="role_gap",
                            source_id=gap.get("gap_id"),
                            target_type="tool",
                            target_id=tool_name,
                            payload={
                                "tool_name": tool_name,
                                "role_gap": gap,
                                "readiness": readiness,
                                "required_configuration": True,
                            },
                        )
                    )
        readiness = snapshot.get("readiness_evidence") or {}
        alerts = readiness.get("alerts") or {}
        if alerts.get("stale") or alerts.get("blocking"):
            actions.append(
                self._action_spec(
                    action_type="request_owner_approval",
                    title="Refresh alert email proof",
                    summary="Critical notification proof is stale or missing.",
                    risk_level="medium",
                    confidence=0.8,
                    impact={"financial_usd": 0, "recipients": 1},
                    source_type="readiness",
                    source_id="alerts",
                    payload={"target_view": "operations", "send_email": True},
                )
            )
        if not actions:
            actions.append(
                self._action_spec(
                    action_type="observe_only",
                    title="Record executive operating brief",
                    summary="No intervention is required beyond recording the operating state.",
                    risk_level="low",
                    confidence=0.95,
                    impact={"financial_usd": 0, "recipients": 0},
                    source_type="executive_snapshot",
                    source_id=snapshot.get("generated_at"),
                    payload={},
                )
            )
        return actions

    async def _execute_action(
        self,
        *,
        run_id: str,
        run_node_id: str,
        actor: str,
        action: dict[str, Any],
        policy: dict[str, Any],
        dry_run: bool,
        auto_apply_low_risk: bool,
        paused: bool,
        observer_blocks: bool,
    ) -> dict[str, Any]:
        gate = self._impact_gate(action, policy, observer_blocks=observer_blocks)
        idempotency_key = f"exec:{run_id}:{action['idempotency_key']}"
        existing = await self._execution_by_key(idempotency_key)
        if existing:
            return {**existing, "duplicate": True}
        status = "planned" if dry_run else "completed"
        result: dict[str, Any] = {
            "dry_run": dry_run,
            "gate": gate,
            "external_side_effects": False,
        }
        approval_id = None
        completed_at = None
        if paused:
            status = "blocked"
            result["blocked_reason"] = "executive_autonomy_paused"
        elif gate["blocked"]:
            if gate["requires_approval"]:
                approval = await self._request_action_approval(
                    run_id=run_id,
                    actor=actor,
                    action=action,
                    gate=gate,
                )
                approval_id = approval.id
                status = "approval_required"
                result["approval_id"] = approval_id
            else:
                status = "blocked"
                result["blocked_reason"] = gate["reason"]
        elif action["action_type"] == "request_outsourcing":
            if dry_run:
                status = "planned"
            else:
                request = await self._create_outsourcing_from_action(
                    run_id=run_id,
                    actor=actor,
                    action=action,
                )
                status = "outsourcing_required"
                result["outsourcing_request_id"] = request["id"]
        elif action["action_type"] == "request_provider_configuration":
            status = "planned" if dry_run else "owner_action_required"
            result.update(
                {
                    "action": "provider_configuration_required",
                    "tool_name": action.get("target_id"),
                    "readiness": (action.get("payload") or {}).get("readiness") or {},
                    "owner_next_step": (
                        "Configure the required provider credentials or explicitly "
                        "defer this optional capability."
                    ),
                }
            )
        elif action["action_type"] == "seed_memory" and auto_apply_low_risk:
            if dry_run:
                status = "planned"
                result["action"] = "owner_instruction_memory_seed_planned"
            else:
                memory_result = await self._seed_owner_instruction_memory(
                    run_id=run_id,
                    actor=actor,
                    action=action,
                )
                if memory_result["status"] == "completed":
                    status = "completed"
                    completed_at = utc_now()
                else:
                    status = "blocked"
                result.update(memory_result)
        elif action["risk_level"] == "low" and auto_apply_low_risk:
            status = "completed" if not dry_run else "planned"
            completed_at = utc_now() if not dry_run else None
            result["action"] = "safe_internal_recorded"
        else:
            approval = await self._request_action_approval(
                run_id=run_id,
                actor=actor,
                action=action,
                gate={"reason": "non_low_risk_action", "blockers": ["owner_gate"]},
            )
            approval_id = approval.id
            status = "approval_required"
            result["approval_id"] = approval_id
        node = await self._upsert_graph_node(
            node_type="autonomous_execution",
            title=action["title"],
            summary=action["summary"],
            source_type=action.get("source_type"),
            source_id=action.get("source_id"),
            agent_id="chief_operating_agent",
            tool_name=action.get("target_id") if action.get("target_type") == "tool" else None,
            risk_level=action["risk_level"],
            confidence=action["confidence"],
            impact_score=self._impact_score(action),
            tags=["execution", action["action_type"], status],
            metadata={"action": action, "result": result},
            idempotency_key=f"operation_graph:execution:{idempotency_key}",
        )
        await self._create_graph_edge(run_node_id, node["id"], "executed_or_blocked")
        async with async_session() as session:
            record = AutonomousExecutionRecord(
                id=f"exec_{uuid.uuid4().hex[:16]}",
                run_id=run_id,
                action_type=action["action_type"],
                title=action["title"],
                status=status,
                risk_level=action["risk_level"],
                confidence=action["confidence"],
                impact=action.get("impact") or {},
                approval_id=approval_id,
                operation_node_id=node["id"],
                result=result,
                error=None,
                idempotency_key=idempotency_key,
                completed_at=completed_at,
            )
            session.add(record)
            await session.commit()
            return self._execution_to_dict(record)

    async def _create_outsourcing_from_action(
        self,
        *,
        run_id: str,
        actor: str,
        action: dict[str, Any],
    ) -> dict[str, Any]:
        payload = action.get("payload") or {}
        tool_name = str(payload.get("tool_name") or action.get("target_id") or "tool")
        return await self.create_outsourcing_request(
            actor=actor,
            data={
                "title": action["title"],
                "complexity_reason": payload.get("complexity_reason") or action["summary"],
                "task_spec": {
                    "tool_or_skill": tool_name,
                    "purpose": action["summary"],
                    "required_inputs": ["owner-reviewed requirements"],
                    "required_outputs": ["implementation plan", "tests", "rollback notes"],
                    "activation_policy": "code_review_ci_deploy_required",
                },
                "context_pack": {
                    "run_id": run_id,
                    "action": action,
                    "source": {
                        "type": action.get("source_type"),
                        "id": action.get("source_id"),
                    },
                },
                "acceptance_tests": [
                    "FOSS-only policy evidence is declared",
                    "No live hot-loading or external mutation occurs without approval",
                    "Readiness reports configuration_required until credentials exist",
                    "Unit tests cover approval, validation, and failure paths",
                ],
                "foss_constraints": self._foss_constraints(),
                "security_constraints": self._security_constraints(),
                "files_involved": [
                    "backend/src/cyber_team/tools/registry.py",
                    "backend/src/cyber_team/operations/executive.py",
                    "frontend/src/components/OperationsView.tsx",
                ],
                "expected_artifact": (
                    "A reviewed patch or PR implementing the capability plus tests "
                    "and readiness checks."
                ),
                "replay_instructions": (
                    "A human may use standalone AI coding tools outside the runtime, "
                    "then submit a normal code change through CI and deployment."
                ),
                "source_type": action.get("source_type"),
                "source_id": action.get("source_id"),
            },
        )

    async def _request_action_approval(
        self,
        *,
        run_id: str,
        actor: str,
        action: dict[str, Any],
        gate: dict[str, Any],
    ) -> ApprovalRequest:
        target_id = action["idempotency_key"]
        async with async_session() as session:
            existing = (
                await session.execute(
                    select(ApprovalRequest)
                    .where(
                        ApprovalRequest.target_type == "executive_action",
                        ApprovalRequest.target_id == target_id,
                        ApprovalRequest.status.in_(["pending", "approved"]),
                        ApprovalRequest.consumed_at.is_(None),
                    )
                    .order_by(desc(ApprovalRequest.created_at))
                    .limit(1)
                )
            ).scalar_one_or_none()
            now = utc_now()
            if existing and (not existing.expires_at or existing.expires_at > now):
                return existing
            approval = ApprovalRequest(
                id=f"appr_{uuid.uuid4().hex[:16]}",
                agent_id="chief_operating_agent",
                action_type=f"executive:{action['action_type']}",
                action_description=action["summary"],
                action_payload={
                    "run_id": run_id,
                    "action": self._redact(action),
                    "gate": gate,
                    "replay_instruction": (
                        "Approve only from the owner console. Execution must verify "
                        "target_type=executive_action and target_id equals the action "
                        "idempotency key."
                    ),
                },
                requester=actor,
                requester_type="agent",
                risk_level=action["risk_level"],
                target_type="executive_action",
                target_id=target_id,
                status="pending",
                expires_at=now + timedelta(days=7),
            )
            session.add(approval)
            await session.commit()
            return approval

    async def _run_observer_review(
        self,
        *,
        run_id: str | None,
        snapshot: dict[str, Any],
        actions: list[dict[str, Any]],
        benchmark_results: list[dict[str, Any]],
        owner_instruction: str | None,
    ) -> dict[str, Any]:
        findings = []
        if owner_instruction:
            lowered = owner_instruction.lower()
            markers = [
                marker
                for marker in self.PROMPT_INJECTION_MARKERS
                if marker in lowered
            ]
            if markers:
                findings.append(
                    {
                        "severity": "high",
                        "type": "prompt_injection",
                        "detail": "Owner instruction contains policy-override markers.",
                        "markers": markers,
                    }
                )
        failed_benchmarks = [
            item for item in benchmark_results if item.get("status") == "failed"
        ]
        if failed_benchmarks:
            findings.append(
                {
                    "severity": "medium",
                    "type": "benchmark_failure",
                    "detail": "One or more executive benchmarks failed.",
                    "benchmark_keys": [
                        item.get("benchmark_key") for item in failed_benchmarks[:10]
                    ],
                }
            )
        low_confidence = [
            action
            for action in actions
            if action.get("confidence", 1.0) < settings.governor_min_confidence
        ]
        if low_confidence:
            findings.append(
                {
                    "severity": "medium",
                    "type": "low_confidence_action",
                    "detail": "Action confidence is below policy threshold.",
                    "actions": [item.get("title") for item in low_confidence[:10]],
                }
            )
        resource_policy = snapshot.get("resource_policy") or {}
        if resource_policy.get("blocking"):
            findings.append(
                {
                    "severity": "high",
                    "type": "resource_policy_violation",
                    "detail": "FOSS-only resource policy has blockers.",
                }
            )
        unresolved = [
            item
            for item in findings
            if item.get("severity") in {"high", "critical"}
        ]
        status = "agreed"
        critique = "Observer agrees with the proposed below-threshold internal actions."
        consensus_log = [
            {
                "speaker": "observer_agent",
                "message": "Evidence reviewed with read-only authority.",
            },
            {
                "speaker": "chief_operating_agent",
                "message": "External mutations remain gated by impact thresholds.",
            },
        ]
        if unresolved:
            status = "escalated"
            critique = (
                "Observer found unresolved high-severity objections; owner review is required."
            )
            consensus_log.append(
                {
                    "speaker": "observer_agent",
                    "message": "Consensus failed due to high-severity findings.",
                }
            )
        elif findings:
            status = "disagreed"
            critique = (
                "Observer found medium-severity issues; execution can continue only for "
                "safe internal actions with recorded caveats."
            )
            consensus_log.append(
                {
                    "speaker": "chief_operating_agent",
                    "message": "Medium findings are recorded and benchmark follow-up is created.",
                }
            )
        async with async_session() as session:
            review = ObserverReview(
                id=f"obs_{uuid.uuid4().hex[:16]}",
                run_id=run_id,
                status=status,
                critique=critique,
                findings=findings,
                consensus_log=consensus_log,
                unresolved_objections=unresolved,
                confidence=0.92 if status == "agreed" else 0.74,
                metadata_={
                    "actions_reviewed": len(actions),
                    "benchmark_results_reviewed": len(benchmark_results),
                    "side_effect_authority": "none",
                },
            )
            session.add(review)
            await session.commit()
            return self._observer_review_to_dict(review)

    async def _record_kpi_observations(
        self,
        run_id: str,
        snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        governor = snapshot.get("governor_snapshot") or {}
        evidence = snapshot.get("readiness_evidence") or {}
        readiness_blockers = sum(
            1
            for value in evidence.values()
            if isinstance(value, dict) and value.get("blocking")
        )
        memory = governor.get("memory") or {}
        role_backlog = governor.get("role_backlog") or {}
        workflows = governor.get("workflows") or {}
        tools = governor.get("tools") or {}
        values = {
            "readiness_blockers": float(readiness_blockers),
            "open_memory_findings": float(memory.get("open_findings") or 0),
            "active_role_gaps": float(role_backlog.get("active") or 0),
            "recent_workflow_failures": float(workflows.get("recent_failed") or 0),
            "side_effect_tool_blockers": float(
                len(tools.get("side_effects_not_live") or [])
            ),
        }
        await self._ensure_kpi_definitions(values.keys())
        observations = []
        async with async_session() as session:
            for key, value in values.items():
                item = OperatingKPIObservation(
                    id=f"kpiobs_{uuid.uuid4().hex[:16]}",
                    kpi_key=key,
                    value=value,
                    status="recorded",
                    source_type="executive_governor_run",
                    source_id=run_id,
                    metadata_={"snapshot_hash": self._stable_hash(snapshot)},
                )
                session.add(item)
                observations.append(item)
            await session.commit()
            return [self._kpi_observation_to_dict(item) for item in observations]

    async def _ensure_kpi_definitions(self, keys) -> None:
        labels = {
            "readiness_blockers": ("Readiness blockers", "count", "max", 0),
            "open_memory_findings": ("Open memory findings", "count", "max", 5),
            "active_role_gaps": ("Active role gaps", "count", "max", 10),
            "recent_workflow_failures": ("Recent workflow failures", "count", "max", 0),
            "side_effect_tool_blockers": ("Side-effect tool blockers", "count", "max", 0),
        }
        async with async_session() as session:
            existing = {
                row[0]
                for row in (
                    await session.execute(select(OperatingKPIDefinition.key))
                ).all()
            }
            for key in keys:
                if key in existing:
                    continue
                title, unit, comparison, target = labels.get(
                    key,
                    (str(key).replace("_", " ").title(), "count", "max", 0),
                )
                session.add(
                    OperatingKPIDefinition(
                        id=f"kpi_{uuid.uuid4().hex[:16]}",
                        key=key,
                        title=title,
                        unit=unit,
                        comparison=comparison,
                        target_value=float(target),
                        source="executive_snapshot",
                        status="active",
                        tags=["executive", "autonomy"],
                        metadata_={},
                    )
                )
            await session.commit()

    async def _record_benchmark_results(
        self,
        run_id: str,
        observations: list[dict[str, Any]],
        *,
        force_refresh: bool,
    ) -> list[dict[str, Any]]:
        observed = {item["kpi_key"]: item["value"] for item in observations}
        async with async_session() as session:
            result = await session.execute(
                select(ExecutiveBenchmarkDefinition)
                .where(ExecutiveBenchmarkDefinition.status == "active")
                .order_by(ExecutiveBenchmarkDefinition.key)
            )
            definitions = result.scalars().all()
            records = []
            for definition in definitions:
                rule = definition.rule or {}
                keys = definition.kpi_keys or []
                value = max([observed.get(key, 0.0) for key in keys] or [0.0])
                threshold = float(rule.get("threshold") or 0.0)
                comparison = rule.get("comparison") or "max"
                passed = value <= threshold if comparison == "max" else value >= threshold
                status = "passed" if passed else "failed"
                record = ExecutiveBenchmarkResult(
                    id=f"benchres_{uuid.uuid4().hex[:16]}",
                    benchmark_key=definition.key,
                    run_id=run_id,
                    status=status,
                    score=1.0 if passed else 0.0,
                    observed_value=float(value),
                    threshold_value=threshold,
                    evidence={
                        "kpi_keys": keys,
                        "comparison": comparison,
                        "force_refresh": force_refresh,
                        "detail": (
                            f"Observed {value} against {comparison} threshold {threshold}."
                        ),
                    },
                )
                session.add(record)
                records.append(record)
            await session.commit()
            return [self._benchmark_result_to_dict(item) for item in records]

    async def _record_reflection(
        self,
        *,
        run_id: str,
        snapshot: dict[str, Any],
        executions: list[dict[str, Any]],
        review: dict[str, Any] | None,
        benchmark_results: list[dict[str, Any]],
        force: bool,
    ) -> dict[str, Any]:
        failed_benchmarks = [
            item["benchmark_key"]
            for item in benchmark_results
            if item.get("status") == "failed"
        ]
        blocked = [
            item["title"]
            for item in executions
            if item.get("status") in {"blocked", "approval_required", "outsourcing_required"}
        ]
        summary = (
            "Executive cycle recorded operating state, benchmarks, Observer review, "
            "and safe internal actions."
        )
        if blocked:
            summary += f" Blocked or gated items: {len(blocked)}."
        if force:
            summary += " Reflection refresh was forced by owner or scheduler."
        async with async_session() as session:
            reflection = ExecutiveReflection(
                id=f"refl_{uuid.uuid4().hex[:16]}",
                run_id=run_id,
                summary=summary,
                what_changed=[
                    "Recorded executive operating snapshot",
                    "Updated KPI observations",
                    "Recorded Observer consensus state",
                ],
                repeated_patterns=blocked[:10],
                failures=failed_benchmarks,
                memory_gaps=[
                    "No operation graph history exists yet"
                    if not await self._has_operation_graph_history(session)
                    else "Operation graph history is available"
                ],
                next_watch_items=self._next_watch_items(snapshot, executions, review),
                metadata_={
                    "execution_count": len(executions),
                    "observer_status": review.get("status") if review else "not_run",
                    "force": force,
                },
            )
            session.add(reflection)
            await session.commit()
            return self._reflection_to_dict(reflection)

    async def _record_owner_instruction_context(
        self,
        *,
        run_node_id: str,
        actor: str,
        owner_instruction: str | None,
    ) -> dict[str, Any] | None:
        instruction = (owner_instruction or "").strip()
        if not instruction:
            return None
        sanitized = self._sanitize_instruction_text(instruction, limit=2000)
        lowered = instruction.lower()
        suspicious = [
            marker
            for marker in self.PROMPT_INJECTION_MARKERS
            if marker in lowered
        ]
        source_id = self._stable_hash({"instruction": sanitized})[:16]
        node = await self._upsert_graph_node(
            node_type="owner_instruction",
            title="Owner instruction",
            summary=sanitized[:8000],
            source_type="owner_instruction",
            source_id=source_id,
            agent_id=None,
            risk_level="high" if suspicious else "low",
            confidence=0.95 if not suspicious else 0.5,
            impact_score=0.0,
            tags=[
                "owner_instruction",
                "executive",
                "safety_review" if suspicious else "memory_seed",
            ],
            metadata={
                "actor": actor,
                "instruction_hash": source_id,
                "suspicious_markers": suspicious,
                "requires_review": bool(suspicious),
                "policy_version": self.POLICY_VERSION,
            },
            idempotency_key=f"operation_graph:owner_instruction:{source_id}",
        )
        await self._create_graph_edge(node["id"], run_node_id, "triggered_run")
        return node

    async def _has_operation_graph_history(self, session) -> bool:
        count = (
            await session.execute(select(func.count()).select_from(OperationGraphNode))
        ).scalar_one()
        return int(count or 0) > 0

    def _next_watch_items(
        self,
        snapshot: dict[str, Any],
        executions: list[dict[str, Any]],
        review: dict[str, Any] | None,
    ) -> list[str]:
        items = []
        if any(item.get("status") == "approval_required" for item in executions):
            items.append("Owner approvals are waiting for large-impact or gated actions")
        if any(item.get("status") == "outsourcing_required" for item in executions):
            items.append("Outsourcing backlog needs owner review or external execution")
        if review and review.get("unresolved_objections"):
            items.append("Observer objections must be resolved before execution")
        resource = snapshot.get("resource_policy") or {}
        if resource.get("blocking"):
            items.append("FOSS-only resource policy blockers remain open")
        return items or ["Continue monitoring objectives, KPIs, and benchmark drift"]

    async def _write_operation_memory(
        self,
        *,
        run_id: str,
        brief: str,
        counts: dict[str, Any],
        review: dict[str, Any] | None,
        reflection: dict[str, Any],
    ) -> None:
        if not self._memory or not settings.operation_graph_indexing_enabled:
            return
        content = (
            f"Executive governor run {run_id}: {brief}\n"
            f"Counts: {json.dumps(counts, sort_keys=True)}\n"
            f"Observer: {(review or {}).get('status', 'not_run')}\n"
            f"Reflection: {reflection.get('summary')}"
        )
        try:
            await self._memory.remember(
                SimpleNamespace(
                    agent_id="chief_operating_agent",
                    memory_type="procedural",
                    namespace="company:operation_graph",
                    content=content,
                    metadata={
                        "source_type": "executive_governor_run",
                        "source_id": run_id,
                        "policy_version": self.POLICY_VERSION,
                        "operation_graph_indexed": True,
                    },
                    importance=0.9,
                )
            )
        except Exception:
            # Memory indexing failure should be visible through readiness, not crash
            # the operating cycle after the durable DB graph has been written.
            return

    async def _seed_owner_instruction_memory(
        self,
        *,
        run_id: str,
        actor: str,
        action: dict[str, Any],
    ) -> dict[str, Any]:
        if not settings.operation_graph_indexing_enabled:
            return {
                "status": "blocked",
                "action": "owner_instruction_memory_seed_blocked",
                "blocked_reason": "operation_graph_indexing_disabled",
            }
        if not self._memory:
            return {
                "status": "blocked",
                "action": "owner_instruction_memory_seed_blocked",
                "blocked_reason": "memory_service_unavailable",
            }
        payload = action.get("payload") or {}
        instruction_summary = self._sanitize_instruction_text(
            str(payload.get("instruction_summary") or ""),
            limit=1000,
        )
        if not instruction_summary:
            return {
                "status": "blocked",
                "action": "owner_instruction_memory_seed_blocked",
                "blocked_reason": "empty_instruction_summary",
            }
        source_id = str(action.get("source_id") or "")[:64]
        try:
            memory = await self._memory.remember(
                SimpleNamespace(
                    agent_id="chief_operating_agent",
                    memory_type="procedural",
                    namespace="company:operation_graph",
                    content=(
                        "Owner instruction for the Chief Operating Agent: "
                        f"{instruction_summary}"
                    ),
                    metadata={
                        "source_type": "owner_instruction",
                        "source_id": source_id,
                        "run_id": run_id,
                        "actor": actor,
                        "policy_version": self.POLICY_VERSION,
                        "action_idempotency_key": action.get("idempotency_key"),
                        "operation_graph_indexed": True,
                    },
                    importance=0.88,
                )
            )
            return {
                "status": "completed",
                "action": "owner_instruction_memory_seeded",
                "memory_id": memory.get("id") if isinstance(memory, dict) else None,
                "source_id": source_id,
            }
        except Exception as exc:
            return {
                "status": "blocked",
                "action": "owner_instruction_memory_seed_blocked",
                "blocked_reason": "memory_write_failed",
                "error": str(exc),
                "source_id": source_id,
            }

    async def _upsert_graph_node(
        self,
        *,
        node_type: str,
        title: str,
        summary: str,
        source_type: str | None,
        source_id: str | None,
        agent_id: str | None = None,
        workflow_run_id: str | None = None,
        tool_name: str | None = None,
        risk_level: str = "low",
        confidence: float = 1.0,
        impact_score: float = 0.0,
        memory_namespace: str | None = "company:operation_graph",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = idempotency_key or self._stable_hash(
            {
                "node_type": node_type,
                "source_type": source_type,
                "source_id": source_id,
                "title": title,
            }
        )
        async with async_session() as session:
            existing = (
                await session.execute(
                    select(OperationGraphNode)
                    .where(OperationGraphNode.idempotency_key == key)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing:
                return self._graph_node_to_dict(existing)
            node = OperationGraphNode(
                id=f"opnode_{uuid.uuid4().hex[:16]}",
                node_type=node_type,
                title=title[:240],
                summary=summary[:8000],
                source_type=source_type,
                source_id=source_id,
                agent_id=agent_id,
                workflow_run_id=workflow_run_id,
                tool_name=tool_name,
                risk_level=risk_level,
                confidence=float(confidence),
                impact_score=float(impact_score),
                memory_namespace=memory_namespace,
                tags=tags or [],
                metadata_=self._redact(metadata or {}),
                idempotency_key=key,
            )
            session.add(node)
            await session.commit()
            return self._graph_node_to_dict(node)

    async def _create_graph_edge(
        self,
        source_node_id: str,
        target_node_id: str,
        edge_type: str,
    ) -> dict[str, Any]:
        async with async_session() as session:
            edge = OperationGraphEdge(
                id=f"opedge_{uuid.uuid4().hex[:16]}",
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                edge_type=edge_type,
                metadata_={},
            )
            session.add(edge)
            await session.commit()
            return self._graph_edge_to_dict(edge)

    async def _execution_by_key(self, idempotency_key: str) -> dict[str, Any] | None:
        async with async_session() as session:
            existing = (
                await session.execute(
                    select(AutonomousExecutionRecord)
                    .where(AutonomousExecutionRecord.idempotency_key == idempotency_key)
                    .limit(1)
                )
            ).scalar_one_or_none()
            return self._execution_to_dict(existing) if existing else None

    def _impact_gate(
        self,
        action: dict[str, Any],
        policy: dict[str, Any],
        *,
        observer_blocks: bool,
    ) -> dict[str, Any]:
        thresholds = policy.get("thresholds") or self._default_thresholds()
        impact = action.get("impact") or {}
        blockers = []
        if float(impact.get("financial_usd") or 0) > float(
            thresholds["financial_action_limit_usd"]
        ):
            blockers.append("financial_action_limit_exceeded")
        if float(impact.get("financial_daily_usd") or 0) > float(
            thresholds["financial_daily_limit_usd"]
        ):
            blockers.append("financial_daily_limit_exceeded")
        if int(impact.get("recipients") or 0) > int(
            thresholds["bulk_recipient_daily_limit"]
        ):
            blockers.append("bulk_recipient_limit_exceeded")
        if float(action.get("confidence") or 0) < float(thresholds["min_confidence"]):
            blockers.append("confidence_below_threshold")
        if impact.get("irreversible_without_backup"):
            blockers.append("fresh_backup_required")
        if action.get("payload", {}).get("external_write"):
            blockers.append("external_write_requires_approval")
        if observer_blocks:
            blockers.append("observer_unresolved_objection")
        requires_approval = bool(blockers)
        return {
            "blocked": requires_approval,
            "requires_approval": requires_approval,
            "blockers": blockers,
            "reason": blockers[0] if blockers else "below_threshold",
            "thresholds": thresholds,
        }

    def _action_spec(
        self,
        *,
        action_type: str,
        title: str,
        summary: str,
        risk_level: str,
        confidence: float,
        impact: dict[str, Any],
        source_type: str | None,
        source_id: str | None,
        payload: dict[str, Any],
        target_type: str | None = None,
        target_id: str | None = None,
    ) -> dict[str, Any]:
        basis = {
            "action_type": action_type,
            "title": title,
            "source_type": source_type,
            "source_id": source_id,
            "target_type": target_type,
            "target_id": target_id,
            "payload": payload,
        }
        return {
            "action_type": action_type,
            "title": title,
            "summary": summary,
            "risk_level": risk_level,
            "confidence": confidence,
            "impact": impact,
            "source_type": source_type,
            "source_id": source_id,
            "target_type": target_type,
            "target_id": target_id,
            "payload": payload,
            "idempotency_key": "executive:"
            + hashlib.sha256(
                json.dumps(basis, sort_keys=True, default=str).encode()
            ).hexdigest()[:40],
        }

    async def _objective_summary(self) -> dict[str, Any]:
        objectives = await self.list_objectives()
        active = [
            item for item in objectives["items"] if item.get("status") == "active"
        ]
        return {
            "active_count": len(active),
            "high_priority_count": sum(
                1 for item in active if item.get("priority") == "high"
            ),
            "titles": [item["title"] for item in active[:5]],
        }

    @staticmethod
    def _kpi_summary(observations: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "count": len(observations),
            "values": {
                item["kpi_key"]: item["value"]
                for item in observations
            },
        }

    @staticmethod
    def _benchmark_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
        failed = [item for item in results if item.get("status") == "failed"]
        return {
            "count": len(results),
            "failed": len(failed),
            "status": "ready" if not failed else "degraded",
            "failed_keys": [item["benchmark_key"] for item in failed[:10]],
        }

    @staticmethod
    def _consensus_state(review: dict[str, Any] | None) -> dict[str, Any]:
        if not review:
            return {"status": "not_required", "unresolved": False}
        return {
            "status": review["status"],
            "unresolved": bool(review.get("unresolved_objections")),
            "unresolved_count": len(review.get("unresolved_objections") or []),
        }

    async def _reflection_freshness(self) -> dict[str, Any]:
        async with async_session() as session:
            reflection = (
                await session.execute(
                    select(ExecutiveReflection)
                    .order_by(desc(ExecutiveReflection.created_at))
                    .limit(1)
                )
            ).scalar_one_or_none()
        if not reflection:
            return {"status": "waiting", "stale": True, "latest_at": None}
        age = utc_now() - reflection.created_at
        stale = age > timedelta(days=7)
        return {
            "status": "stale" if stale else "ready",
            "stale": stale,
            "latest_at": reflection.created_at.isoformat(),
            "age_hours": round(age.total_seconds() / 3600, 2),
        }

    async def _record_run_audit(
        self,
        *,
        actor: str,
        run_id: str,
        status: str,
        counts: dict[str, Any],
        errors: list[dict[str, Any]],
        dry_run: bool,
    ) -> None:
        await self._record_audit(
            event_type="executive_governor.run",
            actor=actor,
            resource_id=run_id,
            outcome=status,
            metadata={
                "policy_version": self.POLICY_VERSION,
                "dry_run": dry_run,
                "counts": counts,
                "errors": errors,
            },
        )
        if self._audit and hasattr(self._audit, "record_control_evidence"):
            await self._audit.record_control_evidence(
                control_id="autonomy.executive_governor_run",
                control_area="autonomous_operations",
                actor=actor,
                outcome=status,
                evidence={
                    "run_id": run_id,
                    "policy_version": self.POLICY_VERSION,
                    "counts": counts,
                    "resource_policy": settings.autonomy_resource_policy,
                    "observer_required": settings.observer_review_required,
                },
            )

    async def _record_audit(
        self,
        *,
        event_type: str,
        actor: str,
        resource_id: str,
        metadata: dict[str, Any],
        outcome: str = "success",
    ) -> None:
        if not self._audit:
            return
        await self._audit.record(
            event_type=event_type,
            actor=actor,
            actor_type="agent" if actor.endswith("_agent") else "owner",
            resource_type="executive_company_os",
            resource_id=resource_id,
            action="run",
            outcome=outcome,
            metadata=self._redact(metadata),
        )

    def _proposal_resource_view(
        self,
        proposal: OrchestrationToolProposal,
    ) -> dict[str, Any]:
        payload = proposal.sandbox_result or {}
        metadata = payload.get("resource_policy") or payload.get("metadata") or {}
        license_name = str(metadata.get("license") or payload.get("license") or "").lower()
        cost_model = str(
            metadata.get("cost_model") or payload.get("cost_model") or ""
        ).lower()
        hosted_required = bool(
            metadata.get("hosted_service_required")
            or payload.get("hosted_service_required")
        )
        data_sharing_risk = bool(
            metadata.get("data_sharing_risk") or payload.get("data_sharing_risk")
        )
        license_known = bool(license_name)
        license_allowed = any(
            marker in license_name for marker in self.ALLOWED_LICENSE_MARKERS
        )
        paid = any(marker in cost_model for marker in self.PAID_COST_MARKERS)
        return {
            "id": proposal.id,
            "title": proposal.title,
            "status": proposal.status,
            "capability": proposal.capability,
            "resource_analysis": {
                "license": license_name or None,
                "license_unknown": not license_known,
                "license_allowed": license_allowed if license_known else None,
                "cost_model": cost_model or None,
                "paid_or_saas_only": paid or hosted_required,
                "hosted_service_required": hosted_required,
                "data_sharing_risk": data_sharing_risk,
            },
        }

    def _operating_brief(
        self,
        snapshot: dict[str, Any],
        actions: list[dict[str, Any]],
        policy: dict[str, Any],
    ) -> str:
        governor = snapshot.get("governor_snapshot") or {}
        memory = governor.get("memory") or {}
        role_backlog = governor.get("role_backlog") or {}
        return (
            "Executive Company OS assessed "
            f"{len(actions)} action(s). Autonomy mode={policy['mode']}, "
            f"paused={policy['paused']}, resource_policy={policy['resource_policy']}. "
            f"Open memory findings={memory.get('open_findings', 0)}, "
            f"active role gaps={role_backlog.get('active', 0)}."
        )

    @staticmethod
    def _run_counts(
        executions: list[dict[str, Any]],
        *,
        benchmark_results: list[dict[str, Any]],
        review: dict[str, Any] | None,
        errors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        by_status: dict[str, int] = {}
        by_action: dict[str, int] = {}
        for item in executions:
            by_status[item["status"]] = by_status.get(item["status"], 0) + 1
            by_action[item["action_type"]] = by_action.get(item["action_type"], 0) + 1
        return {
            "executions": len(executions),
            "by_status": by_status,
            "by_action": by_action,
            "benchmark_failed": sum(
                1 for item in benchmark_results if item.get("status") == "failed"
            ),
            "observer_status": review.get("status") if review else "not_run",
            "errors": len(errors),
        }

    def _default_thresholds(self) -> dict[str, Any]:
        return {
            "financial_action_limit_usd": settings.governor_financial_action_limit_usd,
            "financial_daily_limit_usd": settings.governor_financial_daily_limit_usd,
            "bulk_recipient_daily_limit": settings.governor_bulk_recipient_daily_limit,
            "min_confidence": settings.governor_min_confidence,
            "observer_unresolved_objection_blocks": True,
            "irreversible_mutation_requires_fresh_backup": True,
        }

    @staticmethod
    def _default_policy_body() -> dict[str, Any]:
        return {
            "large_impact_requires_owner": True,
            "external_writes_require_matching_approval": True,
            "generated_code_hot_loading": False,
            "observer_can_block": True,
            "outsourcing_for_complexity": True,
        }

    def _sanitize_thresholds(self, thresholds: dict[str, Any]) -> dict[str, Any]:
        defaults = self._default_thresholds()
        sanitized = dict(defaults)
        for key in defaults:
            if key in thresholds:
                sanitized[key] = thresholds[key]
        return sanitized

    def _sanitize_instruction_text(self, text: str, *, limit: int = 1000) -> str:
        sanitized = self.BEARER_PATTERN.sub("Bearer [redacted]", text)
        sanitized = self.SECRET_ASSIGNMENT_PATTERN.sub(r"\1\2[redacted]", sanitized)
        return sanitized.strip()[:limit]

    @staticmethod
    def _foss_constraints() -> list[str]:
        return [
            "Use only free and open-source licenses by default.",
            "Avoid paid SaaS dependencies until company revenue permits.",
            "Prefer self-hosted services and local libraries.",
            "Declare license, cost model, hosted dependency, and data-sharing risk.",
        ]

    @staticmethod
    def _security_constraints() -> list[str]:
        return [
            "Do not include secret values in context packs.",
            "No external mutation without exact owner approval.",
            "Generated code must pass review, CI, deployment, and readiness validation.",
            "Do not bypass audit, memory trace, or approval policies.",
        ]

    @staticmethod
    def _impact_score(action: dict[str, Any]) -> float:
        impact = action.get("impact") or {}
        financial = float(impact.get("financial_usd") or 0)
        recipients = float(impact.get("recipients") or 0)
        irreversible = 100.0 if impact.get("irreversible_without_backup") else 0.0
        return financial + recipients * 10.0 + irreversible

    @staticmethod
    def _stable_hash(value: Any) -> str:
        payload = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def _redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            redacted = {}
            for key, item in value.items():
                lowered = str(key).lower()
                if any(marker in lowered for marker in self.SECRET_MARKERS):
                    redacted[key] = "[redacted]"
                else:
                    redacted[key] = self._redact(item)
            return redacted
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        return value

    @staticmethod
    def _agent_to_dict(agent: Agent) -> dict[str, Any]:
        return {
            "id": agent.id,
            "role_family": agent.role_family,
            "role_name": agent.role_name,
            "tools": agent.tools,
            "memory_namespace": agent.memory_namespace,
            "approval_policy": agent.approval_policy,
            "status": agent.status,
            "config": agent.config,
            "created_at": agent.created_at.isoformat(),
            "updated_at": agent.updated_at.isoformat(),
        }

    @staticmethod
    def _policy_to_dict(policy: AutonomyPolicy) -> dict[str, Any]:
        return {
            "id": policy.id,
            "mode": policy.mode,
            "resource_policy": policy.resource_policy,
            "paused": policy.paused,
            "thresholds": policy.thresholds,
            "policy": policy.policy,
            "updated_by": policy.updated_by,
            "created_at": policy.created_at.isoformat(),
            "updated_at": policy.updated_at.isoformat(),
        }

    @staticmethod
    def _objective_to_dict(item: CompanyObjective) -> dict[str, Any]:
        return {
            "id": item.id,
            "title": item.title,
            "description": item.description,
            "status": item.status,
            "priority": item.priority,
            "target": item.target,
            "tags": item.tags,
            "created_by": item.created_by,
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
        }

    @staticmethod
    def _benchmark_to_dict(item: ExecutiveBenchmarkDefinition) -> dict[str, Any]:
        return {
            "id": item.id,
            "key": item.key,
            "title": item.title,
            "description": item.description,
            "kpi_keys": item.kpi_keys,
            "rule": item.rule,
            "severity": item.severity,
            "status": item.status,
            "created_by": item.created_by,
            "metadata": item.metadata_,
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
        }

    @staticmethod
    def _benchmark_result_to_dict(item: ExecutiveBenchmarkResult) -> dict[str, Any]:
        detail = (item.evidence or {}).get("detail") or ""
        return {
            "id": item.id,
            "benchmark_key": item.benchmark_key,
            "run_id": item.run_id,
            "status": item.status,
            "score": item.score,
            "observed_value": item.observed_value,
            "threshold_value": item.threshold_value,
            "evidence": item.evidence,
            "detail": detail,
            "created_at": item.created_at.isoformat(),
        }

    @staticmethod
    def _kpi_observation_to_dict(item: OperatingKPIObservation) -> dict[str, Any]:
        return {
            "id": item.id,
            "kpi_key": item.kpi_key,
            "value": item.value,
            "status": item.status,
            "source_type": item.source_type,
            "source_id": item.source_id,
            "metadata": item.metadata_,
            "observed_at": item.observed_at.isoformat(),
        }

    @staticmethod
    def _graph_node_to_dict(item: OperationGraphNode) -> dict[str, Any]:
        return {
            "id": item.id,
            "node_type": item.node_type,
            "title": item.title,
            "summary": item.summary,
            "source_type": item.source_type,
            "source_id": item.source_id,
            "agent_id": item.agent_id,
            "workflow_run_id": item.workflow_run_id,
            "tool_name": item.tool_name,
            "risk_level": item.risk_level,
            "confidence": item.confidence,
            "impact_score": item.impact_score,
            "memory_namespace": item.memory_namespace,
            "tags": item.tags,
            "metadata": item.metadata_,
            "idempotency_key": item.idempotency_key,
            "created_at": item.created_at.isoformat(),
        }

    @staticmethod
    def _graph_edge_to_dict(item: OperationGraphEdge) -> dict[str, Any]:
        return {
            "id": item.id,
            "source_node_id": item.source_node_id,
            "target_node_id": item.target_node_id,
            "edge_type": item.edge_type,
            "metadata": item.metadata_,
            "created_at": item.created_at.isoformat(),
        }

    @staticmethod
    def _reflection_to_dict(item: ExecutiveReflection) -> dict[str, Any]:
        return {
            "id": item.id,
            "run_id": item.run_id,
            "summary": item.summary,
            "what_changed": item.what_changed,
            "repeated_patterns": item.repeated_patterns,
            "failures": item.failures,
            "memory_gaps": item.memory_gaps,
            "next_watch_items": item.next_watch_items,
            "metadata": item.metadata_,
            "created_at": item.created_at.isoformat(),
        }

    @staticmethod
    def _observer_review_to_dict(item: ObserverReview) -> dict[str, Any]:
        return {
            "id": item.id,
            "run_id": item.run_id,
            "status": item.status,
            "critique": item.critique,
            "findings": item.findings,
            "consensus_log": item.consensus_log,
            "unresolved_objections": item.unresolved_objections,
            "confidence": item.confidence,
            "metadata": item.metadata_,
            "created_at": item.created_at.isoformat(),
        }

    @staticmethod
    def _execution_to_dict(item: AutonomousExecutionRecord) -> dict[str, Any]:
        return {
            "id": item.id,
            "run_id": item.run_id,
            "action_type": item.action_type,
            "title": item.title,
            "status": item.status,
            "risk_level": item.risk_level,
            "confidence": item.confidence,
            "impact": item.impact,
            "approval_id": item.approval_id,
            "operation_node_id": item.operation_node_id,
            "result": item.result,
            "error": item.error,
            "idempotency_key": item.idempotency_key,
            "created_at": item.created_at.isoformat(),
            "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        }

    @staticmethod
    def _outsourcing_to_dict(item: OutsourcingRequest) -> dict[str, Any]:
        return {
            "id": item.id,
            "title": item.title,
            "status": item.status,
            "complexity_reason": item.complexity_reason,
            "task_spec": item.task_spec,
            "context_pack": item.context_pack,
            "acceptance_tests": item.acceptance_tests,
            "foss_constraints": item.foss_constraints,
            "security_constraints": item.security_constraints,
            "files_involved": item.files_involved,
            "expected_artifact": item.expected_artifact,
            "replay_instructions": item.replay_instructions,
            "source_type": item.source_type,
            "source_id": item.source_id,
            "approval_id": item.approval_id,
            "created_by": item.created_by,
            "resolution": item.resolution,
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
            "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
        }

    @staticmethod
    def _run_to_dict(run: OrchestrationGovernorRun) -> dict[str, Any]:
        return {
            "run_id": run.id,
            "status": run.status,
            "actor": run.actor,
            "policy_version": run.policy_version,
            "mode": run.mode,
            "auto_apply_low_risk": run.auto_apply_low_risk,
            "max_actions": run.max_actions,
            "snapshot_hash": run.snapshot_hash,
            "operating_snapshot": run.operating_snapshot,
            "operating_brief": run.operating_brief,
            "counts": run.counts,
            "errors": run.errors,
            "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        }
