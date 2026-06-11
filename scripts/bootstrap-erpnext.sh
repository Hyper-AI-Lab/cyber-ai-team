#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ERPNEXT_ENV_FILE:-$ROOT_DIR/deploy/environments/staging.env}"
EVIDENCE_DIR="${ERPNEXT_BOOTSTRAP_EVIDENCE_DIR:-$ROOT_DIR/dist/erpnext/bootstrap}"
START_STACK="${ERPNEXT_BOOTSTRAP_START_STACK:-1}"
SITE_NAME="${ERPNEXT_SITE_NAME:-erpnext.hyperailab.com}"
INTEGRATION_USER="${ERPNEXT_INTEGRATION_USER:-cyberteam.integration@example.local}"
PUBLISHED_PORT="${ERPNEXT_PUBLISHED_PORT:-18100}"
FRONTEND_URL="${ERPNEXT_BOOTSTRAP_FRONTEND_URL:-http://127.0.0.1:$PUBLISHED_PORT}"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERPNext env file not found: $ENV_FILE" >&2
  exit 1
fi

load_env() {
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  SITE_NAME="${ERPNEXT_SITE_NAME:-$SITE_NAME}"
  INTEGRATION_USER="${ERPNEXT_INTEGRATION_USER:-$INTEGRATION_USER}"
  PUBLISHED_PORT="${ERPNEXT_PUBLISHED_PORT:-$PUBLISHED_PORT}"
  FRONTEND_URL="${ERPNEXT_BOOTSTRAP_FRONTEND_URL:-http://127.0.0.1:$PUBLISHED_PORT}"
}

upsert_env() {
  local key="$1"
  local value="$2"
  python3 - "$ENV_FILE" "$key" "$value" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
line = f"{key}={value}\n"
lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
for index, existing in enumerate(lines):
    if existing.startswith(f"{key}="):
        lines[index] = line
        break
else:
    if lines and not lines[-1].endswith("\n"):
        lines[-1] = f"{lines[-1]}\n"
    lines.append(line)
path.write_text("".join(lines), encoding="utf-8")
PY
}

is_placeholder_or_empty() {
  local value="${1:-}"
  [ -z "$value" ] || [[ "$value" == replace-with-* ]] || [[ "$value" == changeme-* ]]
}

generate_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 36 | tr -d '\n'
  else
    python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(36), end="")
PY
  fi
}

ensure_secret() {
  local key="$1"
  local current="${!key:-}"
  if is_placeholder_or_empty "$current"; then
    upsert_env "$key" "$(generate_secret)"
  fi
}

json_string() {
  python3 -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$1"
}

parse_scalar() {
  python3 -c '
import ast
import json
import sys

text = sys.stdin.read().strip()
lines = [line.strip() for line in text.splitlines() if line.strip()]
if not lines:
    raise SystemExit("empty command output")
candidate = lines[-1]
try:
    value = ast.literal_eval(candidate)
except Exception:
    try:
        value = json.loads(candidate)
    except Exception:
        value = candidate
if isinstance(value, (list, tuple)) and value:
    value = value[0]
if isinstance(value, dict):
    for key in ("message", "name", "api_key", "api_secret"):
        if key in value:
            value = value[key]
            break
print(str(value))
'
}

parse_field() {
  local field="$1"
  python3 -c '
import ast
import json
import sys

field = sys.argv[1]
text = sys.stdin.read().strip()
lines = [line.strip() for line in text.splitlines() if line.strip()]
if not lines:
    raise SystemExit("empty command output")
candidate = lines[-1]
try:
    value = ast.literal_eval(candidate)
except Exception:
    value = json.loads(candidate)
if not isinstance(value, dict) or field not in value:
    raise SystemExit(f"missing expected field: {field}")
print(str(value[field]))
' "$field"
}

url_quote() {
  python3 -c 'from urllib.parse import quote; import sys; print(quote(sys.argv[1], safe=""))' "$1"
}

load_env
upsert_env ERPNEXT_IMAGE "${ERPNEXT_IMAGE:-frappe/erpnext:v16.21.1}"
upsert_env ERPNEXT_URL "${ERPNEXT_URL:-http://erpnext-frontend:8080}"
upsert_env ERPNEXT_SITE_NAME "$SITE_NAME"
upsert_env ERPNEXT_EDGE_DOMAIN "${ERPNEXT_EDGE_DOMAIN:-erpnext.hyperailab.com}"
upsert_env ERPNEXT_PUBLISHED_PORT "$PUBLISHED_PORT"
upsert_env ERPNEXT_INTEGRATION_USER "$INTEGRATION_USER"
upsert_env REQUIRED_COMMUNICATION_PROVIDERS "${REQUIRED_COMMUNICATION_PROVIDERS:-smtp,imap,erpnext}"
load_env
ensure_secret ERPNEXT_ADMIN_PASSWORD
ensure_secret ERPNEXT_MARIADB_ROOT_PASSWORD
ensure_secret ERPNEXT_DB_PASSWORD
load_env

COMPOSE=(docker compose --env-file "$ENV_FILE" --profile erp)

if [ "$START_STACK" = "1" ]; then
  "${COMPOSE[@]}" up -d \
    erpnext-mariadb \
    erpnext-redis-cache \
    erpnext-redis-queue \
    erpnext-configurator \
    erpnext-create-site \
    erpnext-backend \
    erpnext-websocket \
    erpnext-queue-short \
    erpnext-queue-long \
    erpnext-scheduler \
    erpnext-frontend
fi

