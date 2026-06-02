from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.config import settings
from cyber_team.db import Base
from cyber_team.db.models import MemoryStewardFinding, MemoryTrace
from cyber_team.operations.memory_steward import MemoryStewardService


class FakeMemoryService:
    def __init__(self):
        self.writes = []

    async def remember(self, data):
        memory_id = f"memory-{len(self.writes) + 1}"
        self.writes.append({
            "id": memory_id,
            "agent_id": data.agent_id,
            "memory_type": data.memory_type,
            "namespace": data.namespace,
            "content": data.content,
            "metadata": data.metadata,
            "importance": data.importance,
        })
        return self.writes[-1]


class FakeAgentManager:
    def __init__(self):
        self.gaps = []
        self.approvals = []
        self.approved = set()
        self.consumed = []

    async def report_role_gap(self, data, reporter="system"):
        gap_id = f"gap-{len(self.gaps) + 1}"
        self.gaps.append({
            "id": gap_id,
            "title": data.title,
            "description": data.description,
            "status": "open",
            "severity": data.severity,
            "source_agent_id": data.source_agent_id,
            "source_type": data.source_type,
            "company_namespace": data.company_namespace,
            "capability": data.capability,
            "requested_tools": data.requested_tools,
            "context": data.context,
            "reporter": reporter,
        })
        return self.gaps[-1]

    async def _request_approval(
        self,
        agent_id,
        action_type,
        description,
        payload,
        **kwargs,
    ):
        approval_id = f"approval-{len(self.approvals) + 1}"
        self.approvals.append({
            "id": approval_id,
            "agent_id": agent_id,
            "action_type": action_type,
            "description": description,
            "payload": payload,
            **kwargs,
        })
        return approval_id

    async def approval_is_executable(
        self,
        approval_id,
        target_type=None,
        target_id=None,
    ):
        return approval_id in self.approved

    async def consume_approval(
        self,
        approval_id,
        consumer="system",
        target_type=None,
        target_id=None,
    ):
        self.consumed.append({
            "approval_id": approval_id,
            "consumer": consumer,
            "target_type": target_type,
            "target_id": target_id,
        })


async def build_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def memory_trace(
    trace_id: str,
    *,
    agent_id: str = "ops_agent",
    namespace: str = "company:acme:ops",
    recall_count: int = 0,
    errors: list[str] | None = None,
    created_at: datetime | None = None,
    scope_results: list[dict] | None = None,
) -> MemoryTrace:
    now = created_at or datetime(2026, 6, 2, 12, 0, 0)
    return MemoryTrace(
        id=trace_id,
        invocation_id=f"invoke-{trace_id}",
        agent_id=agent_id,
        conversation_id=None,
        source_type="agent_invocation",
        task_excerpt="Prepare launch operations.",
        memory_namespace=namespace,
        read_policy={
            "company_namespace": "company:acme",
            "scope_results": scope_results
            if scope_results is not None
            else [
                {
                    "name": "company_constitution",
                    "namespace": "company:acme",
                    "returned": 0,
                    "added": 0,
                }
            ],
        },
        write_policy={"memory_type": "episodic"},
        recalled_memory_ids=[],
        written_memory_ids=["memory-write-1"],
        recall_count=recall_count,
        write_count=1,
        errors=errors or [],
        metadata_={"memory_coverage": "empty" if recall_count == 0 else "hit"},
        created_at=now,
    )


