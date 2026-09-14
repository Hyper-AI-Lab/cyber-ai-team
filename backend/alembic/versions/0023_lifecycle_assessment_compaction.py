"""Compact duplicate lifecycle assessments and stabilize transition keys.

Revision ID: 0023_lifecycle_assessment_compaction
Revises: 0022_operating_model_lifecycle_v4
Create Date: 2026-09-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0023_lifecycle_assessment_compaction"
down_revision: str | None = "0022_operating_model_lifecycle_v4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TEMPORARY TABLE lifecycle_assessments_v2_compacted
        ON COMMIT DROP
        AS
        WITH latest_assessments AS MATERIALIZED (
            SELECT
                company_namespace,
                resource_type,
                resource_id,
                lifecycle_status,
                max(assessed_at) AS assessed_at
            FROM lifecycle_assessments
            GROUP BY
                company_namespace,
                resource_type,
                resource_id,
                lifecycle_status
        )
        SELECT DISTINCT ON (
            assessment.company_namespace,
            assessment.resource_type,
            assessment.resource_id,
            assessment.lifecycle_status
        )
            assessment.id,
            assessment.company_namespace,
            assessment.resource_type,
            assessment.resource_id,
            assessment.operating_model_revision_id,
            assessment.lifecycle_status,
            assessment.reason,
            assessment.evidence_ids,
            assessment.observer_review_id,
            assessment.metadata,
            'lifecycle:v2:' || md5(
                assessment.company_namespace || chr(31) ||
                assessment.resource_type || chr(31) ||
                assessment.resource_id || chr(31) ||
                assessment.lifecycle_status
            ) AS idempotency_key,
            assessment.assessed_at,
            assessment.expires_at
        FROM lifecycle_assessments AS assessment
        JOIN latest_assessments AS latest
          ON latest.company_namespace = assessment.company_namespace
         AND latest.resource_type = assessment.resource_type
         AND latest.resource_id = assessment.resource_id
         AND latest.lifecycle_status = assessment.lifecycle_status
         AND latest.assessed_at = assessment.assessed_at
        ORDER BY
            assessment.company_namespace,
            assessment.resource_type,
            assessment.resource_id,
            assessment.lifecycle_status,
            assessment.id DESC
        """
    )
    op.execute("TRUNCATE TABLE lifecycle_assessments")
    op.execute(
        """
        INSERT INTO lifecycle_assessments (
            id,
            company_namespace,
            resource_type,
            resource_id,
            operating_model_revision_id,
            lifecycle_status,
            reason,
            evidence_ids,
            observer_review_id,
            metadata,
            idempotency_key,
            assessed_at,
            expires_at
        )
        SELECT
            id,
            company_namespace,
            resource_type,
            resource_id,
            operating_model_revision_id,
            lifecycle_status,
            reason,
            evidence_ids,
            observer_review_id,
            metadata,
            idempotency_key,
            assessed_at,
            expires_at
        FROM lifecycle_assessments_v2_compacted
        """
    )


def downgrade() -> None:
    # Compacted duplicate evidence cannot be reconstructed. Revision 0022's
    # schema remains compatible when rolling application code back.
    pass
