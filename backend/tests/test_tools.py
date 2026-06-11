from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from cyber_team.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_contract_draft_success():
    registry = ToolRegistry()

    result = await registry.execute(
        "contract_draft",
        {
            "topic": "Partnership Agreement",
            "query": "Define equity split and IP assignment.",
            "context": {"description": "Initial cofounder alignment."},
            "content": " C-Corp split 50/50.",
        }
    )

    assert result.success is True
    output = result.output
    assert output["status"] == "completed"
    assert output["topic"] == "Partnership Agreement"

    file_path = Path(output["file_path"])
    assert file_path.exists() is True
    assert file_path.suffix == ".md"
    assert "contracts" in file_path.parts

    # Verify file contents are compiled properly
    file_content = file_path.read_text()
    assert "# CONTRACT DRAFT: Partnership Agreement" in file_content
    assert "C-Corp split 50/50." in file_content
    assert "Governing Law" in file_content


@pytest.mark.asyncio
async def test_policy_draft_success():
    registry = ToolRegistry()

    result = await registry.execute(
        "policy_draft",
        {
            "topic": "Remote Work Policy",
            "query": "Specify core working hours.",
            "context": {"description": "Transitioning to fully remote operations."},
            "content": "Core hours are 10:00 to 16:00 UTC.",
        }
    )

    assert result.success is True
    output = result.output
    assert output["status"] == "completed"
    assert output["topic"] == "Remote Work Policy"

    file_path = Path(output["file_path"])
    assert file_path.exists() is True
    assert file_path.suffix == ".md"
    assert "policies" in file_path.parts

    file_content = file_path.read_text()
    assert "# COMPANY POLICY: Remote Work Policy" in file_content
    assert "Core hours are 10:00 to 16:00 UTC." in file_content
    assert "Review Period" in file_content


@pytest.mark.asyncio
async def test_legal_tools_path_traversal_blocked():
    registry = ToolRegistry()

    # Topic attempt escaping the directory boundary should return a failed ToolResult
    result_contract = await registry.execute(
        "contract_draft",
        {
            "topic": "../../../etc/passwd",
            "query": "malicious query",
        }
    )
    assert result_contract.success is False
    assert "Path traversal detected" in result_contract.error

    result_policy = await registry.execute(
        "policy_draft",
        {
            "topic": "../../../etc/passwd",
            "query": "malicious query",
        }
    )
    assert result_policy.success is False
    assert "Path traversal detected" in result_policy.error


@pytest.mark.asyncio
async def test_role_gap_report_tool_injects_reporting_agent():
    registry = ToolRegistry()
    manager = AsyncMock()
    manager.report_role_gap.return_value = {
        "id": "gap_123",
        "status": "open",
        "title": "Need phone outreach",
    }
    registry.set_services(agent_manager=manager)

    result = await registry.execute(
        "role_gap_report",
        {
            "title": "Need phone outreach",
            "description": "Sales is blocked because phone outreach is needed.",
            "severity": "high",
            "capability": "outbound_voice",
            "requested_tools": ["make_call"],
            "_agent_id": "sales_agent",
        },
    )

    assert result.success is True
    assert result.output["id"] == "gap_123"
    report_data = manager.report_role_gap.await_args.args[0]
    assert report_data.source_agent_id == "sales_agent"
    assert report_data.source_type == "agent"
    assert report_data.requested_tools == ["make_call"]


@pytest.mark.asyncio
async def test_missing_tool_execution_reports_autonomous_gap():
    registry = ToolRegistry()
    manager = AsyncMock()
    manager.report_tool_gap = AsyncMock(return_value={"id": "gap_missing_tool"})
    registry.set_services(agent_manager=manager)

    result = await registry.execute(
        "calendar_event_create",
        {"_agent_id": "operations"},
    )

    assert result.success is False
    assert "Tool not found" in result.error
    manager.report_tool_gap.assert_awaited_once()
    tool_name = manager.report_tool_gap.await_args.args[0]
    kwargs = manager.report_tool_gap.await_args.kwargs
    assert tool_name == "calendar_event_create"
    assert kwargs["agent_id"] == "operations"
    assert kwargs["reason"] == "tool_not_found"


