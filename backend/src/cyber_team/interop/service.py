"""MCP- and A2A-compatible adapter views over Cyber-Team runtime state."""

from __future__ import annotations

from typing import Any

from cyber_team.agents.manager import AgentManager
from cyber_team.config import settings
from cyber_team.roles.team_activation import TeamActivationService
from cyber_team.tools.registry import ToolRegistry


class InteropService:
    """Exports Cyber-Team capabilities without replacing the internal runtime."""

    MCP_VERSION = "2025-11-25"
    A2A_VERSION = "0.3.0"

    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        agent_manager: AgentManager,
        team_activation: TeamActivationService,
    ) -> None:
        self._tool_registry = tool_registry
        self._agent_manager = agent_manager
        self._team_activation = team_activation

    def mcp_tool_catalog(self) -> dict[str, Any]:
        tools = []
        for contract in self._tool_registry.list_tool_contracts():
            tools.append(
                {
                    "name": contract["name"],
                    "description": contract["description"],
                    "inputSchema": contract["input_schema"],
                    "outputSchema": contract["output_schema"],
                    "annotations": {
                        "title": contract["name"].replace("_", " ").title(),
                        "readOnlyHint": not contract["side_effects"],
                        "destructiveHint": bool(contract["side_effects"]),
                        "openWorldHint": bool(contract["side_effects"]),
                        "riskLevel": contract["risk_level"],
                        "category": contract["category"],
                    },
                    "readiness": {
                        "state": contract["state"],
                        "reason": contract["readiness_reason"],
                        "executor_kind": contract["executor_kind"],
                        "requires_configuration": contract["requires_configuration"],
                        "requires_approval": contract["requires_approval"],
                    },
                }
            )
        return {
            "protocol": "mcp",
            "version": self.MCP_VERSION,
            "server": {
                "name": "cyber-team",
                "app_version": settings.app_version,
                "build_sha": settings.build_sha,
                "capabilities": {"tools": True, "resources": False, "prompts": False},
            },
            "tools": tools,
            "counts": {
                "total": len(tools),
                "live": sum(1 for tool in tools if tool["readiness"]["state"] == "live"),
                "advisory": sum(
                    1 for tool in tools if tool["readiness"]["state"] == "advisory"
                ),
                "configuration_required": sum(
                    1
                    for tool in tools
                    if tool["readiness"]["state"] == "configuration_required"
                ),
                "unavailable": sum(
                    1 for tool in tools if tool["readiness"]["state"] == "unavailable"
                ),
            },
        }

    async def a2a_agent_cards(self) -> dict[str, Any]:
        agents = await self._agent_manager.list_agents()
        cards = []
        for agent in agents:
            grants = await self._team_activation.list_agent_grants(agent["id"])
            active_grants = [grant for grant in grants if grant["state"] == "active"]
            pending_grants = [
                grant
                for grant in grants
                if grant["state"] in {"pending_approval", "configuration_required", "blocked"}
            ]
            cards.append(
                {
                    "protocol": "a2a",
                    "version": self.A2A_VERSION,
                    "id": agent["id"],
                    "name": agent["role_name"],
                    "description": agent["instructions"][:500],
                    "role_family": agent["role_family"],
                    "status": agent["status"],
                    "memory_namespace": agent["memory_namespace"],
                    "approval_policy": agent["approval_policy"],
                    "skills": [
                        {
                            "id": grant["tool_name"],
                            "name": grant["tool_name"].replace("_", " ").title(),
                            "state": grant["state"],
                            "risk_level": grant["risk_level"],
                            "side_effects": grant["side_effects"],
                            "approval_id": grant["approval_id"],
                        }
                        for grant in grants
                    ],
                    "capabilities": {
                        "active_tool_count": len(active_grants),
                        "pending_or_blocked_tool_count": len(pending_grants),
                        "agent_invocation_endpoint": f"/api/agents/{agent['id']}/invoke",
                    },
                    "metadata": {
                        "config": agent.get("config") or {},
                        "tool_names": agent.get("tools") or [],
                    },
                }
            )
        return {
            "protocol": "a2a",
            "version": self.A2A_VERSION,
            "agents": cards,
            "counts": {
                "total": len(cards),
                "active": sum(1 for card in cards if card["status"] == "active"),
            },
        }

    async def summary(self) -> dict[str, Any]:
        mcp = self.mcp_tool_catalog()
        a2a = await self.a2a_agent_cards()
        return {
            "mcp": {
                "version": mcp["version"],
                "tool_counts": mcp["counts"],
            },
            "a2a": {
                "version": a2a["version"],
                "agent_counts": a2a["counts"],
            },
            "status": "available",
        }
