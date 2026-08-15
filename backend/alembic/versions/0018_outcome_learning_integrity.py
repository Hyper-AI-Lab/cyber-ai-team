"""Enforce one durable outcome assessment per work item.

Revision ID: 0018_outcome_learning_integrity
Revises: 0017_company_claim_extraction
Create Date: 2026-08-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018_outcome_learning_integrity"
down_revision: str | None = "0017_company_claim_extraction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_outcome_assessments_work_item_id",
        "outcome_assessments",
        ["work_item_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_outcome_assessments_work_item_id",
        "outcome_assessments",
        type_="unique",
    )
