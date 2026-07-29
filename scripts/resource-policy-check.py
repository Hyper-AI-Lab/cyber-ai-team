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
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.strip()
            image = _image_from_line(stripped)
            if not image:
                continue
            expanded = re.sub(r"\$\{[^:}]+:-([^}]+)\}", r"\1", image)
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
            if image.endswith(":latest"):
                warnings.append(f"{path}:{lineno} uses floating latest image `{image}`.")
            if "docker.io/" in image and "frappe/erpnext" not in image:
                # Docker Hub is allowed; this warning-worthy pattern is kept as a
                # failure only for explicit proprietary markers in the reference.
                lowered = image.lower()
                if any(marker in lowered for marker in DENIED_LICENSE_MARKERS):
                    failures.append(f"{path}:{lineno} uses denied image `{image}`.")


def _image_from_line(line: str) -> str | None:
    if line.startswith("FROM "):
        parts = line.split()
        return parts[1] if len(parts) >= 2 else None
    match = re.match(r"image:\s*['\"]?([^'\"\s]+)", line)
    return match.group(1) if match else None


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
