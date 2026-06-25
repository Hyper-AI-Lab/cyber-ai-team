import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.agents import manager as manager_module
from cyber_team.agents.manager import AgentManager
from cyber_team.clock import utc_now
from cyber_team.db import Base
from cyber_team.db.models import Agent, AgentCapabilityGrant, ApprovalRequest, RoleGap
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


async def seed_gap(factory, *, gap_id: str, name: str, tools: list[str]):
    async with factory() as session:
        gap = RoleGap(
            id=gap_id,
            title=f"Recommended role: {name}",
            description="Company context requires this capability.",
            status="proposed",
            severity="medium",
            source_type="company_context_snapshot",
            company_namespace="company:acme",
            capability="operations",
            requested_tools=tools,
            context={"snapshot_id": "ctx_1", "source_hash": "hash-1"},
            proposed_role={
                "manifest_payload": {
                    "family": "operations",
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
