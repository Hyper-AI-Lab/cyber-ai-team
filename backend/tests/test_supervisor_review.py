from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.config import settings
from cyber_team.db import Base
from cyber_team.db.models import ApprovalRequest, RoleGap, Workflow, WorkflowRun
from cyber_team.operations.supervisor_review import SupervisorReviewService


async def build_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def role_gap(gap_id: str, status: str = "open") -> RoleGap:
    now = datetime(2026, 6, 1, 12, 0, 0)
    return RoleGap(
        id=gap_id,
        title="Need customer calling",
        description="Sales work is blocked until a specialist can call customers.",
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
        created_at=now - timedelta(hours=2),
        updated_at=now - timedelta(hours=2),
    )


class FakeManager:
    def __init__(self):
        self.propose_role_for_gap = AsyncMock(
            side_effect=lambda gap_id: {
                "id": gap_id,
                "status": "proposed",
                "proposed_role": {
                    "manifest_payload": {"name": "Customer Calling Specialist"}
                },
            }
        )
        self.report_role_gap = AsyncMock(
            return_value={"id": "gap_workflow_failure", "status": "open"}
        )


@pytest.mark.asyncio
async def test_supervisor_review_proposes_open_role_gap_and_annotates_it():
    now = datetime(2026, 6, 1, 12, 0, 0)
    engine, session_factory = await build_session_factory()
    manager = FakeManager()

    try:
        async with session_factory() as session:
            session.add(role_gap("gap_1"))
            await session.commit()

        result = await SupervisorReviewService(
            manager,
            session_factory=session_factory,
        ).run_once(now=now, actor="test")

        assert result["role_gaps_reviewed"] == 1
        assert result["role_gaps_proposed"] == ["gap_1"]
        manager.propose_role_for_gap.assert_awaited_once_with("gap_1")

        async with session_factory() as session:
            gap = (
                await session.execute(select(RoleGap).where(RoleGap.id == "gap_1"))
            ).scalar_one()
            review = gap.context["supervisor_review"]
            assert review["recommendation"] == "role_proposed"
            assert review["priority"] == "high"
            assert review["proposed_role"] == "Customer Calling Specialist"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_supervisor_review_flags_stale_role_gap_approval(monkeypatch):
    now = datetime(2026, 6, 1, 12, 0, 0)
    old = now - timedelta(hours=30)
    monkeypatch.setattr(settings, "supervisor_review_stale_approval_hours", 24)
    engine, session_factory = await build_session_factory()
    manager = FakeManager()

    try:
        async with session_factory() as session:
            gap = role_gap("gap_approval", status="proposed")
            gap.resolution = {
                "approval_required": True,
                "pending_approval_id": "approval_1",
            }
            session.add(gap)
            session.add(
                ApprovalRequest(
                    id="approval_1",
                    agent_id="company_builder",
                    action_type="role_gap.tool_grant",
                    action_description="Approve generated role.",
                    action_payload={"role_gap_id": "gap_approval"},
                    requester="company_builder",
                    requester_type="agent",
                    risk_level="high",
                    target_type="role_gap",
                    target_id="gap_approval",
                    status="pending",
                    created_at=old,
                )
            )
            await session.commit()

        result = await SupervisorReviewService(
            manager,
            session_factory=session_factory,
        ).run_once(now=now, actor="test")

        assert result["stale_approvals"][0]["approval_id"] == "approval_1"
        async with session_factory() as session:
            gap = (
                await session.execute(select(RoleGap).where(RoleGap.id == "gap_approval"))
            ).scalar_one()
            review = gap.context["supervisor_review"]
            assert review["recommendation"] == "review_stale_approval"
            assert review["approval_id"] == "approval_1"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_supervisor_review_creates_gap_for_repeated_workflow_failures(monkeypatch):
    now = datetime(2026, 6, 1, 12, 0, 0)
    monkeypatch.setattr(settings, "supervisor_review_failure_threshold", 2)
    monkeypatch.setattr(settings, "supervisor_review_failure_lookback_hours", 24)
    engine, session_factory = await build_session_factory()
    manager = FakeManager()

    try:
        async with session_factory() as session:
            session.add(
                Workflow(
                    id="workflow_1",
                    name="Lead outreach",
                    description=None,
                    graph_definition={},
                    status="active",
                    trigger_type="manual",
                    trigger_config={},
                    created_at=now - timedelta(days=1),
                    updated_at=now - timedelta(days=1),
                )
            )
            for index in range(2):
                session.add(
                    WorkflowRun(
                        id=f"run_{index}",
                        workflow_id="workflow_1",
                        status="failed",
                        current_node="call_customer",
                        state={},
                        result=None,
                        error="Communications gateway not available",
                        started_at=now - timedelta(hours=2),
                        completed_at=now - timedelta(hours=1, minutes=index),
                    )
                )
            await session.commit()

        result = await SupervisorReviewService(
            manager,
            session_factory=session_factory,
        ).run_once(now=now, actor="test")

        assert result["workflow_failure_gaps"] == [
            {
                "gap_id": "gap_workflow_failure",
                "workflow_id": "workflow_1",
                "current_node": "call_customer",
                "failure_count": 2,
                "status": "proposed",
            }
        ]
        report_data = manager.report_role_gap.await_args.args[0]
        assert report_data.capability == "workflow_reliability"
        assert report_data.context["failure_count"] == 2
        assert report_data.context["dedupe_key"].startswith("workflow-failure:")
        manager.propose_role_for_gap.assert_awaited_once_with("gap_workflow_failure")
    finally:
        await engine.dispose()
