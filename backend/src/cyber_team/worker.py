"""Temporal worker for durable workflow execution."""

import asyncio
import logging
from datetime import timedelta

from temporalio import workflow, activity
from temporalio.client import Client
from temporalio.worker import Worker

logger = logging.getLogger(__name__)

with workflow.unsafe.imports_passed_through():
    from cyber_team.config import settings
    from cyber_team.agents.manager import AgentManager
    from cyber_team.memory.service import MemoryService


@activity.defn
async def invoke_agent_activity(agent_id: str, task: str) -> str:
    mgr = AgentManager()
    return await mgr.invoke_agent(agent_id, task)


@activity.defn
async def remember_activity(agent_id: str, memory_type: str, namespace: str, content: str) -> str:
    svc = MemoryService()
    await svc.startup()
    data = type("MemoryWrite", (), {
        "agent_id": agent_id,
        "memory_type": memory_type,
        "namespace": namespace,
        "content": content,
        "metadata": {},
        "importance": 0.5,
    })()
    result = await svc.remember(data)
    await svc.shutdown()
    return result["id"]


@activity.defn
async def request_approval_activity(agent_id: str, action_type: str, description: str) -> str:
    mgr = AgentManager()
    return await mgr._request_approval(agent_id, action_type, description, {})


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
            "sales_outreach",
            "send_outreach",
            f"Send outreach to {lead_info.get('name', 'lead')}: {draft[:200]}",
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
        ],
        activities=[
            invoke_agent_activity,
            remember_activity,
            request_approval_activity,
        ],
    )
    logger.info("Starting Temporal worker...")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(run_worker())
