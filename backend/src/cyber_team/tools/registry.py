"""Tool Registry — executable tool definitions for agent use.

Each tool has a name, description, parameter schema, and an async execute function.
Agents reference tools by name; the registry resolves and executes them.
"""

import logging
from copy import deepcopy
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ToolParameter(BaseModel):
    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    default: Any = None
    enum: Optional[list[Any]] = None

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
            "parameters": [parameter.model_dump() for parameter in self.parameters],
            "input_schema": self.input_schema(),
            "output_schema": self.output_schema,
        }


class ToolResult(BaseModel):
    success: bool
    output: Any = None
    error: Optional[str] = None


class ToolRegistry:
    """Central registry for executable tools that agents can invoke."""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        self._executors: dict[str, Callable] = {}
        self._register_builtin_tools()

    def register(self, tool: ToolDefinition, executor: Callable) -> None:
        """Register a tool with its executor function."""
        self._tools[tool.name] = tool
        self._executors[tool.name] = executor
        logger.debug(f"Registered tool: {tool.name}")

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def list_tools(self, category: Optional[str] = None) -> list[ToolDefinition]:
        tools = list(self._tools.values())
        if category:
            tools = [t for t in tools if t.category == category]
        return tools

    def list_tool_contracts(
        self,
        category: Optional[str] = None,
        allowed_tools: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        allowed = set(allowed_tools) if allowed_tools is not None else None
        return [
            tool.contract()
            for tool in self.list_tools(category)
            if allowed is None or tool.name in allowed
        ]

    def validate_params(
        self,
        tool_name: str,
        params: dict | None,
    ) -> tuple[bool, dict, Optional[str]]:
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
        if params is None:
            params = {}

        agent_id = params.pop("_agent_id", None)
        approval_id = params.pop("_approval_id", None)
        actor = params.pop("_actor", agent_id or "owner")
        actor_type = params.pop("_actor_type", "agent" if agent_id else "user")

        if tool_name not in self._tools:
            if self._audit:
                await self._audit.record(
                    event_type="tool.execute",
                    actor=actor,
                    actor_type=actor_type,
                    resource_type="tool",
                    resource_id=tool_name,
                    action="execute",
                    outcome="failed",
                    metadata={"error": "tool_not_found"},
                )
            return ToolResult(success=False, error=f"Tool not found: {tool_name}")

        tool = self._tools[tool_name]
        executor = self._executors[tool_name]
        valid, params, validation_error = self.validate_params(tool_name, params)
        if not valid:
            if self._audit:
                await self._audit.record(
                    event_type="tool.execute",
                    actor=actor,
                    actor_type=actor_type,
                    resource_type="tool",
                    resource_id=tool_name,
                    action="execute",
                    outcome="failed",
                    metadata={"error": validation_error},
                )
            return ToolResult(success=False, error=validation_error)

        if tool.requires_approval and not await self._approval_granted(approval_id, tool_name):
            requested_id = None
            if self._agent_manager:
                requested_id = await self._agent_manager._request_approval(
                    agent_id,
                    f"tool:{tool_name}",
                    f"Execute tool {tool_name}",
                    params,
                    requester=agent_id or actor,
                    requester_type="agent" if agent_id else actor_type,
                    risk_level="high",
                    target_type="tool",
                    target_id=tool_name,
                )
            if self._audit:
                await self._audit.record(
                    event_type="tool.approval_required",
                    actor=actor,
                    actor_type=actor_type,
                    resource_type="tool",
                    resource_id=tool_name,
                    action="execute",
                    outcome="blocked",
                    metadata={"approval_id": requested_id},
                )
            return ToolResult(
                success=False,
                output={
                    "approval_required": True,
                    "approval_id": requested_id,
                    "tool_name": tool_name,
                },
                error="Approval required before executing this tool",
            )

        try:
            if approval_id and tool.requires_approval and self._agent_manager:
                await self._agent_manager.consume_approval(
                    approval_id,
                    consumer=f"tool:{tool_name}",
                    target_type="tool",
                    target_id=tool_name,
                )
            result = await executor(**params)
            if self._audit:
                await self._audit.record(
                    event_type="tool.execute",
                    actor=actor,
                    actor_type=actor_type,
                    resource_type="tool",
                    resource_id=tool_name,
                    action="execute",
                    outcome="success",
                    metadata={
                        "approval_id": approval_id,
                        "requires_approval": tool.requires_approval,
                    },
                )
            return ToolResult(success=True, output=result)
        except Exception as e:
            logger.error(f"Tool execution failed [{tool_name}]: {e}")
            if self._audit:
                await self._audit.record(
                    event_type="tool.execute",
                    actor=actor,
                    actor_type=actor_type,
                    resource_type="tool",
                    resource_id=tool_name,
                    action="execute",
                    outcome="failed",
                    metadata={"approval_id": approval_id, "error": str(e)},
                )
            return ToolResult(success=False, error=str(e))

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
                ],
                category="communications",
                requires_approval=True,
                risk_level="high",
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
                ],
                category="communications",
                requires_approval=True,
                risk_level="high",
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
                ],
                category="communications",
                requires_approval=True,
                risk_level="high",
            ),
            self._tool_make_call,
        )

        self.register(
            ToolDefinition(
                name="send_message",
                description="Send a message via Telegram, WhatsApp, or Slack",
                parameters=[
                    ToolParameter(
                        name="platform",
                        description="Platform: telegram, whatsapp, slack",
                        enum=["telegram", "whatsapp", "slack"],
                    ),
                    ToolParameter(name="recipient", description="Recipient identifier"),
                    ToolParameter(name="message", description="Message text"),
                ],
                category="communications",
                requires_approval=True,
                risk_level="high",
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
            ),
            self._tool_erpnext_create_lead,
        )

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
            ),
            self._tool_role_instantiate,
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
        content_param = ToolParameter(
            name="content",
            description="Content or notes",
            required=False,
            default="",
        )
        entity_id_param = ToolParameter(
            name="entity_id",
            description="Target entity identifier",
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

        self._register_manifest_tool(
            "erpnext_invoice_create",
            "Create a Sales Invoice in ERPNext",
            "erpnext",
            self._tool_erpnext_create_invoice,
            [ToolParameter(name="invoice_data", type="dict", description="Invoice data")],
            requires_approval=True,
            risk_level="high",
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
            "ci_trigger": ("engineering", "Prepare a CI trigger request", "high"),
            "compliance_check": ("governance", "Run a compliance checklist", "medium"),
            "content_create": ("marketing", "Draft marketing content", "medium"),
            "contract_draft": ("legal", "Draft a contract outline", "medium"),
            "crm_contact_update": ("erpnext", "Prepare a CRM contact update", "high"),
            "crm_deal_update": ("erpnext", "Prepare a CRM deal update", "high"),
            "git_commit_draft": ("engineering", "Draft a git commit summary", "medium"),
            "git_read": ("engineering", "Summarize repository read request", "low"),
            "incident_report": ("security", "Draft a security incident report", "medium"),
            "job_posting_draft": ("hr", "Draft a job posting", "medium"),
            "nda_draft": ("legal", "Draft an NDA outline", "medium"),
            "onboarding_checklist": ("hr", "Draft an onboarding checklist", "low"),
            "policy_draft": ("legal", "Draft a policy outline", "medium"),
            "process_audit": ("operations", "Produce a process audit summary", "medium"),
            "procurement_request": ("operations", "Prepare a procurement request", "high"),
            "progress_report": ("product", "Draft a progress report", "low"),
            "regulation_search": ("legal", "Summarize regulatory research", "low"),
            "research_report": ("knowledge", "Draft a research report", "low"),
            "security_scan": ("security", "Prepare a security scan summary", "medium"),
            "sla_monitor": ("operations", "Summarize SLA monitoring status", "low"),
            "social_post_draft": ("marketing", "Draft a social media post", "medium"),
            "sprint_plan": ("product", "Draft a sprint plan", "low"),
            "task_create": ("product", "Prepare a task creation request", "medium"),
            "task_update": ("product", "Prepare a task update request", "medium"),
            "test_run": ("engineering", "Plan or summarize a test run", "medium"),
            "ticket_create": ("support", "Prepare a support ticket creation request", "medium"),
            "ticket_read": ("support", "Summarize a support ticket read request", "low"),
            "ticket_update": ("support", "Prepare a support ticket update request", "medium"),
            "vendor_search": ("operations", "Summarize vendor research", "low"),
            "web_search": ("knowledge", "Summarize a web research request", "low"),
        }
        for name, (category, description, risk_level) in generic_tools.items():
            requires_approval = risk_level == "high"
            self._register_manifest_tool(
                name,
                description,
                category,
                self._make_manifest_stub(name, description),
                [topic_param, query_param, context_param, content_param],
                requires_approval=requires_approval,
                risk_level=risk_level,
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

        for name in ["crm_contact_update", "crm_deal_update", "task_update"]:
            self._tools[name].parameters.extend([entity_id_param, updates_param])

    def _register_manifest_tool(
        self,
        name: str,
        description: str,
        category: str,
        executor: Callable,
        parameters: Optional[list[ToolParameter]] = None,
        requires_approval: bool = False,
        risk_level: str = "low",
    ) -> None:
        self.register(
            ToolDefinition(
                name=name,
                description=description,
                parameters=parameters or [],
                category=category,
                requires_approval=requires_approval,
                risk_level=risk_level,
            ),
            executor,
        )

    # ─── Tool Executor Implementations ───────────────────────────────
    # These are stub implementations that delegate to the actual services.
    # The services are injected at startup via set_services().

    _comms_gateway = None
    _memory_service = None
    _agent_manager = None
    _erpnext_client = None
    _audit = None

    def set_services(self, comms=None, memory=None, agent_manager=None, erpnext=None, audit=None):
        """Inject service instances for tool execution."""
        self._comms_gateway = comms
        self._memory_service = memory
        self._agent_manager = agent_manager
        self._erpnext_client = erpnext
        self._audit = audit

    async def _approval_granted(self, approval_id: Optional[str], tool_name: str) -> bool:
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
            },
        )()
        return await self._comms_gateway.send_email(data)

    async def _tool_send_sms(self, to_number: str, message: str):
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
            },
        )()
        return await self._comms_gateway.send_sms(data)

    async def _tool_make_call(self, to_number: str, context: str):
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
            },
        )()
        return await self._comms_gateway.make_call(data)

    async def _tool_send_message(self, platform: str, recipient: str, message: str):
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
        return await self._erpnext_client.create_lead(lead_data)

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
        return await self._erpnext_client.create_invoice(invoice_data)

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
        memory_id = await self._memory_service.remember(data)
        return {"memory_id": memory_id, "title": title, "namespace": namespace}

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

    @staticmethod
    def _audit_event_summary(event: dict) -> dict:
        return {
            "event_type": event.get("event_type"),
            "resource_type": event.get("resource_type"),
            "action": event.get("action"),
            "outcome": event.get("outcome"),
            "created_at": event.get("created_at"),
        }

    def _make_manifest_stub(self, tool_name: str, description: str) -> Callable:
        async def execute_stub(**params):
            return {
                "tool": tool_name,
                "status": "prepared",
                "description": description,
                "inputs": params,
                "side_effects": False,
            }

        return execute_stub
