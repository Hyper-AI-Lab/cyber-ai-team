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

    assert result.success is True
    assert result.output == "ERPNext client not available"
    manager.report_tool_gap.assert_awaited_once()
    tool_name = manager.report_tool_gap.await_args.args[0]
    kwargs = manager.report_tool_gap.await_args.kwargs
    assert tool_name == "erpnext_get_invoices"
    assert kwargs["agent_id"] == "finance"
    assert kwargs["reason"] == "service_unavailable"
