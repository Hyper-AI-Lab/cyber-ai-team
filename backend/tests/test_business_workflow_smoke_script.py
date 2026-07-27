import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "business-workflow-smoke.py"
SPEC = importlib.util.spec_from_file_location("business_workflow_smoke", SCRIPT_PATH)
business_workflow_smoke = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(business_workflow_smoke)


class FakeApi:
    def __init__(self, status=200, response=None):
        self.status = status
        self.response = response or {"id": "approval-1", "status": "rejected"}
        self.calls = []

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        return self.status, self.response


def test_generated_approval_id_only_reads_nested_tool_output():
    assert (
        business_workflow_smoke.generated_approval_id(
            {"success": False, "output": {"approval_id": "approval-1"}}
        )
        == "approval-1"
    )
    assert business_workflow_smoke.generated_approval_id({"approval_id": "top-level"}) is None
    assert business_workflow_smoke.generated_approval_id({"output": None}) is None


def test_reject_generated_approval_uses_owner_rejection_endpoint():
    api = FakeApi()

    response = business_workflow_smoke.reject_generated_approval(api, "approval/unsafe")

    assert response == {"id": "approval-1", "status": "rejected"}
    assert api.calls == [
        (
            "POST",
            "/api/dashboard/approval/approval%2Funsafe/reject",
            {
                "note": (
                    "Rejected by the business workflow smoke lifecycle cleanup; "
                    "no external side effect was authorized."
                )
            },
        )
    ]


def test_reject_generated_approval_fails_closed():
    api = FakeApi(status=400, response={"detail": "already expired"})

    try:
        business_workflow_smoke.reject_generated_approval(api, "approval-1")
    except RuntimeError as exc:
        assert "Could not reject smoke-generated approval" in str(exc)
    else:
        raise AssertionError("cleanup failure should fail the smoke")