site_ready=0
for _ in $(seq 1 180); do
  if curl -fsS -H "Host: $SITE_NAME" "$FRONTEND_URL/api/method/ping" >/dev/null 2>&1; then
    site_ready=1
    break
  fi
  sleep 2
done

if [ "$site_ready" != "1" ]; then
  echo "Timed out waiting for ERPNext frontend at $FRONTEND_URL" >&2
  "${COMPOSE[@]}" logs --tail=120 erpnext-create-site erpnext-backend erpnext-frontend >&2 || true
  exit 1
fi

bench_execute() {
  "${COMPOSE[@]}" exec -T erpnext-backend bench --site "$SITE_NAME" execute "$@"
}

user_json="$(
  python3 - "$INTEGRATION_USER" <<'PY'
import json
import sys

email = sys.argv[1]
payload = {
    "doc": {
        "doctype": "User",
        "email": email,
        "first_name": "Cyber-Team",
        "last_name": "Integration",
        "enabled": 1,
        "user_type": "System User",
        "send_welcome_email": 0,
        "roles": [{"role": "System Manager"}],
    }
}
print(json.dumps(payload))
PY
)"
exists_args="[ $(json_string "User"), $(json_string "$INTEGRATION_USER") ]"
if ! bench_execute frappe.db.exists --args "$exists_args" | grep -q "$INTEGRATION_USER"; then
  bench_execute frappe.client.insert --kwargs "$user_json" >/dev/null
fi

enabled_args="[ $(json_string "User"), $(json_string "$INTEGRATION_USER"), $(json_string "enabled"), 1 ]"
bench_execute frappe.client.set_value --args "$enabled_args" >/dev/null
roles_json="$(
  python3 - <<'PY'
import json

print(json.dumps([
    "System Manager",
    "Sales Manager",
    "Sales User",
    "Projects User",
    "Support Team",
    "Item Manager",
    "Purchase Manager",
    "Accounts Manager",
]))
PY
)"
"${COMPOSE[@]}" exec -T erpnext-backend bench --site "$SITE_NAME" console >/dev/null <<PY
import frappe
import json

email = $(json_string "$INTEGRATION_USER")
desired_roles = json.loads($(json_string "$roles_json"))
user = frappe.get_doc("User", email)
existing_roles = {row.role for row in user.roles}
for role in desired_roles:
    if frappe.db.exists("Role", role) and role not in existing_roles:
        user.append("roles", {"role": role})
user.save(ignore_permissions=True)
frappe.db.commit()
PY
bench_execute frappe.db.commit >/dev/null

secret_args="[ $(json_string "$INTEGRATION_USER") ]"
generated_keys="$(bench_execute frappe.core.doctype.user.user.generate_keys --args "$secret_args")"
api_secret="$(printf '%s' "$generated_keys" | parse_field api_secret)"
generated_api_key="$(printf '%s' "$generated_keys" | parse_field api_key)"
bench_execute frappe.db.commit >/dev/null
api_key_args="[ $(json_string "User"), $(json_string "$INTEGRATION_USER"), $(json_string "api_key") ]"
api_key="$(bench_execute frappe.db.get_value --args "$api_key_args" | parse_scalar)"
if [ "$api_key" != "$generated_api_key" ]; then
  echo "ERPNext API key generation returned a key that does not match the stored user key." >&2
  exit 1
fi

if [ -z "$api_key" ] || [ -z "$api_secret" ]; then
  echo "ERPNext API key generation did not return both key and secret." >&2
  exit 1
fi

encoded_user="$(url_quote "$INTEGRATION_USER")"
curl -fsS \
  -H "Host: $SITE_NAME" \
  -H "Authorization: token $api_key:$api_secret" \
  "$FRONTEND_URL/api/resource/User/$encoded_user" >/dev/null

upsert_env ERPNEXT_API_KEY "$api_key"
upsert_env ERPNEXT_API_SECRET "$api_secret"

mkdir -p "$EVIDENCE_DIR"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
evidence_file="$EVIDENCE_DIR/erpnext-bootstrap-$timestamp.json"
ERPNEXT_BOOTSTRAP_SITE_NAME="$SITE_NAME" \
ERPNEXT_BOOTSTRAP_INTEGRATION_USER="$INTEGRATION_USER" \
ERPNEXT_BOOTSTRAP_FRONTEND_URL="$FRONTEND_URL" \
ERPNEXT_BOOTSTRAP_ENV_FILE="$ENV_FILE" \
ERPNEXT_BOOTSTRAP_EVIDENCE_FILE="$evidence_file" \
python3 - <<'PY'
import json
import os
from pathlib import Path

payload = {
    "status": "passed",
    "site_name": os.environ["ERPNEXT_BOOTSTRAP_SITE_NAME"],
    "integration_user": os.environ["ERPNEXT_BOOTSTRAP_INTEGRATION_USER"],
    "frontend_url": os.environ["ERPNEXT_BOOTSTRAP_FRONTEND_URL"],
    "env_file": os.environ["ERPNEXT_BOOTSTRAP_ENV_FILE"],
    "validated_endpoints": [
        "/api/method/ping",
        "/api/resource/User/<integration-user>",
    ],
    "completed_at": __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).isoformat(),
}
Path(os.environ["ERPNEXT_BOOTSTRAP_EVIDENCE_FILE"]).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

echo "ERPNext bootstrap passed."
echo "Site: $SITE_NAME"
echo "Integration user: $INTEGRATION_USER"
echo "Evidence: $evidence_file"
