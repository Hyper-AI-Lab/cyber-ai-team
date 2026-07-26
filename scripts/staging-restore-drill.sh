#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
ENV_FILE="${CYBERTEAM_ENV_FILE:-$ROOT_DIR/deploy/environments/staging.env}"
BACKUP_DIR="${RESTORE_DRILL_BACKUP_DIR:-$ROOT_DIR/backups/staging}"
BACKUP_FILE="${RESTORE_DRILL_BACKUP_FILE:-}"
EVIDENCE_DIR="${RESTORE_DRILL_EVIDENCE_DIR:-$ROOT_DIR/dist/restore-drills/staging}"
CONTAINER_NAME="${RESTORE_DRILL_CONTAINER:-cyberteam-staging-restore-drill}"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

POSTGRES_DB="${POSTGRES_DB:-cyberteam}"
POSTGRES_USER="${POSTGRES_USER:-cyberteam}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-changeme-postgres-password}"
RESTORE_POSTGRES_PORT="${RESTORE_DRILL_PORT:-55434}"
BACKEND_VENV="${BACKEND_VENV:-$ROOT_DIR/.venv-quality}"
if [[ "$BACKEND_VENV" != /* ]]; then
  BACKEND_VENV="$ROOT_DIR/$BACKEND_VENV"
fi
QDRANT_PUBLISHED_PORT="${QDRANT_HTTP_PUBLISHED_PORT:-6333}"
QDRANT_PUBLISHED_PORT="${QDRANT_PUBLISHED_PORT##*:}"
QDRANT_URL="${RESTORE_DRILL_QDRANT_URL:-http://127.0.0.1:${QDRANT_PUBLISHED_PORT}}"
QDRANT_COLLECTION="${RESTORE_DRILL_QDRANT_COLLECTION:-cyberteam_memory}"
QDRANT_SOURCE_CONTAINER="${CYBERTEAM_CONTAINER_PREFIX:-cyberteam}-qdrant"
QDRANT_RESTORE_CONTAINER="${RESTORE_DRILL_QDRANT_CONTAINER:-cyberteam-qdrant-restore-drill}"
QDRANT_RESTORE_PORT="${RESTORE_DRILL_QDRANT_PORT:-16433}"
QDRANT_API_KEY="${QDRANT_API_KEY:-}"

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

started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
started_epoch="$(date +%s)"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
evidence_file="$EVIDENCE_DIR/staging-restore-drill-$timestamp.json"
row_counts_file="$(mktemp /tmp/cyberteam-restore-counts.XXXXXX)"
alembic_output_file="$(mktemp /tmp/cyberteam-restore-alembic.XXXXXX)"
qdrant_snapshot_file="$(mktemp /tmp/cyberteam-qdrant-snapshot.XXXXXX.snapshot)"
qdrant_details_file="$(mktemp /tmp/cyberteam-qdrant-details.XXXXXX.json)"
qdrant_snapshot_name=""

qdrant_headers=()
if [ -n "$QDRANT_API_KEY" ]; then
  qdrant_headers=(-H "api-key: $QDRANT_API_KEY")
fi

cleanup() {
  if [ -n "$qdrant_snapshot_name" ]; then
    curl -fsS -X DELETE "${qdrant_headers[@]}" \
      "$QDRANT_URL/collections/$QDRANT_COLLECTION/snapshots/$qdrant_snapshot_name?wait=true" \
      >/dev/null 2>&1 || true
  fi
  docker rm -f "$CONTAINER_NAME" "$QDRANT_RESTORE_CONTAINER" >/dev/null 2>&1 || true
  rm -f \
    "$row_counts_file" \
    "$alembic_output_file" \
    "$qdrant_snapshot_file" \
    "$qdrant_details_file"
}
trap cleanup EXIT

docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
docker run -d \
  --name "$CONTAINER_NAME" \
  -e POSTGRES_DB="$POSTGRES_DB" \
  -e POSTGRES_USER="$POSTGRES_USER" \
  -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  -p "127.0.0.1:${RESTORE_POSTGRES_PORT}:5432" \
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
    POSTGRES_PORT="$RESTORE_POSTGRES_PORT" \
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
  autonomous_tasks \
  company_context_snapshots \
  company_context_sync_runs
do
  count="$(
    docker exec "$CONTAINER_NAME" \
      psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
        "SELECT count(*) FROM $table" 2>/dev/null || printf "missing"
  )"
  printf "%s=%s\n" "$table" "$(printf "%s" "$count" | tr -d "[:space:]")" \
    >>"$row_counts_file"
done

qdrant_source_info="$(
  curl -fsS "${qdrant_headers[@]}" \
    "$QDRANT_URL/collections/$QDRANT_COLLECTION"
)"
qdrant_source_points="$(printf "%s" "$qdrant_source_info" | jq -er '.result.points_count')"
qdrant_snapshot_response="$(
  curl -fsS -X POST "${qdrant_headers[@]}" \
    "$QDRANT_URL/collections/$QDRANT_COLLECTION/snapshots?wait=true"
)"
qdrant_snapshot_name="$(printf "%s" "$qdrant_snapshot_response" | jq -er '.result.name')"
qdrant_snapshot_checksum="$(
  printf "%s" "$qdrant_snapshot_response" | jq -r '.result.checksum // ""'
)"

curl -fsS "${qdrant_headers[@]}" \
  "$QDRANT_URL/collections/$QDRANT_COLLECTION/snapshots/$qdrant_snapshot_name" \
  --output "$qdrant_snapshot_file"

qdrant_image="${RESTORE_DRILL_QDRANT_IMAGE:-$(
  docker inspect "$QDRANT_SOURCE_CONTAINER" --format '{{.Config.Image}}'
)}"
qdrant_target_collection="restore_drill_${timestamp}"
docker rm -f "$QDRANT_RESTORE_CONTAINER" >/dev/null 2>&1 || true
docker run -d \
  --name "$QDRANT_RESTORE_CONTAINER" \
  -p "127.0.0.1:${QDRANT_RESTORE_PORT}:6333" \
  "$qdrant_image" >/dev/null

qdrant_ready=0
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${QDRANT_RESTORE_PORT}/readyz" >/dev/null 2>&1; then
    qdrant_ready=1
    break
  fi
  sleep 1
done
if [ "$qdrant_ready" != "1" ]; then
  echo "Timed out waiting for isolated Qdrant restore target" >&2
  docker logs "$QDRANT_RESTORE_CONTAINER" >&2 || true
  exit 1
fi

qdrant_restore_response="$(
  curl -fsS -X POST \
    "http://127.0.0.1:${QDRANT_RESTORE_PORT}/collections/$qdrant_target_collection/snapshots/upload?priority=snapshot&wait=true" \
    -F "snapshot=@$qdrant_snapshot_file"
)"
qdrant_restored_info="$(
  curl -fsS \
    "http://127.0.0.1:${QDRANT_RESTORE_PORT}/collections/$qdrant_target_collection"
)"
qdrant_restored_points="$(printf "%s" "$qdrant_restored_info" | jq -er '.result.points_count')"
if [ "$qdrant_source_points" != "$qdrant_restored_points" ]; then
  echo "Qdrant restore point-count mismatch: source=$qdrant_source_points restored=$qdrant_restored_points" >&2
  exit 1
fi

jq -n \
  --arg source_collection "$QDRANT_COLLECTION" \
  --arg target_collection "$qdrant_target_collection" \
  --arg snapshot_name "$qdrant_snapshot_name" \
  --arg snapshot_checksum "$qdrant_snapshot_checksum" \
  --arg image "$qdrant_image" \
  --argjson snapshot_size_bytes "$(wc -c <"$qdrant_snapshot_file" | tr -d "[:space:]")" \
  --argjson source_points_count "$qdrant_source_points" \
  --argjson restored_points_count "$qdrant_restored_points" \
  --arg restore_status "$(printf "%s" "$qdrant_restore_response" | jq -r '.status')" \
  '{
    source_collection: $source_collection,
    target_collection: $target_collection,
    snapshot_name: $snapshot_name,
    snapshot_checksum: ($snapshot_checksum | if length > 0 then . else null end),
    snapshot_size_bytes: $snapshot_size_bytes,
    source_points_count: $source_points_count,
    restored_points_count: $restored_points_count,
    restore_status: $restore_status,
    isolated_image: $image,
    validation: "source and restored point counts match",
    cleanup: "source snapshot and isolated restore container are removed by trap"
  }' >"$qdrant_details_file"

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
python3 - "$evidence_file" "$row_counts_file" "$alembic_output_file" "$qdrant_details_file" <<PY
import json
import os
import sys
from pathlib import Path

evidence_path = Path(sys.argv[1])
counts_path = Path(sys.argv[2])
alembic_output_path = Path(sys.argv[3])
qdrant_details_path = Path(sys.argv[4])

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
    "qdrant": json.loads(qdrant_details_path.read_text(encoding="utf-8")),
}
evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

echo "Staging restore drill passed."
echo "Backup: $BACKUP_FILE"
echo "Qdrant collection restored and verified: $QDRANT_COLLECTION"
echo "Evidence: $evidence_file"
