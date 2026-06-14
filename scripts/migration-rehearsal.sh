#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
CONTAINER_NAME="${MIGRATION_REHEARSAL_CONTAINER:-cyberteam-migration-rehearsal}"
POSTGRES_PORT="${MIGRATION_REHEARSAL_PORT:-55433}"
POSTGRES_DB="${POSTGRES_DB:-cyberteam}"
POSTGRES_USER="${POSTGRES_USER:-cyberteam}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-changeme-postgres-password}"
MIGRATION_REHEARSAL_CLEANUP="${MIGRATION_REHEARSAL_CLEANUP:-1}"
MIGRATION_REHEARSAL_RUN_REPRESENTATIVE="${MIGRATION_REHEARSAL_RUN_REPRESENTATIVE:-1}"
MIGRATION_REHEARSAL_SYNTHETIC_ROWS="${MIGRATION_REHEARSAL_SYNTHETIC_ROWS:-25}"
BACKEND_VENV="${BACKEND_VENV:-$ROOT_DIR/.venv-quality}"
ALEMBIC_HEAD="${ALEMBIC_HEAD:-}"

if [ -x "$BACKEND_VENV/bin/alembic" ]; then
  ALEMBIC_BIN="$BACKEND_VENV/bin/alembic"
else
  ALEMBIC_BIN="${ALEMBIC_BIN:-alembic}"
fi

if [ -z "$ALEMBIC_HEAD" ]; then
  ALEMBIC_HEAD="$(
    cd "$BACKEND_DIR"
    env PYTHONPATH=src "$ALEMBIC_BIN" heads | awk '/\(head\)/ { print $1 }'
  )"
fi
if [ "$(printf "%s\n" "$ALEMBIC_HEAD" | sed '/^$/d' | wc -l | tr -d " ")" != "1" ]; then
  echo "Expected exactly one Alembic head, got: $ALEMBIC_HEAD" >&2
  exit 1
fi

cleanup() {
  if [ "$MIGRATION_REHEARSAL_CLEANUP" = "1" ]; then
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
docker run -d \
  --name "$CONTAINER_NAME" \
  -e POSTGRES_DB="$POSTGRES_DB" \
  -e POSTGRES_USER="$POSTGRES_USER" \
  -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  -p "127.0.0.1:${POSTGRES_PORT}:5432" \
  postgres:16-alpine >/dev/null

database_ready=0
for _ in $(seq 1 60); do
  if docker exec "$CONTAINER_NAME" \
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT 1" \
    >/dev/null 2>&1; then
    database_ready=1
    break
  fi
  sleep 1
done
if [ "$database_ready" != "1" ]; then
  echo "Timed out waiting for PostgreSQL database '$POSTGRES_DB' in $CONTAINER_NAME" >&2
  docker logs "$CONTAINER_NAME" >&2 || true
  exit 1
fi

docker exec -i "$CONTAINER_NAME" \
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  < "$ROOT_DIR/scripts/sql/pre-alembic-approval-schema.sql"

(
  cd "$BACKEND_DIR"
  env \
    PYTHONPATH=src \
    POSTGRES_HOST=127.0.0.1 \
    POSTGRES_PORT="$POSTGRES_PORT" \
    POSTGRES_DB="$POSTGRES_DB" \
    POSTGRES_USER="$POSTGRES_USER" \
    POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
    "$ALEMBIC_BIN" upgrade head
)

current_revision="$(docker exec "$CONTAINER_NAME" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT version_num FROM alembic_version")"
if [ "$current_revision" != "$ALEMBIC_HEAD" ]; then
  echo "Unexpected Alembic revision: $current_revision" >&2
  exit 1
fi

agent_nullable="$(docker exec "$CONTAINER_NAME" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT is_nullable FROM information_schema.columns WHERE table_name = 'approval_requests' AND column_name = 'agent_id'")"
if [ "$agent_nullable" != "YES" ]; then
  echo "approval_requests.agent_id should be nullable after migration" >&2
  exit 1
fi

foreign_keys="$(docker exec "$CONTAINER_NAME" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM information_schema.table_constraints WHERE table_name = 'approval_requests' AND constraint_type = 'FOREIGN KEY'")"
if [ "$foreign_keys" != "0" ]; then
  echo "approval_requests should not retain legacy foreign keys" >&2
  exit 1
