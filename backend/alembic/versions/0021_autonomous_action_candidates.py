"""Add governed autonomous action candidate lifecycle.

Revision ID: 0021_autonomous_action_candidates
Revises: 0020_model_capability_evaluations
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021_autonomous_action_candidates"
down_revision: str | None = "0020_model_capability_evaluations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "autonomous_action_candidates",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("company_namespace", sa.String(length=200), nullable=False),
        sa.Column("parent_work_item_id", sa.String(length=64), nullable=False),
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column("mandate_id", sa.String(length=64), nullable=False),
        sa.Column("action_class", sa.String(length=120), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("action_envelope", sa.JSON(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("expected_outcome", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("risk_level", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reversible", sa.Boolean(), nullable=False),
        sa.Column("external_side_effect", sa.Boolean(), nullable=False),
        sa.Column("observer_review_id", sa.String(length=64), nullable=True),
        sa.Column("policy_decision", sa.JSON(), nullable=False),
        sa.Column("approval_id", sa.String(length=64), nullable=True),
        sa.Column("execution_work_item_id", sa.String(length=64), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=240), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["approval_id"], ["approval_requests.id"]),
        sa.ForeignKeyConstraint(["execution_work_item_id"], ["business_work_items.id"]),
        sa.ForeignKeyConstraint(["mandate_id"], ["agent_mandates.id"]),
        sa.ForeignKeyConstraint(["observer_review_id"], ["observer_reviews.id"]),
        sa.ForeignKeyConstraint(["parent_work_item_id"], ["business_work_items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_autonomous_action_candidates_idempotency_key",
        ),
    )
    for column in (
        "company_namespace",
        "parent_work_item_id",
        "agent_id",
        "mandate_id",
        "action_class",
        "tool_name",
        "status",
        "risk_level",
        "observer_review_id",
        "approval_id",
        "execution_work_item_id",
        "idempotency_key",
        "created_at",
        "reviewed_at",
        "completed_at",
    ):
        op.create_index(
            op.f(f"ix_autonomous_action_candidates_{column}"),
            "autonomous_action_candidates",
            [column],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("autonomous_action_candidates")
