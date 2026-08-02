from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.clock import utc_now
from cyber_team.db import Base
from cyber_team.db.models import (
    Agent,
    MemoryStewardFinding,
    OrchestrationToolProposal,
    RoleGap,
    Workflow,
    WorkflowRun,
)
from cyber_team.operations import governor as governor_module
from cyber_team.operations.governor import OrchestrationGovernorService
from cyber_team.tools.registry import ToolRegistry


class FakeToolRegistry:
    def __init__(self):
        self.contracts = [
            {
                "name": "memory_recall",
                "state": "live",
                "side_effects": False,
                "requires_configuration": False,
            }
        ]

    def list_tool_contracts(self):
        return self.contracts

    def get_tool(self, name: str):
        return object() if name == "memory_recall" else None


class FakePlanning:
    async def list_owner_attention(self, status="active", limit=100):
        return {
            "status": "ready",
            "counts": {"active": 0, "total": 0},
            "items": [],
        }


class FakeReadinessEvidence:
    async def summary(self):
        return {
            "alerts": {
                "status": "ready",
                "blocking": False,
                "stale": False,
                "detail": "Alert evidence is fresh.",
            }
        }


class FakeComms:
    def integration_status(self):
        return [
            {
                "provider": "smtp",
                "mode": "live",
                "api_secret": "do-not-store-me",
            }
        ]


class FakeERPNext:
    def integration_status(self):
        return {
            "provider": "erpnext",
            "mode": "live",
            "api_key": "do-not-store-me-either",
        }


class FakeAgentManager:
    async def summarize_role_backlog(
        self,
        statuses=None,
        source_type=None,
        limit=500,
    ):
        return {
            "counts": {
                "total": 12,
                "actionable": 2,
                "owner_pending": 7,
                "configuration_blocked": 3,
                "by_recommended_action": {
                    "create_role": 2,
                    "await_approval": 7,
                    "configure_tools": 3,
                },
                "by_function": {"Operations": 2, "Communications": 10},
                "by_risk": {"medium": 5, "high": 7},
            },
            "groups": [{"business_function": "Operations"}],
        }


class FakeAudit:
    def __init__(self):
        self.events = []
        self.evidence = []

    async def record(self, **kwargs):
        self.events.append(kwargs)

    async def record_control_evidence(self, **kwargs):
        self.evidence.append(kwargs)


@pytest.fixture
async def governor_session_factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(governor_module, "async_session", factory)
    try:
        yield factory
    finally:
        await engine.dispose()


async def seed_role_gap(factory):
    async with factory() as session:
        session.add(
            RoleGap(
                id="gap_tool_1",
                title="Need analytics connector",
                description="The company needs analytics data but no tool exists.",
                status="open",
                severity="medium",
                source_type="company_context_snapshot",
                company_namespace="company:acme",
                capability="analytics",
                requested_tools=["analytics_data_sync"],
                context={"snapshot_id": "ctx_1"},
                proposed_role={},
                resolution={},
                created_at=datetime(2026, 7, 4, 6, 0, 0),
                updated_at=datetime(2026, 7, 4, 6, 0, 0),
            )
        )
        await session.commit()


