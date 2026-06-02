"""Add memory steward findings.

Revision ID: 0006_memory_steward_findings
Revises: 0005_memory_traces
Create Date: 2026-06-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_memory_steward_findings"
down_revision: str | None = "0005_memory_traces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_steward_findings (
            id VARCHAR(64) PRIMARY KEY,
            finding_type VARCHAR(80) NOT NULL,
            severity VARCHAR(20) NOT NULL,
            status VARCHAR(30) NOT NULL,
            agent_id VARCHAR(64),
            memory_namespace VARCHAR(200),
            company_namespace VARCHAR(200),
            title VARCHAR(200) NOT NULL,
            description TEXT NOT NULL,
            recommendation TEXT NOT NULL,
            trace_ids JSON NOT NULL,
            evidence JSON NOT NULL,
            metadata JSON NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            resolved_at TIMESTAMP WITHOUT TIME ZONE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_steward_findings_finding_type "
        "ON memory_steward_findings (finding_type)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_steward_findings_severity "
        "ON memory_steward_findings (severity)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_steward_findings_status "
        "ON memory_steward_findings (status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_steward_findings_agent_id "
        "ON memory_steward_findings (agent_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_steward_findings_memory_namespace "
        "ON memory_steward_findings (memory_namespace)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_steward_findings_company_namespace "
        "ON memory_steward_findings (company_namespace)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_steward_findings_created_at "
        "ON memory_steward_findings (created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_memory_steward_findings_created_at")
    op.execute("DROP INDEX IF EXISTS ix_memory_steward_findings_company_namespace")
    op.execute("DROP INDEX IF EXISTS ix_memory_steward_findings_memory_namespace")
    op.execute("DROP INDEX IF EXISTS ix_memory_steward_findings_agent_id")
    op.execute("DROP INDEX IF EXISTS ix_memory_steward_findings_status")
    op.execute("DROP INDEX IF EXISTS ix_memory_steward_findings_severity")
    op.execute("DROP INDEX IF EXISTS ix_memory_steward_findings_finding_type")
    op.execute("DROP TABLE IF EXISTS memory_steward_findings CASCADE")
