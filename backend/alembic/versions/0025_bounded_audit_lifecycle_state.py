"""Add bounded audit rollups and current lifecycle state.

Revision ID: 0025_bounded_audit_lifecycle_state
Revises: 0024_vision_integrity_canonical_evidence
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025_bounded_audit_lifecycle_state"
down_revision: str | None = "0024_vision_integrity_canonical_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_event_rollups",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("hour_bucket", sa.DateTime(), nullable=False),
        sa.Column("rollup_key", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("actor_type", sa.String(length=30), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=True),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("first_at", sa.DateTime(), nullable=False),
        sa.Column("last_at", sa.DateTime(), nullable=False),
        sa.Column("sample_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "hour_bucket",
            "rollup_key",
            name="uq_audit_event_rollups_bucket_key",
        ),
    )
    for column in (
        "hour_bucket",
        "rollup_key",
        "event_type",
        "actor",
        "resource_type",
        "outcome",
        "first_at",
        "last_at",
        "created_at",
    ):
        op.create_index(
            op.f(f"ix_audit_event_rollups_{column}"),
            "audit_event_rollups",
            [column],
        )

    op.create_table(
        "lifecycle_current_states",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("company_namespace", sa.String(length=200), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.String(length=200), nullable=False),
        sa.Column("operating_model_revision_id", sa.String(length=64), nullable=False),
        sa.Column("lifecycle_status", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("assessment_id", sa.String(length=64), nullable=False),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("assessed_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["lifecycle_assessments.id"],
        ),
        sa.ForeignKeyConstraint(
            ["operating_model_revision_id"],
            ["operating_model_revisions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_namespace",
            "resource_type",
            "resource_id",
            name="uq_lifecycle_current_states_resource",
        ),
    )
    for column in (
        "company_namespace",
        "resource_type",
        "resource_id",
        "operating_model_revision_id",
        "lifecycle_status",
        "assessment_id",
        "source_fingerprint",
        "assessed_at",
    ):
        op.create_index(
            op.f(f"ix_lifecycle_current_states_{column}"),
            "lifecycle_current_states",
            [column],
        )

    if op.get_context().dialect.name == "postgresql":
        op.execute(
            """
            INSERT INTO lifecycle_current_states (
                id,
                company_namespace,
                resource_type,
                resource_id,
                operating_model_revision_id,
                lifecycle_status,
                reason,
                metadata,
                assessment_id,
                source_fingerprint,
                assessed_at,
                updated_at
            )
            SELECT
                'lifecycle_state_' || md5(
                    latest.company_namespace || chr(31) ||
                    latest.resource_type || chr(31) ||
                    latest.resource_id
                ),
                latest.company_namespace,
                latest.resource_type,
                latest.resource_id,
                latest.operating_model_revision_id,
                latest.lifecycle_status,
                latest.reason,
                latest.metadata,
                latest.id,
                md5(
                    latest.operating_model_revision_id || chr(31) ||
                    latest.lifecycle_status || chr(31) ||
                    latest.reason || chr(31) ||
                    latest.metadata::text
                ),
                latest.assessed_at,
                latest.assessed_at
            FROM (
                SELECT DISTINCT ON (
                    company_namespace,
                    resource_type,
                    resource_id
                ) *
                FROM lifecycle_assessments
                ORDER BY
                    company_namespace,
                    resource_type,
                    resource_id,
                    assessed_at DESC,
                    id DESC
            ) AS latest
            ON CONFLICT (
                company_namespace,
                resource_type,
                resource_id
            ) DO NOTHING
            """
        )
        for table_name in (
            "audit_events",
            "lifecycle_assessments",
            "lifecycle_current_states",
            "business_work_items",
        ):
            op.execute(
                f"""
                ALTER TABLE {table_name} SET (
                    autovacuum_vacuum_scale_factor = 0.02,
                    autovacuum_analyze_scale_factor = 0.01,
                    autovacuum_vacuum_threshold = 5000,
                    autovacuum_analyze_threshold = 2500
                )
                """
            )


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        for table_name in (
            "audit_events",
            "lifecycle_assessments",
            "business_work_items",
        ):
            op.execute(
                f"""
                ALTER TABLE {table_name} RESET (
                    autovacuum_vacuum_scale_factor,
                    autovacuum_analyze_scale_factor,
                    autovacuum_vacuum_threshold,
                    autovacuum_analyze_threshold
                )
                """
            )
    op.drop_table("lifecycle_current_states")
    op.drop_table("audit_event_rollups")
