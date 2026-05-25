#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE_SMOKE_SKIP_UP="${COMPOSE_SMOKE_SKIP_UP:-0}"
COMPOSE_SMOKE_BUILD="${COMPOSE_SMOKE_BUILD:-1}"
COMPOSE_SMOKE_CLEANUP="${COMPOSE_SMOKE_CLEANUP:-0}"
COMPOSE_SMOKE_SERVICES="${COMPOSE_SMOKE_SERVICES:-opa core ui}"

created_env=0
if [ ! -f "$ROOT_DIR/.env" ] && [ -f "$ROOT_DIR/.env.example" ]; then
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  created_env=1
fi

if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT_DIR/.env"
  set +a
fi

cleanup() {
  if [ "$COMPOSE_SMOKE_CLEANUP" = "1" ]; then
    docker compose down --remove-orphans
  fi
  if [ "$created_env" = "1" ]; then
    rm -f "$ROOT_DIR/.env"
  fi
}
trap cleanup EXIT

if [ "$COMPOSE_SMOKE_SKIP_UP" != "1" ]; then
  up_args=(up -d)
  if [ "$COMPOSE_SMOKE_BUILD" = "1" ]; then
    up_args+=(--build)
  fi
  # shellcheck disable=SC2086
  docker compose "${up_args[@]}" $COMPOSE_SMOKE_SERVICES
fi

python3 "$ROOT_DIR/scripts/compose-smoke.py"
