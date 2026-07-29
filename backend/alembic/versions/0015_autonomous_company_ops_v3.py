"""Add evidence-driven autonomous company operations persistence.

Revision ID: 0015_autonomous_company_ops_v3
Revises: 0014_workflow_intents
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_autonomous_company_ops_v3"
down_revision: str | None = "0014_workflow_intents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _index(table: str, *columns: str, unique: bool = False, name: str | None = None) -> None:
    index_name = name or f"ix_{table}_{'_'.join(columns)}"
    op.create_index(index_name, table, list(columns), unique=unique)


def upgrade() -> None:
    op.create_table(
        "company_sources",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("company_namespace", sa.String(200), nullable=False),
        sa.Column("source_key", sa.String(160), nullable=False),
        sa.Column("source_type", sa.String(80), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("trust_class", sa.String(30), nullable=False),
        sa.Column("sensitivity", sa.String(30), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("cursor", sa.JSON(), nullable=False),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "company_namespace",
            "source_key",
            name="uq_company_sources_namespace_key",
        ),
    )
    for column in (
        "company_namespace",
        "source_key",
        "source_type",
        "status",
        "trust_class",
        "sensitivity",
        "created_at",
    ):
        _index("company_sources", column)

    op.create_table(
        "company_signals",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("company_namespace", sa.String(200), nullable=False),
        sa.Column("source_id", sa.String(64), sa.ForeignKey("company_sources.id"), nullable=False),
        sa.Column("signal_type", sa.String(100), nullable=False),
        sa.Column("external_id", sa.String(240), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("disposition", sa.String(40), nullable=True),
        sa.Column("trust_class", sa.String(30), nullable=False),
        sa.Column("sensitivity", sa.String(30), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("redacted_payload", sa.JSON(), nullable=False),
        sa.Column("injection_status", sa.String(30), nullable=False),
        sa.Column("quarantine_reason", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(240), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_company_signals_idempotency_key"),
    )
    for column in (
        "company_namespace",
        "source_id",
        "signal_type",
        "external_id",
        "status",
        "disposition",
        "trust_class",
        "sensitivity",
        "content_hash",
        "injection_status",
        "idempotency_key",
        "occurred_at",
        "received_at",
    ):
        _index("company_signals", column)

    op.create_table(
        "evidence_artifacts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("company_namespace", sa.String(200), nullable=False),
        sa.Column("source_id", sa.String(64), sa.ForeignKey("company_sources.id"), nullable=False),
        sa.Column("signal_id", sa.String(64), sa.ForeignKey("company_signals.id"), nullable=True),
        sa.Column("artifact_type", sa.String(80), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column("trust_class", sa.String(30), nullable=False),
        sa.Column("sensitivity", sa.String(30), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "source_id",
            "content_hash",
            name="uq_evidence_artifacts_source_hash",
        ),
    )
    for column in (
        "company_namespace",
        "source_id",
        "signal_id",
        "artifact_type",
        "content_hash",
        "trust_class",
        "sensitivity",
        "expires_at",
        "created_at",
    ):
        _index("evidence_artifacts", column)

    op.create_table(
        "company_claims",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("company_namespace", sa.String(200), nullable=False),
        sa.Column("subject", sa.String(240), nullable=False),
        sa.Column("predicate", sa.String(160), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("epistemic_state", sa.String(30), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("trust_class", sa.String(30), nullable=False),
        sa.Column("sensitivity", sa.String(30), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("claim_hash", sa.String(64), nullable=False),
        sa.Column("owner_locked", sa.Boolean(), nullable=False),
        sa.Column("valid_from", sa.DateTime(), nullable=False),
        sa.Column("valid_until", sa.DateTime(), nullable=True),
        sa.Column(
            "supersedes_id",
            sa.String(64),
            sa.ForeignKey("company_claims.id"),
            nullable=True,
        ),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("claim_hash", name="uq_company_claims_claim_hash"),
    )
    for column in (
        "company_namespace",
        "subject",
        "predicate",
        "epistemic_state",
        "trust_class",
        "sensitivity",
        "claim_hash",
        "owner_locked",
        "valid_from",
        "valid_until",
        "supersedes_id",
        "created_at",
    ):
        _index("company_claims", column)

    op.create_table(
        "company_model_revisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("company_namespace", sa.String(200), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("model", sa.JSON(), nullable=False),
        sa.Column("claim_ids", sa.JSON(), nullable=False),
        sa.Column("unknowns", sa.JSON(), nullable=False),
        sa.Column("disputes", sa.JSON(), nullable=False),
        sa.Column("provenance_coverage", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column(
            "observer_review_id",
            sa.String(64),
            sa.ForeignKey("observer_reviews.id"),
            nullable=True,
        ),
        sa.Column("owner_locks", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "company_namespace",
            "revision",
            name="uq_company_model_revisions_namespace_revision",
        ),
        sa.UniqueConstraint("source_hash", name="uq_company_model_revisions_source_hash"),
    )
    for column in (
        "company_namespace",
        "status",
        "source_hash",
        "observer_review_id",
        "created_at",
        "activated_at",
    ):
        _index("company_model_revisions", column)

    op.create_table(
        "company_objective_revisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "objective_id",
            sa.String(64),
            sa.ForeignKey("company_objectives.id"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False),
        sa.Column("target", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("owner_locked", sa.Boolean(), nullable=False),
        sa.Column("probation_until", sa.DateTime(), nullable=True),
        sa.Column(
            "supersedes_id",
            sa.String(64),
            sa.ForeignKey("company_objective_revisions.id"),
            nullable=True,
        ),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "objective_id",
            "revision",
            name="uq_company_objective_revisions_objective_revision",
        ),
    )
    for column in (
        "objective_id",
        "status",
        "category",
        "priority",
        "owner_locked",
        "probation_until",
        "created_at",
    ):
        _index("company_objective_revisions", column)

    op.create_table(
        "operating_kpi_revisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "kpi_definition_id",
            sa.String(64),
            sa.ForeignKey("operating_kpi_definitions.id"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("formula", sa.Text(), nullable=False),
        sa.Column("measurement_bindings", sa.JSON(), nullable=False),
        sa.Column("target_value", sa.Float(), nullable=False),
        sa.Column("lower_guardrail", sa.Float(), nullable=True),
        sa.Column("upper_guardrail", sa.Float(), nullable=True),
        sa.Column("objective_revision_ids", sa.JSON(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("owner_locked", sa.Boolean(), nullable=False),
        sa.Column("probation_until", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "kpi_definition_id",
            "revision",
            name="uq_operating_kpi_revisions_definition_revision",
        ),
    )
    for column in (
        "kpi_definition_id",
        "status",
        "owner_locked",
        "probation_until",
        "created_at",
    ):
        _index("operating_kpi_revisions", column)

    op.create_table(
        "strategic_experiments",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("company_namespace", sa.String(200), nullable=False),
        sa.Column(
            "objective_revision_id",
            sa.String(64),
            sa.ForeignKey("company_objective_revisions.id"),
            nullable=True,
        ),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("design", sa.JSON(), nullable=False),
        sa.Column("metric_keys", sa.JSON(), nullable=False),
        sa.Column("budget", sa.JSON(), nullable=False),
        sa.Column("risk_level", sa.String(20), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    for column in (
        "company_namespace",
        "objective_revision_id",
        "status",
        "risk_level",
        "created_at",
    ):
        _index("strategic_experiments", column)

    op.create_table(
        "agent_mandates",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("agent_id", sa.String(64), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("objective_ids", sa.JSON(), nullable=False),
        sa.Column("authority", sa.JSON(), nullable=False),
        sa.Column("budget", sa.JSON(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("outputs", sa.JSON(), nullable=False),
        sa.Column("kpi_keys", sa.JSON(), nullable=False),
        sa.Column("cadence", sa.JSON(), nullable=False),
        sa.Column("escalation_rules", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_mandates_agent_version"),
    )
    for column in ("agent_id", "status", "created_at"):
        _index("agent_mandates", column)

    op.create_table(
        "domain_autonomy_controls",
        sa.Column("domain", sa.String(100), primary_key=True),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("owner", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for column in ("state", "created_at"):
        _index("domain_autonomy_controls", column)

    op.create_table(
        "business_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("company_namespace", sa.String(200), nullable=False),
        sa.Column("signal_id", sa.String(64), sa.ForeignKey("company_signals.id"), nullable=True),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("source_type", sa.String(80), nullable=False),
        sa.Column("source_id", sa.String(200), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("disposition", sa.String(40), nullable=True),
        sa.Column("disposition_reason", sa.Text(), nullable=True),
        sa.Column("work_item_id", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(240), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_business_events_idempotency_key"),
    )
    for column in (
        "company_namespace",
        "signal_id",
        "event_type",
        "source_type",
        "source_id",
        "status",
        "disposition",
        "work_item_id",
        "idempotency_key",
        "occurred_at",
        "created_at",
    ):
        _index("business_events", column)

    op.create_table(
        "business_event_deliveries",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "event_id",
            sa.String(64),
            sa.ForeignKey("business_events.id"),
            nullable=False,
        ),
        sa.Column("destination", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("lease_owner", sa.String(200), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "event_id",
            "destination",
            name="uq_business_event_deliveries_event_destination",
        ),
    )
    for column in (
        "event_id",
        "destination",
        "status",
        "available_at",
        "lease_owner",
        "lease_expires_at",
        "created_at",
        "delivered_at",
    ):
        _index("business_event_deliveries", column)

    op.create_table(
        "workflow_specifications",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("spec_key", sa.String(160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("specification", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("risk_level", sa.String(20), nullable=False),
        sa.Column("source_type", sa.String(80), nullable=False),
        sa.Column("source_id", sa.String(200), nullable=True),
        sa.Column("sandbox_result", sa.JSON(), nullable=False),
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
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("spec_key", "version", name="uq_workflow_specifications_key_version"),
        sa.UniqueConstraint("content_hash", name="uq_workflow_specifications_content_hash"),
    )
    for column in (
        "spec_key",
        "status",
        "content_hash",
        "risk_level",
        "source_type",
        "source_id",
        "observer_review_id",
        "approval_id",
        "created_at",
        "activated_at",
    ):
        _index("workflow_specifications", column)

    op.create_table(
        "business_work_items",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("company_namespace", sa.String(200), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("work_type", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False),
        sa.Column("risk_level", sa.String(20), nullable=False),
        sa.Column("assigned_agent_id", sa.String(64), sa.ForeignKey("agents.id"), nullable=True),
        sa.Column("mandate_id", sa.String(64), sa.ForeignKey("agent_mandates.id"), nullable=True),
        sa.Column("event_id", sa.String(64), sa.ForeignKey("business_events.id"), nullable=True),
        sa.Column(
            "objective_revision_id",
            sa.String(64),
            sa.ForeignKey("company_objective_revisions.id"),
            nullable=True,
        ),
        sa.Column(
            "workflow_specification_id",
            sa.String(64),
            sa.ForeignKey("workflow_specifications.id"),
            nullable=True,
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("acceptance_criteria", sa.JSON(), nullable=False),
        sa.Column("expected_outcome", sa.JSON(), nullable=False),
        sa.Column("actual_outcome", sa.JSON(), nullable=False),
        sa.Column("policy_decision", sa.JSON(), nullable=False),
        sa.Column(
            "approval_id",
            sa.String(64),
            sa.ForeignKey("approval_requests.id"),
            nullable=True,
        ),
        sa.Column("lease_owner", sa.String(200), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("deadline_at", sa.DateTime(), nullable=True),
        sa.Column("idempotency_key", sa.String(240), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_business_work_items_idempotency_key"),
    )
    for column in (
        "company_namespace",
        "work_type",
        "status",
        "priority",
        "risk_level",
        "assigned_agent_id",
        "mandate_id",
        "event_id",
        "objective_revision_id",
        "workflow_specification_id",
        "approval_id",
        "lease_owner",
        "lease_expires_at",
        "deadline_at",
        "idempotency_key",
        "created_at",
    ):
        _index("business_work_items", column)
    op.create_foreign_key(
        "fk_business_events_work_item_id",
        "business_events",
        "business_work_items",
        ["work_item_id"],
        ["id"],
    )

    op.create_table(
        "business_work_item_dependencies",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "work_item_id",
            sa.String(64),
            sa.ForeignKey("business_work_items.id"),
            nullable=False,
        ),
        sa.Column(
            "depends_on_id",
            sa.String(64),
            sa.ForeignKey("business_work_items.id"),
            nullable=False,
        ),
        sa.Column("dependency_type", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "work_item_id",
            "depends_on_id",
            name="uq_business_work_item_dependencies_edge",
        ),
    )
    for column in ("work_item_id", "depends_on_id", "created_at"):
        _index("business_work_item_dependencies", column)

    op.create_table(
        "business_event_dispositions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "event_id",
            sa.String(64),
            sa.ForeignKey("business_events.id"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("disposition", sa.String(40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "work_item_id",
            sa.String(64),
            sa.ForeignKey("business_work_items.id"),
            nullable=True,
        ),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "event_id",
            "sequence",
            name="uq_business_event_dispositions_event_sequence",
        ),
    )
    for column in (
        "event_id",
        "status",
        "disposition",
        "work_item_id",
        "created_at",
    ):
        _index("business_event_dispositions", column)

    op.create_table(
        "outcome_assessments",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "work_item_id",
            sa.String(64),
            sa.ForeignKey("business_work_items.id"),
            nullable=True,
        ),
        sa.Column(
            "execution_record_id",
            sa.String(64),
            sa.ForeignKey("autonomous_execution_records.id"),
            nullable=True,
        ),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("expected_outcome", sa.JSON(), nullable=False),
        sa.Column("actual_outcome", sa.JSON(), nullable=False),
        sa.Column("kpi_changes", sa.JSON(), nullable=False),
        sa.Column("guardrail_breaches", sa.JSON(), nullable=False),
        sa.Column("costs", sa.JSON(), nullable=False),
        sa.Column("failures", sa.JSON(), nullable=False),
        sa.Column("attribution_confidence", sa.Float(), nullable=False),
        sa.Column("evaluator_score", sa.Float(), nullable=False),
        sa.Column("recommendation", sa.String(40), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(240), nullable=False),
        sa.Column("assessed_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_outcome_assessments_idempotency_key"),
    )
    for column in (
        "work_item_id",
        "execution_record_id",
        "status",
        "recommendation",
        "idempotency_key",
        "created_at",
    ):
        _index("outcome_assessments", column)

    op.create_table(
        "action_class_policies",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("action_class", sa.String(120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("permanent_gate", sa.Boolean(), nullable=False),
        sa.Column("auto_execute_enabled", sa.Boolean(), nullable=False),
        sa.Column("thresholds", sa.JSON(), nullable=False),
        sa.Column("validated_cases", sa.Integer(), nullable=False),
        sa.Column("hard_policy_compliance", sa.Float(), nullable=False),
        sa.Column("evaluator_score", sa.Float(), nullable=False),
        sa.Column("high_severity_findings", sa.Integer(), nullable=False),
        sa.Column("shadow_started_at", sa.DateTime(), nullable=True),
        sa.Column("promoted_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("action_class", "version", name="uq_action_class_policies_version"),
    )
    for column in (
        "action_class",
        "status",
        "permanent_gate",
        "auto_execute_enabled",
        "shadow_started_at",
        "created_at",
    ):
        _index("action_class_policies", column)

    op.execute(
        """
        INSERT INTO company_objective_revisions (
            id, objective_id, revision, status, title, description, category,
            priority, target, rationale, evidence_ids, confidence, owner_locked,
            probation_until, supersedes_id, created_by, created_at, activated_at
        )
        SELECT
            'objrev_' || substr(md5(id), 1, 32), id, 1, status, title, description,
            'system_operations', priority, target,
            'Backfilled from the pre-v3 technical objective.', '[]'::json, 1.0,
            FALSE, NULL, NULL, created_by, created_at, created_at
        FROM company_objectives
        ON CONFLICT (objective_id, revision) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO operating_kpi_revisions (
            id, kpi_definition_id, revision, status, formula,
            measurement_bindings, target_value, lower_guardrail, upper_guardrail,
            objective_revision_ids, evidence_ids, confidence, owner_locked,
            probation_until, created_by, created_at, activated_at
        )
        SELECT
            'kpirev_' || substr(md5(id), 1, 32), id, 1, status, key,
            json_build_object(key, source), target_value, NULL, NULL,
            '[]'::json, '[]'::json, 1.0, FALSE, NULL,
            'v3_migration', created_at, created_at
        FROM operating_kpi_definitions
        ON CONFLICT (kpi_definition_id, revision) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("action_class_policies")
    op.drop_table("outcome_assessments")
    op.drop_table("business_event_dispositions")
    op.drop_table("business_work_item_dependencies")
    op.drop_constraint(
        "fk_business_events_work_item_id",
        "business_events",
        type_="foreignkey",
    )
    op.drop_table("business_work_items")
    op.drop_table("workflow_specifications")
    op.drop_table("business_event_deliveries")
    op.drop_table("business_events")
    op.drop_table("domain_autonomy_controls")
    op.drop_table("agent_mandates")
    op.drop_table("strategic_experiments")
    op.drop_table("operating_kpi_revisions")
    op.drop_table("company_objective_revisions")
    op.drop_table("company_model_revisions")
    op.drop_table("company_claims")
    op.drop_table("evidence_artifacts")
    op.drop_table("company_signals")
    op.drop_table("company_sources")
