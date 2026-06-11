#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ERPNEXT_ENV_FILE:-$ROOT_DIR/deploy/environments/staging.env}"
BACKUP_DIR="${ERPNEXT_BACKUP_DIR:-$ROOT_DIR/backups/erpnext/staging}"
BACKUP_MANIFEST="${ERPNEXT_RESTORE_BACKUP_MANIFEST:-}"
EVIDENCE_DIR="${ERPNEXT_RESTORE_EVIDENCE_DIR:-$ROOT_DIR/dist/erpnext/restore-drills}"
SITE_NAME="${ERPNEXT_SITE_NAME:-erpnext.hyperailab.com}"
PUBLISHED_PORT="${ERPNEXT_PUBLISHED_PORT:-18100}"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERPNext env file not found: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

SITE_NAME="${ERPNEXT_SITE_NAME:-$SITE_NAME}"
PUBLISHED_PORT="${ERPNEXT_PUBLISHED_PORT:-$PUBLISHED_PORT}"
COMPOSE=(docker compose --env-file "$ENV_FILE" --profile erp)

if [ -z "${ERPNEXT_MARIADB_ROOT_PASSWORD:-}" ]; then
  echo "ERPNEXT_MARIADB_ROOT_PASSWORD is required for the restore drill." >&2
  exit 1
fi

if [ -z "$BACKUP_MANIFEST" ]; then
  if ! compgen -G "$BACKUP_DIR/*/backup-manifest.json" >/dev/null; then
    echo "No ERPNext backup manifests found in $BACKUP_DIR" >&2
    exit 1
  fi
  BACKUP_MANIFEST="$(ls -t "$BACKUP_DIR"/*/backup-manifest.json | head -n 1)"
fi

if [ ! -f "$BACKUP_MANIFEST" ]; then
  echo "ERPNext backup manifest not found: $BACKUP_MANIFEST" >&2
  exit 1
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
started_epoch="$(date +%s)"
TEMP_SITE="${ERPNEXT_RESTORE_DRILL_SITE:-restore-drill-$timestamp.local}"
container_restore_dir="/tmp/cyberteam-erpnext-restore-$timestamp"
evidence_file="$EVIDENCE_DIR/erpnext-restore-drill-$timestamp.json"
row_counts_file="$(mktemp /tmp/cyberteam-erpnext-restore-counts.XXXXXX)"
migrate_output_file="$(mktemp /tmp/cyberteam-erpnext-restore-migrate.XXXXXX)"
validation_output_file="$(mktemp /tmp/cyberteam-erpnext-restore-validation.XXXXXX)"

case "$TEMP_SITE" in
  restore-drill-*|cyberteam-restore-*) ;;
  *)
    echo "Temporary site must start with restore-drill- or cyberteam-restore-: $TEMP_SITE" >&2
    exit 1
    ;;
esac

read_manifest_field() {
  python3 - "$BACKUP_MANIFEST" "$1" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
value = manifest
for part in sys.argv[2].split("."):
    value = value.get(part) if isinstance(value, dict) else None
print("" if value is None else value)
PY
}

db_file="$(read_manifest_field files.database.path)"
public_file="$(read_manifest_field files.public_files.path)"
private_file="$(read_manifest_field files.private_files.path)"

if [ -z "$db_file" ] || [ ! -f "$db_file" ]; then
  echo "Database backup file from manifest is missing: $db_file" >&2
  exit 1
fi

cleanup() {
  "${COMPOSE[@]}" exec -T erpnext-backend bench drop-site "$TEMP_SITE" \
    --db-root-username root \
    --db-root-password "$ERPNEXT_MARIADB_ROOT_PASSWORD" \
    --no-backup \
    --force >/dev/null 2>&1 || true
  "${COMPOSE[@]}" exec -T erpnext-backend rm -rf "$container_restore_dir" >/dev/null 2>&1 || true
  rm -f "$row_counts_file" "$migrate_output_file" "$validation_output_file"
}
trap cleanup EXIT

"${COMPOSE[@]}" exec -T erpnext-backend rm -rf "$container_restore_dir" >/dev/null
"${COMPOSE[@]}" exec -T erpnext-backend mkdir -p "$container_restore_dir" >/dev/null
"${COMPOSE[@]}" cp "$db_file" "erpnext-backend:$container_restore_dir/database.sql.gz" >/dev/null
restore_args=("$container_restore_dir/database.sql.gz")

if [ -n "$public_file" ] && [ -f "$public_file" ]; then
  public_container_file="$container_restore_dir/$(basename "$public_file")"
  "${COMPOSE[@]}" cp "$public_file" "erpnext-backend:$public_container_file" >/dev/null
  restore_args+=(--with-public-files "$public_container_file")
fi
if [ -n "$private_file" ] && [ -f "$private_file" ]; then
  private_container_file="$container_restore_dir/$(basename "$private_file")"
  "${COMPOSE[@]}" cp "$private_file" "erpnext-backend:$private_container_file" >/dev/null
  restore_args+=(--with-private-files "$private_container_file")
fi

temp_admin_password="$(openssl rand -base64 24 | tr -d '\n')"
temp_db_password="$(openssl rand -base64 24 | tr -d '\n')"

