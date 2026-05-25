"""Temporal worker for durable workflow execution."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import Worker

logger = logging.getLogger(__name__)

with workflow.unsafe.imports_passed_through():
    from cyber_team.agents.manager import AgentManager
    from cyber_team.config import settings
    from cyber_team.memory.service import MemoryService


@asynccontextmanager
async def activity_services():
    """Build the service graph activities need outside the FastAPI lifespan."""
    from cyber_team.audit.service import AuditService
    from cyber_team.comms.gateway import CommsGateway
    from cyber_team.integrations.erpnext import ERPNextClient
    from cyber_team.tools.registry import ToolRegistry

    audit = AuditService()
    memory = MemoryService()
    comms = CommsGateway()
    erpnext = ERPNextClient()
    registry = ToolRegistry()
    manager = AgentManager(
        memory_service=memory,
        audit_service=audit,
        tool_registry=registry,
    )
    registry.set_services(
        comms=comms,
        memory=memory,
        agent_manager=manager,
        erpnext=erpnext,
        audit=audit,
    )
    await memory.startup()
    try:
        yield {
            "audit": audit,
            "memory": memory,
            "comms": comms,
            "erpnext": erpnext,
            "registry": registry,
            "manager": manager,
        }
    finally:
        await memory.shutdown()
        await erpnext.close()


@activity.defn
async def invoke_agent_activity(agent_id: str, task: str) -> str:
    async with activity_services() as services:
        return await services["manager"].invoke_agent(agent_id, task)


@activity.defn
async def remember_activity(agent_id: str, memory_type: str, namespace: str, content: str) -> str:
    async with activity_services() as services:
        data = type("MemoryWrite", (), {
            "agent_id": agent_id,
            "memory_type": memory_type,
            "namespace": namespace,
            "content": content,
            "metadata": {},
            "importance": 0.5,
        })()
        result = await services["memory"].remember(data)
        return result["id"]


@activity.defn
async def request_approval_activity(
    agent_id: str,
    action_type: str,
    description: str,
    state: dict,
    risk_level: str,
    target_type: str,
    target_id: str,
) -> str:
    async with activity_services() as services:
        return await services["manager"]._request_approval(
            agent_id,
            action_type,
            description,
            state,
            requester="workflow",
            requester_type="system",
            risk_level=risk_level,
            target_type=target_type,
            target_id=target_id,
        )


@activity.defn
async def execute_tool_activity(tool_name: str, tool_args: dict) -> dict:
    async with activity_services() as services:
        result = await services["registry"].execute(tool_name, tool_args)
        return result.model_dump()


@activity.defn
async def consume_approval_activity(
    approval_id: str,
    consumer: str,
    target_type: str,
    target_id: str,
) -> None:
    async with activity_services() as services:
        await services["manager"].consume_approval(
            approval_id,
            consumer=consumer,
            target_type=target_type,
            target_id=target_id,
        )


@activity.defn
async def approval_is_executable_activity(
    approval_id: str,
    target_type: str | None = None,
    target_id: str | None = None,
) -> bool:
    async with activity_services() as services:
        return await services["manager"].approval_is_executable(
            approval_id,
            target_type=target_type,
            target_id=target_id,
        )


@activity.defn
async def update_workflow_run_db_activity(
    run_id: str,
    status: str,
    current_node: str,
    state: dict,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    from sqlalchemy import select

    from cyber_team.clock import utc_now
    from cyber_team.db import async_session
    from cyber_team.db.models import WorkflowRun

    async with async_session() as session:
        result_select = await session.execute(
            select(WorkflowRun).where(WorkflowRun.id == run_id)
        )
        run_obj = result_select.scalar_one()
        run_obj.status = status
        run_obj.current_node = current_node
        run_obj.state = state
        if result is not None:
            run_obj.result = result
        if error is not None:
            run_obj.error = error
        if status in ("completed", "failed", "rejected"):
            run_obj.completed_at = utc_now()
        await session.commit()


@workflow.defn
class CompanyOnboardingWorkflow:
    @workflow.run
    async def run(self, company_profile: dict) -> dict:
        result = await workflow.execute_activity(
            invoke_agent_activity,
            "company_builder",
            f"Set up company based on profile: {company_profile}",
            start_to_close_timeout=timedelta(minutes=5),
        )
        return {"onboarding_result": result}


@workflow.defn
class SalesOutreachWorkflow:
    @workflow.run
    async def run(self, lead_info: dict) -> dict:
        # Step 1: Research the lead
        research = await workflow.execute_activity(
            invoke_agent_activity,
            "sales_outreach",
            f"Research this lead: {lead_info}",
            start_to_close_timeout=timedelta(minutes=3),
        )
        # Step 2: Draft outreach message
        draft = await workflow.execute_activity(
            invoke_agent_activity,
            "sales_outreach",
            f"Draft outreach message based on research: {research}",
            start_to_close_timeout=timedelta(minutes=3),
        )
        # Step 3: Request approval before sending
        approval_id = await workflow.execute_activity(
            request_approval_activity,
            args=[
                "sales_outreach",
                "send_outreach",
                f"Send outreach to {lead_info.get('name', 'lead')}: {draft[:200]}",
                {},
                "medium",
                "workflow_run",
                "sales-outreach-id",
            ],
            start_to_close_timeout=timedelta(minutes=1),
        )
        return {"research": research, "draft": draft, "approval_id": approval_id}


@workflow.defn
class CustomerSupportWorkflow:
    @workflow.run
    async def run(self, ticket: dict) -> dict:
        # Step 1: Classify and research
        classification = await workflow.execute_activity(
            invoke_agent_activity,
            "customer_support",
            f"Classify and research this support ticket: {ticket}",
            start_to_close_timeout=timedelta(minutes=3),
        )
        # Step 2: Generate response
        response = await workflow.execute_activity(
            invoke_agent_activity,
            "customer_support",
            f"Generate response for ticket: {classification}",
            start_to_close_timeout=timedelta(minutes=3),
        )
        # Step 3: Store in memory
        memory_id = await workflow.execute_activity(
            remember_activity,
            "customer_support",
            "episodic",
            f"support:{ticket.get('customer_id', 'unknown')}",
            f"Ticket: {ticket}, Response: {response}",
            start_to_close_timeout=timedelta(minutes=1),
        )
        return {"classification": classification, "response": response, "memory_id": memory_id}


@workflow.defn
class DynamicGraphWorkflow:
    def __init__(self) -> None:
        self._approval_status: str | None = None

    @workflow.signal
    def approval_signal(self, status: str) -> None:
        self._approval_status = status

    @workflow.run
    async def run(self, graph: dict, input_data: dict, run_id: str) -> dict:
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        entry_node = graph.get("entry_node")

        if not nodes or not entry_node:
            raise ValueError("Invalid graph: missing nodes or entry_node")

        state = dict(input_data)
        current = entry_node
        node_map = {n["id"]: n for n in nodes}

        while current:
            node = node_map.get(current)
            if not node:
                break

            # Update DB run status to running
            await workflow.execute_activity(
                update_workflow_run_db_activity,
                args=[run_id, "running", current, state],
                start_to_close_timeout=timedelta(seconds=15),
            )

            node_type = node.get("type", "agent")

            if node_type == "agent":
                agent_id = node.get("agent_id")
                task = node.get("task_template", "").format(**state)
                result = await workflow.execute_activity(
                    invoke_agent_activity,
                    args=[agent_id, task],
                    start_to_close_timeout=timedelta(minutes=5),
                )
                state[f"{current}_output"] = result

            elif node_type == "tool":
                tool_name = node.get("tool_name")
                tool_args = node.get("args_template", {}).copy()
                for k, v in tool_args.items():
                    if isinstance(v, str):
                        try:
                            tool_args[k] = v.format(**state)
                        except KeyError:
                            pass

                # Check if there was a pending approval from a previous execution
                approval_id = state.get(f"{current}_approval_id")
                if approval_id:
                    tool_args["_approval_id"] = approval_id

                result_data = await workflow.execute_activity(
                    execute_tool_activity,
                    args=[tool_name, tool_args],
                    start_to_close_timeout=timedelta(minutes=3),
                )
                state[f"{current}_output"] = result_data

                output_val = result_data.get("output") or {}
                approval_id = output_val.get("approval_id")
                if output_val.get("approval_required") and not approval_id:
                    raise ValueError(
                        f"Tool {tool_name} requires approval, but no approval request was created"
                    )
                if approval_id:
                    state[f"{current}_approval_id"] = approval_id
                    state[f"{current}_tool_args"] = tool_args

                    # Update status to waiting_approval
                    await workflow.execute_activity(
                        update_workflow_run_db_activity,
                        args=[run_id, "waiting_approval", current, state],
                        start_to_close_timeout=timedelta(seconds=15),
                    )

                    # Pause workflow and wait for approval signal
                    self._approval_status = None
                    await workflow.wait_condition(lambda: self._approval_status is not None)

                    if self._approval_status == "rejected":
                        await workflow.execute_activity(
                            update_workflow_run_db_activity,
                            args=[run_id, "rejected", current, state],
                            start_to_close_timeout=timedelta(seconds=15),
                        )
                        return state

                    # If approved, execute the tool again with the approval id.
                    # ToolRegistry owns the single-use consumption step.
                    is_exec = await workflow.execute_activity(
                        approval_is_executable_activity,
                        args=[approval_id, "tool", tool_name],
                        start_to_close_timeout=timedelta(seconds=15),
                    )
                    if not is_exec:
                        raise ValueError(f"Approval request {approval_id} is not executable")

                    # Try execution again with the approval_id
                    tool_args["_approval_id"] = approval_id
                    result_data = await workflow.execute_activity(
                        execute_tool_activity,
                        args=[tool_name, tool_args],
                        start_to_close_timeout=timedelta(minutes=3),
                    )
                    state[f"{current}_output"] = result_data

            elif node_type == "decision":
                condition_key = node.get("condition_key")
                condition_value = state.get(condition_key)
                next_node = None
                for edge in edges:
                    if edge.get("from") == current:
                        if edge.get("condition") == condition_value or not edge.get("condition"):
                            next_node = edge.get("to")
                            break
                current = next_node
                continue

            elif node_type == "approval":
                agent_id = node.get("agent_id", "supervisor")
                description = node.get("description_template", "").format(**state)
                approval_id = await workflow.execute_activity(
                    request_approval_activity,
                    args=[
                        agent_id,
                        "workflow_step",
                        description,
                        state,
                        node.get("risk_level", "medium"),
                        "workflow_run",
                        run_id,
                    ],
                    start_to_close_timeout=timedelta(seconds=30),
                )
                state[f"{current}_approval_id"] = approval_id

                # Update DB run status to waiting_approval
                await workflow.execute_activity(
                    update_workflow_run_db_activity,
                    args=[run_id, "waiting_approval", current, state],
                    start_to_close_timeout=timedelta(seconds=15),
                )

                # Pause and wait for signal
                self._approval_status = None
                await workflow.wait_condition(lambda: self._approval_status is not None)

                if self._approval_status == "rejected":
                    await workflow.execute_activity(
                        update_workflow_run_db_activity,
                        args=[run_id, "rejected", current, state],
                        start_to_close_timeout=timedelta(seconds=15),
                    )
                    return state

                # Consume approval
                is_exec = await workflow.execute_activity(
                    approval_is_executable_activity,
                    args=[approval_id, "workflow_run", run_id],
                    start_to_close_timeout=timedelta(seconds=15),
                )
                if not is_exec:
                    raise ValueError(f"Approval request {approval_id} is not executable")

                await workflow.execute_activity(
                    consume_approval_activity,
                    args=[approval_id, "workflow", "workflow_run", run_id],
                    start_to_close_timeout=timedelta(seconds=15),
                )

            # Find next node
            next_nodes = [
                e.get("to")
                for e in edges
                if e.get("from") == current and not e.get("condition")
            ]
            current = next_nodes[0] if next_nodes else None

        # Complete workflow run in DB
        await workflow.execute_activity(
            update_workflow_run_db_activity,
            args=[run_id, "completed", current, state, state],
            start_to_close_timeout=timedelta(seconds=15),
        )
        return state


async def run_worker():
    client = await Client.connect(
        settings.temporal_url,
        namespace=settings.temporal_namespace,
    )
    worker = Worker(
        client,
        task_queue="cyberteam-tasks",
        workflows=[
            CompanyOnboardingWorkflow,
            SalesOutreachWorkflow,
            CustomerSupportWorkflow,
            DynamicGraphWorkflow,
        ],
        activities=[
            invoke_agent_activity,
            remember_activity,
            request_approval_activity,
            execute_tool_activity,
            consume_approval_activity,
            approval_is_executable_activity,
            update_workflow_run_db_activity,
        ],
    )
    logger.info("Starting Temporal worker...")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(run_worker())