async def seed_alias_role_gap(factory):
    async with factory() as session:
        session.add(
            RoleGap(
                id="gap_alias_tools",
                title="Need canonical alias tools",
                description="The role uses operating-model aliases for existing tools.",
                status="open",
                severity="medium",
                source_type="company_context_snapshot",
                company_namespace="company:acme",
                capability="operations",
                requested_tools=["memory_write", "erpnext_finance_read"],
                context={"snapshot_id": "ctx_alias"},
                proposed_role={},
                resolution={},
                created_at=datetime(2026, 7, 4, 6, 0, 0),
                updated_at=datetime(2026, 7, 4, 6, 0, 0),
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_governor_workflow_failures_use_configured_recent_window(
    governor_session_factory,
    monkeypatch,
):
    monkeypatch.setattr(
        governor_module.settings,
        "governor_workflow_failure_lookback_hours",
        24,
    )
    now = utc_now()
    async with governor_session_factory() as session:
        session.add(
            Workflow(
                id="workflow-health",
                name="Workflow health",
                graph_definition={},
                status="active",
            )
        )
        session.add_all([
            WorkflowRun(
                id="old-failure",
                workflow_id="workflow-health",
                status="failed",
                error="Historical failure",
                started_at=now - timedelta(days=2),
                completed_at=now - timedelta(days=2),
            ),
            WorkflowRun(
                id="recent-failure",
                workflow_id="workflow-health",
                status="failed",
                error="Recent failure",
                started_at=now - timedelta(hours=1),
                completed_at=now - timedelta(hours=1),
            ),
        ])
        await session.commit()
        counts = await build_service()._workflow_counts(session)

    assert counts["recent_failed"] == 1
    assert counts["recent_failed_ids"] == ["recent-failure"]
    assert counts["historical_failed"] == 2
    assert counts["failure_lookback_hours"] == 24


@pytest.mark.asyncio
async def test_governor_memory_counts_separate_provider_incidents(
    governor_session_factory,
):
    now = utc_now()
    async with governor_session_factory() as session:
        session.add_all([
            MemoryStewardFinding(
                id="memory-integrity",
                finding_type="stale_procedural_memory",
                severity="medium",
                status="open",
                title="Stale procedure",
                description="Procedure is stale.",
                recommendation="Refresh it.",
                trace_ids=[],
                evidence={},
                metadata_={},
                created_at=now,
                updated_at=now,
            ),
            MemoryStewardFinding(
                id="provider-incident",
                finding_type="llm_provider_errors",
                severity="medium",
                status="open",
                title="Provider rate limited",
                description="Provider incident.",
                recommendation="Wait for recovery.",
                trace_ids=[],
                evidence={"category": "rate_limited"},
                metadata_={"failure_domain": "llm_provider"},
                created_at=now,
                updated_at=now,
            ),
        ])
        await session.commit()
        counts = await build_service()._memory_finding_counts(session)

    assert counts["open_findings"] == 2
    assert counts["actionable_findings"] == 1
    assert counts["provider_findings"] == 1
    assert counts["open_by_type"] == {
        "llm_provider_errors": 1,
        "stale_procedural_memory": 1,
    }


def build_service(audit=None, agent_manager=None):
    return OrchestrationGovernorService(
        agent_manager=agent_manager,
        planning_service=FakePlanning(),
        tool_registry=FakeToolRegistry(),
        audit_service=audit or FakeAudit(),
        readiness_evidence_service=FakeReadinessEvidence(),
        comms_gateway=FakeComms(),
        erpnext=FakeERPNext(),
    )


@pytest.mark.asyncio
async def test_governor_ensures_chief_agent_and_redacts_snapshot(
    governor_session_factory,
):
    service = build_service()

    snapshot = await service.build_operating_snapshot()
    agent = await service.ensure_chief_operating_agent()

    assert agent["id"] == "chief_operating_agent"
    assert snapshot["integrations"]["communications"][0]["api_secret"] == "[redacted]"
    assert snapshot["integrations"]["erpnext"]["api_key"] == "[redacted]"
    async with governor_session_factory() as session:
        stored = await session.get(Agent, "chief_operating_agent")
    assert stored is not None
    assert stored.status == "active"


@pytest.mark.asyncio
async def test_governor_snapshot_separates_required_and_optional_tool_blockers(
    governor_session_factory,
    monkeypatch,
):
    monkeypatch.setattr(
        "cyber_team.operations.tool_readiness_policy.settings.required_communication_providers",
        "smtp,imap,erpnext",
    )
    registry = FakeToolRegistry()
    registry.contracts = [
        {
            "name": "memory_recall",
            "state": "live",
            "side_effects": False,
            "requires_configuration": False,
        },
        {
            "name": "sms_send",
            "state": "configuration_required",
            "side_effects": True,
            "category": "communications",
            "requires_configuration": True,
            "readiness_reason": "SMS provider is intentionally disabled.",
        },
        {
            "name": "ci_trigger",
            "state": "configuration_required",
            "side_effects": True,
            "category": "devops",
            "requires_configuration": True,
            "readiness_reason": "GitHub dispatch is optional for this environment.",
        },
        {
            "name": "task_create",
            "state": "configuration_required",
            "side_effects": True,
            "category": "erpnext",
            "requires_configuration": True,
            "readiness_reason": "ERPNext task executor needs credentials.",
        },
    ]
    service = OrchestrationGovernorService(
        planning_service=FakePlanning(),
        tool_registry=registry,
        audit_service=FakeAudit(),
        readiness_evidence_service=FakeReadinessEvidence(),
        comms_gateway=FakeComms(),
        erpnext=FakeERPNext(),
    )

    snapshot = await service.build_operating_snapshot()
    tools = snapshot["tools"]

    assert {item["name"] for item in tools["side_effects_not_live"]} == {
        "sms_send",
        "ci_trigger",
        "task_create",
    }
    assert [item["name"] for item in tools["required_side_effects_not_live"]] == [
        "task_create"
    ]
    assert {item["name"] for item in tools["non_blocking_side_effects"]} == {
        "sms_send",
        "ci_trigger",
    }
    assert tools["required_side_effects_not_live"][0]["readiness_required"] is True
    assert all(
        item["readiness_required"] is False
        for item in tools["non_blocking_side_effects"]
    )


@pytest.mark.asyncio
async def test_governor_snapshot_uses_role_backlog_actionability_summary(
    governor_session_factory,
):
    service = build_service(agent_manager=FakeAgentManager())

    snapshot = await service.build_operating_snapshot()
    role_backlog = snapshot["role_backlog"]

    assert role_backlog["active"] == 12
    assert role_backlog["actionable"] == 2
    assert role_backlog["owner_pending"] == 7
    assert role_backlog["configuration_blocked"] == 3
    assert role_backlog["summary_available"] is True
    assert role_backlog["by_recommended_action"] == {
        "create_role": 2,
        "await_approval": 7,
        "configure_tools": 3,
    }


@pytest.mark.asyncio
async def test_governor_creates_idempotent_tool_proposal_from_role_gap(
    governor_session_factory,
):
    await seed_role_gap(governor_session_factory)
    audit = FakeAudit()
    service = build_service(audit=audit)

    first = await service.run_once(actor="owner@example.com", max_actions=10)
    second = await service.run_once(actor="owner@example.com", max_actions=10)

    assert first["status"] == "completed"
    assert any(
        decision["decision_type"] == "propose_tool"
        for decision in first["decisions"]
    )
    assert second["counts"]["duplicates"] >= 1
    async with governor_session_factory() as session:
        proposals = (
            await session.execute(select(OrchestrationToolProposal))
        ).scalars().all()
    assert len(proposals) == 1
    assert proposals[0].capability == "analytics"
    assert proposals[0].sandbox_result["status"] == "not_executed"
    assert (
        proposals[0].sandbox_result["resource_policy"]["cost_model"]
        == "free_self_hosted_only"
    )
    assert "mit" in proposals[0].sandbox_result["resource_policy"]["license"].lower()
    assert audit.events[0]["event_type"] == "orchestration_governor.run"
    assert audit.evidence[0]["control_id"] == "autonomy.governor_run"


@pytest.mark.asyncio
async def test_governor_treats_operating_model_alias_tools_as_registered(
    governor_session_factory,
):
    await seed_alias_role_gap(governor_session_factory)
    registry = ToolRegistry()
    service = OrchestrationGovernorService(
        planning_service=FakePlanning(),
        tool_registry=registry,
        audit_service=FakeAudit(),
        readiness_evidence_service=FakeReadinessEvidence(),
        comms_gateway=FakeComms(),
        erpnext=FakeERPNext(),
    )

    snapshot = await service.build_operating_snapshot()
    [gap] = [
        item
        for item in snapshot["role_gap_samples"]
        if item["gap_id"] == "gap_alias_tools"
    ]

    assert registry.get_tool("memory_write") is not None
    assert registry.get_tool("erpnext_finance_read") is not None
    assert gap["missing_tools"] == []