@pytest.mark.asyncio
async def test_memory_steward_creates_and_dedupes_findings(monkeypatch):
    now = datetime(2026, 6, 2, 12, 0, 0)
    engine, session_factory = await build_session_factory()
    monkeypatch.setattr(settings, "memory_steward_empty_recall_threshold", 2)
    monkeypatch.setattr(settings, "memory_steward_trace_lookback_hours", 24)
    monkeypatch.setattr(settings, "memory_steward_trace_limit", 100)
    monkeypatch.setattr(settings, "memory_steward_planner_enabled", False)

    try:
        async with session_factory() as session:
            session.add_all([
                memory_trace("trace-1", created_at=now - timedelta(minutes=3)),
                memory_trace("trace-2", created_at=now - timedelta(minutes=2)),
                memory_trace(
                    "trace-3",
                    errors=["write:RuntimeError:database unavailable"],
                    created_at=now - timedelta(minutes=1),
                ),
            ])
            await session.commit()

        steward = MemoryStewardService(session_factory=session_factory)
        first = await steward.run_once(now=now, actor="test")
        second = await steward.run_once(now=now, actor="test")

        assert first["traces_reviewed"] == 3
        assert first["findings_created"] == 3
        assert second["findings_created"] == 0
        assert second["findings_updated"] == 3

        findings = await steward.list_findings(status="open")
        finding_types = {finding["finding_type"] for finding in findings}
        assert finding_types == {
            "repeated_empty_recall",
            "memory_operation_errors",
            "missing_company_shared_memory",
        }

        empty_recall = next(
            finding
            for finding in findings
            if finding["finding_type"] == "repeated_empty_recall"
        )
        assert empty_recall["evidence"]["empty_recall_count"] == 3
        assert empty_recall["evidence"]["threshold"] == 2

        resolved = await steward.resolve_finding(
            empty_recall["id"],
            status="resolved",
            note="Seeded memory.",
            actor="owner@example.com",
        )
        assert resolved is not None
        assert resolved["status"] == "resolved"

        async with session_factory() as session:
            db_finding = (
                await session.execute(
                    select(MemoryStewardFinding).where(
                        MemoryStewardFinding.id == empty_recall["id"]
                    )
                )
            ).scalar_one()
            assert db_finding.metadata_["resolution"]["note"] == "Seeded memory."
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_memory_steward_executes_seed_memory_action(monkeypatch):
    now = datetime(2026, 6, 2, 12, 0, 0)
    engine, session_factory = await build_session_factory()
    monkeypatch.setattr(settings, "memory_steward_empty_recall_threshold", 2)
    monkeypatch.setattr(settings, "memory_steward_trace_lookback_hours", 24)
    monkeypatch.setattr(settings, "memory_steward_trace_limit", 100)
    monkeypatch.setattr(settings, "memory_steward_planner_enabled", False)

    try:
        async with session_factory() as session:
            session.add_all([
                memory_trace("trace-action-1", created_at=now - timedelta(minutes=2)),
                memory_trace("trace-action-2", created_at=now - timedelta(minutes=1)),
            ])
            await session.commit()

        memory = FakeMemoryService()
        steward = MemoryStewardService(
            memory_service=memory,
            session_factory=session_factory,
        )
        await steward.run_once(now=now, actor="test")
        findings = await steward.list_findings(status="open")
        finding = next(
            item
            for item in findings
            if item["finding_type"] == "repeated_empty_recall"
        )

        result = await steward.execute_action(
            finding["id"],
            action_type="seed_memory",
            actor="owner@example.com",
        )

        assert result is not None
        assert result["action"]["action_type"] == "seed_memory"
        assert result["finding"]["status"] == "acknowledged"
        assert result["finding"]["available_actions"] == [
            {
                "type": "seed_memory",
                "label": "Seed Memory",
                "description": "Write a durable memory entry that guides future recall.",
            },
            {
                "type": "report_role_gap",
                "label": "Open Gap",
                "description": "Create a role or capability gap for follow-up.",
            },
        ]
        assert len(memory.writes) == 1
        assert memory.writes[0]["namespace"] == "company:acme:ops"
        assert memory.writes[0]["memory_type"] == "procedural"
        assert memory.writes[0]["metadata"]["finding_id"] == finding["id"]

        async with session_factory() as session:
            db_finding = (
                await session.execute(
                    select(MemoryStewardFinding).where(
                        MemoryStewardFinding.id == finding["id"]
                    )
                )
            ).scalar_one()
            assert db_finding.metadata_["last_action"]["action_type"] == "seed_memory"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_memory_steward_executes_role_gap_action(monkeypatch):
    now = datetime(2026, 6, 2, 12, 0, 0)
    engine, session_factory = await build_session_factory()
    monkeypatch.setattr(settings, "memory_steward_empty_recall_threshold", 2)
    monkeypatch.setattr(settings, "memory_steward_trace_lookback_hours", 24)
    monkeypatch.setattr(settings, "memory_steward_trace_limit", 100)
    monkeypatch.setattr(settings, "memory_steward_planner_enabled", False)

    try:
        async with session_factory() as session:
            session.add(
                memory_trace(
                    "trace-error-1",
                    errors=["write:RuntimeError:database unavailable"],
                    created_at=now - timedelta(minutes=1),
                )
            )
            await session.commit()

        manager = FakeAgentManager()
        steward = MemoryStewardService(
            agent_manager=manager,
            session_factory=session_factory,
        )
        await steward.run_once(now=now, actor="test")
        findings = await steward.list_findings(status="open")
        finding = next(
            item
            for item in findings
            if item["finding_type"] == "memory_operation_errors"
        )

        result = await steward.execute_action(
            finding["id"],
            action_type="report_role_gap",
            actor="owner@example.com",
        )

        assert result is not None
        assert result["action"]["action_type"] == "report_role_gap"
        assert result["action"]["result"]["role_gap_id"] == "gap-1"
        assert result["finding"]["status"] == "acknowledged"
        assert len(manager.gaps) == 1
        assert manager.gaps[0]["capability"] == "memory_operations"
        assert manager.gaps[0]["requested_tools"] == [
            "memory_remember",
            "knowledge_query",
        ]
        assert manager.gaps[0]["context"]["finding_id"] == finding["id"]
        assert manager.gaps[0]["reporter"] == "owner@example.com"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_memory_steward_planner_auto_applies_safe_seed_once(monkeypatch):
    now = datetime(2026, 6, 2, 12, 0, 0)
    engine, session_factory = await build_session_factory()
    monkeypatch.setattr(settings, "memory_steward_empty_recall_threshold", 2)
    monkeypatch.setattr(settings, "memory_steward_trace_lookback_hours", 24)
    monkeypatch.setattr(settings, "memory_steward_trace_limit", 100)
    monkeypatch.setattr(settings, "memory_steward_planner_enabled", False)

    try:
        async with session_factory() as session:
            session.add_all([
                memory_trace(
                    "trace-plan-1",
                    created_at=now - timedelta(minutes=2),
                    scope_results=[],
                ),
                memory_trace(
                    "trace-plan-2",
                    created_at=now - timedelta(minutes=1),
                    scope_results=[],
                ),
            ])
            await session.commit()

        memory = FakeMemoryService()
        steward = MemoryStewardService(
            memory_service=memory,
            session_factory=session_factory,
        )
        await steward.run_once(now=now, actor="test")

        first = await steward.plan_remediations(
            actor="planner",
            apply_safe_actions=True,
            request_approvals=False,
        )
        second = await steward.plan_remediations(
            actor="planner",
            apply_safe_actions=True,
            request_approvals=False,
        )

        assert first["actions_applied"] == 1
        assert first["already_applied"] == 0
        assert second["actions_applied"] == 0
        assert second["already_applied"] == 1
        assert len(memory.writes) == 1

        finding = (await steward.list_findings(status="acknowledged"))[0]
        plan = finding["metadata"]["remediation_plan"]
        assert plan["action_type"] == "seed_memory"
        assert plan["status"] == "already_applied"
        assert finding["metadata"]["last_action"]["action_type"] == "seed_memory"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_memory_steward_planner_requests_and_consumes_approval(monkeypatch):
    now = datetime(2026, 6, 2, 12, 0, 0)
    engine, session_factory = await build_session_factory()
    monkeypatch.setattr(settings, "memory_steward_empty_recall_threshold", 2)
    monkeypatch.setattr(settings, "memory_steward_trace_lookback_hours", 24)
    monkeypatch.setattr(settings, "memory_steward_trace_limit", 100)
    monkeypatch.setattr(settings, "memory_steward_planner_enabled", False)

    try:
        async with session_factory() as session:
            session.add(
                memory_trace(
                    "trace-plan-error-1",
                    errors=["write:RuntimeError:database unavailable"],
                    recall_count=1,
                    created_at=now - timedelta(minutes=1),
                    scope_results=[],
                )
            )
            await session.commit()

        manager = FakeAgentManager()
        steward = MemoryStewardService(
            agent_manager=manager,
            session_factory=session_factory,
        )
        await steward.run_once(now=now, actor="test")

        requested = await steward.plan_remediations(
            actor="planner",
            apply_safe_actions=True,
            request_approvals=True,
        )
        manager.approved.add("approval-1")
        applied = await steward.plan_remediations(
            actor="planner",
            apply_safe_actions=True,
            request_approvals=True,
        )

        assert requested["approvals_requested"] == 1
        assert requested["actions_applied"] == 0
        assert manager.approvals[0]["action_type"] == "memory_steward.report_role_gap"
        assert manager.approvals[0]["target_type"] == "memory_steward_finding"
        assert applied["actions_applied"] == 1
        assert len(manager.gaps) == 1
        assert manager.gaps[0]["capability"] == "memory_operations"
        assert manager.consumed[0]["approval_id"] == "approval-1"

        finding = (await steward.list_findings(status="acknowledged"))[0]
        plan = finding["metadata"]["remediation_plan"]
        assert plan["status"] == "applied"
        assert plan["approval_id"] == "approval-1"
        assert finding["metadata"]["last_action"]["action_type"] == "report_role_gap"
    finally:
        await engine.dispose()
