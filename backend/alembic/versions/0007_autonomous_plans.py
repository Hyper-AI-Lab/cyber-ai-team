"""Add autonomous planning tables.

Revision ID: 0007_autonomous_plans
Revises: 0006_memory_steward_findings
Create Date: 2026-06-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_autonomous_plans"
down_revision: str | None = "0006_memory_steward_findings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS autonomous_plans (
            id VARCHAR(64) PRIMARY KEY,
            title VARCHAR(200) NOT NULL,
            objective TEXT NOT NULL,
            source_type VARCHAR(80) NOT NULL,
            source_id VARCHAR(200) NOT NULL,
            status VARCHAR(30) NOT NULL,
            priority VARCHAR(20) NOT NULL,
            created_by VARCHAR(200) NOT NULL,
            context JSON NOT NULL,
            summary JSON NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            completed_at TIMESTAMP WITHOUT TIME ZONE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS autonomous_tasks (
            id VARCHAR(64) PRIMARY KEY,
            plan_id VARCHAR(64) NOT NULL REFERENCES autonomous_plans(id),
            sequence INTEGER NOT NULL,
            title VARCHAR(200) NOT NULL,
            description TEXT NOT NULL,
            task_type VARCHAR(80) NOT NULL,
            status VARCHAR(30) NOT NULL,
            agent_id VARCHAR(64),
            target_type VARCHAR(100),
            target_id VARCHAR(200),
            action_payload JSON NOT NULL,
            result JSON NOT NULL,
            error TEXT,
            approval_id VARCHAR(64),
            autonomous_allowed BOOLEAN NOT NULL,
            risk_level VARCHAR(20) NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            completed_at TIMESTAMP WITHOUT TIME ZONE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_autonomous_plans_source_type "
        "ON autonomous_plans (source_type)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_autonomous_plans_source_id "
        "ON autonomous_plans (source_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_autonomous_plans_status "
        "ON autonomous_plans (status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_autonomous_plans_priority "
        "ON autonomous_plans (priority)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_autonomous_plans_created_at "
        "ON autonomous_plans (created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_autonomous_plans_source_active "
        "ON autonomous_plans (source_type, source_id, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_autonomous_tasks_plan_id "
        "ON autonomous_tasks (plan_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_autonomous_tasks_task_type "
        "ON autonomous_tasks (task_type)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_autonomous_tasks_status "
        "ON autonomous_tasks (status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_autonomous_tasks_agent_id "
        "ON autonomous_tasks (agent_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_autonomous_tasks_target_type "
        "ON autonomous_tasks (target_type)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_autonomous_tasks_target_id "
        "ON autonomous_tasks (target_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_autonomous_tasks_approval_id "
        "ON autonomous_tasks (approval_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_autonomous_tasks_risk_level "
        "ON autonomous_tasks (risk_level)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_autonomous_tasks_created_at "
        "ON autonomous_tasks (created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_autonomous_tasks_created_at")
    op.execute("DROP INDEX IF EXISTS ix_autonomous_tasks_risk_level")
    op.execute("DROP INDEX IF EXISTS ix_autonomous_tasks_approval_id")
    op.execute("DROP INDEX IF EXISTS ix_autonomous_tasks_target_id")
    op.execute("DROP INDEX IF EXISTS ix_autonomous_tasks_target_type")
    op.execute("DROP INDEX IF EXISTS ix_autonomous_tasks_agent_id")
    op.execute("DROP INDEX IF EXISTS ix_autonomous_tasks_status")
    op.execute("DROP INDEX IF EXISTS ix_autonomous_tasks_task_type")
    op.execute("DROP INDEX IF EXISTS ix_autonomous_tasks_plan_id")
    op.execute("DROP INDEX IF EXISTS ix_autonomous_plans_source_active")
    op.execute("DROP INDEX IF EXISTS ix_autonomous_plans_created_at")
    op.execute("DROP INDEX IF EXISTS ix_autonomous_plans_priority")
    op.execute("DROP INDEX IF EXISTS ix_autonomous_plans_status")
    op.execute("DROP INDEX IF EXISTS ix_autonomous_plans_source_id")
    op.execute("DROP INDEX IF EXISTS ix_autonomous_plans_source_type")
    op.execute("DROP TABLE IF EXISTS autonomous_tasks CASCADE")
    op.execute("DROP TABLE IF EXISTS autonomous_plans CASCADE")
