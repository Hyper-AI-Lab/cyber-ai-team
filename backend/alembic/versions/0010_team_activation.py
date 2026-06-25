"""Add team activation, capability grants, and workflow templates.

Revision ID: 0010_team_activation
Revises: 0009_company_context_snapshots
Create Date: 2026-06-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_team_activation"
down_revision: str | None = "0009_company_context_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_capability_grants (
            id VARCHAR(64) PRIMARY KEY,
            agent_id VARCHAR(64) NOT NULL REFERENCES agents(id),
            role_gap_id VARCHAR(64) REFERENCES role_gaps(id),
            tool_name VARCHAR(100) NOT NULL,
            state VARCHAR(30) NOT NULL,
            risk_level VARCHAR(20) NOT NULL,
            side_effects BOOLEAN NOT NULL,
            approval_id VARCHAR(64) REFERENCES approval_requests(id),
            requested_by VARCHAR(200) NOT NULL,
            reason TEXT NOT NULL,
            metadata JSON NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            activated_at TIMESTAMP WITHOUT TIME ZONE,
            revoked_at TIMESTAMP WITHOUT TIME ZONE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_capability_grants_agent_tool "
        "ON agent_capability_grants (agent_id, tool_name)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_capability_grants_agent_id "
        "ON agent_capability_grants (agent_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_capability_grants_role_gap_id "
        "ON agent_capability_grants (role_gap_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_capability_grants_tool_name "
        "ON agent_capability_grants (tool_name)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_capability_grants_state "
        "ON agent_capability_grants (state)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_capability_grants_risk_level "
        "ON agent_capability_grants (risk_level)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_capability_grants_approval_id "
        "ON agent_capability_grants (approval_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_capability_grants_created_at "
        "ON agent_capability_grants (created_at)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_templates (
            id VARCHAR(64) PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            description TEXT NOT NULL,
            category VARCHAR(100) NOT NULL,
            version VARCHAR(40) NOT NULL,
            graph_definition JSON NOT NULL,
            default_trigger_type VARCHAR(30) NOT NULL,
            default_trigger_config JSON NOT NULL,
            status VARCHAR(30) NOT NULL,
            is_core BOOLEAN NOT NULL,
            metadata JSON NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_templates_name_version "
        "ON workflow_templates (name, version)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_templates_name "
        "ON workflow_templates (name)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_templates_category "
        "ON workflow_templates (category)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_templates_version "
        "ON workflow_templates (version)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_templates_status "
        "ON workflow_templates (status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_templates_is_core "
        "ON workflow_templates (is_core)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_templates_created_at "
        "ON workflow_templates (created_at)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS team_activation_runs (
            id VARCHAR(64) PRIMARY KEY,
            source_snapshot_id VARCHAR(64) REFERENCES company_context_snapshots(id),
            source_hash VARCHAR(64),
            company_namespace VARCHAR(200),
            status VARCHAR(30) NOT NULL,
            dry_run BOOLEAN NOT NULL,
            apply_safe_roles BOOLEAN NOT NULL,
            request_high_risk_grants BOOLEAN NOT NULL,
            counts JSON NOT NULL,
            result JSON NOT NULL,
            errors JSON NOT NULL,
            actor VARCHAR(200) NOT NULL,
            started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            completed_at TIMESTAMP WITHOUT TIME ZONE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_team_activation_runs_source_snapshot_id "
        "ON team_activation_runs (source_snapshot_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_team_activation_runs_source_hash "
        "ON team_activation_runs (source_hash)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_team_activation_runs_company_namespace "
        "ON team_activation_runs (company_namespace)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_team_activation_runs_status "
        "ON team_activation_runs (status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_team_activation_runs_started_at "
        "ON team_activation_runs (started_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_team_activation_runs_started_at")
    op.execute("DROP INDEX IF EXISTS ix_team_activation_runs_status")
    op.execute("DROP INDEX IF EXISTS ix_team_activation_runs_company_namespace")
    op.execute("DROP INDEX IF EXISTS ix_team_activation_runs_source_hash")
    op.execute("DROP INDEX IF EXISTS ix_team_activation_runs_source_snapshot_id")
    op.execute("DROP TABLE IF EXISTS team_activation_runs CASCADE")
    op.execute("DROP INDEX IF EXISTS ix_workflow_templates_created_at")
    op.execute("DROP INDEX IF EXISTS ix_workflow_templates_is_core")
    op.execute("DROP INDEX IF EXISTS ix_workflow_templates_status")
    op.execute("DROP INDEX IF EXISTS ix_workflow_templates_version")
    op.execute("DROP INDEX IF EXISTS ix_workflow_templates_category")
    op.execute("DROP INDEX IF EXISTS ix_workflow_templates_name")
    op.execute("DROP INDEX IF EXISTS uq_workflow_templates_name_version")
    op.execute("DROP TABLE IF EXISTS workflow_templates CASCADE")
    op.execute("DROP INDEX IF EXISTS ix_agent_capability_grants_created_at")
    op.execute("DROP INDEX IF EXISTS ix_agent_capability_grants_approval_id")
    op.execute("DROP INDEX IF EXISTS ix_agent_capability_grants_risk_level")
    op.execute("DROP INDEX IF EXISTS ix_agent_capability_grants_state")
    op.execute("DROP INDEX IF EXISTS ix_agent_capability_grants_tool_name")
    op.execute("DROP INDEX IF EXISTS ix_agent_capability_grants_role_gap_id")
    op.execute("DROP INDEX IF EXISTS ix_agent_capability_grants_agent_id")
    op.execute("DROP INDEX IF EXISTS uq_agent_capability_grants_agent_tool")
    op.execute("DROP TABLE IF EXISTS agent_capability_grants CASCADE")
