from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.clock import utc_now
from cyber_team.company.context_sync import CompanyContextSyncService
from cyber_team.db import Base
from cyber_team.db.models import (
    ApprovalRequest,
    AutonomousPlan,
    AutonomousTask,
    CompanyContextSnapshot,
    CompanyContextSyncRun,
    RoleGap,
)


async def build_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class FakeERPNext:
    def __init__(self):
        self.customer_name = "Acme"

    async def validate(self):
        return {
            "status": "ready",
            "provider": "erpnext",
            "mode": "live",
            "configured": True,
            "detail": "ok",
        }

    async def get_doc(self, doctype, name):
        if doctype == "Global Defaults":
            return {
                "name": name,
                "default_company": "Hyper AI Lab",
                "default_currency": "USD",
                "country": "United States",
                "api_secret": "must-not-leak",
            }
        if doctype == "System Settings":
            return {"name": name, "country": "United States", "time_zone": "UTC"}
        return {"name": name}

    async def list_docs(self, doctype, filters=None, fields=None, limit=None):
        records = {
            "Company": [
                {
                    "name": "Hyper AI Lab",
                    "company_name": "Hyper AI Lab",
                    "country": "United States",
                    "default_currency": "USD",
                    "abbr": "HAL",
                }
            ],
            "Customer": [{"name": "CUST-1", "customer_name": self.customer_name}],
            "Supplier": [{"name": "SUP-1", "supplier_name": "Cloud Vendor"}],
            "Project": [{"name": "PROJ-1", "project_name": "Cyber-Team"}],
            "Task": [{"name": "TASK-1", "subject": "Ship context sync", "status": "Open"}],
            "Issue": [{"name": "ISS-1", "subject": "Support request", "status": "Open"}],
            "Item": [{"name": "ITEM-1", "item_name": "AI Company OS"}],
        }.get(doctype, [])
        allowed = set(fields or [])
        return [
            {key: value for key, value in record.items() if key in allowed}
            for record in records
        ][: limit or len(records)]


class MultiCompanyERPNext(FakeERPNext):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def list_docs(self, doctype, filters=None, fields=None, limit=None):
        self.calls.append({"doctype": doctype, "filters": filters})
        records = {
            "Company": [
                {
                    "name": "Cyber-Team Smoke Company",
                    "company_name": "Cyber-Team Smoke Company",
                    "country": "United States",
                    "default_currency": "USD",
                    "abbr": "CTSMK",
                },
                {
                    "name": "Hyper AI Lab",
                    "company_name": "Hyper AI Lab",
                    "country": "Japan",
                    "default_currency": "JPY",
                    "abbr": "HAL",
                },
                {
                    "name": "Hyper AI Lab (Demo)",
                    "company_name": "Hyper AI Lab (Demo)",
                    "country": "Japan",
                    "default_currency": "JPY",
                    "abbr": "HALD",
                },
            ],
            "Account": [
                {
                    "name": "Cash - HAL",
                    "account_name": "Cash",
                    "company": "Hyper AI Lab",
                    "account_type": "Cash",
                    "root_type": "Asset",
                    "is_group": 0,
                }
            ],
            "Customer": [
                {
                    "name": "Demo Customer",
                    "customer_name": "Demo Customer",
                    "customer_group": "Demo Customer Group",
                },
                {
                    "name": "Real Customer",
                    "customer_name": "Real Customer",
                    "customer_group": "Commercial",
                },
            ],
            "Supplier": [
                {
                    "name": "Demo Supplier",
                    "supplier_name": "Demo Supplier",
                    "supplier_group": "Demo Supplier Group",
                }
            ],
            "Lead": [
                {
                    "name": "LEAD-SMOKE",
                    "lead_name": "Cyber-Team API ERPNext smoke 20260727T000000Z",
                    "status": "Do Not Contact",
                },
                {"name": "LEAD-REAL", "lead_name": "Real Lead", "status": "Lead"},
            ],
            "Task": [
                {
                    "name": "TASK-SMOKE",
                    "subject": "Cyber-Team ERPNext smoke 20260727T000000Z",
                    "status": "Completed",
                },
                {"name": "TASK-REAL", "subject": "Ship product", "status": "Open"},
            ],
            "Issue": [
                {
                    "name": "ISS-SMOKE",
                    "subject": "Cyber-Team API ERPNext smoke 20260727T000000Z issue",
                    "status": "Closed",
                }
            ],
            "Item": [
                {
                    "name": "SKU-DEMO",
                    "item_name": "Demo Item",
                    "item_group": "Demo Item Group",
                },
                {
                    "name": "SKU-REAL",
                    "item_name": "AI Company OS",
                    "item_group": "Products",
                },
            ],
        }.get(doctype, [])
        allowed = set(fields or [])
        return [
            {key: value for key, value in record.items() if key in allowed}
            for record in records
        ][: limit or len(records)]


