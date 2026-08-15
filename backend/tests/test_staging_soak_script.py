import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts/staging-soak.py"
SPEC = importlib.util.spec_from_file_location("staging_soak", SCRIPT_PATH)
staging_soak = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(staging_soak)


def _readiness() -> dict:
    return {
        "autonomous_company": {
            "status": "ready",
            "sections": {
                "company_signals": {
                    "status": "ready",
                    "stale_pending": 0,
                    "undispositioned_processed": 0,
                },
                "claim_extraction": {
                    "status": "ready",
                    "expired_leases": 0,
                    "stale_failed": 0,
                },
                "mandates": {"status": "ready", "missing_mandates": 0},
                "business_events": {
                    "status": "ready",
                    "stale_unexplained": 0,
                    "unexplained": 0,
                },
                "work_portfolio": {
                    "status": "bounded",
                    "saturated_domains": [],
                    "recovery_required_domains": [],
                },
                "outcome_learning": {
                    "status": "ready",
                    "stale_unassessed_work": 0,
                },
                "action_candidates": {"stale_proposed": 0},
                "model_availability": {
                    "status": "ready",
                    "capabilities": {
                        "status": "ready",
                        "qualified": 5,
                        "required": 5,
                    },
                },
                "temporal_delivery": {"status": "ready"},
            },
        }
    }


def test_autonomy_gate_requires_complete_evidence_to_outcome_loop():
    passed, detail = staging_soak.autonomy_gate(_readiness())

    assert passed is True
    assert detail["status"] == "passed"
    assert all(detail["checks"].values())


def test_autonomy_gate_fails_for_unassessed_outcomes_or_unqualified_model():
    readiness = _readiness()
    sections = readiness["autonomous_company"]["sections"]
    sections["outcome_learning"].update(
        {"status": "stale_backlog", "stale_unassessed_work": 3}
    )
    sections["model_availability"].update({"status": "not_qualified"})

    passed, detail = staging_soak.autonomy_gate(readiness)

    assert passed is False
    assert detail["checks"]["outcomes_current"] is False
    assert detail["checks"]["model_task_qualified"] is False
