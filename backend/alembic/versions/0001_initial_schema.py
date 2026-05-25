"""Initial Cyber-Team schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-05-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agents (
            id VARCHAR(64) PRIMARY KEY,
            role_family VARCHAR(100) NOT NULL,
            role_name VARCHAR(200) NOT NULL,
            instructions TEXT NOT NULL,
            tools JSON NOT NULL,
            memory_namespace VARCHAR(200) NOT NULL,
            approval_policy VARCHAR(50) NOT NULL,
            status VARCHAR(20) NOT NULL,
            config JSON NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workflows (
            id VARCHAR(64) PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            description TEXT,
            graph_definition JSON NOT NULL,
            status VARCHAR(20) NOT NULL,
            trigger_type VARCHAR(30) NOT NULL,
            trigger_config JSON NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS role_manifests (
            id VARCHAR(64) PRIMARY KEY,
            family VARCHAR(100) NOT NULL,
            name VARCHAR(200) NOT NULL,
            description TEXT NOT NULL,
            instructions_template TEXT NOT NULL,
            default_tools JSON NOT NULL,
            memory_namespace VARCHAR(200) NOT NULL,
            approval_policy VARCHAR(50) NOT NULL,
            success_metrics JSON NOT NULL,
            is_core BOOLEAN NOT NULL,
            config JSON NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_runs (
            id VARCHAR(64) PRIMARY KEY,
            workflow_id VARCHAR(64) NOT NULL REFERENCES workflows(id),
            status VARCHAR(20) NOT NULL,
            current_node VARCHAR(200),
            state JSON NOT NULL,
            result JSON,
            error TEXT,
            started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            completed_at TIMESTAMP WITHOUT TIME ZONE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_entries (
            id VARCHAR(64) PRIMARY KEY,
            agent_id VARCHAR(64) REFERENCES agents(id),
            memory_type VARCHAR(30) NOT NULL,
            namespace VARCHAR(200) NOT NULL,
            content TEXT NOT NULL,
            metadata JSON NOT NULL,
            importance FLOAT NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            expires_at TIMESTAMP WITHOUT TIME ZONE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS approval_requests (
            id VARCHAR(64) PRIMARY KEY,
            agent_id VARCHAR(64),
            action_type VARCHAR(100) NOT NULL,
            action_description TEXT NOT NULL,
            action_payload JSON NOT NULL,
            requester VARCHAR(200) NOT NULL,
            requester_type VARCHAR(30) NOT NULL,
            risk_level VARCHAR(20) NOT NULL,
            target_type VARCHAR(100),
            target_id VARCHAR(200),
            status VARCHAR(20) NOT NULL,
            reviewer VARCHAR(200),
            review_note TEXT,
            consumed_at TIMESTAMP WITHOUT TIME ZONE,
            expires_at TIMESTAMP WITHOUT TIME ZONE,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            resolved_at TIMESTAMP WITHOUT TIME ZONE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            id VARCHAR(64) PRIMARY KEY,
            event_type VARCHAR(100) NOT NULL,
            actor VARCHAR(200) NOT NULL,
            actor_type VARCHAR(30) NOT NULL,
            resource_type VARCHAR(100),
            resource_id VARCHAR(200),
            action VARCHAR(100),
            outcome VARCHAR(30) NOT NULL,
            metadata JSON NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS communication_logs (
            id VARCHAR(64) PRIMARY KEY,
            agent_id VARCHAR(64) REFERENCES agents(id),
            channel VARCHAR(30) NOT NULL,
            direction VARCHAR(10) NOT NULL,
            recipient VARCHAR(200) NOT NULL,
            content TEXT NOT NULL,
            metadata JSON NOT NULL,
            status VARCHAR(20) NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )

    op.execute("ALTER TABLE approval_requests ALTER COLUMN agent_id DROP NOT NULL")
    op.execute(
        "ALTER TABLE approval_requests "
        "DROP CONSTRAINT IF EXISTS approval_requests_agent_id_fkey"
    )
    op.execute(
        "ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS "
        "requester VARCHAR(200) DEFAULT 'system' NOT NULL"
    )
    op.execute(
        "ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS "
        "requester_type VARCHAR(30) DEFAULT 'system' NOT NULL"
    )
    op.execute(
        "ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS "
        "risk_level VARCHAR(20) DEFAULT 'medium' NOT NULL"
    )
    op.execute(
        "ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS target_type VARCHAR(100)"
    )
    op.execute(
        "ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS target_id VARCHAR(200)"
    )
    op.execute(
        "ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS "
        "consumed_at TIMESTAMP WITHOUT TIME ZONE"
    )
    op.execute(
        "ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS "
        "expires_at TIMESTAMP WITHOUT TIME ZONE"
    )

    op.execute("CREATE INDEX IF NOT EXISTS ix_agents_role_family ON agents (role_family)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_events_actor ON audit_events (actor)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_events_created_at ON audit_events (created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_events_event_type ON audit_events (event_type)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_events_outcome ON audit_events (outcome)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_events_resource_id "
        "ON audit_events (resource_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_events_resource_type "
        "ON audit_events (resource_type)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_entries_memory_type "
        "ON memory_entries (memory_type)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_entries_namespace "
        "ON memory_entries (namespace)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_role_manifests_family "
        "ON role_manifests (family)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_role_manifests_name "
        "ON role_manifests (name)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS communication_logs CASCADE")
    op.execute("DROP TABLE IF EXISTS audit_events CASCADE")
    op.execute("DROP TABLE IF EXISTS approval_requests CASCADE")
    op.execute("DROP TABLE IF EXISTS memory_entries CASCADE")
    op.execute("DROP TABLE IF EXISTS workflow_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS role_manifests CASCADE")
    op.execute("DROP TABLE IF EXISTS workflows CASCADE")
    op.execute("DROP TABLE IF EXISTS agents CASCADE")
