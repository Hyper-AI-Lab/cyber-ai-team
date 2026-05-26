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
BACKEND_VENV="${BACKEND_VENV:-$ROOT_DIR/.venv-quality}"

if [ -x "$BACKEND_VENV/bin/alembic" ]; then
  ALEMBIC_BIN="$BACKEND_VENV/bin/alembic"
else
  ALEMBIC_BIN="${ALEMBIC_BIN:-alembic}"
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

for _ in $(seq 1 60); do
  if docker exec "$CONTAINER_NAME" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec "$CONTAINER_NAME" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null

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
if [ "$current_revision" != "0002_communication_idempotency" ]; then
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

workflow_tables="$(docker exec "$CONTAINER_NAME" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name IN ('workflows', 'workflow_runs', 'memory_entries', 'audit_events', 'communication_logs', 'role_manifests')")"
if [ "$workflow_tables" != "6" ]; then
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

echo "Migration rehearsal passed against legacy pre-Alembic schema."
