#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE_SMOKE_SKIP_UP="${COMPOSE_SMOKE_SKIP_UP:-0}"
COMPOSE_SMOKE_BUILD="${COMPOSE_SMOKE_BUILD:-1}"
COMPOSE_SMOKE_CLEANUP="${COMPOSE_SMOKE_CLEANUP:-0}"
COMPOSE_SMOKE_SERVICES="${COMPOSE_SMOKE_SERVICES:-opa core ui}"
COMPOSE_SMOKE_ENV_FILE="${COMPOSE_SMOKE_ENV_FILE:-${CYBERTEAM_ENV_FILE:-$ROOT_DIR/.env}}"

created_env=0
if [ ! -f "$COMPOSE_SMOKE_ENV_FILE" ] && [ "$COMPOSE_SMOKE_ENV_FILE" = "$ROOT_DIR/.env" ] && [ -f "$ROOT_DIR/.env.example" ]; then
  cp "$ROOT_DIR/.env.example" "$COMPOSE_SMOKE_ENV_FILE"
  created_env=1
fi

if [ -f "$COMPOSE_SMOKE_ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$COMPOSE_SMOKE_ENV_FILE"
  set +a
fi

cleanup() {
  if [ "$COMPOSE_SMOKE_CLEANUP" = "1" ]; then
    docker compose --env-file "$COMPOSE_SMOKE_ENV_FILE" down --remove-orphans
  fi
  if [ "$created_env" = "1" ]; then
    rm -f "$COMPOSE_SMOKE_ENV_FILE"
  fi
}
trap cleanup EXIT

if [ "$COMPOSE_SMOKE_SKIP_UP" != "1" ]; then
  up_args=(up -d)
  if [ "$COMPOSE_SMOKE_BUILD" = "1" ]; then
    up_args+=(--build)
  fi
  # shellcheck disable=SC2086
  docker compose --env-file "$COMPOSE_SMOKE_ENV_FILE" "${up_args[@]}" $COMPOSE_SMOKE_SERVICES
fi

python3 "$ROOT_DIR/scripts/compose-smoke.py"
