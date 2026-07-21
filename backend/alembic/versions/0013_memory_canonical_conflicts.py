"""Add memory canonical conflict records.

Revision ID: 0013_memory_canonical_conflicts
Revises: 0012_executive_company_os
Create Date: 2026-07-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013_memory_canonical_conflicts"
down_revision: str | None = "0012_executive_company_os"
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
        CREATE TABLE IF NOT EXISTS memory_canonical_conflicts (
            id VARCHAR(64) PRIMARY KEY,
            conflict_type VARCHAR(80) NOT NULL,
            severity VARCHAR(20) NOT NULL,
            status VARCHAR(30) NOT NULL,
            memory_id VARCHAR(64) NOT NULL REFERENCES memory_entries(id),
            memory_namespace VARCHAR(200) NOT NULL,
            company_namespace VARCHAR(200) NOT NULL,
            canonical_source_type VARCHAR(80) NOT NULL,
            canonical_source_id VARCHAR(200),
            canonical_source_hash VARCHAR(64),
            memory_source_hash VARCHAR(64),
            claim_path VARCHAR(240),
            title VARCHAR(240) NOT NULL,
            description TEXT NOT NULL,
            recommendation TEXT NOT NULL,
            memory_excerpt TEXT NOT NULL,
            canonical_excerpt TEXT NOT NULL,
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
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_memory_canonical_conflicts_dedupe_key "
        "ON memory_canonical_conflicts (dedupe_key)"
    )
    for column in (
        "conflict_type",
        "severity",
        "status",
        "memory_id",
        "memory_namespace",
        "company_namespace",
        "canonical_source_type",
        "canonical_source_id",
        "canonical_source_hash",
        "memory_source_hash",
        "claim_path",
        "dedupe_key",
        "created_at",
    ):
        _index("memory_canonical_conflicts", column)


def downgrade() -> None:
    for column in (
        "created_at",
        "dedupe_key",
        "claim_path",
        "memory_source_hash",
        "canonical_source_hash",
        "canonical_source_id",
        "canonical_source_type",
        "company_namespace",
        "memory_namespace",
        "memory_id",
        "status",
        "severity",
        "conflict_type",
    ):
        op.execute(f"DROP INDEX IF EXISTS ix_memory_canonical_conflicts_{column}")
    op.execute("DROP INDEX IF EXISTS uq_memory_canonical_conflicts_dedupe_key")
    op.execute("DROP TABLE IF EXISTS memory_canonical_conflicts CASCADE")
