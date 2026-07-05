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
    _check_python_requirements(failures)
    _check_node_lock(failures, warnings)
    _check_docker_images(failures, warnings)
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


def _check_python_requirements(failures: list[str]) -> None:
    requirements = ROOT / "backend" / "requirements.txt"
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


def _check_node_lock(failures: list[str], warnings: list[str]) -> None:
    package_lock = ROOT / "frontend" / "package-lock.json"
    if not package_lock.exists():
        failures.append("frontend/package-lock.json is missing.")
        return
    data = json.loads(package_lock.read_text())
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


def _check_docker_images(failures: list[str], warnings: list[str]) -> None:
    files = [
        ROOT / "backend" / "Dockerfile",
        ROOT / "frontend" / "Dockerfile",
        ROOT / "docker-compose.yml",
    ]
    for path in files:
        if not path.exists():
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.strip()
            image = _image_from_line(stripped)
            if not image:
                continue
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
