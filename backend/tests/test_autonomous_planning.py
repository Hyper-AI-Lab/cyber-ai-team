from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.db import Base
from cyber_team.db.models import CompanyContextSnapshot, MemoryStewardFinding, RoleGap
from cyber_team.operations.planning import AutonomousPlanningService


async def build_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def role_gap(
    gap_id: str = "gap_1",
    *,
    status: str = "open",
    severity: str = "high",
    requested_tools: list[str] | None = None,
) -> RoleGap:
    now = datetime(2026, 6, 3, 12, 0, 0)
    return RoleGap(
        id=gap_id,
        title="Need outbound calling",
        description="Sales is blocked until a specialist can call customers.",
        status=status,
        severity=severity,
        source_agent_id="sales",
        source_type="agent",
        company_namespace="company:acme",
        capability="outbound_voice",
        requested_tools=["make_call"] if requested_tools is None else requested_tools,
        context={},
        proposed_role={},
        resolution={},
        created_at=now - timedelta(hours=1),
        updated_at=now - timedelta(hours=1),
    )


def memory_finding(
    finding_id: str = "finding_1",
    *,
    severity: str = "medium",
) -> MemoryStewardFinding:
    now = datetime(2026, 6, 3, 12, 0, 0)
    return MemoryStewardFinding(
        id=finding_id,
        finding_type="missing_write",
        severity=severity,
        status="open",
        agent_id="sales",
        memory_namespace="company:acme:sales",
        company_namespace="company:acme",
        title="Missing customer preference memory",
        description="Customer preference was used but not written to memory.",
        recommendation="Store a durable customer preference memory.",
        trace_ids=["trace_1"],
        evidence={},
        metadata_={},
        created_at=now - timedelta(hours=1),
        updated_at=now - timedelta(hours=1),
    )


class FakeTool:
    def __init__(
        self,
        name: str,
        *,
        risk_level: str = "low",
        requires_approval: bool = False,
        category: str = "general",
    ):
        self.name = name
        self.risk_level = risk_level
        self.requires_approval = requires_approval
        self.category = category
        self.description = f"{name} tool"

    def contract(self):
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "requires_approval": self.requires_approval,
            "risk_level": self.risk_level,
        }


class FakeToolRegistry:
    def __init__(self, tools: list[FakeTool] | None = None):
        self.tools = {tool.name: tool for tool in tools or []}

    def get_tool(self, name: str):
        return self.tools.get(name)

    def list_tools(self):
        return list(self.tools.values())


class FakeAgentManager:
    def __init__(self, apply_result, *, approval_executable: bool = False):
        self.apply_result = apply_result
        self.propose_role_for_gap = AsyncMock(side_effect=self._propose)
        self.apply_role_gap_proposal = AsyncMock(side_effect=self._apply)
        self.approval_is_executable = AsyncMock(return_value=approval_executable)
        self.consume_approval = AsyncMock()
        self._request_approval = AsyncMock(return_value="review_approval_1")

    async def _propose(self, gap_id):
        return {
            "id": gap_id,
            "status": "proposed",
            "proposed_role": {
                "manifest_payload": {"name": "Outbound Calling Specialist"}
            },
        }

    async def _apply(self, gap_id, approval_id=None, requested_by="owner"):
        result = dict(self.apply_result)
        result.setdefault("id", gap_id)
        return result


class FakeMemorySteward:
    def __init__(self):
        self.findings = {}
        self.plan_remediations = AsyncMock(side_effect=self._plan_remediations)

    async def get_finding(self, finding_id):
        return self.findings.get(finding_id)

    async def _plan_remediations(self, **kwargs):
        for finding in self.findings.values():
            finding.setdefault("metadata", {})["remediation_plan"] = {
                "status": "applied",
                "action_type": "seed_memory",
            }
            finding["status"] = "resolved"
        return {"actions_applied": len(self.findings)}


class FakeCompanyContext:
    def __init__(self, *, unsafe_role_count: int = 1):
        self.unsafe_role_count = unsafe_role_count
        self.assess_snapshot = AsyncMock(side_effect=self._assess)
        self.seed_snapshot_memory = AsyncMock(
            return_value={"created_memory_ids": ["mem_1"], "already_seeded": False}
        )
        self.apply_snapshot_low_risk_roles = AsyncMock(
            return_value={
                "agent_ids": ["company_memory_steward"],
                "role_manifest_ids": ["company_memory_steward"],
                "skipped_role_specs": [{"name": "Sales & CRM Agent"}],
            }
        )
        self.report_snapshot_risky_role_gaps = AsyncMock(
            return_value={
                "role_gap_ids": ["gap_sales"],
                "unsafe_role_count": unsafe_role_count,
            }
        )

    async def _assess(self, snapshot_id):
        return {
            "snapshot_id": snapshot_id,
            "source_hash": "hash-1",
            "company_namespace": "company:hyper_ai_lab",
            "counts": {"planned_roles": 2},
            "safe_role_count": 1,
            "unsafe_role_count": self.unsafe_role_count,
            "capability_gap_count": 0,
            "errors": [],
        }


