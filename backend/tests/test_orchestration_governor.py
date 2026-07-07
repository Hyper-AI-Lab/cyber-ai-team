from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.db import Base
from cyber_team.db.models import (
    Agent,
    OrchestrationToolProposal,
    RoleGap,
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


def build_service(audit=None):
    return OrchestrationGovernorService(
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
