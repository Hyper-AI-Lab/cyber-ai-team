#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROMTOOL_IMAGE="${PROMTOOL_IMAGE:-prom/prometheus:latest}"

if command -v promtool >/dev/null 2>&1; then
  promtool check config "$ROOT_DIR/monitoring/prometheus.yml"
  promtool check rules "$ROOT_DIR/monitoring/alerts.yml"
else
  docker run --rm \
    --entrypoint promtool \
    -v "$ROOT_DIR/monitoring:/etc/prometheus:ro" \
    "$PROMTOOL_IMAGE" \
    check config /etc/prometheus/prometheus.yml
  docker run --rm \
    --entrypoint promtool \
    -v "$ROOT_DIR/monitoring:/etc/prometheus:ro" \
    "$PROMTOOL_IMAGE" \
    check rules /etc/prometheus/alerts.yml
fi

if command -v amtool >/dev/null 2>&1; then
  amtool check-config "$ROOT_DIR/monitoring/alertmanager.yml"
else
  docker run --rm \
    --entrypoint amtool \
    -v "$ROOT_DIR/monitoring:/etc/alertmanager:ro" \
    prom/alertmanager:latest \
    check-config /etc/alertmanager/alertmanager.yml
fi
