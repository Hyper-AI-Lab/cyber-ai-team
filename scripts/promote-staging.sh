#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PROMOTE_ENVIRONMENT="${PROMOTE_ENVIRONMENT:-staging}"

if [ -n "${STAGING_ENV_FILE:-}" ] && [ -z "${PROMOTE_ENV_FILE:-}" ]; then
  export PROMOTE_ENV_FILE="$STAGING_ENV_FILE"
fi

exec "$ROOT_DIR/scripts/promote-release.sh" "$@"
