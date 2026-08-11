"""Track durable company-signal claim extraction and retry state.

Revision ID: 0017_company_claim_extraction
Revises: 0016_action_policy_cases
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_company_claim_extraction"
down_revision: str | None = "0016_action_policy_cases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "company_signals",
        sa.Column(
            "claim_extraction_status",
            sa.String(30),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "company_signals",
        sa.Column(
            "claim_extraction_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "company_signals",
        sa.Column("claim_extraction_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "company_signals",
        sa.Column("claim_extracted_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_company_signals_claim_extraction_status",
        "company_signals",
        ["claim_extraction_status"],
    )
    op.create_index(
        "ix_company_signals_claim_extracted_at",
        "company_signals",
        ["claim_extracted_at"],
    )

    extractable = (
        "'document.updated', 'email.received', 'erpnext.company_context_snapshot', "
        "'owner.instruction', 'research.results', 'website.snapshot'"
    )
    op.execute(
        sa.text(
            "UPDATE company_signals "
            "SET claim_extraction_status = 'not_applicable' "
            f"WHERE signal_type NOT IN ({extractable})"
        )
    )
    op.execute(
        sa.text(
            "UPDATE company_signals "
            "SET claim_extraction_status = 'succeeded', "
            "    claim_extracted_at = processed_at "
            "WHERE status = 'processed' "
            "  AND signal_type IN "
            "      ('erpnext.company_context_snapshot', 'owner.instruction')"
        )
    )
    op.execute(
        sa.text(
            "UPDATE company_signals "
            "SET claim_extraction_status = 'legacy_unknown' "
            "WHERE status = 'processed' "
            "  AND signal_type IN "
            "      ('document.updated', 'email.received', 'research.results', "
            "       'website.snapshot')"
        )
    )
    # Repository documents are the bounded canonical recovery set after the prior
    # provider outage. Business-event idempotency prevents duplicate downstream work.
    op.execute(
        sa.text(
            "UPDATE company_signals "
            "SET status = 'pending', disposition = NULL, processed_at = NULL, "
            "    claim_extraction_status = 'pending', "
            "    claim_extraction_attempts = 0, claim_extraction_error = NULL, "
            "    claim_extracted_at = NULL "
            "WHERE signal_type = 'document.updated' "
            "  AND injection_status = 'clear'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE company_signals "
            "SET claim_extraction_status = 'blocked', "
            "    claim_extracted_at = COALESCE(processed_at, received_at) "
            "WHERE injection_status = 'suspected'"
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_company_signals_claim_extracted_at",
        table_name="company_signals",
    )
    op.drop_index(
        "ix_company_signals_claim_extraction_status",
        table_name="company_signals",
    )
    op.drop_column("company_signals", "claim_extracted_at")
    op.drop_column("company_signals", "claim_extraction_error")
    op.drop_column("company_signals", "claim_extraction_attempts")
    op.drop_column("company_signals", "claim_extraction_status")
