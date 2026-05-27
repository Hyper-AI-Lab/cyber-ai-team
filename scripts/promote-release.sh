#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROMOTE_ENVIRONMENT="${PROMOTE_ENVIRONMENT:-staging}"
DEPLOYMENT_MANIFEST="${DEPLOYMENT_MANIFEST:-$ROOT_DIR/deploy/manifests/$PROMOTE_ENVIRONMENT.json}"
VERSION="${RELEASE_VERSION:-}"
RELEASE_MANIFEST="${RELEASE_MANIFEST:-}"
DRY_RUN="${PROMOTE_DRY_RUN:-1}"
ALLOW_INCOMPLETE_CHECKS="${PROMOTE_ALLOW_INCOMPLETE_CHECKS:-0}"
APPROVAL_FILE="${PROMOTION_APPROVAL_FILE:-}"

if [ ! -f "$DEPLOYMENT_MANIFEST" ]; then
  echo "Deployment manifest not found: $DEPLOYMENT_MANIFEST" >&2
  exit 1
fi

eval "$("$ROOT_DIR/scripts/promotion_policy.py" emit-config \
  --root "$ROOT_DIR" \
  --environment "$PROMOTE_ENVIRONMENT" \
  --deployment-manifest "$DEPLOYMENT_MANIFEST")"

if [ -z "$RELEASE_MANIFEST" ]; then
  if [ -z "$VERSION" ]; then
    echo "Set RELEASE_VERSION or RELEASE_MANIFEST." >&2
    exit 2
  fi
  RELEASE_MANIFEST="$ROOT_DIR/dist/releases/$VERSION.json"
fi

if [ ! -f "$RELEASE_MANIFEST" ]; then
  echo "Release manifest not found: $RELEASE_MANIFEST" >&2
  exit 1
fi

if [ ! -f "$PROMOTE_ENV_FILE" ]; then
  echo "Environment file not found: $PROMOTE_ENV_FILE" >&2
  exit 1
fi

