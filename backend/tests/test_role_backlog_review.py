from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.agents import manager as manager_module
from cyber_team.agents.manager import AgentManager
from cyber_team.clock import utc_now
from cyber_team.db import Base
from cyber_team.db.models import (
    Agent,
    ApprovalRequest,
    AutonomousPlan,
    AutonomousTask,
    CompanyContextSnapshot,
    RoleGap,
)


class FakeTool:
    def __init__(self, name: str):
        self.name = name


class FakeToolRegistry:
    def __init__(self, tool_names: set[str]):
        self._tool_names = tool_names

    def list_tools(self):
        return [FakeTool(name) for name in sorted(self._tool_names)]

    def get_tool(self, name: str):
        if name in self._tool_names:
            return FakeTool(name)
        return None

    def get_tool_readiness(self, name: str):
        if name in self._tool_names:
            return {
                "state": "live",
                "readiness_reason": "test tool is ready",
                "side_effects": name in AgentManager.HIGH_RISK_ROLE_TOOLS,
                "executor_kind": "live",
                "requires_configuration": False,
                "executable": True,
            }
        return {
            "state": "unavailable",
            "readiness_reason": f"Tool not found: {name}",
            "side_effects": False,
            "executor_kind": "unavailable",
            "requires_configuration": False,
            "executable": False,
        }


@pytest.fixture
async def session_factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(manager_module, "async_session", factory)
    try:
        yield factory
    finally:
        await engine.dispose()


def proposed_role(default_tools: list[str] | None = None) -> dict:
    tools = default_tools or ["make_call", "memory_recall"]
    return {
        "manifest_payload": {
            "family": "communications",
            "name": "Outbound Calling Specialist",
            "description": "Handles outbound calling.",
            "instructions_template": "Use approved call workflows.",
            "default_tools": tools,
            "memory_namespace": "company:acme:gap:outbound_calling_specialist",
            "approval_policy": "sensitive",
            "success_metrics": [],
            "is_core": False,
            "config": {},
        }
    }


async def seed_company_context_gap(factory, *, approval_status: str = "pending"):
    now = utc_now()
    async with factory() as session:
        snapshot = CompanyContextSnapshot(
            id="ctx_1",
            source="erpnext",
            source_hash="hash-1",
            company_namespace="company:acme",
            normalized_profile={"name": "Acme"},
            erpnext_summary={},
            operating_model={},
            role_gap_ids=["gap_1"],
            plan_ids=["plan_1"],
            created_by="test",
        )
        plan = AutonomousPlan(
            id="plan_1",
            title="Apply ERPNext company context",
            objective="Review risky roles.",
            source_type="company_context_snapshot",
            source_id="ctx_1",
            status="waiting_approval",
            priority="medium",
            context={"source_hash": "hash-1"},
        )
        task = AutonomousTask(
            id="task_review",
            plan_id="plan_1",
            sequence=5,
            title="Owner review",
            description="Review role backlog.",
            task_type="plan.owner_review",
            status="waiting_approval",
            target_type="company_context_snapshot",
            target_id="ctx_1",
            action_payload={"review_for": "company_context.report_risky_roles"},
            risk_level="medium",
        )
        gap = RoleGap(
            id="gap_1",
            title="Review ERPNext-derived role: Outbound Calling Specialist",
            description="ERPNext context implies outbound calling.",
            status="proposed",
            severity="medium",
            source_type="company_context_snapshot",
            company_namespace="company:acme",
            capability="communications",
            requested_tools=["make_call"],
            context={
                "snapshot_id": "ctx_1",
                "source_hash": "hash-1",
                "role_family": "communications",
                "dedupe_key": "company_context_role:hash-1:Outbound Calling Specialist",
            },
            proposed_role=proposed_role(),
            resolution={},
        )
        approval = ApprovalRequest(
            id="approval_1",
            action_type="role_gap.tool_grant",
            action_description="Approve generated role",
            action_payload={
                "role_gap_id": "gap_1",
                "role_name": "Outbound Calling Specialist",
                "high_risk_tools": ["make_call"],
                "default_tools": ["make_call", "memory_recall"],
            },
            requester="company_builder",
            requester_type="agent",
            risk_level="high",
            target_type="role_gap",
            target_id="gap_1",
            status=approval_status,
            expires_at=now - timedelta(minutes=1),
        )
        session.add_all([snapshot, plan, task, gap, approval])
        await session.commit()


@pytest.mark.asyncio
async def test_role_backlog_summary_groups_traceability_and_expired_approval(session_factory):
    await seed_company_context_gap(session_factory)
    manager = AgentManager(tool_registry=FakeToolRegistry({"make_call", "memory_recall"}))

    summary = await manager.summarize_role_backlog(
        statuses=["open", "proposed"],
        source_type="company_context_snapshot",
    )

    assert summary["counts"]["total"] == 1
    assert summary["expired_approval_count"] == 1
    assert summary["groups"][0]["business_function"] == "Communications"
    item = summary["items"][0]
    assert item["source_snapshot_id"] == "ctx_1"
    assert item["source_plan_id"] == "plan_1"
    assert item["source_task_id"] == "task_review"
    assert item["approval"]["state"] == "expired"
    assert item["recommended_action"] == "regenerate_approval"


