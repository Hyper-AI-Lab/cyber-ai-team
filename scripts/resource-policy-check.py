#!/usr/bin/env python3
"""Static FOSS/resource policy checks for Cyber-Team.

The runtime database enforces tool-proposal resource metadata. This script keeps
repository-level dependencies and Docker references aligned with the same rule.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "config" / "foss-resource-inventory.json"
ALLOWED_LICENSE_MARKERS = {
    "apache",
    "bsd",
    "cc-by",
    "cc0",
    "isc",
    "lgpl",
    "mit",
    "mpl",
    "python",
    "unlicense",
    "zlib",
}
DENIED_LICENSE_MARKERS = {
    "commercial",
    "proprietary",
    "source-available",
    "trial",
}
PAID_RESOURCE_MARKERS = {
    "commercial_only",
    "commercial-only",
    "paid_only",
    "paid-only",
    "requires_paid_account",
    "saas_only",
    "saas-only",
    "subscription_only",
    "subscription-only",
}


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []
    inventory = _load_inventory(failures)
    _check_python_requirements(failures, inventory)
    _check_node_lock(failures, warnings, inventory)
    _check_docker_images(failures, warnings, inventory)
    _check_local_models(failures, inventory)
    _check_hosted_service_exceptions(failures, inventory)
    _check_static_tool_proposals(failures)
    if warnings:
        print("Resource policy warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    if failures:
        print("Resource policy failures:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("Resource policy check passed.")
    return 0


def _load_inventory(failures: list[str]) -> dict:
    if not INVENTORY_PATH.exists():
        failures.append(f"{INVENTORY_PATH} is missing.")
        return {}
    data = json.loads(INVENTORY_PATH.read_text())
    if data.get("policy") != "foss_only":
        failures.append("Resource inventory must declare policy=foss_only.")
    defaults = data.get("defaults") or {}
    for field in ("cost_model", "self_hostable", "data_sharing_risk"):
        if field not in defaults:
            failures.append(f"Resource inventory defaults omit `{field}`.")
    return data


def _normalise_package(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _check_python_requirements(failures: list[str], inventory: dict) -> None:
    requirements = ROOT / "backend" / "requirements.txt"
    reviewed = {
        _normalise_package(name): license_name
        for name, license_name in (inventory.get("python") or {}).items()
    }
    for lineno, raw_line in enumerate(requirements.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        lowered = line.lower()
        if "git+" in lowered or lowered.startswith(("http://", "https://")):
            failures.append(
                f"{requirements}:{lineno} uses a direct URL dependency; declare "
                "license and self-hostability before use."
            )
        name = re.split(r"[<>=!~\[]", line, maxsplit=1)[0]
        license_name = reviewed.get(_normalise_package(name))
        if not license_name:
            failures.append(
                f"{requirements}:{lineno} package `{name}` has no reviewed license "
                f"in {INVENTORY_PATH}."
            )
        elif any(marker in license_name.lower() for marker in DENIED_LICENSE_MARKERS):
            failures.append(f"{name} declares denied license `{license_name}`.")


def _check_node_lock(failures: list[str], warnings: list[str], inventory: dict) -> None:
    package_lock = ROOT / "frontend" / "package-lock.json"
    if not package_lock.exists():
        failures.append("frontend/package-lock.json is missing.")
        return
    data = json.loads(package_lock.read_text())
    root_package = (data.get("packages") or {}).get("") or {}
    direct = set(root_package.get("dependencies") or {}) | set(
        root_package.get("devDependencies") or {}
    )
    reviewed = set((inventory.get("node") or {}).keys())
    for name in sorted(direct - reviewed):
        failures.append(
            f"Direct Node dependency `{name}` has no reviewed license in {INVENTORY_PATH}."
        )
    packages = data.get("packages", {})
    for name, package in packages.items():
        if not name or name == "":
            continue
        license_value = str(package.get("license") or "").strip().lower()
        if not license_value:
            continue
        if any(marker in license_value for marker in DENIED_LICENSE_MARKERS):
            failures.append(f"{name} declares denied license `{license_value}`.")
            continue
        if not any(marker in license_value for marker in ALLOWED_LICENSE_MARKERS):
            warnings.append(f"{name} declares unreviewed license `{license_value}`.")


def _check_docker_images(failures: list[str], warnings: list[str], inventory: dict) -> None:
    files = [
        ROOT / "backend" / "Dockerfile",
        ROOT / "frontend" / "Dockerfile",
        ROOT / "docker-compose.yml",
    ]
    reviewed = inventory.get("docker") or []
    for path in files:
        if not path.exists():
            continue
        lines = path.read_text().splitlines()
        build_args = _docker_build_args(lines) if path.name == "Dockerfile" else {}
        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()
            image = _image_from_line(stripped)
            if not image:
                continue
            expanded = _expand_docker_image(image, build_args)
            match = next(
                (item for item in reviewed if item.get("match") in expanded),
                None,
            )
            if not match:
                failures.append(
                    f"{path}:{lineno} image `{image}` has no reviewed license/resource "
                    f"entry in {INVENTORY_PATH}."
                )
                continue
            for field in ("license", "purpose"):
                if not match.get(field):
                    failures.append(
                        f"Docker inventory entry `{match.get('match')}` omits `{field}`."
                    )
            if expanded.endswith(":latest"):
                warnings.append(f"{path}:{lineno} uses floating latest image `{expanded}`.")
            if "docker.io/" in expanded and "frappe/erpnext" not in expanded:
                # Docker Hub is allowed; this warning-worthy pattern is kept as a
                # failure only for explicit proprietary markers in the reference.
                lowered = expanded.lower()
                if any(marker in lowered for marker in DENIED_LICENSE_MARKERS):
                    failures.append(f"{path}:{lineno} uses denied image `{expanded}`.")


def _check_local_models(failures: list[str], inventory: dict) -> None:
    reviewed = inventory.get("models") or []
    for item in reviewed:
        for field in (
            "match",
            "license",
            "purpose",
            "cost_model",
            "self_hostable",
            "hosted_service_dependency",
            "data_sharing_risk",
        ):
            if field not in item:
                failures.append(f"Model inventory entry omits `{field}`: {item}")
        if item.get("cost_model") != "free_self_hosted":
            failures.append(f"Local model is not zero-spend/self-hosted: {item.get('match')}")
        if item.get("self_hostable") is not True:
            failures.append(f"Local model is not self-hostable: {item.get('match')}")
        if item.get("hosted_service_dependency") is not False:
            failures.append(
                f"Local model requires a hosted service: {item.get('match')}"
            )
        license_name = str(item.get("license") or "").lower()
        if not any(marker in license_name for marker in ALLOWED_LICENSE_MARKERS):
            failures.append(
                f"Local model has an unreviewed license: {item.get('match')}={license_name}"
            )

    references: list[tuple[Path, int, str]] = []
    files = [
        ROOT / "deploy" / "environments" / "staging.env.example",
        ROOT / "deploy" / "environments" / "production.env.example",
        ROOT / "docker-compose.yml",
        ROOT / "scripts" / "configure-autonomy-staging.py",
    ]
    pattern = re.compile(
        r"(?:LLM_LOCAL_MODEL_REPO(?:\}|\"|')?\s*(?::[-=]|=|:\s*)\s*[\"']?)"
        r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"
    )
    for path in files:
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            match = pattern.search(line)
            if match:
                references.append((path, lineno, match.group(1)))
    if not references:
        failures.append("No local model repository defaults were found for policy review.")
    for path, lineno, model in references:
        if not any(str(item.get("match") or "").startswith(model) for item in reviewed):
            failures.append(
                f"{path}:{lineno} local model `{model}` lacks reviewed FOSS metadata."
            )


def _check_hosted_service_exceptions(failures: list[str], inventory: dict) -> None:
    for name, item in (inventory.get("hosted_service_exceptions") or {}).items():
        for field in ("status", "data_sharing_risk", "automatic_paid_usage"):
            if field not in item:
                failures.append(f"Hosted service exception `{name}` omits `{field}`.")
        owner_authorized_metered = "owner_authorized" in str(
            item.get("status") or ""
        )
        if item.get("automatic_paid_usage") is not False and not owner_authorized_metered:
            failures.append(
                f"Hosted service exception `{name}` permits automatic paid usage without "
                "an owner-authorized status."
            )
        if owner_authorized_metered:
            for field in (
                "activation_guard",
                "automatic_paid_activation",
                "provider_side_budget_required",
            ):
                if field not in item:
                    failures.append(
                        f"Owner-authorized hosted service exception `{name}` omits "
                        f"`{field}`."
                    )
            if item.get("automatic_paid_activation") is not False:
                failures.append(
                    f"Owner-authorized hosted service exception `{name}` must prohibit "
                    "automatic activation."
                )
            if item.get("provider_side_budget_required") is not True:
                failures.append(
                    f"Owner-authorized hosted service exception `{name}` must require a "
                    "provider-side budget."
                )


def _image_from_line(line: str) -> str | None:
    if line.startswith("FROM "):
        parts = line.split()
        return parts[1] if len(parts) >= 2 else None
    match = re.match(r"image:\s*['\"]?([^'\"\s]+)", line)
    return match.group(1) if match else None


def _docker_build_args(lines: list[str]) -> dict[str, str]:
    """Return Docker ARG defaults available to FROM instructions."""
    defaults: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        match = re.match(r"ARG\s+([A-Za-z_][A-Za-z0-9_]*)=(\S+)$", line)
        if match:
            defaults[match.group(1)] = match.group(2)
    return defaults


def _expand_docker_image(image: str, build_args: dict[str, str]) -> str:
    """Resolve Compose defaults and declared Docker ARG defaults in an image."""
    expanded = re.sub(r"\$\{[^:}]+:-([^}]+)\}", r"\1", image)

    def replace_braced(match: re.Match[str]) -> str:
        name = match.group(1)
        return build_args.get(name, match.group(0))

    expanded = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace_braced, expanded)
    return re.sub(
        r"\$([A-Za-z_][A-Za-z0-9_]*)",
        lambda match: build_args.get(match.group(1), match.group(0)),
        expanded,
    )


def _check_static_tool_proposals(failures: list[str]) -> None:
    candidate_files = [
        *(ROOT / "docs").glob("**/*.json"),
        *(ROOT / "deploy").glob("**/*.json"),
    ]
    for path in candidate_files:
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        text = json.dumps(data, sort_keys=True).lower()
        if any(marker in text for marker in PAID_RESOURCE_MARKERS):
            failures.append(
                f"{path} contains paid/SaaS-only resource metadata; mark as "
                "optional future work or replace with FOSS/self-hosted resources."
            )


if __name__ == "__main__":
    raise SystemExit(main())
