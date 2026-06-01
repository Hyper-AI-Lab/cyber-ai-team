"""Add persistent role gap events.

Revision ID: 0004_role_gaps
Revises: 0003_retention_indexes
Create Date: 2026-06-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_role_gaps"
down_revision: str | None = "0003_retention_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS role_gaps (
            id VARCHAR(64) PRIMARY KEY,
            title VARCHAR(200) NOT NULL,
            description TEXT NOT NULL,
            status VARCHAR(30) NOT NULL,
            severity VARCHAR(20) NOT NULL,
            source_agent_id VARCHAR(64),
            source_type VARCHAR(30) NOT NULL,
            company_namespace VARCHAR(200) NOT NULL,
            capability VARCHAR(100),
            requested_tools JSON NOT NULL,
            context JSON NOT NULL,
            proposed_role JSON NOT NULL,
            resolution JSON NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            resolved_at TIMESTAMP WITHOUT TIME ZONE
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_role_gaps_status ON role_gaps (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_role_gaps_severity ON role_gaps (severity)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_role_gaps_source_agent_id "
        "ON role_gaps (source_agent_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_role_gaps_company_namespace "
        "ON role_gaps (company_namespace)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_role_gaps_capability ON role_gaps (capability)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_role_gaps_created_at ON role_gaps (created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_role_gaps_resolved_at ON role_gaps (resolved_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_role_gaps_resolved_at")
    op.execute("DROP INDEX IF EXISTS ix_role_gaps_created_at")
    op.execute("DROP INDEX IF EXISTS ix_role_gaps_capability")
    op.execute("DROP INDEX IF EXISTS ix_role_gaps_company_namespace")
    op.execute("DROP INDEX IF EXISTS ix_role_gaps_source_agent_id")
    op.execute("DROP INDEX IF EXISTS ix_role_gaps_severity")
    op.execute("DROP INDEX IF EXISTS ix_role_gaps_status")
    op.execute("DROP TABLE IF EXISTS role_gaps CASCADE")
