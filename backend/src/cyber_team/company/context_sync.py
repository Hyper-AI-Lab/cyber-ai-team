"""ERPNext-backed company context synchronization.

The sync service treats ERPNext as the canonical business system, pulls an
allowlisted and redacted operational snapshot, turns it into Cyber-Team company
context, and applies only low-risk internal changes automatically.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
from sqlalchemy import desc, select

from cyber_team.clock import utc_now
from cyber_team.company.operating_model import OperatingModelBuilder
from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import CompanyContextSnapshot, CompanyContextSyncRun, RoleGap

SOURCE = "erpnext"
SNAPSHOT_STALE_AFTER = timedelta(hours=settings.erpnext_drift_stale_after_hours)

SENSITIVE_FIELD_RE = re.compile(
    r"(password|secret|token|api_key|api_secret|authorization|email|phone|mobile|fax)",
    re.IGNORECASE,
)

SINGLE_DOCTYPES: dict[str, list[str]] = {
    "Global Defaults": [
        "name",
        "default_company",
        "default_currency",
        "country",
        "current_fiscal_year",
    ],
    "System Settings": ["name", "country", "time_zone", "language"],
}

LIST_DOCTYPES: dict[str, list[str]] = {
    "Company": ["name", "company_name", "country", "default_currency", "abbr"],
    "Fiscal Year": ["name", "year_start_date", "year_end_date", "disabled"],
    "Account": ["name", "account_name", "company", "account_type", "root_type", "is_group"],
    "Cost Center": ["name", "cost_center_name", "company", "is_group"],
    "Warehouse": ["name", "warehouse_name", "company", "is_group", "disabled"],
    "Customer": [
        "name",
        "customer_name",
        "customer_type",
        "customer_group",
        "territory",
        "disabled",
    ],
    "Supplier": ["name", "supplier_name", "supplier_type", "supplier_group", "disabled"],
    "Lead": [
        "name",
        "lead_name",
        "company_name",
        "status",
        "territory",
        "creation",
        "modified",
    ],
    "Opportunity": [
        "name",
        "opportunity_from",
        "party_name",
        "status",
        "sales_stage",
        "opportunity_amount",
        "transaction_date",
        "creation",
        "modified",
    ],
    "Project": [
        "name",
        "project_name",
        "status",
        "expected_start_date",
        "expected_end_date",
        "percent_complete",
    ],
    "Task": ["name", "subject", "status", "priority", "project", "exp_start_date", "exp_end_date"],
    "Issue": ["name", "subject", "status", "priority", "customer", "creation", "modified"],
    "Sales Invoice": [
        "name",
        "customer",
        "status",
        "grand_total",
        "currency",
        "posting_date",
        "due_date",
        "outstanding_amount",
    ],
    "Material Request": [
        "name",
        "material_request_type",
        "status",
        "transaction_date",
        "schedule_date",
        "company",
    ],
    "Item": ["name", "item_name", "item_group", "stock_uom", "disabled", "is_stock_item"],
}


class CompanyContextSyncService:
    def __init__(
        self,
        *,
        erpnext,
        agent_manager,
        memory_service,
        tool_registry,
        audit_service=None,
        planner=None,
        session_factory=async_session,
    ):
        self._erpnext = erpnext
        self._agent_manager = agent_manager
        self._memory = memory_service
        self._tool_registry = tool_registry
        self._audit = audit_service
        self._planner = planner
        self._session_factory = session_factory

    def set_planner(self, planner) -> None:
        self._planner = planner

    async def scan_for_erpnext_drift(
        self,
        *,
        actor: str = "company_context_drift_scheduler",
        dry_run: bool = False,
        apply_low_risk: bool = True,
        run_planner: bool = True,
    ) -> dict[str, Any]:
        """Check ERPNext for changed company context and maintain stale role gaps.

        A drift scan intentionally reuses the existing sync pipeline so the
        source hash, redaction, idempotency, memory seeding, and low-risk role
        policy all stay centralized. The extra work here is comparison against
        the previous active snapshot and marking superseded role gaps stale.
        """
        previous_snapshot = await self.latest_snapshot()
        sync_result = await self.sync_from_erpnext(
            actor=actor,
            dry_run=dry_run,
            apply_low_risk=apply_low_risk and not dry_run,
            run_planner=run_planner and not dry_run,
        )
        candidate_snapshot = sync_result.get("snapshot") or {}
        candidate_hash = candidate_snapshot.get("source_hash") or sync_result.get("source_hash")
        previous_hash = (previous_snapshot or {}).get("source_hash")
        initial_baseline = previous_snapshot is None and bool(candidate_hash)
        drift_detected = bool(candidate_hash and candidate_hash != previous_hash)
        stale_result = {
            "count": 0,
            "role_gap_ids": [],
        }

        if (
            drift_detected
            and not initial_baseline
            and not dry_run
            and sync_result.get("status") == "synced"
            and previous_snapshot
        ):
            stale_result = await self.mark_superseded_role_gaps_stale(
                previous_snapshot_id=previous_snapshot["id"],
                previous_source_hash=previous_snapshot["source_hash"],
                new_snapshot_id=candidate_snapshot.get("id"),
                new_source_hash=candidate_hash,
                actor=actor,
            )

        drift_status = (
            "dry_run"
            if dry_run
            else "initial_baseline"
            if initial_baseline
            else "changed"
            if drift_detected
            else "unchanged"
        )
        drift = {
            "status": drift_status,
            "detected": drift_detected,
            "initial_baseline": initial_baseline,
            "previous_snapshot_id": (previous_snapshot or {}).get("id"),
            "previous_source_hash": previous_hash,
            "current_snapshot_id": candidate_snapshot.get("id"),
            "current_source_hash": candidate_hash,
            "sync_run_id": sync_result.get("sync_run_id"),
            "sync_status": sync_result.get("status"),
            "stale_role_gaps": stale_result,
            "checked_at": utc_now().isoformat(),
            "dry_run": dry_run,
            "apply_low_risk": apply_low_risk and not dry_run,
            "run_planner": run_planner and not dry_run,
        }
        if sync_result.get("sync_run_id"):
            await self._annotate_sync_run_result(sync_result["sync_run_id"], {"drift": drift})
        await self._record_drift_evidence(
            actor=actor,
            outcome="success" if sync_result.get("status") != "failed" else "failed",
            drift=drift,
            errors=sync_result.get("errors", []),
        )
        return {
            "status": drift_status,
            "drift": drift,
            "sync": sync_result,
        }

    async def sync_from_erpnext(
        self,
        *,
        actor: str = "system",
        dry_run: bool = False,
        apply_low_risk: bool = True,
        run_planner: bool = True,
    ) -> dict[str, Any]:
        run_id = f"ctxsync_{uuid.uuid4().hex[:12]}"
        started_at = utc_now()
        await self._create_sync_run(
            run_id,
            actor=actor,
            dry_run=dry_run,
            apply_low_risk=apply_low_risk,
            run_planner=run_planner,
            started_at=started_at,
        )

        try:
            fetched = await self._fetch_erpnext_context()
            normalized_profile = self._normalize_company_profile(fetched)
            company_namespace = self._company_namespace(normalized_profile)
            operating_model = OperatingModelBuilder().build(
                normalized_profile,
                existing_manifests=await self._agent_manager.list_role_manifests(),
                available_tools=self._available_tool_names(),
            )
            source_hash = self._source_hash(
                {
                    "normalized_profile": normalized_profile,
                    "erpnext_summary": fetched["summary"],
                    "operating_model_basis": operating_model.get("decision_basis", {}),
                }
            )
            candidate = {
                "source": SOURCE,
                "source_id": settings.erpnext_site_name,
                "source_hash": source_hash,
                "company_namespace": company_namespace,
                "normalized_profile": normalized_profile,
                "erpnext_summary": fetched["summary"],
                "operating_model": operating_model,
                "errors": fetched["errors"],
            }

            if dry_run:
                result = {
                    "status": "dry_run",
                    "created": False,
                    "snapshot": candidate,
                    "counts": self._snapshot_counts(candidate),
                    "errors": fetched["errors"],
                }
                await self._finish_sync_run(
                    run_id,
                    status="dry_run",
                    source_hash=source_hash,
                    company_namespace=company_namespace,
                    counts=result["counts"],
                    result=result,
                    errors=fetched["errors"],
                )
                result["sync_run_id"] = run_id
                return result

            existing = await self._get_snapshot_by_hash(source_hash)
            if existing:
                plan_result = await self._create_or_execute_snapshot_plan(
                    existing["id"],
                    actor=actor,
                    run_planner=run_planner,
                    execute=apply_low_risk,
                )
                if plan_result and plan_result.get("plan"):
                    await self._append_snapshot_values(
                        existing["id"],
                        plan_ids=[plan_result["plan"]["id"]],
                    )
                    existing = await self.get_snapshot(existing["id"]) or existing
                result = {
                    "status": "noop",
                    "created": False,
                    "reason": "ERPNext company context hash is unchanged.",
                    "snapshot": existing,
                    "counts": self._snapshot_counts(existing),
                    "planner": plan_result,
                    "errors": fetched["errors"],
                }
                await self._finish_sync_run(
                    run_id,
                    status="noop",
                    snapshot_id=existing["id"],
                    source_hash=source_hash,
                    company_namespace=company_namespace,
                    counts=result["counts"],
                    result=result,
                    errors=fetched["errors"],
                )
                result["sync_run_id"] = run_id
                return result

            snapshot = await self._create_snapshot(candidate, actor=actor)
            apply_result = {
                "memory_ids": [],
                "agent_ids": [],
                "role_manifest_ids": [],
                "skipped_role_specs": self._unsafe_role_specs(operating_model),
            }
            if apply_low_risk:
                memory_result = await self.seed_snapshot_memory(snapshot["id"], actor=actor)
                role_result = await self.apply_snapshot_low_risk_roles(
                    snapshot["id"],
                    actor=actor,
                )
                apply_result.update(
                    {
                        "memory_ids": memory_result["created_memory_ids"],
                        "agent_ids": role_result["agent_ids"],
                        "role_manifest_ids": role_result["role_manifest_ids"],
                        "skipped_role_specs": role_result["skipped_role_specs"],
                    }
                )
                snapshot = await self.get_snapshot(snapshot["id"]) or snapshot

            plan_result = None
            if run_planner:
                plan_result = await self._create_or_execute_snapshot_plan(
                    snapshot["id"],
                    actor=actor,
                    run_planner=run_planner,
                    execute=apply_low_risk,
                )
                plan = plan_result.get("plan")
                if plan:
                    await self._append_snapshot_values(snapshot["id"], plan_ids=[plan["id"]])
                    snapshot = await self.get_snapshot(snapshot["id"]) or snapshot

            await self._record_evidence(
                actor=actor,
                outcome="success",
                snapshot=snapshot,
                sync_run_id=run_id,
                counts=self._snapshot_counts(snapshot),
            )
            result = {
                "status": "synced",
                "created": True,
                "snapshot": snapshot,
                "counts": self._snapshot_counts(snapshot),
                "apply_result": apply_result,
                "planner": plan_result,
                "errors": fetched["errors"],
            }
            await self._finish_sync_run(
                run_id,
                status="synced",
                snapshot_id=snapshot["id"],
                source_hash=source_hash,
                company_namespace=company_namespace,
                counts=result["counts"],
                result=result,
                errors=fetched["errors"],
            )
            result["sync_run_id"] = run_id
            return result
        except Exception as exc:
            error = {
                "source": SOURCE,
                "error": type(exc).__name__,
                "detail": str(exc),
            }
            result = {"status": "failed", "created": False, "errors": [error]}
            await self._finish_sync_run(
                run_id,
                status="failed",
                counts={},
                result=result,
                errors=[error],
            )
            await self._record_evidence(
                actor=actor,
                outcome="failed",
                snapshot=None,
                sync_run_id=run_id,
                counts={},
                errors=[error],
            )
            result["sync_run_id"] = run_id
            return result

    async def get_latest_context(self) -> dict[str, Any]:
        snapshot = await self.latest_snapshot()
        runs = await self.list_sync_runs(limit=1)
        readiness = self.readiness_from_snapshot(snapshot, latest_run=runs[0] if runs else None)
        pending_plans = []
        if self._planner and snapshot:
            pending_plans = [
                plan for plan in await self._planner.list_plans(
                    source_type="company_context_snapshot",
                    limit=20,
                )
                if plan["source_id"] == snapshot["id"]
                and plan["status"] in {"planned", "running", "waiting_approval", "blocked"}
            ]
        return {
            "snapshot": snapshot,
            "freshness": readiness,
            "normalized_company_profile": (snapshot or {}).get("normalized_profile"),
            "erpnext_summary": (snapshot or {}).get("erpnext_summary"),
            "operating_model_summary": (
                (snapshot or {}).get("operating_model") or {}
            ).get("summary"),
            "pending_plans": pending_plans,
            "latest_sync_run": runs[0] if runs else None,
        }

    async def latest_snapshot(self) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(CompanyContextSnapshot)
                .where(CompanyContextSnapshot.status == "active")
                .order_by(desc(CompanyContextSnapshot.created_at))
                .limit(1)
            )
            snapshot = result.scalar_one_or_none()
            return self._snapshot_to_dict(snapshot) if snapshot else None

    async def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(CompanyContextSnapshot).where(
                    CompanyContextSnapshot.id == snapshot_id
                )
            )
            snapshot = result.scalar_one_or_none()
            return self._snapshot_to_dict(snapshot) if snapshot else None

    async def list_sync_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 200))
        async with self._session_factory() as session:
            result = await session.execute(
                select(CompanyContextSyncRun)
                .order_by(desc(CompanyContextSyncRun.started_at))
                .limit(safe_limit)
            )
            return [self._sync_run_to_dict(run) for run in result.scalars().all()]

    async def drift_status(self) -> dict[str, Any]:
        snapshot = await self.latest_snapshot()
        runs = await self.list_sync_runs(limit=20)
        latest_drift = None
        for run in runs:
            drift = (run.get("result") or {}).get("drift")
            if drift:
                latest_drift = {
                    **drift,
                    "run_id": run["id"],
                    "run_status": run["status"],
                    "completed_at": run.get("completed_at"),
                }
                break
        stale_summary = None
        if self._agent_manager:
            stale_summary = await self._agent_manager.summarize_role_backlog(
                statuses=["stale"],
                source_type="company_context_snapshot",
                limit=200,
            )
        return {
            "enabled": settings.erpnext_drift_detection_enabled,
            "interval_seconds": settings.erpnext_drift_interval_seconds,
            "initial_delay_seconds": settings.erpnext_drift_initial_delay_seconds,
            "stale_after_hours": settings.erpnext_drift_stale_after_hours,
            "apply_low_risk": settings.erpnext_drift_apply_low_risk,
            "run_planner": settings.erpnext_drift_run_planner,
            "latest_drift": latest_drift,
            "latest_snapshot_id": (snapshot or {}).get("id"),
            "latest_source_hash": (snapshot or {}).get("source_hash"),
            "stale_role_gap_count": (stale_summary or {}).get("counts", {}).get("total", 0),
        }

    def readiness_from_snapshot(
        self,
        snapshot: dict[str, Any] | None,
        *,
        latest_run: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        required = "erpnext" in settings.required_provider_names
        if not snapshot:
            status = "missing"
            return {
                "status": status,
                "required": required,
                "blocking": required,
                "stale": True,
                "last_sync_at": None,
                "source_hash": None,
                "latest_run_status": (latest_run or {}).get("status"),
                "detail": "No successful ERPNext company-context sync has been recorded.",
            }
        snapshot_created_at = snapshot["created_at"]
        last_verified_at = self._last_verified_at(snapshot, latest_run)
        freshness_at = last_verified_at or snapshot_created_at
        age_seconds = max(0.0, (utc_now() - self._parse_iso(freshness_at)).total_seconds())
        stale = age_seconds > SNAPSHOT_STALE_AFTER.total_seconds()
        status = "stale" if stale else "ready"
        return {
            "status": status,
            "required": required,
            "blocking": False,
            "stale": stale,
            "stale_after_hours": int(SNAPSHOT_STALE_AFTER.total_seconds() / 3600),
            "age_seconds": round(age_seconds, 2),
            "last_sync_at": freshness_at,
            "snapshot_created_at": snapshot_created_at,
            "last_verified_at": last_verified_at,
            "freshness_basis": "sync_verification" if last_verified_at else "snapshot",
            "source_hash": snapshot["source_hash"],
            "company_namespace": snapshot["company_namespace"],
            "latest_run_status": (latest_run or {}).get("status"),
            "latest_drift": ((latest_run or {}).get("result") or {}).get("drift"),
            "detail": (
                "ERPNext company context is fresh."
                if not stale
                else "ERPNext company context has not been verified within the freshness policy."
            ),
        }

    def _last_verified_at(
        self,
        snapshot: dict[str, Any],
        latest_run: dict[str, Any] | None,
    ) -> str | None:
        if not latest_run or latest_run.get("dry_run"):
            return None
        if latest_run.get("status") not in {"synced", "noop"}:
            return None
        if not self._sync_run_matches_snapshot(snapshot, latest_run):
            return None
        return latest_run.get("completed_at") or latest_run.get("started_at")

    @staticmethod
    def _sync_run_matches_snapshot(
        snapshot: dict[str, Any],
        latest_run: dict[str, Any],
    ) -> bool:
        snapshot_id = snapshot.get("id")
        run_snapshot_id = latest_run.get("snapshot_id")
        if snapshot_id and run_snapshot_id and snapshot_id == run_snapshot_id:
            return True
        snapshot_hash = snapshot.get("source_hash")
        run_hash = latest_run.get("source_hash")
        return bool(snapshot_hash and run_hash and snapshot_hash == run_hash)

    async def assess_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        snapshot = await self.get_snapshot(snapshot_id)
        if not snapshot:
            raise ValueError(f"Company context snapshot {snapshot_id} not found")
        return {
            "snapshot_id": snapshot_id,
            "source_hash": snapshot["source_hash"],
            "company_namespace": snapshot["company_namespace"],
            "counts": self._snapshot_counts(snapshot),
            "safe_role_count": len(self._safe_role_specs(snapshot["operating_model"])),
            "unsafe_role_count": len(self._unsafe_role_specs(snapshot["operating_model"])),
            "capability_gap_count": len(
                (snapshot["operating_model"] or {}).get("capability_gaps") or []
            ),
            "errors": snapshot.get("errors", []),
        }

    async def mark_superseded_role_gaps_stale(
        self,
        *,
        previous_snapshot_id: str,
        previous_source_hash: str,
        new_snapshot_id: str | None,
        new_source_hash: str | None,
        actor: str,
    ) -> dict[str, Any]:
        now = utc_now()
        stale_ids: list[str] = []
        async with self._session_factory() as session:
            result = await session.execute(
                select(RoleGap).where(
                    RoleGap.source_type == "company_context_snapshot",
                    RoleGap.status.in_(("open", "proposed")),
                )
            )
            for gap in result.scalars().all():
                context = gap.context or {}
                if (
                    context.get("snapshot_id") != previous_snapshot_id
                    and context.get("source_hash") != previous_source_hash
                ):
                    continue
                gap.status = "stale"
                gap.context = {
                    **context,
                    "stale_at": now.isoformat(),
                    "stale_reason": "ERPNext company-context snapshot was superseded.",
                    "superseded_by_snapshot_id": new_snapshot_id,
                    "superseded_by_source_hash": new_source_hash,
                }
                gap.resolution = {
                    **(gap.resolution or {}),
                    "resolver": actor,
                    "resolved_at": now.isoformat(),
                    "reason": "superseded_by_company_context_drift",
                    "superseded_by_snapshot_id": new_snapshot_id,
                    "superseded_by_source_hash": new_source_hash,
                }
                gap.resolved_at = now
                gap.updated_at = now
                stale_ids.append(gap.id)
            await session.commit()
        if stale_ids:
            await self._record(
                "company_context.role_gaps_staled",
                actor=actor,
                resource_id=previous_snapshot_id,
                metadata={
                    "role_gap_ids": stale_ids,
                    "previous_source_hash": previous_source_hash,
                    "new_snapshot_id": new_snapshot_id,
                    "new_source_hash": new_source_hash,
                },
            )
        return {
            "count": len(stale_ids),
            "role_gap_ids": stale_ids,
        }

    async def seed_snapshot_memory(
        self,
        snapshot_id: str,
        *,
        actor: str = "system",
    ) -> dict[str, Any]:
        snapshot = await self.get_snapshot(snapshot_id)
        if not snapshot:
            raise ValueError(f"Company context snapshot {snapshot_id} not found")
        if snapshot.get("memory_ids"):
            return {
                "snapshot_id": snapshot_id,
                "already_seeded": True,
                "created_memory_ids": snapshot["memory_ids"],
            }
        seeds = self._memory_seeds_for_snapshot(snapshot)
        created_ids = []
        for seed in seeds:
            remembered = await self._memory.remember(
                SimpleNamespace(
                    agent_id=None,
                    memory_type=seed["memory_type"],
                    namespace=seed["namespace"],
                    content=seed["content"],
                    metadata={
                        "source": "erpnext_company_context_sync",
                        "snapshot_id": snapshot_id,
                        "source_hash": snapshot["source_hash"],
                        "company_namespace": snapshot["company_namespace"],
                        "seed_id": seed["id"],
                    },
                    importance=seed["importance"],
                )
            )
            created_ids.append(remembered["id"])
        await self._append_snapshot_values(snapshot_id, memory_ids=created_ids)
        await self._record(
            "company_context.memory_seeded",
            actor=actor,
            resource_id=snapshot_id,
            metadata={"memory_ids": created_ids, "seed_count": len(created_ids)},
        )
        return {
            "snapshot_id": snapshot_id,
            "already_seeded": False,
            "created_memory_ids": created_ids,
        }

    async def apply_snapshot_low_risk_roles(
        self,
        snapshot_id: str,
        *,
        actor: str = "system",
    ) -> dict[str, Any]:
        snapshot = await self.get_snapshot(snapshot_id)
        if not snapshot:
            raise ValueError(f"Company context snapshot {snapshot_id} not found")
        if snapshot.get("agent_ids") or snapshot.get("role_manifest_ids"):
            return {
                "snapshot_id": snapshot_id,
                "already_applied": True,
                "agent_ids": snapshot.get("agent_ids", []),
                "role_manifest_ids": snapshot.get("role_manifest_ids", []),
                "skipped_role_specs": self._unsafe_role_specs(snapshot["operating_model"]),
            }
        manifests = await self._agent_manager.list_role_manifests()
        manifest_by_name = {manifest["name"]: manifest for manifest in manifests}
        created_manifest_ids = []
        agent_ids = []
        safe_specs = self._safe_role_specs(snapshot["operating_model"])
        for role_spec in safe_specs:
            manifest = manifest_by_name.get(role_spec["name"])
            if not manifest:
                manifest = await self._agent_manager.create_role_manifest(
                    SimpleNamespace(**role_spec["manifest_payload"])
                )
                manifest_by_name[manifest["name"]] = manifest
                created_manifest_ids.append(manifest["id"])
            agent = await self._agent_manager.instantiate_role(
                manifest["id"],
                {
                    **snapshot["normalized_profile"],
                    "company_name": snapshot["normalized_profile"].get("name"),
                    "company_namespace": snapshot["company_namespace"],
                    "source_hash": snapshot["source_hash"],
                    "provisioned_by": "erpnext_company_context_sync",
                    "role_rationale": role_spec.get("rationale", []),
                    "activation_triggers": role_spec.get("activation_triggers", []),
                },
            )
            agent_ids.append(agent["id"])
        await self._append_snapshot_values(
            snapshot_id,
            agent_ids=agent_ids,
            role_manifest_ids=created_manifest_ids,
            applied=True,
        )
        await self._record(
            "company_context.low_risk_roles_applied",
            actor=actor,
            resource_id=snapshot_id,
            metadata={
                "agent_ids": agent_ids,
                "role_manifest_ids": created_manifest_ids,
                "safe_role_count": len(safe_specs),
                "skipped_role_specs": self._unsafe_role_specs(snapshot["operating_model"]),
            },
        )
        return {
            "snapshot_id": snapshot_id,
            "already_applied": False,
            "agent_ids": agent_ids,
            "role_manifest_ids": created_manifest_ids,
            "skipped_role_specs": self._unsafe_role_specs(snapshot["operating_model"]),
        }

    async def report_snapshot_risky_role_gaps(
        self,
        snapshot_id: str,
        *,
        actor: str = "system",
    ) -> dict[str, Any]:
        snapshot = await self.get_snapshot(snapshot_id)
        if not snapshot:
            raise ValueError(f"Company context snapshot {snapshot_id} not found")
        unsafe_specs = self._unsafe_role_specs(snapshot["operating_model"])
        existing_gap_ids = snapshot.get("role_gap_ids", [])
        if existing_gap_ids:
            return {
                "snapshot_id": snapshot_id,
                "already_reported": True,
                "role_gap_ids": existing_gap_ids,
                "unsafe_role_count": len(unsafe_specs),
            }
        created_gap_ids = []
        for skipped in unsafe_specs:
            gap = await self._agent_manager.report_role_gap(
                SimpleNamespace(
                    title=f"Review ERPNext-derived role: {skipped['name']}",
                    description=(
                        f"ERPNext company context implies the {skipped['name']} role, "
                        f"but it was not auto-created because {skipped['reason']}."
                    ),
                    severity="medium",
                    source_agent_id=None,
                    source_type="company_context_snapshot",
                    company_namespace=snapshot["company_namespace"],
                    capability=skipped["family"],
                    requested_tools=skipped["tools"],
                    context={
                        "snapshot_id": snapshot_id,
                        "source_hash": snapshot["source_hash"],
                        "role_name": skipped["name"],
                        "role_family": skipped["family"],
                        "approval_policy": skipped["approval_policy"],
                        "dedupe_key": (
                            "company_context_role:"
                            f"{snapshot['source_hash']}:{skipped['name']}"
                        ),
                    },
                ),
                reporter=actor,
            )
            created_gap_ids.append(gap["id"])
        await self._append_snapshot_values(snapshot_id, role_gap_ids=created_gap_ids)
        await self._record(
            "company_context.risky_role_gaps_reported",
            actor=actor,
            resource_id=snapshot_id,
            metadata={
                "role_gap_ids": created_gap_ids,
                "unsafe_role_count": len(unsafe_specs),
            },
        )
        return {
            "snapshot_id": snapshot_id,
            "already_reported": False,
            "role_gap_ids": created_gap_ids,
            "unsafe_role_count": len(unsafe_specs),
        }

    async def _create_or_execute_snapshot_plan(
        self,
        snapshot_id: str,
        *,
        actor: str,
        run_planner: bool,
        execute: bool,
    ) -> dict[str, Any] | None:
        if not run_planner or not self._planner:
            return None
        plan_result = await self._planner.create_plan_from_company_context_snapshot(
            snapshot_id,
            actor=actor,
        )
        plan = plan_result.get("plan")
        if plan and execute:
            plan_result["execution"] = await self._planner.execute_plan(
                plan["id"],
                actor=actor,
            )
            refreshed_plan = await self._planner.get_plan(plan["id"])
            if refreshed_plan:
                plan_result["plan"] = refreshed_plan
        return plan_result

    async def _fetch_erpnext_context(self) -> dict[str, Any]:
        validation = await self._erpnext.validate()
        if validation.get("status") != "ready":
            raise RuntimeError(validation.get("detail") or "ERPNext validation failed")

        errors = []
        singles: dict[str, dict[str, Any]] = {}
        for doctype, fields in SINGLE_DOCTYPES.items():
            try:
                doc = await self._erpnext.get_doc(doctype, doctype)
                singles[doctype] = self._allow_fields(self._redact_record(doc), fields)
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                errors.append(self._fetch_error(doctype, exc, optional=True))

        lists: dict[str, list[dict[str, Any]]] = {}
        for doctype, fields in LIST_DOCTYPES.items():
            try:
                docs = await self._erpnext.list_docs(
                    doctype,
                    fields=fields,
                    limit=50,
                )
                lists[doctype] = [
                    self._allow_fields(self._redact_record(doc), fields)
                    for doc in docs
                ]
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                errors.append(self._fetch_error(doctype, exc, optional=doctype != "Company"))
                lists[doctype] = []

        summary = {
            "site_name": settings.erpnext_site_name,
            "site_url": f"https://{settings.erpnext_edge_domain}",
            "api_url": settings.erpnext_url,
            "validation": validation,
            "singles": singles,
            "records": lists,
            "counts": {doctype: len(records) for doctype, records in lists.items()},
            "statuses": {
                doctype: self._status_counts(records)
                for doctype, records in lists.items()
            },
            "recent": {
                doctype: records[:10]
                for doctype, records in lists.items()
                if records
            },
        }
        return {"summary": summary, "errors": errors}

    def _normalize_company_profile(self, fetched: dict[str, Any]) -> dict[str, Any]:
        summary = fetched["summary"]
        records = summary.get("records", {})
        singles = summary.get("singles", {})
        companies = records.get("Company", [])
        primary_company = companies[0] if companies else {}
        global_defaults = singles.get("Global Defaults") or {}
        company_name = (
            primary_company.get("company_name")
            or primary_company.get("name")
            or global_defaults.get("default_company")
            or settings.app_name
        )
        country = primary_company.get("country") or global_defaults.get("country")
        currency = primary_company.get("default_currency") or global_defaults.get(
            "default_currency"
        )
        customer_names = self._names(records.get("Customer", []), "customer_name")
        supplier_names = self._names(records.get("Supplier", []), "supplier_name")
        project_names = self._names(records.get("Project", []), "project_name")
        item_names = self._names(records.get("Item", []), "item_name")
        open_tasks = [
            task for task in records.get("Task", [])
            if str(task.get("status", "")).lower() not in {"closed", "completed", "cancelled"}
        ]
        open_issues = [
            issue for issue in records.get("Issue", [])
            if str(issue.get("status", "")).lower() not in {"closed", "resolved"}
        ]
        profile = {
            "name": company_name,
            "company_name": company_name,
            "industry": "Business operations managed in ERPNext",
            "stage": "operational",
            "product": ", ".join(item_names[:6]) or "ERPNext-backed business operations",
            "target_customers": ", ".join(customer_names[:8]) or "Customers tracked in ERPNext",
            "channels": "email, ERPNext CRM, projects, support tickets, procurement",
            "goals": self._derived_goals(
                projects=project_names,
                open_tasks=open_tasks,
                open_issues=open_issues,
                material_requests=records.get("Material Request", []),
            ),
            "jurisdictions": ", ".join(value for value in [country] if value),
            "default_currency": currency,
            "source": "erpnext",
            "source_site": settings.erpnext_site_name,
            "source_hash_basis": "allowlisted_redacted_erpnext_snapshot",
            "erpnext_counts": summary.get("counts", {}),
            "erpnext_statuses": summary.get("statuses", {}),
            "erpnext_business_context": {
                "companies": self._names(companies, "company_name"),
                "customers": customer_names[:20],
                "suppliers": supplier_names[:20],
                "projects": project_names[:20],
                "items": item_names[:20],
                "open_task_count": len(open_tasks),
                "open_issue_count": len(open_issues),
            },
        }
        return profile

    def _safe_role_specs(self, operating_model: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            role_spec
            for role_spec in operating_model.get("planned_role_specs", [])
            if self._role_spec_is_safe(role_spec)
        ]

    def _unsafe_role_specs(self, operating_model: dict[str, Any]) -> list[dict[str, Any]]:
        skipped = []
        for role_spec in operating_model.get("planned_role_specs", []):
            if self._role_spec_is_safe(role_spec):
                continue
            skipped.append(
                {
                    "name": role_spec.get("name"),
                    "family": role_spec.get("family"),
                    "approval_policy": role_spec.get("approval_policy"),
                    "tools": role_spec.get("default_tools", []),
                    "reason": self._unsafe_role_reason(role_spec),
                }
            )
        return skipped

    def _role_spec_is_safe(self, role_spec: dict[str, Any]) -> bool:
        if role_spec.get("approval_policy") != "auto":
            return False
        for tool_name in role_spec.get("default_tools", []):
            if not self._tool_is_safe_for_auto_role(tool_name):
                return False
        return True

    def _unsafe_role_reason(self, role_spec: dict[str, Any]) -> str:
        if role_spec.get("approval_policy") != "auto":
            return f"approval_policy is {role_spec.get('approval_policy')}"
        unsafe_tools = [
            tool_name for tool_name in role_spec.get("default_tools", [])
            if not self._tool_is_safe_for_auto_role(tool_name)
        ]
        if unsafe_tools:
            return "tools not safe for automatic ERPNext-context role creation: " + ", ".join(
                unsafe_tools
            )
        return "role does not satisfy low-risk auto-apply policy"

    def _tool_is_safe_for_auto_role(self, tool_name: str) -> bool:
        if not self._tool_registry:
            return False
        readiness = self._tool_registry.get_tool_readiness(tool_name)
        if readiness.get("state") not in {"live", "advisory"}:
            return False
        if readiness.get("side_effects"):
            return False
        tool = self._tool_registry.get_tool(tool_name)
        if tool and getattr(tool, "risk_level", "low") not in {"low"}:
            return False
        if tool and getattr(tool, "requires_approval", False):
            return False
        return True

    def _available_tool_names(self) -> set[str]:
        if not self._tool_registry:
            return set()
        return {tool.name for tool in self._tool_registry.list_tools()}

    def _memory_seeds_for_snapshot(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        operating_model = snapshot.get("operating_model") or {}
        seeds = list(operating_model.get("memory_seed") or [])
        profile = snapshot.get("normalized_profile") or {}
        erp_summary = snapshot.get("erpnext_summary") or {}
        company_namespace = snapshot["company_namespace"]
        counts = erp_summary.get("counts", {})
        statuses = erp_summary.get("statuses", {})
        seeds.extend(
            [
                {
                    "id": "erpnext_company_profile",
                    "memory_type": "semantic",
                    "namespace": company_namespace,
                    "importance": 0.94,
                    "content": (
                        "ERPNext company profile snapshot:\n"
                        + json.dumps(profile, sort_keys=True, indent=2)
                    )[:6000],
                },
                {
                    "id": "erpnext_operational_summary",
                    "memory_type": "procedural",
                    "namespace": f"{company_namespace}:operations",
                    "importance": 0.88,
                    "content": (
                        "ERPNext operational baseline counts and statuses:\n"
                        + json.dumps(
                            {"counts": counts, "statuses": statuses},
                            sort_keys=True,
                            indent=2,
                        )
                    )[:6000],
                },
            ]
        )
        return seeds

    async def _create_sync_run(
        self,
        run_id: str,
        *,
        actor: str,
        dry_run: bool,
        apply_low_risk: bool,
        run_planner: bool,
        started_at,
    ) -> None:
        async with self._session_factory() as session:
            session.add(
                CompanyContextSyncRun(
                    id=run_id,
                    source=SOURCE,
                    status="running",
                    dry_run=dry_run,
                    apply_low_risk=apply_low_risk,
                    run_planner=run_planner,
                    counts={},
                    result={},
                    errors=[],
                    actor=actor,
                    started_at=started_at,
                )
            )
            await session.commit()

    async def _finish_sync_run(
        self,
        run_id: str,
        *,
        status: str,
        counts: dict[str, Any],
        result: dict[str, Any],
        errors: list[dict[str, Any]],
        snapshot_id: str | None = None,
        source_hash: str | None = None,
        company_namespace: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            run = (
                await session.execute(
                    select(CompanyContextSyncRun).where(CompanyContextSyncRun.id == run_id)
                )
            ).scalar_one()
            run.status = status
            run.snapshot_id = snapshot_id
            run.source_hash = source_hash
            run.company_namespace = company_namespace
            run.counts = counts
            run.result = self._compact_result(result)
            run.errors = errors
            run.completed_at = utc_now()
            await session.commit()

    async def _annotate_sync_run_result(
        self,
        run_id: str,
        metadata: dict[str, Any],
    ) -> None:
        async with self._session_factory() as session:
            run = (
                await session.execute(
                    select(CompanyContextSyncRun).where(CompanyContextSyncRun.id == run_id)
                )
            ).scalar_one_or_none()
            if not run:
                return
            run.result = {
                **(run.result or {}),
                **metadata,
            }
            await session.commit()

    async def _create_snapshot(self, data: dict[str, Any], *, actor: str) -> dict[str, Any]:
        snapshot_id = f"ctx_{uuid.uuid4().hex[:12]}"
        now = utc_now()
        async with self._session_factory() as session:
            snapshot = CompanyContextSnapshot(
                id=snapshot_id,
                source=data["source"],
                source_id=data.get("source_id"),
                source_hash=data["source_hash"],
                company_namespace=data["company_namespace"],
                status="active",
                normalized_profile=data["normalized_profile"],
                erpnext_summary=data["erpnext_summary"],
                operating_model=data["operating_model"],
                memory_ids=[],
                agent_ids=[],
                role_manifest_ids=[],
                role_gap_ids=[],
                approval_ids=[],
                plan_ids=[],
                errors=data.get("errors", []),
                created_by=actor,
                created_at=now,
            )
            session.add(snapshot)
            await session.commit()
            return self._snapshot_to_dict(snapshot)

    async def _append_snapshot_values(
        self,
        snapshot_id: str,
        *,
        memory_ids: list[str] | None = None,
        agent_ids: list[str] | None = None,
        role_manifest_ids: list[str] | None = None,
        role_gap_ids: list[str] | None = None,
        approval_ids: list[str] | None = None,
        plan_ids: list[str] | None = None,
        applied: bool = False,
    ) -> None:
        async with self._session_factory() as session:
            snapshot = (
                await session.execute(
                    select(CompanyContextSnapshot).where(
                        CompanyContextSnapshot.id == snapshot_id
                    )
                )
            ).scalar_one()
            snapshot.memory_ids = self._merge(snapshot.memory_ids, memory_ids)
            snapshot.agent_ids = self._merge(snapshot.agent_ids, agent_ids)
            snapshot.role_manifest_ids = self._merge(
                snapshot.role_manifest_ids,
                role_manifest_ids,
            )
            snapshot.role_gap_ids = self._merge(snapshot.role_gap_ids, role_gap_ids)
            snapshot.approval_ids = self._merge(snapshot.approval_ids, approval_ids)
            snapshot.plan_ids = self._merge(snapshot.plan_ids, plan_ids)
            if applied:
                snapshot.applied_at = utc_now()
            await session.commit()

    async def _get_snapshot_by_hash(self, source_hash: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(CompanyContextSnapshot)
                .where(
                    CompanyContextSnapshot.source == SOURCE,
                    CompanyContextSnapshot.source_hash == source_hash,
                )
                .order_by(desc(CompanyContextSnapshot.created_at))
                .limit(1)
            )
            snapshot = result.scalar_one_or_none()
            return self._snapshot_to_dict(snapshot) if snapshot else None

    async def _record(
        self,
        event_type: str,
        *,
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
            actor_type="system",
            resource_type="company_context_snapshot",
            resource_id=resource_id,
            action="sync",
            outcome=outcome,
            metadata=metadata,
        )

    async def _record_evidence(
        self,
        *,
        actor: str,
        outcome: str,
        snapshot: dict[str, Any] | None,
        sync_run_id: str,
        counts: dict[str, Any],
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        if not self._audit:
            return
        await self._audit.record_control_evidence(
            control_id="erpnext.company_context_sync",
            control_area="soc2_availability",
            actor=actor,
            outcome=outcome,
            evidence={
                "sync_run_id": sync_run_id,
                "snapshot_id": (snapshot or {}).get("id"),
                "source_hash": (snapshot or {}).get("source_hash"),
                "company_namespace": (snapshot or {}).get("company_namespace"),
                "counts": counts,
                "errors": errors or (snapshot or {}).get("errors", []),
            },
        )

    async def _record_drift_evidence(
        self,
        *,
        actor: str,
        outcome: str,
        drift: dict[str, Any],
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        if not self._audit:
            return
        await self._audit.record(
            event_type="company_context.drift_scan",
            actor=actor,
            actor_type="system",
            resource_type="company_context_snapshot",
            resource_id=drift.get("current_snapshot_id") or drift.get("previous_snapshot_id"),
            action="drift_scan",
            outcome=outcome,
            metadata=drift,
        )
        await self._audit.record_control_evidence(
            control_id="erpnext.company_context_drift_detection",
            control_area="soc2_availability",
            actor=actor,
            outcome=outcome,
            evidence={
                **drift,
                "errors": errors or [],
            },
        )

    @staticmethod
    def _snapshot_to_dict(snapshot: CompanyContextSnapshot) -> dict[str, Any]:
        return {
            "id": snapshot.id,
            "source": snapshot.source,
            "source_id": snapshot.source_id,
            "source_hash": snapshot.source_hash,
            "company_namespace": snapshot.company_namespace,
            "status": snapshot.status,
            "normalized_profile": snapshot.normalized_profile or {},
            "erpnext_summary": snapshot.erpnext_summary or {},
            "operating_model": snapshot.operating_model or {},
            "memory_ids": snapshot.memory_ids or [],
            "agent_ids": snapshot.agent_ids or [],
            "role_manifest_ids": snapshot.role_manifest_ids or [],
            "role_gap_ids": snapshot.role_gap_ids or [],
            "approval_ids": snapshot.approval_ids or [],
            "plan_ids": snapshot.plan_ids or [],
            "errors": snapshot.errors or [],
            "created_by": snapshot.created_by,
            "created_at": snapshot.created_at.isoformat(),
            "applied_at": snapshot.applied_at.isoformat() if snapshot.applied_at else None,
        }

    @staticmethod
    def _sync_run_to_dict(run: CompanyContextSyncRun) -> dict[str, Any]:
        return {
            "id": run.id,
            "source": run.source,
            "status": run.status,
            "dry_run": run.dry_run,
            "apply_low_risk": run.apply_low_risk,
            "run_planner": run.run_planner,
            "snapshot_id": run.snapshot_id,
            "source_hash": run.source_hash,
            "company_namespace": run.company_namespace,
            "counts": run.counts or {},
            "result": run.result or {},
            "errors": run.errors or [],
            "actor": run.actor,
            "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        }

    @staticmethod
    def _snapshot_counts(snapshot: dict[str, Any]) -> dict[str, int]:
        operating_model = snapshot.get("operating_model") or {}
        erp_summary = snapshot.get("erpnext_summary") or {}
        return {
            "erpnext_doctypes": len((erp_summary.get("records") or {}).keys()),
            "erpnext_records": sum((erp_summary.get("counts") or {}).values()),
            "planned_roles": len(operating_model.get("planned_role_specs") or []),
            "deferred_roles": len(operating_model.get("role_backlog") or []),
            "capability_gaps": len(operating_model.get("capability_gaps") or []),
            "memory_ids": len(snapshot.get("memory_ids") or []),
            "agent_ids": len(snapshot.get("agent_ids") or []),
            "role_manifest_ids": len(snapshot.get("role_manifest_ids") or []),
            "approval_ids": len(snapshot.get("approval_ids") or []),
            "plan_ids": len(snapshot.get("plan_ids") or []),
        }

    @staticmethod
    def _source_hash(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _redact_record(record: dict[str, Any]) -> dict[str, Any]:
        redacted = {}
        for key, value in record.items():
            if SENSITIVE_FIELD_RE.search(str(key)):
                redacted[key] = "[redacted]" if value else value
            elif isinstance(value, dict):
                redacted[key] = CompanyContextSyncService._redact_record(value)
            elif isinstance(value, list):
                redacted[key] = [
                    CompanyContextSyncService._redact_record(item)
                    if isinstance(item, dict)
                    else item
                    for item in value
                ]
            else:
                redacted[key] = value
        return redacted

    @staticmethod
    def _allow_fields(record: dict[str, Any], fields: list[str]) -> dict[str, Any]:
        allowed = set(fields)
        return {key: value for key, value in record.items() if key in allowed}

    @staticmethod
    def _status_counts(records: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in records:
            status = str(record.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
        return counts

    @staticmethod
    def _names(records: list[dict[str, Any]], preferred_key: str) -> list[str]:
        names = []
        for record in records:
            value = record.get(preferred_key) or record.get("name")
            if value and value not in names:
                names.append(str(value))
        return names

    @staticmethod
    def _derived_goals(
        *,
        projects: list[str],
        open_tasks: list[dict[str, Any]],
        open_issues: list[dict[str, Any]],
        material_requests: list[dict[str, Any]],
    ) -> str:
        goals = ["keep ERPNext business records synchronized with Cyber-Team memory"]
        if projects:
            goals.append("coordinate active projects: " + ", ".join(projects[:5]))
        if open_tasks:
            goals.append(f"complete {len(open_tasks)} open ERPNext tasks")
        if open_issues:
            goals.append(f"resolve {len(open_issues)} open support issues")
        if material_requests:
            goals.append("monitor procurement/material requests")
        return "; ".join(goals)

    @staticmethod
    def _company_namespace(profile: dict[str, Any]) -> str:
        slug = re.sub(
            r"[^a-z0-9]+",
            "_",
            str(profile.get("name") or settings.app_name).lower(),
        ).strip("_")
        return f"company:{slug or 'default'}"

    @staticmethod
    def _fetch_error(doctype: str, exc: Exception, *, optional: bool) -> dict[str, Any]:
        return {
            "doctype": doctype,
            "optional": optional,
            "error": type(exc).__name__,
            "detail": str(exc)[:500],
        }

    @staticmethod
    def _merge(existing: list | None, additions: list | None) -> list:
        merged = list(existing or [])
        for item in additions or []:
            if item and item not in merged:
                merged.append(item)
        return merged

    @staticmethod
    def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
        compact = dict(result)
        snapshot = compact.get("snapshot")
        if isinstance(snapshot, dict):
            compact["snapshot"] = {
                "id": snapshot.get("id"),
                "source_hash": snapshot.get("source_hash"),
                "company_namespace": snapshot.get("company_namespace"),
                "created_at": snapshot.get("created_at"),
                "counts": CompanyContextSyncService._snapshot_counts(snapshot),
            }
        return compact

    @staticmethod
    def _parse_iso(value: str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.replace(tzinfo=None)
        return parsed
