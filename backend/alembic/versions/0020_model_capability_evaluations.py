"""Add task-level model capability evaluation evidence.

Revision ID: 0020_model_capability_evaluations
Revises: 0019_claim_extraction_leases
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020_model_capability_evaluations"
down_revision: str | None = "0019_claim_extraction_leases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_capability_evaluations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=240), nullable=False),
        sa.Column("task_type", sa.String(length=100), nullable=False),
        sa.Column("prompt_contract_version", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("cases", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "task_type",
            name="uq_model_capability_evaluations_run_task",
        ),
    )
    for column in (
        "run_id",
        "provider",
        "model",
        "task_type",
        "prompt_contract_version",
        "status",
        "evaluated_at",
        "expires_at",
    ):
        op.create_index(
            op.f(f"ix_model_capability_evaluations_{column}"),
            "model_capability_evaluations",
            [column],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("model_capability_evaluations")
