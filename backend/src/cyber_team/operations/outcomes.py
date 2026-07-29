"""Outcome assessment, reflection, and probation adaptation."""

from __future__ import annotations

import hashlib
import json
import uuid
from types import SimpleNamespace
from typing import Any

from sqlalchemy import desc, func, select

from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import (
    BusinessWorkItem,
    ExecutiveReflection,
    OperationGraphEdge,
    OperationGraphNode,
    OutcomeAssessment,
    OutsourcingRequest,
    WorkflowSpecification,
)


class OutcomeLearningService:
    """Compare expected/actual work and adapt bounded autonomous policy."""

    def __init__(
        self,
        *,
        action_policy_service,
        memory_service=None,
        audit_service=None,
    ) -> None:
        self._policy = action_policy_service
        self._memory = memory_service
        self._audit = audit_service

    async def assess_terminal_work(self, *, limit: int = 200) -> dict[str, Any]:
        created: list[dict[str, Any]] = []
        async with async_session() as session:
            items = (
                await session.execute(
                    select(BusinessWorkItem)
                    .where(
                        BusinessWorkItem.status.in_(
                            {"completed", "failed", "blocked", "cancelled"}
                        )
                    )
                    .order_by(BusinessWorkItem.updated_at)
                    .limit(max(1, min(limit, 500)))
                )
            ).scalars().all()
        for item in items:
            assessment = await self.assess_work_item(item.id)
            if not assessment.get("duplicate"):
                created.append(assessment)
        reflection = await self._reflect(created) if created else None
        remediation = await self._open_repeated_failure_outsourcing()
        return {
            "status": "completed",
            "assessed": len(created),
            "items": created,
            "reflection": reflection,
            "remediation": remediation,
        }

    async def assess_work_item(self, work_item_id: str) -> dict[str, Any]:
        key = self._hash({"work_item_id": work_item_id, "version": "v1"})
        async with async_session() as session:
            existing = (
                await session.execute(
                    select(OutcomeAssessment).where(
                        OutcomeAssessment.idempotency_key == key
                    )
                )
            ).scalar_one_or_none()
            if existing:
                return {**self._to_dict(existing), "duplicate": True}
            work = await session.get(BusinessWorkItem, work_item_id)
            if not work:
                raise ValueError("Business work item not found")
            if work.status not in {"completed", "failed", "blocked", "cancelled"}:
                raise ValueError("Business work item is not terminal")
            actual = work.actual_outcome or {}
            expected = work.expected_outcome or {}
            failures = self._failures(work)
            guardrails = self._guardrail_breaches(work)
            evaluator_score = self._evaluator_score(work, failures, guardrails)
            attribution = self._attribution_confidence(work)
            recommendation = self._recommendation(
                work,
                evaluator_score=evaluator_score,
                guardrails=guardrails,
            )
            assessment = OutcomeAssessment(
                id=f"outcome_{uuid.uuid4().hex}",
                work_item_id=work.id,
                status="recorded",
                expected_outcome=expected,
                actual_outcome=actual,
                kpi_changes=dict(actual.get("kpi_changes") or {}),
                guardrail_breaches=guardrails,
                costs=dict(actual.get("costs") or {"financial_usd": 0}),
                failures=failures,
                attribution_confidence=attribution,
                evaluator_score=evaluator_score,
                recommendation=recommendation,
                evidence_ids=list(actual.get("evidence_ids") or [])[:100],
                idempotency_key=key,
                assessed_by="outcome_evaluator",
            )
            session.add(assessment)
            if work.workflow_specification_id:
                specification = await session.get(
                    WorkflowSpecification, work.workflow_specification_id
                )
                if specification and recommendation in {"stop", "rollback", "revise"}:
                    specification.status = "review_required"
                    specification.sandbox_result = {
                        **(specification.sandbox_result or {}),
                        "outcome_recommendation": recommendation,
                        "outcome_assessment_id": assessment.id,
                    }
            await session.commit()
            result = self._to_dict(assessment)
        await self._record_graph(work, result)
        policy = work.policy_decision or {}
        action_class = policy.get("action_class")
        if action_class and policy.get("external_side_effect"):
            result["action_policy"] = await self._policy.record_validated_case(
                action_class,
                compliant=not guardrails,
                evaluator_score=evaluator_score,
                high_severity_findings=sum(
                    1 for item in guardrails if item.get("severity") == "high"
                ),
            )
        if self._audit:
            await self._audit.record_control_evidence(
                control_id="autonomy.outcome_assessment",
                control_area="autonomous_operations",
                actor="outcome_evaluator",
                outcome="success" if recommendation == "continue" else "review_required",
                evidence={
                    "assessment_id": result["id"],
                    "work_item_id": work.id,
                    "evaluator_score": evaluator_score,
                    "recommendation": recommendation,
                    "guardrail_breaches": guardrails,
                },
            )
        return {**result, "duplicate": False}

    async def list_assessments(
        self,
        *,
        recommendation: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        async with async_session() as session:
            query = select(OutcomeAssessment)
            if recommendation:
                query = query.where(
                    OutcomeAssessment.recommendation == recommendation
                )
            items = (
                await session.execute(
                    query.order_by(desc(OutcomeAssessment.created_at)).limit(
                        max(1, min(limit, 500))
                    )
                )
            ).scalars().all()
            return [self._to_dict(item) for item in items]

    async def _reflect(self, assessments: list[dict[str, Any]]) -> dict[str, Any]:
        failures = [
            item
            for item in assessments
            if item["recommendation"] in {"revise", "stop", "rollback"}
        ]
        reflection = ExecutiveReflection(
            id=f"reflection_{uuid.uuid4().hex}",
            run_id=None,
            summary=(
                f"Assessed {len(assessments)} terminal work item(s); "
                f"{len(failures)} require adaptation."
            ),
            what_changed=[
                {
                    "assessment_id": item["id"],
                    "recommendation": item["recommendation"],
                    "score": item["evaluator_score"],
                }
                for item in assessments
            ],
            repeated_patterns=self._repeated_patterns(assessments),
            failures=[
                {"assessment_id": item["id"], "failures": item["failures"]}
                for item in failures
            ],
            memory_gaps=[
                item["id"]
                for item in assessments
                if item["attribution_confidence"] < 0.7
            ],
            next_watch_items=[item["id"] for item in failures],
            metadata_={"source_type": "outcome_learning", "assessment_count": len(assessments)},
        )
        async with async_session() as session:
            session.add(reflection)
            await session.commit()
        memory_id = None
        if self._memory:
            remembered = await self._memory.remember(
                SimpleNamespace(
                    agent_id="chief_operating_agent",
                    memory_type="procedural",
                    namespace=f"{settings.company_namespace}:executive",
                    content=(
                        reflection.summary
                        + "\n"
                        + json.dumps(reflection.what_changed, sort_keys=True)
                    )[:8000],
                    metadata={
                        "source_type": "executive_reflection",
                        "source_id": reflection.id,
                        "assessment_ids": [item["id"] for item in assessments],
                    },
                    importance=0.9,
                )
            )
            memory_id = remembered["id"]
        return {
            "id": reflection.id,
            "summary": reflection.summary,
            "memory_id": memory_id,
            "created_at": reflection.created_at.isoformat(),
        }

    async def _record_graph(
        self,
        work: BusinessWorkItem,
        assessment: dict[str, Any],
    ) -> None:
        if not settings.operation_graph_indexing_enabled:
            return
        work_key = f"business_work_item:{work.id}"
        outcome_key = f"outcome_assessment:{assessment['id']}"
        async with async_session() as session:
            work_node = (
                await session.execute(
                    select(OperationGraphNode).where(
                        OperationGraphNode.idempotency_key == work_key
                    )
                )
            ).scalar_one_or_none()
            if not work_node:
                work_node = OperationGraphNode(
                    id=f"opnode_{uuid.uuid4().hex}",
                    node_type="business_work_item",
                    title=work.title,
                    summary=work.description[:2000],
                    source_type="business_work_item",
                    source_id=work.id,
                    agent_id=work.assigned_agent_id,
                    risk_level=work.risk_level,
                    confidence=1.0,
                    impact_score=0.0,
                    memory_namespace=settings.company_namespace,
                    tags=[work.work_type, work.status],
                    metadata_={"mandate_id": work.mandate_id},
                    idempotency_key=work_key,
                )
                session.add(work_node)
                await session.flush()
            outcome_node = OperationGraphNode(
                id=f"opnode_{uuid.uuid4().hex}",
                node_type="outcome_assessment",
                title=f"Outcome: {work.title}"[:240],
                summary=(
                    f"Recommendation={assessment['recommendation']}; "
                    f"score={assessment['evaluator_score']:.2f}"
                ),
                source_type="outcome_assessment",
                source_id=assessment["id"],
                agent_id="outcome_evaluator",
                risk_level=work.risk_level,
                confidence=assessment["attribution_confidence"],
                impact_score=0.0,
                memory_namespace=settings.company_namespace,
                tags=[assessment["recommendation"], "outcome_learning"],
                metadata_={"work_item_id": work.id},
                idempotency_key=outcome_key,
            )
            session.add(outcome_node)
            await session.flush()
            session.add(
                OperationGraphEdge(
                    id=f"opedge_{uuid.uuid4().hex}",
                    source_node_id=work_node.id,
                    target_node_id=outcome_node.id,
                    edge_type="evaluated_by",
                    metadata_={},
                )
            )
            await session.commit()

    async def _open_repeated_failure_outsourcing(self) -> dict[str, Any]:
        threshold = 3
        async with async_session() as session:
            failures = (
                await session.execute(
                    select(
                        BusinessWorkItem.assigned_agent_id,
                        func.count(BusinessWorkItem.id),
                    )
                    .where(
                        BusinessWorkItem.status.in_({"failed", "blocked"}),
                        BusinessWorkItem.assigned_agent_id.is_not(None),
                    )
                    .group_by(BusinessWorkItem.assigned_agent_id)
                    .having(func.count(BusinessWorkItem.id) >= threshold)
                )
            ).all()
            created = []
            for agent_id, count in failures:
                existing = (
                    await session.execute(
                        select(OutsourcingRequest).where(
                            OutsourcingRequest.status == "open",
                            OutsourcingRequest.source_type == "repeated_agent_failure",
                            OutsourcingRequest.source_id == agent_id,
                        )
                    )
                ).scalar_one_or_none()
                if existing:
                    continue
                request = OutsourcingRequest(
                    id=f"outsource_{uuid.uuid4().hex}",
                    title=f"Remediate repeated failures for {agent_id}"[:240],
                    status="open",
                    complexity_reason=(
                        f"Agent has {count} failed or policy-blocked work items."
                    ),
                    task_spec={
                        "goal": "Diagnose capability, workflow, evidence, or tool gap.",
                        "agent_id": agent_id,
                    },
                    context_pack={"agent_id": agent_id, "failure_count": count},
                    acceptance_tests=[
                        "root_cause_documented",
                        "tests_reproduce_and_verify_fix",
                        "no_secret_material_in_artifacts",
                    ],
                    foss_constraints=[
                        "Use only free and open-source dependencies.",
                        "Declare licenses and hosted-service requirements.",
                    ],
                    security_constraints=[
                        "Use a no-secret sandbox.",
                        "Do not activate generated code without owner review and CI.",
                    ],
                    files_involved=[],
                    expected_artifact="Reviewable patch or evidence-backed operating change.",
                    replay_instructions=(
                        "Run acceptance tests in an isolated development environment."
                    ),
                    source_type="repeated_agent_failure",
                    source_id=agent_id,
                    created_by="outcome_evaluator",
                )
                session.add(request)
                created.append(request.id)
            await session.commit()
        return {"created": created, "threshold": threshold}

    @staticmethod
    def _failures(work: BusinessWorkItem) -> list[dict[str, Any]]:
        if work.status == "completed":
            return list((work.actual_outcome or {}).get("failures") or [])
        return [
            {
                "type": work.status,
                "error": (work.actual_outcome or {}).get("error", "unknown"),
            }
        ]

    @staticmethod
    def _guardrail_breaches(work: BusinessWorkItem) -> list[dict[str, Any]]:
        breaches = list((work.actual_outcome or {}).get("guardrail_breaches") or [])
        if (work.actual_outcome or {}).get("side_effects_executed") and not (
            work.policy_decision or {}
        ).get("allowed"):
            breaches.append(
                {"type": "unauthorized_side_effect", "severity": "high"}
            )
        return breaches

    @staticmethod
    def _evaluator_score(work, failures, guardrails) -> float:
        if work.status != "completed":
            return 0.0
        score = 1.0
        score -= min(0.5, len(failures) * 0.2)
        score -= min(0.8, len(guardrails) * 0.4)
        if not work.actual_outcome:
            score -= 0.3
        return round(max(0.0, score), 3)

    @staticmethod
    def _attribution_confidence(work: BusinessWorkItem) -> float:
        actual = work.actual_outcome or {}
        if actual.get("evidence_ids") and actual.get("kpi_changes"):
            return 0.9
        if actual:
            return 0.7
        return 0.3

    @staticmethod
    def _recommendation(work, *, evaluator_score, guardrails) -> str:
        if any(item.get("severity") == "high" for item in guardrails):
            return "rollback"
        if work.status in {"failed", "cancelled"}:
            return "revise"
        if work.status == "blocked":
            return "stop"
        if evaluator_score < settings.action_policy_min_evaluator_score:
            return "revise"
        return "continue"

    @staticmethod
    def _repeated_patterns(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for item in items:
            counts[item["recommendation"]] = counts.get(item["recommendation"], 0) + 1
        return [
            {"recommendation": key, "count": value}
            for key, value in sorted(counts.items())
            if value > 1
        ]

    @staticmethod
    def _hash(value: Any) -> str:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _to_dict(item: OutcomeAssessment) -> dict[str, Any]:
        return {
            "id": item.id,
            "work_item_id": item.work_item_id,
            "execution_record_id": item.execution_record_id,
            "status": item.status,
            "expected_outcome": item.expected_outcome,
            "actual_outcome": item.actual_outcome,
            "kpi_changes": item.kpi_changes,
            "guardrail_breaches": item.guardrail_breaches,
            "costs": item.costs,
            "failures": item.failures,
            "attribution_confidence": item.attribution_confidence,
            "evaluator_score": item.evaluator_score,
            "recommendation": item.recommendation,
            "evidence_ids": item.evidence_ids,
            "assessed_by": item.assessed_by,
            "created_at": item.created_at.isoformat(),
        }
