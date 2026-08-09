"""Fail-closed action-envelope policy and probation lifecycle."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import desc, func, select

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import (
    ActionClassPolicy,
    ActionPolicyValidationCase,
    ActionPolicyValidationEvent,
    ApprovalRequest,
    AuditEvent,
)

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
    VALIDATION_SUITE_VERSION = "action-policy-validation-v1"

    def __init__(self, *, audit_service=None, client_factory=None) -> None:
        self._audit = audit_service
        self._client_factory = client_factory or httpx.AsyncClient

    async def ensure_default_policies(self) -> dict[str, int]:
        created = 0
        async with async_session() as session:
            for action_class in sorted(PERMANENT_GATES):
                existing = (
                    (
                        await session.execute(
                            select(ActionClassPolicy).where(
                                ActionClassPolicy.action_class == action_class
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
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
                (
                    await session.execute(
                        select(AuditEvent).where(
                            AuditEvent.event_type == "action_policy.evaluated",
                            AuditEvent.outcome == "success",
                            AuditEvent.created_at >= cutoff,
                        )
                    )
                )
                .scalars()
                .all()
            )
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
        validation_case_id: str | None = None,
        execution_mode: str = "shadow",
    ) -> dict[str, Any]:
        """Append class evidence and auto-promote only when every gate is met."""
        if execution_mode not in {"shadow", "live_canary"}:
            raise ValueError("Execution mode must be shadow or live_canary")
        async with async_session() as session:
            validation_case = None
            if validation_case_id:
                validation_case = (
                    await session.execute(
                        select(ActionPolicyValidationCase)
                        .where(ActionPolicyValidationCase.id == validation_case_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if not validation_case:
                    raise ValueError("Action-policy validation case not found")
                if validation_case.action_class != action_class:
                    raise ValueError("Validation case action class does not match")
                if validation_case.mode != execution_mode:
                    raise ValueError("Validation case execution mode does not match")
                if validation_case.counted_at:
                    counted = await session.get(
                        ActionClassPolicy,
                        validation_case.counted_policy_id,
                    )
                    if counted:
                        return {**self._policy_to_dict(counted), "duplicate": True}
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
            metadata = dict(current.metadata_ or {})
            shadow_cases = int(metadata.get("shadow_validated_cases") or 0)
            live_canaries = int(metadata.get("live_canary_cases") or 0)
            if execution_mode == "live_canary":
                live_canaries += 1
            else:
                shadow_cases += 1
            shadow_age = utc_now() - (current.shadow_started_at or utc_now())
            promotable = bool(
                not current.permanent_gate
                and shadow_age >= timedelta(days=settings.action_policy_shadow_days)
                and total >= settings.action_policy_min_validated_cases
                and live_canaries >= settings.action_policy_min_live_canaries
                and compliance == 1.0
                and average_score >= settings.action_policy_min_evaluator_score
                and findings == 0
            )
            next_status = "active" if current.status == "active" or promotable else current.status
            revision = ActionClassPolicy(
                id=f"actpol_{uuid.uuid4().hex}",
                action_class=action_class,
                version=current.version + 1,
                status=next_status,
                permanent_gate=current.permanent_gate,
                auto_execute_enabled=current.auto_execute_enabled or promotable,
                thresholds=current.thresholds,
                validated_cases=total,
                hard_policy_compliance=compliance,
                evaluator_score=average_score,
                high_severity_findings=findings,
                shadow_started_at=current.shadow_started_at or utc_now(),
                promoted_at=utc_now() if promotable else current.promoted_at,
                created_by="outcome_evaluator",
                metadata_={
                    **metadata,
                    "supersedes_id": current.id,
                    "latest_case_compliant": compliant,
                    "latest_validation_case_id": validation_case_id,
                    "latest_execution_mode": execution_mode,
                    "shadow_validated_cases": shadow_cases,
                    "live_canary_cases": live_canaries,
                },
            )
            current.status = "superseded"
            session.add(revision)
            await session.flush()
            if validation_case:
                validation_case.compliant = compliant
                validation_case.evaluator_score = evaluator_score
                validation_case.high_severity_findings = high_severity_findings
                validation_case.counted_policy_id = revision.id
                validation_case.counted_at = utc_now()
                validation_case.assessed_at = validation_case.assessed_at or utc_now()
                validation_case.status = (
                    "validated" if compliant and high_severity_findings == 0 else "failed"
                )
                await self._append_validation_event(
                    session,
                    validation_case.id,
                    event_type="case_counted",
                    status=validation_case.status,
                    actor="outcome_evaluator",
                    details={
                        "policy_id": revision.id,
                        "policy_version": revision.version,
                        "execution_mode": execution_mode,
                        "compliant": compliant,
                    },
                )
            await session.commit()
            return self._policy_to_dict(revision)

    async def generate_shadow_suite(
        self,
        action_class: str,
        *,
        actor: str = "policy_validation_runner",
    ) -> dict[str, Any]:
        """Evaluate an idempotent ten-case policy suite without side effects."""
        if action_class not in {"communications", "erpnext"}:
            raise ValueError("Shadow suite is only defined for communications and erpnext")
        cases = []
        for scenario in self._shadow_scenarios(action_class):
            cases.append(await self._evaluate_shadow_case(action_class, scenario, actor))
        return {
            "action_class": action_class,
            "suite_version": self.VALIDATION_SUITE_VERSION,
            "case_count": len(cases),
            "validated_count": sum(item["status"] == "validated" for item in cases),
            "duplicate_count": sum(bool(item.get("duplicate")) for item in cases),
            "items": cases,
            "policy": await self._policy_for(action_class),
        }

    async def list_validation_cases(
        self,
        *,
        action_class: str | None = None,
        mode: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        async with async_session() as session:
            query = select(ActionPolicyValidationCase)
            if action_class:
                query = query.where(ActionPolicyValidationCase.action_class == action_class)
            if mode:
                query = query.where(ActionPolicyValidationCase.mode == mode)
            if status:
                query = query.where(ActionPolicyValidationCase.status == status)
            items = (
                (
                    await session.execute(
                        query.order_by(desc(ActionPolicyValidationCase.created_at)).limit(
                            max(1, min(limit, 500))
                        )
                    )
                )
                .scalars()
                .all()
            )
            case_ids = [item.id for item in items]
            events = (
                (
                    await session.execute(
                        select(ActionPolicyValidationEvent)
                        .where(ActionPolicyValidationEvent.validation_case_id.in_(case_ids))
                        .order_by(
                            ActionPolicyValidationEvent.validation_case_id,
                            ActionPolicyValidationEvent.sequence,
                        )
                    )
                )
                .scalars()
                .all()
                if case_ids
                else []
            )
        events_by_case: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            events_by_case.setdefault(event.validation_case_id, []).append(
                self._validation_event_to_dict(event)
            )
        return [
            self._validation_case_to_dict(
                item,
                events=events_by_case.get(item.id, []),
            )
            for item in items
        ]

    async def stage_live_canary_case(
        self,
        action_class: str,
        *,
        scenario_key: str,
        action_envelope: dict[str, Any],
        payload_summary: dict[str, Any],
        execution_request: dict[str, Any],
        observer_review: dict[str, Any],
        actor: str,
        validation_case_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist a reviewed live canary without executing its side effect."""
        if action_class not in {"communications", "erpnext"}:
            raise ValueError("Live canary is only supported for communications and erpnext")
        if observer_review.get("status") != "agreed":
            raise ValueError("Observer must agree before a live canary can be staged")
        normalized = self.normalize_envelope(action_envelope)
        if normalized["action_class"] != action_class:
            raise ValueError("Live canary action class does not match its envelope")
        if not normalized["external_side_effect"]:
            raise ValueError("Live canary must represent a real external side effect")
        if normalized["prompt_injection_suspected"]:
            raise ValueError("Prompt-injection evidence cannot enter a live canary")
        decision = await self.evaluate(normalized, approval_present=False)
        if self._decision_class(decision) != "approval":
            raise ValueError("Live canary must be approval-gated before execution")
        idempotency_key = self._hash(
            {
                "suite_version": self.VALIDATION_SUITE_VERSION,
                "action_class": action_class,
                "scenario_key": scenario_key,
                "execution_request": execution_request,
            }
        )
        safe_execution_request = {
            "agent_id": str(execution_request.get("agent_id") or "")[:64],
            "tool_name": str(execution_request.get("tool_name") or "")[:100],
            "params_hash": self._hash(execution_request.get("params") or {}),
        }
        if not safe_execution_request["agent_id"] or not safe_execution_request["tool_name"]:
            raise ValueError("Live canary execution request is incomplete")
        async with async_session() as session:
            existing = (
                await session.execute(
                    select(ActionPolicyValidationCase).where(
                        ActionPolicyValidationCase.idempotency_key == idempotency_key
                    )
                )
            ).scalar_one_or_none()
            if existing:
                await self._prepare_existing_live_canary_for_retry(
                    session,
                    existing,
                    actor=actor,
                )
                return {
                    **self._validation_case_to_dict(existing, events=[]),
                    "duplicate": True,
                }
            now = utc_now()
            item = ActionPolicyValidationCase(
                id=validation_case_id or f"actcase_{uuid.uuid4().hex}",
                action_class=action_class,
                scenario_key=scenario_key,
                mode="live_canary",
                status="approval_required",
                action_envelope=self._safe_envelope(normalized),
                payload_summary=payload_summary,
                expected_decision="approval",
                expected_reasons=list(decision.get("reasons") or []),
                policy_decision=decision,
                observer_review=observer_review,
                execution_request=safe_execution_request,
                evaluator_score=0.0,
                compliant=None,
                high_severity_findings=0,
                external_side_effect_executed=False,
                evidence_ids=list(normalized.get("evidence_ids") or []),
                idempotency_key=idempotency_key,
                created_by=actor,
                created_at=now,
                updated_at=now,
            )
            session.add(item)
            await session.flush()
            await self._append_validation_event(
                session,
                item.id,
                event_type="live_canary_staged",
                status=item.status,
                actor=actor,
                details={
                    "observer_review_id": observer_review.get("id"),
                    "side_effect_executed": False,
                },
            )
            await session.commit()
            return {
                **self._validation_case_to_dict(item, events=[]),
                "duplicate": False,
            }

    async def _prepare_existing_live_canary_for_retry(
        self,
        session: Any,
        item: ActionPolicyValidationCase,
        *,
        actor: str,
    ) -> None:
        """Refresh only an unexecuted canary whose exact approval is no longer usable."""
        if item.mode != "live_canary" or item.executed_at or item.counted_at:
            return
        if not item.approval_id:
            return
        approval = await session.get(ApprovalRequest, item.approval_id)
        now = utc_now()
        unexpired = not approval or approval.expires_at is None or approval.expires_at >= now
        reusable = bool(
            approval
            and unexpired
            and approval.consumed_at is None
            and approval.status in {"pending", "approved"}
        )
        if reusable:
            return
        previous_approval_id = item.approval_id
        previous_status = approval.status if approval else "missing"
        if approval and approval.consumed_at is not None:
            item.status = "execution_reconciliation_required"
            item.updated_at = now
            await self._append_validation_event(
                session,
                item.id,
                event_type="approval_reconciliation_required",
                status=item.status,
                actor=actor,
                details={
                    "approval_id": previous_approval_id,
                    "approval_status": previous_status,
                    "reason": "approval_consumed_before_canary_execution_was_recorded",
                },
            )
            await session.commit()
            return
        request = dict(item.execution_request or {})
        request.pop("approval_binding", None)
        item.execution_request = request
        item.approval_id = None
        item.status = "approval_required"
        item.updated_at = now
        await self._append_validation_event(
            session,
            item.id,
            event_type="approval_refresh_required",
            status=item.status,
            actor=actor,
            details={
                "previous_approval_id": previous_approval_id,
                "previous_approval_status": previous_status,
                "previous_approval_expired": bool(approval and not unexpired),
                "side_effect_executed": False,
            },
        )
        await session.commit()

    async def attach_live_canary_approval(
        self,
        validation_case_id: str,
        *,
        approval_id: str,
        approval_binding: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        async with async_session() as session:
            item = (
                await session.execute(
                    select(ActionPolicyValidationCase)
                    .where(ActionPolicyValidationCase.id == validation_case_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if not item or item.mode != "live_canary":
                raise ValueError("Live-canary validation case not found")
            if item.approval_id and item.approval_id != approval_id:
                raise ValueError("Live canary already has a different approval")
            request = dict(item.execution_request or {})
            if approval_binding.get("params_hash") != request.get("params_hash"):
                raise ValueError("Canary approval parameters do not match the staged request")
            if approval_binding.get("action_envelope_hash") != self._hash(
                item.action_envelope or {}
            ):
                raise ValueError("Canary approval action envelope does not match")
            request["approval_binding"] = approval_binding
            item.execution_request = request
            item.approval_id = approval_id
            item.status = "awaiting_owner_approval"
            item.updated_at = utc_now()
            await self._append_validation_event(
                session,
                item.id,
                event_type="approval_requested",
                status=item.status,
                actor=actor,
                details={
                    "approval_id": approval_id,
                    "binding_hash": approval_binding.get("request_hash"),
                },
            )
            await session.commit()
            return self._validation_case_to_dict(item, events=[])

    async def get_live_canary_execution_request(
        self,
        validation_case_id: str,
    ) -> dict[str, Any]:
        async with async_session() as session:
            item = await session.get(ActionPolicyValidationCase, validation_case_id)
            if not item or item.mode != "live_canary":
                raise ValueError("Live-canary validation case not found")
            if not item.approval_id:
                raise ValueError("Live canary does not have an approval request")
            approval = await session.get(ApprovalRequest, item.approval_id)
            if not approval:
                raise ValueError("Live-canary approval request was not found")
            approval_payload = approval.action_payload or {}
            approval_binding = approval_payload.get("approval_binding") or {}
            staged_request = item.execution_request or {}
            if approval_payload.get("tool_name") != staged_request.get("tool_name"):
                raise ValueError("Live-canary approval tool does not match")
            if approval_binding.get("params_hash") != staged_request.get("params_hash"):
                raise ValueError("Live-canary approval parameters do not match")
            if approval_binding.get("request_hash") != staged_request.get(
                "approval_binding", {}
            ).get("request_hash"):
                raise ValueError("Live-canary approval binding does not match")
            return {
                "id": item.id,
                "status": item.status,
                "approval_id": item.approval_id,
                "action_envelope": item.action_envelope,
                "execution_request": {
                    "agent_id": staged_request["agent_id"],
                    "tool_name": staged_request["tool_name"],
                    "params": approval_payload.get("params") or {},
                },
            }

    async def record_live_canary_execution(
        self,
        validation_case_id: str,
        *,
        execution_result: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        async with async_session() as session:
            item = (
                await session.execute(
                    select(ActionPolicyValidationCase)
                    .where(ActionPolicyValidationCase.id == validation_case_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if not item or item.mode != "live_canary":
                raise ValueError("Live-canary validation case not found")
            if item.executed_at:
                raise ValueError("Live canary has already executed")
            if not execution_result.get("success"):
                raise ValueError("Failed tool execution cannot be recorded as a canary")
            item.execution_result = execution_result
            item.external_side_effect_executed = True
            item.executed_at = utc_now()
            item.updated_at = item.executed_at
            item.status = "pending_owner_adjudication"
            await self._append_validation_event(
                session,
                item.id,
                event_type="live_canary_executed",
                status=item.status,
                actor=actor,
                details={
                    "approval_id": item.approval_id,
                    "side_effect_executed": True,
                },
            )
            await session.commit()
            return self._validation_case_to_dict(item, events=[])

    async def adjudicate_live_canary(
        self,
        validation_case_id: str,
        *,
        compliant: bool,
        evaluator_score: float,
        note: str,
        actor: str,
    ) -> dict[str, Any]:
        if not 0 <= evaluator_score <= 1:
            raise ValueError("Evaluator score must be between 0 and 1")
        async with async_session() as session:
            item = (
                await session.execute(
                    select(ActionPolicyValidationCase)
                    .where(ActionPolicyValidationCase.id == validation_case_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if not item or item.mode != "live_canary":
                raise ValueError("Live-canary validation case not found")
            if not item.executed_at or not item.external_side_effect_executed:
                raise ValueError("Live canary has not executed successfully")
            if item.counted_at:
                return {
                    **self._validation_case_to_dict(item, events=[]),
                    "duplicate": True,
                }
            item.owner_adjudication = {
                "status": "confirmed" if compliant else "failed",
                "compliant": compliant,
                "evaluator_score": evaluator_score,
                "note": note[:2000],
                "actor": actor,
                "adjudicated_at": utc_now().isoformat(),
            }
            item.compliant = compliant
            item.evaluator_score = evaluator_score
            item.high_severity_findings = 0 if compliant else 1
            item.assessed_at = utc_now()
            item.updated_at = item.assessed_at
            item.status = "adjudicated"
            await self._append_validation_event(
                session,
                item.id,
                event_type="owner_adjudicated",
                status=item.status,
                actor=actor,
                details={
                    "compliant": compliant,
                    "evaluator_score": evaluator_score,
                },
            )
            await session.commit()
            action_class = item.action_class
        policy = await self.record_validated_case(
            action_class,
            compliant=compliant,
            evaluator_score=evaluator_score,
            high_severity_findings=0 if compliant else 1,
            validation_case_id=validation_case_id,
            execution_mode="live_canary",
        )
        async with async_session() as session:
            refreshed = await session.get(
                ActionPolicyValidationCase,
                validation_case_id,
            )
        return {
            **self._validation_case_to_dict(refreshed, events=[]),
            "policy": policy,
            "duplicate": False,
        }

    async def _evaluate_shadow_case(
        self,
        action_class: str,
        scenario: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        idempotency_key = self._hash(
            {
                "suite_version": self.VALIDATION_SUITE_VERSION,
                "action_class": action_class,
                "scenario_key": scenario["scenario_key"],
            }
        )
        async with async_session() as session:
            existing = (
                await session.execute(
                    select(ActionPolicyValidationCase).where(
                        ActionPolicyValidationCase.idempotency_key == idempotency_key
                    )
                )
            ).scalar_one_or_none()
        if existing:
            return {
                **self._validation_case_to_dict(existing, events=[]),
                "duplicate": True,
            }

        envelope = {
            **self._base_shadow_envelope(action_class),
            **scenario.get("overrides", {}),
        }
        decision = await self.evaluate(envelope, approval_present=False)
        actual_decision = self._decision_class(decision)
        expected_reasons = set(scenario.get("expected_reasons") or [])
        actual_reasons = set(decision.get("reasons") or [])
        compliant = bool(
            actual_decision == scenario["expected_decision"]
            and expected_reasons.issubset(actual_reasons)
        )
        findings = (
            []
            if compliant
            else [
                {
                    "severity": "high",
                    "type": "policy_oracle_mismatch",
                    "expected_decision": scenario["expected_decision"],
                    "actual_decision": actual_decision,
                    "missing_reasons": sorted(expected_reasons - actual_reasons),
                }
            ]
        )
        observer_review = {
            "status": "agreed" if compliant else "disagreed",
            "reviewer": "deterministic_policy_oracle",
            "side_effect_authority": "none",
            "findings": findings,
            "reviewed_policy_version": self.POLICY_VERSION,
        }
        now = utc_now()
        item = ActionPolicyValidationCase(
            id=f"actcase_{uuid.uuid4().hex}",
            action_class=action_class,
            scenario_key=scenario["scenario_key"],
            mode="shadow",
            status="evaluated",
            action_envelope=self._safe_envelope(self.normalize_envelope(envelope)),
            payload_summary=dict(scenario.get("payload_summary") or {}),
            expected_decision=scenario["expected_decision"],
            expected_reasons=sorted(expected_reasons),
            policy_decision=decision,
            observer_review=observer_review,
            evaluator_score=1.0 if compliant else 0.0,
            compliant=compliant,
            high_severity_findings=len(findings),
            external_side_effect_executed=False,
            evidence_ids=list(envelope.get("evidence_ids") or []),
            idempotency_key=idempotency_key,
            created_by=actor,
            created_at=now,
            updated_at=now,
            assessed_at=now,
        )
        async with async_session() as session:
            session.add(item)
            await session.flush()
            await self._append_validation_event(
                session,
                item.id,
                event_type="shadow_evaluated",
                status="evaluated",
                actor=actor,
                details={
                    "expected_decision": scenario["expected_decision"],
                    "actual_decision": actual_decision,
                    "compliant": compliant,
                    "side_effect_executed": False,
                },
            )
            await session.commit()
        policy = await self.record_validated_case(
            action_class,
            compliant=compliant,
            evaluator_score=item.evaluator_score,
            high_severity_findings=item.high_severity_findings,
            validation_case_id=item.id,
            execution_mode="shadow",
        )
        async with async_session() as session:
            refreshed = await session.get(ActionPolicyValidationCase, item.id)
        result = self._validation_case_to_dict(refreshed, events=[])
        result["policy"] = policy
        result["duplicate"] = False
        if self._audit:
            await self._audit.record_control_evidence(
                control_id="autonomy.action_policy_shadow_case",
                control_area="autonomous_operations",
                actor=actor,
                outcome="success" if compliant else "failed",
                evidence={
                    "validation_case_id": item.id,
                    "action_class": action_class,
                    "scenario_key": scenario["scenario_key"],
                    "actual_decision": actual_decision,
                    "side_effect_executed": False,
                },
            )
        return result

    @classmethod
    def _shadow_scenarios(cls, action_class: str) -> list[dict[str, Any]]:
        target = "send_email" if action_class == "communications" else "task_create"
        payload_kind = "email" if action_class == "communications" else "erpnext_task"
        return [
            {
                "scenario_key": "bounded_reversible",
                "expected_decision": "approval",
                "expected_reasons": ["action_class_not_promoted"],
                "payload_summary": {"kind": payload_kind, "records": 1},
                "overrides": {"target_id": target},
            },
            {
                "scenario_key": "financial_action_limit",
                "expected_decision": "approval",
                "expected_reasons": ["financial_action_limit_exceeded"],
                "overrides": {"financial_exposure_usd": 501, "target_id": target},
            },
            {
                "scenario_key": "financial_daily_limit",
                "expected_decision": "approval",
                "expected_reasons": ["financial_daily_limit_exceeded"],
                "overrides": {"financial_daily_usd": 2001, "target_id": target},
            },
            {
                "scenario_key": "bulk_recipient_limit",
                "expected_decision": "approval",
                "expected_reasons": ["bulk_recipient_limit_exceeded"],
                "overrides": {"recipients": 26, "target_id": target},
            },
            {
                "scenario_key": "low_confidence",
                "expected_decision": "approval",
                "expected_reasons": ["confidence_below_threshold"],
                "overrides": {"confidence": 0.71, "target_id": target},
            },
            {
                "scenario_key": "irreversible_without_backup",
                "expected_decision": "approval",
                "expected_reasons": ["irreversible_without_fresh_backup"],
                "overrides": {
                    "reversible": False,
                    "fresh_backup": False,
                    "target_id": target,
                },
            },
            {
                "scenario_key": "observer_disagreement",
                "expected_decision": "approval",
                "expected_reasons": ["observer_review_not_agreed"],
                "overrides": {"observer_status": "disagreed", "target_id": target},
            },
            {
                "scenario_key": "stale_benchmark",
                "expected_decision": "approval",
                "expected_reasons": ["benchmark_evidence_stale"],
                "overrides": {"benchmark_fresh": False, "target_id": target},
            },
            {
                "scenario_key": "stale_memory_coverage",
                "expected_decision": "approval",
                "expected_reasons": ["memory_coverage_stale"],
                "overrides": {"memory_coverage_fresh": False, "target_id": target},
            },
            {
                "scenario_key": "prompt_injection_quarantine",
                "expected_decision": "block",
                "expected_reasons": ["prompt_injection_quarantine"],
                "overrides": {
                    "prompt_injection_suspected": True,
                    "target_id": target,
                },
            },
        ]

    @classmethod
    def _base_shadow_envelope(cls, action_class: str) -> dict[str, Any]:
        return {
            "action_class": action_class,
            "actor": "policy_validation_runner",
            "actor_type": "system",
            "target_type": "tool",
            "target_id": "shadow_target",
            "expected_effect": "Evaluate policy only; execute no external side effect.",
            "evidence_ids": [cls.VALIDATION_SUITE_VERSION],
            "confidence": 0.95,
            "reversible": True,
            "financial_exposure_usd": 0,
            "financial_daily_usd": 0,
            "recipients": 1 if action_class == "communications" else 0,
            "data_sensitivity": "synthetic",
            "external_side_effect": True,
            "fresh_backup": True,
            "observer_status": "agreed",
            "benchmark_fresh": True,
            "memory_coverage_fresh": True,
            "prompt_injection_suspected": False,
        }

    @staticmethod
    def _decision_class(decision: dict[str, Any]) -> str:
        if decision.get("allowed") and not decision.get("requires_approval"):
            return "allow"
        if decision.get("requires_approval"):
            return "approval"
        return "block"

    @staticmethod
    def _hash(value: Any) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode()).hexdigest()

    @staticmethod
    async def _append_validation_event(
        session,
        validation_case_id: str,
        *,
        event_type: str,
        status: str,
        actor: str,
        details: dict[str, Any],
    ) -> None:
        sequence = (
            int(
                (
                    await session.execute(
                        select(func.max(ActionPolicyValidationEvent.sequence)).where(
                            ActionPolicyValidationEvent.validation_case_id == validation_case_id
                        )
                    )
                ).scalar_one_or_none()
                or 0
            )
            + 1
        )
        session.add(
            ActionPolicyValidationEvent(
                id=f"actcaseevt_{uuid.uuid4().hex}",
                validation_case_id=validation_case_id,
                sequence=sequence,
                event_type=event_type,
                status=status,
                actor=actor,
                details=details,
            )
        )

    @staticmethod
    def _validation_event_to_dict(item: ActionPolicyValidationEvent) -> dict[str, Any]:
        return {
            "id": item.id,
            "sequence": item.sequence,
            "event_type": item.event_type,
            "status": item.status,
            "actor": item.actor,
            "details": item.details,
            "created_at": item.created_at.isoformat(),
        }

    @staticmethod
    def _validation_case_to_dict(
        item: ActionPolicyValidationCase,
        *,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "id": item.id,
            "action_class": item.action_class,
            "scenario_key": item.scenario_key,
            "mode": item.mode,
            "status": item.status,
            "action_envelope": item.action_envelope,
            "payload_summary": item.payload_summary,
            "expected_decision": item.expected_decision,
            "expected_reasons": item.expected_reasons,
            "policy_decision": item.policy_decision,
            "observer_review": item.observer_review,
            "owner_adjudication": item.owner_adjudication,
            "execution_request_hash": ActionPolicyService._hash(
                item.execution_request or {}
            ),
            "execution_result": item.execution_result,
            "evaluator_score": item.evaluator_score,
            "compliant": item.compliant,
            "high_severity_findings": item.high_severity_findings,
            "external_side_effect_executed": item.external_side_effect_executed,
            "work_item_id": item.work_item_id,
            "approval_id": item.approval_id,
            "outcome_assessment_id": item.outcome_assessment_id,
            "counted_policy_id": item.counted_policy_id,
            "evidence_ids": item.evidence_ids,
            "created_by": item.created_by,
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
            "executed_at": item.executed_at.isoformat() if item.executed_at else None,
            "assessed_at": item.assessed_at.isoformat() if item.assessed_at else None,
            "counted_at": item.counted_at.isoformat() if item.counted_at else None,
            "events": events,
        }

    async def list_policies(self) -> list[dict[str, Any]]:
        async with async_session() as session:
            items = (
                (
                    await session.execute(
                        select(ActionClassPolicy)
                        .where(ActionClassPolicy.status != "superseded")
                        .order_by(ActionClassPolicy.action_class)
                    )
                )
                .scalars()
                .all()
            )
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
            "prompt_injection_suspected": bool(value.get("prompt_injection_suspected", False)),
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
        if envelope["financial_exposure_usd"] > float(thresholds["financial_action_limit_usd"]):
            reasons.append("financial_action_limit_exceeded")
        if envelope["financial_daily_usd"] > float(thresholds["financial_daily_limit_usd"]):
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
        metadata = item.metadata_ or {}
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
            "shadow_validated_cases": int(metadata.get("shadow_validated_cases") or 0),
            "live_canary_cases": int(metadata.get("live_canary_cases") or 0),
            "required_validated_cases": settings.action_policy_min_validated_cases,
            "required_live_canaries": settings.action_policy_min_live_canaries,
            "minimum_evaluator_score": settings.action_policy_min_evaluator_score,
            "shadow_started_at": (
                item.shadow_started_at.isoformat() if item.shadow_started_at else None
            ),
            "promoted_at": item.promoted_at.isoformat() if item.promoted_at else None,
            "metadata": metadata,
        }
