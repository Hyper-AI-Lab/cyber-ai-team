#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Docker Compose gives inherited shell values precedence over --env-file values.
# Preserve that contract when loading credentials for the Python smoke client.
declare -A inherited_env_values=()
while IFS= read -r name; do
  inherited_env_values["$name"]="${!name}"
done < <(compgen -e)

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
  declare -a inherited_override_names=()
  while IFS= read -r name; do
    if [[ ${inherited_env_values[$name]+present} ]]; then
      inherited_override_names+=("$name")
    fi
  done < <(
    sed -nE \
      's/^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=.*/\2/p' \
      "$COMPOSE_SMOKE_ENV_FILE"
  )
  set -a
  # shellcheck disable=SC1090
  . "$COMPOSE_SMOKE_ENV_FILE"
  set +a
  for name in "${inherited_override_names[@]}"; do
    printf -v "$name" '%s' "${inherited_env_values[$name]}"
    export "${name?}"
  done
fi

# A caller-provided project name denotes an isolated smoke stack. Do not let a
# network name loaded from a staging env file attach that stack to live services.
if [[ ${inherited_env_values[COMPOSE_PROJECT_NAME]+present} ]] \
  && [[ ! ${inherited_env_values[CYBERTEAM_NETWORK_NAME]+present} ]]; then
  export CYBERTEAM_NETWORK_NAME="${COMPOSE_PROJECT_NAME}-network"
fi
if [[ ${inherited_env_values[COMPOSE_PROJECT_NAME]+present} ]] \
  && [[ ! ${inherited_env_values[CYBERTEAM_CONTAINER_PREFIX]+present} ]]; then
  export CYBERTEAM_CONTAINER_PREFIX="${COMPOSE_PROJECT_NAME}"
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
