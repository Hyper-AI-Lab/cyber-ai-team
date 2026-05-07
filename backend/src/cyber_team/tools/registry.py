"""Tool Registry — executable tool definitions for agent use.

Each tool has a name, description, parameter schema, and an async execute function.
Agents reference tools by name; the registry resolves and executes them.
"""

import logging
from typing import Any, Callable, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ToolParameter(BaseModel):
    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    default: Any = None


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: list[ToolParameter] = Field(default_factory=list)
    category: str = "general"
    requires_approval: bool = False


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

    async def execute(self, tool_name: str, params: dict = None) -> ToolResult:
        """Execute a tool by name with given parameters."""
        if params is None:
            params = {}

        if tool_name not in self._tools:
            return ToolResult(success=False, error=f"Tool not found: {tool_name}")

        tool = self._tools[tool_name]
        executor = self._executors[tool_name]
        agent_id = params.pop("_agent_id", None)
        approval_id = params.pop("_approval_id", None)

        if tool.requires_approval and not await self._approval_granted(approval_id):
            requested_id = None
            if self._agent_manager:
                requested_id = await self._agent_manager._request_approval(
                    agent_id,
                    f"tool:{tool_name}",
                    f"Execute tool {tool_name}",
                    params,
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
            result = await executor(**params)
            return ToolResult(success=True, output=result)
        except Exception as e:
            logger.error(f"Tool execution failed [{tool_name}]: {e}")
            return ToolResult(success=False, error=str(e))

    def get_tools_for_agent(self, tool_names: list[str]) -> list[ToolDefinition]:
        """Get tool definitions available to an agent based on its tool list."""
        return [self._tools[name] for name in tool_names if name in self._tools]

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
                    ToolParameter(name="cc", description="CC recipients", required=False, default=[]),
                ],
                category="communications",
                requires_approval=True,
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
            ),
            self._tool_make_call,
        )

        self.register(
            ToolDefinition(
                name="send_message",
                description="Send a message via Telegram, WhatsApp, or Slack",
                parameters=[
                    ToolParameter(name="platform", description="Platform: telegram, whatsapp, slack"),
                    ToolParameter(name="recipient", description="Recipient identifier"),
                    ToolParameter(name="message", description="Message text"),
                ],
                category="communications",
                requires_approval=True,
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
                    ToolParameter(name="memory_type", description="Type: episodic, semantic, procedural, entity"),
                    ToolParameter(name="namespace", description="Memory namespace"),
                    ToolParameter(name="importance", description="Importance 0-1", required=False, default=0.5),
                ],
                category="memory",
            ),
            self._tool_memory_remember,
        )

        self.register(
            ToolDefinition(
                name="memory_recall",
                description="Search and retrieve relevant memories",
                parameters=[
                    ToolParameter(name="query", description="Search query"),
                    ToolParameter(name="namespace", description="Filter by namespace", required=False),
                    ToolParameter(name="limit", description="Max results", required=False, default=5),
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
                    ToolParameter(name="filters", description="Filter dict", required=False),
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
                    ToolParameter(name="lead_data", description="Lead data dict"),
                ],
                category="erpnext",
                requires_approval=True,
            ),
            self._tool_erpnext_create_lead,
        )

        self.register(
            ToolDefinition(
                name="erpnext_get_projects",
                description="List projects from ERPNext",
                parameters=[
                    ToolParameter(name="filters", description="Filter dict", required=False),
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
                    ToolParameter(name="overrides", description="Override parameters", required=False),
                ],
                category="roles",
                requires_approval=True,
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
                    ToolParameter(name="decision", description="approved or rejected"),
                ],
                category="governance",
                requires_approval=True,
            ),
            self._tool_approval_resolve,
        )

        self.register(
            ToolDefinition(
                name="owner_notify",
                description="Send a notification to the human owner",
                parameters=[
                    ToolParameter(name="message", description="Notification message"),
                    ToolParameter(name="priority", description="Priority: low, medium, high", required=False, default="medium"),
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
                    ToolParameter(name="limit", description="Max results", required=False, default=20),
                ],
                category="memory",
            ),
            self._tool_memory_read,
        )

    # ─── Tool Executor Implementations ───────────────────────────────
    # These are stub implementations that delegate to the actual services.
    # The services are injected at startup via set_services().

    _comms_gateway = None
    _memory_service = None
    _agent_manager = None
    _erpnext_client = None

    def set_services(self, comms=None, memory=None, agent_manager=None, erpnext=None):
        """Inject service instances for tool execution."""
        self._comms_gateway = comms
        self._memory_service = memory
        self._agent_manager = agent_manager
        self._erpnext_client = erpnext

    async def _approval_granted(self, approval_id: Optional[str]) -> bool:
        if not approval_id or not self._agent_manager:
            return False
        queue = await self._agent_manager.get_approval_queue("approved")
        return any(item["id"] == approval_id for item in queue)

    async def _tool_send_email(self, to_address: str, subject: str, body: str, cc: list = None):
        if not self._comms_gateway:
            return "Communications gateway not available"
        data = type("EmailReq", (), {"to_address": to_address, "subject": subject, "body": body, "agent_id": None, "cc": cc or []})()
        return await self._comms_gateway.send_email(data)

    async def _tool_send_sms(self, to_number: str, message: str):
        if not self._comms_gateway:
            return "Communications gateway not available"
        data = type("SMSReq", (), {"to_number": to_number, "message": message, "agent_id": None, "from_number": None})()
        return await self._comms_gateway.send_sms(data)

    async def _tool_make_call(self, to_number: str, context: str):
        if not self._comms_gateway:
            return "Communications gateway not available"
        data = type("CallReq", (), {"to_number": to_number, "context": context, "agent_id": None, "from_number": None})()
        return await self._comms_gateway.make_call(data)

    async def _tool_send_message(self, platform: str, recipient: str, message: str):
        if not self._comms_gateway:
            return "Communications gateway not available"
        data = type("MsgReq", (), {"platform": platform, "recipient": recipient, "message": message, "agent_id": None})()
        return await self._comms_gateway.send_message(data)

    async def _tool_memory_remember(self, content: str, memory_type: str = "episodic", namespace: str = "general", importance: float = 0.5):
        if not self._memory_service:
            return "Memory service not available"
        data = type("MemW", (), {"agent_id": None, "memory_type": memory_type, "namespace": namespace, "content": content, "metadata": {}, "importance": importance})()
        return await self._memory_service.remember(data)

    async def _tool_memory_recall(self, query: str, namespace: str = None, limit: int = 5):
        if not self._memory_service:
            return "Memory service not available"
        data = type("MemQ", (), {"query": query, "namespace": namespace, "agent_id": None, "memory_type": None, "limit": limit})()
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
        return [m for m in manifests if query_lower in m["name"].lower() or query_lower in m["description"].lower()]

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