fi

legacy_rows="$(docker exec "$CONTAINER_NAME" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM approval_requests WHERE id = 'legacy-approval-1' AND requester = 'system' AND requester_type = 'system' AND risk_level = 'medium'")"
if [ "$legacy_rows" != "1" ]; then
  echo "Legacy approval row was not preserved with expected defaults" >&2
  exit 1
fi

workflow_tables="$(docker exec "$CONTAINER_NAME" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name IN ('workflows', 'workflow_runs', 'memory_entries', 'audit_events', 'communication_logs', 'role_manifests', 'role_gaps', 'memory_traces', 'memory_steward_findings', 'company_context_snapshots', 'company_context_sync_runs')")"
if [ "$workflow_tables" != "11" ]; then
  echo "Expected new Cyber-Team tables were not created" >&2
  exit 1
fi

idempotency_column="$(docker exec "$CONTAINER_NAME" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT is_nullable FROM information_schema.columns WHERE table_name = 'communication_logs' AND column_name = 'idempotency_key'")"
if [ "$idempotency_column" != "YES" ]; then
  echo "communication_logs.idempotency_key should exist and be nullable" >&2
  exit 1
fi

idempotency_index="$(docker exec "$CONTAINER_NAME" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM pg_indexes WHERE tablename = 'communication_logs' AND indexname = 'ix_communication_logs_idempotency_key'")"
if [ "$idempotency_index" != "1" ]; then
  echo "communication_logs idempotency index is missing" >&2
  exit 1
fi

retention_index="$(docker exec "$CONTAINER_NAME" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM pg_indexes WHERE indexname IN ('ix_communication_logs_created_at', 'ix_memory_entries_expires_at', 'ix_workflow_runs_completed_at')")"
if [ "$retention_index" != "3" ]; then
  echo "Expected retention indexes are missing" >&2
  exit 1
fi

adaptive_operations_indexes="$(docker exec "$CONTAINER_NAME" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM pg_indexes WHERE indexname IN ('ix_role_gaps_status', 'ix_role_gaps_severity', 'ix_role_gaps_source_agent_id', 'ix_role_gaps_company_namespace', 'ix_role_gaps_capability', 'ix_role_gaps_created_at', 'ix_role_gaps_resolved_at', 'ix_memory_traces_invocation_id', 'ix_memory_traces_agent_id', 'ix_memory_traces_conversation_id', 'ix_memory_traces_source_type', 'ix_memory_traces_memory_namespace', 'ix_memory_traces_created_at', 'ix_memory_steward_findings_finding_type', 'ix_memory_steward_findings_severity', 'ix_memory_steward_findings_status', 'ix_memory_steward_findings_agent_id', 'ix_memory_steward_findings_memory_namespace', 'ix_memory_steward_findings_company_namespace', 'ix_memory_steward_findings_created_at', 'uq_company_context_snapshots_source_hash', 'ix_company_context_snapshots_source', 'ix_company_context_snapshots_source_id', 'ix_company_context_snapshots_source_hash', 'ix_company_context_snapshots_company_namespace', 'ix_company_context_snapshots_status', 'ix_company_context_snapshots_created_at', 'ix_company_context_sync_runs_source', 'ix_company_context_sync_runs_status', 'ix_company_context_sync_runs_snapshot_id', 'ix_company_context_sync_runs_source_hash', 'ix_company_context_sync_runs_company_namespace', 'ix_company_context_sync_runs_started_at')")"
if [ "$adaptive_operations_indexes" != "33" ]; then
  echo "Expected adaptive operations indexes are missing: $adaptive_operations_indexes" >&2
  exit 1
fi

echo "Migration rehearsal passed against legacy pre-Alembic schema."

