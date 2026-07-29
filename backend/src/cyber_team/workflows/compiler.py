"""Validation and activation of immutable declarative workflow specifications."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import desc, select
from temporalio.client import Client

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import (
    Agent,
    ObserverReview,
    Workflow,
    WorkflowRun,
    WorkflowSpecification,
)


class WorkflowTrigger(BaseModel):
    type: Literal["manual", "event", "schedule"] = "manual"
    config: dict[str, Any] = Field(default_factory=dict)


class WorkflowRetry(BaseModel):
    max_attempts: int = Field(default=3, ge=1, le=10)
    initial_interval_seconds: int = Field(default=2, ge=1, le=300)
    backoff_coefficient: float = Field(default=2.0, ge=1.0, le=10.0)
    maximum_interval_seconds: int = Field(default=60, ge=1, le=3600)


class WorkflowStep(BaseModel):
    id: str = Field(..., pattern=r"^[a-z][a-z0-9_]{0,79}$")
    type: Literal["agent", "tool", "memory", "decision", "approval"]
    agent_id: str | None = Field(default=None, max_length=64)
    tool_name: str | None = Field(default=None, max_length=100)
    task_template: str = Field(default="", max_length=12_000)
    args_template: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, max_length=50)
    timeout_seconds: int = Field(default=300, ge=5, le=3600)
    retry: WorkflowRetry = Field(default_factory=WorkflowRetry)
    compensation_step_id: str | None = Field(default=None, max_length=80)
    risk_level: Literal["low", "medium", "high", "critical"] = "low"
    action_envelope: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_required_step_fields(self):
        if self.type == "agent" and not self.agent_id:
            raise ValueError("agent steps require agent_id")
        if self.type == "tool" and not self.tool_name:
            raise ValueError("tool steps require tool_name")
        if self.type == "approval" and not self.action_envelope:
            raise ValueError("approval steps require action_envelope")
        return self


class DeclarativeWorkflowSpec(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    trigger: WorkflowTrigger = Field(default_factory=WorkflowTrigger)
    preconditions: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    agents: list[str] = Field(default_factory=list, max_length=50)
    tools: list[str] = Field(default_factory=list, max_length=100)
    steps: list[WorkflowStep] = Field(..., min_length=1, max_length=200)
    acceptance_tests: list[dict[str, Any]] = Field(..., min_length=1, max_length=100)
    metrics: list[str] = Field(default_factory=list, max_length=100)
    approval_policy: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_references(self):
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("workflow step ids must be unique")
        known = set(ids)
        for step in self.steps:
            missing = sorted(set(step.depends_on) - known)
            if missing:
                raise ValueError(
                    f"step {step.id} has unknown dependencies: {', '.join(missing)}"
                )
            if step.id in step.depends_on:
                raise ValueError(f"step {step.id} cannot depend on itself")
            if step.compensation_step_id and step.compensation_step_id not in known:
                raise ValueError(
                    f"step {step.id} has unknown compensation step "
                    f"{step.compensation_step_id}"
                )
            if step.agent_id and step.agent_id not in self.agents:
                raise ValueError(f"step {step.id} agent is absent from agents allowlist")
            if step.tool_name and step.tool_name not in self.tools:
                raise ValueError(f"step {step.id} tool is absent from tools allowlist")
        return self


class WorkflowCompilerService:
    """Compile generated workflow descriptions into governed runtime artifacts."""

    def __init__(self, *, tool_registry, action_policy_service, audit_service=None):
        self._tools = tool_registry
        self._policy = action_policy_service
        self._audit = audit_service

    async def run(
        self,
        specification_id: str,
        *,
        input_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with async_session() as session:
            item = await session.get(WorkflowSpecification, specification_id)
            if not item:
                raise ValueError("Workflow specification not found")
            if item.status != "active":
                raise ValueError(
                    f"Workflow specification is not active: {item.status}"
                )
            workflow_id = (item.sandbox_result or {}).get("workflow_id")
            workflow_projection = await session.get(Workflow, workflow_id)
            if not workflow_projection or workflow_projection.status != "active":
                raise ValueError("Active workflow projection is unavailable")
            run = WorkflowRun(
                id=str(uuid.uuid4()),
                workflow_id=workflow_id,
                status="running",
                current_node=None,
                state=input_data or {},
            )
            session.add(run)
            await session.commit()
            result = {
                "id": run.id,
                "workflow_id": workflow_id,
                "workflow_specification_id": item.id,
                "status": run.status,
                "started_at": run.started_at.isoformat(),
            }
        try:
            client = await Client.connect(
                settings.temporal_url,
                namespace=settings.temporal_namespace,
            )
            await client.start_workflow(
                "GenericSpecificationWorkflow",
                args=[item.specification, input_data or {}, run.id],
                id=run.id,
                task_queue="cyberteam-tasks",
            )
        except Exception as exc:
            async with async_session() as session:
                failed = await session.get(WorkflowRun, run.id)
                failed.status = "failed"
                failed.error = type(exc).__name__
                failed.completed_at = utc_now()
                await session.commit()
            raise
        if self._audit:
            await self._audit.record(
                event_type="workflow_specification.run_started",
                actor="workflow_compiler",
                actor_type="system",
                resource_type="workflow_run",
                resource_id=run.id,
                action="run",
                outcome="success",
                metadata={"workflow_specification_id": item.id},
            )
        return result

    async def propose(
        self,
        *,
        spec_key: str,
        title: str,
        specification: dict[str, Any],
        source_type: str,
        source_id: str | None = None,
        created_by: str = "workflow_compiler",
        activate_if_safe: bool = True,
    ) -> dict[str, Any]:
        parsed = DeclarativeWorkflowSpec.model_validate(specification)
        normalized = parsed.model_dump(mode="json")
        content_hash = self._hash(normalized)
        async with async_session() as session:
            duplicate = (
                await session.execute(
                    select(WorkflowSpecification).where(
                        WorkflowSpecification.content_hash == content_hash
                    )
                )
            ).scalar_one_or_none()
            if duplicate:
                return {**self._to_dict(duplicate), "duplicate": True}

        validation = await self.validate(normalized)
        observer = await self._observer_review(
            spec_key=spec_key,
            content_hash=content_hash,
            validation=validation,
        )
        safe_to_activate = bool(
            activate_if_safe
            and validation["valid"]
            and observer["status"] == "agreed"
            and not validation["approval_required"]
        )
        async with async_session() as session:
            latest_version = int(
                (
                    await session.execute(
                        select(WorkflowSpecification.version)
                        .where(WorkflowSpecification.spec_key == spec_key)
                        .order_by(desc(WorkflowSpecification.version))
                        .limit(1)
                    )
                ).scalar_one_or_none()
                or 0
            )
            workflow_id = f"specwf_{content_hash[:32]}"
            if safe_to_activate and not await session.get(Workflow, workflow_id):
                session.add(
                    Workflow(
                        id=workflow_id,
                        name=title[:200],
                        description=(
                            "Immutable declarative workflow specification "
                            f"{spec_key} version {latest_version + 1}."
                        ),
                        graph_definition={
                            "specification": normalized,
                            "specification_hash": content_hash,
                            "workflow_specification_id": None,
                        },
                        status="active",
                        trigger_type=parsed.trigger.type,
                        trigger_config=parsed.trigger.config,
                    )
                )
            item = WorkflowSpecification(
                id=f"wfspec_{uuid.uuid4().hex}",
                spec_key=spec_key[:160],
                version=latest_version + 1,
                status=(
                    "active"
                    if safe_to_activate
                    else "approval_required"
                    if validation["approval_required"]
                    else "blocked"
                    if not validation["valid"]
                    else "observer_review"
                ),
                title=title[:240],
                specification=normalized,
                content_hash=content_hash,
                risk_level=validation["risk_level"],
                source_type=source_type[:80],
                source_id=source_id[:200] if source_id else None,
                sandbox_result={
                    **validation,
                    "mode": "no_side_effect_validation",
                    "workflow_id": workflow_id if safe_to_activate else None,
                },
                observer_review_id=observer["id"],
                created_by=created_by[:200],
                activated_at=utc_now() if safe_to_activate else None,
            )
            session.add(item)
            await session.flush()
            if safe_to_activate:
                workflow = await session.get(Workflow, workflow_id)
                workflow.graph_definition = {
                    **workflow.graph_definition,
                    "workflow_specification_id": item.id,
                }
            await session.commit()
            result = self._to_dict(item)
        if self._audit:
            await self._audit.record_control_evidence(
                control_id="workflow_compiler.specification_validation",
                control_area="ai_governance",
                actor=created_by,
                outcome="success" if safe_to_activate else "review_required",
                evidence={
                    "specification_id": result["id"],
                    "content_hash": content_hash,
                    "status": result["status"],
                    "validation": validation,
                    "observer_review_id": observer["id"],
                },
            )
        return {**result, "duplicate": False}

    async def validate(self, specification: dict[str, Any]) -> dict[str, Any]:
        parsed = DeclarativeWorkflowSpec.model_validate(specification)
        errors: list[str] = []
        readiness: dict[str, Any] = {}
        policy_decisions: dict[str, Any] = {}
        async with async_session() as session:
            active_agents = {
                row[0]
                for row in (
                    await session.execute(
                        select(Agent.id).where(
                            Agent.id.in_(parsed.agents), Agent.status == "active"
                        )
                    )
                ).all()
            }
        missing_agents = sorted(set(parsed.agents) - active_agents)
        errors.extend(f"inactive_or_missing_agent:{item}" for item in missing_agents)
        for tool_name in parsed.tools:
            tool_readiness = self._tools.get_tool_readiness(tool_name)
            readiness[tool_name] = tool_readiness
            if not tool_readiness["executable"]:
                errors.append(f"tool_not_ready:{tool_name}:{tool_readiness['state']}")
        cycle = self._cycle_path(parsed.steps)
        if cycle:
            errors.append("dependency_cycle:" + "->".join(cycle))
        approval_required = False
        risk_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        risk_level = max(
            (step.risk_level for step in parsed.steps),
            key=lambda item: risk_rank[item],
        )
        for step in parsed.steps:
            if step.type != "tool":
                continue
            tool = self._tools.get_tool(step.tool_name)
            if not tool:
                continue
            envelope = self._step_envelope(step, tool)
            decision = await self._policy.evaluate(envelope)
            policy_decisions[step.id] = decision
            approval_required = approval_required or decision["requires_approval"]
            if not decision["allowed"] and not decision["requires_approval"]:
                errors.append(f"policy_denied:{step.id}:{','.join(decision['reasons'])}")
        return {
            "valid": not errors,
            "errors": errors,
            "risk_level": risk_level,
            "tool_readiness": readiness,
            "policy_decisions": policy_decisions,
            "approval_required": approval_required,
            "cycle": cycle,
            "acceptance_test_count": len(parsed.acceptance_tests),
            "step_count": len(parsed.steps),
        }

    async def list_specifications(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        async with async_session() as session:
            query = select(WorkflowSpecification)
            if status:
                query = query.where(WorkflowSpecification.status == status)
            items = (
                await session.execute(
                    query.order_by(desc(WorkflowSpecification.created_at)).limit(
                        max(1, min(limit, 500))
                    )
                )
            ).scalars().all()
            return [self._to_dict(item) for item in items]

    async def get_specification(self, specification_id: str) -> dict[str, Any] | None:
        async with async_session() as session:
            item = await session.get(WorkflowSpecification, specification_id)
            return self._to_dict(item) if item else None

    async def _observer_review(
        self,
        *,
        spec_key: str,
        content_hash: str,
        validation: dict[str, Any],
    ) -> dict[str, Any]:
        agreed = bool(validation["valid"])
        review = ObserverReview(
            id=f"obsrev_{uuid.uuid4().hex}",
            run_id=None,
            status="agreed" if agreed else "disagreed",
            critique=(
                "Declarative workflow passed schema, dependency, readiness, and policy checks."
                if agreed
                else "Declarative workflow is blocked by validation findings."
            ),
            findings=[
                {"type": "workflow_validation", "detail": error}
                for error in validation["errors"]
            ],
            consensus_log=[
                {
                    "actor": "observer_agent",
                    "decision": "activate_or_gate" if agreed else "block",
                }
            ],
            unresolved_objections=[] if agreed else list(validation["errors"]),
            confidence=1.0 if agreed else 0.0,
            metadata_={
                "review_type": "workflow_specification",
                "spec_key": spec_key,
                "content_hash": content_hash,
            },
        )
        async with async_session() as session:
            session.add(review)
            await session.commit()
        return {"id": review.id, "status": review.status}

    @staticmethod
    def _step_envelope(step: WorkflowStep, tool) -> dict[str, Any]:
        supplied = dict(step.action_envelope)
        return {
            "action_class": supplied.get("action_class") or tool.category,
            "actor": supplied.get("actor") or step.agent_id or "workflow",
            "actor_type": "agent",
            "target_type": "tool",
            "target_id": tool.name,
            "expected_effect": supplied.get("expected_effect")
            or f"Execute workflow tool {tool.name}",
            "evidence_ids": supplied.get("evidence_ids", []),
            "confidence": supplied.get("confidence", 1.0),
            "reversible": supplied.get("reversible", not tool.side_effects),
            "financial_exposure_usd": supplied.get("financial_exposure_usd", 0),
            "financial_daily_usd": supplied.get("financial_daily_usd", 0),
            "recipients": supplied.get("recipients", 0),
            "data_sensitivity": supplied.get("data_sensitivity", "internal"),
            "external_side_effect": tool.side_effects,
            "fresh_backup": supplied.get("fresh_backup", not tool.side_effects),
            "observer_status": supplied.get("observer_status", "agreed"),
            "benchmark_fresh": supplied.get("benchmark_fresh", True),
            "memory_coverage_fresh": supplied.get("memory_coverage_fresh", True),
            "prompt_injection_suspected": supplied.get(
                "prompt_injection_suspected", False
            ),
        }

    @staticmethod
    def _cycle_path(steps: list[WorkflowStep]) -> list[str]:
        dependencies = {step.id: list(step.depends_on) for step in steps}
        visiting: set[str] = set()
        visited: set[str] = set()
        path: list[str] = []

        def visit(node: str) -> list[str] | None:
            if node in visiting:
                index = path.index(node)
                return path[index:] + [node]
            if node in visited:
                return None
            visiting.add(node)
            path.append(node)
            for dependency in dependencies[node]:
                found = visit(dependency)
                if found:
                    return found
            path.pop()
            visiting.remove(node)
            visited.add(node)
            return None

        for node in dependencies:
            found = visit(node)
            if found:
                return found
        return []

    @staticmethod
    def _hash(value: Any) -> str:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _to_dict(item: WorkflowSpecification) -> dict[str, Any]:
        return {
            "id": item.id,
            "spec_key": item.spec_key,
            "version": item.version,
            "status": item.status,
            "title": item.title,
            "specification": item.specification,
            "content_hash": item.content_hash,
            "risk_level": item.risk_level,
            "source_type": item.source_type,
            "source_id": item.source_id,
            "sandbox_result": item.sandbox_result,
            "observer_review_id": item.observer_review_id,
            "approval_id": item.approval_id,
            "created_by": item.created_by,
            "created_at": item.created_at.isoformat(),
            "activated_at": item.activated_at.isoformat() if item.activated_at else None,
        }
