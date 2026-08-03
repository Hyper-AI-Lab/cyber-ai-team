#!/usr/bin/env python3
"""Fail CI when Cyber-Team gains a host Google Cloud credential path."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
SCANNED_ROOTS = {
    ".github",
    "backend",
    "deploy",
    "frontend",
    "policies",
    "scripts",
}
SCANNED_FILES = {
    ".env.example",
    "docker-compose.yml",
    "start.sh",
}
MAX_FILE_BYTES = 2_000_000
PROHIBITED = (
    (
        "interactive-google-auth",
        re.compile(r"\bgcloud\s+auth\s+(?:application-default\s+)?login\b", re.I),
    ),
    (
        "automatic-google-billing-link",
        re.compile(r"\bgcloud\s+billing\s+projects\s+link\b", re.I),
    ),
    (
        "automatic-google-api-enable",
        re.compile(r"\bgcloud\s+services\s+enable\b", re.I),
    ),
    (
        "google-account-selection",
        re.compile(r"\bgcloud\s+config\s+set\s+account\b", re.I),
    ),
    (
        "google-credential-env",
        re.compile(
            r"\b(?:GOOGLE_APPLICATION_CREDENTIALS|"
            r"CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE)\b"
        ),
    ),
    (
        "google-credential-file",
        re.compile(
            r"(?:application_default_credentials\.json|"
            r"/\.config/gcloud(?:/|\b)|/\.gsutil/credstore)",
            re.I,
        ),
    ),
    (
        "host-root-mount",
        re.compile(r"(?:^|[\s\"'])/root(?:/[^:\s\"']*)?:", re.M),
    ),
)


def candidate_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    candidates: list[Path] = []
    for name in result.stdout.splitlines():
        relative = Path(name)
        if relative.name in SCANNED_FILES or (
            relative.parts and relative.parts[0] in SCANNED_ROOTS
        ):
            path = (ROOT / relative).resolve()
            if path != SELF and path.is_file() and path.stat().st_size <= MAX_FILE_BYTES:
                candidates.append(path)
    return candidates


def main() -> int:
    findings: list[tuple[Path, int, str]] = []
    for path in candidate_files():
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for name, pattern in PROHIBITED:
            for match in pattern.finditer(content):
                line = content.count("\n", 0, match.start()) + 1
                findings.append((path.relative_to(ROOT), line, name))

    if findings:
        print("Cyber-Team Google Cloud isolation violations:", file=sys.stderr)
        for path, line, name in findings:
            print(f"- {path}:{line} [{name}]", file=sys.stderr)
        return 1

    print("Google Cloud isolation policy passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
