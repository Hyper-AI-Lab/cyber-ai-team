"""Add autonomous executive company OS persistence.

Revision ID: 0012_executive_company_os
Revises: 0011_orchestration_governor
Create Date: 2026-07-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012_executive_company_os"
down_revision: str | None = "0011_orchestration_governor"
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
        CREATE TABLE IF NOT EXISTS autonomy_policies (
            id VARCHAR(64) PRIMARY KEY,
            mode VARCHAR(60) NOT NULL,
            resource_policy VARCHAR(60) NOT NULL,
            paused BOOLEAN NOT NULL,
            thresholds JSON NOT NULL,
            policy JSON NOT NULL,
            updated_by VARCHAR(200) NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    _index("autonomy_policies", "mode")
    _index("autonomy_policies", "resource_policy")
    _index("autonomy_policies", "paused")
    _index("autonomy_policies", "created_at")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS company_objectives (
            id VARCHAR(64) PRIMARY KEY,
            title VARCHAR(240) NOT NULL,
            description TEXT NOT NULL,
            status VARCHAR(30) NOT NULL,
            priority VARCHAR(20) NOT NULL,
            target JSON NOT NULL,
            tags JSON NOT NULL,
            created_by VARCHAR(200) NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    _index("company_objectives", "status")
    _index("company_objectives", "priority")
    _index("company_objectives", "created_at")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS operating_kpi_definitions (
            id VARCHAR(64) PRIMARY KEY,
            key VARCHAR(120) NOT NULL,
            title VARCHAR(240) NOT NULL,
            description TEXT NOT NULL,
            unit VARCHAR(40) NOT NULL,
            comparison VARCHAR(20) NOT NULL,
            target_value FLOAT NOT NULL,
            source VARCHAR(100) NOT NULL,
            status VARCHAR(30) NOT NULL,
            tags JSON NOT NULL,
            metadata JSON NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_operating_kpi_definitions_key "
        "ON operating_kpi_definitions (key)"
    )
    _index("operating_kpi_definitions", "key")
    _index("operating_kpi_definitions", "status")
    _index("operating_kpi_definitions", "created_at")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS operating_kpi_observations (
            id VARCHAR(64) PRIMARY KEY,
            kpi_key VARCHAR(120) NOT NULL,
            value FLOAT NOT NULL,
            status VARCHAR(30) NOT NULL,
            source_type VARCHAR(80),
            source_id VARCHAR(200),
            metadata JSON NOT NULL,
            observed_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    _index("operating_kpi_observations", "kpi_key")
    _index("operating_kpi_observations", "status")
    _index("operating_kpi_observations", "source_type")
    _index("operating_kpi_observations", "source_id")
    _index("operating_kpi_observations", "observed_at")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS executive_benchmark_definitions (
            id VARCHAR(64) PRIMARY KEY,
            key VARCHAR(120) NOT NULL,
            title VARCHAR(240) NOT NULL,
            description TEXT NOT NULL,
            kpi_keys JSON NOT NULL,
            rule JSON NOT NULL,
            severity VARCHAR(20) NOT NULL,
            status VARCHAR(30) NOT NULL,
            created_by VARCHAR(200) NOT NULL,
            metadata JSON NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_executive_benchmark_definitions_key "
        "ON executive_benchmark_definitions (key)"
    )
    _index("executive_benchmark_definitions", "key")
    _index("executive_benchmark_definitions", "severity")
    _index("executive_benchmark_definitions", "status")
    _index("executive_benchmark_definitions", "created_at")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS executive_benchmark_results (
            id VARCHAR(64) PRIMARY KEY,
            benchmark_key VARCHAR(120) NOT NULL,
            run_id VARCHAR(64) REFERENCES orchestration_governor_runs(id),
            status VARCHAR(30) NOT NULL,
            score FLOAT NOT NULL,
            observed_value FLOAT NOT NULL,
            threshold_value FLOAT NOT NULL,
            evidence JSON NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    _index("executive_benchmark_results", "benchmark_key")
    _index("executive_benchmark_results", "run_id")
    _index("executive_benchmark_results", "status")
    _index("executive_benchmark_results", "created_at")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS operation_graph_nodes (
            id VARCHAR(64) PRIMARY KEY,
            node_type VARCHAR(80) NOT NULL,
            title VARCHAR(240) NOT NULL,
            summary TEXT NOT NULL,
            source_type VARCHAR(80),
            source_id VARCHAR(200),
            agent_id VARCHAR(64),
            workflow_run_id VARCHAR(64),
            tool_name VARCHAR(100),
            risk_level VARCHAR(20) NOT NULL,
            confidence FLOAT NOT NULL,
            impact_score FLOAT NOT NULL,
            memory_namespace VARCHAR(200),
            tags JSON NOT NULL,
            metadata JSON NOT NULL,
            idempotency_key VARCHAR(240) NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_operation_graph_nodes_idempotency_key "
        "ON operation_graph_nodes (idempotency_key)"
    )
    for column in (
        "node_type",
        "source_type",
        "source_id",
        "agent_id",
        "workflow_run_id",
        "tool_name",
        "risk_level",
        "memory_namespace",
        "created_at",
    ):
        _index("operation_graph_nodes", column)

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS operation_graph_edges (
            id VARCHAR(64) PRIMARY KEY,
            source_node_id VARCHAR(64) NOT NULL REFERENCES operation_graph_nodes(id),
            target_node_id VARCHAR(64) NOT NULL REFERENCES operation_graph_nodes(id),
            edge_type VARCHAR(80) NOT NULL,
            metadata JSON NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    _index("operation_graph_edges", "source_node_id")
    _index("operation_graph_edges", "target_node_id")
    _index("operation_graph_edges", "edge_type")
    _index("operation_graph_edges", "created_at")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS executive_reflections (
            id VARCHAR(64) PRIMARY KEY,
            run_id VARCHAR(64) REFERENCES orchestration_governor_runs(id),
            summary TEXT NOT NULL,
            what_changed JSON NOT NULL,
            repeated_patterns JSON NOT NULL,
            failures JSON NOT NULL,
            memory_gaps JSON NOT NULL,
            next_watch_items JSON NOT NULL,
            metadata JSON NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    _index("executive_reflections", "run_id")
    _index("executive_reflections", "created_at")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS observer_reviews (
            id VARCHAR(64) PRIMARY KEY,
            run_id VARCHAR(64) REFERENCES orchestration_governor_runs(id),
            status VARCHAR(30) NOT NULL,
            critique TEXT NOT NULL,
            findings JSON NOT NULL,
            consensus_log JSON NOT NULL,
            unresolved_objections JSON NOT NULL,
            confidence FLOAT NOT NULL,
            metadata JSON NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    _index("observer_reviews", "run_id")
    _index("observer_reviews", "status")
    _index("observer_reviews", "created_at")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS autonomous_execution_records (
            id VARCHAR(64) PRIMARY KEY,
            run_id VARCHAR(64) REFERENCES orchestration_governor_runs(id),
            action_type VARCHAR(80) NOT NULL,
            title VARCHAR(240) NOT NULL,
            status VARCHAR(30) NOT NULL,
            risk_level VARCHAR(20) NOT NULL,
            confidence FLOAT NOT NULL,
            impact JSON NOT NULL,
            approval_id VARCHAR(64) REFERENCES approval_requests(id),
            operation_node_id VARCHAR(64) REFERENCES operation_graph_nodes(id),
            result JSON NOT NULL,
            error TEXT,
            idempotency_key VARCHAR(240) NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            completed_at TIMESTAMP WITHOUT TIME ZONE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_autonomous_execution_records_key "
        "ON autonomous_execution_records (idempotency_key)"
    )
    for column in (
        "run_id",
        "action_type",
        "status",
        "risk_level",
        "approval_id",
        "operation_node_id",
        "created_at",
    ):
        _index("autonomous_execution_records", column)

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS outsourcing_requests (
            id VARCHAR(64) PRIMARY KEY,
            title VARCHAR(240) NOT NULL,
            status VARCHAR(30) NOT NULL,
            complexity_reason TEXT NOT NULL,
            task_spec JSON NOT NULL,
            context_pack JSON NOT NULL,
            acceptance_tests JSON NOT NULL,
            foss_constraints JSON NOT NULL,
            security_constraints JSON NOT NULL,
            files_involved JSON NOT NULL,
            expected_artifact TEXT NOT NULL,
            replay_instructions TEXT NOT NULL,
            source_type VARCHAR(80),
            source_id VARCHAR(200),
            approval_id VARCHAR(64) REFERENCES approval_requests(id),
            created_by VARCHAR(200) NOT NULL,
            resolution JSON NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            resolved_at TIMESTAMP WITHOUT TIME ZONE
        )
        """
    )
    for column in (
        "status",
        "source_type",
        "source_id",
        "approval_id",
        "created_at",
    ):
        _index("outsourcing_requests", column)


def downgrade() -> None:
    for table in (
        "outsourcing_requests",
        "autonomous_execution_records",
        "observer_reviews",
        "executive_reflections",
        "operation_graph_edges",
        "operation_graph_nodes",
        "executive_benchmark_results",
        "executive_benchmark_definitions",
        "operating_kpi_observations",
        "operating_kpi_definitions",
        "company_objectives",
        "autonomy_policies",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
