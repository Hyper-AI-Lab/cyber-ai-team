"""Tool Registry — executable tool definitions for agent use.

Each tool has a name, description, parameter schema, and an async execute function.
Agents reference tools by name; the registry resolves and executes them.
"""

import logging
import subprocess
import uuid
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import MemoryEntry

logger = logging.getLogger(__name__)


class ToolParameter(BaseModel):
    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    default: Any = None
    enum: list[Any] | None = None

    def to_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "type": self._json_type(),
            "description": self.description,
        }
        if self.default is not None:
            schema["default"] = self.default
        if self.enum:
            schema["enum"] = self.enum
        return schema

    def _json_type(self) -> str:
        aliases = {
            "dict": "object",
            "list": "array",
            "float": "number",
            "int": "integer",
            "bool": "boolean",
        }
        return aliases.get(self.type, self.type)


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: list[ToolParameter] = Field(default_factory=list)
    category: str = "general"
    requires_approval: bool = False
    risk_level: str = "low"
    side_effects: bool = False
    executor_kind: str = "live"
    requires_configuration: bool = False
    configuration_keys: list[str] = Field(default_factory=list)
    readiness_reason: str | None = None
    output_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})

    def input_schema(self) -> dict[str, Any]:
        properties = {parameter.name: parameter.to_schema() for parameter in self.parameters}
        required = [parameter.name for parameter in self.parameters if parameter.required]
        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }

    def contract(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "requires_approval": self.requires_approval,
            "risk_level": self.risk_level,
            "state": self.executor_kind,
            "readiness_reason": self.readiness_reason,
            "side_effects": self.side_effects,
            "executor_kind": self.executor_kind,
            "requires_configuration": self.requires_configuration,
            "parameters": [parameter.model_dump() for parameter in self.parameters],
            "input_schema": self.input_schema(),
            "output_schema": self.output_schema,
        }


class ToolResult(BaseModel):
    success: bool
    output: Any = None
    error: str | None = None