class FakeAgentManager:
    def __init__(self):
        self.list_agents = AsyncMock(return_value=[])
        self.list_role_manifests = AsyncMock(return_value=[])
        self.create_role_manifest = AsyncMock()
        self.instantiate_role = AsyncMock()
        self.report_role_gap = AsyncMock()


@pytest.mark.asyncio
async def test_role_gap_reconciliation_closes_fulfilled_snapshot_recommendation():
    engine, session_factory = await build_session_factory()
    manager = FakeAgentManager()
    manager.list_agents.return_value = [
        {
            "id": "finance-agent",
            "role_family": "finance",
            "role_name": (
                "Review ERPNext-derived role: Finance & Accounting Agent (Baseline)"
            ),
            "status": "active",
        }
    ]
    now = utc_now()
    snapshot = CompanyContextSnapshot(
        id="ctx-current",
        source="erpnext",
        source_id="erpnext.example.com",
        source_hash="current-hash",
        company_namespace="company:acme",
        normalized_profile={"company_name": "Acme"},
        erpnext_summary={},
        operating_model={},
        status="active",
        created_by="test",
        created_at=now,
    )
    gap = RoleGap(
        id="gap-finance",
        title="Review ERPNext-derived role: Finance & Accounting Agent",
        description="Review finance role.",
        status="proposed",
        severity="medium",
        source_type="company_context_snapshot",
        company_namespace="company:acme",
        capability="finance",
        requested_tools=["memory_recall"],
        context={
            "snapshot_id": snapshot.id,
            "role_name": "Finance & Accounting Agent",
            "role_family": "finance",
        },
        proposed_role={},
        resolution={},
        created_at=now,
        updated_at=now,
    )
    approval = ApprovalRequest(
        id="approval-finance",
        action_type="role_gap.apply",
        action_description="Apply finance role.",
        action_payload={},
        requester="system",
        requester_type="agent",
        risk_level="medium",
        target_type="role_gap",
        target_id=gap.id,
        status="pending",
        created_at=now,
        expires_at=now + timedelta(days=1),
    )
    try:
        async with session_factory() as session:
            session.add_all([snapshot, gap, approval])
            await session.commit()
        service = CompanyContextSyncService(
            erpnext=FakeERPNext(),
            agent_manager=manager,
            memory_service=FakeMemory(),
            tool_registry=FakeToolRegistry(),
            session_factory=session_factory,
        )

        result = await service.reconcile_fulfilled_role_gaps(
            snapshot_id=snapshot.id,
            actor="test-reconciler",
        )

        assert result["role_gap_ids"] == [gap.id]
        assert result["agent_ids"] == ["finance-agent"]
        assert result["rejected_approval_ids"] == [approval.id]
        async with session_factory() as session:
            stored_gap = await session.get(RoleGap, gap.id)
            stored_approval = await session.get(ApprovalRequest, approval.id)
        assert stored_gap.status == "resolved"
        assert stored_gap.resolution["reason"] == "fulfilled_by_active_equivalent_agent"
        assert stored_approval.status == "rejected"
    finally:
        await engine.dispose()


class FakeMemory:
    def __init__(self):
        self.remember = AsyncMock(side_effect=self._remember)
        self._count = 0

    async def _remember(self, data):
        self._count += 1
        return {
            "id": f"mem_{self._count}",
            "namespace": data.namespace,
            "content": data.content,
        }


class FakeToolRegistry:
    def list_tools(self):
        return []

    def get_tool(self, name):
        return None

    def get_tool_readiness(self, name):
        return {
            "state": "unavailable",
            "readiness_reason": f"{name} not registered",
            "side_effects": False,
            "requires_configuration": False,
        }


