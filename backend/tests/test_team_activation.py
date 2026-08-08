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
    AgentCapabilityGrant,
    AgentMandate,
    ApprovalRequest,
    RoleGap,
    RoleManifest,
)
from cyber_team.roles import team_activation as team_activation_module
from cyber_team.roles.team_activation import TeamActivationService


class FakeTool:
    def __init__(
        self,
        name: str,
        *,
        category: str = "general",
        risk_level: str = "low",
        side_effects: bool = False,
        requires_approval: bool = False,
        executor_kind: str = "live",
    ):
        self.name = name
        self.category = category
        self.risk_level = risk_level
        self.side_effects = side_effects
        self.requires_approval = requires_approval
        self.executor_kind = executor_kind
        self.requires_configuration = executor_kind == "configuration_required"
        self.readiness_reason = "test readiness"


class FakeToolRegistry:
    def __init__(self):
        self._tools = {
            "memory_recall": FakeTool("memory_recall", category="memory"),
            "memory_remember": FakeTool(
                "memory_remember",
                category="memory",
                risk_level="medium",
            ),
            "approval_request": FakeTool("approval_request", category="governance"),
            "company_profile_read": FakeTool("company_profile_read", category="roles"),
            "send_email": FakeTool(
                "send_email",
                category="communications",
                risk_level="high",
                side_effects=True,
                requires_approval=True,
            ),
        }

    def list_tools(self):
        return list(self._tools.values())

    def get_tool(self, name: str):
        return self._tools.get(name)

    def get_tool_readiness(self, name: str):
        tool = self._tools.get(name)
        if not tool:
            return {
                "state": "unavailable",
                "readiness_reason": f"Tool not found: {name}",
                "side_effects": False,
                "executor_kind": "unavailable",
                "requires_configuration": False,
                "executable": False,
            }
        executable = tool.executor_kind in {"live", "advisory"}
        return {
            "state": tool.executor_kind,
            "readiness_reason": tool.readiness_reason,
            "side_effects": tool.side_effects,
            "executor_kind": tool.executor_kind,
            "requires_configuration": tool.requires_configuration,
            "executable": executable,
        }


@pytest.fixture
async def session_factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(manager_module, "async_session", factory)
    monkeypatch.setattr(team_activation_module, "async_session", factory)
    try:
        yield factory
    finally:
        await engine.dispose()


