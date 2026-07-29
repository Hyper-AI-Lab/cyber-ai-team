from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.config import settings
from cyber_team.db import Base
from cyber_team.db.models import OrchestrationToolProposal
from cyber_team.operations import capability_expansion as capability_module
from cyber_team.operations.capability_expansion import CapabilityExpansionService


@pytest.fixture
async def capability_session_factory(monkeypatch, tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(capability_module, "async_session", factory)
    monkeypatch.setattr(settings, "tool_sandbox_enabled", True)
    monkeypatch.setattr(settings, "tool_sandbox_image", "cyber-team-core:test")
    monkeypatch.setattr(settings, "tool_sandbox_artifact_root", str(tmp_path))
    try:
        yield factory
    finally:
        await engine.dispose()


async def seed_proposal(factory):
    async with factory() as session:
        session.add(
            OrchestrationToolProposal(
                id="proposal-1",
                title="Tool proposal: evidence scorer",
                capability="evidence_scoring",
                status="proposed",
                risk_level="low",
                side_effects=False,
                source_type="role_gap",
                source_id="gap-1",
                purpose="Score evidence deterministically.",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                required_credentials=[],
                executor_kind="proposed_executor",
                tests_required=["unit tests"],
                rollback_notes="Do not activate runtime code.",
                readiness_checks=["tests_pass"],
                sandbox_mode="sandbox_draft",
                sandbox_result={
                    "resource_policy": {
                        "license": "Apache-2.0",
                        "cost_model": "free_self_hosted",
                    }
                },
                idempotency_key="proposal-key-1",
            )
        )
        await session.commit()


def valid_draft():
    return {
        "license": "Apache-2.0",
        "files": [
            {
                "path": "evidence_scorer.py",
                "content": (
                    "# SPDX-License-Identifier: Apache-2.0\n"
                    "def score(values):\n"
                    "    return sum(values) / max(len(values), 1)\n"
                ),
            }
        ],
        "tests": [
            {
                "path": "test_evidence_scorer.py",
                "content": (
                    "# SPDX-License-Identifier: Apache-2.0\n"
                    "import unittest\n"
                    "from evidence_scorer import score\n\n"
                    "class ScoreTest(unittest.TestCase):\n"
                    "    def test_score(self):\n"
                    "        self.assertEqual(score([1, 0]), 0.5)\n"
                ),
            }
        ],
    }


@pytest.mark.asyncio
async def test_isolated_sandbox_emits_sbom_patch_and_never_activates_runtime(
    capability_session_factory,
):
    await seed_proposal(capability_session_factory)
    commands = []

    def runner(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="1 test OK", stderr="")

    service = CapabilityExpansionService(runner=runner)

    result = await service.sandbox_tool_proposal(
        "proposal-1", draft=valid_draft(), actor="owner@example.com"
    )

    assert result["status"] == "sandbox_passed"
    sandbox = result["sandbox_result"]
    assert sandbox["runtime_activation"] is False
    assert sandbox["runner"]["network"] == "none"
    assert "--cap-drop=ALL" in commands[0][0]
    assert commands[0][1]["env"] == {}
    from pathlib import Path

    assert Path(sandbox["sbom_path"]).is_file()
    assert Path(sandbox["patch_path"]).is_file()
    assert sandbox["review_branch"] == "codex/tool-proposal-proposal-1"
    assert "materialize-tool-proposal.sh" in sandbox["materialization_command"]


@pytest.mark.asyncio
async def test_static_validation_rejects_secret_network_and_code_execution(
    capability_session_factory,
):
    await seed_proposal(capability_session_factory)
    draft = valid_draft()
    draft["files"][0]["content"] = (
        "# SPDX-License-Identifier: Apache-2.0\n"
        "import socket\n"
        "password='leak'\n"
        "eval('1+1')\n"
    )
    runner_calls = []
    service = CapabilityExpansionService(
        runner=lambda *args, **kwargs: runner_calls.append((args, kwargs))
    )

    result = await service.sandbox_tool_proposal("proposal-1", draft=draft)

    assert result["status"] == "sandbox_failed"
    errors = result["sandbox_result"]["validation"]["errors"]
    assert any(item.startswith("forbidden_import") for item in errors)
    assert any(item.startswith("forbidden_call") for item in errors)
    assert any(item.startswith("secret_like_content") for item in errors)
    assert runner_calls == []


@pytest.mark.asyncio
async def test_safe_procedure_is_delegated_to_workflow_compiler(
    capability_session_factory,
):
    compiler = SimpleNamespace(propose=None)
    from unittest.mock import AsyncMock

    compiler.propose = AsyncMock(return_value={"status": "active"})
    service = CapabilityExpansionService(workflow_compiler=compiler)

    result = await service.activate_safe_procedure(
        spec_key="research_procedure",
        title="Research procedure",
        specification={"steps": []},
        source_id="gap-1",
        actor="chief_operating_agent",
    )

    assert result["status"] == "active"
    compiler.propose.assert_awaited_once()
    assert compiler.propose.await_args.kwargs["activate_if_safe"] is True
