"""Fail-closed action-envelope policy and probation lifecycle."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import desc, select

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import ActionClassPolicy, AuditEvent

PERMANENT_GATES = {
    "contract_commitment",
    "credential_change",
    "destructive_deletion",
    "legal_filing",
    "payment",
    "payroll",
    "permission_change",
    "production_deployment",
    "tax_filing",
}


class ActionPolicyService:
    """Evaluate complete action envelopes through OPA and durable class policy."""

    POLICY_VERSION = "action-envelope-v3"

    def __init__(self, *, audit_service=None, client_factory=None) -> None:
        self._audit = audit_service
        self._client_factory = client_factory or httpx.AsyncClient

    async def ensure_default_policies(self) -> dict[str, int]:
        created = 0
        async with async_session() as session:
            for action_class in sorted(PERMANENT_GATES):
                existing = (
                    await session.execute(
                        select(ActionClassPolicy).where(
                            ActionClassPolicy.action_class == action_class
                        )
                    )
                ).scalars().first()
                if existing:
                    continue
                session.add(
                    ActionClassPolicy(
                        id=f"actpol_{uuid.uuid4().hex}",
                        action_class=action_class,
                        version=1,
                        status="permanent_gate",
                        permanent_gate=True,
                        auto_execute_enabled=False,
                        thresholds=self.default_thresholds(),
                        created_by="policy_engine",
                        metadata_={"policy_version": self.POLICY_VERSION},
                    )
                )
                created += 1
            await session.commit()
        return {"created": created, "permanent_gates": len(PERMANENT_GATES)}

    async def evaluate(
        self,
        envelope: dict[str, Any],
        *,
        approval_present: bool = False,
    ) -> dict[str, Any]:
        normalized = self.normalize_envelope(envelope)
        recorded_daily_exposure = await self._daily_financial_exposure()
        normalized["financial_daily_usd"] = max(
            normalized["financial_daily_usd"],
            recorded_daily_exposure + normalized["financial_exposure_usd"],
        )
        policy = await self._policy_for(normalized["action_class"])
        local_reasons = self._hard_gate_reasons(normalized, policy)
        opa = await self._opa_decision(
            {
                **normalized,
                "approval_present": bool(approval_present),
                "hard_gate_reasons": local_reasons,
                "class_policy": policy,
                "policy_version": self.POLICY_VERSION,
            }
        )
        if opa is None:
            decision = {
                "allowed": False,
                "requires_approval": False,
                "reasons": ["opa_unavailable_fail_closed"],
                "source": "fail_closed",
            }
        else:
            decision = {
                "allowed": bool(opa.get("allowed")),
                "requires_approval": bool(opa.get("requires_approval")),
                "reasons": list(opa.get("reasons") or local_reasons),
                "source": "opa",
            }
        decision.update(
            {
                "policy_version": self.POLICY_VERSION,
                "action_class": normalized["action_class"],
                "class_status": policy["status"],
                "permanent_gate": policy["permanent_gate"],
                "envelope": self._safe_envelope(normalized),
            }
        )
        if self._audit:
            await self._audit.record(
                event_type="action_policy.evaluated",
                actor=normalized["actor"],
                actor_type=normalized["actor_type"],
                resource_type="action_envelope",
                resource_id=normalized["target_id"],
                action=normalized["action_class"],
                outcome="success" if decision["allowed"] else "blocked",
                metadata={
                    "decision": decision,
                    "target_type": normalized["target_type"],
                },
            )
        return decision

    @staticmethod
    async def _daily_financial_exposure() -> float:
        cutoff = utc_now() - timedelta(hours=24)
        async with async_session() as session:
            events = (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type == "action_policy.evaluated",
                        AuditEvent.outcome == "success",
                        AuditEvent.created_at >= cutoff,
                    )
                )
            ).scalars().all()
        total = 0.0
        for event in events:
            decision = (event.metadata_ or {}).get("decision") or {}
            safe_envelope = decision.get("envelope") or {}
            try:
                total += max(0.0, float(safe_envelope.get("financial_exposure_usd", 0)))
            except (TypeError, ValueError):
                continue
        return total

    async def record_validated_case(
        self,
        action_class: str,
        *,
        compliant: bool,
        evaluator_score: float,
        high_severity_findings: int = 0,
    ) -> dict[str, Any]:
        """Append class evidence and auto-promote only when every gate is met."""
        async with async_session() as session:
            current = await self._policy_model(session, action_class)
            if not current:
                current = ActionClassPolicy(
                    id=f"actpol_{uuid.uuid4().hex}",
                    action_class=action_class,
                    version=1,
                    status="shadow",
                    permanent_gate=action_class in PERMANENT_GATES,
                    auto_execute_enabled=False,
                    thresholds=self.default_thresholds(),
                    shadow_started_at=utc_now(),
                    metadata_={"policy_version": self.POLICY_VERSION},
                )
                session.add(current)
                await session.flush()
            total = current.validated_cases + 1
            prior_compliant = round(current.hard_policy_compliance * current.validated_cases)
            compliance = (prior_compliant + int(compliant)) / total
            average_score = (
                current.evaluator_score * current.validated_cases + evaluator_score
            ) / total
            findings = current.high_severity_findings + high_severity_findings
            shadow_age = utc_now() - (current.shadow_started_at or utc_now())
            promotable = bool(
                not current.permanent_gate
                and shadow_age >= timedelta(days=settings.action_policy_shadow_days)
                and total >= settings.action_policy_min_validated_cases
                and compliance == 1.0
                and average_score >= settings.action_policy_min_evaluator_score
                and findings == 0
            )
            revision = ActionClassPolicy(
                id=f"actpol_{uuid.uuid4().hex}",
                action_class=action_class,
                version=current.version + 1,
                status="active" if promotable else current.status,
                permanent_gate=current.permanent_gate,
                auto_execute_enabled=promotable,
                thresholds=current.thresholds,
                validated_cases=total,
                hard_policy_compliance=compliance,
                evaluator_score=average_score,
                high_severity_findings=findings,
                shadow_started_at=current.shadow_started_at or utc_now(),
                promoted_at=utc_now() if promotable else current.promoted_at,
                created_by="outcome_evaluator",
                metadata_={
                    **(current.metadata_ or {}),
                    "supersedes_id": current.id,
                    "latest_case_compliant": compliant,
                },
            )
            current.status = "superseded"
            session.add(revision)
            await session.commit()
            return self._policy_to_dict(revision)

    async def list_policies(self) -> list[dict[str, Any]]:
        async with async_session() as session:
            items = (
                await session.execute(
                    select(ActionClassPolicy)
                    .where(ActionClassPolicy.status != "superseded")
                    .order_by(ActionClassPolicy.action_class)
                )
            ).scalars().all()
            return [self._policy_to_dict(item) for item in items]

    async def _policy_for(self, action_class: str) -> dict[str, Any]:
        async with async_session() as session:
            model = await self._policy_model(session, action_class)
            if not model:
                model = ActionClassPolicy(
                    id=f"actpol_{uuid.uuid4().hex}",
                    action_class=action_class,
                    version=1,
                    status="shadow",
                    permanent_gate=action_class in PERMANENT_GATES,
                    auto_execute_enabled=False,
                    thresholds=self.default_thresholds(),
                    shadow_started_at=utc_now(),
                    created_by="policy_engine",
                    metadata_={"policy_version": self.POLICY_VERSION},
                )
                session.add(model)
                await session.commit()
            return self._policy_to_dict(model)

    @staticmethod
    async def _policy_model(session, action_class: str):
        return (
            await session.execute(
                select(ActionClassPolicy)
                .where(
                    ActionClassPolicy.action_class == action_class,
                    ActionClassPolicy.status != "superseded",
                )
                .order_by(desc(ActionClassPolicy.version))
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _opa_decision(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            async with self._client_factory() as client:
                response = await client.post(
                    f"{settings.opa_api_url}/v1/data/cyberteam/action/decision",
                    json={"input": payload},
                    timeout=2.0,
                )
            if response.status_code != 200:
                return None
            result = response.json().get("result")
            return result if isinstance(result, dict) else None
        except Exception:  # noqa: BLE001 - policy dependency must fail closed.
            return None

    @staticmethod
    def normalize_envelope(value: dict[str, Any]) -> dict[str, Any]:
        required = {
            "action_class",
            "actor",
            "actor_type",
            "target_type",
            "target_id",
            "expected_effect",
        }
        missing = sorted(required - set(value))
        if missing:
            raise ValueError(f"Action envelope missing: {', '.join(missing)}")
        return {
            "action_class": str(value["action_class"])[:120],
            "actor": str(value["actor"])[:200],
            "actor_type": str(value["actor_type"])[:40],
            "target_type": str(value["target_type"])[:100],
            "target_id": str(value["target_id"])[:240],
            "expected_effect": str(value["expected_effect"])[:2000],
            "evidence_ids": list(value.get("evidence_ids") or [])[:100],
            "confidence": float(value.get("confidence", 0.0)),
            "reversible": bool(value.get("reversible", False)),
            "financial_exposure_usd": float(value.get("financial_exposure_usd", 0.0)),
            "financial_daily_usd": float(value.get("financial_daily_usd", 0.0)),
            "recipients": int(value.get("recipients", 0)),
            "data_sensitivity": str(value.get("data_sensitivity", "internal"))[:40],
            "external_side_effect": bool(value.get("external_side_effect", False)),
            "fresh_backup": bool(value.get("fresh_backup", False)),
            "observer_status": str(value.get("observer_status", "not_reviewed"))[:40],
            "benchmark_fresh": bool(value.get("benchmark_fresh", False)),
            "memory_coverage_fresh": bool(value.get("memory_coverage_fresh", False)),
            "prompt_injection_suspected": bool(
                value.get("prompt_injection_suspected", False)
            ),
        }

    def _hard_gate_reasons(
        self,
        envelope: dict[str, Any],
        policy: dict[str, Any],
    ) -> list[str]:
        thresholds = policy.get("thresholds") or self.default_thresholds()
        reasons = []
        if policy["permanent_gate"]:
            reasons.append("permanent_owner_gate")
        if envelope["prompt_injection_suspected"]:
            reasons.append("prompt_injection_quarantine")
        if envelope["confidence"] < float(thresholds["min_confidence"]):
            reasons.append("confidence_below_threshold")
        if envelope["financial_exposure_usd"] > float(
            thresholds["financial_action_limit_usd"]
        ):
            reasons.append("financial_action_limit_exceeded")
        if envelope["financial_daily_usd"] > float(
            thresholds["financial_daily_limit_usd"]
        ):
            reasons.append("financial_daily_limit_exceeded")
        if envelope["recipients"] > int(thresholds["bulk_recipient_daily_limit"]):
            reasons.append("bulk_recipient_limit_exceeded")
        if not envelope["reversible"] and not envelope["fresh_backup"]:
            reasons.append("irreversible_without_fresh_backup")
        if envelope["observer_status"] != "agreed":
            reasons.append("observer_review_not_agreed")
        if not envelope["benchmark_fresh"]:
            reasons.append("benchmark_evidence_stale")
        if not envelope["memory_coverage_fresh"]:
            reasons.append("memory_coverage_stale")
        if envelope["external_side_effect"] and not policy["auto_execute_enabled"]:
            reasons.append("action_class_not_promoted")
        return reasons

    @staticmethod
    def default_thresholds() -> dict[str, Any]:
        return {
            "min_confidence": settings.governor_min_confidence,
            "financial_action_limit_usd": settings.governor_financial_action_limit_usd,
            "financial_daily_limit_usd": settings.governor_financial_daily_limit_usd,
            "bulk_recipient_daily_limit": settings.governor_bulk_recipient_daily_limit,
        }

    @staticmethod
    def _safe_envelope(value: dict[str, Any]) -> dict[str, Any]:
        return {key: item for key, item in value.items() if key != "payload"}

    @staticmethod
    def _policy_to_dict(item: ActionClassPolicy) -> dict[str, Any]:
        return {
            "id": item.id,
            "action_class": item.action_class,
            "version": item.version,
            "status": item.status,
            "permanent_gate": item.permanent_gate,
            "auto_execute_enabled": item.auto_execute_enabled,
            "thresholds": item.thresholds,
            "validated_cases": item.validated_cases,
            "hard_policy_compliance": item.hard_policy_compliance,
            "evaluator_score": item.evaluator_score,
            "high_severity_findings": item.high_severity_findings,
            "shadow_started_at": (
                item.shadow_started_at.isoformat() if item.shadow_started_at else None
            ),
            "promoted_at": item.promoted_at.isoformat() if item.promoted_at else None,
            "metadata": item.metadata_,
        }
