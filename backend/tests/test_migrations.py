import io
from contextlib import redirect_stdout
from pathlib import Path

from alembic.config import Config

from alembic import command

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def alembic_config() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return cfg


def render_offline_upgrade_sql() -> str:
    stream = io.StringIO()
    with redirect_stdout(stream):
        command.upgrade(alembic_config(), "head", sql=True)
    return stream.getvalue()


def render_offline_downgrade_sql() -> str:
    stream = io.StringIO()
    with redirect_stdout(stream):
        command.downgrade(alembic_config(), "0001_initial_schema:base", sql=True)
    return stream.getvalue()


def render_role_gap_downgrade_sql() -> str:
    stream = io.StringIO()
    with redirect_stdout(stream):
        command.downgrade(alembic_config(), "0004_role_gaps:0003_retention_indexes", sql=True)
    return stream.getvalue()


def render_memory_trace_downgrade_sql() -> str:
    stream = io.StringIO()
    with redirect_stdout(stream):
        command.downgrade(alembic_config(), "0005_memory_traces:0004_role_gaps", sql=True)
    return stream.getvalue()


def render_memory_steward_downgrade_sql() -> str:
    stream = io.StringIO()
    with redirect_stdout(stream):
        command.downgrade(
            alembic_config(),
            "0006_memory_steward_findings:0005_memory_traces",
            sql=True,
        )
    return stream.getvalue()


def test_initial_migration_offline_sql_contains_core_tables_and_indexes():
    sql = render_offline_upgrade_sql()

    for table in [
        "agents",
        "workflows",
        "workflow_runs",
        "memory_entries",
        "approval_requests",
        "audit_events",
        "communication_logs",
        "role_manifests",
        "role_gaps",
        "memory_traces",
        "memory_steward_findings",
        "autonomous_plans",
        "autonomous_tasks",
    ]:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql

    assert "CREATE INDEX IF NOT EXISTS ix_agents_role_family" in sql
    assert "CREATE INDEX IF NOT EXISTS ix_memory_entries_namespace" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS ix_role_manifests_name" in sql
    assert "ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(128)" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS ix_communication_logs_idempotency_key" in sql
    assert "CREATE INDEX IF NOT EXISTS ix_communication_logs_created_at" in sql
    assert "CREATE INDEX IF NOT EXISTS ix_memory_entries_expires_at" in sql
    assert "CREATE INDEX IF NOT EXISTS ix_workflow_runs_completed_at" in sql
    assert "CREATE INDEX IF NOT EXISTS ix_role_gaps_status" in sql
    assert "CREATE INDEX IF NOT EXISTS ix_role_gaps_company_namespace" in sql
    assert "CREATE INDEX IF NOT EXISTS ix_role_gaps_created_at" in sql
    assert "CREATE INDEX IF NOT EXISTS ix_memory_traces_invocation_id" in sql
    assert "CREATE INDEX IF NOT EXISTS ix_memory_traces_agent_id" in sql
    assert "CREATE INDEX IF NOT EXISTS ix_memory_traces_created_at" in sql
    assert "CREATE INDEX IF NOT EXISTS ix_memory_steward_findings_status" in sql
    assert "CREATE INDEX IF NOT EXISTS ix_memory_steward_findings_finding_type" in sql
    assert "CREATE INDEX IF NOT EXISTS ix_memory_steward_findings_created_at" in sql
    assert "CREATE INDEX IF NOT EXISTS ix_autonomous_plans_status" in sql
    assert "CREATE INDEX IF NOT EXISTS ix_autonomous_plans_source_active" in sql
    assert "CREATE INDEX IF NOT EXISTS ix_autonomous_tasks_status" in sql
    assert "CREATE INDEX IF NOT EXISTS ix_autonomous_tasks_approval_id" in sql


def test_initial_migration_preserves_pre_alembic_approval_compatibility():
    sql = render_offline_upgrade_sql()

    assert "ALTER TABLE approval_requests ALTER COLUMN agent_id DROP NOT NULL" in sql
    assert "DROP CONSTRAINT IF EXISTS approval_requests_agent_id_fkey" in sql
    assert "ADD COLUMN IF NOT EXISTS requester VARCHAR(200)" in sql
    assert "ADD COLUMN IF NOT EXISTS requester_type VARCHAR(30)" in sql
    assert "ADD COLUMN IF NOT EXISTS risk_level VARCHAR(20)" in sql
    assert "ADD COLUMN IF NOT EXISTS target_type VARCHAR(100)" in sql
    assert "ADD COLUMN IF NOT EXISTS target_id VARCHAR(200)" in sql
    assert "ADD COLUMN IF NOT EXISTS consumed_at TIMESTAMP WITHOUT TIME ZONE" in sql


def test_initial_migration_downgrade_policy_is_explicit_destructive_cleanup():
    sql = render_offline_downgrade_sql()

    for table in [
        "communication_logs",
        "audit_events",
        "approval_requests",
        "memory_entries",
        "workflow_runs",
        "role_manifests",
        "workflows",
        "agents",
    ]:
        assert f"DROP TABLE IF EXISTS {table} CASCADE" in sql


def test_role_gap_migration_downgrade_removes_role_gap_table_and_indexes():
    sql = render_role_gap_downgrade_sql()

    assert "DROP INDEX IF EXISTS ix_role_gaps_status" in sql
    assert "DROP INDEX IF EXISTS ix_role_gaps_company_namespace" in sql
    assert "DROP TABLE IF EXISTS role_gaps CASCADE" in sql


def test_memory_trace_migration_downgrade_removes_trace_table_and_indexes():
    sql = render_memory_trace_downgrade_sql()

    assert "DROP INDEX IF EXISTS ix_memory_traces_invocation_id" in sql
    assert "DROP INDEX IF EXISTS ix_memory_traces_created_at" in sql
    assert "DROP TABLE IF EXISTS memory_traces CASCADE" in sql


def test_memory_steward_migration_downgrade_removes_findings_table_and_indexes():
    sql = render_memory_steward_downgrade_sql()

    assert "DROP INDEX IF EXISTS ix_memory_steward_findings_status" in sql
    assert "DROP INDEX IF EXISTS ix_memory_steward_findings_created_at" in sql
    assert "DROP TABLE IF EXISTS memory_steward_findings CASCADE" in sql
