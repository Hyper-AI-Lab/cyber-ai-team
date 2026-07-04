"""Add autonomous orchestration governor persistence.

Revision ID: 0011_orchestration_governor
Revises: 0010_team_activation
Create Date: 2026-07-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011_orchestration_governor"
down_revision: str | None = "0010_team_activation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS orchestration_governor_runs (
            id VARCHAR(64) PRIMARY KEY,
            status VARCHAR(30) NOT NULL,
            actor VARCHAR(200) NOT NULL,
            policy_version VARCHAR(80) NOT NULL,
            mode VARCHAR(40) NOT NULL,
            auto_apply_low_risk BOOLEAN NOT NULL,
            max_actions INTEGER NOT NULL,
            snapshot_hash VARCHAR(64) NOT NULL,
            operating_snapshot JSON NOT NULL,
            operating_brief TEXT NOT NULL,
            counts JSON NOT NULL,
            errors JSON NOT NULL,
            started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            completed_at TIMESTAMP WITHOUT TIME ZONE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orchestration_governor_runs_status "
        "ON orchestration_governor_runs (status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orchestration_governor_runs_snapshot_hash "
        "ON orchestration_governor_runs (snapshot_hash)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orchestration_governor_runs_started_at "
        "ON orchestration_governor_runs (started_at)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS orchestration_tool_proposals (
            id VARCHAR(64) PRIMARY KEY,
            title VARCHAR(240) NOT NULL,
            capability VARCHAR(120) NOT NULL,
            status VARCHAR(30) NOT NULL,
            risk_level VARCHAR(20) NOT NULL,
            side_effects BOOLEAN NOT NULL,
            source_type VARCHAR(80),
            source_id VARCHAR(200),
            purpose TEXT NOT NULL,
            input_schema JSON NOT NULL,
            output_schema JSON NOT NULL,
            required_credentials JSON NOT NULL,
            executor_kind VARCHAR(60) NOT NULL,
            tests_required JSON NOT NULL,
            rollback_notes TEXT NOT NULL,
            readiness_checks JSON NOT NULL,
            sandbox_mode VARCHAR(40) NOT NULL,
            sandbox_result JSON NOT NULL,
            approval_id VARCHAR(64) REFERENCES approval_requests(id),
            idempotency_key VARCHAR(200) NOT NULL,
            created_by VARCHAR(200) NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_orchestration_tool_proposals_idempotency_key "
        "ON orchestration_tool_proposals (idempotency_key)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orchestration_tool_proposals_capability "
        "ON orchestration_tool_proposals (capability)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orchestration_tool_proposals_status "
        "ON orchestration_tool_proposals (status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orchestration_tool_proposals_risk_level "
        "ON orchestration_tool_proposals (risk_level)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orchestration_tool_proposals_source_type "
        "ON orchestration_tool_proposals (source_type)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orchestration_tool_proposals_source_id "
        "ON orchestration_tool_proposals (source_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orchestration_tool_proposals_approval_id "
        "ON orchestration_tool_proposals (approval_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orchestration_tool_proposals_created_at "
        "ON orchestration_tool_proposals (created_at)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS orchestration_governor_decisions (
            id VARCHAR(64) PRIMARY KEY,
            run_id VARCHAR(64) NOT NULL REFERENCES orchestration_governor_runs(id),
            decision_type VARCHAR(60) NOT NULL,
            title VARCHAR(240) NOT NULL,
            description TEXT NOT NULL,
            status VARCHAR(30) NOT NULL,
            risk_level VARCHAR(20) NOT NULL,
            source_type VARCHAR(80),
            source_id VARCHAR(200),
            target_type VARCHAR(100),
            target_id VARCHAR(200),
            action_payload JSON NOT NULL,
            result JSON NOT NULL,
            error TEXT,
            approval_id VARCHAR(64) REFERENCES approval_requests(id),
            plan_id VARCHAR(64) REFERENCES autonomous_plans(id),
            tool_proposal_id VARCHAR(64) REFERENCES orchestration_tool_proposals(id),
            idempotency_key VARCHAR(200) NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            resolved_at TIMESTAMP WITHOUT TIME ZONE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_orchestration_governor_decisions_idempotency_key "
        "ON orchestration_governor_decisions (idempotency_key)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orchestration_governor_decisions_run_id "
        "ON orchestration_governor_decisions (run_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orchestration_governor_decisions_decision_type "
        "ON orchestration_governor_decisions (decision_type)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orchestration_governor_decisions_status "
        "ON orchestration_governor_decisions (status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orchestration_governor_decisions_risk_level "
        "ON orchestration_governor_decisions (risk_level)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orchestration_governor_decisions_source_type "
        "ON orchestration_governor_decisions (source_type)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orchestration_governor_decisions_source_id "
        "ON orchestration_governor_decisions (source_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orchestration_governor_decisions_target_type "
        "ON orchestration_governor_decisions (target_type)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orchestration_governor_decisions_target_id "
        "ON orchestration_governor_decisions (target_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orchestration_governor_decisions_approval_id "
        "ON orchestration_governor_decisions (approval_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orchestration_governor_decisions_plan_id "
        "ON orchestration_governor_decisions (plan_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS "
        "ix_orchestration_governor_decisions_tool_proposal_id "
        "ON orchestration_governor_decisions (tool_proposal_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orchestration_governor_decisions_created_at "
        "ON orchestration_governor_decisions (created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_orchestration_governor_decisions_created_at")
    op.execute(
        "DROP INDEX IF EXISTS "
        "ix_orchestration_governor_decisions_tool_proposal_id"
    )
    op.execute("DROP INDEX IF EXISTS ix_orchestration_governor_decisions_plan_id")
    op.execute("DROP INDEX IF EXISTS ix_orchestration_governor_decisions_approval_id")
    op.execute("DROP INDEX IF EXISTS ix_orchestration_governor_decisions_target_id")
    op.execute("DROP INDEX IF EXISTS ix_orchestration_governor_decisions_target_type")
    op.execute("DROP INDEX IF EXISTS ix_orchestration_governor_decisions_source_id")
    op.execute("DROP INDEX IF EXISTS ix_orchestration_governor_decisions_source_type")
    op.execute("DROP INDEX IF EXISTS ix_orchestration_governor_decisions_risk_level")
    op.execute("DROP INDEX IF EXISTS ix_orchestration_governor_decisions_status")
    op.execute("DROP INDEX IF EXISTS ix_orchestration_governor_decisions_decision_type")
    op.execute("DROP INDEX IF EXISTS ix_orchestration_governor_decisions_run_id")
    op.execute(
        "DROP INDEX IF EXISTS "
        "uq_orchestration_governor_decisions_idempotency_key"
    )
    op.execute("DROP TABLE IF EXISTS orchestration_governor_decisions CASCADE")

    op.execute("DROP INDEX IF EXISTS ix_orchestration_tool_proposals_created_at")
    op.execute("DROP INDEX IF EXISTS ix_orchestration_tool_proposals_approval_id")
    op.execute("DROP INDEX IF EXISTS ix_orchestration_tool_proposals_source_id")
    op.execute("DROP INDEX IF EXISTS ix_orchestration_tool_proposals_source_type")
    op.execute("DROP INDEX IF EXISTS ix_orchestration_tool_proposals_risk_level")
    op.execute("DROP INDEX IF EXISTS ix_orchestration_tool_proposals_status")
    op.execute("DROP INDEX IF EXISTS ix_orchestration_tool_proposals_capability")
    op.execute(
        "DROP INDEX IF EXISTS "
        "uq_orchestration_tool_proposals_idempotency_key"
    )
    op.execute("DROP TABLE IF EXISTS orchestration_tool_proposals CASCADE")

    op.execute("DROP INDEX IF EXISTS ix_orchestration_governor_runs_started_at")
    op.execute("DROP INDEX IF EXISTS ix_orchestration_governor_runs_snapshot_hash")
    op.execute("DROP INDEX IF EXISTS ix_orchestration_governor_runs_status")
    op.execute("DROP TABLE IF EXISTS orchestration_governor_runs CASCADE")
