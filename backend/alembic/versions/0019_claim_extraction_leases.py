"""Add durable claim-extraction leases and retry scheduling.

Revision ID: 0019_claim_extraction_leases
Revises: 0018_outcome_learning_integrity
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_claim_extraction_leases"
down_revision: str | None = "0018_outcome_learning_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "company_signals",
        sa.Column("claim_extraction_available_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "company_signals",
        sa.Column("claim_extraction_lease_owner", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "company_signals",
        sa.Column("claim_extraction_lease_expires_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        op.f("ix_company_signals_claim_extraction_available_at"),
        "company_signals",
        ["claim_extraction_available_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_company_signals_claim_extraction_lease_owner"),
        "company_signals",
        ["claim_extraction_lease_owner"],
        unique=False,
    )
    op.create_index(
        op.f("ix_company_signals_claim_extraction_lease_expires_at"),
        "company_signals",
        ["claim_extraction_lease_expires_at"],
        unique=False,
    )
    op.execute(
        """
        UPDATE company_signals
        SET claim_extraction_available_at = received_at
        WHERE status IN ('pending', 'quarantined')
          AND claim_extraction_available_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_company_signals_claim_extraction_lease_expires_at"),
        table_name="company_signals",
    )
    op.drop_index(
        op.f("ix_company_signals_claim_extraction_lease_owner"),
        table_name="company_signals",
    )
    op.drop_index(
        op.f("ix_company_signals_claim_extraction_available_at"),
        table_name="company_signals",
    )
    op.drop_column("company_signals", "claim_extraction_lease_expires_at")
    op.drop_column("company_signals", "claim_extraction_lease_owner")
    op.drop_column("company_signals", "claim_extraction_available_at")
