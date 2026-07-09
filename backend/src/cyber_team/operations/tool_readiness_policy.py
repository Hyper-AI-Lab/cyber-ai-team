"""Shared readiness policy for side-effectful tools."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from cyber_team.config import settings


def tool_is_required_for_readiness(
    tool: dict[str, Any],
    *,
    required_provider_names: Iterable[str] | None = None,
) -> bool:
    """Return whether a non-live side-effectful tool should block readiness.

    Optional providers such as SMS, voice, chat, and CI are useful to show in the
    owner console, but they should not trip production-readiness or executive
    benchmarks unless the operator explicitly marks the provider as required.
    """

    required = {
        str(provider).strip().lower()
        for provider in (
            required_provider_names
            if required_provider_names is not None
            else settings.required_provider_names
        )
        if str(provider).strip()
    }
    name = str(tool.get("name") or tool.get("tool_name") or "").lower()
    category = str(tool.get("category") or "").lower()
    if category == "erpnext" or name.startswith("erpnext_"):
        return "erpnext" in required
    if name in {
        "crm_contact_update",
        "crm_deal_update",
        "task_create",
        "task_update",
        "ticket_create",
        "ticket_update",
        "procurement_request",
    }:
        return "erpnext" in required
    if name in {"send_email", "email_send"}:
        return "smtp" in required or "email" in required
    if name in {"send_sms", "sms_send"}:
        return bool({"sms", "twilio", "jasmin"} & required)
    if name in {"make_call", "call_make"}:
        return bool({"voice", "twilio", "asterisk"} & required)
    if name in {"send_message", "message_send"}:
        return bool({"slack", "telegram", "whatsapp"} & required)
    if name == "ci_trigger":
        return bool({"github", "github_ci", "ci"} & required)
    return True
