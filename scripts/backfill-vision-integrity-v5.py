#!/usr/bin/env python3
"""Canonicalize company evidence and compact oversized operating-model history."""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
from collections import defaultdict
from typing import Any

from sqlalchemy import select

from cyber_team.clock import utc_now
from cyber_team.company.intelligence import CompanyIntelligenceService
from cyber_team.db import async_session
from cyber_team.db.models import (
    CompanyClaim,
    CompanyClaimObservation,
    CompanyModelRevision,
    EvidenceArtifact,
    EvidencePayloadArtifact,
    OperatingDomain,
    OperatingDomainRevision,
    OperatingDomainRevisionEvidence,
    OperatingModelRevision,
    OperatingModelRevisionClaim,
)

EVIDENCE_LIST_LIMIT = 250
INLINE_PAYLOAD_LIMIT = 64 * 1024
STATE_RANK = {
    "verified": 6,
    "inferred": 5,
    "hypothesis": 4,
    "disputed": 3,
    "unknown": 2,
    "superseded": 1,
}
TRUST_RANK = {
    "owner_locked": 7,
    "canonical": 6,
    "authenticated": 5,
    "internal": 4,
    "public_primary": 3,
    "public_secondary": 2,
    "untrusted": 1,
}


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def bounded_unique(values: list[Any], limit: int = EVIDENCE_LIST_LIMIT) -> list[str]:
    return list(dict.fromkeys(str(item) for item in values if item))[-limit:]


def compact_evidence(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_type": str(item.get("source_type") or "unknown")[:80],
        "source_id": str(item.get("source_id") or "unknown")[:64],
        "evidence_ids": bounded_unique(list(item.get("evidence_ids") or []), 25),
        "confidence": max(0.0, min(float(item.get("confidence") or 0), 1.0)),
        "matched_selectors": bounded_unique(
            list(item.get("matched_selectors") or []), 25
        ),
    }


def compact_summary(summary: dict[str, Any], archive_id: str | None) -> dict[str, Any]:
    result = dict(summary or {})
    compact_domains = []
    for raw_domain in list(result.get("domains") or []):
        if not isinstance(raw_domain, dict):
            continue
        domain = {key: value for key, value in raw_domain.items() if key != "evidence"}
        domain["evidence_ids"] = bounded_unique(
            list(domain.get("evidence_ids") or [])
        )
        domain["evidence"] = [
            compact_evidence(item)
            for item in list(raw_domain.get("evidence") or [])[:50]
            if isinstance(item, dict)
        ]
        compact_domains.append(domain)
    result["domains"] = compact_domains
    if archive_id:
        result["evidence_archive"] = {
            "artifact_id": archive_id,
            "encoding": "gzip+json",
            "contains": ["summary", "evidence_ids"],
        }
    return result


