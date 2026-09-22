"""Add immutable category-partitioned audit archives.

Revision ID: 0026_immutable_audit_archives
Revises: 0025_bounded_audit_lifecycle_state
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026_immutable_audit_archives"
down_revision: str | None = "0025_bounded_audit_lifecycle_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ARCHIVE_PARTITIONS = ("security", "governance", "operational")


def upgrade() -> None:
    op.create_table(
        "audit_event_archives",
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("first_event_at", sa.DateTime(), nullable=False),
        sa.Column("last_event_at", sa.DateTime(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("encoding", sa.String(length=30), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("raw_size", sa.Integer(), nullable=False),
        sa.Column("compressed_size", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("category", "id"),
        sa.UniqueConstraint(
            "category",
            "content_hash",
            name="uq_audit_event_archives_category_hash",
        ),
        postgresql_partition_by="LIST (category)",
    )
    for category in ARCHIVE_PARTITIONS:
        op.execute(
            f"""
            CREATE TABLE audit_event_archives_{category}
            PARTITION OF audit_event_archives
            FOR VALUES IN ('{category}')
            """
        )
    op.execute(
        """
        CREATE TABLE audit_event_archives_default
        PARTITION OF audit_event_archives DEFAULT
        """
    )
    for column in (
        "period_start",
        "period_end",
        "first_event_at",
        "last_event_at",
        "content_hash",
        "created_at",
    ):
        op.create_index(
            op.f(f"ix_audit_event_archives_{column}"),
            "audit_event_archives",
            [column],
        )


def downgrade() -> None:
    op.drop_table("audit_event_archives")