async def seed_gap(
    factory,
    *,
    gap_id: str,
    name: str,
    tools: list[str],
    capability: str = "operations",
    context_family: str | None = None,
    proposed_family: str = "operations",
):
    async with factory() as session:
        gap = RoleGap(
            id=gap_id,
            title=f"Recommended role: {name}",
            description="Company context requires this capability.",
            status="proposed",
            severity="medium",
            source_type="company_context_snapshot",
            company_namespace="company:acme",
            capability=capability,
            requested_tools=tools,
            context={
                "snapshot_id": "ctx_1",
                "source_hash": "hash-1",
                **({"role_family": context_family} if context_family else {}),
            },
            proposed_role={
                "manifest_payload": {
                    "family": proposed_family,
                    "name": name,
                    "description": f"{name} role.",
                    "instructions_template": "Operate within safe policy.",
                    "default_tools": tools,
                    "memory_namespace": f"company:acme:gap:{name.lower().replace(' ', '_')}",
                    "approval_policy": "sensitive" if "send_email" in tools else "auto",
                    "success_metrics": [],
                    "is_core": False,
                    "config": {},
                }
            },
            resolution={},
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(gap)
        await session.commit()


@pytest.mark.asyncio
async def test_team_activation_creates_safe_baseline_and_pending_high_risk_grant(
    session_factory,
):
    await seed_gap(
        session_factory,
        gap_id="gap_email",
        name="Client Email Specialist",
        tools=["send_email", "memory_recall"],
    )
    registry = FakeToolRegistry()
    manager = AgentManager(tool_registry=registry)
    service = TeamActivationService(agent_manager=manager, tool_registry=registry)

    result = await service.run_activation(actor="owner@example.com")

    assert result["status"] == "completed"
    assert result["counts"]["agents_created"] == 1
    assert result["counts"]["safe_grants_active"] >= 1
    assert result["counts"]["grants_pending_approval"] == 1
    assert result["counts"]["approvals_requested"] == 1
    async with session_factory() as session:
        agent = (
            await session.execute(
                select(Agent).where(Agent.role_name == "Client Email Specialist (Baseline)")
            )
        ).scalar_one()
        grants = (
            await session.execute(
                select(AgentCapabilityGrant).where(
                    AgentCapabilityGrant.agent_id == agent.id
                )
            )
        ).scalars().all()
        approval = (
            await session.execute(select(ApprovalRequest))
        ).scalar_one()
        gap = (
            await session.execute(select(RoleGap).where(RoleGap.id == "gap_email"))
        ).scalar_one()

    assert "send_email" not in agent.tools
    assert {grant.tool_name for grant in grants if grant.state == "active"} >= {
        "memory_recall"
    }
    assert {grant.tool_name for grant in grants if grant.state == "pending_approval"} == {
        "send_email"
    }
    assert approval.target_type == "role_gap"
    assert approval.target_id == "gap_email"
    assert gap.status == "proposed"
    assert gap.resolution["activation_state"] == "baseline_created"


@pytest.mark.asyncio
async def test_team_activation_resolves_safe_only_role_gap(session_factory):
    await seed_gap(
        session_factory,
        gap_id="gap_memory",
        name="Knowledge Steward",
        tools=["memory_recall"],
    )
    registry = FakeToolRegistry()
    manager = AgentManager(tool_registry=registry)
    service = TeamActivationService(agent_manager=manager, tool_registry=registry)

    result = await service.run_activation(actor="owner@example.com")

    assert result["status"] == "completed"
    assert result["counts"]["safe_gaps_resolved"] == 1
    async with session_factory() as session:
        gap = (
            await session.execute(select(RoleGap).where(RoleGap.id == "gap_memory"))
        ).scalar_one()
        agent = (
            await session.execute(select(Agent).where(Agent.role_name == "Knowledge Steward"))
        ).scalar_one()
    assert gap.status == "resolved"
    assert gap.resolution["activation_state"] == "safe_full"
    assert agent.tools == [
        "memory_recall",
        "memory_remember",
        "approval_request",
        "company_profile_read",
    ]


@pytest.mark.asyncio
async def test_scoped_tool_grant_is_exact_atomic_and_single_use(session_factory):
    async with session_factory() as session:
        session.add(
            Agent(
                id="communications-agent",
                role_family="communications",
                role_name="Communications Agent",
                instructions="Operate within policy.",
                tools=["memory_recall"],
                memory_namespace="company:acme:communications",
                approval_policy="sensitive",
                status="active",
                config={},
            )
        )
        session.add(
            AgentMandate(
                id="mandate-communications-v1",
                agent_id="communications-agent",
                version=1,
                status="active",
                objective_ids=[],
                authority={"read_tools": ["memory_recall"]},
                budget={},
                inputs=[],
                outputs=[],
                kpi_keys=[],
                cadence={},
                escalation_rules=[],
                metadata_={},
                created_by="test",
                activated_at=utc_now(),
            )
        )
        await session.commit()

    registry = FakeToolRegistry()
    manager = AgentManager(tool_registry=registry)
    service = TeamActivationService(agent_manager=manager, tool_registry=registry)
    requested = await service.request_scoped_tool_grant(
        agent_id="communications-agent",
        tool_name="send_email",
        reason="One-recipient canary only.",
        actor="owner@example.com",
    )
    await manager.resolve_approval(
        requested["approval_id"],
        "approved",
        reviewer="owner@example.com",
    )
    applied = await service.apply_scoped_tool_grant(
        agent_id="communications-agent",
        tool_name="send_email",
        approval_id=requested["approval_id"],
        actor="owner@example.com",
    )

    assert applied["status"] == "active"
    assert applied["duplicate"] is False
    async with session_factory() as session:
        agent = await session.get(Agent, "communications-agent")
        mandate = await session.get(AgentMandate, "mandate-communications-v1")
        approval = await session.get(ApprovalRequest, requested["approval_id"])
        grant = (
            await session.execute(
                select(AgentCapabilityGrant).where(
                    AgentCapabilityGrant.agent_id == "communications-agent",
                    AgentCapabilityGrant.tool_name == "send_email",
                )
            )
        ).scalar_one()
    assert "send_email" in agent.tools
    assert "send_email" in mandate.authority["read_tools"]
    assert approval.consumed_at is not None
    assert grant.state == "active"

    with pytest.raises(ValueError, match="already consumed"):
        await service.apply_scoped_tool_grant(
            agent_id="communications-agent",
            tool_name="send_email",
            approval_id=requested["approval_id"],
            actor="owner@example.com",
        )


@pytest.mark.asyncio
async def test_scoped_tool_grant_rolls_back_approval_when_mandate_is_missing(
    session_factory,
):
    async with session_factory() as session:
        session.add(
            Agent(
                id="communications-agent-no-mandate",
                role_family="communications",
                role_name="Communications Agent",
                instructions="Operate within policy.",
                tools=["memory_recall"],
                memory_namespace="company:acme:communications",
                approval_policy="sensitive",
                status="active",
                config={},
            )
        )
        session.add(
            ApprovalRequest(
                id="approval-no-mandate",
                agent_id="communications-agent-no-mandate",
                action_type="agent.tool_grant",
                action_description="Grant send_email.",
                action_payload={
                    "approval_binding": TeamActivationService._scoped_grant_binding(
                        "communications-agent-no-mandate",
                        "send_email",
                    )
                },
                requester="owner@example.com",
                requester_type="user",
                risk_level="high",
                target_type="agent_tool_grant",
                target_id="communications-agent-no-mandate:send_email",
                status="approved",
                reviewer="owner@example.com",
                resolved_at=utc_now(),
            )
        )
        await session.commit()
    registry = FakeToolRegistry()
    manager = AgentManager(tool_registry=registry)
    service = TeamActivationService(agent_manager=manager, tool_registry=registry)

    with pytest.raises(ValueError, match="Active agent and mandate are required"):
        await service.apply_scoped_tool_grant(
            agent_id="communications-agent-no-mandate",
            tool_name="send_email",
            approval_id="approval-no-mandate",
            actor="owner@example.com",
        )
    async with session_factory() as session:
        approval = await session.get(ApprovalRequest, "approval-no-mandate")
        agent = await session.get(Agent, "communications-agent-no-mandate")
    assert approval.consumed_at is None
    assert "send_email" not in agent.tools


@pytest.mark.asyncio
async def test_team_activation_prefers_explicit_gap_family_over_stale_proposal(
    session_factory,
):
    await seed_gap(
        session_factory,
        gap_id="gap_support",
        name="Support Specialist",
        tools=["memory_recall"],
        capability="support",
        context_family="support",
        proposed_family="communications",
    )
    registry = FakeToolRegistry()
    manager = AgentManager(tool_registry=registry)
    service = TeamActivationService(agent_manager=manager, tool_registry=registry)

    result = await service.run_activation(actor="owner@example.com")

    assert result["status"] == "completed"
    async with session_factory() as session:
        agent = (
            await session.execute(select(Agent).where(Agent.id == "support_specialist"))
        ).scalar_one()
        manifest = (
            await session.execute(
                select(RoleManifest).where(RoleManifest.id == "support_specialist")
            )
        ).scalar_one()
    assert agent.role_family == "support"
    assert manifest.family == "support"
    assert manifest.config["family_source"] == "role_gap_context"
    assert manifest.config["proposed_family_before_activation"] == "communications"


@pytest.mark.asyncio
async def test_team_activation_reconciles_only_generated_historical_families(
    session_factory,
):
    await seed_gap(
        session_factory,
        gap_id="gap_historical_support",
        name="Historical Support Specialist",
        tools=["memory_recall"],
        capability="support",
        context_family="support",
        proposed_family="communications",
    )
    async with session_factory() as session:
        session.add(
            RoleManifest(
                id="historical_support_specialist_baseline",
                family="communications",
                name="Historical Support Specialist (Baseline)",
                description="Historical generated baseline.",
                instructions_template="Operate safely.",
                default_tools=["memory_recall"],
                memory_namespace="company:acme:team:historical_support",
                approval_policy="auto",
                success_metrics=[],
                is_core=False,
                config={
                    "source": "team_activation",
                    "role_gap_id": "gap_historical_support",
                },
            )
        )
        session.add(
            Agent(
                id="historical_support_specialist_baseline",
                role_family="communications",
                role_name="Historical Support Specialist (Baseline)",
                instructions="Operate safely.",
                tools=["memory_recall"],
                memory_namespace="company:acme:team:historical_support",
                approval_policy="auto",
                status="active",
                config={
                    "provisioned_by": "team_activation",
                    "role_gap_id": "gap_historical_support",
                },
            )
        )
        session.add(
            Agent(
                id="owner_created_agent",
                role_family="communications",
                role_name="Owner-created Support Advisor",
                instructions="Owner managed.",
                tools=[],
                memory_namespace="company:acme:owner-advisor",
                approval_policy="auto",
                status="active",
                config={"provisioned_by": "owner"},
            )
        )
        await session.commit()

    registry = FakeToolRegistry()
    manager = AgentManager(tool_registry=registry)
    mandate_service = AsyncMock()
    mandate_service.ensure_active_agent_mandates.return_value = {
        "status": "completed",
        "created": 1,
    }
    service = TeamActivationService(
        agent_manager=manager,
        tool_registry=registry,
        mandate_service=mandate_service,
    )

    preview = await service.reconcile_generated_role_families(
        actor="owner@example.com",
        dry_run=True,
    )
    assert preview["candidate_count"] == 1
    assert preview["agents_reconciled"] == 0
    mandate_service.ensure_active_agent_mandates.assert_not_awaited()

    applied = await service.reconcile_generated_role_families(
        actor="owner@example.com",
        dry_run=False,
    )
    assert applied["status"] == "completed"
    assert applied["agents_reconciled"] == 1
    assert applied["manifests_reconciled"] == 1
    mandate_service.ensure_active_agent_mandates.assert_awaited_once_with(
        actor="owner@example.com"
    )
    async with session_factory() as session:
        generated = await session.get(Agent, "historical_support_specialist_baseline")
        owner_created = await session.get(Agent, "owner_created_agent")
        manifest = await session.get(RoleManifest, "historical_support_specialist_baseline")
    assert generated.role_family == "support"
    assert generated.config["role_family_reconciliation"]["previous_family"] == (
        "communications"
    )
    assert manifest.family == "support"
    assert owner_created.role_family == "communications"
