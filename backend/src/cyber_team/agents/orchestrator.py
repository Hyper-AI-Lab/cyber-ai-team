"""LangGraph-based Orchestrator — workflow engine with stateful graph execution."""

import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import select

from cyber_team.db import async_session
from cyber_team.db.models import ApprovalRequest, Workflow, WorkflowRun
from cyber_team.agents.manager import AgentManager
from cyber_team.memory.service import MemoryService


class Orchestrator:
    def __init__(self, agent_manager: AgentManager, memory_service: MemoryService, tool_registry=None):
        self.agent_manager = agent_manager
        self.memory_service = memory_service
        self._tool_registry = tool_registry

    async def list_workflows(self) -> list[dict]:
        async with async_session() as session:
            result = await session.execute(select(Workflow))
            workflows = result.scalars().all()
            return [self._workflow_to_dict(w) for w in workflows]

    async def get_workflow(self, workflow_id: str) -> Optional[dict]:
        async with async_session() as session:
            result = await session.execute(
                select(Workflow).where(Workflow.id == workflow_id)
            )
            wf = result.scalar_one_or_none()
            return self._workflow_to_dict(wf) if wf else None

    async def create_workflow(self, data) -> dict:
        wf_id = str(uuid.uuid4())
        async with async_session() as session:
            wf = Workflow(
                id=wf_id,
                name=data.name,
                description=data.description,
                graph_definition=data.graph_definition,
                trigger_type=data.trigger_type,
                trigger_config=data.trigger_config,
            )
            session.add(wf)
            await session.commit()
            return self._workflow_to_dict(wf)

    async def run_workflow(self, workflow_id: str, input_data: Optional[dict] = None) -> dict:
        if input_data is None:
            input_data = {}
        wf = await self.get_workflow(workflow_id)
        if not wf:
            raise ValueError(f"Workflow {workflow_id} not found")

        run_id = str(uuid.uuid4())
        graph = wf["graph_definition"]

        # Create run record
        async with async_session() as session:
            run = WorkflowRun(
                id=run_id,
                workflow_id=workflow_id,
                status="running",
                current_node=graph.get("entry_node"),
                state=input_data,
            )
            session.add(run)
            await session.commit()

        # Execute the graph
        try:
            result, status = await self._execute_graph(graph, input_data, run_id)
            async with async_session() as session:
                run_obj = (await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
                run_obj.status = status
                if status == "completed":
                    run_obj.result = result
                    run_obj.completed_at = datetime.utcnow()
                else:
                    run_obj.state = result
                await session.commit()
            return self._run_to_dict(run_obj)
        except Exception as e:
            async with async_session() as session:
                run_obj = (await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
                run_obj.status = "failed"
                run_obj.error = str(e)
                run_obj.completed_at = datetime.utcnow()
                await session.commit()
            raise

    async def _execute_graph(self, graph: dict, input_data: dict, run_id: str, start_node: Optional[str] = None) -> tuple[dict, str]:
        """Execute a workflow graph by iterating through nodes."""
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        entry_node = graph.get("entry_node")

        if not nodes or not entry_node:
            raise ValueError("Invalid graph: missing nodes or entry_node")

        state = dict(input_data)
        current = start_node or entry_node
        node_map = {n["id"]: n for n in nodes}

        while current:
            node = node_map.get(current)
            if not node:
                break

            # Update run state
            async with async_session() as session:
                run_obj = (await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
                run_obj.current_node = current
                run_obj.state = state
                await session.commit()

            node_type = node.get("type", "agent")

            if node_type == "agent":
                agent_id = node.get("agent_id")
                task = node.get("task_template", "").format(**state)
                result = await self.agent_manager.invoke_agent(agent_id, task)
                state[f"{current}_output"] = result

            elif node_type == "tool":
                tool_name = node.get("tool_name")
                tool_args = node.get("args_template", {}).copy()
                for k, v in tool_args.items():
                    if isinstance(v, str):
                        try:
                            tool_args[k] = v.format(**state)
                        except KeyError:
                            pass  # Leave template as-is if key missing
                # Execute via tool registry
                if self._tool_registry:
                    approval_id = state.get(f"{current}_approval_id")
                    if approval_id:
                        tool_args["_approval_id"] = approval_id
                    result = await self._tool_registry.execute(tool_name, tool_args)
                    result_data = result.model_dump()
                    state[f"{current}_output"] = result_data
                    approval_id = (result_data.get("output") or {}).get("approval_id")
                    if approval_id:
                        state[f"{current}_approval_id"] = approval_id
                        state[f"{current}_tool_args"] = tool_args
                        async with async_session() as session:
                            run_obj = (await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
                            run_obj.status = "waiting_approval"
                            run_obj.current_node = current
                            run_obj.state = state
                            await session.commit()
                        return state, "waiting_approval"
                else:
                    state[f"{current}_output"] = {"tool": tool_name, "args": tool_args, "note": "tool_registry_not_available"}

            elif node_type == "decision":
                condition_key = node.get("condition_key")
                condition_value = state.get(condition_key)
                # Find matching edge
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
                approval_id = await self.agent_manager._request_approval(
                    agent_id, "workflow_step", description, state
                )
                state[f"{current}_approval_id"] = approval_id
                async with async_session() as session:
                    run_obj = (await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
                    run_obj.status = "waiting_approval"
                    run_obj.current_node = current
                    run_obj.state = state
                    await session.commit()
                return state, "waiting_approval"

            # Find next node via edges
            next_nodes = [e.get("to") for e in edges if e.get("from") == current and not e.get("condition")]
            current = next_nodes[0] if next_nodes else None

        return state, "completed"

    async def resume_workflow_run(self, run_id: str) -> dict:
        async with async_session() as session:
            run_obj = (await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one_or_none()
            if not run_obj:
                raise ValueError(f"Workflow run {run_id} not found")
            if run_obj.status != "waiting_approval":
                raise ValueError(f"Workflow run {run_id} is not waiting for approval")
            wf = (await session.execute(select(Workflow).where(Workflow.id == run_obj.workflow_id))).scalar_one()
            graph = wf.graph_definition
            current = run_obj.current_node
            state = dict(run_obj.state or {})

        if not current:
            raise ValueError(f"Workflow run {run_id} has no current node")

        approval_id = state.get(f"{current}_approval_id")
        if not approval_id:
            raise ValueError(f"Workflow run {run_id} has no pending approval")

        async with async_session() as session:
            approval = (await session.execute(select(ApprovalRequest).where(ApprovalRequest.id == approval_id))).scalar_one_or_none()
            if not approval:
                raise ValueError(f"Approval request {approval_id} not found")
            if approval.status == "pending":
                raise ValueError(f"Approval request {approval_id} is still pending")
            if approval.status == "rejected":
                run_obj = (await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
                run_obj.status = "rejected"
                run_obj.error = approval.review_note or "Approval rejected"
                run_obj.completed_at = datetime.utcnow()
                await session.commit()
                return self._run_to_dict(run_obj)

        nodes = {node["id"]: node for node in graph.get("nodes", [])}
        node_type = nodes.get(current, {}).get("type", "agent")
        if node_type == "approval":
            edges = graph.get("edges", [])
            next_nodes = [edge.get("to") for edge in edges if edge.get("from") == current and not edge.get("condition")]
            start_node = next_nodes[0] if next_nodes else None
        else:
            start_node = current

        if not start_node:
            async with async_session() as session:
                run_obj = (await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
                run_obj.status = "completed"
                run_obj.result = state
                run_obj.completed_at = datetime.utcnow()
                await session.commit()
                return self._run_to_dict(run_obj)

        result, status = await self._execute_graph(graph, state, run_id, start_node=start_node)
        async with async_session() as session:
            run_obj = (await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
            run_obj.status = status
            if status == "completed":
                run_obj.result = result
                run_obj.completed_at = datetime.utcnow()
            else:
                run_obj.state = result
            await session.commit()
            return self._run_to_dict(run_obj)

    async def list_workflow_runs(self, workflow_id: str) -> list[dict]:
        async with async_session() as session:
            result = await session.execute(
                select(WorkflowRun).where(WorkflowRun.workflow_id == workflow_id)
                .order_by(WorkflowRun.started_at.desc())
            )
            runs = result.scalars().all()
            return [self._run_to_dict(r) for r in runs]

    async def get_workflow_run(self, run_id: str) -> Optional[dict]:
        async with async_session() as session:
            result = await session.execute(
                select(WorkflowRun).where(WorkflowRun.id == run_id)
            )
            run = result.scalar_one_or_none()
            return self._run_to_dict(run) if run else None

    # ─── Dashboard Helpers ────────────────────────────────────────────

    async def get_kpis(self) -> dict:
        async with async_session() as session:
            total_agents = len(await self.agent_manager.list_agents())
            total_workflows = len(await self.list_workflows())
            pending_approvals = len(await self.agent_manager.get_approval_queue("pending"))
            running_workflows = len([
                r for r in (await session.execute(
                    select(WorkflowRun).where(WorkflowRun.status == "running")
                )).scalars().all()
            ])
        return {
            "total_agents": total_agents,
            "total_workflows": total_workflows,
            "pending_approvals": pending_approvals,
            "running_workflows": running_workflows,
        }

    async def get_workflow_visualizations(self) -> list[dict]:
        workflows = await self.list_workflows()
        return [
            {
                "id": wf["id"],
                "name": wf["name"],
                "graph": wf["graph_definition"],
                "status": wf["status"],
            }
            for wf in workflows
        ]

    async def get_recent_activity(self, limit: int = 50) -> list[dict]:
        async with async_session() as session:
            result = await session.execute(
                select(WorkflowRun)
                .order_by(WorkflowRun.started_at.desc())
                .limit(limit)
            )
            runs = result.scalars().all()
            return [
                {
                    "id": r.id,
                    "workflow_id": r.workflow_id,
                    "status": r.status,
                    "current_node": r.current_node,
                    "started_at": r.started_at.isoformat(),
                    "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                }
                for r in runs
            ]

    # ─── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _workflow_to_dict(wf: Workflow) -> dict:
        return {
            "id": wf.id,
            "name": wf.name,
            "description": wf.description,
            "graph_definition": wf.graph_definition,
            "status": wf.status,
            "trigger_type": wf.trigger_type,
            "trigger_config": wf.trigger_config,
        }

    @staticmethod
    def _run_to_dict(run: WorkflowRun) -> dict:
        return {
            "id": run.id,
            "workflow_id": run.workflow_id,
            "status": run.status,
            "current_node": run.current_node,
            "state": run.state,
            "result": run.result,
            "error": run.error,
        }