@pytest.mark.asyncio
async def test_unavailable_tool_service_reports_autonomous_gap():
    registry = ToolRegistry()
    manager = AsyncMock()
    manager.report_tool_gap = AsyncMock(return_value={"id": "gap_unavailable"})
    registry.set_services(agent_manager=manager)

    result = await registry.execute(
        "erpnext_get_invoices",
        {"_agent_id": "finance"},
    )

    assert result.success is False
    assert result.output == "ERPNext client not available"
    assert result.error == "ERPNext client not available"
    manager.report_tool_gap.assert_awaited_once()
    tool_name = manager.report_tool_gap.await_args.args[0]
    kwargs = manager.report_tool_gap.await_args.kwargs
    assert tool_name == "erpnext_get_invoices"
    assert kwargs["agent_id"] == "finance"
    assert kwargs["reason"] == "service_unavailable"


@pytest.mark.asyncio
async def test_erpnext_business_tool_is_config_required_when_unconfigured(monkeypatch):
    monkeypatch.setattr(
        "cyber_team.tools.registry.settings.require_live_tool_executors",
        True,
    )
    monkeypatch.setattr("cyber_team.tools.registry.settings.erpnext_api_key", "")
    monkeypatch.setattr("cyber_team.tools.registry.settings.erpnext_api_secret", "")
    registry = ToolRegistry()
    contract = next(
        tool for tool in registry.list_tool_contracts()
        if tool["name"] == "task_create"
    )

    assert contract["state"] == "configuration_required"
    assert contract["side_effects"] is True
    assert contract["requires_configuration"] is True

    result = await registry.execute("task_create", {"task_data": {"subject": "Follow up"}})

    assert result.success is False
    assert result.output["blocked"] is True
    assert result.output["state"] == "configuration_required"
    assert "ERPNext API credentials" in result.error


@pytest.mark.asyncio
async def test_approved_task_create_executes_against_erpnext(monkeypatch):
    monkeypatch.setattr(
        "cyber_team.tools.registry.settings.require_live_tool_executors",
        True,
    )
    monkeypatch.setattr(
        "cyber_team.tools.registry.settings.autonomy_side_effect_mode",
        "manual_only",
    )
    monkeypatch.setattr("cyber_team.tools.registry.settings.erpnext_api_key", "key")
    monkeypatch.setattr("cyber_team.tools.registry.settings.erpnext_api_secret", "secret")
    registry = ToolRegistry()
    manager = AsyncMock()
    manager.approval_is_executable.return_value = True
    manager.consume_approval = AsyncMock()
    erpnext = AsyncMock()
    erpnext.create_task.return_value = {"name": "TASK-0001", "subject": "Follow up"}
    registry.set_services(agent_manager=manager, erpnext=erpnext)

    result = await registry.execute(
        "task_create",
        {
            "task_data": {"subject": "Follow up"},
            "_approval_id": "approval-1",
        },
    )

    assert result.success is True
    assert result.output["doctype"] == "Task"
    assert result.output["action"] == "created"
    assert result.output["record_id"] == "TASK-0001"
    erpnext.create_task.assert_awaited_once_with({"subject": "Follow up"})
    manager.consume_approval.assert_awaited_once()


@pytest.mark.asyncio
async def test_approved_lead_create_uses_standard_erpnext_write_result(monkeypatch):
    monkeypatch.setattr(
        "cyber_team.tools.registry.settings.require_live_tool_executors",
        True,
    )
    monkeypatch.setattr(
        "cyber_team.tools.registry.settings.autonomy_side_effect_mode",
        "manual_only",
    )
    monkeypatch.setattr("cyber_team.tools.registry.settings.erpnext_api_key", "key")
    monkeypatch.setattr("cyber_team.tools.registry.settings.erpnext_api_secret", "secret")
    registry = ToolRegistry()
    manager = AsyncMock()
    manager.approval_is_executable.return_value = True
    manager.consume_approval = AsyncMock()
    erpnext = AsyncMock()
    erpnext.create_lead.return_value = {
        "name": "CRM-LEAD-0001",
        "lead_name": "Smoke Lead",
    }
    registry.set_services(agent_manager=manager, erpnext=erpnext)

    result = await registry.execute(
        "erpnext_create_lead",
        {
            "lead_data": {"lead_name": "Smoke Lead"},
            "_approval_id": "approval-1",
        },
    )

    assert result.success is True
    assert result.output["doctype"] == "Lead"
    assert result.output["action"] == "created"
    assert result.output["record_id"] == "CRM-LEAD-0001"
    erpnext.create_lead.assert_awaited_once_with({"lead_name": "Smoke Lead"})


