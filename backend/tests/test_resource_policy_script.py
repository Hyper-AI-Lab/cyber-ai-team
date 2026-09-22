import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "resource-policy-check.py"
SPEC = importlib.util.spec_from_file_location("resource_policy_check", SCRIPT_PATH)
resource_policy_check = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(resource_policy_check)


def test_docker_arg_default_expands_from_image():
    lines = [
        "ARG PYTHON_BASE_IMAGE=python:3.12-slim@sha256:abc123",
        "FROM ${PYTHON_BASE_IMAGE} AS builder",
    ]

    build_args = resource_policy_check._docker_build_args(lines)

    assert build_args == {
        "PYTHON_BASE_IMAGE": "python:3.12-slim@sha256:abc123",
    }
    assert resource_policy_check._expand_docker_image(
        "${PYTHON_BASE_IMAGE}", build_args
    ) == "python:3.12-slim@sha256:abc123"


def test_unresolved_docker_arg_is_not_silently_removed():
    assert resource_policy_check._expand_docker_image(
        "${UNDECLARED_IMAGE}", {}
    ) == "${UNDECLARED_IMAGE}"


def test_compose_default_image_syntax_still_expands():
    assert resource_policy_check._expand_docker_image(
        "${POSTGRES_IMAGE:-postgres:17.6-alpine}", {}
    ) == "postgres:17.6-alpine"
