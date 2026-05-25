from pathlib import Path

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