@pytest.mark.asyncio
async def test_procurement_request_validates_required_items(monkeypatch):
    monkeypatch.setattr("cyber_team.tools.registry.settings.erpnext_api_key", "key")
    monkeypatch.setattr("cyber_team.tools.registry.settings.erpnext_api_secret", "secret")
    registry = ToolRegistry()
    manager = AsyncMock()
    manager.approval_is_executable.return_value = True
    manager.consume_approval = AsyncMock()
    registry.set_services(agent_manager=manager, erpnext=AsyncMock())

    result = await registry.execute(
        "procurement_request",
        {
            "request_data": {"items": [{"qty": 1}]},
            "_approval_id": "approval-1",
        },
    )

    assert result.success is False
    assert "item_code" in result.error


def test_ci_trigger_readiness_tracks_github_configuration(monkeypatch):
    monkeypatch.setattr("cyber_team.tools.registry.settings.github_token", "")
    monkeypatch.setattr("cyber_team.tools.registry.settings.github_repository", "")
    registry = ToolRegistry()
    unconfigured = registry.get_tool_readiness("ci_trigger")
    assert unconfigured["state"] == "configuration_required"

    monkeypatch.setattr("cyber_team.tools.registry.settings.github_token", "token")
    monkeypatch.setattr(
        "cyber_team.tools.registry.settings.github_repository",
        "Hyper-AI-Lab/cyber-team",
    )
    monkeypatch.setattr("cyber_team.tools.registry.settings.github_default_workflow", "ci.yml")
    monkeypatch.setattr("cyber_team.tools.registry.settings.github_default_ref", "main")
    configured = registry.get_tool_readiness("ci_trigger")
    assert configured["state"] == "live"


@pytest.mark.asyncio
async def test_advisory_manifest_tool_reports_advisory_status():
    registry = ToolRegistry()

    result = await registry.execute("candidate_screen", {"query": "market map"})

    assert result.success is True
    assert result.output["status"] == "advisory"
    assert result.output["side_effects"] is False


@pytest.mark.asyncio
async def test_tool_execution_records_memory_trace_metadata():
    registry = ToolRegistry()
    memory = AsyncMock()
    memory.recall.return_value = [
        {
            "id": "memory-1",
            "content": "Known fact",
            "memory_type": "semantic",
            "namespace": "company:acme",
            "agent_id": None,
            "importance": 0.8,
        }
    ]
    registry.set_services(memory=memory)

    result = await registry.execute(
        "memory_recall",
        {
            "query": "Known",
            "namespace": "company:acme",
            "_agent_id": "agent-1",
            "_conversation_id": "conversation-1",
            "_workflow_run_id": "workflow-1",
            "_source_type": "workflow_tool_activity",
        },
    )

    assert result.success is True
    memory.record_trace.assert_awaited_once()
    trace_data = memory.record_trace.await_args.args[0]
    assert trace_data.source_type == "workflow_tool_activity"
    assert trace_data.agent_id == "agent-1"
    assert trace_data.conversation_id == "conversation-1"
    assert trace_data.memory_namespace == "company:acme"
    assert trace_data.recalled_memory_ids == ["memory-1"]
    assert trace_data.metadata["tool_name"] == "memory_recall"
    assert trace_data.metadata["workflow_run_id"] == "workflow-1"
    assert trace_data.metadata["coverage"] == "read"
