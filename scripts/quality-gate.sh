#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

PYTHON_BIN="${PYTHON_BIN:-python3}"
BACKEND_VENV="${BACKEND_VENV:-$ROOT_DIR/.venv-quality}"
BACKEND_AUDIT_VENV="${BACKEND_AUDIT_VENV:-/tmp/cyberteam-audit-venv}"
SKIP_BACKEND_INSTALL="${SKIP_BACKEND_INSTALL:-0}"
SKIP_BACKEND_AUDIT_INSTALL="${SKIP_BACKEND_AUDIT_INSTALL:-0}"
SKIP_FRONTEND_INSTALL="${SKIP_FRONTEND_INSTALL:-0}"
RUN_FRONTEND_BUILD="${RUN_FRONTEND_BUILD:-1}"
RUN_MIGRATION_REHEARSAL="${RUN_MIGRATION_REHEARSAL:-0}"
RUN_COMPOSE_SMOKE="${RUN_COMPOSE_SMOKE:-0}"

if [ -n "${FRONTEND_NODE_BIN:-}" ]; then
  FRONTEND_NODE_CMD=("$FRONTEND_NODE_BIN")
else
  node_major="$(node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null || echo 0)"
  if [ "$node_major" -ge 20 ]; then
    FRONTEND_NODE_CMD=(node)
  else
    FRONTEND_NODE_CMD=(npx -y node@20)
  fi
fi

run_frontend_node() {
  (
    cd "$FRONTEND_DIR"
    "${FRONTEND_NODE_CMD[@]}" "$@"
  )
}

if [ ! -x "$BACKEND_VENV/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$BACKEND_VENV"
fi

if [ "$SKIP_BACKEND_INSTALL" != "1" ]; then
  "$BACKEND_VENV/bin/python" -m pip install --upgrade pip setuptools wheel
  "$BACKEND_VENV/bin/python" -m pip install \
    -r "$BACKEND_DIR/requirements.txt" \
    pytest \
    pytest-asyncio \
    pytest-cov \
    ruff \
    alembic
fi

echo "== Backend: lint =="
(
  cd "$BACKEND_DIR"
  PYTHONPATH=src "$BACKEND_VENV/bin/ruff" check src tests alembic
)

echo "== Backend: tests =="
(
  cd "$BACKEND_DIR"
  PYTHONPATH=src "$BACKEND_VENV/bin/pytest" -q
)

echo "== Backend: compile =="
(
  cd "$BACKEND_DIR"
  PYTHONPATH=src "$BACKEND_VENV/bin/python" -m compileall -q src tests alembic
)

echo "== Backend: Alembic offline SQL =="
(
  cd "$BACKEND_DIR"
  PYTHONPATH=src "$BACKEND_VENV/bin/alembic" upgrade head --sql >/tmp/cyberteam-alembic.sql
)

if [ ! -x "$BACKEND_AUDIT_VENV/bin/pip-audit" ]; then
  "$PYTHON_BIN" -m venv "$BACKEND_AUDIT_VENV"
fi

if [ "$SKIP_BACKEND_AUDIT_INSTALL" != "1" ] || [ ! -x "$BACKEND_AUDIT_VENV/bin/pip-audit" ]; then
  "$BACKEND_AUDIT_VENV/bin/python" -m pip install --upgrade pip setuptools wheel
  "$BACKEND_AUDIT_VENV/bin/python" -m pip install pip-audit
fi

echo "== Backend: dependency audit =="
"$BACKEND_AUDIT_VENV/bin/pip-audit" -r "$BACKEND_DIR/requirements.txt"

if [ "$SKIP_FRONTEND_INSTALL" != "1" ]; then
  echo "== Frontend: install =="
  (cd "$FRONTEND_DIR" && npm ci)
fi

if [ "$RUN_FRONTEND_BUILD" = "1" ]; then
  echo "== Frontend: build =="
  run_frontend_node ./node_modules/next/dist/bin/next build
fi

echo "== Frontend: typecheck =="
run_frontend_node ./node_modules/typescript/bin/tsc --noEmit --incremental false

echo "== Frontend: tests =="
run_frontend_node ./node_modules/vitest/vitest.mjs run

echo "== Frontend: dependency audit =="
(cd "$FRONTEND_DIR" && npm audit --audit-level=moderate)

echo "== Compose: config =="
(cd "$ROOT_DIR" && docker compose config --quiet)

echo "== Operations: script and dashboard syntax =="
(cd "$ROOT_DIR" && bash -n scripts/*.sh)
"$PYTHON_BIN" -m json.tool \
  "$ROOT_DIR/monitoring/grafana/dashboards/cyberteam-overview.json" >/dev/null
unreadable_monitoring="$(
  cd "$ROOT_DIR" && find monitoring \( -type f ! -perm -004 -o -type d ! -perm -001 \) -print
)"
if [ -n "$unreadable_monitoring" ]; then
  echo "Monitoring files must be readable by non-root containers:" >&2
  echo "$unreadable_monitoring" >&2
  exit 1
fi

echo "== Security: secret scan =="
(cd "$ROOT_DIR" && "$PYTHON_BIN" scripts/secret-scan.py)

if [ "$RUN_MIGRATION_REHEARSAL" = "1" ]; then
  echo "== Database: migration rehearsal =="
  (cd "$ROOT_DIR" && BACKEND_VENV="$BACKEND_VENV" scripts/migration-rehearsal.sh)
fi

if [ "$RUN_COMPOSE_SMOKE" = "1" ]; then
  echo "== Compose: smoke/e2e =="
  (cd "$ROOT_DIR" && scripts/compose-smoke.sh)
fi

echo "== Git: diff hygiene =="
(cd "$ROOT_DIR" && git diff --check)

echo "Quality gate passed."