class ToolRegistry:
    """Central registry for executable tools that agents can invoke."""

    TOOL_ALIASES = {
        "call_make": "make_call",
        "crm_lead_create": "erpnext_create_lead",
        "email_send": "send_email",
        "message_send": "send_message",
        "sms_send": "send_sms",
    }

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        self._executors: dict[str, Callable] = {}
        self._register_builtin_tools()

    def register(self, tool: ToolDefinition, executor: Callable) -> None:
        """Register a tool with its executor function."""
        self._tools[tool.name] = tool
        self._executors[tool.name] = executor
        logger.debug(f"Registered tool: {tool.name}")

    def get_tool(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_tools(self, category: str | None = None) -> list[ToolDefinition]:
        tools = list(self._tools.values())
        if category:
            tools = [t for t in tools if t.category == category]
        return tools

    def list_tool_contracts(
        self,
        category: str | None = None,
        allowed_tools: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        allowed = set(allowed_tools) if allowed_tools is not None else None
        return [
            self._contract_for(tool)
            for tool in self.list_tools(category)
            if allowed is None or tool.name in allowed
        ]

    def _contract_for(self, tool: ToolDefinition) -> dict[str, Any]:
        contract = tool.contract()
        readiness = self.get_tool_readiness(tool.name)
        contract.update(
            {
                "state": readiness["state"],
                "readiness_reason": readiness["readiness_reason"],
                "side_effects": readiness["side_effects"],
                "executor_kind": readiness["executor_kind"],
                "requires_configuration": readiness["requires_configuration"],
            }
        )
        return contract

    def get_tool_readiness(
        self,
        tool_name: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        tool = self._tools.get(tool_name)
        if not tool:
            return {
                "state": "unavailable",
                "readiness_reason": f"Tool not found: {tool_name}",
                "side_effects": False,
                "executor_kind": "unavailable",
                "requires_configuration": False,
                "executable": False,
            }

        state = tool.executor_kind
        reason = tool.readiness_reason
        requires_configuration = tool.requires_configuration
        if tool.executor_kind == "live":
            state = "live"
            reason = reason or "Live executor is registered."
        elif tool.executor_kind == "advisory":
            state = "advisory"
            reason = (
                reason
                or "Advisory executor can draft or inspect, but cannot mutate external systems."
            )
        elif tool.executor_kind == "configuration_required":
            state = "configuration_required"
            reason = reason or "This tool requires configuration before it can run."
            requires_configuration = True
        elif tool.executor_kind == "unavailable":
            state = "unavailable"
            reason = reason or "No live executor is registered for this tool."

        dynamic = self._dynamic_readiness(tool, params or {})
        if dynamic:
            state = dynamic["state"]
            reason = dynamic["readiness_reason"]
            requires_configuration = dynamic["requires_configuration"]

        proof_block = (
            settings.require_live_tool_executors
            and tool.side_effects
            and state != "live"
        )
        executable = state in {"live", "advisory"} and not proof_block
        if proof_block:
            executable = False
            reason = (
                reason
                or "A live executor is required for side-effectful tools in this environment."
            )
            if state == "advisory":
                state = "unavailable"

        return {
            "state": state,
            "readiness_reason": reason,
            "side_effects": tool.side_effects,
            "executor_kind": tool.executor_kind,
            "requires_configuration": requires_configuration,
            "executable": executable,
        }

    def validate_params(
        self,
        tool_name: str,
        params: dict | None,
    ) -> tuple[bool, dict, str | None]:
        if tool_name not in self._tools:
            return False, {}, f"Tool not found: {tool_name}"
        tool = self._tools[tool_name]
        validated = deepcopy(params or {})
        parameter_map = {parameter.name: parameter for parameter in tool.parameters}
        unknown = sorted(set(validated) - set(parameter_map))
        if unknown:
            return False, validated, f"Unexpected parameters: {', '.join(unknown)}"
        for parameter in tool.parameters:
            if parameter.name not in validated:
                if parameter.default is not None:
                    validated[parameter.name] = deepcopy(parameter.default)
                elif parameter.required:
                    return False, validated, f"Missing required parameter: {parameter.name}"
                else:
                    continue
            value = validated.get(parameter.name)
            if value is None:
                if parameter.required:
                    return False, validated, f"Missing required parameter: {parameter.name}"
                continue
            if parameter.enum and value not in parameter.enum:
                allowed_values = ", ".join(str(item) for item in parameter.enum)
                return (
                    False,
                    validated,
                    f"Parameter {parameter.name} must be one of: {allowed_values}",
                )
            if not self._value_matches_type(parameter.type, value):
                return False, validated, f"Parameter {parameter.name} must be {parameter.type}"
        return True, validated, None

    async def execute(self, tool_name: str, params: dict = None) -> ToolResult:
        """Execute a tool by name with given parameters."""
        params = dict(params or {})

        agent_id = params.pop("_agent_id", None)
        approval_id = params.pop("_approval_id", None)
        actor = params.pop("_actor", agent_id or "owner")
        actor_type = params.pop("_actor_type", "agent" if agent_id else "user")
        conversation_id = params.pop("_conversation_id", None)
        workflow_run_id = params.pop("_workflow_run_id", None)
        workflow_node_id = params.pop("_workflow_node_id", None)
        source_type = params.pop("_source_type", "tool_execution")
        trace_metadata = {
            "tool_name": tool_name,
            "conversation_id": conversation_id,
            "workflow_run_id": workflow_run_id,
            "workflow_node_id": workflow_node_id,
            "actor": actor,
            "actor_type": actor_type,
            "approval_id": approval_id,
        }
        if tool_name == "role_gap_report" and agent_id:
            params.setdefault("source_agent_id", agent_id)
            params.setdefault("source_type", "agent")

        if tool_name not in self._tools:
            error = f"Tool not found: {tool_name}"
            readiness = self.get_tool_readiness(tool_name)
            await self._audit_tool_event(
                tool_name,
                actor=actor,
                actor_type=actor_type,
                outcome="failed",
                metadata={"error": "tool_not_found", **readiness},
            )
            self._record_tool_metric(tool_name, "failed", "unknown", readiness["state"])
            await self._record_tool_trace(
                tool_name,
                agent_id=agent_id,
                source_type=source_type,
                conversation_id=conversation_id,
                workflow_run_id=workflow_run_id,
                task_excerpt=f"Execute tool {tool_name}",
                metadata={**trace_metadata, **readiness},
                errors=[error],
            )
            await self._report_tool_gap(
                tool_name,
                agent_id=agent_id,
                actor=actor,
                actor_type=actor_type,
                reason="tool_not_found",
                error=error,
            )
            return ToolResult(success=False, error=error)

        tool = self._tools[tool_name]
        executor = self._executors[tool_name]
        valid, params, validation_error = self.validate_params(tool_name, params)
        if not valid:
            readiness = self.get_tool_readiness(tool_name)
            await self._audit_tool_event(
                tool_name,
                actor=actor,
                actor_type=actor_type,
                outcome="failed",
                metadata={"error": validation_error, **readiness},
            )
            self._record_tool_metric(
                tool_name,
                "failed",
                tool.risk_level,
                readiness["state"],
            )
            await self._record_tool_trace(
                tool_name,
                agent_id=agent_id,
                source_type=source_type,
                conversation_id=conversation_id,
                workflow_run_id=workflow_run_id,
                task_excerpt=f"Execute tool {tool_name}",
                metadata={**trace_metadata, **readiness},
                errors=[validation_error or "validation_failed"],
            )
            return ToolResult(success=False, error=validation_error)

        readiness = self.get_tool_readiness(tool_name, params)
        trace_metadata.update(readiness)
        if params.get("namespace"):
            trace_metadata["memory_namespace"] = params.get("namespace")
        if not readiness["executable"]:
            output = self._blocked_tool_output(tool_name, params, readiness)
            await self._audit_tool_event(
                tool_name,
                actor=actor,
                actor_type=actor_type,
                outcome="blocked",
                metadata={"reason": readiness["readiness_reason"], **readiness},
            )
            self._record_tool_metric(
                tool_name,
                "blocked",
                tool.risk_level,
                readiness["state"],
            )
            await self._record_tool_trace(
                tool_name,
                agent_id=agent_id,
                source_type=source_type,
                conversation_id=conversation_id,
                workflow_run_id=workflow_run_id,
                task_excerpt=f"Execute tool {tool_name}",
                metadata=trace_metadata,
                errors=[readiness["readiness_reason"] or "tool_not_executable"],
            )
            await self._report_tool_gap(
                tool_name,
                agent_id=agent_id,
                actor=actor,
                actor_type=actor_type,
                reason=readiness["state"],
                error=readiness["readiness_reason"],
            )
            return ToolResult(
                success=False,
                output=output,
                error=readiness["readiness_reason"] or "Tool is not executable",
            )

        approval_required = self._approval_required_for(tool)
        if approval_required and not await self._approval_granted(approval_id, tool_name):
            requested_id = None
            if self._agent_manager:
                requested_id = await self._agent_manager._request_approval(
                    agent_id,
                    f"tool:{tool_name}",
                    f"Execute tool {tool_name}",
                    {
                        "tool_name": tool_name,
                        "params": params,
                        "payload_summary": self._payload_summary(params),
                        "replay_instructions": self._replay_instructions(tool_name, params),
                    },
                    requester=agent_id or actor,
                    requester_type="agent" if agent_id else actor_type,
                    risk_level=tool.risk_level,
                    target_type="tool",
                    target_id=tool_name,
                )
            await self._audit_tool_event(
                tool_name,
                actor=actor,
                actor_type=actor_type,
                outcome="blocked",
                event_type="tool.approval_required",
                metadata={"approval_id": requested_id, **readiness},
            )
            self._record_approval_metric("requested", "blocked", tool.risk_level)
            self._record_tool_metric(
                tool_name,
                "blocked",
                tool.risk_level,
                readiness["state"],
            )
            output = {
                "approval_required": True,
                "approval_id": requested_id,
                "tool_name": tool_name,
                "risk_level": tool.risk_level,
                "reason": "Owner approval is required before executing this tool.",
                "target": {"type": "tool", "id": tool_name},
                "payload_summary": self._payload_summary(params),
                "replay_instructions": self._replay_instructions(tool_name, params),
            }
            await self._record_tool_trace(
                tool_name,
                agent_id=agent_id,
                source_type=source_type,
                conversation_id=conversation_id,
                workflow_run_id=workflow_run_id,
                task_excerpt=f"Execute tool {tool_name}",
                metadata={**trace_metadata, "approval_id": requested_id},
                errors=["approval_required"],
            )
            return ToolResult(
                success=False,
                output=output,
                error="Approval required before executing this tool",
            )

        try:
            if approval_id and approval_required and self._agent_manager:
                await self._agent_manager.consume_approval(
                    approval_id,
                    consumer=f"tool:{tool_name}",
                    target_type="tool",
                    target_id=tool_name,
                )
                self._record_approval_metric("consumed", "success", tool.risk_level)
            result = await executor(**params)
            if self._tool_output_signals_unavailable(result):
                error = self._stringify_tool_output(result)
                await self._report_tool_gap(
                    tool_name,
                    agent_id=agent_id,
                    actor=actor,
                    actor_type=actor_type,
                    reason="service_unavailable",
                    error=error,
                )
                await self._audit_tool_event(
                    tool_name,
                    actor=actor,
                    actor_type=actor_type,
                    outcome="failed",
                    metadata={
                        "approval_id": approval_id,
                        "requires_approval": approval_required,
                        "error": error,
                        **readiness,
                    },
                )
                self._record_tool_metric(
                    tool_name,
                    "failed",
                    tool.risk_level,
                    readiness["state"],
                )
                await self._record_tool_trace(
                    tool_name,
                    agent_id=agent_id,
                    source_type=source_type,
                    conversation_id=conversation_id,
                    workflow_run_id=workflow_run_id,
                    task_excerpt=f"Execute tool {tool_name}",
                    metadata=trace_metadata,
                    output=result,
                    errors=[error],
                )
                return ToolResult(success=False, output=result, error=error)
            await self._audit_tool_event(
                tool_name,
                actor=actor,
                actor_type=actor_type,
                outcome="success",
                metadata={
                    "approval_id": approval_id,
                    "requires_approval": approval_required,
                    **readiness,
                },
            )
            self._record_tool_metric(
                tool_name,
                "success",
                tool.risk_level,
                readiness["state"],
            )
            await self._record_tool_trace(
                tool_name,
                agent_id=agent_id,
                source_type=source_type,
                conversation_id=conversation_id,
                workflow_run_id=workflow_run_id,
                task_excerpt=f"Execute tool {tool_name}",
                metadata=trace_metadata,
                output=result,
            )
            return ToolResult(success=True, output=result)
        except Exception as e:
            logger.error(f"Tool execution failed [{tool_name}]: {e}")
            if self._tool_output_signals_unavailable(str(e)):
                await self._report_tool_gap(
                    tool_name,
                    agent_id=agent_id,
                    actor=actor,
                    actor_type=actor_type,
                    reason="service_unavailable",
                    error=str(e),
                )
            await self._audit_tool_event(
                tool_name,
                actor=actor,
                actor_type=actor_type,
                outcome="failed",
                metadata={"approval_id": approval_id, "error": str(e), **readiness},
            )
            self._record_tool_metric(
                tool_name,
                "failed",
                tool.risk_level,
                readiness["state"],
            )
            await self._record_tool_trace(
                tool_name,
                agent_id=agent_id,
                source_type=source_type,
                conversation_id=conversation_id,
                workflow_run_id=workflow_run_id,
                task_excerpt=f"Execute tool {tool_name}",
                metadata=trace_metadata,
                errors=[str(e)],
            )
            return ToolResult(success=False, error=str(e))

    def _dynamic_readiness(
        self,
        tool: ToolDefinition,
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        canonical_name = self._canonical_tool_name(tool.name)
        if canonical_name in {"send_email", "send_sms", "make_call", "send_message"}:
            return self._communications_readiness(tool, params, canonical_name)
        if canonical_name == "ci_trigger":
            if settings.github_ci_configured:
                return {
                    "state": "live",
                    "readiness_reason": "GitHub workflow_dispatch credentials are configured.",
                    "requires_configuration": False,
                }
            return {
                "state": "configuration_required",
                "readiness_reason": (
                    "GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_DEFAULT_WORKFLOW, "
                    "and GITHUB_DEFAULT_REF are required for CI triggering."
                ),
                "requires_configuration": True,
            }
        if tool.category == "erpnext" and (
            tool.side_effects or settings.require_live_tool_executors
        ):
            configured = bool(settings.erpnext_api_key and settings.erpnext_api_secret)
            if configured:
                return {
                    "state": "live",
                    "readiness_reason": "ERPNext credentials are configured.",
                    "requires_configuration": False,
                }
            return {
                "state": "configuration_required",
                "readiness_reason": "ERPNext API credentials are required for this tool.",
                "requires_configuration": True,
            }
        return None

    def _communications_readiness(
        self,
        tool: ToolDefinition,
        params: dict[str, Any],
        canonical_name: str,
    ) -> dict[str, Any]:
        if not self._comms_gateway:
            return {
                "state": "configuration_required",
                "readiness_reason": "Communications gateway is not available.",
                "requires_configuration": True,
            }
        integration_status = getattr(self._comms_gateway, "integration_status", None)
        if not integration_status:
            return {
                "state": "live",
                "readiness_reason": "Communications executor is injected.",
                "requires_configuration": False,
            }
        statuses = integration_status()
        channel = {
            "send_email": "email",
            "send_sms": "sms",
            "make_call": "voice",
            "send_message": params.get("platform"),
        }.get(canonical_name)
        if canonical_name == "send_message" and not channel:
            messaging_channels = {"telegram", "whatsapp", "slack"}
            candidates = [
                item for item in statuses if item.get("channel") in messaging_channels
            ]
        else:
            candidates = [
                item for item in statuses
                if not channel or item.get("channel") == channel
            ]
        if any(item.get("mode") == "live" for item in candidates):
            return {
                "state": "live",
                "readiness_reason": f"Live {channel or 'communications'} provider is configured.",
                "requires_configuration": False,
            }
        if any(item.get("mode") == "simulated" for item in candidates):
            return {
                "state": "configuration_required"
                if settings.require_live_tool_executors
                else "advisory",
                "readiness_reason": (
                    f"{channel or 'Communications'} provider is simulation-only; "
                    "configure a live provider for production side effects."
                ),
                "requires_configuration": True,
            }
        return {
            "state": "configuration_required",
            "readiness_reason": f"No configured {channel or 'communications'} provider.",
            "requires_configuration": True,
        }

    def _canonical_tool_name(self, tool_name: str) -> str:
        return self.TOOL_ALIASES.get(tool_name, tool_name)

    def _register_tool_alias(self, alias: str, target_name: str) -> None:
        target = self._tools[target_name]
        target_executor = self._executors[target_name]
        alias_tool = target.model_copy(
            deep=True,
            update={
                "name": alias,
                "description": f"Alias for {target_name}: {target.description}",
                "readiness_reason": f"Delegates to canonical tool {target_name}.",
            },
        )

        async def alias_executor(**params):
            return await target_executor(**params)

        self.register(alias_tool, alias_executor)

    def _register_tool_aliases(self) -> None:
        for alias, target_name in self.TOOL_ALIASES.items():
            if alias in self._tools:
                continue
            if target_name not in self._tools:
                logger.warning(
                    "Skipping tool alias %s because canonical tool %s is unavailable",
                    alias,
                    target_name,
                )
                continue
            self._register_tool_alias(alias, target_name)

    def _approval_required_for(self, tool: ToolDefinition) -> bool:
        if tool.requires_approval:
            return True
        if tool.side_effects and tool.risk_level in {"medium", "high", "critical"}:
            return True
        return (
            settings.autonomy_side_effect_mode == "manual_only"
            and tool.side_effects
        )

    @staticmethod
    def _payload_summary(params: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for key, value in params.items():
            if isinstance(value, str):
                summary[key] = value if len(value) <= 180 else value[:177] + "..."
            elif isinstance(value, (int, float, bool)) or value is None:
                summary[key] = value
            elif isinstance(value, list):
                summary[key] = {"type": "list", "count": len(value)}
            elif isinstance(value, dict):
                summary[key] = {
                    "type": "object",
                    "keys": sorted(str(item) for item in value.keys())[:20],
                }
            else:
                summary[key] = {"type": type(value).__name__}
        return summary

    @staticmethod
    def _replay_instructions(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "method": "POST",
            "path": "/api/tools/execute",
            "body": {
                "tool_name": tool_name,
                "params": params,
            },
        }

    def _blocked_tool_output(
        self,
        tool_name: str,
        params: dict[str, Any],
        readiness: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "tool_name": tool_name,
            "blocked": True,
            "state": readiness["state"],
            "readiness_reason": readiness["readiness_reason"],
            "requires_configuration": readiness["requires_configuration"],
            "side_effects": readiness["side_effects"],
            "executor_kind": readiness["executor_kind"],
            "payload_summary": self._payload_summary(params),
            "replay_instructions": self._replay_instructions(tool_name, params),
        }

    async def _audit_tool_event(
        self,
        tool_name: str,
        *,
        actor: str,
        actor_type: str,
        outcome: str,
        metadata: dict[str, Any],
        event_type: str = "tool.execute",
    ) -> None:
        if not self._audit:
            return
        await self._audit.record(
            event_type=event_type,
            actor=actor,
            actor_type=actor_type,
            resource_type="tool",
            resource_id=tool_name,
            action="execute",
            outcome=outcome,
            metadata=metadata,
        )

    def _record_tool_metric(
        self,
        tool_name: str,
        status: str,
        risk_level: str,
        state: str,
    ) -> None:
        if self._metrics:
            self._metrics.record_tool_execution(tool_name, status, risk_level, state)

    def _record_approval_metric(
        self,
        action: str,
        status: str,
        risk_level: str,
    ) -> None:
        if self._metrics:
            self._metrics.record_approval_event(action, status, risk_level)

    async def _record_tool_trace(
        self,
        tool_name: str,
        *,
        agent_id: str | None,
        source_type: str,
        conversation_id: str | None,
        workflow_run_id: str | None,
        task_excerpt: str,
        metadata: dict[str, Any],
        output: Any = None,
        errors: list[str] | None = None,
    ) -> None:
        if not self._memory_service:
            return
        recalled_ids, written_ids = self._memory_ids_from_tool_output(tool_name, output)
        trace_metadata = dict(metadata)
        trace_metadata["coverage"] = self._trace_coverage(
            recalled_ids=recalled_ids,
            written_ids=written_ids,
            errors=errors or [],
        )
        try:
            await self._memory_service.record_trace(
                SimpleNamespace(
                    id=str(uuid.uuid4()),
                    invocation_id=f"tool:{uuid.uuid4()}",
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    source_type=source_type,
                    task_excerpt=task_excerpt,
                    memory_namespace=trace_metadata.get("memory_namespace"),
                    read_policy={"tool_name": tool_name},
                    write_policy={"tool_name": tool_name},
                    recalled_memory_ids=recalled_ids,
                    written_memory_ids=written_ids,
                    recall_count=len(recalled_ids),
                    write_count=len(written_ids),
                    errors=errors or [],
                    metadata=trace_metadata,
                )
            )
        except Exception:
            logger.debug("Failed to record tool memory trace for %s", tool_name, exc_info=True)

    @staticmethod
    def _memory_ids_from_tool_output(
        tool_name: str,
        output: Any,
    ) -> tuple[list[str], list[str]]:
        if not output:
            return [], []
        if tool_name in {"memory_recall", "knowledge_query"} and isinstance(output, list):
            return [
                str(item["id"])
                for item in output
                if isinstance(item, dict) and item.get("id")
            ], []
        if tool_name in {"memory_remember", "document_index"} and isinstance(output, dict):
            memory_id = output.get("id") or output.get("memory_id")
            return [], [str(memory_id)] if memory_id else []
        return [], []

    @staticmethod
    def _trace_coverage(
        *,
        recalled_ids: list[str],
        written_ids: list[str],
        errors: list[str],
    ) -> str:
        if errors:
            return "error"
        if recalled_ids and written_ids:
            return "read_write"
        if recalled_ids:
            return "read"
        if written_ids:
            return "write"
        return "metadata_only"

    def get_tools_for_agent(self, tool_names: list[str]) -> list[ToolDefinition]:
        """Get tool definitions available to an agent based on its tool list."""
        return [self._tools[name] for name in tool_names if name in self._tools]

    @staticmethod
    def _value_matches_type(parameter_type: str, value: Any) -> bool:
        parameter_type = {
            "dict": "object",
            "list": "array",
            "float": "number",
            "int": "integer",
            "bool": "boolean",
        }.get(parameter_type, parameter_type)
        if parameter_type == "string":
            return isinstance(value, str)
        if parameter_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if parameter_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if parameter_type == "boolean":
            return isinstance(value, bool)
        if parameter_type == "object":
            return isinstance(value, dict)
        if parameter_type == "array":
            return isinstance(value, list)
        return True

    async def _report_tool_gap(
        self,
        tool_name: str,
        *,
        agent_id: str | None,
        actor: str,
        actor_type: str,
        reason: str,
        error: str | None = None,
    ) -> None:
        if not self._agent_manager:
            return
        report_tool_gap = getattr(self._agent_manager, "report_tool_gap", None)
        if not report_tool_gap:
            return
        try:
            await report_tool_gap(
                tool_name,
                agent_id=agent_id,
                actor=actor,
                actor_type=actor_type,
                reason=reason,
                error=error,
            )
        except Exception:
            logger.debug("Failed to report tool role gap for %s", tool_name, exc_info=True)

    @staticmethod
    def _tool_output_signals_unavailable(output: Any) -> bool:
        text = ToolRegistry._stringify_tool_output(output).lower()
        unavailable_phrases = (
            "not available",
            "not configured",
            "missing configuration",
            "missing credentials",
            "client unavailable",
            "client not available",
            "gateway unavailable",
            "gateway not available",
            "provider unavailable",
            "provider not available",
        )
        return any(phrase in text for phrase in unavailable_phrases)

    @staticmethod
    def _stringify_tool_output(output: Any) -> str:
        if isinstance(output, str):
            return output
        if isinstance(output, dict):
            return " ".join(str(value) for value in output.values())
        if isinstance(output, list):
            return " ".join(ToolRegistry._stringify_tool_output(item) for item in output)
        return str(output)

    def _register_builtin_tools(self):
        """Register built-in tools that ship with Cyber-Team."""

        # ─── Communication Tools ─────────────────────────────────────
        self.register(
            ToolDefinition(
                name="send_email",
                description="Send an email to a recipient",
                parameters=[
                    ToolParameter(name="to_address", description="Recipient email"),
                    ToolParameter(name="subject", description="Email subject"),
                    ToolParameter(name="body", description="Email body (HTML)"),
                    ToolParameter(
                        name="cc",
                        type="list",
                        description="CC recipients",
                        required=False,
                        default=[],
                    ),
                    ToolParameter(
                        name="idempotency_key",
                        description="Optional stable key to prevent duplicate sends",
                        required=False,
                    ),
                ],
                category="communications",
                requires_approval=True,
                risk_level="high",
                side_effects=True,
            ),
            self._tool_send_email,
        )

        self.register(
            ToolDefinition(
                name="send_sms",
                description="Send an SMS message",
                parameters=[
                    ToolParameter(name="to_number", description="Recipient phone number"),
                    ToolParameter(name="message", description="SMS message text"),
                    ToolParameter(
                        name="idempotency_key",
                        description="Optional stable key to prevent duplicate sends",
                        required=False,
                    ),
                ],
                category="communications",
                requires_approval=True,
                risk_level="high",
                side_effects=True,
            ),
            self._tool_send_sms,
        )

        self.register(
            ToolDefinition(
                name="make_call",
                description="Make a phone call with a spoken message",
                parameters=[
                    ToolParameter(name="to_number", description="Recipient phone number"),
                    ToolParameter(name="context", description="What to say on the call"),
                    ToolParameter(
                        name="idempotency_key",
                        description="Optional stable key to prevent duplicate calls",
                        required=False,
                    ),
                ],
                category="communications",
                requires_approval=True,
                risk_level="high",
                side_effects=True,
            ),
            self._tool_make_call,
        )

        self.register(
            ToolDefinition(
                name="send_message",
                description="Send a message via a configured messaging provider",
                parameters=[
                    ToolParameter(
                        name="platform",
                        description="Platform: telegram, whatsapp, slack",
                        enum=["telegram", "whatsapp", "slack"],
                    ),
                    ToolParameter(name="recipient", description="Recipient identifier"),
                    ToolParameter(name="message", description="Message text"),
                    ToolParameter(
                        name="idempotency_key",
                        description="Optional stable key to prevent duplicate sends",
                        required=False,
                    ),
                ],
                category="communications",
                requires_approval=True,
                risk_level="high",
                side_effects=True,
            ),
            self._tool_send_message,
        )

        # ─── Memory Tools ────────────────────────────────────────────
        self.register(
            ToolDefinition(
                name="memory_remember",
                description="Store a memory entry for later recall",
                parameters=[
                    ToolParameter(name="content", description="Content to remember"),
                    ToolParameter(
                        name="memory_type",
                        description="Type: episodic, semantic, procedural, entity",
                        enum=["episodic", "semantic", "procedural", "entity"],
                    ),
                    ToolParameter(name="namespace", description="Memory namespace"),
                    ToolParameter(
                        name="importance",
                        type="float",
                        description="Importance 0-1",
                        required=False,
                        default=0.5,
                    ),
                ],
                category="memory",
                risk_level="medium",
            ),
            self._tool_memory_remember,
        )

        self.register(
            ToolDefinition(
                name="memory_recall",
                description="Search and retrieve relevant memories",
                parameters=[
                    ToolParameter(name="query", description="Search query"),
                    ToolParameter(
                        name="namespace",
                        description="Filter by namespace",
                        required=False,
                    ),
                    ToolParameter(
                        name="limit",
                        type="int",
                        description="Max results",
                        required=False,
                        default=5,
                    ),
                ],
                category="memory",
            ),
            self._tool_memory_recall,
        )

        # ─── ERPNext Tools ───────────────────────────────────────────
        self.register(
            ToolDefinition(
                name="erpnext_get_invoices",
                description="Retrieve invoices from ERPNext",
                parameters=[
                    ToolParameter(
                        name="filters",
                        type="dict",
                        description="Filter dict",
                        required=False,
                    ),
                ],
                category="erpnext",
            ),
            self._tool_erpnext_get_invoices,
        )

        self.register(
            ToolDefinition(
                name="erpnext_create_lead",
                description="Create a new lead in ERPNext CRM",
                parameters=[
                    ToolParameter(name="lead_data", type="dict", description="Lead data dict"),
                ],
                category="erpnext",
                requires_approval=True,
                risk_level="high",
                side_effects=True,
                requires_configuration=True,
            ),
            self._tool_erpnext_create_lead,
        )

        self._register_tool_aliases()

        self.register(
            ToolDefinition(
                name="erpnext_get_projects",
                description="List projects from ERPNext",
                parameters=[
                    ToolParameter(
                        name="filters",
                        type="dict",
                        description="Filter dict",
                        required=False,
                    ),
                ],
                category="erpnext",
            ),
            self._tool_erpnext_get_projects,
        )

        # ─── Role Management Tools ───────────────────────────────────
        self.register(
            ToolDefinition(
                name="role_catalog_search",
                description="Search the role catalog for available roles",
                parameters=[
                    ToolParameter(name="query", description="Search query for roles"),
                ],
                category="roles",
            ),
            self._tool_role_catalog_search,
        )

        self.register(
            ToolDefinition(
                name="role_instantiate",
                description="Instantiate a role from a manifest",
                parameters=[
                    ToolParameter(name="manifest_id", description="Role manifest ID"),
                    ToolParameter(
                        name="overrides",
                        type="dict",
                        description="Override parameters",
                        required=False,
                    ),
                ],
                category="roles",
                requires_approval=True,
                risk_level="high",
                side_effects=True,
            ),
            self._tool_role_instantiate,
        )

        self.register(
            ToolDefinition(
                name="role_gap_report",
                description="Report a missing role, skill, tool, or capability to Company Builder",
                parameters=[
                    ToolParameter(name="title", description="Short gap title"),
                    ToolParameter(name="description", description="What work is blocked and why"),
                    ToolParameter(
                        name="severity",
                        description="Gap severity",
                        required=False,
                        default="medium",
                        enum=["low", "medium", "high", "critical"],
                    ),
                    ToolParameter(
                        name="capability",
                        description="Missing business capability",
                        required=False,
                    ),
                    ToolParameter(
                        name="requested_tools",
                        type="list",
                        description="Tools that would unblock the work",
                        required=False,
                        default=[],
                    ),
                    ToolParameter(
                        name="company_namespace",
                        description="Company memory namespace",
                        required=False,
                        default="company:default",
                    ),
                    ToolParameter(
                        name="context",
                        type="dict",
                        description="Structured context about the blocked work",
                        required=False,
                        default={},
                    ),
                    ToolParameter(
                        name="source_agent_id",
                        description="Reporting agent ID",
                        required=False,
                    ),
                    ToolParameter(
                        name="source_type",
                        description="Reporter type",
                        required=False,
                        default="agent",
                        enum=["agent", "system", "owner", "user"],
                    ),
                ],
                category="roles",
                risk_level="medium",
            ),
            self._tool_role_gap_report,
        )

        # ─── Agent Management Tools ──────────────────────────────────
        self.register(
            ToolDefinition(
                name="agent_status_read",
                description="Read status of all agents",
                parameters=[],
                category="agents",
            ),
            self._tool_agent_status_read,
        )

        self.register(
            ToolDefinition(
                name="agent_invoke",
                description="Invoke another agent with a task",
                parameters=[
                    ToolParameter(name="agent_id", description="Target agent ID"),
                    ToolParameter(name="task", description="Task description"),
                ],
                category="agents",
            ),
            self._tool_agent_invoke,
        )

        self.register(
            ToolDefinition(
                name="approval_request",
                description="Request human approval for an action",
                parameters=[
                    ToolParameter(name="action_type", description="Type of action"),
                    ToolParameter(name="description", description="Action description"),
                ],
                category="governance",
            ),
            self._tool_approval_request,
        )

        self.register(
            ToolDefinition(
                name="approval_resolve",
                description="Approve or reject a pending approval request",
                parameters=[
                    ToolParameter(name="approval_id", description="Approval request ID"),
                    ToolParameter(
                        name="decision",
                        description="approved or rejected",
                        enum=["approved", "rejected"],
                    ),
                ],
                category="governance",
                requires_approval=True,
                risk_level="high",
                side_effects=True,
            ),
            self._tool_approval_resolve,
        )

        self.register(
            ToolDefinition(
                name="owner_notify",
                description="Send a notification to the human owner",
                parameters=[
                    ToolParameter(name="message", description="Notification message"),
                    ToolParameter(
                        name="priority",
                        description="Priority: low, medium, high",
                        required=False,
                        default="medium",
                        enum=["low", "medium", "high"],
                    ),
                ],
                category="governance",
            ),
            self._tool_owner_notify,
        )

        self.register(
            ToolDefinition(
                name="company_profile_read",
                description="Read the company profile configuration",
                parameters=[],
                category="roles",
            ),
            self._tool_company_profile_read,
        )

        self.register(
            ToolDefinition(
                name="memory_read",
                description="Read agent memories (read-only access)",
                parameters=[
                    ToolParameter(name="agent_id", description="Agent ID to read memories for"),
                    ToolParameter(
                        name="limit",
                        type="int",
                        description="Max results",
                        required=False,
                        default=20,
                    ),
                ],
                category="memory",
            ),
            self._tool_memory_read,
        )

        self._register_manifest_parity_tools()

    def _register_manifest_parity_tools(self) -> None:
        filters_param = ToolParameter(
            name="filters",
            type="dict",
            description="Optional filters",
            required=False,
            default={},
        )
        limit_param = ToolParameter(
            name="limit",
            type="int",
            description="Maximum results",
            required=False,
            default=20,
        )
        query_param = ToolParameter(
            name="query",
            description="Search or analysis query",
            required=False,
            default="",
        )
        topic_param = ToolParameter(
            name="topic",
            description="Topic or objective",
            required=False,
            default="general",
        )
        context_param = ToolParameter(
            name="context",
            type="dict",
            description="Additional structured context",
            required=False,
            default={},
        )
        fields_param = ToolParameter(
            name="fields",
            type="list",
            description="Optional ERPNext fields to return",
            required=False,
            default=[],
        )
        content_param = ToolParameter(
            name="content",
            description="Content or notes",
            required=False,
            default="",
        )
        updates_param = ToolParameter(
            name="updates",
            type="dict",
            description="Proposed updates",
            required=False,
            default={},
        )
        contact_id_param = ToolParameter(
            name="contact_id",
            description="ERPNext Contact identifier",
        )
        opportunity_id_param = ToolParameter(
            name="opportunity_id",
            description="ERPNext Opportunity identifier",
        )
        task_id_param = ToolParameter(
            name="task_id",
            description="ERPNext Task identifier",
        )
        issue_id_param = ToolParameter(
            name="issue_id",
            description="ERPNext Issue identifier",
        )
        task_data_param = ToolParameter(
            name="task_data",
            type="dict",
            description="ERPNext Task fields, including at least subject",
        )
        issue_data_param = ToolParameter(
            name="issue_data",
            type="dict",
            description="ERPNext Issue fields, including at least subject",
        )
        material_request_data_param = ToolParameter(
            name="request_data",
            type="dict",
            description=(
                "ERPNext Material Request fields with items containing item_code and qty"
            ),
        )
        github_workflow_param = ToolParameter(
            name="workflow",
            description="GitHub Actions workflow file name or workflow id",
            required=False,
            default="",
        )
        github_ref_param = ToolParameter(
            name="ref",
            description="Git ref to run the workflow on",
            required=False,
            default="",
        )
        github_repository_param = ToolParameter(
            name="repository",
            description="GitHub owner/repo override",
            required=False,
            default="",
        )
        github_inputs_param = ToolParameter(
            name="inputs",
            type="dict",
            description="Optional workflow_dispatch inputs",
            required=False,
            default={},
        )

        self._register_manifest_tool(
            "erpnext_invoice_create",
            "Create a Sales Invoice in ERPNext",
            "erpnext",
            self._tool_erpnext_create_invoice,
            [ToolParameter(name="invoice_data", type="dict", description="Invoice data")],
            requires_approval=True,
            risk_level="high",
            side_effects=True,
            requires_configuration=True,
        )
        self._register_manifest_tool(
            "erpnext_expense_track",
            "Read expense claims from ERPNext",
            "erpnext",
            self._tool_erpnext_get_expenses,
            [filters_param],
        )
        self._register_manifest_tool(
            "erpnext_hr_read",
            "Read employee records from ERPNext",
            "erpnext",
            self._tool_erpnext_get_employees,
            [filters_param],
        )
        self._register_manifest_tool(
            "crm_lead_search",
            "Search leads in ERPNext CRM",
            "erpnext",
            self._tool_erpnext_get_leads,
            [filters_param],
        )

        for name, channel in {
            "email_read": "email",
            "sms_read": "sms",
            "call_receive": "voice",
            "message_read": "message",
        }.items():
            self._register_manifest_tool(
                name,
                f"Read recent {channel} communication logs",
                "communications",
                self._make_comm_log_reader(channel),
                [limit_param],
            )

        self._register_manifest_tool(
            "document_index",
            "Index a document into semantic memory",
            "memory",
            self._tool_document_index,
            [
                ToolParameter(
                    name="title",
                    description="Document title",
                    required=False,
                    default="Untitled document",
                ),
                content_param,
                ToolParameter(
                    name="namespace",
                    description="Memory namespace",
                    required=False,
                    default="knowledge",
                ),
            ],
            risk_level="medium",
        )
        self._register_manifest_tool(
            "knowledge_query",
            "Query semantic memory for knowledge",
            "memory",
            self._tool_knowledge_query,
            [
                query_param,
                ToolParameter(
                    name="namespace",
                    description="Memory namespace",
                    required=False,
                    default="knowledge",
                ),
                limit_param,
            ],
        )

        generic_tools = {
            "access_audit": ("security", "Produce an access audit summary", "medium"),
            "analytics_read": ("marketing", "Read marketing analytics summary", "low"),
            "brand_monitor": ("marketing", "Summarize brand monitoring signals", "low"),
            "browser_automate": ("engineering", "Plan browser automation steps", "medium"),
            "candidate_screen": ("hr", "Draft a candidate screening summary", "medium"),
            "cashflow_forecast": ("finance", "Draft a cash-flow forecast", "medium"),
            "compliance_check": ("governance", "Run a compliance checklist", "medium"),
            "content_create": ("marketing", "Draft marketing content", "medium"),
            "git_commit_draft": ("engineering", "Draft a git commit summary", "medium"),
            "git_read": ("engineering", "Summarize repository read request", "low"),
            "incident_report": ("security", "Draft a security incident report", "medium"),
            "job_posting_draft": ("hr", "Draft a job posting", "medium"),
            "nda_draft": ("legal", "Draft an NDA outline", "medium"),
            "onboarding_checklist": ("hr", "Draft an onboarding checklist", "low"),
            "process_audit": ("operations", "Produce a process audit summary", "medium"),
            "progress_report": ("product", "Draft a progress report", "low"),
            "regulation_search": ("legal", "Summarize regulatory research", "low"),
            "research_report": ("knowledge", "Draft a research report", "low"),
            "security_scan": ("security", "Prepare a security scan summary", "medium"),
            "sla_monitor": ("operations", "Summarize SLA monitoring status", "low"),
            "social_post_draft": ("marketing", "Draft a social media post", "medium"),
            "sprint_plan": ("product", "Draft a sprint plan", "low"),
            "test_run": ("engineering", "Plan or summarize a test run", "medium"),
            "ticket_read": ("support", "Summarize a support ticket read request", "low"),
            "vendor_search": ("operations", "Summarize vendor research", "low"),
            "web_search": ("knowledge", "Summarize a web research request", "low"),
        }
        external_mutation_tools = set()
        for name, (category, description, risk_level) in generic_tools.items():
            requires_approval = risk_level == "high"
            side_effects = name in external_mutation_tools
            executor_kind = "unavailable" if side_effects else "advisory"
            self._register_manifest_tool(
                name,
                description,
                category,
                self._make_manifest_advisory_executor(name, description),
                [topic_param, query_param, context_param, content_param],
                requires_approval=requires_approval,
                risk_level=risk_level,
                side_effects=side_effects,
                executor_kind=executor_kind,
                requires_configuration=side_effects,
                readiness_reason=(
                    "No live executor is registered for this side-effectful business tool."
                    if side_effects
                    else "Advisory drafting/inspection tool; it cannot mutate external systems."
                ),
            )

        erpnext_business_tools = [
            (
                "crm_contact_update",
                "Update an ERPNext Contact record",
                self._tool_crm_contact_update,
                [contact_id_param, updates_param],
                "high",
            ),
            (
                "crm_deal_update",
                "Update an ERPNext Opportunity record",
                self._tool_crm_deal_update,
                [opportunity_id_param, updates_param],
                "high",
            ),
            (
                "task_create",
                "Create an ERPNext Task record",
                self._tool_task_create,
                [task_data_param],
                "medium",
            ),
            (
                "task_update",
                "Update an ERPNext Task record",
                self._tool_task_update,
                [task_id_param, updates_param],
                "medium",
            ),
            (
                "ticket_create",
                "Create an ERPNext Issue record",
                self._tool_ticket_create,
                [issue_data_param],
                "medium",
            ),
            (
                "ticket_update",
                "Update an ERPNext Issue record",
                self._tool_ticket_update,
                [issue_id_param, updates_param],
                "medium",
            ),
            (
                "procurement_request",
                "Create an ERPNext Material Request",
                self._tool_procurement_request,
                [material_request_data_param],
                "high",
            ),
        ]
        for name, description, executor, parameters, risk_level in erpnext_business_tools:
            self._register_manifest_tool(
                name,
                description,
                "erpnext",
                executor,
                parameters,
                requires_approval=True,
                risk_level=risk_level,
                side_effects=True,
                requires_configuration=True,
            )

        self._register_manifest_tool(
            "ci_trigger",
            "Trigger a configured GitHub Actions workflow_dispatch run",
            "engineering",
            self._tool_ci_trigger,
            [
                github_workflow_param,
                github_ref_param,
                github_repository_param,
                github_inputs_param,
            ],
            requires_approval=True,
            risk_level="high",
            side_effects=True,
            requires_configuration=True,
        )

        # Real legal tool registrations
        self._register_manifest_tool(
            "contract_draft",
            "Draft a professional contract document with custom provisions and details",
            "legal",
            self._tool_contract_draft,
            [topic_param, query_param, context_param, content_param],
            requires_approval=False,
            risk_level="medium",
        )
        self._register_manifest_tool(
            "policy_draft",
            "Draft a comprehensive company policy or guideline document",
            "legal",
            self._tool_policy_draft,
            [topic_param, query_param, context_param, content_param],
            requires_approval=False,
            risk_level="medium",
        )

        insight_tools = {
            "access_audit": (
                "security",
                "Summarize recent access and authorization audit events",
                self._tool_access_audit,
                [limit_param, query_param],
            ),
            "analytics_read": (
                "marketing",
                "Read local communication and execution analytics",
                self._tool_analytics_read,
                [limit_param],
            ),
            "compliance_check": (
                "governance",
                "Evaluate local governance controls and recent compliance signals",
                self._tool_compliance_check,
                [topic_param, limit_param],
            ),
            "process_audit": (
                "operations",
                "Summarize recent operational process audit events",
                self._tool_process_audit,
                [limit_param, query_param],
            ),
            "progress_report": (
                "product",
                "Generate a local progress report from agents and recent events",
                self._tool_progress_report,
                [topic_param, limit_param],
            ),
            "security_scan": (
                "security",
                "Inspect local agent and tool security posture",
                self._tool_security_scan,
                [limit_param],
            ),
            "sla_monitor": (
                "operations",
                "Summarize local communication SLA signals",
                self._tool_sla_monitor,
                [limit_param],
            ),
        }
        for name, (category, description, executor, parameters) in insight_tools.items():
            self._register_manifest_tool(
                name,
                description,
                category,
                executor,
                parameters,
                risk_level="low",
            )

        erpnext_read_tools = {
            "ticket_read": (
                "support",
                "Search ERPNext support ticket records",
                "Issue",
                ["name", "subject", "status", "priority", "modified"],
            ),
            "vendor_search": (
                "operations",
                "Search ERPNext supplier records",
                "Supplier",
                ["name", "supplier_name", "supplier_type", "modified"],
            ),
            "erpnext_customer_search": (
                "erpnext",
                "Search ERPNext customer records",
                "Customer",
                ["name", "customer_name", "customer_type", "modified"],
            ),
            "erpnext_contact_search": (
                "erpnext",
                "Search ERPNext contact records",
                "Contact",
                ["name", "first_name", "last_name", "email_id", "modified"],
            ),
            "erpnext_project_search": (
                "erpnext",
                "Search ERPNext project records",
                "Project",
                ["name", "project_name", "status", "modified"],
            ),
            "erpnext_task_search": (
                "erpnext",
                "Search ERPNext task records",
                "Task",
                ["name", "subject", "status", "priority", "modified"],
            ),
            "erpnext_supplier_search": (
                "erpnext",
                "Search ERPNext supplier records",
                "Supplier",
                ["name", "supplier_name", "supplier_type", "modified"],
            ),
            "erpnext_issue_search": (
                "erpnext",
                "Search ERPNext issue records",
                "Issue",
                ["name", "subject", "status", "priority", "modified"],
            ),
        }
        for name, (category, description, doctype, default_fields) in erpnext_read_tools.items():
            self._register_manifest_tool(
                name,
                description,
                category,
                self._make_erpnext_search_reader(doctype, default_fields),
                [query_param, filters_param, fields_param, limit_param],
            )

        engineering_tools = {
            "git_read": (
                "Summarize local repository state without modifying files",
                self._tool_git_read,
                [query_param, limit_param],
                "low",
            ),
            "git_commit_draft": (
                "Draft a commit summary from current repository metadata",
                self._tool_git_commit_draft,
                [topic_param, query_param, limit_param],
                "medium",
            ),
            "test_run": (
                "Plan a test run from discovered project metadata",
                self._tool_test_run,
                [topic_param, query_param],
                "medium",
            ),
            "browser_automate": (
                "Plan browser automation steps without launching a browser",
                self._tool_browser_automate,
                [topic_param, query_param, context_param],
                "medium",
            ),
        }
        for name, (description, executor, parameters, risk_level) in engineering_tools.items():
            self._register_manifest_tool(
                name,
                description,
                "engineering",
                executor,
                parameters,
                risk_level=risk_level,
            )

        knowledge_tools = {
            "web_search": (
                "Search local knowledge and prepare an external web research plan",
                "knowledge",
                self._tool_web_search,
                [query_param, topic_param, context_param, limit_param],
            ),
            "research_report": (
                "Generate a local evidence-backed research report draft",
                "knowledge",
                self._tool_research_report,
                [topic_param, query_param, context_param, limit_param],
            ),
            "regulation_search": (
                "Search local regulatory knowledge and produce review checklist",
                "legal",
                self._tool_regulation_search,
                [topic_param, query_param, context_param, limit_param],
            ),
            "brand_monitor": (
                "Summarize local brand signals from knowledge and communications",
                "marketing",
                self._tool_brand_monitor,
                [topic_param, query_param, context_param, limit_param],
            ),
        }
        for name, (description, category, executor, parameters) in knowledge_tools.items():
            self._register_manifest_tool(
                name,
                description,
                category,
                executor,
                parameters,
                risk_level="low",
            )

    def _register_manifest_tool(
        self,
        name: str,
        description: str,
        category: str,
        executor: Callable,
        parameters: list[ToolParameter] | None = None,
        requires_approval: bool = False,
        risk_level: str = "low",
        side_effects: bool = False,
        executor_kind: str = "live",
        requires_configuration: bool = False,
        readiness_reason: str | None = None,
    ) -> None:
        self.register(
            ToolDefinition(
                name=name,
                description=description,
                parameters=parameters or [],
                category=category,
                requires_approval=requires_approval,
                risk_level=risk_level,
                side_effects=side_effects,
                executor_kind=executor_kind,
                requires_configuration=requires_configuration,
                readiness_reason=readiness_reason,
            ),
            executor,
        )

    # ─── Tool Executor Implementations ───────────────────────────────
    # These executors delegate to injected services or advisory local planners.
    # The services are injected at startup via set_services().

    _comms_gateway = None
    _memory_service = None
    _agent_manager = None
    _erpnext_client = None
    _audit = None
    _metrics = None

    def set_services(
        self,
        comms=None,
        memory=None,
        agent_manager=None,
        erpnext=None,
        audit=None,
        metrics=None,
    ):
        """Inject service instances for tool execution."""
        self._comms_gateway = comms
        self._memory_service = memory
        self._agent_manager = agent_manager
        self._erpnext_client = erpnext
        self._audit = audit
        self._metrics = metrics or getattr(audit, "_metrics", None)

    async def _approval_granted(self, approval_id: str | None, tool_name: str) -> bool:
        if not approval_id or not self._agent_manager:
            return False
        return await self._agent_manager.approval_is_executable(
            approval_id,
            target_type="tool",
            target_id=tool_name,
        )

    async def _tool_send_email(
        self,
        to_address: str,
        subject: str,
        body: str,
        cc: list = None,
        idempotency_key: str = None,
    ):
        if not self._comms_gateway:
            return "Communications gateway not available"
        data = type(
            "EmailReq",
            (),
            {
                "to_address": to_address,
                "subject": subject,
                "body": body,
                "agent_id": None,
                "cc": cc or [],
                "idempotency_key": idempotency_key,
            },
        )()
        return await self._comms_gateway.send_email(data)

    async def _tool_send_sms(
        self,
        to_number: str,
        message: str,
        idempotency_key: str = None,
    ):
        if not self._comms_gateway:
            return "Communications gateway not available"
        data = type(
            "SMSReq",
            (),
            {
                "to_number": to_number,
                "message": message,
                "agent_id": None,
                "from_number": None,
                "idempotency_key": idempotency_key,
            },
        )()
        return await self._comms_gateway.send_sms(data)

    async def _tool_make_call(
        self,
        to_number: str,
        context: str,
        idempotency_key: str = None,
    ):
        if not self._comms_gateway:
            return "Communications gateway not available"
        data = type(
            "CallReq",
            (),
            {
                "to_number": to_number,
                "context": context,
                "agent_id": None,
                "from_number": None,
                "idempotency_key": idempotency_key,
            },
        )()
        return await self._comms_gateway.make_call(data)

    async def _tool_send_message(
        self,
        platform: str,
        recipient: str,
        message: str,
        idempotency_key: str = None,
    ):
        if not self._comms_gateway:
            return "Communications gateway not available"
        data = type(
            "MsgReq",
            (),
            {
                "platform": platform,
                "recipient": recipient,
                "message": message,
                "agent_id": None,
                "idempotency_key": idempotency_key,
            },
        )()
        return await self._comms_gateway.send_message(data)

    async def _tool_memory_remember(
        self,
        content: str,
        memory_type: str = "episodic",
        namespace: str = "general",
        importance: float = 0.5,
    ):
        if not self._memory_service:
            return "Memory service not available"
        data = type(
            "MemW",
            (),
            {
                "agent_id": None,
                "memory_type": memory_type,
                "namespace": namespace,
                "content": content,
                "metadata": {},
                "importance": importance,
            },
        )()
        return await self._memory_service.remember(data)

    async def _tool_memory_recall(self, query: str, namespace: str = None, limit: int = 5):
        if not self._memory_service:
            return "Memory service not available"
        data = type(
            "MemQ",
            (),
            {
                "query": query,
                "namespace": namespace,
                "agent_id": None,
                "memory_type": None,
                "limit": limit,
            },
        )()
        return await self._memory_service.recall(data)

    async def _tool_erpnext_get_invoices(self, filters: dict = None):
        if not self._erpnext_client:
            return "ERPNext client not available"
        return await self._erpnext_client.get_invoices(filters)

    async def _tool_erpnext_create_lead(self, lead_data: dict):
        if not self._erpnext_client:
            return "ERPNext client not available"
        self._require_non_empty_dict(lead_data, "lead_data")
        self._require_non_empty_string(lead_data.get("lead_name"), "lead_data.lead_name")
        record = await self._erpnext_client.create_lead(lead_data)
        return self._erpnext_write_result("Lead", "created", record)

    async def _tool_erpnext_get_projects(self, filters: dict = None):
        if not self._erpnext_client:
            return "ERPNext client not available"
        return await self._erpnext_client.get_projects(filters)

    async def _tool_role_catalog_search(self, query: str):
        if not self._agent_manager:
            return "Agent manager not available"
        manifests = await self._agent_manager.list_role_manifests()
        query_lower = query.lower()
        return [
            m
            for m in manifests
            if query_lower in m["name"].lower()
            or query_lower in m["description"].lower()
        ]

    async def _tool_role_instantiate(self, manifest_id: str, overrides: dict = None):
        if not self._agent_manager:
            return "Agent manager not available"
        return await self._agent_manager.instantiate_role(manifest_id, overrides)

    async def _tool_role_gap_report(
        self,
        title: str,
        description: str,
        severity: str = "medium",
        capability: str = None,
        requested_tools: list = None,
        company_namespace: str = "company:default",
        context: dict = None,
        source_agent_id: str = None,
        source_type: str = "agent",
    ):
        if not self._agent_manager:
            return "Agent manager not available"
        data = type(
            "RoleGapReport",
            (),
            {
                "title": title,
                "description": description,
                "severity": severity,
                "source_agent_id": source_agent_id,
                "source_type": source_type,
                "company_namespace": company_namespace,
                "capability": capability,
                "requested_tools": requested_tools or [],
                "context": context or {},
            },
        )()
        return await self._agent_manager.report_role_gap(data, reporter=source_agent_id or "agent")

    async def _tool_agent_status_read(self):
        if not self._agent_manager:
            return "Agent manager not available"
        return await self._agent_manager.get_all_agent_status()

    async def _tool_agent_invoke(self, agent_id: str, task: str):
        if not self._agent_manager:
            return "Agent manager not available"
        return await self._agent_manager.invoke_agent(agent_id, task)

    async def _tool_approval_request(self, action_type: str, description: str):
        if not self._agent_manager:
            return "Agent manager not available"
        return await self._agent_manager._request_approval(None, action_type, description, {})

    async def _tool_approval_resolve(self, approval_id: str, decision: str):
        if not self._agent_manager:
            return "Agent manager not available"
        return await self._agent_manager.resolve_approval(approval_id, decision)

    async def _tool_owner_notify(self, message: str, priority: str = "medium"):
        logger.info(f"[OWNER NOTIFY] priority={priority}: {message}")
        return {"notified": True, "message": message, "priority": priority}

    async def _tool_company_profile_read(self):
        from cyber_team.config import settings
        return {
            "app_name": settings.app_name,
            "environment": settings.environment,
        }

    async def _tool_memory_read(self, agent_id: str, limit: int = 20):
        if not self._memory_service:
            return "Memory service not available"
        return await self._memory_service.get_agent_memory(agent_id)

    async def _tool_erpnext_create_invoice(self, invoice_data: dict):
        if not self._erpnext_client:
            return "ERPNext client not available"
        self._require_non_empty_dict(invoice_data, "invoice_data")
        record = await self._erpnext_client.create_invoice(invoice_data)
        return self._erpnext_write_result("Sales Invoice", "created", record)

    async def _tool_erpnext_get_expenses(self, filters: dict = None):
        if not self._erpnext_client:
            return "ERPNext client not available"
        return await self._erpnext_client.get_expenses(filters)

    async def _tool_erpnext_get_employees(self, filters: dict = None):
        if not self._erpnext_client:
            return "ERPNext client not available"
        return await self._erpnext_client.get_employees(filters)

    async def _tool_erpnext_get_leads(self, filters: dict = None):
        if not self._erpnext_client:
            return "ERPNext client not available"
        return await self._erpnext_client.get_leads(filters)

    async def _tool_crm_contact_update(self, contact_id: str, updates: dict):
        if not self._erpnext_client:
            return "ERPNext client not available"
        self._require_non_empty_string(contact_id, "contact_id")
        self._require_non_empty_dict(updates, "updates")
        record = await self._erpnext_client.update_contact(contact_id, updates)
        return self._erpnext_write_result("Contact", "updated", record)

    async def _tool_crm_deal_update(self, opportunity_id: str, updates: dict):
        if not self._erpnext_client:
            return "ERPNext client not available"
        self._require_non_empty_string(opportunity_id, "opportunity_id")
        self._require_non_empty_dict(updates, "updates")
        record = await self._erpnext_client.update_opportunity(opportunity_id, updates)
        return self._erpnext_write_result("Opportunity", "updated", record)

    async def _tool_task_create(self, task_data: dict):
        if not self._erpnext_client:
            return "ERPNext client not available"
        self._require_non_empty_dict(task_data, "task_data")
        self._require_non_empty_string(task_data.get("subject"), "task_data.subject")
        record = await self._erpnext_client.create_task(task_data)
        return self._erpnext_write_result("Task", "created", record)

    async def _tool_task_update(self, task_id: str, updates: dict):
        if not self._erpnext_client:
            return "ERPNext client not available"
        self._require_non_empty_string(task_id, "task_id")
        self._require_non_empty_dict(updates, "updates")
        record = await self._erpnext_client.update_task(task_id, updates)
        return self._erpnext_write_result("Task", "updated", record)

    async def _tool_ticket_create(self, issue_data: dict):
        if not self._erpnext_client:
            return "ERPNext client not available"
        self._require_non_empty_dict(issue_data, "issue_data")
        self._require_non_empty_string(issue_data.get("subject"), "issue_data.subject")
        record = await self._erpnext_client.create_issue(issue_data)
        return self._erpnext_write_result("Issue", "created", record)

    async def _tool_ticket_update(self, issue_id: str, updates: dict):
        if not self._erpnext_client:
            return "ERPNext client not available"
        self._require_non_empty_string(issue_id, "issue_id")
        self._require_non_empty_dict(updates, "updates")
        record = await self._erpnext_client.update_issue(issue_id, updates)
        return self._erpnext_write_result("Issue", "updated", record)

    async def _tool_procurement_request(self, request_data: dict):
        if not self._erpnext_client:
            return "ERPNext client not available"
        self._validate_material_request_data(request_data)
        record = await self._erpnext_client.create_material_request(request_data)
        return self._erpnext_write_result("Material Request", "created", record)

    @staticmethod
    def _require_non_empty_string(value: Any, field_name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} is required")

    @staticmethod
    def _require_non_empty_dict(value: Any, field_name: str) -> None:
        if not isinstance(value, dict) or not value:
            raise ValueError(f"{field_name} must be a non-empty object")

    def _validate_material_request_data(self, request_data: dict) -> None:
        self._require_non_empty_dict(request_data, "request_data")
        items = request_data.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("request_data.items must contain at least one item")
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"request_data.items[{index}] must be an object")
            self._require_non_empty_string(
                item.get("item_code"),
                f"request_data.items[{index}].item_code",
            )
            qty = item.get("qty")
            if not isinstance(qty, (int, float)) or qty <= 0:
                raise ValueError(f"request_data.items[{index}].qty must be greater than 0")

    @staticmethod
    def _erpnext_write_result(doctype: str, action: str, record: dict) -> dict[str, Any]:
        return {
            "status": "complete",
            "doctype": doctype,
            "action": action,
            "record": record,
            "record_id": record.get("name"),
            "side_effects": True,
        }

    def _make_erpnext_search_reader(
        self,
        doctype: str,
        default_fields: list[str],
    ) -> Callable:
        async def read_records(
            query: str = "",
            filters: dict = None,
            fields: list = None,
            limit: int = 20,
        ):
            if not self._erpnext_client:
                return self._erpnext_unavailable_result(doctype)
            search_filters = dict(filters or {})
            try:
                records = await self._erpnext_client.search(
                    doctype,
                    filters=search_filters,
                    fields=fields or default_fields,
                )
            except Exception as exc:
                return self._erpnext_unavailable_result(doctype, str(exc))
            filtered_records = self._filter_erpnext_records(records, query)
            return {
                "status": "complete",
                "doctype": doctype,
                "filters": search_filters,
                "query": query,
                "count": len(filtered_records[:limit]),
                "records": filtered_records[:limit],
                "side_effects": False,
            }

        return read_records

    def _make_comm_log_reader(self, channel: str) -> Callable:
        async def read_logs(limit: int = 20):
            if not self._comms_gateway:
                return "Communications gateway not available"
            return await self._comms_gateway.get_logs(channel=channel, limit=limit)

        return read_logs

    async def _tool_document_index(
        self,
        title: str = "Untitled document",
        content: str = "",
        namespace: str = "knowledge",
    ):
        if not self._memory_service:
            return "Memory service not available"
        indexed_content = f"{title}\n\n{content}".strip()
        data = type(
            "MemW",
            (),
            {
                "agent_id": None,
                "memory_type": "semantic",
                "namespace": namespace,
                "content": indexed_content,
                "metadata": {"title": title, "source": "document_index"},
                "importance": 0.7,
            },
        )()
        memory_result = await self._memory_service.remember(data)
        return {"memory_id": memory_result["id"], "title": title, "namespace": namespace}

    async def _tool_knowledge_query(
        self,
        query: str = "",
        namespace: str = "knowledge",
        limit: int = 20,
    ):
        if not self._memory_service:
            return "Memory service not available"
        data = type(
            "MemQ",
            (),
            {
                "query": query,
                "namespace": namespace,
                "agent_id": None,
                "memory_type": None,
                "limit": limit,
            },
        )()
        return await self._memory_service.recall(data)

    async def _tool_access_audit(self, limit: int = 20, query: str = ""):
        events = await self._list_audit_events(limit=limit, query=query)
        authz_events = [
            event for event in events if event["event_type"].startswith("authorization.")
        ]
        denied = [event for event in authz_events if event["outcome"] != "success"]
        return {
            "status": "complete",
            "events_reviewed": len(events),
            "authorization_events": len(authz_events),
            "denied_events": len(denied),
            "recent_denials": [self._audit_event_summary(event) for event in denied[:5]],
            "side_effects": False,
        }

    async def _tool_analytics_read(self, limit: int = 20):
        events = await self._list_audit_events(limit=limit)
        comms = await self._list_comm_logs(limit=limit)
        outcomes: dict[str, int] = {}
        event_types: dict[str, int] = {}
        channels: dict[str, int] = {}
        for event in events:
            outcomes[event["outcome"]] = outcomes.get(event["outcome"], 0) + 1
            event_type = event["event_type"]
            event_types[event_type] = event_types.get(event_type, 0) + 1
        for log in comms:
            channel = log["channel"]
            channels[channel] = channels.get(channel, 0) + 1
        return {
            "status": "complete",
            "audit_events_reviewed": len(events),
            "communication_logs_reviewed": len(comms),
            "event_types": event_types,
            "outcomes": outcomes,
            "communication_channels": channels,
            "side_effects": False,
        }

    async def _tool_compliance_check(self, topic: str = "general", limit: int = 20):
        events = await self._list_audit_events(limit=limit)
        approval_events = [
            event for event in events if event["event_type"].startswith("approval.")
        ]
        denied_events = [
            event for event in events if event["event_type"] == "authorization.denied"
        ]
        tool_events = [
            event for event in events if event["resource_type"] == "tool"
        ]
        return {
            "status": "complete",
            "topic": topic,
            "checks": {
                "approval_events_present": bool(approval_events),
                "authorization_denials_reviewed": len(denied_events),
                "tool_events_reviewed": len(tool_events),
            },
            "recommendations": [
                "Review denied authorization events before expanding privileges.",
                "Keep high-risk tools approval-gated.",
            ],
            "side_effects": False,
        }

    async def _tool_process_audit(self, limit: int = 20, query: str = ""):
        events = await self._list_audit_events(limit=limit, query=query)
        resource_counts: dict[str, int] = {}
        blocked = []
        for event in events:
            resource = event["resource_type"] or "unknown"
            resource_counts[resource] = resource_counts.get(resource, 0) + 1
            if event["outcome"] == "blocked":
                blocked.append(event)
        return {
            "status": "complete",
            "events_reviewed": len(events),
            "resource_counts": resource_counts,
            "blocked_events": [
                self._audit_event_summary(event) for event in blocked[:5]
            ],
            "side_effects": False,
        }

    async def _tool_progress_report(self, topic: str = "general", limit: int = 20):
        agents = []
        if self._agent_manager:
            agents = await self._agent_manager.get_all_agent_status()
        events = await self._list_audit_events(limit=limit)
        active_agents = [agent for agent in agents if agent["status"] == "active"]
        return {
            "status": "complete",
            "topic": topic,
            "active_agents": len(active_agents),
            "total_agents": len(agents),
            "recent_events": len(events),
            "latest_events": [
                self._audit_event_summary(event) for event in events[:5]
            ],
            "side_effects": False,
        }

    async def _tool_security_scan(self, limit: int = 20):
        agents = []
        if self._agent_manager:
            agents = await self._agent_manager.list_agents()
        events = await self._list_audit_events(limit=limit)
        approval_gated_tools = [
            tool.name for tool in self.list_tools() if tool.requires_approval
        ]
        denied_events = [
            event for event in events if event["event_type"] == "authorization.denied"
        ]
        return {
            "status": "complete",
            "agents_reviewed": len(agents),
            "approval_gated_tools": sorted(approval_gated_tools),
            "authorization_denials": len(denied_events),
            "side_effects": False,
        }

    async def _tool_sla_monitor(self, limit: int = 20):
        comms = await self._list_comm_logs(limit=limit)
        statuses: dict[str, int] = {}
        channels: dict[str, int] = {}
        for log in comms:
            status = log["status"]
            channel = log["channel"]
            statuses[status] = statuses.get(status, 0) + 1
            channels[channel] = channels.get(channel, 0) + 1
        return {
            "status": "complete",
            "logs_reviewed": len(comms),
            "communication_statuses": statuses,
            "communication_channels": channels,
            "side_effects": False,
        }

    async def _tool_ci_trigger(
        self,
        workflow: str = "",
        ref: str = "",
        repository: str = "",
        inputs: dict = None,
    ):
        token = settings.github_token
        repo = repository.strip() or settings.github_repository
        workflow_name = workflow.strip() or settings.github_default_workflow
        git_ref = ref.strip() or settings.github_default_ref
        missing = []
        if not token:
            missing.append("GITHUB_TOKEN")
        if not repo:
            missing.append("GITHUB_REPOSITORY")
        if not workflow_name:
            missing.append("GITHUB_DEFAULT_WORKFLOW")
        if not git_ref:
            missing.append("GITHUB_DEFAULT_REF")
        if missing:
            raise RuntimeError(
                "GitHub workflow dispatch is not configured; missing "
                + ", ".join(missing)
            )
        if "/" not in repo:
            raise ValueError("repository must use owner/repo format")

        url = (
            f"https://api.github.com/repos/{repo}/actions/workflows/"
            f"{quote(workflow_name, safe='')}/dispatches"
        )
        payload = {"ref": git_ref, "inputs": inputs or {}}
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(url, json=payload, headers=headers)
        if response.status_code != 204:
            detail = response.text[:500] if response.text else response.reason_phrase
            raise RuntimeError(
                f"GitHub workflow dispatch failed with HTTP {response.status_code}: {detail}"
            )
        return {
            "status": "complete",
            "provider": "github_actions",
            "repository": repo,
            "workflow": workflow_name,
            "ref": git_ref,
            "inputs": payload["inputs"],
            "side_effects": True,
        }

    async def _tool_git_read(self, query: str = "", limit: int = 20):
        result = self._local_repo_summary(query=query, limit=limit)
        result["status"] = "complete"
        result["side_effects"] = False
        return result

    async def _tool_git_commit_draft(
        self,
        topic: str = "general",
        query: str = "",
        limit: int = 20,
    ):
        summary = self._local_repo_summary(query=query, limit=limit)
        status_lines = summary.get("status_lines", [])
        changed_paths = [line[3:] for line in status_lines if len(line) > 3]
        subject = topic if topic != "general" else "Update project implementation"
        bullets = [
            f"Review {len(changed_paths)} changed path(s).",
            "Run static checks before committing.",
            "Confirm no secrets or environment files are staged.",
        ]
        if changed_paths:
            bullets.extend(f"Include changes in {path}." for path in changed_paths[:5])
        return {
            "status": "complete",
            "repository_available": summary["repository_available"],
            "proposed_subject": subject,
            "query": query,
            "changed_paths": changed_paths[:limit],
            "recent_commits": summary.get("recent_commits", []),
            "draft_body_bullets": bullets,
            "executed": False,
            "side_effects": False,
        }

    async def _tool_test_run(self, topic: str = "general", query: str = ""):
        metadata = self._project_metadata_summary()
        commands = []
        if metadata["backend_available"]:
            backend_path = metadata["paths"].get("backend_source", "src")
            commands.append(f"python3 -m compileall -q {backend_path}")
        if metadata["frontend_available"]:
            commands.append("npm run lint")
            commands.append("npm run build")
        commands.extend(
            [
                "git diff --check",
                "docker compose config --quiet",
            ]
        )
        if query:
            commands.append(f"Run focused checks related to: {query}")
        return {
            "status": "complete",
            "topic": topic,
            "query": query,
            "metadata": metadata,
            "recommended_commands": commands,
            "executed": False,
            "side_effects": False,
        }

    async def _tool_browser_automate(
        self,
        topic: str = "general",
        query: str = "",
        context: dict = None,
    ):
        context = context or {}
        target = context.get("url") or context.get("target") or "application under test"
        steps = [
            {"step": 1, "action": "open_target", "target": target},
            {"step": 2, "action": "verify_page_loaded", "target": target},
            {"step": 3, "action": "perform_user_flow", "objective": query or topic},
            {"step": 4, "action": "capture_console_and_network_errors"},
            {"step": 5, "action": "summarize_findings_without_state_changes"},
        ]
        return {
            "status": "complete",
            "topic": topic,
            "query": query,
            "context": context,
            "automation_plan": steps,
            "launched_browser": False,
            "executed": False,
            "side_effects": False,
        }

    async def _tool_web_search(
        self,
        query: str = "",
        topic: str = "general",
        context: dict = None,
        limit: int = 20,
    ):
        context = context or {}
        search_query = query or topic
        evidence = await self._search_local_memories(
            search_query,
            namespace=context.get("namespace"),
            limit=limit,
        )
        return {
            "status": "complete",
            "mode": "local_knowledge_only",
            "topic": topic,
            "query": search_query,
            "local_results": evidence,
            "external_research_plan": self._research_plan(search_query, context),
            "external_requests": False,
            "side_effects": False,
        }

    async def _tool_research_report(
        self,
        topic: str = "general",
        query: str = "",
        context: dict = None,
        limit: int = 20,
    ):
        context = context or {}
        search_query = query or topic
        evidence = await self._search_local_memories(
            search_query,
            namespace=context.get("namespace"),
            limit=limit,
        )
        findings = self._evidence_findings(evidence)
        return {
            "status": "complete",
            "title": topic,
            "query": search_query,
            "summary": self._report_summary(topic, evidence),
            "key_findings": findings,
            "evidence_count": len(evidence),
            "evidence": evidence,
            "open_questions": self._open_research_questions(search_query, findings),
            "external_requests": False,
            "side_effects": False,
        }

    async def _tool_regulation_search(
        self,
        topic: str = "general",
        query: str = "",
        context: dict = None,
        limit: int = 20,
    ):
        context = context or {}
        jurisdiction = context.get("jurisdiction") or "unspecified"
        search_query = " ".join(
            part
            for part in [topic, query, jurisdiction, "regulation compliance policy"]
            if part
        )
        evidence = await self._search_local_memories(
            search_query,
            namespace=context.get("namespace"),
            limit=limit,
        )
        return {
            "status": "complete",
            "topic": topic,
            "query": query,
            "jurisdiction": jurisdiction,
            "local_results": evidence,
            "review_checklist": [
                "Confirm applicable jurisdiction and regulatory scope.",
                "Identify required disclosures, consent, and retention rules.",
                "Check approval gates before legal or policy commitments.",
                "Escalate material legal ambiguity to the human owner.",
            ],
            "external_requests": False,
            "side_effects": False,
        }

    async def _tool_brand_monitor(
        self,
        topic: str = "general",
        query: str = "",
        context: dict = None,
        limit: int = 20,
    ):
        context = context or {}
        search_query = query or topic
        evidence = await self._search_local_memories(
            search_query,
            namespace=context.get("namespace"),
            limit=limit,
        )
        comm_signals = await self._communication_signals(search_query, limit)
        return {
            "status": "complete",
            "topic": topic,
            "query": search_query,
            "knowledge_signals": evidence,
            "communication_signals": comm_signals,
            "recommendations": [
                "Review recent customer-facing communication for consistency.",
                "Escalate negative or ambiguous sentiment to marketing/owner review.",
                "Use external monitoring only after explicit approval/configuration.",
            ],
            "external_requests": False,
            "side_effects": False,
        }

    async def _list_audit_events(self, limit: int = 20, query: str = "") -> list[dict]:
        if not self._audit:
            return []
        events = await self._audit.list_events(limit=limit)
        if not query:
            return events
        query_lower = query.lower()
        return [
            event
            for event in events
            if query_lower in event["event_type"].lower()
            or query_lower in str(event.get("resource_type") or "").lower()
            or query_lower in str(event.get("action") or "").lower()
        ]

    async def _list_comm_logs(self, limit: int = 20) -> list[dict]:
        if not self._comms_gateway:
            return []
        return await self._comms_gateway.get_logs(limit=limit)

    async def _search_local_memories(
        self,
        query: str,
        namespace: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        limit = self._safe_limit(limit)
        query_text = (query or "").strip()
        async with async_session() as session:
            statement = select(MemoryEntry)
            if namespace:
                statement = statement.where(MemoryEntry.namespace == namespace)
            if query_text:
                statement = statement.where(MemoryEntry.content.ilike(f"%{query_text}%"))
            statement = statement.order_by(desc(MemoryEntry.importance)).limit(limit)
            rows = (await session.execute(statement)).scalars().all()
        return [self._memory_summary(row) for row in rows]

    async def _communication_signals(self, query: str, limit: int = 20) -> dict:
        logs = await self._list_comm_logs(limit=limit)
        query_lower = (query or "").lower()
        matching_logs = []
        channels: dict[str, int] = {}
        statuses: dict[str, int] = {}
        for log in logs:
            channels[log["channel"]] = channels.get(log["channel"], 0) + 1
            statuses[log["status"]] = statuses.get(log["status"], 0) + 1
            content = " ".join(
                str(log.get(key) or "")
                for key in ["recipient", "content", "status", "channel"]
            ).lower()
            if not query_lower or query_lower in content:
                matching_logs.append(
                    {
                        "channel": log.get("channel"),
                        "direction": log.get("direction"),
                        "status": log.get("status"),
                        "created_at": log.get("created_at"),
                    }
                )
        return {
            "logs_reviewed": len(logs),
            "matches": matching_logs[: self._safe_limit(limit)],
            "channels": channels,
            "statuses": statuses,
        }

    @staticmethod
    def _memory_summary(entry: MemoryEntry) -> dict:
        return {
            "id": entry.id,
            "namespace": entry.namespace,
            "memory_type": entry.memory_type,
            "content": entry.content[:500],
            "importance": entry.importance,
            "created_at": entry.created_at.isoformat(),
        }

    @staticmethod
    def _research_plan(query: str, context: dict) -> list[dict]:
        source_types = context.get("source_types") or [
            "official documentation",
            "regulatory or standards sources",
            "vendor/product documentation",
            "recent reputable analysis",
        ]
        return [
            {
                "step": index + 1,
                "objective": f"Search {source_type} for {query}",
                "requires_external_access": True,
            }
            for index, source_type in enumerate(source_types)
        ]

    @staticmethod
    def _evidence_findings(evidence: list[dict]) -> list[str]:
        if not evidence:
            return ["No matching local evidence was found."]
        findings = []
        for item in evidence[:5]:
            content = item.get("content", "").replace("\n", " ").strip()
            findings.append(content[:180] or f"Memory {item.get('id')} matched.")
        return findings

    @staticmethod
    def _report_summary(topic: str, evidence: list[dict]) -> str:
        if not evidence:
            return f"No local evidence is currently available for {topic}."
        return f"Found {len(evidence)} local evidence item(s) related to {topic}."

    @staticmethod
    def _open_research_questions(query: str, findings: list[str]) -> list[str]:
        if findings and findings[0] != "No matching local evidence was found.":
            return [
                f"What external sources can corroborate the local findings for {query}?",
                "Are there conflicting or more recent authoritative sources?",
            ]
        return [
            f"Which authoritative sources should be consulted for {query}?",
            "What internal documents or memories should be indexed next?",
        ]

    def _local_repo_summary(self, query: str = "", limit: int = 20) -> dict:
        limit = self._safe_limit(limit)
        repo_root = self._find_repo_root()
        metadata = self._project_metadata_summary()
        if not repo_root:
            return {
                "repository_available": False,
                "root": str(Path.cwd()),
                "query": query,
                "branch": None,
                "head": None,
                "status_lines": [],
                "recent_commits": [],
                "matching_paths": self._matching_project_paths(query, limit),
                "metadata": metadata,
            }
        branch = self._run_git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
        head = self._run_git(repo_root, ["rev-parse", "--short", "HEAD"])
        status_lines = self._run_git(repo_root, ["status", "--short"])
        recent_commits = self._run_git(
            repo_root,
            ["log", f"--max-count={limit}", "--pretty=format:%h %s"],
        )
        tracked_paths = self._run_git(repo_root, ["ls-files"])
        matching_paths = self._filter_path_list(tracked_paths, query, limit)
        return {
            "repository_available": True,
            "root": str(repo_root),
            "query": query,
            "branch": branch[0] if branch else None,
            "head": head[0] if head else None,
            "status_lines": status_lines[:limit],
            "recent_commits": recent_commits[:limit],
            "matching_paths": matching_paths,
            "metadata": metadata,
        }

    def _project_metadata_summary(self) -> dict:
        repo_root = self._find_repo_root()
        app_root = Path.cwd()
        backend_root = repo_root / "backend" if repo_root else app_root
        frontend_root = repo_root / "frontend" if repo_root else app_root / "frontend"
        backend_source = backend_root / "src"
        paths = {
            "app_root": str(app_root),
            "repo_root": str(repo_root) if repo_root else None,
            "backend_source": str(backend_source)
            if backend_source.exists()
            else "src",
            "frontend": str(frontend_root) if frontend_root.exists() else None,
        }
        files = {
            "backend_pyproject": self._path_exists(backend_root / "pyproject.toml"),
            "backend_requirements": self._path_exists(backend_root / "requirements.txt"),
            "app_requirements": self._path_exists(app_root / "requirements.txt"),
            "frontend_package": self._path_exists(frontend_root / "package.json"),
            "docker_compose": self._path_exists(
                (repo_root or app_root) / "docker-compose.yml"
            ),
        }
        return {
            "backend_available": bool(
                files["backend_pyproject"]
                or files["backend_requirements"]
                or files["app_requirements"]
            ),
            "frontend_available": files["frontend_package"],
            "docker_compose_available": files["docker_compose"],
            "paths": paths,
            "files": files,
        }

    @staticmethod
    def _find_repo_root() -> Path | None:
        candidates = [Path.cwd(), *Path(__file__).resolve().parents]
        for candidate in candidates:
            for current in [candidate, *candidate.parents]:
                if (current / ".git").exists():
                    return current
        return None

    @staticmethod
    def _run_git(repo_root: Path, args: list[str]) -> list[str]:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if completed.returncode != 0:
            return []
        return [line for line in completed.stdout.splitlines() if line]

    def _matching_project_paths(self, query: str = "", limit: int = 20) -> list[str]:
        limit = self._safe_limit(limit)
        roots = [Path.cwd(), Path(__file__).resolve().parents[2]]
        seen: set[str] = set()
        paths = []
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if len(paths) >= limit:
                    return paths
                if not path.is_file() or self._skip_path(path):
                    continue
                path_text = str(path)
                if path_text in seen:
                    continue
                if query and query.lower() not in path_text.lower():
                    continue
                seen.add(path_text)
                paths.append(path_text)
        return paths

    @staticmethod
    def _filter_path_list(paths: list[str], query: str, limit: int) -> list[str]:
        if query:
            paths = [path for path in paths if query.lower() in path.lower()]
        return paths[:limit]

    @staticmethod
    def _path_exists(path: Path) -> bool:
        return path.exists()

    @staticmethod
    def _skip_path(path: Path) -> bool:
        ignored = {".git", "__pycache__", "node_modules", ".pytest_cache"}
        return any(part in ignored for part in path.parts)

    @staticmethod
    def _safe_limit(limit: int) -> int:
        return max(1, min(limit, 100))

    @staticmethod
    def _erpnext_unavailable_result(doctype: str, error: str = "") -> dict:
        result = {
            "status": "unavailable",
            "doctype": doctype,
            "count": 0,
            "records": [],
            "side_effects": False,
        }
        if error:
            result["error"] = error
        return result

    @staticmethod
    def _filter_erpnext_records(records: list[dict], query: str = "") -> list[dict]:
        if not query:
            return records
        query_lower = query.lower()
        return [
            record
            for record in records
            if query_lower in " ".join(str(value).lower() for value in record.values())
        ]

    @staticmethod
    def _audit_event_summary(event: dict) -> dict:
        return {
            "event_type": event.get("event_type"),
            "resource_type": event.get("resource_type"),
            "action": event.get("action"),
            "outcome": event.get("outcome"),
            "created_at": event.get("created_at"),
        }

    def _make_manifest_advisory_executor(self, tool_name: str, description: str) -> Callable:
        async def execute_advisory(**params):
            return {
                "tool": tool_name,
                "status": "advisory",
                "description": description,
                "inputs": params,
                "executor_kind": "advisory",
                "readiness_reason": (
                    "This tool can draft or inspect only; it has no external executor."
                ),
                "side_effects": False,
            }

        return execute_advisory

    async def _tool_contract_draft(
        self,
        topic: str = "",
        query: str = "",
        context: dict = None,
        content: str = "",
    ) -> dict:
        import os
        import uuid
        from pathlib import Path

        if ".." in topic or "/" in topic or "\\" in topic:
            raise ValueError("Path traversal detected")

        base_dir = Path(settings.data_dir).resolve()
        sub_dir = (base_dir / "contracts").resolve()
        sub_dir.mkdir(parents=True, exist_ok=True)

        safe_topic = "".join(c for c in topic if c.isalnum() or c in (" ", "-", "_")).strip()
        safe_topic = safe_topic.replace(" ", "_")[:30] or "contract"
        generated_at = utc_now()
        timestamp = generated_at.strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_topic}_{timestamp}_{uuid.uuid4().hex[:6]}.md"

        target_path = (sub_dir / filename).resolve()

        # Enforce trail boundary prefix checks for sandbox boundary
        if (
            not str(target_path).startswith(str(sub_dir) + os.sep)
            and target_path.parent != sub_dir
        ):
            raise ValueError("Path traversal detected")

        # Handle structured dictionary context securely
        if isinstance(context, dict):
            context_str = (
                context.get("description")
                or context.get("notes")
                or context.get("details")
                or str(context)
            )
        else:
            context_str = str(context or "")

        doc_content = (
            f"""# CONTRACT DRAFT: {topic or 'General Service Agreement'}
Generated on: {generated_at.isoformat()}

## 1. Context & Purpose
{context_str or 'No additional context provided.'}

## 2. Core Clauses & Scope of Work
{content or query or 'Default startup service outline.'}

## 3. Standard Boilerplate & General Provisions
"""
            "- **Governing Law**: This contract shall be governed and interpreted under the "
            "laws of the jurisdiction of the Company's primary registration.\n"
            "- **Severability**: If any provision of this contract is found invalid or "
            "unenforceable, the remaining provisions will continue to be in full force and "
            "effect.\n"
            """
- **Entire Agreement**: This document constitutes the entire agreement between the parties.
"""
        )

        with open(target_path, "w", encoding="utf-8") as f:
            f.write(doc_content)

        return {
            "status": "completed",
            "file_path": str(target_path),
            "file_size": target_path.stat().st_size,
            "topic": topic,
            "preview": doc_content[:300] + "...",
            "side_effects": True,
        }

    async def _tool_policy_draft(
        self,
        topic: str = "",
        query: str = "",
        context: dict = None,
        content: str = "",
    ) -> dict:
        import os
        import uuid
        from pathlib import Path

        if ".." in topic or "/" in topic or "\\" in topic:
            raise ValueError("Path traversal detected")

        base_dir = Path(settings.data_dir).resolve()
        sub_dir = (base_dir / "policies").resolve()
        sub_dir.mkdir(parents=True, exist_ok=True)

        safe_topic = "".join(c for c in topic if c.isalnum() or c in (" ", "-", "_")).strip()
        safe_topic = safe_topic.replace(" ", "_")[:30] or "policy"
        generated_at = utc_now()
        timestamp = generated_at.strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_topic}_{timestamp}_{uuid.uuid4().hex[:6]}.md"

        target_path = (sub_dir / filename).resolve()

        # Enforce trail boundary prefix checks for sandbox boundary
        if (
            not str(target_path).startswith(str(sub_dir) + os.sep)
            and target_path.parent != sub_dir
        ):
            raise ValueError("Path traversal detected")

        # Handle structured dictionary context securely
        if isinstance(context, dict):
            context_str = (
                context.get("description")
                or context.get("notes")
                or context.get("details")
                or str(context)
            )
        else:
            context_str = str(context or "")

        doc_content = (
            f"""# COMPANY POLICY: {topic or 'Acceptable Use Policy'}
Generated on: {generated_at.isoformat()}

## 1. Objective & Scope
{context_str or 'No additional context provided.'}

## 2. Guidelines & Compliance Criteria
{content or query or 'Default startup policy guidelines.'}

## 3. Enforcement & Revisions
- **Violations**: Failure to adhere to this policy may result in disciplinary action.
"""
            "- **Review Period**: This policy is subject to annual review and updates as "
            "operational requirements dictate.\n"
        )

        with open(target_path, "w", encoding="utf-8") as f:
            f.write(doc_content)

        return {
            "status": "completed",
            "file_path": str(target_path),
            "file_size": target_path.stat().st_size,
            "topic": topic,
            "preview": doc_content[:300] + "...",
            "side_effects": True,
        }
