#!/usr/bin/env python3
"""High-confidence secret scanner for CI and local quality gates."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 2_000_000
SKIP_PARTS = {
    ".git",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
}
SECRET_PATTERNS = [
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}\b")),
    ("github-fine-grained-token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{80,}\b")),
    ("openai-token", re.compile(r"\bsk-[A-Za-z0-9_-]{32,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
]


def candidate_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    files = []
    for line in result.stdout.splitlines():
        path = ROOT / line
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        if path.is_file():
            files.append(path)
    return files


def scan_file(path: Path) -> list[tuple[str, int]]:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return []
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    findings = []
    for name, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(content):
            line = content.count("\n", 0, match.start()) + 1
            findings.append((name, line))
    return findings


def main() -> int:
    findings: list[tuple[Path, str, int]] = []
    for path in candidate_files():
        for name, line in scan_file(path):
            findings.append((path.relative_to(ROOT), name, line))

    if findings:
        print("Potential committed secrets detected:", file=sys.stderr)
        for path, name, line in findings:
            print(f"- {path}:{line} [{name}]", file=sys.stderr)
        return 1

    print("No high-confidence secrets detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