@pytest.mark.asyncio
async def test_context_uses_global_default_company_filters_and_excludes_fixtures(monkeypatch):
    monkeypatch.setattr(
        "cyber_team.company.context_sync.settings.erpnext_primary_company",
        "",
    )
    monkeypatch.setattr(
        "cyber_team.company.context_sync.settings.erpnext_context_exclude_fixture_records",
        True,
    )
    erpnext = MultiCompanyERPNext()
    service = CompanyContextSyncService(
        erpnext=erpnext,
        agent_manager=FakeAgentManager(),
        memory_service=FakeMemory(),
        tool_registry=FakeToolRegistry(),
    )

    fetched = await service._fetch_erpnext_context()
    profile = service._normalize_company_profile(fetched)
    summary = fetched["summary"]

    assert profile["company_name"] == "Hyper AI Lab"
    assert profile["source_company"] == "Hyper AI Lab"
    assert profile["company_selection_source"] == "erpnext_global_defaults"
    assert summary["scope"]["primary_company"] == "Hyper AI Lab"
    assert summary["scope"]["available_companies"] == [
        "Cyber-Team Smoke Company",
        "Hyper AI Lab",
        "Hyper AI Lab (Demo)",
    ]
    assert summary["records"]["Company"] == [
        {
            "name": "Hyper AI Lab",
            "company_name": "Hyper AI Lab",
            "country": "Japan",
            "default_currency": "JPY",
            "abbr": "HAL",
        }
    ]
    assert [record["customer_name"] for record in summary["records"]["Customer"]] == [
        "Real Customer"
    ]
    assert summary["records"]["Supplier"] == []
    assert [record["lead_name"] for record in summary["records"]["Lead"]] == [
        "Real Lead"
    ]
    assert [record["subject"] for record in summary["records"]["Task"]] == [
        "Ship product"
    ]
    assert summary["records"]["Issue"] == []
    assert [record["item_name"] for record in summary["records"]["Item"]] == [
        "AI Company OS"
    ]
    assert summary["scope"]["excluded_fixture_counts"] == {
        "Customer": 1,
        "Supplier": 1,
        "Lead": 1,
        "Task": 1,
        "Issue": 1,
        "Item": 1,
    }
    account_call = next(call for call in erpnext.calls if call["doctype"] == "Account")
    assert account_call["filters"] == {"company": "Hyper AI Lab"}


def test_primary_company_selection_honors_override_and_fails_ambiguous(monkeypatch):
    companies = [
        {"name": "Alpha", "company_name": "Alpha"},
        {"name": "Beta", "company_name": "Beta"},
    ]
    monkeypatch.setattr(
        "cyber_team.company.context_sync.settings.erpnext_primary_company",
        "Beta",
    )

    selected, source = CompanyContextSyncService._select_primary_company(companies, {})

    assert selected["name"] == "Beta"
    assert source == "configuration"

    monkeypatch.setattr(
        "cyber_team.company.context_sync.settings.erpnext_primary_company",
        "",
    )
    with pytest.raises(RuntimeError, match="multiple companies"):
        CompanyContextSyncService._select_primary_company(companies, {})


def test_excluded_fixture_counts_do_not_change_context_hash():
    first = {
        "scope": {
            "primary_company": "Hyper AI Lab",
            "excluded_fixture_counts": {"Task": 1},
        },
        "records": {"Task": []},
    }
    second = {
        "scope": {
            "primary_company": "Hyper AI Lab",
            "excluded_fixture_counts": {"Task": 12, "Issue": 4},
        },
        "records": {"Task": []},
    }

    first_basis = CompanyContextSyncService._erpnext_summary_hash_basis(first)
    second_basis = CompanyContextSyncService._erpnext_summary_hash_basis(second)

    assert first_basis["scope"].get("excluded_fixture_counts") is None
    assert second_basis["scope"].get("excluded_fixture_counts") is None
    assert CompanyContextSyncService._source_hash(first_basis) == (
        CompanyContextSyncService._source_hash(second_basis)
    )


