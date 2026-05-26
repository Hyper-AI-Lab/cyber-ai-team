#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${RELEASE_VERSION:-$(git -C "$ROOT_DIR" rev-parse --short HEAD)}"
ALLOW_DIRTY="${RELEASE_ALLOW_DIRTY:-0}"
RUN_QUALITY_GATE="${RUN_QUALITY_GATE:-1}"
RUN_MIGRATION_REHEARSAL="${RUN_MIGRATION_REHEARSAL:-1}"
RUN_COMPOSE_SMOKE="${RUN_COMPOSE_SMOKE:-0}"
BUILD_IMAGES="${BUILD_IMAGES:-1}"
MANIFEST_DIR="${RELEASE_MANIFEST_DIR:-$ROOT_DIR/dist/releases}"

if [ "$ALLOW_DIRTY" != "1" ] && [ -n "$(git -C "$ROOT_DIR" status --short)" ]; then
  echo "Refusing release check from a dirty working tree. Set RELEASE_ALLOW_DIRTY=1 to override." >&2
  exit 1
fi

if [ "$RUN_QUALITY_GATE" = "1" ]; then
  (
    cd "$ROOT_DIR"
    RUN_MIGRATION_REHEARSAL=0 RUN_COMPOSE_SMOKE=0 ./scripts/quality-gate.sh
  )
fi

if [ "$RUN_MIGRATION_REHEARSAL" = "1" ]; then
  (cd "$ROOT_DIR" && ./scripts/migration-rehearsal.sh)
fi

if [ "$RUN_COMPOSE_SMOKE" = "1" ]; then
  (cd "$ROOT_DIR" && COMPOSE_SMOKE_CLEANUP=1 ./scripts/compose-smoke.sh)
fi

if [ "$BUILD_IMAGES" = "1" ]; then
  docker build -t "cyber-team-core:$VERSION" "$ROOT_DIR/backend"
  docker build -t "cyber-team-ui:$VERSION" "$ROOT_DIR/frontend"
fi

mkdir -p "$MANIFEST_DIR"
cat >"$MANIFEST_DIR/$VERSION.json" <<JSON
{
  "version": "$VERSION",
  "git_commit": "$(git -C "$ROOT_DIR" rev-parse HEAD)",
  "git_branch": "$(git -C "$ROOT_DIR" branch --show-current)",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "checks": {
    "quality_gate": "$RUN_QUALITY_GATE",
    "migration_rehearsal": "$RUN_MIGRATION_REHEARSAL",
    "compose_smoke": "$RUN_COMPOSE_SMOKE",
    "images_built": "$BUILD_IMAGES"
  },
  "images": {
    "core": "cyber-team-core:$VERSION",
    "ui": "cyber-team-ui:$VERSION"
  }
}
JSON

echo "Release candidate $VERSION verified."
echo "Manifest: $MANIFEST_DIR/$VERSION.json"
