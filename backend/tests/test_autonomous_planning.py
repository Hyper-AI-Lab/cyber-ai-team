from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.db import Base
from cyber_team.db.models import RoleGap
from cyber_team.operations.planning import AutonomousPlanningService


async def build_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def role_gap(gap_id: str = "gap_1", *, status: str = "open") -> RoleGap:
    now = datetime(2026, 6, 3, 12, 0, 0)
    return RoleGap(
        id=gap_id,
        title="Need outbound calling",
        description="Sales is blocked until a specialist can call customers.",
        status=status,
        severity="high",
        source_agent_id="sales",
        source_type="agent",
        company_namespace="company:acme",
        capability="outbound_voice",
        requested_tools=["make_call"],
        context={},
        proposed_role={},
        resolution={},
        created_at=now - timedelta(hours=1),
        updated_at=now - timedelta(hours=1),
    )


class FakeAgentManager:
    def __init__(self, apply_result):
        self.apply_result = apply_result
        self.propose_role_for_gap = AsyncMock(
            return_value={
                "id": "gap_1",
                "status": "proposed",
                "proposed_role": {
                    "manifest_payload": {"name": "Outbound Calling Specialist"}
                },
            }
        )
        self.apply_role_gap_proposal = AsyncMock(side_effect=self._apply)
        self.approval_is_executable = AsyncMock(return_value=False)

    async def _apply(self, gap_id, approval_id=None, requested_by="owner"):
        result = dict(self.apply_result)
        result.setdefault("id", gap_id)
        return result


class FakeMemorySteward:
    async def get_finding(self, finding_id):
        return None

    async def plan_remediations(self, **kwargs):
        return {}


@pytest.mark.asyncio
async def test_scan_creates_and_executes_role_gap_plan():
    engine, session_factory = await build_session_factory()
    manager = FakeAgentManager(
        {
            "status": "resolved",
            "resolution": {
                "agent_id": "outbound_calling_specialist",
                "role_name": "Outbound Calling Specialist",
            },
        }
    )
    try:
        async with session_factory() as session:
            session.add(role_gap())
            await session.commit()

        service = AutonomousPlanningService(
            agent_manager=manager,
            memory_steward_service=FakeMemorySteward(),
            session_factory=session_factory,
        )

        result = await service.scan_and_plan(
            actor="test",
            include_memory_findings=False,
            auto_execute=True,
        )

        assert result["plans_created"] == 1
        assert result["execution"]["plans_completed"] == 1
        plan = (await service.list_plans())[0]
        assert plan["status"] == "completed"
        assert [task["status"] for task in plan["tasks"]] == ["completed", "completed"]
        manager.propose_role_for_gap.assert_awaited_once_with("gap_1")
        manager.apply_role_gap_proposal.assert_awaited_once_with(
            "gap_1",
            approval_id=None,
            requested_by="test",
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_role_gap_plan_waits_when_apply_requires_approval():
    engine, session_factory = await build_session_factory()
    manager = FakeAgentManager(
        {
            "status": "proposed",
            "approval_required": True,
            "approval_id": "approval_1",
            "high_risk_tools": ["make_call"],
        }
    )
    try:
        async with session_factory() as session:
            session.add(role_gap())
            await session.commit()

        service = AutonomousPlanningService(
            agent_manager=manager,
            memory_steward_service=FakeMemorySteward(),
            session_factory=session_factory,
        )

        result = await service.scan_and_plan(
            actor="test",
            include_memory_findings=False,
            auto_execute=True,
        )

        assert result["execution"]["plans_waiting_approval"] == 1
        plan = (await service.list_plans())[0]
        assert plan["status"] == "waiting_approval"
        assert plan["tasks"][1]["status"] == "waiting_approval"
        assert plan["tasks"][1]["approval_id"] == "approval_1"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_scan_dedupes_active_plans_for_same_role_gap():
    engine, session_factory = await build_session_factory()
    manager = FakeAgentManager({"status": "resolved", "resolution": {}})
    try:
        async with session_factory() as session:
            session.add(role_gap())
            await session.commit()

        service = AutonomousPlanningService(
            agent_manager=manager,
            memory_steward_service=FakeMemorySteward(),
            session_factory=session_factory,
        )

        first = await service.scan_and_plan(
            actor="test",
            include_memory_findings=False,
            auto_execute=False,
        )
        second = await service.scan_and_plan(
            actor="test",
            include_memory_findings=False,
            auto_execute=False,
        )

        assert first["plans_created"] == 1
        assert second["plans_created"] == 0
        assert second["plans_existing"] == 1
        assert len(await service.list_plans()) == 1
    finally:
        await engine.dispose()
