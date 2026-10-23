"""Keep the frontend CI/runtime and ERPNext security derivative aligned."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_frontend_lock_contains_reviewed_runtime_security_patches():
    manifest = json.loads((ROOT / "frontend/package.json").read_text())
    packages = json.loads((ROOT / "frontend/package-lock.json").read_text())["packages"]
    assert manifest["dependencies"]["next"] == "15.5.28"
    assert manifest["overrides"]["sharp"] == "0.35.5"
    assert manifest["overrides"]["source-map-js"] == "1.2.2"
    for name, version in (("next", "15.5.28"), ("sharp", "0.35.5"), ("source-map-js", "1.2.2")):
        assert packages[f"node_modules/{name}"]["version"] == version


def test_frontend_node_is_pinned_and_shared_with_ci():
    version = (ROOT / "frontend/.node-version").read_text().strip()
    assert version == "24.22.1"
    dockerfile = (ROOT / "frontend/Dockerfile").read_text()
    images = re.findall(r"^FROM (\S+)", dockerfile, re.MULTILINE)
    assert len(images) == 2
    assert images[0] == images[1]
    assert re.fullmatch(rf"node:{re.escape(version)}-alpine@sha256:[0-9a-f]{{64}}", images[0])
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "node-version-file: frontend/.node-version" in ci
    assert 'node-version: "20"' not in ci


def test_erpnext_patch_retains_application_and_only_copies_node():
    dockerfile = (ROOT / "deploy/erpnext-node/Dockerfile").read_text()
    images = re.findall(r"^FROM (\S+)", dockerfile, re.MULTILINE)
    assert len(images) == 2
    assert re.fullmatch(r"node:24\.22\.1-bookworm-slim@sha256:[0-9a-f]{64}", images[0])
    assert images[1] == (
        "frappe/erpnext@sha256:"
        "e1479ded7bfd78cd76015e62da703311a50c16ecca0ef381e851cfa52f0aab16"
    )
    copies = [line for line in dockerfile.splitlines() if line.startswith("COPY ")]
    assert copies == [
        "COPY --from=node --chown=frappe:frappe /usr/local/bin/node "
        "/home/frappe/.nvm/versions/node/v24.12.0/bin/node"
    ]
    assert "v24.22.1" in dockerfile
    assert "bench migrate" not in dockerfile
