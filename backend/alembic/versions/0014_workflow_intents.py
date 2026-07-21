"""Add generated workflow intents.

Revision ID: 0014_workflow_intents
Revises: 0013_memory_canonical_conflicts
Create Date: 2026-07-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014_workflow_intents"
down_revision: str | None = "0013_memory_canonical_conflicts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _index(table: str, column: str) -> None:
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{table}_{column} "
        f"ON {table} ({column})"
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_intents (
            id VARCHAR(64) PRIMARY KEY,
            title VARCHAR(240) NOT NULL,
            description TEXT NOT NULL,
            status VARCHAR(30) NOT NULL,
            category VARCHAR(100) NOT NULL,
            business_function VARCHAR(100) NOT NULL,
            source_type VARCHAR(80) NOT NULL,
            source_id VARCHAR(200),
            source_hash VARCHAR(64),
            company_namespace VARCHAR(200) NOT NULL,
            role_family VARCHAR(100),
            role_name VARCHAR(200),
            capability VARCHAR(100),
            risk_level VARCHAR(20) NOT NULL,
            trigger_type VARCHAR(30) NOT NULL,
            trigger_config JSON NOT NULL,
            graph_definition JSON NOT NULL,
            requested_tools JSON NOT NULL,
            required_agents JSON NOT NULL,
            tool_readiness JSON NOT NULL,
            readiness JSON NOT NULL,
            approval_required BOOLEAN NOT NULL,
            approval_id VARCHAR(64) REFERENCES approval_requests(id),
            workflow_template_id VARCHAR(64) REFERENCES workflow_templates(id),
            workflow_id VARCHAR(64) REFERENCES workflows(id),
            proposed_by VARCHAR(200) NOT NULL,
            evidence JSON NOT NULL,
            resolution JSON NOT NULL,
            dedupe_key VARCHAR(240) NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            resolved_at TIMESTAMP WITHOUT TIME ZONE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_intents_dedupe_key "
        "ON workflow_intents (dedupe_key)"
    )
    for column in (
        "status",
        "category",
        "business_function",
        "source_type",
        "source_id",
        "source_hash",
        "company_namespace",
        "role_family",
        "role_name",
        "capability",
        "risk_level",
        "approval_required",
        "approval_id",
        "workflow_template_id",
        "workflow_id",
        "dedupe_key",
        "created_at",
    ):
        _index("workflow_intents", column)


def downgrade() -> None:
    for column in (
        "created_at",
        "dedupe_key",
        "workflow_id",
        "workflow_template_id",
        "approval_id",
        "approval_required",
        "risk_level",
        "capability",
        "role_name",
        "role_family",
        "company_namespace",
        "source_hash",
        "source_id",
        "source_type",
        "business_function",
        "category",
        "status",
    ):
        op.execute(f"DROP INDEX IF EXISTS ix_workflow_intents_{column}")
    op.execute("DROP INDEX IF EXISTS uq_workflow_intents_dedupe_key")
    op.execute("DROP TABLE IF EXISTS workflow_intents CASCADE")
