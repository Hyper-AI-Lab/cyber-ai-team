"""LangGraph-based Orchestrator — workflow engine with stateful graph execution."""

import uuid

from sqlalchemy import select
from temporalio.client import Client

from cyber_team.agents.manager import AgentManager
from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import ApprovalRequest, Workflow, WorkflowRun
from cyber_team.memory.service import MemoryService


class Orchestrator:
    def __init__(
        self,
        agent_manager: AgentManager,
        memory_service: MemoryService,
        tool_registry=None,
    ):
        self.agent_manager = agent_manager
        self.memory_service = memory_service
        self._tool_registry = tool_registry
        self._audit = getattr(agent_manager, "_audit", None)
        self._metrics = getattr(agent_manager, "_metrics", None)

    async def list_workflows(self) -> list[dict]:
        async with async_session() as session:
            result = await session.execute(select(Workflow))
            workflows = result.scalars().all()
            return [self._workflow_to_dict(w) for w in workflows]

    async def get_workflow(self, workflow_id: str) -> dict | None:
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
            workflow = self._workflow_to_dict(wf)
        if self._audit:
            await self._audit.record(
                event_type="workflow.created",
                actor="owner",
                actor_type="user",
                resource_type="workflow",
                resource_id=wf_id,
                action="create",
                metadata={"name": data.name},
            )
        return workflow

    async def run_workflow(self, workflow_id: str, input_data: dict | None = None) -> dict:
        if input_data is None:
            input_data = {}
        wf = await self.get_workflow(workflow_id)
        if not wf:
            raise ValueError(f"Workflow {workflow_id} not found")

        run_id = str(uuid.uuid4())
        graph = wf["graph_definition"]

        # Create run record in the database first
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
            run_dict = self._run_to_dict(run)

        if self._audit:
            await self._audit.record(
                event_type="workflow.run_started",
                actor="owner",
                actor_type="user",
                resource_type="workflow_run",
                resource_id=run_id,
                action="run",
                metadata={"workflow_id": workflow_id},
            )
        if self._metrics:
            self._metrics.record_workflow_state("running")

        # Dispatch the dynamic durable execution to Temporal
        try:
            client = await Client.connect(
                settings.temporal_url,
                namespace=settings.temporal_namespace,
            )
            graph_copy = dict(graph)
            graph_copy["workflow_id"] = workflow_id

            await client.start_workflow(
                "DynamicGraphWorkflow",
                args=[graph_copy, input_data, run_id],
                id=run_id,
                task_queue="cyberteam-tasks",
            )
            return run_dict
        except Exception as e:
            async with async_session() as session:
                run_obj = (
                    await session.execute(
                        select(WorkflowRun).where(WorkflowRun.id == run_id)
                    )
                ).scalar_one()
                run_obj.status = "failed"
                run_obj.error = str(e)
                run_obj.completed_at = utc_now()
                await session.commit()
            if self._audit:
                await self._audit.record(
                    event_type="workflow.run_failed",
                    actor="system",
                    actor_type="system",
                    resource_type="workflow_run",
                    resource_id=run_id,
                    action="run",
                    outcome="failed",
                    metadata={"workflow_id": workflow_id, "error": str(e)},
                )
            if self._metrics:
                self._metrics.record_workflow_state("failed")
            raise

    async def resume_workflow_run(self, run_id: str) -> dict:
        async with async_session() as session:
            run_obj = (
                await session.execute(
                    select(WorkflowRun).where(WorkflowRun.id == run_id)
                )
            ).scalar_one_or_none()
            if not run_obj:
                raise ValueError(f"Workflow run {run_id} not found")
            if run_obj.status != "waiting_approval":
                raise ValueError(f"Workflow run {run_id} is not waiting for approval")
            current = run_obj.current_node
            state = dict(run_obj.state or {})

        if not current:
            raise ValueError(f"Workflow run {run_id} has no current node")

        approval_id = state.get(f"{current}_approval_id")
        if not approval_id:
            raise ValueError(f"Workflow run {run_id} has no pending approval")

        async with async_session() as session:
            approval = (
                await session.execute(
                    select(ApprovalRequest).where(ApprovalRequest.id == approval_id)
                )
            ).scalar_one_or_none()
            if not approval:
                raise ValueError(f"Approval request {approval_id} not found")
            if approval.status == "pending":
                raise ValueError(f"Approval request {approval_id} is still pending")

        # Connect to Temporal to signal the resumed approval decision
        client = await Client.connect(
            settings.temporal_url,
            namespace=settings.temporal_namespace,
        )
        handle = client.get_workflow_handle(run_id)

        if approval.status == "rejected":
            await handle.signal("approval_signal", "rejected")
            async with async_session() as session:
                run_obj = (
                    await session.execute(
                        select(WorkflowRun).where(WorkflowRun.id == run_id)
                    )
                ).scalar_one()
                run_obj.status = "rejected"
                run_obj.error = approval.review_note or "Approval rejected"
                run_obj.completed_at = utc_now()
                await session.commit()
                run_dict = self._run_to_dict(run_obj)
            if self._audit:
                await self._audit.record(
                    event_type="workflow.run_rejected",
                    actor="system",
                    actor_type="system",
                    resource_type="workflow_run",
                    resource_id=run_id,
                    action="resume",
                    outcome="blocked",
                    metadata={"approval_id": approval_id},
                )
            if self._metrics:
                self._metrics.record_workflow_state("rejected")
            return run_dict

        # Signal "approved" to the running Temporal Workflow condition
        await handle.signal("approval_signal", "approved")

        async with async_session() as session:
            run_obj = (
                await session.execute(
                    select(WorkflowRun).where(WorkflowRun.id == run_id)
                )
            ).scalar_one()
            run_dict = self._run_to_dict(run_obj)

        if self._audit:
            await self._audit.record(
                event_type="workflow.run_resumed",
                actor="system",
                actor_type="system",
                resource_type="workflow_run",
                resource_id=run_id,
                action="resume",
                metadata={"approval_id": approval_id},
            )
        if self._metrics:
            self._metrics.record_workflow_state("resumed")
        return run_dict

    async def list_workflow_runs(self, workflow_id: str) -> list[dict]:
        async with async_session() as session:
            result = await session.execute(
                select(WorkflowRun).where(WorkflowRun.workflow_id == workflow_id)
                .order_by(WorkflowRun.started_at.desc())
            )
            runs = result.scalars().all()
            return [self._run_to_dict(r) for r in runs]

    async def get_workflow_run(self, run_id: str) -> dict | None:
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