def company_context_snapshot(snapshot_id: str = "ctx_1") -> CompanyContextSnapshot:
    now = datetime(2026, 6, 3, 12, 0, 0)
    return CompanyContextSnapshot(
        id=snapshot_id,
        source="erpnext",
        source_id="erpnext.hyperailab.com",
        source_hash="hash-1",
        company_namespace="company:hyper_ai_lab",
        status="active",
        normalized_profile={"name": "Hyper AI Lab"},
        erpnext_summary={"counts": {"Company": 1}},
        operating_model={"planned_role_specs": []},
        memory_ids=[],
        agent_ids=[],
        role_manifest_ids=[],
        role_gap_ids=[],
        approval_ids=[],
        plan_ids=[],
        errors=[],
        created_by="owner@example.com",
        created_at=now,
    )


def tool_registry(*tools: FakeTool) -> FakeToolRegistry:
    return FakeToolRegistry(list(tools))


def high_risk_tool_registry() -> FakeToolRegistry:
    return tool_registry(
        FakeTool(
            "make_call",
            risk_level="high",
            requires_approval=True,
            category="communications",
        )
    )


def low_risk_tool_registry() -> FakeToolRegistry:
    return tool_registry(FakeTool("progress_report", risk_level="low"))


@pytest.mark.asyncio
async def test_scan_creates_role_gap_graph_and_waits_for_owner_review():
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
            tool_registry=high_risk_tool_registry(),
            session_factory=session_factory,
        )

        result = await service.scan_and_plan(
            actor="test",
            include_memory_findings=False,
            auto_execute=True,
        )

        assert result["plans_created"] == 1
        assert result["execution"]["plans_waiting_approval"] == 1
        plan = (await service.list_plans())[0]
        assert plan["status"] == "waiting_approval"
        assert plan["context"]["policy"]["max_risk"] == "high"
        assert [task["task_type"] for task in plan["tasks"]] == [
            "plan.risk_assess",
            "tools.readiness_check",
            "role_gap.propose",
            "plan.owner_review",
            "role_gap.apply",
        ]
        assert [task["status"] for task in plan["tasks"]] == [
            "completed",
            "completed",
            "completed",
            "waiting_approval",
            "planned",
        ]
        assert plan["tasks"][3]["approval_id"] == "review_approval_1"
        manager._request_approval.assert_awaited_once()
        manager.apply_role_gap_proposal.assert_not_awaited()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_company_context_snapshot_plan_executes_safe_tasks_and_waits_for_review():
    engine, session_factory = await build_session_factory()
    manager = FakeAgentManager({"status": "resolved"})
    company_context = FakeCompanyContext(unsafe_role_count=1)
    try:
        async with session_factory() as session:
            session.add(company_context_snapshot())
            await session.commit()

        service = AutonomousPlanningService(
            agent_manager=manager,
            memory_steward_service=FakeMemorySteward(),
            tool_registry=low_risk_tool_registry(),
            company_context_service=company_context,
            session_factory=session_factory,
        )

        result = await service.scan_and_plan(
            actor="test",
            include_role_gaps=False,
            include_memory_findings=False,
            include_company_context=True,
            auto_execute=True,
        )

        assert result["plans_created"] == 1
        assert result["execution"]["plans_waiting_approval"] == 1
        plan = (await service.list_plans())[0]
        assert plan["source_type"] == "company_context_snapshot"
        assert [task["task_type"] for task in plan["tasks"]] == [
            "company_context.assess",
            "company_context.seed_memory",
            "company_context.apply_low_risk_roles",
            "company_context.report_risky_roles",
            "plan.owner_review",
        ]
        assert [task["status"] for task in plan["tasks"]] == [
            "completed",
            "completed",
            "completed",
            "completed",
            "waiting_approval",
        ]
        company_context.seed_snapshot_memory.assert_awaited_once_with(
            "ctx_1",
            actor="test",
        )
        company_context.apply_snapshot_low_risk_roles.assert_awaited_once_with(
            "ctx_1",
            actor="test",
        )
        company_context.report_snapshot_risky_role_gaps.assert_awaited_once_with(
            "ctx_1",
            actor="test",
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_owner_approved_role_gap_plan_continues_after_review():
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
            tool_registry=high_risk_tool_registry(),
            session_factory=session_factory,
        )

        first = await service.scan_and_plan(
            actor="test",
            include_memory_findings=False,
            auto_execute=True,
        )
        plan_id = first["created_plan_ids"][0]
        manager.approval_is_executable.return_value = True

        result = await service.execute_plan(plan_id, actor="test")

        assert result["status"] == "completed"
        plan = await service.get_plan(plan_id)
        assert plan["status"] == "completed"
        assert [task["status"] for task in plan["tasks"]] == [
            "completed",
            "completed",
            "completed",
            "completed",
            "completed",
        ]
        manager.consume_approval.assert_awaited_once_with(
            "review_approval_1",
            consumer="autonomous_planner",
            target_type="autonomous_task",
            target_id=plan["tasks"][3]["id"],
        )
        manager.apply_role_gap_proposal.assert_awaited_once_with(
            "gap_1",
            approval_id=None,
            requested_by="test",
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tool_readiness_blocks_missing_requested_tools():
    engine, session_factory = await build_session_factory()
    manager = FakeAgentManager({"status": "resolved", "resolution": {}})
    try:
        async with session_factory() as session:
            session.add(role_gap(severity="low", requested_tools=["missing_tool"]))
            await session.commit()

        service = AutonomousPlanningService(
            agent_manager=manager,
            memory_steward_service=FakeMemorySteward(),
            tool_registry=low_risk_tool_registry(),
            session_factory=session_factory,
        )

        result = await service.scan_and_plan(
            actor="test",
            include_memory_findings=False,
            auto_execute=True,
        )

        assert result["execution"]["plans_blocked"] == 1
        plan = (await service.list_plans())[0]
        assert plan["status"] == "blocked"
        assert plan["tasks"][1]["task_type"] == "tools.readiness_check"
        assert plan["tasks"][1]["status"] == "blocked"
        assert plan["tasks"][1]["result"]["tool_readiness"]["missing_tools"] == [
            "missing_tool"
        ]
        manager.propose_role_for_gap.assert_not_awaited()
        manager.apply_role_gap_proposal.assert_not_awaited()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_low_risk_role_gap_apply_can_wait_on_downstream_approval():
    engine, session_factory = await build_session_factory()
    manager = FakeAgentManager(
        {
            "status": "proposed",
            "approval_required": True,
            "approval_id": "approval_1",
            "high_risk_tools": ["progress_report"],
        }
    )
    try:
        async with session_factory() as session:
            session.add(role_gap(severity="low", requested_tools=["progress_report"]))
            await session.commit()

        service = AutonomousPlanningService(
            agent_manager=manager,
            memory_steward_service=FakeMemorySteward(),
            tool_registry=low_risk_tool_registry(),
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
        assert [task["task_type"] for task in plan["tasks"]] == [
            "plan.risk_assess",
            "tools.readiness_check",
            "role_gap.propose",
            "role_gap.apply",
        ]
        assert plan["tasks"][3]["status"] == "waiting_approval"
        assert plan["tasks"][3]["approval_id"] == "approval_1"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_memory_finding_requires_owner_review_for_medium_risk():
    engine, session_factory = await build_session_factory()
    manager = FakeAgentManager({"status": "resolved", "resolution": {}})
    memory = FakeMemorySteward()
    try:
        async with session_factory() as session:
            session.add(memory_finding(severity="medium"))
            await session.commit()
        memory.findings["finding_1"] = {
            "id": "finding_1",
            "status": "open",
            "metadata": {},
        }

        service = AutonomousPlanningService(
            agent_manager=manager,
            memory_steward_service=memory,
            tool_registry=low_risk_tool_registry(),
            session_factory=session_factory,
        )

        result = await service.scan_and_plan(
            actor="test",
            include_role_gaps=False,
            auto_execute=True,
        )

        assert result["execution"]["plans_waiting_approval"] == 1
        plan = (await service.list_plans())[0]
        assert plan["status"] == "waiting_approval"
        assert [task["task_type"] for task in plan["tasks"]] == [
            "plan.risk_assess",
            "plan.owner_review",
            "memory_finding.remediate",
        ]
        assert plan["tasks"][1]["status"] == "waiting_approval"
        memory.plan_remediations.assert_not_awaited()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_scan_dedupes_active_plans_for_same_role_gap():
    engine, session_factory = await build_session_factory()
    manager = FakeAgentManager({"status": "resolved", "resolution": {}})
    try:
        async with session_factory() as session:
            session.add(role_gap(severity="low", requested_tools=["progress_report"]))
            await session.commit()

        service = AutonomousPlanningService(
            agent_manager=manager,
            memory_steward_service=FakeMemorySteward(),
            tool_registry=low_risk_tool_registry(),
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
