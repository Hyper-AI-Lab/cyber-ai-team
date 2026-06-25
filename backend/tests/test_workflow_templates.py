import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.agents import orchestrator as orchestrator_module
from cyber_team.agents.manager import AgentManager
from cyber_team.agents.orchestrator import Orchestrator
from cyber_team.db import Base
from cyber_team.db.models import Workflow, WorkflowTemplate
from cyber_team.workflows import templates as templates_module
from cyber_team.workflows.templates import WorkflowTemplateService


@pytest.fixture
async def session_factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(templates_module, "async_session", factory)
    monkeypatch.setattr(orchestrator_module, "async_session", factory)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_core_workflow_templates_are_idempotent_and_instantiable(session_factory):
    orchestrator = Orchestrator(agent_manager=AgentManager(), memory_service=None)
    service = WorkflowTemplateService(orchestrator=orchestrator)

    first = await service.ensure_core_templates()
    second = await service.ensure_core_templates()
    workflows = await service.ensure_core_workflows()
    workflows_again = await service.ensure_core_workflows()

    assert first["created"] == 4
    assert second["updated"] == 4
    assert len(workflows["created"]) == 4
    assert workflows_again["created"] == []
    async with session_factory() as session:
        template_count = len((await session.execute(select(WorkflowTemplate))).scalars().all())
        workflow_rows = (await session.execute(select(Workflow))).scalars().all()
    assert template_count == 4
    assert len(workflow_rows) == 4
    assert {row.trigger_config["template_id"] for row in workflow_rows}
