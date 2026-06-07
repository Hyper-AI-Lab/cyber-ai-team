"""Add inbound email messages table.

Revision ID: 0008_inbound_email_messages
Revises: 0007_autonomous_plans
Create Date: 2026-06-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_inbound_email_messages"
down_revision: str | None = "0007_autonomous_plans"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS inbound_email_messages (
            id VARCHAR(64) PRIMARY KEY,
            provider VARCHAR(30) NOT NULL,
            mailbox VARCHAR(200) NOT NULL,
            provider_uid VARCHAR(200) NOT NULL,
            message_id VARCHAR(500),
            from_address VARCHAR(500) NOT NULL,
            to_addresses JSON NOT NULL,
            cc_addresses JSON NOT NULL,
            subject VARCHAR(500) NOT NULL,
            text_body TEXT NOT NULL,
            html_body TEXT,
            snippet TEXT NOT NULL,
            status VARCHAR(30) NOT NULL,
            received_at TIMESTAMP WITHOUT TIME ZONE,
            first_seen_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            last_seen_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            metadata JSON NOT NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_inbound_email_provider_mailbox_uid "
        "ON inbound_email_messages (provider, mailbox, provider_uid)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_inbound_email_messages_provider "
        "ON inbound_email_messages (provider)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_inbound_email_messages_mailbox "
        "ON inbound_email_messages (mailbox)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_inbound_email_messages_message_id "
        "ON inbound_email_messages (message_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_inbound_email_messages_from_address "
        "ON inbound_email_messages (from_address)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_inbound_email_messages_status "
        "ON inbound_email_messages (status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_inbound_email_messages_received_at "
        "ON inbound_email_messages (received_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_inbound_email_messages_first_seen_at "
        "ON inbound_email_messages (first_seen_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_inbound_email_messages_first_seen_at")
    op.execute("DROP INDEX IF EXISTS ix_inbound_email_messages_received_at")
    op.execute("DROP INDEX IF EXISTS ix_inbound_email_messages_status")
    op.execute("DROP INDEX IF EXISTS ix_inbound_email_messages_from_address")
    op.execute("DROP INDEX IF EXISTS ix_inbound_email_messages_message_id")
    op.execute("DROP INDEX IF EXISTS ix_inbound_email_messages_mailbox")
    op.execute("DROP INDEX IF EXISTS ix_inbound_email_messages_provider")
    op.execute("DROP INDEX IF EXISTS uq_inbound_email_provider_mailbox_uid")
    op.execute("DROP TABLE IF EXISTS inbound_email_messages CASCADE")