async def run(*, apply: bool) -> dict[str, Any]:
    report: dict[str, Any] = {
        "mode": "apply" if apply else "dry_run",
        "claims_scanned": 0,
        "semantic_claims": 0,
        "claims_superseded": 0,
        "observations_created": 0,
        "model_claim_references_rewritten": 0,
        "operating_model_revisions_compacted": 0,
        "domain_revisions_compacted": 0,
        "revision_claim_links_created": 0,
        "domain_evidence_links_created": 0,
        "payload_artifacts_created": 0,
    }
    now = utc_now()
    async with async_session() as session:
        claims = (await session.execute(select(CompanyClaim))).scalars().all()
        evidence_rows = (
            await session.execute(select(EvidenceArtifact.id, EvidenceArtifact.signal_id))
        ).all()
        evidence_signal = {row.id: row.signal_id for row in evidence_rows}
        observation_hashes = set(
            (
                await session.execute(
                    select(CompanyClaimObservation.observation_hash)
                )
            ).scalars()
        )
        groups: dict[str, list[CompanyClaim]] = defaultdict(list)
        for claim in claims:
            semantic_hash = CompanyIntelligenceService._semantic_claim_hash(
                namespace=claim.company_namespace,
                subject=claim.subject,
                predicate=claim.predicate,
                value=claim.value,
            )
            groups[semantic_hash].append(claim)

        report["claims_scanned"] = len(claims)
        report["semantic_claims"] = len(groups)
        canonical_by_id: dict[str, str] = {}
        for semantic_hash, rows in groups.items():
            rows.sort(
                key=lambda item: (
                    bool(item.owner_locked),
                    STATE_RANK.get(item.epistemic_state, 0),
                    TRUST_RANK.get(item.trust_class, 0),
                    float(item.confidence or 0),
                    item.created_at,
                    item.id,
                ),
                reverse=True,
            )
            canonical = rows[0]
            canonical.semantic_hash = semantic_hash
            all_evidence: list[str] = []
            for row in rows:
                canonical_by_id[row.id] = canonical.id
                references = list(row.evidence_ids or []) or [None]
                all_evidence.extend(str(item) for item in references if item)
                for reference in references:
                    observation_hash = stable_hash(
                        {
                            "semantic_hash": semantic_hash,
                            "source_reference": reference,
                            "legacy_claim_id": row.id if reference is None else None,
                        }
                    )
                    if observation_hash in observation_hashes:
                        continue
                    observation_hashes.add(observation_hash)
                    valid_evidence_id = (
                        str(reference)
                        if reference and str(reference) in evidence_signal
                        else None
                    )
                    session.add(
                        CompanyClaimObservation(
                            id=f"claimobs_{observation_hash[:32]}",
                            claim_id=canonical.id,
                            evidence_id=valid_evidence_id,
                            source_reference=str(reference)[:240] if reference else row.id,
                            signal_id=(
                                evidence_signal.get(valid_evidence_id)
                                if valid_evidence_id
                                else None
                            ),
                            observation_hash=observation_hash,
                            epistemic_state=row.epistemic_state,
                            confidence=float(row.confidence or 0),
                            trust_class=row.trust_class,
                            sensitivity=row.sensitivity,
                            observed_at=row.valid_from,
                            created_at=row.created_at,
                        )
                    )
                    report["observations_created"] += 1
                if row.id == canonical.id:
                    continue
                row.semantic_hash = None
                if row.epistemic_state != "superseded":
                    row.epistemic_state = "superseded"
                    row.valid_until = row.valid_until or now
                    row.supersedes_id = canonical.id
                    report["claims_superseded"] += 1
            canonical.evidence_ids = bounded_unique(all_evidence, 100)

        model_revisions = (
            await session.execute(select(CompanyModelRevision)
        )).scalars().all()
        for model in model_revisions:
            rewritten = bounded_unique(
                [canonical_by_id.get(str(item), str(item)) for item in model.claim_ids or []],
                1000,
            )
            if rewritten != list(model.claim_ids or []):
                model.claim_ids = rewritten
                report["model_claim_references_rewritten"] += 1

        artifact_by_hash = {
            row.content_hash: row
            for row in (
                await session.execute(select(EvidencePayloadArtifact))
            ).scalars()
        }
        existing_revision_claims = set(
            (
                await session.execute(
                    select(
                        OperatingModelRevisionClaim.operating_model_revision_id,
                        OperatingModelRevisionClaim.claim_id,
                    )
                )
            ).all()
        )
        existing_domain_evidence = set(
            (
                await session.execute(
                    select(
                        OperatingDomainRevisionEvidence.domain_revision_id,
                        OperatingDomainRevisionEvidence.evidence_hash,
                    )
                )
            ).all()
        )

        async def archive(
            namespace: str,
            artifact_type: str,
            payload: dict[str, Any],
        ) -> EvidencePayloadArtifact:
            raw = canonical_json(payload)
            content_hash = hashlib.sha256(raw).hexdigest()
            existing = artifact_by_hash.get(content_hash)
            if existing:
                return existing
            compressed = gzip.compress(raw, compresslevel=9, mtime=0)
            artifact = EvidencePayloadArtifact(
                id=f"payload_{content_hash[:32]}",
                company_namespace=namespace,
                artifact_type=artifact_type,
                content_hash=content_hash,
                encoding="gzip+json",
                payload=compressed,
                raw_size=len(raw),
                compressed_size=len(compressed),
            )
            session.add(artifact)
            artifact_by_hash[content_hash] = artifact
            report["payload_artifacts_created"] += 1
            return artifact

        operating_revisions = (
            await session.execute(
                select(OperatingModelRevision).order_by(OperatingModelRevision.revision)
            )
        ).scalars().all()
        models_by_id = {item.id: item for item in model_revisions}
        claims_by_id = {item.id: item for item in claims}
        for revision in operating_revisions:
            original_summary = dict(revision.summary or {})
            original_evidence = list(revision.evidence_ids or [])
            raw_size = len(
                canonical_json(
                    {"summary": original_summary, "evidence_ids": original_evidence}
                )
            )
            archive_item = None
            if revision.evidence_archive_id is None and (
                raw_size > INLINE_PAYLOAD_LIMIT
                or len(original_evidence) > EVIDENCE_LIST_LIMIT
            ):
                archive_item = await archive(
                    revision.company_namespace,
                    "operating_model_revision",
                    {"summary": original_summary, "evidence_ids": original_evidence},
                )
                revision.evidence_archive_id = archive_item.id
            compacted = compact_summary(
                original_summary,
                archive_item.id if archive_item else revision.evidence_archive_id,
            )
            bounded_evidence = bounded_unique(original_evidence)
            if compacted != original_summary or bounded_evidence != original_evidence:
                revision.summary = compacted
                revision.evidence_ids = bounded_evidence
                report["operating_model_revisions_compacted"] += 1

            company_model = models_by_id.get(revision.company_model_revision_id)
            for legacy_claim_id in list(company_model.claim_ids or []) if company_model else []:
                claim_id = canonical_by_id.get(str(legacy_claim_id), str(legacy_claim_id))
                claim = claims_by_id.get(claim_id)
                if not claim or (revision.id, claim_id) in existing_revision_claims:
                    continue
                semantic_hash = (
                    claim.semantic_hash
                    or CompanyIntelligenceService._semantic_claim_hash(
                        namespace=claim.company_namespace,
                        subject=claim.subject,
                        predicate=claim.predicate,
                        value=claim.value,
                    )
                )
                session.add(
                    OperatingModelRevisionClaim(
                        id=f"opmodelclaim_{stable_hash([revision.id, claim_id])[:32]}",
                        operating_model_revision_id=revision.id,
                        claim_id=claim_id,
                        semantic_hash=semantic_hash,
                    )
                )
                existing_revision_claims.add((revision.id, claim_id))
                report["revision_claim_links_created"] += 1

            evidence_by_domain = {
                str(item.get("key")): [
                    compact_evidence(match)
                    for match in list(item.get("evidence") or [])[:50]
                    if isinstance(match, dict)
                ]
                for item in list(compacted.get("domains") or [])
                if isinstance(item, dict) and item.get("key")
            }
            domain_rows = (
                await session.execute(
                    select(
                        OperatingDomainRevision,
                        OperatingDomain.domain_key,
                        OperatingDomain.current_revision,
                    )
                    .join(OperatingDomain, OperatingDomain.id == OperatingDomainRevision.domain_id)
                    .where(
                        OperatingDomainRevision.operating_model_revision_id == revision.id
                    )
                )
            ).all()
            for domain_revision, domain_key, current_revision in domain_rows:
                full_ids = list(domain_revision.evidence_ids or [])
                if (
                    domain_revision.evidence_archive_id is None
                    and len(full_ids) > EVIDENCE_LIST_LIMIT
                ):
                    domain_archive = await archive(
                        revision.company_namespace,
                        "operating_domain_revision",
                        {
                            "domain_revision_id": domain_revision.id,
                            "evidence_ids": full_ids,
                        },
                    )
                    domain_revision.evidence_archive_id = domain_archive.id
                    domain_revision.evidence_ids = bounded_unique(full_ids)
                    report["domain_revisions_compacted"] += 1
                if domain_revision.revision != current_revision:
                    continue
                for evidence in evidence_by_domain.get(str(domain_key), []):
                    evidence_id = next(iter(evidence.get("evidence_ids") or []), None)
                    evidence_hash = stable_hash(
                        {
                            "source_type": evidence["source_type"],
                            "source_id": evidence["source_id"],
                            "matched_selectors": evidence["matched_selectors"],
                        }
                    )
                    key = (domain_revision.id, evidence_hash)
                    if key in existing_domain_evidence:
                        continue
                    session.add(
                        OperatingDomainRevisionEvidence(
                            id=f"domainrevev_{stable_hash(key)[:32]}",
                            domain_revision_id=domain_revision.id,
                            source_type=evidence["source_type"],
                            source_id=evidence["source_id"],
                            evidence_id=evidence_id,
                            matched_selectors=evidence["matched_selectors"],
                            confidence=evidence["confidence"],
                            evidence_hash=evidence_hash,
                        )
                    )
                    existing_domain_evidence.add(key)
                    report["domain_evidence_links_created"] += 1

        if apply:
            await session.commit()
        else:
            await session.rollback()
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = asyncio.run(run(apply=bool(args.apply)))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
