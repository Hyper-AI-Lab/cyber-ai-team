#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${SOAK_ENV_FILE:-$ROOT_DIR/deploy/environments/staging.env}"
OUTPUT_DIR="${SOAK_OUTPUT_DIR:-$ROOT_DIR/dist/soak}"
CONTAINER_NAME="${SOAK_CONTAINER_NAME:-cyberteam-staging-soak}"
DURATION_SECONDS="${SOAK_DURATION_SECONDS:-86400}"
INTERVAL_SECONDS="${SOAK_INTERVAL_SECONDS:-300}"
IMAGE="${SOAK_IMAGE:-$(docker inspect cyberteam-staging-core --format '{{.Config.Image}}')}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Staging environment file not found: $ENV_FILE" >&2
  exit 1
fi
if docker ps -a --format '{{.Names}}' | grep -Fxq "$CONTAINER_NAME"; then
  echo "Soak container already exists: $CONTAINER_NAME" >&2
  exit 1
fi

health="$(curl -fsS https://cyberteam.hyperailab.com/health)"
expected_version="$(printf '%s' "$health" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')"
expected_build_sha="$(printf '%s' "$health" | python3 -c 'import json,sys; print(json.load(sys.stdin)["build_sha"])')"
mkdir -p "$OUTPUT_DIR"

docker run --detach --rm \
  --name "$CONTAINER_NAME" \
  --network host \
  --label cyberteam.evidence=staging-soak \
  --mount "type=bind,src=$ROOT_DIR/scripts,dst=/app/scripts,readonly" \
  --mount "type=bind,src=$ENV_FILE,dst=/run/secrets/staging.env,readonly" \
  --mount "type=bind,src=$OUTPUT_DIR,dst=/evidence" \
  "$IMAGE" \
  python /app/scripts/staging-soak.py \
    --env-file /run/secrets/staging.env \
    --api-base https://cyberteam.hyperailab.com \
    --duration-seconds "$DURATION_SECONDS" \
    --interval-seconds "$INTERVAL_SECONDS" \
    --output-dir /evidence \
    --expected-version "$expected_version" \
    --expected-build-sha "$expected_build_sha"

echo "Started $CONTAINER_NAME for ${DURATION_SECONDS}s at ${INTERVAL_SECONDS}s intervals."
echo "Evidence directory: $OUTPUT_DIR"
