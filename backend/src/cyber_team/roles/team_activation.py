"""Safe activation of company-context role backlog into an operating AI team."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import desc, select

from cyber_team.agents.manager import AgentManager, slug_id
from cyber_team.audit.service import AuditService
from cyber_team.clock import utc_now
from cyber_team.db import async_session
from cyber_team.db.models import (
    Agent,
    AgentCapabilityGrant,
    CompanyContextSnapshot,
    RoleGap,
    TeamActivationRun,
)
from cyber_team.tools.registry import ToolRegistry


class TeamActivationService:
    """Turns recommended role gaps into safe baseline agents and explicit grants."""

    CORE_BASELINE_TOOL_CANDIDATES = (
        "memory_recall",
        "memory_remember",
        "approval_request",
        "company_profile_read",
        "knowledge_query",
    )

    def __init__(
        self,
        *,
        agent_manager: AgentManager,
        tool_registry: ToolRegistry,
        audit_service: AuditService | None = None,
    ) -> None:
        self._agent_manager = agent_manager
        self._tool_registry = tool_registry
        self._audit = audit_service

    async def run_activation(
        self,
        *,
        actor: str,
        dry_run: bool = False,
        apply_safe_roles: bool = True,
        request_high_risk_grants: bool = True,
        source_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        started_at = utc_now()
        snapshot = await self._resolve_snapshot(source_snapshot_id)
        run_id = f"teamact_{uuid.uuid4().hex[:12]}"
        company_profile = snapshot.normalized_profile if snapshot else {}
        company_namespace = (
            snapshot.company_namespace
            if snapshot
            else company_profile.get("company_namespace") or "company:default"
        )

        await self._create_run(
            run_id=run_id,
            snapshot=snapshot,
            actor=actor,
            dry_run=dry_run,
            apply_safe_roles=apply_safe_roles,
            request_high_risk_grants=request_high_risk_grants,
            started_at=started_at,
        )

        counts = {
            "gaps_reviewed": 0,
            "agents_created": 0,
            "agents_existing": 0,
            "safe_grants_active": 0,
            "grants_pending_approval": 0,
            "grants_configuration_required": 0,
            "grants_blocked": 0,
            "approvals_requested": 0,
            "safe_gaps_resolved": 0,
            "errors": 0,
        }
        activated: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        try:
            backlog = await self._agent_manager.summarize_role_backlog(
                statuses=["open", "proposed"],
                source_type="company_context_snapshot",
                limit=500,
            )
            for item in backlog["items"]:
                counts["gaps_reviewed"] += 1
                try:
                    result = await self._activate_gap(
                        item,
                        company_profile=company_profile,
                        company_namespace=company_namespace,
                        actor=actor,
                        dry_run=dry_run,
                        apply_safe_roles=apply_safe_roles,
                        request_high_risk_grants=request_high_risk_grants,
                    )
                    activated.append(result)
                    for key in (
                        "agents_created",
                        "agents_existing",
                        "safe_grants_active",
                        "grants_pending_approval",
                        "grants_configuration_required",
                        "grants_blocked",
                        "approvals_requested",
                        "safe_gaps_resolved",
                    ):
                        counts[key] += int(result.get("counts", {}).get(key, 0))
                except Exception as exc:  # noqa: BLE001 - one bad gap must not abort activation.
                    counts["errors"] += 1
                    errors.append(
                        {
                            "gap_id": item.get("gap_id"),
                            "title": item.get("title"),
                            "error": str(exc),
                        }
                    )

            status = "dry_run" if dry_run else ("degraded" if errors else "completed")
            response = {
                "id": run_id,
                "status": status,
                "dry_run": dry_run,
                "apply_safe_roles": apply_safe_roles,
                "request_high_risk_grants": request_high_risk_grants,
                "source_snapshot_id": snapshot.id if snapshot else None,
                "source_hash": snapshot.source_hash if snapshot else None,
                "company_namespace": company_namespace,
                "counts": counts,
                "activated": activated,
                "errors": errors,
                "started_at": started_at.isoformat(),
                "completed_at": utc_now().isoformat(),
            }
            await self._finish_run(run_id, response)
            await self._record_audit(response, actor=actor)
            return response
        except Exception as exc:
            response = {
                "id": run_id,
                "status": "failed",
                "dry_run": dry_run,
                "apply_safe_roles": apply_safe_roles,
                "request_high_risk_grants": request_high_risk_grants,
                "source_snapshot_id": snapshot.id if snapshot else None,
                "source_hash": snapshot.source_hash if snapshot else None,
                "company_namespace": company_namespace,
                "counts": counts,
                "activated": activated,
                "errors": [*errors, {"error": str(exc)}],
                "started_at": started_at.isoformat(),
                "completed_at": utc_now().isoformat(),
            }
            await self._finish_run(run_id, response)
            await self._record_audit(response, actor=actor)
            raise

    async def latest_run(self) -> dict[str, Any] | None:
        async with async_session() as session:
            result = await session.execute(
                select(TeamActivationRun).order_by(desc(TeamActivationRun.started_at)).limit(1)
            )
            run = result.scalar_one_or_none()
            return self._run_to_dict(run) if run else None

    async def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 100))
        async with async_session() as session:
            result = await session.execute(
                select(TeamActivationRun)
                .order_by(desc(TeamActivationRun.started_at))
                .limit(safe_limit)
            )
            return [self._run_to_dict(run) for run in result.scalars().all()]

    async def coverage_summary(self) -> dict[str, Any]:
        latest = await self.latest_run()
        async with async_session() as session:
            agent_count = (
                await session.execute(select(Agent).where(Agent.status != "deleted"))
            ).scalars().all()
            active_grants = (
                await session.execute(
                    select(AgentCapabilityGrant).where(
                        AgentCapabilityGrant.state == "active"
                    )
                )
            ).scalars().all()
            pending_grants = (
                await session.execute(
                    select(AgentCapabilityGrant).where(
                        AgentCapabilityGrant.state.in_(
                            ["pending_approval", "configuration_required", "blocked"]
                        )
                    )
                )
            ).scalars().all()
        return {
            "status": self._coverage_status(latest),
            "latest_run": latest,
            "active_agent_count": len(agent_count),
            "active_grant_count": len(active_grants),
            "pending_or_blocked_grant_count": len(pending_grants),
            "blocking": latest is None or latest.get("status") in {"failed", "degraded"},
        }

    async def list_agent_grants(self, agent_id: str) -> list[dict[str, Any]]:
        async with async_session() as session:
            result = await session.execute(
                select(AgentCapabilityGrant)
                .where(AgentCapabilityGrant.agent_id == agent_id)
                .order_by(AgentCapabilityGrant.tool_name.asc())
            )
            return [self._grant_to_dict(grant) for grant in result.scalars().all()]

    async def revoke_grant(
        self,
        *,
        grant_id: str,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        async with async_session() as session:
            result = await session.execute(
                select(AgentCapabilityGrant).where(AgentCapabilityGrant.id == grant_id)
            )
            grant = result.scalar_one_or_none()
            if not grant:
                raise ValueError(f"Capability grant {grant_id} not found")
            grant.state = "revoked"
            grant.reason = reason or grant.reason
            grant.revoked_at = utc_now()
            grant.updated_at = utc_now()
            await session.commit()
            response = self._grant_to_dict(grant)
        if self._audit:
            await self._audit.record(
                event_type="agent_capability_grant.revoked",
                actor=actor,
                actor_type="user",
                resource_type="agent_capability_grant",
                resource_id=grant_id,
                action="revoke",
                metadata={"agent_id": response["agent_id"], "tool_name": response["tool_name"]},
            )
        return response

    async def _activate_gap(
        self,
        item: dict[str, Any],
        *,
        company_profile: dict[str, Any],
        company_namespace: str,
        actor: str,
        dry_run: bool,
        apply_safe_roles: bool,
        request_high_risk_grants: bool,
    ) -> dict[str, Any]:
        gap_id = item["gap_id"]
        gap = await self._agent_manager.get_role_gap(gap_id)
        if not gap:
            raise ValueError(f"Role gap {gap_id} not found")
        if not gap.get("proposed_role"):
            gap = await self._agent_manager.propose_role_for_gap(gap_id, company_profile)
        manifest_payload = (gap.get("proposed_role") or {}).get("manifest_payload") or {}
        if not manifest_payload:
            raise ValueError(f"Role gap {gap_id} has no manifest payload")

        requested_tools = self._unique(
            [
                *list(gap.get("requested_tools") or []),
                *list(manifest_payload.get("default_tools") or []),
            ]
        )
        resolved_tools, unsupported_tools = self._agent_manager._resolve_tool_names(
            requested_tools
        )
        classified = self._classify_tools(resolved_tools, unsupported_tools)
        safe_tools = self._baseline_tools(classified["safe"])
        needs_baseline_variant = bool(classified["pending"] or classified["blocked"])
        activation_manifest = self._activation_manifest_payload(
            manifest_payload,
            gap=gap,
            safe_tools=safe_tools,
            baseline_variant=needs_baseline_variant,
            company_namespace=company_namespace,
        )
        agent_id = slug_id(activation_manifest["name"])
        result = {
            "gap_id": gap_id,
            "title": gap.get("title"),
            "source_snapshot_id": (gap.get("context") or {}).get("snapshot_id"),
            "baseline_variant": needs_baseline_variant,
            "manifest_id": slug_id(activation_manifest["name"]),
            "agent_id": agent_id,
            "safe_tools": safe_tools,
            "pending_tools": classified["pending"],
            "blocked_tools": classified["blocked"],
            "unsupported_tools": unsupported_tools,
            "approval_id": None,
            "counts": {
                "agents_created": 0,
                "agents_existing": 0,
                "safe_grants_active": 0,
                "grants_pending_approval": 0,
                "grants_configuration_required": 0,
                "grants_blocked": 0,
                "approvals_requested": 0,
                "safe_gaps_resolved": 0,
            },
        }
        if dry_run:
            return result

        if not apply_safe_roles:
            await self._record_gap_activation(
                gap_id,
                {
                    "activation_state": "planned",
                    "planned_agent_id": agent_id,
                    "planned_manifest_id": result["manifest_id"],
                    "safe_tools": safe_tools,
                    "pending_tools": classified["pending"],
                    "blocked_tools": classified["blocked"],
                },
                resolve_gap=False,
            )
            return result

        existing_agent = await self._agent_manager.get_agent(agent_id)
        manifest = await self._ensure_manifest(activation_manifest)
        agent = await self._agent_manager.instantiate_role(
            manifest["id"],
            {
                **company_profile,
                "company_namespace": company_namespace,
                "provisioned_by": "team_activation",
                "role_gap_id": gap_id,
                "source_snapshot_id": (gap.get("context") or {}).get("snapshot_id"),
                "source_hash": (gap.get("context") or {}).get("source_hash"),
                "activation_mode": "baseline_safe" if needs_baseline_variant else "safe_full",
                "deferred_tools": [
                    item["tool_name"]
                    for item in [*classified["pending"], *classified["blocked"]]
                ],
            },
        )
        result["agent_id"] = agent["id"]
        result["manifest_id"] = manifest["id"]
        if existing_agent:
            result["counts"]["agents_existing"] = 1
        else:
            result["counts"]["agents_created"] = 1

        for tool_name in safe_tools:
            grant = await self._upsert_grant(
                agent_id=agent["id"],
                role_gap_id=gap_id,
                tool_name=tool_name,
                state="active",
                requested_by=actor,
                reason="Safe baseline team activation grants non-side-effect capability.",
            )
            if grant["state"] == "active":
                result["counts"]["safe_grants_active"] += 1

        approval_id = await self._maybe_request_role_gap_approval(
            gap=gap,
            manifest_payload=manifest_payload,
            classified=classified,
            actor=actor,
            request_high_risk_grants=request_high_risk_grants,
        )
        result["approval_id"] = approval_id
        if approval_id:
            result["counts"]["approvals_requested"] += 1

        for pending in classified["pending"]:
            grant = await self._upsert_grant(
                agent_id=agent["id"],
                role_gap_id=gap_id,
                tool_name=pending["tool_name"],
                state="pending_approval",
                requested_by=actor,
                reason=pending["reason"],
                approval_id=approval_id,
            )
            if grant["state"] == "pending_approval":
                result["counts"]["grants_pending_approval"] += 1
        for blocked in classified["blocked"]:
            state = (
                "configuration_required"
                if blocked.get("state") == "configuration_required"
                else "blocked"
            )
            grant = await self._upsert_grant(
                agent_id=agent["id"],
                role_gap_id=gap_id,
                tool_name=blocked["tool_name"],
                state=state,
                requested_by=actor,
                reason=blocked["reason"],
            )
            if state == "configuration_required":
                result["counts"]["grants_configuration_required"] += 1
            else:
                result["counts"]["grants_blocked"] += 1

        resolve_gap = not classified["pending"] and not classified["blocked"]
        await self._record_gap_activation(
            gap_id,
            {
                "activation_state": "safe_full" if resolve_gap else "baseline_created",
                "activation_agent_id": agent["id"],
                "activation_manifest_id": manifest["id"],
                "activation_safe_tools": safe_tools,
                "activation_pending_tools": classified["pending"],
                "activation_blocked_tools": classified["blocked"],
                "activation_approval_id": approval_id,
                "activated_at": utc_now().isoformat(),
            },
            resolve_gap=resolve_gap,
        )
        if resolve_gap:
            result["counts"]["safe_gaps_resolved"] = 1
        return result

    async def _resolve_snapshot(
        self,
        source_snapshot_id: str | None,
    ) -> CompanyContextSnapshot | None:
        async with async_session() as session:
            query = select(CompanyContextSnapshot)
            if source_snapshot_id:
                query = query.where(CompanyContextSnapshot.id == source_snapshot_id)
            else:
                query = query.where(CompanyContextSnapshot.status.in_(["active", "created"]))
                query = query.order_by(desc(CompanyContextSnapshot.created_at))
            result = await session.execute(query.limit(1))
            return result.scalar_one_or_none()

    async def _ensure_manifest(self, manifest_payload: dict[str, Any]) -> dict[str, Any]:
        manifest_id = slug_id(manifest_payload["name"])
        existing = await self._agent_manager.get_role_manifest(manifest_id)
        if existing:
            return existing
        return await self._agent_manager.create_role_manifest(
            self._agent_manager._object_from_dict(manifest_payload)
        )

    def _classify_tools(
        self,
        resolved_tools: list[str],
        unsupported_tools: list[str],
    ) -> dict[str, list[dict[str, Any]] | list[str]]:
        safe: list[str] = []
        pending: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        for tool_name in resolved_tools:
            readiness = self._tool_registry.get_tool_readiness(tool_name)
            tool = self._tool_registry.get_tool(tool_name)
            reason = readiness["readiness_reason"]
            if not readiness["executable"]:
                blocked.append(
                    {
                        "tool_name": tool_name,
                        "state": readiness["state"],
                        "reason": reason,
                        "side_effects": readiness["side_effects"],
                        "requires_configuration": readiness["requires_configuration"],
                    }
                )
                continue
            if self._is_safe_auto_tool(tool_name, readiness):
                safe.append(tool_name)
                continue
            pending.append(
                {
                    "tool_name": tool_name,
                    "state": readiness["state"],
                    "reason": (
                        "Tool is live but requires owner approval or is outside "
                        "safe auto-activation policy."
                    ),
                    "risk_level": tool.risk_level if tool else "unknown",
                    "side_effects": readiness["side_effects"],
                    "requires_configuration": readiness["requires_configuration"],
                }
            )
        for tool_name in unsupported_tools:
            blocked.append(
                {
                    "tool_name": tool_name,
                    "state": "unavailable",
                    "reason": f"Tool not found: {tool_name}",
                    "side_effects": False,
                    "requires_configuration": False,
                }
            )
        return {"safe": safe, "pending": pending, "blocked": blocked}

    def _is_safe_auto_tool(self, tool_name: str, readiness: dict[str, Any]) -> bool:
        tool = self._tool_registry.get_tool(tool_name)
        if not tool:
            return False
        if readiness["state"] not in {"live", "advisory"} or not readiness["executable"]:
            return False
        if readiness["side_effects"] or tool.requires_approval:
            return False
        if tool.risk_level == "low":
            return True
        return tool.category in {"memory", "knowledge"} and tool.risk_level in {
            "low",
            "medium",
        }

    def _baseline_tools(self, safe_requested_tools: list[str]) -> list[str]:
        return self._unique(
            [
                *safe_requested_tools,
                *[
                    tool_name
                    for tool_name in self.CORE_BASELINE_TOOL_CANDIDATES
                    if self._tool_registry.get_tool(tool_name)
                    and self._is_safe_auto_tool(
                        tool_name,
                        self._tool_registry.get_tool_readiness(tool_name),
                    )
                ],
            ]
        )

    @staticmethod
    def _activation_manifest_payload(
        manifest_payload: dict[str, Any],
        *,
        gap: dict[str, Any],
        safe_tools: list[str],
        baseline_variant: bool,
        company_namespace: str,
    ) -> dict[str, Any]:
        payload = dict(manifest_payload)
        original_name = str(payload.get("name") or gap["title"]).strip()
        if baseline_variant:
            payload["name"] = f"{original_name} (Baseline)"
            payload["description"] = (
                f"Safe baseline for {original_name}. "
                "External-write and unconfigured capabilities are tracked as grants."
            )
        payload["default_tools"] = safe_tools
        payload["approval_policy"] = "auto"
        payload["memory_namespace"] = (
            payload.get("memory_namespace")
            or f"{company_namespace}:team:{slug_id(payload['name'])}"
        )
        config = dict(payload.get("config") or {})
        config.update(
            {
                "source": "team_activation",
                "role_gap_id": gap["id"],
                "canonical_role_name": original_name,
                "baseline_variant": baseline_variant,
                "activation_policy": "safe_auto_activation_v1",
            }
        )
        payload["config"] = config
        payload.setdefault("success_metrics", [])
        payload["is_core"] = False
        return payload

    async def _maybe_request_role_gap_approval(
        self,
        *,
        gap: dict[str, Any],
        manifest_payload: dict[str, Any],
        classified: dict[str, Any],
        actor: str,
        request_high_risk_grants: bool,
    ) -> str | None:
        if not request_high_risk_grants:
            return None
        if classified["blocked"]:
            return None
        high_risk_tools = self._agent_manager._role_gap_high_risk_tools(
            manifest_payload.get("default_tools", [])
        )
        if not high_risk_tools:
            return None
        existing = await self._agent_manager._latest_role_gap_tool_grant_approval(gap["id"])
        if existing and existing["state"] in {"pending", "approved"}:
            return existing["approval_id"]
        response = await self._agent_manager.regenerate_role_gap_approval(
            gap["id"],
            requested_by=actor,
        )
        return response["approval_id"]

    async def _upsert_grant(
        self,
        *,
        agent_id: str,
        role_gap_id: str,
        tool_name: str,
        state: str,
        requested_by: str,
        reason: str,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        readiness = self._tool_registry.get_tool_readiness(tool_name)
        tool = self._tool_registry.get_tool(tool_name)
        async with async_session() as session:
            result = await session.execute(
                select(AgentCapabilityGrant).where(
                    AgentCapabilityGrant.agent_id == agent_id,
                    AgentCapabilityGrant.tool_name == tool_name,
                )
            )
            grant = result.scalar_one_or_none()
            if grant:
                grant.role_gap_id = grant.role_gap_id or role_gap_id
                grant.state = self._next_grant_state(grant.state, state)
                grant.risk_level = tool.risk_level if tool else readiness.get("state", "unknown")
                grant.side_effects = bool(readiness["side_effects"])
                grant.approval_id = approval_id or grant.approval_id
                grant.reason = reason or grant.reason
                grant.metadata_ = {
                    **(grant.metadata_ or {}),
                    "readiness": readiness,
                    "last_requested_by": requested_by,
                }
                grant.updated_at = now
                if grant.state == "active" and not grant.activated_at:
                    grant.activated_at = now
            else:
                grant = AgentCapabilityGrant(
                    id=f"grant_{uuid.uuid4().hex[:12]}",
                    agent_id=agent_id,
                    role_gap_id=role_gap_id,
                    tool_name=tool_name,
                    state=state,
                    risk_level=tool.risk_level if tool else readiness.get("state", "unknown"),
                    side_effects=bool(readiness["side_effects"]),
                    approval_id=approval_id,
                    requested_by=requested_by,
                    reason=reason,
                    metadata_={"readiness": readiness},
                    created_at=now,
                    updated_at=now,
                    activated_at=now if state == "active" else None,
                )
                session.add(grant)
            await session.commit()
            return self._grant_to_dict(grant)

    @staticmethod
    def _next_grant_state(current: str, desired: str) -> str:
        if current == "active":
            return current
        if current == "revoked":
            return current
        return desired

    async def _record_gap_activation(
        self,
        gap_id: str,
        activation: dict[str, Any],
        *,
        resolve_gap: bool,
    ) -> None:
        async with async_session() as session:
            result = await session.execute(select(RoleGap).where(RoleGap.id == gap_id))
            gap = result.scalar_one_or_none()
            if not gap:
                raise ValueError(f"Role gap {gap_id} not found")
            gap.resolution = {
                **(gap.resolution or {}),
                **activation,
                "approval_required": not resolve_gap,
            }
            if resolve_gap:
                gap.status = "resolved"
                gap.resolved_at = utc_now()
            gap.updated_at = utc_now()
            await session.commit()

    async def _create_run(
        self,
        *,
        run_id: str,
        snapshot: CompanyContextSnapshot | None,
        actor: str,
        dry_run: bool,
        apply_safe_roles: bool,
        request_high_risk_grants: bool,
        started_at,
    ) -> None:
        async with async_session() as session:
            session.add(
                TeamActivationRun(
                    id=run_id,
                    source_snapshot_id=snapshot.id if snapshot else None,
                    source_hash=snapshot.source_hash if snapshot else None,
                    company_namespace=snapshot.company_namespace if snapshot else None,
                    status="running",
                    dry_run=dry_run,
                    apply_safe_roles=apply_safe_roles,
                    request_high_risk_grants=request_high_risk_grants,
                    counts={},
                    result={},
                    errors=[],
                    actor=actor,
                    started_at=started_at,
                )
            )
            await session.commit()

    async def _finish_run(self, run_id: str, response: dict[str, Any]) -> None:
        async with async_session() as session:
            result = await session.execute(
                select(TeamActivationRun).where(TeamActivationRun.id == run_id)
            )
            run = result.scalar_one_or_none()
            if not run:
                return
            run.status = response["status"]
            run.source_snapshot_id = response.get("source_snapshot_id")
            run.source_hash = response.get("source_hash")
            run.company_namespace = response.get("company_namespace")
            run.counts = response.get("counts") or {}
            run.result = {
                "activated": response.get("activated") or [],
                "dry_run": response.get("dry_run"),
                "apply_safe_roles": response.get("apply_safe_roles"),
                "request_high_risk_grants": response.get("request_high_risk_grants"),
            }
            run.errors = response.get("errors") or []
            run.completed_at = utc_now()
            await session.commit()

    async def _record_audit(self, response: dict[str, Any], *, actor: str) -> None:
        if not self._audit:
            return
        await self._audit.record(
            event_type="team_activation.run",
            actor=actor,
            actor_type="user" if actor != "system" else "system",
            resource_type="team_activation_run",
            resource_id=response["id"],
            action="run",
            outcome="success" if response["status"] in {"completed", "dry_run"} else "degraded",
            metadata={
                "status": response["status"],
                "source_snapshot_id": response.get("source_snapshot_id"),
                "source_hash": response.get("source_hash"),
                "company_namespace": response.get("company_namespace"),
                "counts": response.get("counts"),
            },
        )
        await self._audit.record_control_evidence(
            control_id="ai_team_activation",
            control_area="soc2_change_management",
            actor=actor,
            outcome="success" if response["status"] in {"completed", "dry_run"} else "degraded",
            evidence={
                "run_id": response["id"],
                "status": response["status"],
                "counts": response.get("counts"),
                "source_snapshot_id": response.get("source_snapshot_id"),
            },
        )

    @staticmethod
    def _coverage_status(latest: dict[str, Any] | None) -> str:
        if not latest:
            return "not_run"
        if latest["status"] == "completed":
            return "active"
        return latest["status"]

    @staticmethod
    def _run_to_dict(run: TeamActivationRun) -> dict[str, Any]:
        return {
            "id": run.id,
            "source_snapshot_id": run.source_snapshot_id,
            "source_hash": run.source_hash,
            "company_namespace": run.company_namespace,
            "status": run.status,
            "dry_run": run.dry_run,
            "apply_safe_roles": run.apply_safe_roles,
            "request_high_risk_grants": run.request_high_risk_grants,
            "counts": run.counts,
            "result": run.result,
            "errors": run.errors,
            "actor": run.actor,
            "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        }

    @staticmethod
    def _grant_to_dict(grant: AgentCapabilityGrant) -> dict[str, Any]:
        return {
            "id": grant.id,
            "agent_id": grant.agent_id,
            "role_gap_id": grant.role_gap_id,
            "tool_name": grant.tool_name,
            "state": grant.state,
            "risk_level": grant.risk_level,
            "side_effects": grant.side_effects,
            "approval_id": grant.approval_id,
            "requested_by": grant.requested_by,
            "reason": grant.reason,
            "metadata": grant.metadata_,
            "created_at": grant.created_at.isoformat(),
            "updated_at": grant.updated_at.isoformat(),
            "activated_at": grant.activated_at.isoformat() if grant.activated_at else None,
            "revoked_at": grant.revoked_at.isoformat() if grant.revoked_at else None,
        }

    @staticmethod
    def _unique(values) -> list:
        result = []
        for value in values:
            if value and value not in result:
                result.append(value)
        return result
