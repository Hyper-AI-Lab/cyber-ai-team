#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ERPNEXT_ENV_FILE:-$ROOT_DIR/deploy/environments/staging.env}"
BACKUP_DIR="${ERPNEXT_BACKUP_DIR:-$ROOT_DIR/backups/erpnext/staging}"
EVIDENCE_DIR="${ERPNEXT_BACKUP_EVIDENCE_DIR:-$ROOT_DIR/dist/erpnext/backups}"
SITE_NAME="${ERPNEXT_SITE_NAME:-erpnext.hyperailab.com}"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERPNext env file not found: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

SITE_NAME="${ERPNEXT_SITE_NAME:-$SITE_NAME}"
COMPOSE=(docker compose --env-file "$ENV_FILE" --profile erp)

started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
started_epoch="$(date +%s)"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
container_backup_dir="/tmp/cyberteam-erpnext-backup-$timestamp"
artifact_dir="$BACKUP_DIR/$timestamp"
manifest_file="$artifact_dir/backup-manifest.json"
evidence_file="$EVIDENCE_DIR/erpnext-backup-$timestamp.json"

mkdir -p "$artifact_dir" "$EVIDENCE_DIR"

cleanup_container_tmp() {
  "${COMPOSE[@]}" exec -T erpnext-backend rm -rf "$container_backup_dir" >/dev/null 2>&1 || true
}
trap cleanup_container_tmp EXIT

"${COMPOSE[@]}" exec -T erpnext-backend bash -lc \
  "rm -rf '$container_backup_dir' && mkdir -p '$container_backup_dir' && bench --site '$SITE_NAME' backup --with-files --compress --backup-path '$container_backup_dir'"

"${COMPOSE[@]}" cp "erpnext-backend:$container_backup_dir/." "$artifact_dir/" >/dev/null
cleanup_container_tmp
trap - EXIT

db_file="$(find "$artifact_dir" -maxdepth 1 -type f -name '*.sql.gz' | sort | head -n 1)"
public_file="$(
  find "$artifact_dir" -maxdepth 1 -type f \( -name '*files.tar' -o -name '*files.tar.gz' -o -name '*files.tgz' \) \
    ! -name '*private*' | sort | head -n 1
)"
private_file="$(
  find "$artifact_dir" -maxdepth 1 -type f \( -name '*private-files.tar' -o -name '*private-files.tar.gz' -o -name '*private-files.tgz' \) \
    | sort | head -n 1
)"

if [ -z "$db_file" ] || [ ! -f "$db_file" ]; then
  echo "ERPNext database backup file was not copied to $artifact_dir" >&2
  exit 1
fi

finished_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
finished_epoch="$(date +%s)"
duration_seconds="$((finished_epoch - started_epoch))"

ERPNEXT_BACKUP_SITE_NAME="$SITE_NAME" \
ERPNEXT_BACKUP_STARTED_AT="$started_at" \
ERPNEXT_BACKUP_FINISHED_AT="$finished_at" \
ERPNEXT_BACKUP_DURATION_SECONDS="$duration_seconds" \
ERPNEXT_BACKUP_ARTIFACT_DIR="$artifact_dir" \
ERPNEXT_BACKUP_DB_FILE="$db_file" \
ERPNEXT_BACKUP_PUBLIC_FILE="$public_file" \
ERPNEXT_BACKUP_PRIVATE_FILE="$private_file" \
ERPNEXT_BACKUP_MANIFEST_FILE="$manifest_file" \
ERPNEXT_BACKUP_EVIDENCE_FILE="$evidence_file" \
python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path


def file_payload(path_value: str) -> dict | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.exists():
        return None
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "path": str(path),
        "filename": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": digest,
    }


files = {
    "database": file_payload(os.environ["ERPNEXT_BACKUP_DB_FILE"]),
    "public_files": file_payload(os.environ.get("ERPNEXT_BACKUP_PUBLIC_FILE", "")),
    "private_files": file_payload(os.environ.get("ERPNEXT_BACKUP_PRIVATE_FILE", "")),
}
payload = {
    "environment": "staging",
    "status": "passed",
    "site_name": os.environ["ERPNEXT_BACKUP_SITE_NAME"],
    "started_at": os.environ["ERPNEXT_BACKUP_STARTED_AT"],
    "finished_at": os.environ["ERPNEXT_BACKUP_FINISHED_AT"],
    "duration_seconds": int(os.environ["ERPNEXT_BACKUP_DURATION_SECONDS"]),
    "artifact_dir": os.environ["ERPNEXT_BACKUP_ARTIFACT_DIR"],
    "files": files,
}
manifest_path = Path(os.environ["ERPNEXT_BACKUP_MANIFEST_FILE"])
evidence_path = Path(os.environ["ERPNEXT_BACKUP_EVIDENCE_FILE"])
manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

echo "ERPNext backup passed."
echo "Artifact directory: $artifact_dir"
echo "Manifest: $manifest_file"
echo "Evidence: $evidence_file"
