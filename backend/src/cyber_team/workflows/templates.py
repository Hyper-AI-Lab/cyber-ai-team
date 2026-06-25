"""Core safe workflow templates for Cyber-Team operating cadence."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from sqlalchemy import desc, select

from cyber_team.agents.manager import slug_id
from cyber_team.agents.orchestrator import Orchestrator
from cyber_team.clock import utc_now
from cyber_team.db import async_session
from cyber_team.db.models import Workflow, WorkflowTemplate


class WorkflowTemplateService:
    """Persists and instantiates safe, owner-runnable workflow templates."""

    CORE_TEMPLATES: tuple[dict[str, Any], ...] = (
        {
            "name": "Company Context Review",
            "description": (
                "Read the current company profile and memory context, then record a "
                "review note."
            ),
            "category": "company_context",
            "version": "1.0.0",
            "graph_definition": {
                "entry_node": "read_profile",
                "nodes": [
                    {
                        "id": "read_profile",
                        "type": "tool",
                        "tool_name": "company_profile_read",
                        "args_template": {},
                    },
                    {
                        "id": "recall_context",
                        "type": "tool",
                        "tool_name": "memory_recall",
                        "args_template": {
                            "query": "latest company context operating model role backlog",
                            "namespace": "company:default",
                            "limit": 5,
                        },
                    },
                    {
                        "id": "write_review_note",
                        "type": "tool",
                        "tool_name": "memory_remember",
                        "args_template": {
                            "content": "Company context review completed by safe workflow.",
                            "memory_type": "episodic",
                            "namespace": "company:default:operations",
                            "importance": 0.4,
                        },
                    },
                ],
                "edges": [
                    {"from": "read_profile", "to": "recall_context"},
                    {"from": "recall_context", "to": "write_review_note"},
                ],
            },
            "metadata": {
                "risk_level": "low",
                "side_effects": "internal_memory_only",
                "owner_visible": True,
            },
        },
        {
            "name": "Role Backlog Triage",
            "description": "Review role-gap memory and record a triage marker for owner follow-up.",
            "category": "roles",
            "version": "1.0.0",
            "graph_definition": {
                "entry_node": "recall_role_gaps",
                "nodes": [
                    {
                        "id": "recall_role_gaps",
                        "type": "tool",
                        "tool_name": "memory_recall",
                        "args_template": {
                            "query": "recommended roles role gaps approvals blocked tools",
                            "namespace": "company:default",
                            "limit": 8,
                        },
                    },
                    {
                        "id": "write_triage_marker",
                        "type": "tool",
                        "tool_name": "memory_remember",
                        "args_template": {
                            "content": (
                                "Role backlog triage workflow completed; inspect "
                                "Recommended Roles for actionable gaps."
                            ),
                            "memory_type": "procedural",
                            "namespace": "company:default:roles",
                            "importance": 0.5,
                        },
                    },
                ],
                "edges": [
                    {"from": "recall_role_gaps", "to": "write_triage_marker"},
                ],
            },
            "metadata": {
                "risk_level": "low",
                "side_effects": "internal_memory_only",
                "owner_visible": True,
            },
        },
        {
            "name": "ERPNext Operations Snapshot",
            "description": (
                "Read current ERPNext projects, tasks, and issues for the operating "
                "board."
            ),
            "category": "erpnext",
            "version": "1.0.0",
            "graph_definition": {
                "entry_node": "read_projects",
                "nodes": [
                    {
                        "id": "read_projects",
                        "type": "tool",
                        "tool_name": "erpnext_project_search",
                        "args_template": {"query": "", "limit": 10},
                    },
                    {
                        "id": "read_tasks",
                        "type": "tool",
                        "tool_name": "erpnext_task_search",
                        "args_template": {"query": "", "limit": 10},
                    },
                    {
                        "id": "read_issues",
                        "type": "tool",
                        "tool_name": "erpnext_issue_search",
                        "args_template": {"query": "", "limit": 10},
                    },
                ],
                "edges": [
                    {"from": "read_projects", "to": "read_tasks"},
                    {"from": "read_tasks", "to": "read_issues"},
                ],
            },
            "metadata": {
                "risk_level": "low",
                "side_effects": "read_only_erpnext",
                "owner_visible": True,
            },
        },
        {
            "name": "Memory Steward Coverage Review",
            "description": (
                "Review memory trace/finding context and record a steward review marker."
            ),
            "category": "memory",
            "version": "1.0.0",
            "graph_definition": {
                "entry_node": "recall_memory_findings",
                "nodes": [
                    {
                        "id": "recall_memory_findings",
                        "type": "tool",
                        "tool_name": "memory_recall",
                        "args_template": {
                            "query": (
                                "memory steward missing trace coverage empty recall "
                                "write failures stale procedural namespace mismatch"
                            ),
                            "namespace": "company:default",
                            "limit": 8,
                        },
                    },
                    {
                        "id": "write_memory_review_marker",
                        "type": "tool",
                        "tool_name": "memory_remember",
                        "args_template": {
                            "content": "Memory steward coverage review workflow completed.",
                            "memory_type": "episodic",
                            "namespace": "company:default:memory",
                            "importance": 0.4,
                        },
                    },
                ],
                "edges": [
                    {
                        "from": "recall_memory_findings",
                        "to": "write_memory_review_marker",
                    },
                ],
            },
            "metadata": {
                "risk_level": "low",
                "side_effects": "internal_memory_only",
                "owner_visible": True,
            },
        },
    )

    def __init__(self, *, orchestrator: Orchestrator) -> None:
        self._orchestrator = orchestrator

    async def ensure_core_templates(self) -> dict[str, Any]:
        created = 0
        updated = 0
        async with async_session() as session:
            for template in self.CORE_TEMPLATES:
                template_id = self._template_id(template)
                result = await session.execute(
                    select(WorkflowTemplate).where(WorkflowTemplate.id == template_id)
                )
                existing = result.scalar_one_or_none()
                if existing:
                    existing.description = template["description"]
                    existing.category = template["category"]
                    existing.graph_definition = template["graph_definition"]
                    existing.default_trigger_type = "manual"
                    existing.default_trigger_config = {}
                    existing.status = "active"
                    existing.is_core = True
                    existing.metadata_ = template.get("metadata") or {}
                    existing.updated_at = utc_now()
                    updated += 1
                else:
                    session.add(
                        WorkflowTemplate(
                            id=template_id,
                            name=template["name"],
                            description=template["description"],
                            category=template["category"],
                            version=template["version"],
                            graph_definition=template["graph_definition"],
                            default_trigger_type="manual",
                            default_trigger_config={},
                            status="active",
                            is_core=True,
                            metadata_=template.get("metadata") or {},
                            created_at=utc_now(),
                            updated_at=utc_now(),
                        )
                    )
                    created += 1
            await session.commit()
        return {"created": created, "updated": updated}

    async def ensure_core_workflows(self) -> dict[str, Any]:
        templates = await self.list_templates(status="active", is_core=True)
        created = []
        existing = []
        for template in templates:
            workflow = await self._find_workflow_for_template(template["id"])
            if workflow:
                existing.append(workflow["id"])
                continue
            workflow = await self.instantiate_template(template["id"], actor="system")
            created.append(workflow["id"])
        return {"created": created, "existing": existing}

    async def list_templates(
        self,
        *,
        status: str | None = None,
        category: str | None = None,
        is_core: bool | None = None,
    ) -> list[dict[str, Any]]:
        async with async_session() as session:
            query = select(WorkflowTemplate)
            if status:
                query = query.where(WorkflowTemplate.status == status)
            if category:
                query = query.where(WorkflowTemplate.category == category)
            if is_core is not None:
                query = query.where(WorkflowTemplate.is_core == is_core)
            result = await session.execute(
                query.order_by(WorkflowTemplate.category.asc(), WorkflowTemplate.name.asc())
            )
            return [self._template_to_dict(template) for template in result.scalars().all()]

    async def get_template(self, template_id: str) -> dict[str, Any] | None:
        async with async_session() as session:
            result = await session.execute(
                select(WorkflowTemplate).where(WorkflowTemplate.id == template_id)
            )
            template = result.scalar_one_or_none()
            return self._template_to_dict(template) if template else None

    async def instantiate_template(
        self,
        template_id: str,
        *,
        actor: str,
    ) -> dict[str, Any]:
        template = await self.get_template(template_id)
        if not template:
            raise ValueError(f"Workflow template {template_id} not found")
        existing = await self._find_workflow_for_template(template_id)
        if existing:
            return existing
        data = SimpleNamespace(
            name=template["name"],
            description=template["description"],
            graph_definition=template["graph_definition"],
            trigger_type=template["default_trigger_type"],
            trigger_config={
                **(template["default_trigger_config"] or {}),
                "template_id": template_id,
                "template_version": template["version"],
                "created_by": actor,
            },
        )
        return await self._orchestrator.create_workflow(data)

    async def _find_workflow_for_template(self, template_id: str) -> dict[str, Any] | None:
        async with async_session() as session:
            result = await session.execute(
                select(Workflow)
                .where(Workflow.trigger_config["template_id"].as_string() == template_id)
                .order_by(desc(Workflow.created_at))
                .limit(1)
            )
            workflow = result.scalar_one_or_none()
            if not workflow:
                return None
            return self._orchestrator._workflow_to_dict(workflow)

    @staticmethod
    def _template_id(template: dict[str, Any]) -> str:
        return f"wft_{slug_id(template['name'])}_{template['version'].replace('.', '_')}"

    @staticmethod
    def _template_to_dict(template: WorkflowTemplate) -> dict[str, Any]:
        return {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "category": template.category,
            "version": template.version,
            "graph_definition": template.graph_definition,
            "default_trigger_type": template.default_trigger_type,
            "default_trigger_config": template.default_trigger_config,
            "status": template.status,
            "is_core": template.is_core,
            "metadata": template.metadata_,
            "created_at": template.created_at.isoformat(),
            "updated_at": template.updated_at.isoformat(),
        }
