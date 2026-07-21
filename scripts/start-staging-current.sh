#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROMOTION_DIR="${PROMOTION_DIR:-$ROOT_DIR/dist/promotions/staging}"
ENV_FILE="${STAGING_ENV_FILE:-$ROOT_DIR/deploy/environments/staging.env}"
DRY_RUN="${START_STAGING_DRY_RUN:-1}"

if [ ! -d "$PROMOTION_DIR" ]; then
  echo "Promotion directory not found: $PROMOTION_DIR" >&2
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  echo "Staging environment file not found: $ENV_FILE" >&2
  exit 1
fi

latest_exports="$(
  python3 - "$PROMOTION_DIR" <<'PY'
import json
import shlex
import sys
from pathlib import Path

promotion_dir = Path(sys.argv[1])
records = []
for path in promotion_dir.glob("*.json"):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        continue
    promoted_at = data.get("promoted_at")
    if promoted_at:
        records.append((promoted_at, path, data))

if not records:
    raise SystemExit(f"No promotion records with promoted_at found in {promotion_dir}")

_, path, data = sorted(records, key=lambda item: item[0])[-1]
images = data.get("images") or {}
required = {
    "PROMOTION_RECORD": str(path),
    "COMPOSE_PROJECT_NAME": data.get("compose_project_name", "cyberteam-staging"),
    "CORE_IMAGE": images.get("core", ""),
    "UI_IMAGE": images.get("ui", ""),
    "APP_VERSION": data.get("version", ""),
    "BUILD_SHA": data.get("git_commit", ""),
}
missing = [key for key, value in required.items() if not value]
if missing:
    raise SystemExit(f"Promotion record {path} is missing required fields: {', '.join(missing)}")

for key, value in required.items():
    print(f"{key}={shlex.quote(str(value))}")
PY
)"

eval "$latest_exports"

echo "Current staging promotion record: $PROMOTION_RECORD"
echo "Compose project: $COMPOSE_PROJECT_NAME"
echo "Core image: $CORE_IMAGE"
echo "UI image: $UI_IMAGE"
echo "App version: $APP_VERSION"
echo "Build SHA: $BUILD_SHA"

cmd=(
  env
  "COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT_NAME"
  "CORE_IMAGE=$CORE_IMAGE"
  "UI_IMAGE=$UI_IMAGE"
  "APP_VERSION=$APP_VERSION"
  "BUILD_SHA=$BUILD_SHA"
  "CYBERTEAM_ENV_FILE=$ENV_FILE"
  docker compose
  --env-file "$ENV_FILE"
  --profile erp
  up -d --no-build
)

echo "+ ${cmd[*]}"
if [ "$DRY_RUN" = "1" ]; then
  echo "Dry run only. Set START_STAGING_DRY_RUN=0 to start the current promoted staging stack."
  exit 0
fi

"${cmd[@]}"

echo "Staging stack start requested for $APP_VERSION."