"${COMPOSE[@]}" exec -T erpnext-backend bench new-site "$TEMP_SITE" \
  --mariadb-user-host-login-scope=% \
  --db-root-username=root \
  --db-root-password="$ERPNEXT_MARIADB_ROOT_PASSWORD" \
  --db-password="$temp_db_password" \
  --admin-password="$temp_admin_password" \
  --install-app erpnext >/dev/null

"${COMPOSE[@]}" exec -T erpnext-backend bench --site "$TEMP_SITE" restore \
  "${restore_args[@]}" \
  --db-root-username=root \
  --db-root-password="$ERPNEXT_MARIADB_ROOT_PASSWORD" \
  --admin-password="$temp_admin_password" \
  --force >/dev/null

"${COMPOSE[@]}" exec -T erpnext-backend bench --site "$TEMP_SITE" migrate \
  >"$migrate_output_file" 2>&1

curl -fsS \
  -H "Host: $TEMP_SITE" \
  "http://127.0.0.1:$PUBLISHED_PORT/api/method/ping" \
  >"$validation_output_file"

for doctype in \
  "Lead" \
  "Task" \
  "Issue" \
  "Material Request" \
  "Item" \
  "Company" \
  "User"
do
  count="$(
    "${COMPOSE[@]}" exec -T erpnext-backend bench --site "$TEMP_SITE" execute frappe.db.count \
      --args "[\"$doctype\"]" 2>/dev/null || printf "missing"
  )"
  printf "%s=%s\n" "$doctype" "$(printf "%s" "$count" | tr -d "[:space:]")" \
    >>"$row_counts_file"
done

expected_integration_user="${ERPNEXT_INTEGRATION_USER:-cyberteam.integration@example.local}"
integration_user_match="$(
  "${COMPOSE[@]}" exec -T erpnext-backend bench --site "$TEMP_SITE" execute frappe.db.exists \
    --args "[\"User\", \"$expected_integration_user\"]" \
    2>/dev/null | tr -d "[:space:]"
)"

mkdir -p "$EVIDENCE_DIR"
finished_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
finished_epoch="$(date +%s)"
duration_seconds="$((finished_epoch - started_epoch))"

ERPNEXT_RESTORE_STARTED_AT="$started_at" \
ERPNEXT_RESTORE_FINISHED_AT="$finished_at" \
ERPNEXT_RESTORE_DURATION_SECONDS="$duration_seconds" \
ERPNEXT_RESTORE_SOURCE_SITE="$SITE_NAME" \
ERPNEXT_RESTORE_TEMP_SITE="$TEMP_SITE" \
ERPNEXT_RESTORE_BACKUP_MANIFEST="$BACKUP_MANIFEST" \
ERPNEXT_RESTORE_DB_FILE="$db_file" \
ERPNEXT_RESTORE_PUBLIC_FILE="$public_file" \
ERPNEXT_RESTORE_PRIVATE_FILE="$private_file" \
ERPNEXT_RESTORE_INTEGRATION_USER="$expected_integration_user" \
ERPNEXT_RESTORE_INTEGRATION_USER_MATCH="$integration_user_match" \
python3 - "$evidence_file" "$row_counts_file" "$migrate_output_file" "$validation_output_file" <<'PY'
import json
import os
import sys
from pathlib import Path

evidence_path = Path(sys.argv[1])
counts_path = Path(sys.argv[2])
migrate_output_path = Path(sys.argv[3])
validation_output_path = Path(sys.argv[4])

row_counts = {}
for line in counts_path.read_text(encoding="utf-8").splitlines():
    key, value = line.split("=", 1)
    row_counts[key] = None if value == "missing" else int(value)

payload = {
    "environment": "staging",
    "status": "passed",
    "source_site": os.environ["ERPNEXT_RESTORE_SOURCE_SITE"],
    "temporary_site": os.environ["ERPNEXT_RESTORE_TEMP_SITE"],
    "started_at": os.environ["ERPNEXT_RESTORE_STARTED_AT"],
    "finished_at": os.environ["ERPNEXT_RESTORE_FINISHED_AT"],
    "duration_seconds": int(os.environ["ERPNEXT_RESTORE_DURATION_SECONDS"]),
    "backup_manifest": os.environ["ERPNEXT_RESTORE_BACKUP_MANIFEST"],
    "database_backup_file": os.environ["ERPNEXT_RESTORE_DB_FILE"],
    "public_files_backup_file": os.environ.get("ERPNEXT_RESTORE_PUBLIC_FILE") or None,
    "private_files_backup_file": os.environ.get("ERPNEXT_RESTORE_PRIVATE_FILE") or None,
    "api_validation": validation_output_path.read_text(encoding="utf-8").strip(),
    "migrate_output_tail": "\n".join(
        migrate_output_path.read_text(encoding="utf-8").strip().splitlines()[-40:]
    ),
    "integration_user": os.environ["ERPNEXT_RESTORE_INTEGRATION_USER"],
    "integration_user_exists": (
        os.environ["ERPNEXT_RESTORE_INTEGRATION_USER_MATCH"]
        == os.environ["ERPNEXT_RESTORE_INTEGRATION_USER"]
    ),
    "row_counts": row_counts,
    "cleanup": "temporary site is dropped by trap after evidence is written",
}
evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

echo "ERPNext restore drill passed."
echo "Backup manifest: $BACKUP_MANIFEST"
echo "Temporary site: $TEMP_SITE"
echo "Evidence: $evidence_file"
