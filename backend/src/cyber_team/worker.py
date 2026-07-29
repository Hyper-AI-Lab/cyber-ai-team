"""Temporal worker for durable workflow execution."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

logger = logging.getLogger(__name__)

with workflow.unsafe.imports_passed_through():
    from cyber_team.agents.manager import AgentManager
    from cyber_team.config import settings
    from cyber_team.llm.gateway import LLMGateway
    from cyber_team.memory.service import MemoryService


worker_llm_gateway = LLMGateway()


@asynccontextmanager
async def activity_services():
    """Build the service graph activities need outside the FastAPI lifespan."""
    from cyber_team.audit.service import AuditService
    from cyber_team.comms.gateway import CommsGateway
    from cyber_team.company.intelligence import CompanyIntelligenceService
    from cyber_team.integrations.erpnext import ERPNextClient
    from cyber_team.observability.metrics import MetricsService
    from cyber_team.operations.action_policy import ActionPolicyService
    from cyber_team.operations.autonomy_cycle import AutonomousCompanyCycleService
    from cyber_team.operations.executive import ExecutiveCompanyOSService
    from cyber_team.operations.governor import OrchestrationGovernorService
    from cyber_team.operations.memory_steward import MemoryStewardService
    from cyber_team.operations.outcomes import OutcomeLearningService
    from cyber_team.operations.planning import AutonomousPlanningService
    from cyber_team.operations.readiness import ProductionReadinessEvidenceService
    from cyber_team.operations.strategy import CompanyStrategyService
    from cyber_team.operations.work_portfolio import WorkPortfolioService
    from cyber_team.tools.registry import ToolRegistry

    metrics = MetricsService()
    audit = AuditService(metrics_service=metrics)
    memory = MemoryService()
    comms = CommsGateway(metrics_service=metrics)
    erpnext = ERPNextClient()
    registry = ToolRegistry()
    action_policy = ActionPolicyService(audit_service=audit)
    manager = AgentManager(
        memory_service=memory,
        audit_service=audit,
        tool_registry=registry,
        llm_gateway=worker_llm_gateway,
    )
    registry.set_services(
        comms=comms,
        memory=memory,
        agent_manager=manager,
        erpnext=erpnext,
        audit=audit,
        metrics=metrics,
        action_policy=action_policy,
    )
    intelligence = CompanyIntelligenceService(
        llm_gateway=worker_llm_gateway,
        memory_service=memory,
        audit_service=audit,
    )
    strategy = CompanyStrategyService(
        llm_gateway=worker_llm_gateway,
        audit_service=audit,
    )
    work_portfolio = WorkPortfolioService(
        agent_manager=manager,
        audit_service=audit,
        company_intelligence_service=intelligence,
        tool_registry=registry,
    )
    outcomes = OutcomeLearningService(
        action_policy_service=action_policy,
        memory_service=memory,
        audit_service=audit,
    )
    autonomy_cycle = AutonomousCompanyCycleService(
        intelligence_service=intelligence,
        strategy_service=strategy,
        work_portfolio_service=work_portfolio,
        outcome_learning_service=outcomes,
        action_policy_service=action_policy,
        audit_service=audit,
    )
    memory_steward = MemoryStewardService(
        audit_service=audit,
        memory_service=memory,
        agent_manager=manager,
    )
    planning = AutonomousPlanningService(
        agent_manager=manager,
        memory_steward_service=memory_steward,
        tool_registry=registry,
        audit_service=audit,
    )
    readiness = ProductionReadinessEvidenceService(audit_service=audit)
    governor = OrchestrationGovernorService(
        agent_manager=manager,
        planning_service=planning,
        memory_steward_service=memory_steward,
        tool_registry=registry,
        audit_service=audit,
        readiness_evidence_service=readiness,
        comms_gateway=comms,
        erpnext=erpnext,
    )
    executive = ExecutiveCompanyOSService(
        governor_service=governor,
        agent_manager=manager,
        memory_service=memory,
        audit_service=audit,
        tool_registry=registry,
        planning_service=planning,
        readiness_evidence_service=readiness,
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
            "metrics": metrics,
            "autonomy_cycle": autonomy_cycle,
            "executive": executive,
        }
    finally:
        await memory.shutdown()
        await erpnext.close()


@activity.defn
async def invoke_agent_activity(
    agent_id: str,
    task: str,
    workflow_run_id: str | None = None,
    workflow_node_id: str | None = None,
) -> str:
    async with activity_services() as services:
        return await services["manager"].invoke_agent(
            agent_id,
            task,
            source_type="workflow_agent_activity",
            trace_metadata={
                "workflow_run_id": workflow_run_id,
                "workflow_node_id": workflow_node_id,
            },
        )


@activity.defn
async def remember_activity(
    agent_id: str,
    memory_type: str,
    namespace: str,
    content: str,
    workflow_run_id: str | None = None,
    workflow_node_id: str | None = None,
) -> str:
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
        trace_invocation_id = (
            f"workflow-memory:{workflow_run_id or 'unknown'}:"
            f"{workflow_node_id or 'manual'}"
        )
        await services["memory"].record_trace(
            SimpleNamespace(
                invocation_id=trace_invocation_id,
                agent_id=agent_id,
                conversation_id=None,
                source_type="workflow_memory_write",
                task_excerpt=f"Workflow memory write: {namespace}",
                memory_namespace=namespace,
                read_policy={},
                write_policy={"memory_type": memory_type, "namespace": namespace},
                recalled_memory_ids=[],
                written_memory_ids=[result["id"]],
                recall_count=0,
                write_count=1,
                errors=[],
                metadata={
                    "workflow_run_id": workflow_run_id,
                    "workflow_node_id": workflow_node_id,
                    "coverage": "write",
                },
            )
        )
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
async def execute_tool_activity(
    tool_name: str,
    tool_args: dict,
    workflow_run_id: str | None = None,
    workflow_node_id: str | None = None,
    action_envelope: dict | None = None,
) -> dict:
    async with activity_services() as services:
        tool_args = dict(tool_args or {})
        tool_args.update({
            "_actor": "workflow",
            "_actor_type": "system",
            "_workflow_run_id": workflow_run_id,
            "_workflow_node_id": workflow_node_id,
            "_source_type": "workflow_tool_activity",
            "_action_envelope": action_envelope or {},
        })
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


@activity.defn
async def run_autonomous_company_cycle_activity(payload: dict) -> dict:
    async with activity_services() as services:
        return await services["autonomy_cycle"].run(
            trigger=str(payload.get("trigger") or "temporal"),
            event_ids=list(payload.get("event_ids") or []),
        )


@activity.defn
async def run_executive_governor_activity(payload: dict) -> dict:
    async with activity_services() as services:
        return await services["executive"].run_executive_cycle(
            actor=str(payload.get("actor") or "chief_operating_agent_scheduler"),
            dry_run=bool(payload.get("dry_run", False)),
            auto_apply_low_risk=payload.get("auto_apply_low_risk"),
            max_actions=payload.get("max_actions"),
            force_reflection=bool(payload.get("force_reflection", False)),
            force_benchmark_refresh=bool(
                payload.get("force_benchmark_refresh", False)
            ),
            owner_instruction=payload.get("owner_instruction"),
            observer_review=bool(payload.get("observer_review", True)),
            synthetic_large_impact=bool(
                payload.get("synthetic_large_impact", False)
            ),
        )


@workflow.defn
class AutonomousCompanyCycleWorkflow:
    @workflow.run
    async def run(self, payload: dict) -> dict:
        return await workflow.execute_activity(
            run_autonomous_company_cycle_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=40),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=10),
                maximum_interval=timedelta(minutes=5),
                maximum_attempts=3,
            ),
        )


@workflow.defn
class ExecutiveGovernorWorkflow:
    @workflow.run
    async def run(self, payload: dict) -> dict:
        return await workflow.execute_activity(
            run_executive_governor_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=40),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=10),
                maximum_interval=timedelta(minutes=5),
                maximum_attempts=3,
            ),
        )


@workflow.defn
class AutonomousCompanySignalWorkflow:
    def __init__(self) -> None:
        self._event_ids: list[str] = []
        self._cycles = 0

    @workflow.signal
    async def business_event_received(self, event_id: str) -> None:
        if event_id not in self._event_ids and len(self._event_ids) < 200:
            self._event_ids.append(event_id)

    @workflow.run
    async def run(self, config: dict) -> None:
        while True:
            await workflow.wait_condition(lambda: bool(self._event_ids))
            event_ids = list(self._event_ids)
            self._event_ids.clear()
            await workflow.execute_activity(
                run_autonomous_company_cycle_activity,
                {"trigger": "business_event_signal", "event_ids": event_ids},
                start_to_close_timeout=timedelta(minutes=40),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=10),
                    maximum_interval=timedelta(minutes=5),
                    maximum_attempts=3,
                ),
            )
            self._cycles += 1
            if self._cycles >= 100:
                workflow.continue_as_new(config)


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
                    args=[agent_id, task, run_id, current],
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
                    args=[tool_name, tool_args, run_id, current],
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
                        args=[tool_name, tool_args, run_id, current],
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


@workflow.defn
class GenericSpecificationWorkflow:
    """Execute one immutable validated specification with deterministic control flow."""

    def __init__(self) -> None:
        self._approval_status: str | None = None

    @workflow.signal
    def approval_signal(self, status: str) -> None:
        self._approval_status = status

    @workflow.run
    async def run(self, specification: dict, input_data: dict, run_id: str) -> dict:
        steps = {item["id"]: item for item in specification.get("steps", [])}
        if not steps:
            raise ValueError("Workflow specification has no steps")
        compensation_only = {
            step.get("compensation_step_id")
            for step in steps.values()
            if step.get("compensation_step_id")
        }
        executable_steps = set(steps) - compensation_only
        state = dict(input_data)
        completed: set[str] = set()
        executed: list[str] = []
        try:
            while len(completed) < len(executable_steps):
                ready = sorted(
                    step_id
                    for step_id, step in steps.items()
                    if step_id in executable_steps
                    and step_id not in completed
                    and (
                        set(step.get("depends_on") or []) - compensation_only
                    ).issubset(completed)
                )
                if not ready:
                    raise ValueError("Workflow specification dependency deadlock")
                for step_id in ready:
                    step = steps[step_id]
                    await workflow.execute_activity(
                        update_workflow_run_db_activity,
                        args=[run_id, "running", step_id, state],
                        start_to_close_timeout=timedelta(seconds=15),
                    )
                    result = await self._execute_step(step, state, run_id)
                    state[f"{step_id}_output"] = result
                    completed.add(step_id)
                    executed.append(step_id)
        except Exception as exc:
            compensation = await self._compensate(
                executed, steps, state, run_id
            )
            state["compensation"] = compensation
            await workflow.execute_activity(
                update_workflow_run_db_activity,
                args=[run_id, "failed", "", state, None, type(exc).__name__],
                start_to_close_timeout=timedelta(seconds=15),
            )
            raise
        failures = self._acceptance_failures(
            specification.get("acceptance_tests") or [], state
        )
        if failures:
            state["acceptance_failures"] = failures
            await workflow.execute_activity(
                update_workflow_run_db_activity,
                args=[run_id, "failed", "acceptance", state, None, "acceptance_failed"],
                start_to_close_timeout=timedelta(seconds=15),
            )
            raise ValueError("Workflow acceptance tests failed")
        await workflow.execute_activity(
            update_workflow_run_db_activity,
            args=[run_id, "completed", "", state, state],
            start_to_close_timeout=timedelta(seconds=15),
        )
        return state

    async def _execute_step(self, step: dict, state: dict, run_id: str):
        step_id = step["id"]
        retry = step.get("retry") or {}
        retry_policy = RetryPolicy(
            maximum_attempts=int(retry.get("max_attempts", 3)),
            initial_interval=timedelta(
                seconds=int(retry.get("initial_interval_seconds", 2))
            ),
            backoff_coefficient=float(retry.get("backoff_coefficient", 2.0)),
            maximum_interval=timedelta(
                seconds=int(retry.get("maximum_interval_seconds", 60))
            ),
        )
        timeout = timedelta(seconds=int(step.get("timeout_seconds", 300)))
        if step["type"] == "agent":
            task = self._format(step.get("task_template", ""), state)
            return await workflow.execute_activity(
                invoke_agent_activity,
                args=[step["agent_id"], task, run_id, step_id],
                start_to_close_timeout=timeout,
                retry_policy=retry_policy,
            )
        if step["type"] == "tool":
            args = self._format_value(step.get("args_template") or {}, state)
            approval_id = state.get(f"{step_id}_approval_id")
            if approval_id:
                args["_approval_id"] = approval_id
            result = await workflow.execute_activity(
                execute_tool_activity,
                args=[
                    step["tool_name"],
                    args,
                    run_id,
                    step_id,
                    step.get("action_envelope") or {},
                ],
                start_to_close_timeout=timeout,
                retry_policy=retry_policy,
            )
            output = result.get("output") or {}
            requested = output.get("approval_id")
            if output.get("approval_required") and requested:
                state[f"{step_id}_approval_id"] = requested
                await self._wait_for_approval(
                    requested, "tool", step["tool_name"], run_id, step_id, state
                )
                args["_approval_id"] = requested
                result = await workflow.execute_activity(
                    execute_tool_activity,
                    args=[
                        step["tool_name"],
                        args,
                        run_id,
                        step_id,
                        step.get("action_envelope") or {},
                    ],
                    start_to_close_timeout=timeout,
                    retry_policy=retry_policy,
                )
            if not result.get("success"):
                raise ValueError(f"Tool step {step_id} failed")
            return result
        if step["type"] == "memory":
            args = self._format_value(step.get("args_template") or {}, state)
            return await workflow.execute_activity(
                remember_activity,
                args=[
                    args["agent_id"],
                    args.get("memory_type", "episodic"),
                    args["namespace"],
                    args["content"],
                    run_id,
                    step_id,
                ],
                start_to_close_timeout=timeout,
                retry_policy=retry_policy,
            )
        if step["type"] == "approval":
            envelope = step.get("action_envelope") or {}
            approval_id = await workflow.execute_activity(
                request_approval_activity,
                args=[
                    step.get("agent_id") or "chief_operating_agent",
                    envelope.get("action_class", "workflow_step"),
                    envelope.get("expected_effect", f"Approve workflow step {step_id}"),
                    {"action_envelope": envelope},
                    step.get("risk_level", "medium"),
                    "workflow_run",
                    run_id,
                ],
                start_to_close_timeout=timedelta(seconds=30),
            )
            await self._wait_for_approval(
                approval_id, "workflow_run", run_id, run_id, step_id, state
            )
            return {"approval_id": approval_id, "consumed": True}
        if step["type"] == "decision":
            args = step.get("args_template") or {}
            key = args.get("state_key")
            return {
                "value": state.get(key),
                "equals": state.get(key) == args.get("equals"),
            }
        raise ValueError(f"Unsupported step type {step['type']}")

    async def _wait_for_approval(
        self,
        approval_id: str,
        target_type: str,
        target_id: str,
        run_id: str,
        step_id: str,
        state: dict,
    ) -> None:
        state[f"{step_id}_approval_id"] = approval_id
        await workflow.execute_activity(
            update_workflow_run_db_activity,
            args=[run_id, "waiting_approval", step_id, state],
            start_to_close_timeout=timedelta(seconds=15),
        )
        self._approval_status = None
        await workflow.wait_condition(lambda: self._approval_status is not None)
        if self._approval_status != "approved":
            raise ValueError(f"Approval {approval_id} was rejected")
        executable = await workflow.execute_activity(
            approval_is_executable_activity,
            args=[approval_id, target_type, target_id],
            start_to_close_timeout=timedelta(seconds=15),
        )
        if not executable:
            raise ValueError(f"Approval {approval_id} is not executable")
        if target_type == "workflow_run":
            await workflow.execute_activity(
                consume_approval_activity,
                args=[approval_id, "workflow", target_type, target_id],
                start_to_close_timeout=timedelta(seconds=15),
            )

    async def _compensate(
        self,
        executed: list[str],
        steps: dict[str, dict],
        state: dict,
        run_id: str,
    ) -> list[dict]:
        results = []
        for step_id in reversed(executed):
            compensation_id = steps[step_id].get("compensation_step_id")
            compensation = steps.get(compensation_id) if compensation_id else None
            if not compensation or compensation.get("type") != "tool":
                continue
            args = self._format_value(
                compensation.get("args_template") or {}, state
            )
            result = await workflow.execute_activity(
                execute_tool_activity,
                args=[
                    compensation["tool_name"],
                    args,
                    run_id,
                    compensation_id,
                    compensation.get("action_envelope") or {},
                ],
                start_to_close_timeout=timedelta(
                    seconds=int(compensation.get("timeout_seconds", 300))
                ),
            )
            results.append({"step_id": compensation_id, "result": result})
        return results

    @staticmethod
    def _acceptance_failures(tests: list[dict], state: dict) -> list[str]:
        failures = []
        for index, test in enumerate(tests):
            kind = test.get("type")
            key = test.get("state_key")
            if kind == "state_key_exists" and key not in state:
                failures.append(f"test_{index}:missing:{key}")
            elif kind == "equals" and state.get(key) != test.get("expected"):
                failures.append(f"test_{index}:not_equal:{key}")
            elif kind not in {"state_key_exists", "equals"}:
                failures.append(f"test_{index}:unsupported:{kind}")
        return failures

    @classmethod
    def _format_value(cls, value, state):
        if isinstance(value, str):
            return cls._format(value, state)
        if isinstance(value, dict):
            return {key: cls._format_value(item, state) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._format_value(item, state) for item in value]
        return value

    @staticmethod
    def _format(value: str, state: dict) -> str:
        try:
            return value.format(**state)
        except (KeyError, ValueError):
            return value


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
            GenericSpecificationWorkflow,
            AutonomousCompanyCycleWorkflow,
            ExecutiveGovernorWorkflow,
            AutonomousCompanySignalWorkflow,
        ],
        activities=[
            invoke_agent_activity,
            remember_activity,
            request_approval_activity,
            execute_tool_activity,
            consume_approval_activity,
            approval_is_executable_activity,
            update_workflow_run_db_activity,
            run_autonomous_company_cycle_activity,
            run_executive_governor_activity,
        ],
    )
    logger.info("Starting Temporal worker...")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(run_worker())
