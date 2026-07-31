"""Evidence-derived strategy, KPI, experiment, and portfolio services."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import (
    Agent,
    BusinessWorkItem,
    CompanyClaim,
    CompanyModelRevision,
    CompanyObjective,
    CompanyObjectiveRevision,
    ObserverReview,
    OperatingKPIDefinition,
    OperatingKPIObservation,
    OperatingKPIRevision,
    StrategicExperiment,
)

ALLOWED_METRIC_BINDINGS = {
    "unknown_critical_facts": "company_model.unknown_count",
    "verified_customer_segments": "company_claim.verified_customer_segments",
    "total_projects": "erpnext.project.total",
    "completed_projects": "erpnext.project.completed",
    "open_tasks": "erpnext.task.open",
    "open_issues": "erpnext.issue.open",
    "active_customers": "erpnext.customer.active",
    "pipeline_value": "erpnext.opportunity.pipeline_value",
    "outstanding_revenue": "erpnext.sales_invoice.outstanding",
}


class KPIFormulaError(ValueError):
    pass


class KPIFormula:
    """A non-executable arithmetic DSL over allowlisted metric names."""

    ALLOWED_NODES = {
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Mod,
        ast.Pow,
        ast.USub,
        ast.UAdd,
        ast.Name,
        ast.Load,
        ast.Constant,
        ast.Call,
    }
    ALLOWED_FUNCTIONS = {"min": min, "max": max, "abs": abs, "round": round}

    @classmethod
    def validate(cls, formula: str, bindings: dict[str, str]) -> dict[str, Any]:
        if not formula or len(formula) > 500:
            raise KPIFormulaError("KPI formula must contain 1-500 characters")
        try:
            tree = ast.parse(formula, mode="eval")
        except SyntaxError as exc:
            raise KPIFormulaError("KPI formula is not valid arithmetic") from exc
        names: set[str] = set()
        for node in ast.walk(tree):
            if type(node) not in cls.ALLOWED_NODES:
                raise KPIFormulaError(f"KPI formula node {type(node).__name__} is prohibited")
            if isinstance(node, ast.Name):
                names.add(node.id)
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.func.id not in cls.ALLOWED_FUNCTIONS:
                    raise KPIFormulaError("Only min, max, abs, and round calls are allowed")
                if node.keywords:
                    raise KPIFormulaError("KPI function keyword arguments are prohibited")
            if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
                raise KPIFormulaError("KPI constants must be numeric")
            if isinstance(node, ast.Pow) and any(
                isinstance(item, ast.Constant) and abs(float(item.value)) > 10
                for item in ast.walk(node)
                if isinstance(item, ast.Constant) and isinstance(item.value, (int, float))
            ):
                raise KPIFormulaError("KPI exponents are limited to absolute value 10")
        metric_names = names - set(cls.ALLOWED_FUNCTIONS)
        missing = sorted(metric_names - set(bindings))
        if missing:
            raise KPIFormulaError("KPI formula has unbound metrics: " + ", ".join(missing))
        prohibited = sorted(
            name
            for name, binding in bindings.items()
            if name in metric_names and binding not in ALLOWED_METRIC_BINDINGS.values()
        )
        if prohibited:
            raise KPIFormulaError("KPI formula has prohibited bindings: " + ", ".join(prohibited))
        return {"formula": formula, "metric_names": sorted(metric_names), "bindings": bindings}

    @classmethod
    def evaluate(
        cls,
        formula: str,
        bindings: dict[str, str],
        metrics: dict[str, float],
    ) -> float:
        validation = cls.validate(formula, bindings)
        values = {
            name: float(metrics.get(binding, 0.0))
            for name, binding in bindings.items()
            if name in validation["metric_names"]
        }
        tree = ast.parse(formula, mode="eval")
        value = cls._evaluate_node(tree.body, values)
        if not math.isfinite(value):
            raise KPIFormulaError("KPI result must be finite")
        return float(value)

    @classmethod
    def _evaluate_node(cls, node: ast.AST, values: dict[str, float]) -> float:
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            return values[node.id]
        if isinstance(node, ast.UnaryOp):
            value = cls._evaluate_node(node.operand, values)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp):
            left = cls._evaluate_node(node.left, values)
            right = cls._evaluate_node(node.right, values)
            operations = {
                ast.Add: lambda: left + right,
                ast.Sub: lambda: left - right,
                ast.Mult: lambda: left * right,
                ast.Div: lambda: left / right,
                ast.Mod: lambda: left % right,
                ast.Pow: lambda: left**right,
            }
            try:
                return operations[type(node.op)]()
            except ZeroDivisionError as exc:
                raise KPIFormulaError("KPI formula divided by zero") from exc
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            args = [cls._evaluate_node(item, values) for item in node.args]
            if node.func.id == "round" and len(args) == 2:
                return float(round(args[0], int(args[1])))
            return float(cls.ALLOWED_FUNCTIONS[node.func.id](*args))
        raise KPIFormulaError(f"KPI formula node {type(node).__name__} is unsupported")


class CompanyStrategyService:
    """Create and evaluate versioned business strategy from active evidence."""

    STRATEGY_AGENT_ID = "strategy_portfolio_agent"
    PROBATION_DAYS = 30
    STRATEGY_CONTEXT_MAX_CHARS = 18_000
    STRATEGY_MAX_CLAIMS = 80

    def __init__(self, *, llm_gateway=None, audit_service=None) -> None:
        self._llm = llm_gateway
        self._audit = audit_service

    async def ensure_strategy_agent(
        self,
        *,
        company_namespace: str | None = None,
    ) -> Agent:
        """Provision the durable strategy specialist before it creates revisions."""
        namespace = company_namespace or settings.company_namespace
        async with async_session() as session:
            agent = await session.get(Agent, self.STRATEGY_AGENT_ID)
            if agent:
                return agent
            agent = Agent(
                id=self.STRATEGY_AGENT_ID,
                role_family="strategy",
                role_name="Strategy Portfolio Agent",
                instructions=(
                    "Propose evidence-linked objectives, allowlisted KPI formulas, and "
                    "reversible experiments. Preserve unknown facts, optimize the work "
                    "portfolio, and submit every proposal to Observer review."
                ),
                tools=[
                    "company_profile_read",
                    "analytics_read",
                    "memory_recall",
                    "memory_remember",
                    "approval_request",
                    "owner_notify",
                ],
                memory_namespace=f"{namespace}:strategy",
                approval_policy="auto",
                status="active",
                config={
                    "system_agent": True,
                    "authority": "probationary_strategy_proposals",
                    "side_effect_authority": "none",
                    "probation_days": self.PROBATION_DAYS,
                },
            )
            session.add(agent)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                agent = await session.get(Agent, self.STRATEGY_AGENT_ID)
                if not agent:
                    raise
            return agent

    async def run_strategy_cycle(
        self,
        *,
        actor: str = STRATEGY_AGENT_ID,
        company_namespace: str | None = None,
    ) -> dict[str, Any]:
        namespace = company_namespace or settings.company_namespace
        await self.ensure_strategy_agent(company_namespace=namespace)
        model = await self._active_model(namespace)
        if not model:
            return {
                "status": "blocked",
                "reason": "No Observer-approved active company model exists.",
                "company_namespace": namespace,
            }
        claims = await self._active_claims(namespace)
        proposals = await self._propose_strategy(model, claims)
        generation_error = proposals.get("generation_error")
        if generation_error:
            result = {
                "status": "blocked",
                "reason": "strategy_advisory_unavailable",
                "detail": generation_error,
                "company_namespace": namespace,
                "company_model_revision_id": model.id,
            }
            if self._audit:
                await self._audit.record_control_evidence(
                    control_id="strategy.autonomous_cycle",
                    control_area="ai_governance",
                    actor=actor,
                    outcome="failure",
                    evidence={
                        "company_model_revision_id": model.id,
                        "reason": result["reason"],
                    },
                )
            return result
        validation = self._validate_proposals(proposals, model, claims)
        if not validation["valid"]:
            return {"status": "blocked", "reason": "invalid_strategy_proposal", **validation}

        observer = await self._record_observer_review(model, proposals)
        if observer.status != "agreed":
            return {
                "status": "owner_review",
                "reason": "Observer did not approve strategy activation.",
                "observer_review_id": observer.id,
                "findings": observer.findings,
            }

        objectives = []
        for proposal in proposals["objectives"]:
            objectives.append(
                await self._upsert_objective_revision(
                    proposal,
                    model=model,
                    observer_review_id=observer.id,
                    actor=actor,
                )
            )
        objective_by_key = {item["strategy_key"]: item for item in objectives}
        kpis = []
        for proposal in proposals["kpis"]:
            linked = [
                objective_by_key[key]["revision_id"]
                for key in proposal.get("objective_keys", [])
                if key in objective_by_key
            ]
            kpis.append(
                await self._upsert_kpi_revision(
                    proposal,
                    objective_revision_ids=linked,
                    actor=actor,
                )
            )
        experiments = []
        for proposal in proposals.get("experiments", []):
            linked = objective_by_key.get(proposal.get("objective_key"))
            experiments.append(
                await self._upsert_experiment(
                    proposal,
                    objective_revision_id=linked["revision_id"] if linked else None,
                    namespace=namespace,
                    actor=actor,
                )
            )
        observations = await self.observe_kpis(namespace=namespace, source_id=model.id)
        result = {
            "status": "completed",
            "company_namespace": namespace,
            "company_model_revision_id": model.id,
            "observer_review_id": observer.id,
            "objectives": objectives,
            "kpis": kpis,
            "experiments": experiments,
            "observations": observations,
        }
        if self._audit:
            await self._audit.record_control_evidence(
                control_id="strategy.autonomous_cycle",
                control_area="ai_governance",
                actor=actor,
                outcome="success",
                evidence={
                    "company_model_revision_id": model.id,
                    "observer_review_id": observer.id,
                    "objective_count": len(objectives),
                    "kpi_count": len(kpis),
                    "experiment_count": len(experiments),
                },
            )
        return result

    async def portfolio(self, *, company_namespace: str | None = None) -> dict[str, Any]:
        namespace = company_namespace or settings.company_namespace
        async with async_session() as session:
            objective_rows = (
                await session.execute(
                    select(CompanyObjectiveRevision)
                    .where(CompanyObjectiveRevision.status.in_({"probation", "active"}))
                    .order_by(desc(CompanyObjectiveRevision.created_at))
                )
            ).scalars().all()
            kpi_rows = (
                await session.execute(
                    select(OperatingKPIRevision)
                    .where(OperatingKPIRevision.status.in_({"probation", "active"}))
                    .order_by(desc(OperatingKPIRevision.created_at))
                )
            ).scalars().all()
            experiment_rows = (
                await session.execute(
                    select(StrategicExperiment)
                    .where(StrategicExperiment.company_namespace == namespace)
                    .order_by(desc(StrategicExperiment.created_at))
                )
            ).scalars().all()
            work_rows = (
                await session.execute(
                    select(BusinessWorkItem)
                    .where(
                        BusinessWorkItem.company_namespace == namespace,
                        BusinessWorkItem.status.in_({"proposed", "ready", "leased", "running"}),
                    )
                    .order_by(desc(BusinessWorkItem.created_at))
                )
            ).scalars().all()
        ranked_work = sorted(
            [self._rank_work_item(item) for item in work_rows],
            key=lambda item: item["portfolio_score"],
            reverse=True,
        )
        return {
            "company_namespace": namespace,
            "objectives": [self._objective_revision_to_dict(item) for item in objective_rows],
            "kpis": [self._kpi_revision_to_dict(item) for item in kpi_rows],
            "experiments": [self._experiment_to_dict(item) for item in experiment_rows],
            "ranked_work": ranked_work,
            "counts": {
                "objectives": len(objective_rows),
                "kpis": len(kpi_rows),
                "experiments": len(experiment_rows),
                "work": len(ranked_work),
            },
        }

    async def observe_kpis(
        self,
        *,
        namespace: str,
        source_id: str,
    ) -> list[dict[str, Any]]:
        metrics = await self._measurement_values(namespace)
        async with async_session() as session:
            revisions = (
                await session.execute(
                    select(OperatingKPIRevision).where(
                        OperatingKPIRevision.status.in_({"probation", "active"}),
                        OperatingKPIRevision.created_by != "v3_migration",
                    )
                )
            ).scalars().all()
            definitions = {
                item.id: item
                for item in (
                    await session.execute(select(OperatingKPIDefinition))
                ).scalars().all()
            }
            results = []
            for revision in revisions:
                definition = definitions.get(revision.kpi_definition_id)
                if not definition:
                    continue
                try:
                    value = KPIFormula.evaluate(
                        revision.formula,
                        revision.measurement_bindings,
                        metrics,
                    )
                except KPIFormulaError as exc:
                    item = OperatingKPIObservation(
                        id=f"kpiobs_{uuid.uuid4().hex}",
                        kpi_key=definition.key,
                        value=0,
                        status="invalid_definition",
                        source_type="company_strategy_cycle",
                        source_id=source_id,
                        metadata_={
                            "kpi_revision_id": revision.id,
                            "validation_error": str(exc),
                        },
                    )
                    session.add(item)
                    results.append(
                        {
                            "id": item.id,
                            "kpi_key": item.kpi_key,
                            "value": None,
                            "status": item.status,
                            "kpi_revision_id": revision.id,
                        }
                    )
                    continue
                status = "recorded"
                if revision.lower_guardrail is not None and value < revision.lower_guardrail:
                    status = "guardrail_breach"
                if revision.upper_guardrail is not None and value > revision.upper_guardrail:
                    status = "guardrail_breach"
                item = OperatingKPIObservation(
                    id=f"kpiobs_{uuid.uuid4().hex}",
                    kpi_key=definition.key,
                    value=value,
                    status=status,
                    source_type="company_strategy_cycle",
                    source_id=source_id,
                    metadata_={"kpi_revision_id": revision.id, "metrics": metrics},
                )
                session.add(item)
                results.append(
                    {
                        "id": item.id,
                        "kpi_key": item.kpi_key,
                        "value": value,
                        "status": status,
                        "kpi_revision_id": revision.id,
                    }
                )
            await session.commit()
            return results

    async def list_kpi_revisions(self, limit: int = 100) -> list[dict[str, Any]]:
        async with async_session() as session:
            items = (
                await session.execute(
                    select(OperatingKPIRevision)
                    .order_by(desc(OperatingKPIRevision.created_at))
                    .limit(max(1, min(limit, 500)))
                )
            ).scalars().all()
            return [self._kpi_revision_to_dict(item) for item in items]

    async def list_experiments(
        self,
        *,
        company_namespace: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        namespace = company_namespace or settings.company_namespace
        async with async_session() as session:
            items = (
                await session.execute(
                    select(StrategicExperiment)
                    .where(StrategicExperiment.company_namespace == namespace)
                    .order_by(desc(StrategicExperiment.created_at))
                    .limit(max(1, min(limit, 500)))
                )
            ).scalars().all()
            return [self._experiment_to_dict(item) for item in items]

    async def _active_model(self, namespace: str) -> CompanyModelRevision | None:
        async with async_session() as session:
            return (
                await session.execute(
                    select(CompanyModelRevision)
                    .where(
                        CompanyModelRevision.company_namespace == namespace,
                        CompanyModelRevision.status == "active",
                    )
                    .order_by(desc(CompanyModelRevision.revision))
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def _active_claims(self, namespace: str) -> list[dict[str, Any]]:
        async with async_session() as session:
            items = (
                await session.execute(
                    select(CompanyClaim).where(
                        CompanyClaim.company_namespace == namespace,
                        CompanyClaim.epistemic_state.notin_({"superseded", "disputed"}),
                        (CompanyClaim.valid_until.is_(None))
                        | (CompanyClaim.valid_until > utc_now()),
                    )
                )
            ).scalars().all()
            return [
                {
                    "id": item.id,
                    "predicate": item.predicate,
                    "value": item.value,
                    "state": item.epistemic_state,
                    "confidence": item.confidence,
                    "trust_class": item.trust_class,
                    "sensitivity": item.sensitivity,
                    "evidence_ids": item.evidence_ids,
                }
                for item in items
            ]

    async def _propose_strategy(
        self,
        model: CompanyModelRevision,
        claims: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not self._llm:
            return self._generation_failure("No strategy advisory LLM is configured.")
        system_prompt = (
            "You are Cyber-Team's Strategy Portfolio Agent. Propose measurable "
            "objectives, safe arithmetic KPIs, and reversible experiments only "
            "from the supplied active model and provenance claims. Never invent "
            "markets, customers, revenue, legal facts, metric functions, or metric "
            "bindings. Missing facts require discovery objectives. KPI formulas may "
            "use only arithmetic plus min, max, abs, and round over the exact supplied "
            "binding names. Omit any KPI that cannot be expressed with those bindings. "
            "Return only JSON with the same fields and shapes as the supplied example."
        )
        request, advisory_claims = self._bounded_strategy_request(model, claims)
        validation: dict[str, Any] = {"valid": False, "errors": []}
        for attempt in range(2):
            try:
                candidate = await self._llm.invoke_json(
                    system_prompt=system_prompt,
                    user_message=json.dumps(
                        request,
                        default=str,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    agent_id=self.STRATEGY_AGENT_ID,
                )
            except Exception as exc:  # noqa: BLE001 - fail closed on provider errors.
                return self._generation_failure(
                    f"Strategy advisory provider failed: {type(exc).__name__}"
                )
            validation = self._validate_proposals(candidate, model, advisory_claims)
            if validation["valid"]:
                return candidate
            if attempt == 0:
                request = {
                    **request,
                    "invalid_candidate": candidate,
                    "validation_errors": validation["errors"],
                    "repair_instruction": (
                        "Return a corrected complete proposal. Remove unsupported "
                        "objectives, KPIs, or experiments rather than inventing metric "
                        "bindings. Do not cite evidence outside the supplied claims."
                    ),
                }
        return self._generation_failure(
            "Strategy advisory output failed schema or provenance validation: "
            + "; ".join(validation["errors"][:10])
        )

    @classmethod
    def _bounded_strategy_request(
        cls,
        model: CompanyModelRevision,
        claims: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Build valid JSON that fits the local inference context budget."""
        ranked = sorted(
            claims,
            key=lambda item: (
                0 if item.get("state") == "verified" else 1,
                0 if item.get("trust_class") in {"owner_locked", "canonical"} else 1,
                -float(item.get("confidence") or 0),
                str(item.get("id") or ""),
            ),
        )[: cls.STRATEGY_MAX_CLAIMS]
        compact_model = cls._compact_json_value(model.model, max_items=12, max_string=500)
        compact_unknowns = [str(item)[:300] for item in (model.unknowns or [])[:50]]
        compact_claims: list[dict[str, Any]] = []
        model_compacted = compact_model != model.model

        def build(items: list[dict[str, Any]]) -> dict[str, Any]:
            return {
                "company_model": compact_model,
                "unknowns": compact_unknowns,
                "claims": items,
                "context_summary": {
                    "total_claim_count": len(claims),
                    "included_claim_count": len(items),
                    "omitted_claim_count": max(0, len(claims) - len(items)),
                    "selection": "verified/canonical first, then confidence",
                    "model_compacted": model_compacted,
                },
                "allowed_metric_bindings": ALLOWED_METRIC_BINDINGS,
                "example": cls._deterministic_proposals(model, items),
            }

        request = build(compact_claims)
        while (
            len(
                json.dumps(
                    request,
                    default=str,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            > cls.STRATEGY_CONTEXT_MAX_CHARS
            and compact_unknowns
        ):
            compact_unknowns.pop()
            request = build(compact_claims)
        if len(
            json.dumps(
                request,
                default=str,
                separators=(",", ":"),
                sort_keys=True,
            )
        ) > cls.STRATEGY_CONTEXT_MAX_CHARS:
            compact_model = {
                "context_omitted": "Company model exceeded the advisory context budget.",
                "source_hash": model.source_hash,
            }
            model_compacted = True
            request = build(compact_claims)
        for claim in ranked:
            compact_claim = {
                "id": str(claim.get("id") or "")[:120],
                "predicate": str(claim.get("predicate") or "")[:160],
                "value": cls._compact_json_value(
                    claim.get("value") or {}, max_items=12, max_string=500
                ),
                "state": str(claim.get("state") or "unknown")[:40],
                "confidence": float(claim.get("confidence") or 0),
                "trust_class": str(claim.get("trust_class") or "unknown")[:40],
                "sensitivity": str(claim.get("sensitivity") or "internal")[:40],
                "evidence_ids": [
                    str(item)[:120] for item in (claim.get("evidence_ids") or [])[:12]
                ],
            }
            candidate_claims = [*compact_claims, compact_claim]
            candidate_request = build(candidate_claims)
            encoded = json.dumps(
                candidate_request,
                default=str,
                separators=(",", ":"),
                sort_keys=True,
            )
            if len(encoded) > cls.STRATEGY_CONTEXT_MAX_CHARS:
                continue
            compact_claims = candidate_claims
            request = candidate_request
        return request, compact_claims

    @classmethod
    def _compact_json_value(
        cls,
        value: Any,
        *,
        max_items: int,
        max_string: int,
        depth: int = 0,
    ) -> Any:
        if depth >= 5:
            return "[nested value omitted]"
        if isinstance(value, dict):
            return {
                str(key)[:120]: cls._compact_json_value(
                    item,
                    max_items=max_items,
                    max_string=max_string,
                    depth=depth + 1,
                )
                for key, item in list(value.items())[:max_items]
            }
        if isinstance(value, list):
            return [
                cls._compact_json_value(
                    item,
                    max_items=max_items,
                    max_string=max_string,
                    depth=depth + 1,
                )
                for item in value[:max_items]
            ]
        if isinstance(value, str):
            return value[:max_string]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)[:max_string]

    @staticmethod
    def _generation_failure(detail: str) -> dict[str, Any]:
        return {
            "objectives": [],
            "kpis": [],
            "experiments": [],
            "generation_error": detail,
        }

    @staticmethod
    def _deterministic_proposals(
        model: CompanyModelRevision,
        claims: list[dict[str, Any]],
    ) -> dict[str, Any]:
        evidence_ids = sorted(
            {
                evidence_id
                for claim in claims
                for evidence_id in claim.get("evidence_ids", [])
            }
        )
        objectives = []
        kpis = []
        experiments = []
        if model.unknowns:
            objectives.append(
                {
                    "strategy_key": "resolve_critical_company_unknowns",
                    "title": "Resolve critical company model unknowns",
                    "description": "Acquire evidence for currently unknown operating facts.",
                    "category": "company_discovery",
                    "priority": "high",
                    "target": {"unknown_count": 0},
                    "confidence": 0.95,
                    "evidence_ids": evidence_ids,
                }
            )
            kpis.append(
                {
                    "key": "critical_company_unknowns",
                    "title": "Critical company facts still unknown",
                    "unit": "count",
                    "comparison": "max",
                    "formula": "unknown_critical_facts",
                    "bindings": {
                        "unknown_critical_facts": "company_model.unknown_count"
                    },
                    "target_value": 0,
                    "upper_guardrail": max(1, len(model.unknowns)),
                    "objective_keys": ["resolve_critical_company_unknowns"],
                    "confidence": 1.0,
                    "evidence_ids": evidence_ids,
                }
            )
        if model.model.get("offerings") and not model.model.get("customer_segments"):
            objectives.append(
                {
                    "strategy_key": "validate_customer_segments",
                    "title": "Validate customer segments for observed offerings",
                    "description": (
                        "Test who receives value from the evidenced offerings without "
                        "assuming an unevidenced market."
                    ),
                    "category": "market_discovery",
                    "priority": "high",
                    "target": {"verified_customer_segments": 1},
                    "confidence": 0.8,
                    "evidence_ids": evidence_ids,
                }
            )
            kpis.append(
                {
                    "key": "verified_customer_segments",
                    "title": "Verified customer segments",
                    "unit": "count",
                    "comparison": "min",
                    "formula": "verified_customer_segments",
                    "bindings": {
                        "verified_customer_segments": (
                            "company_claim.verified_customer_segments"
                        )
                    },
                    "target_value": 1,
                    "lower_guardrail": 0,
                    "objective_keys": ["validate_customer_segments"],
                    "confidence": 0.8,
                    "evidence_ids": evidence_ids,
                }
            )
            experiments.append(
                {
                    "strategy_key": "segment_problem_interviews",
                    "objective_key": "validate_customer_segments",
                    "title": "Validate one customer problem segment",
                    "hypothesis": (
                        "At least one evidence-backed segment can articulate a recurring "
                        "problem addressed by an observed offering."
                    ),
                    "design": {
                        "type": "research",
                        "external_contact_requires_approval": True,
                        "minimum_evidence_items": 3,
                    },
                    "metric_keys": ["verified_customer_segments"],
                    "budget": {"usd": 0},
                    "risk_level": "low",
                    "evidence_ids": evidence_ids,
                }
            )
        return {"objectives": objectives, "kpis": kpis, "experiments": experiments}

    @staticmethod
    def _validate_proposals(
        proposals: Any,
        model: CompanyModelRevision,
        claims: list[dict[str, Any]],
    ) -> dict[str, Any]:
        errors: list[str] = []
        if not isinstance(proposals, dict):
            return {"valid": False, "errors": ["strategy proposal must be an object"]}
        objectives = proposals.get("objectives")
        kpis = proposals.get("kpis")
        experiments = proposals.get("experiments", [])
        if (
            not isinstance(objectives, list)
            or not isinstance(kpis, list)
            or not isinstance(experiments, list)
        ):
            return {"valid": False, "errors": ["objectives, kpis, and experiments must be lists"]}
        known_evidence = {
            evidence_id
            for claim in claims
            for evidence_id in claim.get("evidence_ids", [])
        }
        objective_keys: set[str] = set()
        for index, item in enumerate(objectives):
            if not isinstance(item, dict) or not item.get("title") or not item.get("strategy_key"):
                errors.append(f"objective {index} is incomplete")
                continue
            strategy_key = str(item["strategy_key"])
            if strategy_key in objective_keys:
                errors.append(f"objective {index} duplicates strategy_key {strategy_key}")
            objective_keys.add(strategy_key)
            if not isinstance(item.get("target"), dict):
                errors.append(f"objective {index} target must be an object")
            if not set(item.get("evidence_ids") or []).issubset(known_evidence):
                errors.append(f"objective {index} cites unknown evidence")
            try:
                confidence = float(item.get("confidence"))
            except (TypeError, ValueError):
                confidence = -1
            if not 0 <= confidence <= 1:
                errors.append(f"objective {index} confidence is invalid")
        kpi_keys: set[str] = set()
        for index, item in enumerate(kpis):
            if not isinstance(item, dict) or not item.get("key") or not item.get("title"):
                errors.append(f"kpi {index} is incomplete")
                continue
            key = str(item["key"])
            if key in kpi_keys:
                errors.append(f"kpi {index} duplicates key {key}")
            kpi_keys.add(key)
            try:
                KPIFormula.validate(str(item.get("formula") or ""), item.get("bindings") or {})
            except KPIFormulaError as exc:
                errors.append(f"kpi {index}: {exc}")
            unknown_objectives = set(item.get("objective_keys") or []) - objective_keys
            if unknown_objectives:
                errors.append(f"kpi {index} references unknown objectives")
            if not set(item.get("evidence_ids") or []).issubset(known_evidence):
                errors.append(f"kpi {index} cites unknown evidence")
            if item.get("comparison") not in {"min", "max", "target"}:
                errors.append(f"kpi {index} comparison is invalid")
            try:
                confidence = float(item.get("confidence"))
            except (TypeError, ValueError):
                confidence = -1
            if not 0 <= confidence <= 1:
                errors.append(f"kpi {index} confidence is invalid")
        experiment_keys: set[str] = set()
        for index, item in enumerate(experiments):
            if (
                not isinstance(item, dict)
                or not item.get("strategy_key")
                or not item.get("title")
                or not item.get("hypothesis")
            ):
                errors.append(f"experiment {index} is incomplete")
                continue
            key = str(item["strategy_key"])
            if key in experiment_keys:
                errors.append(f"experiment {index} duplicates strategy_key {key}")
            experiment_keys.add(key)
            if item.get("objective_key") not in objective_keys:
                errors.append(f"experiment {index} references an unknown objective")
            if not set(item.get("metric_keys") or []).issubset(kpi_keys):
                errors.append(f"experiment {index} references an unknown KPI")
            if not isinstance(item.get("design"), dict):
                errors.append(f"experiment {index} design must be an object")
            if not set(item.get("evidence_ids") or []).issubset(known_evidence):
                errors.append(f"experiment {index} cites unknown evidence")
            budget = item.get("budget") or {}
            try:
                budget_usd = float(budget.get("usd", 0))
            except (AttributeError, TypeError, ValueError):
                budget_usd = -1
            if budget_usd < 0:
                errors.append(f"experiment {index} budget is invalid")
            if item.get("risk_level", "low") not in {"low", "medium", "high", "critical"}:
                errors.append(f"experiment {index} risk level is invalid")
        if model.status != "active":
            errors.append("company model is not active")
        return {"valid": not errors, "errors": errors}

    async def _record_observer_review(
        self,
        model: CompanyModelRevision,
        proposals: dict[str, Any],
    ) -> ObserverReview:
        validation = self._validate_proposals(
            proposals,
            model,
            await self._active_claims(model.company_namespace),
        )
        status = "agreed" if validation["valid"] else "disagreed"
        async with async_session() as session:
            review = ObserverReview(
                id=f"obsrev_{uuid.uuid4().hex}",
                status=status,
                critique=(
                    "Strategy proposal is evidence-linked, measurable, and probationary."
                    if status == "agreed"
                    else "Strategy proposal failed evidence or KPI validation."
                ),
                findings=[{"type": "strategy_validation", "errors": validation["errors"]}]
                if validation["errors"]
                else [],
                consensus_log=[
                    {
                        "actor": "observer_agent",
                        "decision": (
                            "activate_probation" if status == "agreed" else "block"
                        ),
                    }
                ],
                unresolved_objections=[] if status == "agreed" else validation["errors"],
                confidence=model.confidence,
                metadata_={
                    "review_type": "strategy_activation",
                    "company_model_revision_id": model.id,
                    "proposal_hash": self._hash(proposals),
                },
            )
            session.add(review)
            await session.commit()
            return review

    async def _upsert_objective_revision(
        self,
        proposal: dict[str, Any],
        *,
        model: CompanyModelRevision,
        observer_review_id: str,
        actor: str,
    ) -> dict[str, Any]:
        key = str(proposal["strategy_key"])
        objective_id = f"objective_{hashlib.sha256(key.encode()).hexdigest()[:24]}"
        now = utc_now()
        async with async_session() as session:
            objective = await session.get(CompanyObjective, objective_id)
            if not objective:
                objective = CompanyObjective(
                    id=objective_id,
                    title=str(proposal["title"])[:240],
                    description=str(proposal.get("description") or "")[:4000],
                    status="probation",
                    priority=str(proposal.get("priority") or "medium")[:20],
                    target=proposal.get("target") or {},
                    tags=["autonomous_strategy", str(proposal.get("category") or "business")],
                    created_by=actor,
                )
                session.add(objective)
            latest = (
                await session.execute(
                    select(CompanyObjectiveRevision)
                    .where(CompanyObjectiveRevision.objective_id == objective_id)
                    .order_by(desc(CompanyObjectiveRevision.revision))
                    .limit(1)
                )
            ).scalar_one_or_none()
            target = self._bounded_target_update(
                latest.target if latest else {},
                proposal.get("target") or {},
            )
            content_hash = self._hash(
                {"objective_id": objective_id, "target": target, "model_id": model.id}
            )
            if latest and latest.rationale.endswith(content_hash):
                return {
                    **self._objective_revision_to_dict(latest),
                    "strategy_key": key,
                    "revision_id": latest.id,
                    "duplicate": True,
                }
            revision = CompanyObjectiveRevision(
                id=f"objrev_{uuid.uuid4().hex}",
                objective_id=objective_id,
                revision=(latest.revision + 1) if latest else 1,
                status="probation",
                title=str(proposal["title"])[:240],
                description=str(proposal.get("description") or "")[:4000],
                category=str(proposal.get("category") or "business")[:100],
                priority=str(proposal.get("priority") or "medium")[:20],
                target=target,
                rationale=(
                    f"Evidence-derived from company model {model.id}; Observer "
                    f"review {observer_review_id}; {content_hash}"
                ),
                evidence_ids=proposal.get("evidence_ids") or [],
                confidence=float(proposal.get("confidence") or 0),
                owner_locked=False,
                probation_until=now + timedelta(days=self.PROBATION_DAYS),
                supersedes_id=latest.id if latest else None,
                created_by=actor,
                activated_at=now,
            )
            if latest and latest.status in {"probation", "active"}:
                latest.status = "superseded"
            objective.title = revision.title
            objective.description = revision.description
            objective.status = revision.status
            objective.priority = revision.priority
            objective.target = revision.target
            objective.updated_at = now
            session.add(revision)
            await session.commit()
            return {
                **self._objective_revision_to_dict(revision),
                "strategy_key": key,
                "revision_id": revision.id,
                "duplicate": False,
            }

    async def _upsert_kpi_revision(
        self,
        proposal: dict[str, Any],
        *,
        objective_revision_ids: list[str],
        actor: str,
    ) -> dict[str, Any]:
        validation = KPIFormula.validate(proposal["formula"], proposal.get("bindings") or {})
        key = str(proposal["key"])[:120]
        now = utc_now()
        async with async_session() as session:
            definition = (
                await session.execute(
                    select(OperatingKPIDefinition).where(OperatingKPIDefinition.key == key)
                )
            ).scalar_one_or_none()
            if not definition:
                definition = OperatingKPIDefinition(
                    id=f"kpi_{uuid.uuid4().hex}",
                    key=key,
                    title=str(proposal["title"])[:240],
                    description=str(proposal.get("description") or "")[:4000],
                    unit=str(proposal.get("unit") or "count")[:40],
                    comparison=str(proposal.get("comparison") or "max")[:20],
                    target_value=float(proposal.get("target_value") or 0),
                    source="company_strategy",
                    status="probation",
                    tags=["business", "autonomous_strategy"],
                    metadata_={},
                )
                session.add(definition)
                await session.flush()
            latest = (
                await session.execute(
                    select(OperatingKPIRevision)
                    .where(OperatingKPIRevision.kpi_definition_id == definition.id)
                    .order_by(desc(OperatingKPIRevision.revision))
                    .limit(1)
                )
            ).scalar_one_or_none()
            content = {
                "formula": validation["formula"],
                "bindings": validation["bindings"],
                "target": float(proposal.get("target_value") or 0),
                "objectives": objective_revision_ids,
            }
            if latest and self._hash(self._kpi_revision_content(latest)) == self._hash(content):
                return {**self._kpi_revision_to_dict(latest), "duplicate": True}
            target = self._bounded_number(
                latest.target_value if latest else None,
                float(proposal.get("target_value") or 0),
            )
            revision = OperatingKPIRevision(
                id=f"kpirev_{uuid.uuid4().hex}",
                kpi_definition_id=definition.id,
                revision=(latest.revision + 1) if latest else 1,
                status="probation",
                formula=validation["formula"],
                measurement_bindings=validation["bindings"],
                target_value=target,
                lower_guardrail=proposal.get("lower_guardrail"),
                upper_guardrail=proposal.get("upper_guardrail"),
                objective_revision_ids=objective_revision_ids,
                evidence_ids=proposal.get("evidence_ids") or [],
                confidence=float(proposal.get("confidence") or 0),
                owner_locked=False,
                probation_until=now + timedelta(days=self.PROBATION_DAYS),
                created_by=actor,
                activated_at=now,
            )
            if latest and latest.status in {"probation", "active"}:
                latest.status = "superseded"
            definition.status = "probation"
            definition.target_value = target
            definition.updated_at = now
            session.add(revision)
            await session.commit()
            return {**self._kpi_revision_to_dict(revision), "duplicate": False}

    async def _upsert_experiment(
        self,
        proposal: dict[str, Any],
        *,
        objective_revision_id: str | None,
        namespace: str,
        actor: str,
    ) -> dict[str, Any]:
        key = str(proposal["strategy_key"])
        async with async_session() as session:
            existing = (
                await session.execute(
                    select(StrategicExperiment).where(
                        StrategicExperiment.company_namespace == namespace,
                        StrategicExperiment.created_by == f"{actor}:{key}",
                        StrategicExperiment.status.in_({"proposed", "probation", "running"}),
                    )
                )
            ).scalar_one_or_none()
            if existing:
                return {**self._experiment_to_dict(existing), "duplicate": True}
            item = StrategicExperiment(
                id=f"experiment_{uuid.uuid4().hex}",
                company_namespace=namespace,
                objective_revision_id=objective_revision_id,
                title=str(proposal["title"])[:240],
                hypothesis=str(proposal["hypothesis"])[:8000],
                status="probation",
                design=proposal.get("design") or {},
                metric_keys=proposal.get("metric_keys") or [],
                budget=proposal.get("budget") or {"usd": 0},
                risk_level=str(proposal.get("risk_level") or "low")[:20],
                evidence_ids=proposal.get("evidence_ids") or [],
                result={},
                created_by=f"{actor}:{key}"[:200],
                started_at=utc_now(),
            )
            session.add(item)
            await session.commit()
            return {**self._experiment_to_dict(item), "duplicate": False}

    async def _measurement_values(self, namespace: str) -> dict[str, float]:
        async with async_session() as session:
            model = (
                await session.execute(
                    select(CompanyModelRevision)
                    .where(
                        CompanyModelRevision.company_namespace == namespace,
                        CompanyModelRevision.status == "active",
                    )
                    .order_by(desc(CompanyModelRevision.revision))
                    .limit(1)
                )
            ).scalar_one_or_none()
            verified_segments = int(
                (
                    await session.execute(
                        select(func.count(CompanyClaim.id)).where(
                            CompanyClaim.company_namespace == namespace,
                            CompanyClaim.predicate == "customer_segment",
                            CompanyClaim.epistemic_state == "verified",
                        )
                    )
                ).scalar_one()
            )
        operational = (
            (model.model if model else {}).get("operational_measurements") or {}
        )
        erp_counts = operational.get("counts") or {}
        erp_statuses = operational.get("statuses") or {}
        projects = float(erp_counts.get("Project") or 0)
        completed_projects = float(
            sum(
                count
                for status, count in (erp_statuses.get("Project") or {}).items()
                if str(status).lower() in {"completed", "closed", "cancelled"}
            )
        )
        return {
            "company_model.unknown_count": float(len(model.unknowns) if model else 0),
            "company_claim.verified_customer_segments": float(verified_segments),
            "erpnext.project.total": projects,
            "erpnext.project.completed": completed_projects,
            "erpnext.task.open": 0.0,
            "erpnext.issue.open": 0.0,
            "erpnext.customer.active": float(erp_counts.get("Customer") or 0),
            "erpnext.opportunity.pipeline_value": 0.0,
            "erpnext.sales_invoice.outstanding": 0.0,
        }

    @staticmethod
    def _rank_work_item(item: BusinessWorkItem) -> dict[str, Any]:
        payload = item.payload or {}
        impact = payload.get("portfolio") or {}
        score = (
            float(impact.get("objective_contribution") or 0) * 0.25
            + float(impact.get("expected_value") or 0) * 0.2
            + float(impact.get("urgency") or 0) * 0.15
            + float(impact.get("confidence") or 0) * 0.15
            + float(impact.get("reversibility") or 0) * 0.1
            - float(impact.get("cost") or 0) * 0.05
            - float(impact.get("dependency_penalty") or 0) * 0.05
            - float(impact.get("risk") or 0) * 0.1
        )
        return {
            "id": item.id,
            "title": item.title,
            "status": item.status,
            "priority": item.priority,
            "risk_level": item.risk_level,
            "assigned_agent_id": item.assigned_agent_id,
            "objective_revision_id": item.objective_revision_id,
            "portfolio_score": round(score, 4),
            "portfolio_factors": impact,
            "deadline_at": item.deadline_at.isoformat() if item.deadline_at else None,
        }

    @classmethod
    def _bounded_target_update(
        cls,
        previous: dict[str, Any],
        proposed: dict[str, Any],
    ) -> dict[str, Any]:
        result = {
            key: float(value) if isinstance(value, (int, float)) else value
            for key, value in proposed.items()
        }
        for key, value in proposed.items():
            prior = previous.get(key)
            if isinstance(prior, (int, float)) and isinstance(value, (int, float)):
                result[key] = cls._bounded_number(float(prior), float(value))
        return result

    @staticmethod
    def _bounded_number(previous: float | None, proposed: float) -> float:
        if previous is None or previous == 0:
            return proposed
        delta = abs(previous) * 0.2
        return min(max(proposed, previous - delta), previous + delta)

    @staticmethod
    def _kpi_revision_content(item: OperatingKPIRevision) -> dict[str, Any]:
        return {
            "formula": item.formula,
            "bindings": item.measurement_bindings,
            "target": item.target_value,
            "objectives": item.objective_revision_ids,
        }

    @staticmethod
    def _hash(value: Any) -> str:
        payload = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _objective_revision_to_dict(item: CompanyObjectiveRevision) -> dict[str, Any]:
        return {
            "id": item.id,
            "objective_id": item.objective_id,
            "revision": item.revision,
            "status": item.status,
            "title": item.title,
            "description": item.description,
            "category": item.category,
            "priority": item.priority,
            "target": item.target,
            "rationale": item.rationale,
            "evidence_ids": item.evidence_ids,
            "confidence": item.confidence,
            "owner_locked": item.owner_locked,
            "probation_until": item.probation_until.isoformat() if item.probation_until else None,
            "supersedes_id": item.supersedes_id,
            "created_by": item.created_by,
            "created_at": item.created_at.isoformat(),
            "activated_at": item.activated_at.isoformat() if item.activated_at else None,
        }

    @staticmethod
    def _kpi_revision_to_dict(item: OperatingKPIRevision) -> dict[str, Any]:
        return {
            "id": item.id,
            "kpi_definition_id": item.kpi_definition_id,
            "revision": item.revision,
            "status": item.status,
            "formula": item.formula,
            "measurement_bindings": item.measurement_bindings,
            "target_value": item.target_value,
            "lower_guardrail": item.lower_guardrail,
            "upper_guardrail": item.upper_guardrail,
            "objective_revision_ids": item.objective_revision_ids,
            "evidence_ids": item.evidence_ids,
            "confidence": item.confidence,
            "owner_locked": item.owner_locked,
            "probation_until": item.probation_until.isoformat() if item.probation_until else None,
            "created_by": item.created_by,
            "created_at": item.created_at.isoformat(),
            "activated_at": item.activated_at.isoformat() if item.activated_at else None,
        }

    @staticmethod
    def _experiment_to_dict(item: StrategicExperiment) -> dict[str, Any]:
        return {
            "id": item.id,
            "company_namespace": item.company_namespace,
            "objective_revision_id": item.objective_revision_id,
            "title": item.title,
            "hypothesis": item.hypothesis,
            "status": item.status,
            "design": item.design,
            "metric_keys": item.metric_keys,
            "budget": item.budget,
            "risk_level": item.risk_level,
            "evidence_ids": item.evidence_ids,
            "result": item.result,
            "created_by": item.created_by,
            "created_at": item.created_at.isoformat(),
            "started_at": item.started_at.isoformat() if item.started_at else None,
            "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        }