read_manifest() {
  python3 - "$RELEASE_MANIFEST" "$1" <<'PY'
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
BACKUP_FILE=""

validation_args=(
  validate
  --root "$ROOT_DIR"
  --environment "$PROMOTE_ENVIRONMENT"
  --deployment-manifest "$DEPLOYMENT_MANIFEST"
  --release-manifest "$RELEASE_MANIFEST"
)
if [ "$DRY_RUN" = "1" ]; then
  validation_args+=(--dry-run)
fi
if [ "$ALLOW_INCOMPLETE_CHECKS" = "1" ]; then
  validation_args+=(--allow-incomplete-checks)
fi
if [ -n "$APPROVAL_FILE" ]; then
  validation_args+=(--approval-file "$APPROVAL_FILE")
fi
"$ROOT_DIR/scripts/promotion_policy.py" "${validation_args[@]}"

run_step() {
  echo "+ $*"
  if [ "$DRY_RUN" != "1" ]; then
    "$@"
  fi
}

if [ "$DRY_RUN" = "1" ]; then
  echo "Dry run only. Set PROMOTE_DRY_RUN=0 to execute."
fi

echo "Promoting release $VERSION to $PROMOTE_ENVIRONMENT"
echo "Deployment manifest: $DEPLOYMENT_MANIFEST"
echo "Environment file: $PROMOTE_ENV_FILE"
echo "Compose project: $COMPOSE_PROJECT_NAME"
echo "Core image: $CORE_IMAGE"
echo "UI image: $UI_IMAGE"

configured_compose_project_name="$COMPOSE_PROJECT_NAME"
configured_backup_dir="$BACKUP_DIR"
configured_promotion_record_dir="$PROMOTION_RECORD_DIR"
configured_require_approval="$PROMOTION_REQUIRE_APPROVAL"
configured_run_backup="$RUN_BACKUP"
configured_run_compose_smoke="$RUN_COMPOSE_SMOKE"
configured_promote_services="$PROMOTE_SERVICES"

set -a
# shellcheck disable=SC1090
. "$PROMOTE_ENV_FILE"
set +a

COMPOSE_PROJECT_NAME="$configured_compose_project_name"
BACKUP_DIR="$configured_backup_dir"
PROMOTION_RECORD_DIR="$configured_promotion_record_dir"
PROMOTION_REQUIRE_APPROVAL="$configured_require_approval"
RUN_BACKUP="$configured_run_backup"
RUN_COMPOSE_SMOKE="$configured_run_compose_smoke"
PROMOTE_SERVICES="$configured_promote_services"

if [ "$PROMOTION_REQUIRE_APPROVAL" = "1" ]; then
  if [ "$DRY_RUN" = "1" ] && [ -z "$APPROVAL_FILE" ]; then
    echo "Execution will require PROMOTION_APPROVAL_FILE or PROMOTION_APPROVER/PROMOTION_CHANGE_TICKET."
  else
    echo "Promotion approval validated."
  fi
fi

if [ "$RUN_BACKUP" = "1" ]; then
  timestamp="$(date -u +%Y%m%d-%H%M%S)"
  BACKUP_FILE="$BACKUP_DIR/cyberteam-$PROMOTE_ENVIRONMENT-$VERSION-$timestamp.dump"
  run_step mkdir -p "$BACKUP_DIR"
  run_step env COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT_NAME" \
    CYBERTEAM_ENV_FILE="$PROMOTE_ENV_FILE" \
    docker compose --env-file "$PROMOTE_ENV_FILE" exec -T postgres \
    pg_dump -U "${POSTGRES_USER:-cyberteam}" \
    -d "${POSTGRES_DB:-cyberteam}" \
    --format=custom \
    --file="/tmp/$(basename "$BACKUP_FILE")"
  run_step env COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT_NAME" \
    CYBERTEAM_ENV_FILE="$PROMOTE_ENV_FILE" \
    docker compose --env-file "$PROMOTE_ENV_FILE" cp \
    "postgres:/tmp/$(basename "$BACKUP_FILE")" \
    "$BACKUP_FILE"
fi

run_step docker image inspect "$CORE_IMAGE"
run_step docker image inspect "$UI_IMAGE"
run_step env COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT_NAME" \
  CORE_IMAGE="$CORE_IMAGE" UI_IMAGE="$UI_IMAGE" \
  CYBERTEAM_ENV_FILE="$PROMOTE_ENV_FILE" \
  docker compose --env-file "$PROMOTE_ENV_FILE" up -d --no-build \
  $PROMOTE_SERVICES

if [ "$RUN_COMPOSE_SMOKE" = "1" ]; then
  run_step env COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT_NAME" \
    CYBERTEAM_ENV_FILE="$PROMOTE_ENV_FILE" \
    COMPOSE_SMOKE_ENV_FILE="$PROMOTE_ENV_FILE" \
    COMPOSE_SMOKE_SKIP_UP=1 \
    COMPOSE_SMOKE_BUILD=0 \
    "$ROOT_DIR/scripts/compose-smoke.sh"
fi

record_args=(
  record
  --root "$ROOT_DIR"
  --environment "$PROMOTE_ENVIRONMENT"
  --deployment-manifest "$DEPLOYMENT_MANIFEST"
  --release-manifest "$RELEASE_MANIFEST"
  --backup-file "$BACKUP_FILE"
)
if [ "$DRY_RUN" = "1" ]; then
  record_args+=(--dry-run)
fi
if [ "$ALLOW_INCOMPLETE_CHECKS" = "1" ]; then
  record_args+=(--allow-incomplete-checks)
fi
if [ -n "$APPROVAL_FILE" ]; then
  record_args+=(--approval-file "$APPROVAL_FILE")
fi
record_path="$("$ROOT_DIR/scripts/promotion_policy.py" "${record_args[@]}")"

if [ "$DRY_RUN" = "1" ]; then
  echo "Promotion record would be written to $record_path"
else
  echo "Promotion record: $record_path"
fi
echo "Promotion plan completed for release $VERSION to $PROMOTE_ENVIRONMENT."
