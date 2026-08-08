"""Add durable action-policy shadow and live-canary evidence.

Revision ID: 0016_action_policy_cases
Revises: 0015_autonomous_company_ops_v3
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_action_policy_cases"
down_revision: str | None = "0015_autonomous_company_ops_v3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _index(table: str, column: str) -> None:
    op.create_index(f"ix_{table}_{column}", table, [column])


def upgrade() -> None:
    op.create_table(
        "action_policy_validation_cases",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("action_class", sa.String(120), nullable=False),
        sa.Column("scenario_key", sa.String(160), nullable=False),
        sa.Column("mode", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("action_envelope", sa.JSON(), nullable=False),
        sa.Column("payload_summary", sa.JSON(), nullable=False),
        sa.Column("expected_decision", sa.String(30), nullable=False),
        sa.Column("expected_reasons", sa.JSON(), nullable=False),
        sa.Column("policy_decision", sa.JSON(), nullable=False),
        sa.Column("observer_review", sa.JSON(), nullable=False),
        sa.Column("owner_adjudication", sa.JSON(), nullable=False),
        sa.Column("execution_request", sa.JSON(), nullable=False),
        sa.Column("execution_result", sa.JSON(), nullable=False),
        sa.Column("evaluator_score", sa.Float(), nullable=False),
        sa.Column("compliant", sa.Boolean(), nullable=True),
        sa.Column("high_severity_findings", sa.Integer(), nullable=False),
        sa.Column("external_side_effect_executed", sa.Boolean(), nullable=False),
        sa.Column(
            "work_item_id",
            sa.String(64),
            sa.ForeignKey("business_work_items.id"),
            nullable=True,
        ),
        sa.Column(
            "approval_id",
            sa.String(64),
            sa.ForeignKey("approval_requests.id"),
            nullable=True,
        ),
        sa.Column(
            "outcome_assessment_id",
            sa.String(64),
            sa.ForeignKey("outcome_assessments.id"),
            nullable=True,
        ),
        sa.Column(
            "counted_policy_id",
            sa.String(64),
            sa.ForeignKey("action_class_policies.id"),
            nullable=True,
        ),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(240), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
        sa.Column("assessed_at", sa.DateTime(), nullable=True),
        sa.Column("counted_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_action_policy_validation_cases_idempotency_key",
        ),
    )
    for column in (
        "action_class",
        "scenario_key",
        "mode",
        "status",
        "work_item_id",
        "approval_id",
        "outcome_assessment_id",
        "counted_policy_id",
        "idempotency_key",
        "created_at",
        "executed_at",
        "assessed_at",
        "counted_at",
    ):
        _index("action_policy_validation_cases", column)

    op.create_table(
        "action_policy_validation_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "validation_case_id",
            sa.String(64),
            sa.ForeignKey("action_policy_validation_cases.id"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "validation_case_id",
            "sequence",
            name="uq_action_policy_validation_events_case_sequence",
        ),
    )
    for column in (
        "validation_case_id",
        "event_type",
        "status",
        "created_at",
    ):
        _index("action_policy_validation_events", column)


def downgrade() -> None:
    op.drop_table("action_policy_validation_events")
    op.drop_table("action_policy_validation_cases")
