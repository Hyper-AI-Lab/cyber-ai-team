"""Add canonical claim observations and normalized operating-model evidence.

Revision ID: 0024_vision_integrity_canonical_evidence
Revises: 0023_lifecycle_assessment_compaction
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024_vision_integrity_canonical_evidence"
down_revision: str | None = "0023_lifecycle_assessment_compaction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence_payload_artifacts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("company_namespace", sa.String(length=200), nullable=False),
        sa.Column("artifact_type", sa.String(length=100), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("encoding", sa.String(length=30), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("raw_size", sa.Integer(), nullable=False),
        sa.Column("compressed_size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash", name="uq_evidence_payload_artifacts_hash"),
    )
    op.create_index(
        op.f("ix_evidence_payload_artifacts_company_namespace"),
        "evidence_payload_artifacts",
        ["company_namespace"],
    )
    op.create_index(
        op.f("ix_evidence_payload_artifacts_artifact_type"),
        "evidence_payload_artifacts",
        ["artifact_type"],
    )
    op.create_index(
        op.f("ix_evidence_payload_artifacts_content_hash"),
        "evidence_payload_artifacts",
        ["content_hash"],
    )
    op.create_index(
        op.f("ix_evidence_payload_artifacts_created_at"),
        "evidence_payload_artifacts",
        ["created_at"],
    )

    op.add_column(
        "company_claims",
        sa.Column("semantic_hash", sa.String(length=64), nullable=True),
    )
    op.create_index(
        op.f("ix_company_claims_semantic_hash"),
        "company_claims",
        ["semantic_hash"],
        unique=True,
    )
    op.create_table(
        "company_claim_observations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("claim_id", sa.String(length=64), nullable=False),
        sa.Column("evidence_id", sa.String(length=64), nullable=True),
        sa.Column("source_reference", sa.String(length=240), nullable=True),
        sa.Column("signal_id", sa.String(length=64), nullable=True),
        sa.Column("observation_hash", sa.String(length=64), nullable=False),
        sa.Column("epistemic_state", sa.String(length=30), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("trust_class", sa.String(length=30), nullable=False),
        sa.Column("sensitivity", sa.String(length=30), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["claim_id"], ["company_claims.id"]),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence_artifacts.id"]),
        sa.ForeignKeyConstraint(["signal_id"], ["company_signals.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "observation_hash",
            name="uq_company_claim_observations_hash",
        ),
    )
    for column in (
        "claim_id",
        "evidence_id",
        "source_reference",
        "signal_id",
        "observation_hash",
        "epistemic_state",
        "trust_class",
        "sensitivity",
        "observed_at",
        "created_at",
    ):
        op.create_index(
            op.f(f"ix_company_claim_observations_{column}"),
            "company_claim_observations",
            [column],
        )

    op.add_column(
        "operating_model_revisions",
        sa.Column("evidence_archive_id", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        "fk_operating_model_revisions_evidence_archive",
        "operating_model_revisions",
        "evidence_payload_artifacts",
        ["evidence_archive_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_operating_model_revisions_evidence_archive_id"),
        "operating_model_revisions",
        ["evidence_archive_id"],
    )
    op.create_table(
        "operating_model_revision_claims",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("operating_model_revision_id", sa.String(length=64), nullable=False),
        sa.Column("claim_id", sa.String(length=64), nullable=False),
        sa.Column("semantic_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["operating_model_revision_id"], ["operating_model_revisions.id"]
        ),
        sa.ForeignKeyConstraint(["claim_id"], ["company_claims.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operating_model_revision_id",
            "claim_id",
            name="uq_operating_model_revision_claim",
        ),
    )
    for column in (
        "operating_model_revision_id",
        "claim_id",
        "semantic_hash",
        "created_at",
    ):
        op.create_index(
            op.f(f"ix_operating_model_revision_claims_{column}"),
            "operating_model_revision_claims",
            [column],
        )

    op.add_column(
        "operating_domain_revisions",
        sa.Column("evidence_archive_id", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        "fk_operating_domain_revisions_evidence_archive",
        "operating_domain_revisions",
        "evidence_payload_artifacts",
        ["evidence_archive_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_operating_domain_revisions_evidence_archive_id"),
        "operating_domain_revisions",
        ["evidence_archive_id"],
    )
    op.create_table(
        "operating_domain_revision_evidence",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("domain_revision_id", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("evidence_id", sa.String(length=64), nullable=True),
        sa.Column("matched_selectors", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["domain_revision_id"], ["operating_domain_revisions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "domain_revision_id",
            "evidence_hash",
            name="uq_operating_domain_revision_evidence",
        ),
    )
    for column in (
        "domain_revision_id",
        "source_type",
        "source_id",
        "evidence_id",
        "evidence_hash",
        "created_at",
    ):
        op.create_index(
            op.f(f"ix_operating_domain_revision_evidence_{column}"),
            "operating_domain_revision_evidence",
            [column],
        )


def downgrade() -> None:
    op.drop_table("operating_domain_revision_evidence")
    op.drop_index(
        op.f("ix_operating_domain_revisions_evidence_archive_id"),
        table_name="operating_domain_revisions",
    )
    op.drop_constraint(
        "fk_operating_domain_revisions_evidence_archive",
        "operating_domain_revisions",
        type_="foreignkey",
    )
    op.drop_column("operating_domain_revisions", "evidence_archive_id")
    op.drop_table("operating_model_revision_claims")
    op.drop_index(
        op.f("ix_operating_model_revisions_evidence_archive_id"),
        table_name="operating_model_revisions",
    )
    op.drop_constraint(
        "fk_operating_model_revisions_evidence_archive",
        "operating_model_revisions",
        type_="foreignkey",
    )
    op.drop_column("operating_model_revisions", "evidence_archive_id")
    op.drop_table("company_claim_observations")
    op.drop_index(
        op.f("ix_company_claims_semantic_hash"),
        table_name="company_claims",
    )
    op.drop_column("company_claims", "semantic_hash")
    op.drop_table("evidence_payload_artifacts")
