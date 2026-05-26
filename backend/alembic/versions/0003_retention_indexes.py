"""Add retention and lifecycle indexes.

Revision ID: 0003_retention_indexes
Revises: 0002_communication_idempotency
Create Date: 2026-05-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_retention_indexes"
down_revision: str | None = "0002_communication_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_communication_logs_created_at "
        "ON communication_logs (created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_entries_created_at "
        "ON memory_entries (created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_entries_expires_at "
        "ON memory_entries (expires_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_runs_completed_at "
        "ON workflow_runs (completed_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_runs_status "
        "ON workflow_runs (status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_approval_requests_expires_at "
        "ON approval_requests (expires_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_approval_requests_resolved_at "
        "ON approval_requests (resolved_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_approval_requests_resolved_at")
    op.execute("DROP INDEX IF EXISTS ix_approval_requests_expires_at")
    op.execute("DROP INDEX IF EXISTS ix_workflow_runs_status")
    op.execute("DROP INDEX IF EXISTS ix_workflow_runs_completed_at")
    op.execute("DROP INDEX IF EXISTS ix_memory_entries_expires_at")
    op.execute("DROP INDEX IF EXISTS ix_memory_entries_created_at")
    op.execute("DROP INDEX IF EXISTS ix_communication_logs_created_at")
