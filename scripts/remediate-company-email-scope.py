#!/usr/bin/env python3
"""Supersede company intelligence derived exclusively from unrelated mailbox mail."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict

from cyber_team.clock import utc_now
from cyber_team.comms.email_scope import is_intended_recipient, normalize_email_address
from cyber_team.config import settings
from cyber_team.db import async_session
from cyber_team.db.models import (
    CompanyClaim,
    CompanyClaimObservation,
    CompanySignal,
    EvidenceArtifact,
    InboundEmailMessage,
)
from sqlalchemy import select


async def run(*, apply: bool, target_address: str | None = None) -> dict:
    target = normalize_email_address(target_address or settings.inbound_email_address)
    if not target:
        raise ValueError("INBOUND_EMAIL_ADDRESS or --target-address is required")

    report = {
        "mode": "apply" if apply else "dry_run",
        "target_address": target,
        "messages_scanned": 0,
        "messages_in_scope": 0,
        "messages_out_of_scope": 0,
        "signals_matched": 0,
        "signals_superseded": 0,
        "pending_signals_blocked": 0,
        "artifacts_matched": 0,
        "artifacts_marked_out_of_scope": 0,
        "claims_superseded": 0,
        "owner_locked_claims_preserved": 0,
    }
    now = utc_now()
    async with async_session() as session:
        messages = (await session.execute(select(InboundEmailMessage))).scalars().all()
        report["messages_scanned"] = len(messages)
        out_of_scope_message_ids = {
            item.id
            for item in messages
            if not is_intended_recipient(
                target_address=target,
                to_addresses=item.to_addresses,
                cc_addresses=item.cc_addresses,
                metadata=item.metadata_,
            )
        }
        report["messages_out_of_scope"] = len(out_of_scope_message_ids)
        report["messages_in_scope"] = len(messages) - len(out_of_scope_message_ids)

        if not out_of_scope_message_ids:
            return report

        signals = (
            await session.execute(
                select(CompanySignal).where(
                    CompanySignal.signal_type == "email.received",
                    CompanySignal.external_id.in_(out_of_scope_message_ids),
                )
            )
        ).scalars().all()
        out_of_scope_signal_ids = {item.id for item in signals}
        report["signals_matched"] = len(signals)
        signals_to_update = [
            item
            for item in signals
            if item.status != "processed"
            or item.disposition != "superseded"
            or item.claim_extraction_status != "blocked"
            or item.claim_extraction_error != "out_of_scope_recipient"
            or item.claim_extraction_available_at is not None
            or item.claim_extraction_lease_owner is not None
            or item.claim_extraction_lease_expires_at is not None
        ]
        report["signals_superseded"] = len(signals_to_update)
        report["pending_signals_blocked"] = sum(
            1 for item in signals if item.status == "pending"
        )

        artifacts = (
            await session.execute(
                select(EvidenceArtifact).where(
                    EvidenceArtifact.signal_id.in_(out_of_scope_signal_ids)
                )
            )
        ).scalars().all()
        out_of_scope_evidence_ids = {item.id for item in artifacts}
        report["artifacts_matched"] = len(artifacts)
        artifacts_to_update = [
            item
            for item in artifacts
            if (item.metadata_ or {}).get("company_scope", {}).get("status")
            != "out_of_scope"
            or (item.metadata_ or {}).get("company_scope", {}).get("target_address")
            != target
        ]
        report["artifacts_marked_out_of_scope"] = len(artifacts_to_update)

        observations = (
            await session.execute(select(CompanyClaimObservation))
        ).scalars().all()
        observations_by_claim: dict[str, list[CompanyClaimObservation]] = defaultdict(list)
        for observation in observations:
            observations_by_claim[observation.claim_id].append(observation)

        claims = (await session.execute(select(CompanyClaim))).scalars().all()
        claims_to_supersede: list[CompanyClaim] = []
        for claim in claims:
            related = observations_by_claim.get(claim.id, [])
            if not related or claim.epistemic_state == "superseded":
                continue
            exclusively_out_of_scope = all(
                observation.signal_id in out_of_scope_signal_ids
                or observation.evidence_id in out_of_scope_evidence_ids
                for observation in related
            )
            if not exclusively_out_of_scope:
                continue
            if claim.owner_locked:
                report["owner_locked_claims_preserved"] += 1
                continue
            claims_to_supersede.append(claim)
        report["claims_superseded"] = len(claims_to_supersede)

        if not apply:
            await session.rollback()
            return report

        for signal in signals_to_update:
            signal.status = "processed"
            signal.disposition = "superseded"
            signal.claim_extraction_status = "blocked"
            signal.claim_extraction_error = "out_of_scope_recipient"
            signal.claim_extraction_available_at = None
            signal.claim_extraction_lease_owner = None
            signal.claim_extraction_lease_expires_at = None
            signal.processed_at = signal.processed_at or now
        for artifact in artifacts_to_update:
            artifact.metadata_ = {
                **(artifact.metadata_ or {}),
                "company_scope": {
                    "status": "out_of_scope",
                    "reason": "recipient_did_not_match_configured_inbound_address",
                    "target_address": target,
                    "evaluated_at": now.isoformat(),
                },
            }
        for claim in claims_to_supersede:
            claim.epistemic_state = "superseded"
            claim.valid_until = now
        await session.commit()
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--target-address")
    parser.add_argument("--output")
    args = parser.parse_args()
    report = asyncio.run(
        run(apply=args.apply, target_address=args.target_address)
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    print(rendered)


if __name__ == "__main__":
    main()
