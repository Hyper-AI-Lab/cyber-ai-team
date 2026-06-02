"""Add invocation memory traces.

Revision ID: 0005_memory_traces
Revises: 0004_role_gaps
Create Date: 2026-06-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_memory_traces"
down_revision: str | None = "0004_role_gaps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_traces (
            id VARCHAR(64) PRIMARY KEY,
            invocation_id VARCHAR(64) NOT NULL,
            agent_id VARCHAR(64),
            conversation_id VARCHAR(64),
            source_type VARCHAR(50) NOT NULL,
            task_excerpt TEXT NOT NULL,
            memory_namespace VARCHAR(200),
            read_policy JSON NOT NULL,
            write_policy JSON NOT NULL,
            recalled_memory_ids JSON NOT NULL,
            written_memory_ids JSON NOT NULL,
            recall_count INTEGER NOT NULL,
            write_count INTEGER NOT NULL,
            errors JSON NOT NULL,
            metadata JSON NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_traces_invocation_id "
        "ON memory_traces (invocation_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_traces_agent_id "
        "ON memory_traces (agent_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_traces_conversation_id "
        "ON memory_traces (conversation_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_traces_source_type "
        "ON memory_traces (source_type)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_traces_memory_namespace "
        "ON memory_traces (memory_namespace)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_traces_created_at "
        "ON memory_traces (created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_memory_traces_created_at")
    op.execute("DROP INDEX IF EXISTS ix_memory_traces_memory_namespace")
    op.execute("DROP INDEX IF EXISTS ix_memory_traces_source_type")
    op.execute("DROP INDEX IF EXISTS ix_memory_traces_conversation_id")
    op.execute("DROP INDEX IF EXISTS ix_memory_traces_agent_id")
    op.execute("DROP INDEX IF EXISTS ix_memory_traces_invocation_id")
    op.execute("DROP TABLE IF EXISTS memory_traces CASCADE")
