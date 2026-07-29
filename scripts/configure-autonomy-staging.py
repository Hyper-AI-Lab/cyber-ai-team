#!/usr/bin/env python3
"""Idempotently configure non-secret v3 staging flags and required random secrets."""

from __future__ import annotations

import argparse
import secrets
from pathlib import Path


VALUES = {
    "APP_VERSION": "0.3.0-rc2",
    "COMPANY_AUTONOMY_ENABLED": "true",
    "COMPANY_AUTONOMY_TEMPORAL_SCHEDULE_ENABLED": "true",
    "LEGACY_GOVERNOR_RULE_PROPOSER_ENABLED": "false",
    "SEARXNG_ENABLED": "true",
}
SECRET_KEYS = (
    "SEARXNG_SECRET",
    "ERPNEXT_WEBHOOK_SECRET",
    "LLM_LOCAL_API_KEY",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "env_file",
        nargs="?",
        default="deploy/environments/staging.env",
    )
    parser.add_argument(
        "--activate-policy-gated",
        action="store_true",
        help="Promote side effects from manual-only shadow mode after acceptance passes.",
    )
    parser.add_argument(
        "--confirm-zero-cost-hosted-llm",
        action="store_true",
        help="Confirm that the configured hosted LLM route currently incurs no owner spend.",
    )
    parser.add_argument(
        "--enable-local-llm-fallback",
        action="store_true",
        help="Enable the retained local Qwen fallback after its service is validated.",
    )
    args = parser.parse_args()
    path = Path(args.env_file)
    if not path.is_file():
        raise SystemExit(f"Environment file not found: {path}")

    lines = path.read_text(encoding="utf-8").splitlines()
    current = {
        key.strip(): value.strip().strip("'\"")
        for line in lines
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
        for key, value in [line.split("=", 1)]
    }
    updates = dict(VALUES)
    if args.activate_policy_gated:
        updates["AUTONOMY_SIDE_EFFECT_MODE"] = "policy_gated"
    if args.confirm_zero_cost_hosted_llm:
        updates["LLM_EXTERNAL_ZERO_COST_CONFIRMED"] = "true"
    if args.enable_local_llm_fallback:
        updates.update(
            {
                "LLM_LOCAL_FALLBACK_ENABLED": "true",
                "LLM_LOCAL_MODEL": "openai/ggml-org/Qwen3-1.7B-GGUF:Q4_K_M",
                "LLM_LOCAL_MODEL_REPO": "ggml-org/Qwen3-1.7B-GGUF:Q4_K_M",
                "LLM_LOCAL_TIMEOUT_SECONDS": "180",
                "LLM_LOCAL_MAX_TOKENS": "1024",
                "LOCAL_MODEL_CACHE_VOLUME": "cyber-team_local-model-cache",
            }
        )
    for key in SECRET_KEYS:
        value = current.get(key, "")
        if not value or value.startswith(("replace-with-", "changeme-")):
            updates[key] = secrets.token_urlsafe(48)

    output = []
    remaining = dict(updates)
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)
    output.extend(f"{key}={value}" for key, value in remaining.items())
    path.write_text("\n".join(output) + "\n", encoding="utf-8")
    path.chmod(0o600)
    mode = "policy_gated" if args.activate_policy_gated else "unchanged"
    print(
        f"Configured v3 staging flags in {path}; "
        f"side-effect mode={mode}; secret values were not printed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
