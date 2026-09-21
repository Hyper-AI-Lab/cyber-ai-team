"""Recipient scoping for shared inbound mailboxes."""

from __future__ import annotations

from collections.abc import Iterable
from email.utils import getaddresses
from typing import Any


def normalize_email_address(value: str | None) -> str:
    """Return a comparable mailbox address without display-name decoration."""
    if not value:
        return ""
    addresses = getaddresses([value])
    address = addresses[0][1] if addresses else value
    return address.strip().lower()


def normalized_addresses(values: Iterable[str] | None) -> set[str]:
    result: set[str] = set()
    for value in values or []:
        for _, address in getaddresses([str(value)]):
            normalized = normalize_email_address(address)
            if normalized:
                result.add(normalized)
    return result


def recipient_addresses(
    *,
    to_addresses: Iterable[str] | None,
    cc_addresses: Iterable[str] | None,
    metadata: dict[str, Any] | None = None,
) -> set[str]:
    """Collect visible and envelope recipients from a parsed or stored message."""
    result = normalized_addresses(to_addresses) | normalized_addresses(cc_addresses)
    metadata = metadata if isinstance(metadata, dict) else {}
    result |= normalized_addresses(metadata.get("delivery_addresses") or [])
    for key in ("raw_to", "raw_cc", "delivered_to", "x_original_to", "envelope_to"):
        value = metadata.get(key)
        if isinstance(value, list):
            result |= normalized_addresses(value)
        elif value:
            result |= normalized_addresses([str(value)])
    return result


def is_intended_recipient(
    *,
    target_address: str | None,
    to_addresses: Iterable[str] | None,
    cc_addresses: Iterable[str] | None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Require an exact configured recipient match before company ingestion."""
    target = normalize_email_address(target_address)
    if not target:
        return False
    return target in recipient_addresses(
        to_addresses=to_addresses,
        cc_addresses=cc_addresses,
        metadata=metadata,
    )
