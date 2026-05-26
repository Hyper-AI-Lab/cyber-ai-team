#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${ROLLBACK_TARGET:-}"
DRY_RUN="${ROLLBACK_DRY_RUN:-1}"
RESTORE_POSTGRES_BACKUP="${RESTORE_POSTGRES_BACKUP:-}"
POSTGRES_SERVICE="${POSTGRES_SERVICE:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-cyberteam}"
POSTGRES_USER="${POSTGRES_USER:-cyberteam}"

if [ -z "$TARGET" ]; then
  echo "ROLLBACK_TARGET is required. Use a git ref, tag, or commit SHA." >&2
  exit 1
fi

run_step() {
  echo "+ $*"
  if [ "$DRY_RUN" != "1" ]; then
    "$@"
  fi
}

if [ "$DRY_RUN" = "1" ]; then
  echo "Dry run only. Set ROLLBACK_DRY_RUN=0 to execute."
fi

run_step git -C "$ROOT_DIR" fetch --all --tags
run_step git -C "$ROOT_DIR" checkout "$TARGET"
run_step docker compose -f "$ROOT_DIR/docker-compose.yml" build core ui

if [ -n "$RESTORE_POSTGRES_BACKUP" ]; then
  if [ ! -f "$RESTORE_POSTGRES_BACKUP" ]; then
    echo "PostgreSQL backup not found: $RESTORE_POSTGRES_BACKUP" >&2
    exit 1
  fi
  echo "+ cat '$RESTORE_POSTGRES_BACKUP' | docker compose exec -T $POSTGRES_SERVICE pg_restore --clean --if-exists -U '$POSTGRES_USER' -d '$POSTGRES_DB'"
  if [ "$DRY_RUN" != "1" ]; then
    cat "$RESTORE_POSTGRES_BACKUP" | docker compose exec -T "$POSTGRES_SERVICE" \
      pg_restore --clean --if-exists -U "$POSTGRES_USER" -d "$POSTGRES_DB"
  fi
fi

run_step docker compose -f "$ROOT_DIR/docker-compose.yml" up -d postgres redis qdrant temporal opa core worker ui
run_step "$ROOT_DIR/scripts/compose-smoke.sh"

echo "Rollback plan completed for target $TARGET."
