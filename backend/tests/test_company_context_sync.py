from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.clock import utc_now
from cyber_team.company.context_sync import CompanyContextSyncService
from cyber_team.db import Base
from cyber_team.db.models import CompanyContextSnapshot, CompanyContextSyncRun, RoleGap


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


class FakeAgentManager:
    def __init__(self):
        self.list_role_manifests = AsyncMock(return_value=[])
        self.create_role_manifest = AsyncMock()
        self.instantiate_role = AsyncMock()
        self.report_role_gap = AsyncMock()


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

        assert scan["status"] == "changed"
        assert scan["drift"]["detected"] is True
        assert scan["drift"]["previous_snapshot_id"] == old_snapshot["id"]
        assert scan["drift"]["current_snapshot_id"] != old_snapshot["id"]
        assert scan["drift"]["stale_role_gaps"]["role_gap_ids"] == ["gap_old_sales"]
        assert gap.status == "stale"
        assert gap.context["superseded_by_snapshot_id"] == scan["drift"]["current_snapshot_id"]
        assert gap.resolution["reason"] == "superseded_by_company_context_drift"
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