if [ "$MIGRATION_REHEARSAL_RUN_REPRESENTATIVE" = "1" ]; then
  docker exec "$CONTAINER_NAME" psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

  (
    cd "$BACKEND_DIR"
    env \
      PYTHONPATH=src \
      POSTGRES_HOST=127.0.0.1 \
      POSTGRES_PORT="$POSTGRES_PORT" \
      POSTGRES_DB="$POSTGRES_DB" \
      POSTGRES_USER="$POSTGRES_USER" \
      POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
      "$ALEMBIC_BIN" upgrade 0001_initial_schema
  )

  docker exec -i "$CONTAINER_NAME" \
    psql \
      -v ON_ERROR_STOP=1 \
      -v row_count="$MIGRATION_REHEARSAL_SYNTHETIC_ROWS" \
      -U "$POSTGRES_USER" \
      -d "$POSTGRES_DB" \
    < "$ROOT_DIR/scripts/sql/representative-production-seed.sql"

  seeded_comm_rows="$(docker exec "$CONTAINER_NAME" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM communication_logs")"
  if [ "$seeded_comm_rows" != "$MIGRATION_REHEARSAL_SYNTHETIC_ROWS" ]; then
    echo "Representative communication seed count mismatch: $seeded_comm_rows" >&2
    exit 1
  fi

  (
    cd "$BACKEND_DIR"
    env \
      PYTHONPATH=src \
      POSTGRES_HOST=127.0.0.1 \
      POSTGRES_PORT="$POSTGRES_PORT" \
      POSTGRES_DB="$POSTGRES_DB" \
      POSTGRES_USER="$POSTGRES_USER" \
      POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
      "$ALEMBIC_BIN" upgrade head
  )

  representative_revision="$(docker exec "$CONTAINER_NAME" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT version_num FROM alembic_version")"
  if [ "$representative_revision" != "$ALEMBIC_HEAD" ]; then
    echo "Unexpected representative Alembic revision: $representative_revision" >&2
    exit 1
  fi

  representative_counts="$(docker exec "$CONTAINER_NAME" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM agents WHERE id LIKE 'agent-%' UNION ALL SELECT count(*) FROM workflow_runs WHERE id LIKE 'run-%' UNION ALL SELECT count(*) FROM memory_entries WHERE id LIKE 'memory-%' UNION ALL SELECT count(*) FROM communication_logs WHERE id LIKE 'comm-%'")"
  while read -r count; do
    if [ "$count" != "$MIGRATION_REHEARSAL_SYNTHETIC_ROWS" ]; then
      echo "Representative seed count mismatch after migration: $count" >&2
      exit 1
    fi
  done <<<"$representative_counts"

  representative_indexes="$(docker exec "$CONTAINER_NAME" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM pg_indexes WHERE indexname IN ('ix_communication_logs_idempotency_key', 'ix_communication_logs_created_at', 'ix_memory_entries_expires_at', 'ix_workflow_runs_completed_at', 'ix_approval_requests_resolved_at', 'ix_role_gaps_status', 'ix_role_gaps_severity', 'ix_role_gaps_source_agent_id', 'ix_role_gaps_company_namespace', 'ix_role_gaps_capability', 'ix_role_gaps_created_at', 'ix_role_gaps_resolved_at', 'ix_memory_traces_invocation_id', 'ix_memory_traces_agent_id', 'ix_memory_traces_conversation_id', 'ix_memory_traces_source_type', 'ix_memory_traces_memory_namespace', 'ix_memory_traces_created_at', 'ix_memory_steward_findings_finding_type', 'ix_memory_steward_findings_severity', 'ix_memory_steward_findings_status', 'ix_memory_steward_findings_agent_id', 'ix_memory_steward_findings_memory_namespace', 'ix_memory_steward_findings_company_namespace', 'ix_memory_steward_findings_created_at', 'uq_company_context_snapshots_source_hash', 'ix_company_context_snapshots_source', 'ix_company_context_snapshots_source_id', 'ix_company_context_snapshots_source_hash', 'ix_company_context_snapshots_company_namespace', 'ix_company_context_snapshots_status', 'ix_company_context_snapshots_created_at', 'ix_company_context_sync_runs_source', 'ix_company_context_sync_runs_status', 'ix_company_context_sync_runs_snapshot_id', 'ix_company_context_sync_runs_source_hash', 'ix_company_context_sync_runs_company_namespace', 'ix_company_context_sync_runs_started_at')")"
  if [ "$representative_indexes" != "38" ]; then
    echo "Representative migration indexes missing: $representative_indexes" >&2
    exit 1
  fi

  echo "Migration rehearsal passed against representative seeded 0001 schema."
fi
