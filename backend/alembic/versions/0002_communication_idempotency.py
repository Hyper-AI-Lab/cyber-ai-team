"""Add communication idempotency keys.

Revision ID: 0002_communication_idempotency
Revises: 0001_initial_schema
Create Date: 2026-05-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_communication_idempotency"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE communication_logs "
        "ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(128)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_communication_logs_idempotency_key "
        "ON communication_logs (idempotency_key) WHERE idempotency_key IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_communication_logs_idempotency_key")
    op.execute("ALTER TABLE communication_logs DROP COLUMN IF EXISTS idempotency_key")