@pytest.mark.asyncio
async def test_regenerate_role_gap_approval_creates_fresh_target_bound_request(session_factory):
    await seed_company_context_gap(session_factory)
    manager = AgentManager(tool_registry=FakeToolRegistry({"make_call", "memory_recall"}))

    result = await manager.regenerate_role_gap_approval(
        "gap_1",
        {"name": "Acme"},
        requested_by="owner@example.com",
    )

    assert result["approval_id"] != "approval_1"
    assert result["item"]["approval"]["state"] == "pending"
    async with session_factory() as session:
        approval = (
            await session.execute(
                select(ApprovalRequest).where(ApprovalRequest.id == result["approval_id"])
            )
        ).scalar_one()
    assert approval.target_type == "role_gap"
    assert approval.target_id == "gap_1"
    assert approval.action_payload["high_risk_tools"] == ["make_call"]


@pytest.mark.asyncio
async def test_apply_role_gap_rejects_approval_that_does_not_cover_requested_tools(
    session_factory,
):
    await seed_company_context_gap(session_factory, approval_status="approved")
    async with session_factory() as session:
        approval = (
            await session.execute(
                select(ApprovalRequest).where(ApprovalRequest.id == "approval_1")
            )
        ).scalar_one()
        approval.expires_at = utc_now() + timedelta(minutes=10)
        approval.action_payload = {
            **approval.action_payload,
            "high_risk_tools": [],
        }
        await session.commit()
    manager = AgentManager(tool_registry=FakeToolRegistry({"make_call", "memory_recall"}))

    with pytest.raises(ValueError, match="does not cover requested tools"):
        await manager.apply_role_gap_proposal("gap_1", {"name": "Acme"}, "approval_1")


@pytest.mark.asyncio
async def test_batch_role_gap_action_reports_partial_failures():
    manager = AgentManager()
    manager.propose_role_for_gap = AsyncMock(
        side_effect=[
            {"id": "gap_1", "status": "proposed"},
            ValueError("Role gap gap_2 is stale"),
        ]
    )
    manager.summarize_role_backlog = AsyncMock(return_value={"counts": {"total": 1}})

    result = await manager.batch_role_gap_action(
        ["gap_1", "gap_2", "gap_1"],
        action="propose",
        company_profile={"name": "Acme"},
        requested_by="owner@example.com",
    )

    assert result["requested_count"] == 2
    assert result["succeeded_count"] == 1
    assert result["failed_count"] == 1
    assert result["errors"][0]["gap_id"] == "gap_2"
    manager.propose_role_for_gap.assert_any_await("gap_1", {"name": "Acme"})
    manager.propose_role_for_gap.assert_any_await("gap_2", {"name": "Acme"})


@pytest.mark.asyncio
async def test_role_operating_cadence_reports_activated_agent_and_backlog_counts(
    session_factory,
):
    async with session_factory() as session:
        session.add(
            Agent(
                id="sales_specialist",
                role_family="sales",
                role_name="Sales Specialist",
                instructions="Review pipeline.",
                tools=["memory_recall"],
                memory_namespace="company:acme:gap:sales",
                approval_policy="sensitive",
                status="active",
                config={
                    "company_namespace": "company:acme",
                    "role_gap_id": "gap_sales",
                    "source_snapshot_id": "ctx_1",
                    "activation_cadence": {
                        "cadence_id": "cadence:gap_sales",
                        "frequency": "daily",
                        "review_window": "Daily pipeline review",
                    },
                },
            )
        )
        session.add(
            RoleGap(
                id="gap_active",
                title="Need finance role",
                description="Finance review is needed.",
                status="proposed",
                severity="medium",
                source_type="company_context_snapshot",
                company_namespace="company:acme",
                capability="finance",
                requested_tools=["memory_recall"],
                context={},
                proposed_role=proposed_role(["memory_recall"]),
                resolution={},
            )
        )
        await session.commit()
    manager = AgentManager(tool_registry=FakeToolRegistry({"memory_recall"}))

    result = await manager.role_operating_cadence(company_namespace="company:acme")

    assert result["counts"]["active_agents"] == 1
    assert result["counts"]["cadences"] == 1
    assert result["counts"]["active_role_gaps"] == 1
    assert result["cadences"][0]["agent_id"] == "sales_specialist"
    assert result["cadences"][0]["cadence"]["frequency"] == "daily"
    assert result["recommended_owner_actions"][0]["action"] == "review_active_role_backlog"
