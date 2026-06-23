#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${CYBERTEAM_ENV_FILE:-$ROOT_DIR/deploy/environments/staging.env}"
K6_IMAGE="${K6_IMAGE:-grafana/k6:latest}"
EVIDENCE_DIR="${LOAD_SMOKE_EVIDENCE_DIR:-$ROOT_DIR/dist/load-tests}"
API_BASE="${API_BASE:-https://cyberteam.hyperailab.com}"
K6_VUS="${K6_VUS:-5}"
K6_DURATION="${K6_DURATION:-5m}"
K6_DOCKER_USER="${K6_DOCKER_USER:-$(id -u):$(id -g)}"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  API_BASE="${API_BASE:-${NEXT_PUBLIC_API_URL:-$API_BASE}}"
fi

if [ -z "${OWNER_EMAIL:-}" ] || [ -z "${OWNER_PASSWORD:-}" ]; then
  echo "OWNER_EMAIL and OWNER_PASSWORD are required for the load smoke." >&2
  exit 1
fi

mkdir -p "$EVIDENCE_DIR"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
evidence_file="/out/load-smoke-$timestamp.json"

docker run --rm --network host \
  --user "$K6_DOCKER_USER" \
  -e API_BASE="$API_BASE" \
  -e OWNER_EMAIL="$OWNER_EMAIL" \
  -e OWNER_PASSWORD="$OWNER_PASSWORD" \
  -e K6_VUS="$K6_VUS" \
  -e K6_DURATION="$K6_DURATION" \
  -e EVIDENCE_FILE="$evidence_file" \
  -v "$ROOT_DIR/scripts/k6:/scripts:ro" \
  -v "$EVIDENCE_DIR:/out" \
  "$K6_IMAGE" run /scripts/cyberteam-owner-console.js

echo "Load smoke evidence: $EVIDENCE_DIR/load-smoke-$timestamp.json"
