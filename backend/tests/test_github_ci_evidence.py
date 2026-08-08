import importlib.util
import json
from pathlib import Path


def load_ci_evidence_module():
    path = Path(__file__).resolve().parents[2] / "scripts/github-ci-evidence.py"
    spec = importlib.util.spec_from_file_location("github_ci_evidence", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_completed_failed_schedule_is_not_reported_as_pending_or_ready(
    monkeypatch,
    tmp_path,
):
    module = load_ci_evidence_module()
    head = "a" * 40
    runs = {
        "push": (
            {"head_sha": head, "status": "completed", "conclusion": "success"},
            [],
        ),
        "workflow_dispatch": (
            {"head_sha": head, "status": "completed", "conclusion": "success"},
            [],
        ),
        "schedule": (
            {"head_sha": head, "status": "completed", "conclusion": "failure"},
            [{"name": "Docker Image Scan", "conclusion": "failure"}],
        ),
    }
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/cyber-team")
    monkeypatch.setenv("CI_EVIDENCE_DIR", str(tmp_path))
    monkeypatch.setenv("CI_EVIDENCE_REQUIRE_READY", "1")
    monkeypatch.setattr(module, "load_env", lambda _path: None)
    monkeypatch.setattr(module, "branch_head", lambda *_args: head)
    monkeypatch.setattr(
        module,
        "latest_run",
        lambda _repository, _token, event, _branch: runs[event],
    )

    assert module.main() == 1
    payload = json.loads((tmp_path / "github-ci-latest.json").read_text())

    assert payload["status"] == "degraded"
    assert payload["schedule_current_head"] is True
    assert payload["schedule_failed_current_head"] is True
    assert payload["schedule_pending_current_head"] is False
    assert payload["failing_jobs"] == [
        {"name": "Docker Image Scan", "conclusion": "failure"}
    ]
    assert "scheduled CI run failed" in payload["detail"]