@pytest.mark.asyncio
async def test_erpnext_company_context_sync_creates_snapshot_and_noops_on_same_hash():
    engine, session_factory = await build_session_factory()
    memory = FakeMemory()
    try:
        service = CompanyContextSyncService(
            erpnext=FakeERPNext(),
            agent_manager=FakeAgentManager(),
            memory_service=memory,
            tool_registry=FakeToolRegistry(),
            session_factory=session_factory,
        )

        first = await service.sync_from_erpnext(
            actor="owner@example.com",
            run_planner=False,
        )
        second = await service.sync_from_erpnext(
            actor="owner@example.com",
            run_planner=False,
        )
        latest = await service.get_latest_context()
        runs = await service.list_sync_runs()

        assert first["status"] == "synced"
        assert first["snapshot"]["normalized_profile"]["name"] == "Hyper AI Lab"
        assert first["snapshot"]["company_namespace"] == "company:hyper_ai_lab"
        assert first["snapshot"]["memory_ids"]
        assert "must-not-leak" not in str(first["snapshot"]["erpnext_summary"])
        assert second["status"] == "noop"
        assert second["snapshot"]["id"] == first["snapshot"]["id"]
        assert memory.remember.await_count == len(first["snapshot"]["memory_ids"])
        assert latest["freshness"]["status"] == "ready"
        assert [run["status"] for run in runs[:2]] == ["noop", "synced"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_recent_noop_sync_verifies_old_company_context_snapshot():
    engine, session_factory = await build_session_factory()
    try:
        service = CompanyContextSyncService(
            erpnext=FakeERPNext(),
            agent_manager=FakeAgentManager(),
            memory_service=FakeMemory(),
            tool_registry=FakeToolRegistry(),
            session_factory=session_factory,
        )

        first = await service.sync_from_erpnext(actor="owner@example.com", run_planner=False)
        await service.sync_from_erpnext(actor="owner@example.com", run_planner=False)

        old_snapshot_at = utc_now() - timedelta(hours=25)
        recent_verification_at = utc_now() - timedelta(minutes=2)
        async with session_factory() as session:
            snapshot = (
                await session.execute(
                    select(CompanyContextSnapshot).where(
                        CompanyContextSnapshot.id == first["snapshot"]["id"]
                    )
                )
            ).scalar_one()
            latest_noop = (
                await session.execute(
                    select(CompanyContextSyncRun).where(
                        CompanyContextSyncRun.status == "noop"
                    )
                )
            ).scalar_one()
            snapshot.created_at = old_snapshot_at
            for run in (
                await session.execute(
                    select(CompanyContextSyncRun).where(
                        CompanyContextSyncRun.status != "noop"
                    )
                )
            ).scalars():
                run.started_at = old_snapshot_at - timedelta(minutes=1)
                run.completed_at = old_snapshot_at
            latest_noop.started_at = recent_verification_at - timedelta(minutes=1)
            latest_noop.completed_at = recent_verification_at
            await session.commit()

        latest = await service.get_latest_context()

        assert latest["freshness"]["status"] == "ready"
        assert latest["freshness"]["stale"] is False
        assert latest["freshness"]["freshness_basis"] == "sync_verification"
        assert latest["freshness"]["snapshot_created_at"] == old_snapshot_at.isoformat()
        assert latest["freshness"]["last_verified_at"] == recent_verification_at.isoformat()
        assert latest["freshness"]["last_sync_at"] == recent_verification_at.isoformat()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_dry_run_records_sync_run_without_snapshot_or_memory():
    engine, session_factory = await build_session_factory()
    memory = FakeMemory()
    try:
        service = CompanyContextSyncService(
            erpnext=FakeERPNext(),
            agent_manager=FakeAgentManager(),
            memory_service=memory,
            tool_registry=FakeToolRegistry(),
            session_factory=session_factory,
        )

        result = await service.sync_from_erpnext(
            actor="owner@example.com",
            dry_run=True,
            run_planner=False,
        )
        latest = await service.get_latest_context()
        runs = await service.list_sync_runs()

        assert result["status"] == "dry_run"
        assert result["snapshot"]["source_hash"]
        assert latest["snapshot"] is None
        assert latest["freshness"]["status"] == "missing"
        assert runs[0]["status"] == "dry_run"
        memory.remember.assert_not_awaited()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_drift_scan_noops_when_erpnext_hash_is_unchanged():
    engine, session_factory = await build_session_factory()
    memory = FakeMemory()
    try:
        service = CompanyContextSyncService(
            erpnext=FakeERPNext(),
            agent_manager=FakeAgentManager(),
            memory_service=memory,
            tool_registry=FakeToolRegistry(),
            session_factory=session_factory,
        )

        first = await service.sync_from_erpnext(actor="owner@example.com", run_planner=False)
        scan = await service.scan_for_erpnext_drift(
            actor="scheduler",
            apply_low_risk=False,
            run_planner=False,
        )
        runs = await service.list_sync_runs()

        assert scan["status"] == "unchanged"
        assert scan["drift"]["detected"] is False
        assert scan["drift"]["previous_snapshot_id"] == first["snapshot"]["id"]
        assert scan["drift"]["stale_role_gaps"]["count"] == 0
        assert runs[0]["result"]["drift"]["status"] == "unchanged"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unchanged_drift_reconciles_superseded_snapshot_approval():
    engine, session_factory = await build_session_factory()
    memory = FakeMemory()
    try:
        service = CompanyContextSyncService(
            erpnext=FakeERPNext(),
            agent_manager=FakeAgentManager(),
            memory_service=memory,
            tool_registry=FakeToolRegistry(),
            session_factory=session_factory,
        )
        current = await service.sync_from_erpnext(
            actor="owner@example.com",
            run_planner=False,
        )
        now = utc_now()
        old_snapshot = CompanyContextSnapshot(
            id="ctx_superseded",
            source="erpnext",
            source_id="erpnext.example.com",
            source_hash="hash-superseded",
            company_namespace="company:old",
            normalized_profile={"company_name": "Old"},
            erpnext_summary={},
            operating_model={},
            status="superseded",
            created_by="test",
            created_at=now - timedelta(days=1),
        )
        approval = ApprovalRequest(
            id="approval_superseded",
            action_type="autonomous_task.review",
            action_description="Review superseded context.",
            action_payload={"source_id": old_snapshot.id},
            requester="scheduler",
            requester_type="agent",
            risk_level="medium",
            target_type="autonomous_task",
            target_id="task_superseded",
            status="pending",
            created_at=now,
            expires_at=now + timedelta(days=1),
        )
        plan = AutonomousPlan(
            id="plan_superseded",
            title="Superseded plan",
            objective="Review superseded context.",
            source_type="company_context_snapshot",
            source_id=old_snapshot.id,
            status="waiting_approval",
            priority="medium",
            context={},
            summary={},
            created_at=now,
            updated_at=now,
        )
        task = AutonomousTask(
            id="task_superseded",
            plan_id=plan.id,
            sequence=1,
            title="Review superseded context",
            description="Owner review.",
            task_type="company_context.report_risky_roles",
            status="waiting_approval",
            action_payload={"snapshot_id": old_snapshot.id},
            result={"approval_id": approval.id},
            approval_id=approval.id,
            autonomous_allowed=False,
            risk_level="medium",
            created_at=now,
            updated_at=now,
        )
        async with session_factory() as session:
            session.add_all([old_snapshot, approval, plan, task])
            await session.commit()

        scan = await service.scan_for_erpnext_drift(
            actor="scheduler",
            apply_low_risk=False,
            run_planner=False,
        )
        repeated = await service.scan_for_erpnext_drift(
            actor="scheduler",
            apply_low_risk=False,
            run_planner=False,
        )

        assert scan["status"] == "unchanged"
        assert scan["drift"]["current_snapshot_id"] == current["snapshot"]["id"]
        assert scan["drift"]["stale_reviews"]["approval_ids"] == [
            "approval_superseded"
        ]
        assert repeated["drift"]["stale_reviews"] == {
            "plan_count": 0,
            "task_count": 0,
            "approval_count": 0,
            "plan_ids": [],
            "task_ids": [],
            "approval_ids": [],
        }
        async with session_factory() as session:
            reconciled = await session.get(ApprovalRequest, approval.id)
            assert reconciled is not None
            assert reconciled.status == "rejected"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_drift_scan_stales_previous_company_context_role_gaps_on_new_hash():
    engine, session_factory = await build_session_factory()
    erpnext = FakeERPNext()
    memory = FakeMemory()
    try:
        service = CompanyContextSyncService(
            erpnext=erpnext,
            agent_manager=FakeAgentManager(),
            memory_service=memory,
            tool_registry=FakeToolRegistry(),
            session_factory=session_factory,
        )
        first = await service.sync_from_erpnext(actor="owner@example.com", run_planner=False)
        old_snapshot = first["snapshot"]
        async with session_factory() as session:
            approval = ApprovalRequest(
                id="approval_old_context",
                action_type="autonomous_task.review",
                action_description="Review old company context.",
                action_payload={"source_id": old_snapshot["id"]},
                requester="scheduler",
                requester_type="agent",
                risk_level="medium",
                target_type="autonomous_task",
                target_id="task_old_context",
                status="pending",
                created_at=utc_now(),
                expires_at=utc_now() + timedelta(days=1),
            )
            plan = AutonomousPlan(
                id="plan_old_context",
                title="Old company context plan",
                objective="Review old context.",
                source_type="company_context_snapshot",
                source_id=old_snapshot["id"],
                status="waiting_approval",
                priority="medium",
                context={"source_hash": old_snapshot["source_hash"]},
                summary={},
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            task = AutonomousTask(
                id="task_old_context",
                plan_id=plan.id,
                sequence=1,
                title="Review old context roles",
                description="Owner review for old context.",
                task_type="company_context.report_risky_roles",
                status="waiting_approval",
                target_type="company_context_snapshot",
                target_id=old_snapshot["id"],
                action_payload={"snapshot_id": old_snapshot["id"]},
                result={"approval_id": approval.id},
                approval_id=approval.id,
                autonomous_allowed=False,
                risk_level="medium",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add_all([approval, plan, task])
            session.add(
                RoleGap(
                    id="gap_old_sales",
                    title="Review ERPNext-derived role: Sales",
                    description="Old context role gap.",
                    status="proposed",
                    severity="medium",
                    source_agent_id=None,
                    source_type="company_context_snapshot",
                    company_namespace=old_snapshot["company_namespace"],
                    capability="sales",
                    requested_tools=["erpnext_create_lead"],
                    context={
                        "snapshot_id": old_snapshot["id"],
                        "source_hash": old_snapshot["source_hash"],
                    },
                    proposed_role={},
                    resolution={},
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            )
            await session.commit()

        erpnext.customer_name = "Globex"
        scan = await service.scan_for_erpnext_drift(
            actor="scheduler",
            apply_low_risk=False,
            run_planner=False,
        )

        async with session_factory() as session:
            gap = (
                await session.execute(
                    select(RoleGap).where(RoleGap.id == "gap_old_sales")
                )
            ).scalar_one()
            approval = await session.get(ApprovalRequest, "approval_old_context")
            plan = await session.get(AutonomousPlan, "plan_old_context")
            task = await session.get(AutonomousTask, "task_old_context")
            snapshots = (
                await session.execute(
                    select(CompanyContextSnapshot).order_by(
                        CompanyContextSnapshot.created_at
                    )
                )
            ).scalars().all()

        assert scan["status"] == "changed"
        assert scan["drift"]["detected"] is True
        assert scan["drift"]["previous_snapshot_id"] == old_snapshot["id"]
        assert scan["drift"]["current_snapshot_id"] != old_snapshot["id"]
        assert scan["drift"]["stale_role_gaps"]["role_gap_ids"] == ["gap_old_sales"]
        assert gap.status == "stale"
        assert gap.context["superseded_by_snapshot_id"] == scan["drift"]["current_snapshot_id"]
        assert gap.resolution["reason"] == "superseded_by_company_context_drift"
        assert scan["drift"]["stale_reviews"] == {
            "plan_count": 1,
            "task_count": 1,
            "approval_count": 1,
            "plan_ids": ["plan_old_context"],
            "task_ids": ["task_old_context"],
            "approval_ids": ["approval_old_context"],
        }
        assert approval is not None
        assert approval.status == "rejected"
        assert approval.reviewer == "scheduler"
        assert "superseded" in (approval.review_note or "").lower()
        assert plan is not None
        assert plan.status == "blocked"
        assert plan.context["superseded_by_snapshot_id"] == scan["drift"][
            "current_snapshot_id"
        ]
        assert task is not None
        assert task.status == "blocked"
        assert task.completed_at is not None
        assert [snapshot.status for snapshot in snapshots] == ["superseded", "active"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_drift_status_separates_latest_and_historical_stale_role_gaps():
    engine, session_factory = await build_session_factory()
    agent_manager = FakeAgentManager()
    agent_manager.summarize_role_backlog = AsyncMock(
        return_value={"counts": {"total": 3}}
    )
    try:
        service = CompanyContextSyncService(
            erpnext=FakeERPNext(),
            agent_manager=agent_manager,
            memory_service=FakeMemory(),
            tool_registry=FakeToolRegistry(),
            session_factory=session_factory,
        )

        await service.sync_from_erpnext(actor="owner@example.com", run_planner=False)
        scan = await service.scan_for_erpnext_drift(
            actor="scheduler",
            apply_low_risk=False,
            run_planner=False,
        )
        status = await service.drift_status()

        assert scan["drift"]["stale_role_gaps"]["count"] == 0
        assert status["latest_drift"]["status"] == "unchanged"
        assert status["stale_role_gap_count"] == 0
        assert status["historical_stale_role_gap_count"] == 3
    finally:
        await engine.dispose()
