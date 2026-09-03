"""Add autonomous operating-model lifecycle persistence.

Revision ID: 0022_operating_model_lifecycle_v4
Revises: 0021_autonomous_action_candidates
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022_operating_model_lifecycle_v4"
down_revision: str | None = "0021_autonomous_action_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _index(table: str, column: str, *, name: str | None = None) -> None:
    op.create_index(name or f"ix_{table}_{column}", table, [column], unique=False)


def upgrade() -> None:
    op.create_table(
        "operating_model_revisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("company_namespace", sa.String(200), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column(
            "company_model_revision_id",
            sa.String(64),
            sa.ForeignKey("company_model_revisions.id"),
            nullable=True,
        ),
        sa.Column("strategy_context_hash", sa.String(64), nullable=True),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("domain_keys", sa.JSON(), nullable=False),
        sa.Column("objective_revision_ids", sa.JSON(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "observer_review_id",
            sa.String(64),
            sa.ForeignKey("observer_reviews.id"),
            nullable=True,
        ),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "company_namespace",
            "revision",
            name="uq_operating_model_revisions_namespace_revision",
        ),
        sa.UniqueConstraint(
            "company_namespace",
            "source_hash",
            name="uq_operating_model_revisions_namespace_hash",
        ),
    )
    for column in (
        "company_namespace",
        "status",
        "company_model_revision_id",
        "strategy_context_hash",
        "source_hash",
        "observer_review_id",
        "created_at",
        "activated_at",
    ):
        _index("operating_model_revisions", column)

    op.create_table(
        "operating_domains",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("company_namespace", sa.String(200), nullable=False),
        sa.Column("domain_key", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("lifecycle_state", sa.String(30), nullable=False),
        sa.Column("effective_state", sa.String(30), nullable=False),
        sa.Column("core", sa.Boolean(), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column(
            "operating_model_revision_id",
            sa.String(64),
            sa.ForeignKey("operating_model_revisions.id"),
            nullable=True,
        ),
        sa.Column("status_reason", sa.Text(), nullable=False),
        sa.Column("shadow_started_at", sa.DateTime(), nullable=True),
        sa.Column("shadow_successes", sa.Integer(), nullable=False),
        sa.Column("shadow_failures", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_reconciled_at", sa.DateTime(), nullable=True),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "company_namespace",
            "domain_key",
            name="uq_operating_domains_namespace_key",
        ),
    )
    for column in (
        "company_namespace",
        "domain_key",
        "lifecycle_state",
        "effective_state",
        "core",
        "operating_model_revision_id",
        "created_at",
        "last_reconciled_at",
    ):
        _index("operating_domains", column)

    op.create_table(
        "operating_domain_revisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "domain_id",
            sa.String(64),
            sa.ForeignKey("operating_domains.id"),
            nullable=False,
        ),
        sa.Column(
            "operating_model_revision_id",
            sa.String(64),
            sa.ForeignKey("operating_model_revisions.id"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("desired_state", sa.String(30), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("outputs", sa.JSON(), nullable=False),
        sa.Column("required_capabilities", sa.JSON(), nullable=False),
        sa.Column("required_tools", sa.JSON(), nullable=False),
        sa.Column("event_selectors", sa.JSON(), nullable=False),
        sa.Column("objective_revision_ids", sa.JSON(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("cadence", sa.JSON(), nullable=False),
        sa.Column("budget", sa.JSON(), nullable=False),
        sa.Column("activation_criteria", sa.JSON(), nullable=False),
        sa.Column("retirement_criteria", sa.JSON(), nullable=False),
        sa.Column("risk_level", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column(
            "observer_review_id",
            sa.String(64),
            sa.ForeignKey("observer_reviews.id"),
            nullable=True,
        ),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "domain_id",
            "revision",
            name="uq_operating_domain_revisions_domain_revision",
        ),
        sa.UniqueConstraint(
            "domain_id",
            "source_hash",
            name="uq_operating_domain_revisions_domain_hash",
        ),
    )
    for column in (
        "domain_id",
        "operating_model_revision_id",
        "desired_state",
        "risk_level",
        "source_hash",
        "observer_review_id",
        "created_at",
    ):
        _index("operating_domain_revisions", column)

    op.create_table(
        "operating_model_reconciliation_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("company_namespace", sa.String(200), nullable=False),
        sa.Column(
            "operating_model_revision_id",
            sa.String(64),
            sa.ForeignKey("operating_model_revisions.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("actual_state_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(240), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_operating_model_reconciliation_runs_key",
        ),
    )
    _index("operating_model_reconciliation_runs", "company_namespace")
    _index(
        "operating_model_reconciliation_runs",
        "operating_model_revision_id",
        name="ix_om_reconciliation_runs_model_revision",
    )
    for column in (
        "status",
        "dry_run",
        "actual_state_hash",
        "idempotency_key",
        "created_at",
        "completed_at",
    ):
        _index("operating_model_reconciliation_runs", column)

    op.create_table(
        "operating_lifecycle_decisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "reconciliation_run_id",
            sa.String(64),
            sa.ForeignKey("operating_model_reconciliation_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "operating_model_revision_id",
            sa.String(64),
            sa.ForeignKey("operating_model_revisions.id"),
            nullable=False,
        ),
        sa.Column("resource_type", sa.String(80), nullable=False),
        sa.Column("resource_id", sa.String(200), nullable=False),
        sa.Column("domain_key", sa.String(100), nullable=True),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("from_state", sa.String(30), nullable=True),
        sa.Column("to_state", sa.String(30), nullable=True),
        sa.Column("decision_status", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("policy_decision", sa.JSON(), nullable=False),
        sa.Column(
            "observer_review_id",
            sa.String(64),
            sa.ForeignKey("observer_reviews.id"),
            nullable=True,
        ),
        sa.Column(
            "approval_id",
            sa.String(64),
            sa.ForeignKey("approval_requests.id"),
            nullable=True,
        ),
        sa.Column(
            "operation_node_id",
            sa.String(64),
            sa.ForeignKey("operation_graph_nodes.id"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(240), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_operating_lifecycle_decisions_key",
        ),
    )
    for column in (
        "reconciliation_run_id",
        "operating_model_revision_id",
        "resource_type",
        "resource_id",
        "domain_key",
        "action",
        "decision_status",
        "observer_review_id",
        "approval_id",
        "operation_node_id",
        "idempotency_key",
        "created_at",
    ):
        _index("operating_lifecycle_decisions", column)

    op.create_table(
        "lifecycle_assessments",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("company_namespace", sa.String(200), nullable=False),
        sa.Column("resource_type", sa.String(80), nullable=False),
        sa.Column("resource_id", sa.String(200), nullable=False),
        sa.Column(
            "operating_model_revision_id",
            sa.String(64),
            sa.ForeignKey("operating_model_revisions.id"),
            nullable=False,
        ),
        sa.Column("lifecycle_status", sa.String(40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column(
            "observer_review_id",
            sa.String(64),
            sa.ForeignKey("observer_reviews.id"),
            nullable=True,
        ),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(240), nullable=False),
        sa.Column("assessed_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_lifecycle_assessments_key",
        ),
    )
    for column in (
        "company_namespace",
        "resource_type",
        "resource_id",
        "operating_model_revision_id",
        "lifecycle_status",
        "observer_review_id",
        "idempotency_key",
        "assessed_at",
        "expires_at",
    ):
        _index("lifecycle_assessments", column)

    op.create_table(
        "discovery_obligations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("company_namespace", sa.String(200), nullable=False),
        sa.Column("predicate", sa.String(160), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("blocking", sa.Boolean(), nullable=False),
        sa.Column(
            "company_model_revision_id",
            sa.String(64),
            sa.ForeignKey("company_model_revisions.id"),
            nullable=False,
        ),
        sa.Column(
            "claim_id",
            sa.String(64),
            sa.ForeignKey("company_claims.id"),
            nullable=True,
        ),
        sa.Column("source_types", sa.JSON(), nullable=False),
        sa.Column("attempted_source_ids", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("owner_attention_id", sa.String(200), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("resolution", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(240), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_discovery_obligations_key",
        ),
    )
    for column in (
        "company_namespace",
        "predicate",
        "priority",
        "status",
        "blocking",
        "company_model_revision_id",
        "claim_id",
        "next_attempt_at",
        "owner_attention_id",
        "idempotency_key",
        "created_at",
    ):
        _index("discovery_obligations", column)

    op.create_table(
        "domain_control_revisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("company_namespace", sa.String(200), nullable=False),
        sa.Column("domain_key", sa.String(100), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("control_mode", sa.String(30), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("actor_type", sa.String(30), nullable=False),
        sa.Column("source_type", sa.String(80), nullable=False),
        sa.Column("source_id", sa.String(200), nullable=True),
        sa.Column(
            "supersedes_id",
            sa.String(64),
            sa.ForeignKey("domain_control_revisions.id"),
            nullable=True,
        ),
        sa.Column("effective_from", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "company_namespace",
            "domain_key",
            "revision",
            name="uq_domain_control_revisions_namespace_domain_revision",
        ),
    )
    for column in (
        "company_namespace",
        "domain_key",
        "control_mode",
        "locked",
        "source_id",
        "effective_from",
        "expires_at",
        "created_at",
    ):
        _index("domain_control_revisions", column)


def downgrade() -> None:
    for table in (
        "domain_control_revisions",
        "discovery_obligations",
        "lifecycle_assessments",
        "operating_lifecycle_decisions",
        "operating_model_reconciliation_runs",
        "operating_domain_revisions",
        "operating_domains",
        "operating_model_revisions",
    ):
        op.drop_table(table)
