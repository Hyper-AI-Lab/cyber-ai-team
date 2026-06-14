"""Add company context snapshots and sync runs.

Revision ID: 0009_company_context_snapshots
Revises: 0008_inbound_email_messages
Create Date: 2026-06-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_company_context_snapshots"
down_revision: str | None = "0008_inbound_email_messages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS company_context_snapshots (
            id VARCHAR(64) PRIMARY KEY,
            source VARCHAR(40) NOT NULL,
            source_id VARCHAR(200),
            source_hash VARCHAR(64) NOT NULL,
            company_namespace VARCHAR(200) NOT NULL,
            status VARCHAR(30) NOT NULL,
            normalized_profile JSON NOT NULL,
            erpnext_summary JSON NOT NULL,
            operating_model JSON NOT NULL,
            memory_ids JSON NOT NULL,
            agent_ids JSON NOT NULL,
            role_manifest_ids JSON NOT NULL,
            role_gap_ids JSON NOT NULL,
            approval_ids JSON NOT NULL,
            plan_ids JSON NOT NULL,
            errors JSON NOT NULL,
            created_by VARCHAR(200) NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            applied_at TIMESTAMP WITHOUT TIME ZONE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_company_context_snapshots_source_hash "
        "ON company_context_snapshots (source, source_hash)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_company_context_snapshots_source "
        "ON company_context_snapshots (source)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_company_context_snapshots_source_id "
        "ON company_context_snapshots (source_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_company_context_snapshots_source_hash "
        "ON company_context_snapshots (source_hash)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_company_context_snapshots_company_namespace "
        "ON company_context_snapshots (company_namespace)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_company_context_snapshots_status "
        "ON company_context_snapshots (status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_company_context_snapshots_created_at "
        "ON company_context_snapshots (created_at)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS company_context_sync_runs (
            id VARCHAR(64) PRIMARY KEY,
            source VARCHAR(40) NOT NULL,
            status VARCHAR(30) NOT NULL,
            dry_run BOOLEAN NOT NULL,
            apply_low_risk BOOLEAN NOT NULL,
            run_planner BOOLEAN NOT NULL,
            snapshot_id VARCHAR(64) REFERENCES company_context_snapshots(id),
            source_hash VARCHAR(64),
            company_namespace VARCHAR(200),
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
        "CREATE INDEX IF NOT EXISTS ix_company_context_sync_runs_source "
        "ON company_context_sync_runs (source)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_company_context_sync_runs_status "
        "ON company_context_sync_runs (status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_company_context_sync_runs_snapshot_id "
        "ON company_context_sync_runs (snapshot_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_company_context_sync_runs_source_hash "
        "ON company_context_sync_runs (source_hash)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_company_context_sync_runs_company_namespace "
        "ON company_context_sync_runs (company_namespace)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_company_context_sync_runs_started_at "
        "ON company_context_sync_runs (started_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_company_context_sync_runs_started_at")
    op.execute("DROP INDEX IF EXISTS ix_company_context_sync_runs_company_namespace")
    op.execute("DROP INDEX IF EXISTS ix_company_context_sync_runs_source_hash")
    op.execute("DROP INDEX IF EXISTS ix_company_context_sync_runs_snapshot_id")
    op.execute("DROP INDEX IF EXISTS ix_company_context_sync_runs_status")
    op.execute("DROP INDEX IF EXISTS ix_company_context_sync_runs_source")
    op.execute("DROP TABLE IF EXISTS company_context_sync_runs CASCADE")
    op.execute("DROP INDEX IF EXISTS ix_company_context_snapshots_created_at")
    op.execute("DROP INDEX IF EXISTS ix_company_context_snapshots_status")
    op.execute("DROP INDEX IF EXISTS ix_company_context_snapshots_company_namespace")
    op.execute("DROP INDEX IF EXISTS ix_company_context_snapshots_source_hash")
    op.execute("DROP INDEX IF EXISTS ix_company_context_snapshots_source_id")
    op.execute("DROP INDEX IF EXISTS ix_company_context_snapshots_source")
    op.execute("DROP INDEX IF EXISTS uq_company_context_snapshots_source_hash")
    op.execute("DROP TABLE IF EXISTS company_context_snapshots CASCADE")
