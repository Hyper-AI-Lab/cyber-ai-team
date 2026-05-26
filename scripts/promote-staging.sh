#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${RELEASE_VERSION:-}"
MANIFEST="${RELEASE_MANIFEST:-}"
DRY_RUN="${PROMOTE_DRY_RUN:-1}"
STAGING_ENV_FILE="${STAGING_ENV_FILE:-$ROOT_DIR/.env.staging}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-cyberteam-staging}"
RUN_BACKUP="${RUN_BACKUP:-1}"
RUN_COMPOSE_SMOKE="${RUN_COMPOSE_SMOKE:-1}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups/staging}"

if [ -z "$MANIFEST" ]; then
  if [ -z "$VERSION" ]; then
    echo "Set RELEASE_VERSION or RELEASE_MANIFEST." >&2
    exit 2
  fi
  MANIFEST="$ROOT_DIR/dist/releases/$VERSION.json"
fi

if [ ! -f "$MANIFEST" ]; then
  echo "Release manifest not found: $MANIFEST" >&2
  exit 1
fi

read_manifest() {
  python3 - "$MANIFEST" "$1" <<'PY'
import json
import sys

manifest_path, dotted_key = sys.argv[1], sys.argv[2]
with open(manifest_path, encoding="utf-8") as handle:
    value = json.load(handle)
for part in dotted_key.split("."):
    value = value[part]
print(value)
PY
}

VERSION="$(read_manifest version)"
CORE_IMAGE="$(read_manifest images.core)"
UI_IMAGE="$(read_manifest images.ui)"

run_step() {
  echo "+ $*"
  if [ "$DRY_RUN" != "1" ]; then
    "$@"
  fi
}

if [ "$DRY_RUN" = "1" ]; then
  echo "Dry run only. Set PROMOTE_DRY_RUN=0 to execute."
fi

echo "Promoting release $VERSION to staging"
echo "Core image: $CORE_IMAGE"
echo "UI image: $UI_IMAGE"

if [ ! -f "$STAGING_ENV_FILE" ]; then
  echo "Staging env file not found: $STAGING_ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "$STAGING_ENV_FILE"
set +a

if [ "$RUN_BACKUP" = "1" ]; then
  timestamp="$(date -u +%Y%m%d-%H%M%S)"
  backup_file="$BACKUP_DIR/cyberteam-staging-$VERSION-$timestamp.dump"
  run_step mkdir -p "$BACKUP_DIR"
  run_step env COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT_NAME" \
    CYBERTEAM_ENV_FILE="$STAGING_ENV_FILE" \
    docker compose --env-file "$STAGING_ENV_FILE" exec -T postgres \
    pg_dump -U "${POSTGRES_USER:-cyberteam}" \
    -d "${POSTGRES_DB:-cyberteam}" \
    --format=custom \
    --file="/tmp/$(basename "$backup_file")"
  run_step env COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT_NAME" \
    CYBERTEAM_ENV_FILE="$STAGING_ENV_FILE" \
    docker compose --env-file "$STAGING_ENV_FILE" cp \
    "postgres:/tmp/$(basename "$backup_file")" \
    "$backup_file"
fi

run_step docker image inspect "$CORE_IMAGE"
run_step docker image inspect "$UI_IMAGE"
run_step env COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT_NAME" \
  CORE_IMAGE="$CORE_IMAGE" UI_IMAGE="$UI_IMAGE" \
  CYBERTEAM_ENV_FILE="$STAGING_ENV_FILE" \
  docker compose --env-file "$STAGING_ENV_FILE" up -d --no-build \
  postgres redis qdrant temporal opa core worker ui

if [ "$RUN_COMPOSE_SMOKE" = "1" ]; then
  run_step env COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT_NAME" \
    COMPOSE_SMOKE_SKIP_UP=1 \
    "$ROOT_DIR/scripts/compose-smoke.sh"
fi

echo "Staging promotion plan completed for release $VERSION."
