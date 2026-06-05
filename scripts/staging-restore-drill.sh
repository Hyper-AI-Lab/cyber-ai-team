#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
BACKUP_DIR="${RESTORE_DRILL_BACKUP_DIR:-$ROOT_DIR/backups/staging}"
BACKUP_FILE="${RESTORE_DRILL_BACKUP_FILE:-}"
EVIDENCE_DIR="${RESTORE_DRILL_EVIDENCE_DIR:-$ROOT_DIR/dist/restore-drills/staging}"
CONTAINER_NAME="${RESTORE_DRILL_CONTAINER:-cyberteam-staging-restore-drill}"
POSTGRES_PORT="${RESTORE_DRILL_PORT:-55434}"
POSTGRES_DB="${POSTGRES_DB:-cyberteam}"
POSTGRES_USER="${POSTGRES_USER:-cyberteam}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-changeme-postgres-password}"
BACKEND_VENV="${BACKEND_VENV:-$ROOT_DIR/.venv-quality}"

if [ -x "$BACKEND_VENV/bin/alembic" ]; then
  ALEMBIC_BIN="$BACKEND_VENV/bin/alembic"
else
  ALEMBIC_BIN="${ALEMBIC_BIN:-alembic}"
fi

if [ -z "$BACKUP_FILE" ]; then
  if ! compgen -G "$BACKUP_DIR/*.dump" >/dev/null; then
    echo "No staging backup dumps found in $BACKUP_DIR" >&2
    exit 1
  fi
  BACKUP_FILE="$(ls -t "$BACKUP_DIR"/*.dump | head -n 1)"
fi

if [ ! -f "$BACKUP_FILE" ]; then
  echo "Backup file not found: $BACKUP_FILE" >&2
  exit 1
fi

cleanup() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
started_epoch="$(date +%s)"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
evidence_file="$EVIDENCE_DIR/staging-restore-drill-$timestamp.json"
row_counts_file="$(mktemp /tmp/cyberteam-restore-counts.XXXXXX)"
alembic_output_file="$(mktemp /tmp/cyberteam-restore-alembic.XXXXXX)"
trap 'rm -f "$row_counts_file" "$alembic_output_file"; cleanup' EXIT

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
  echo "Timed out waiting for PostgreSQL restore drill database" >&2
  docker logs "$CONTAINER_NAME" >&2 || true
  exit 1
fi

docker cp "$BACKUP_FILE" "$CONTAINER_NAME:/tmp/restore.dump" >/dev/null
docker exec "$CONTAINER_NAME" \
  pg_restore \
    --clean \
    --if-exists \
    --no-owner \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    /tmp/restore.dump

(
  cd "$BACKEND_DIR"
  env \
    PYTHONPATH=src \
    POSTGRES_HOST=127.0.0.1 \
    POSTGRES_PORT="$POSTGRES_PORT" \
    POSTGRES_DB="$POSTGRES_DB" \
    POSTGRES_USER="$POSTGRES_USER" \
    POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
    "$ALEMBIC_BIN" current
) >"$alembic_output_file" 2>&1

alembic_revision="$(docker exec "$CONTAINER_NAME" \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
    "SELECT version_num FROM alembic_version LIMIT 1" | tr -d "[:space:]")"

for table in \
  agents \
  workflows \
  workflow_runs \
  approval_requests \
  audit_events \
  communication_logs \
  memory_entries \
  role_gaps \
  memory_traces \
  memory_steward_findings \
  autonomous_plans \
  autonomous_tasks
do
  count="$(
    docker exec "$CONTAINER_NAME" \
      psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
        "SELECT count(*) FROM $table" 2>/dev/null || printf "missing"
  )"
  printf "%s=%s\n" "$table" "$(printf "%s" "$count" | tr -d "[:space:]")" \
    >>"$row_counts_file"
done

mkdir -p "$EVIDENCE_DIR"
finished_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
finished_epoch="$(date +%s)"
duration_seconds="$((finished_epoch - started_epoch))"
backup_size_bytes="$(wc -c <"$BACKUP_FILE" | tr -d "[:space:]")"

RESTORE_DRILL_STARTED_AT="$started_at" \
RESTORE_DRILL_FINISHED_AT="$finished_at" \
RESTORE_DRILL_DURATION_SECONDS="$duration_seconds" \
RESTORE_DRILL_BACKUP_FILE="$BACKUP_FILE" \
RESTORE_DRILL_BACKUP_SIZE_BYTES="$backup_size_bytes" \
RESTORE_DRILL_ALEMBIC_REVISION="$alembic_revision" \
python3 - "$evidence_file" "$row_counts_file" "$alembic_output_file" <<PY
import json
import os
import sys
from pathlib import Path

evidence_path = Path(sys.argv[1])
counts_path = Path(sys.argv[2])
alembic_output_path = Path(sys.argv[3])

row_counts = {}
for line in counts_path.read_text(encoding="utf-8").splitlines():
    table, value = line.split("=", 1)
    row_counts[table] = None if value == "missing" else int(value)

payload = {
    "environment": "staging",
    "status": "passed",
    "started_at": os.environ["RESTORE_DRILL_STARTED_AT"],
    "finished_at": os.environ["RESTORE_DRILL_FINISHED_AT"],
    "duration_seconds": int(os.environ["RESTORE_DRILL_DURATION_SECONDS"]),
    "backup_file": os.environ["RESTORE_DRILL_BACKUP_FILE"],
    "backup_size_bytes": int(os.environ["RESTORE_DRILL_BACKUP_SIZE_BYTES"]),
    "postgres_image": "postgres:16-alpine",
    "alembic_revision": os.environ["RESTORE_DRILL_ALEMBIC_REVISION"],
    "alembic_current_output": alembic_output_path.read_text(encoding="utf-8").strip(),
    "row_counts": row_counts,
}
evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

echo "Staging restore drill passed."
echo "Backup: $BACKUP_FILE"
echo "Evidence: $evidence_file"
