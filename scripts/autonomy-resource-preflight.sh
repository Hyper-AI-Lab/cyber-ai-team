#!/usr/bin/env bash
set -euo pipefail

MIN_DISK_GB="${AUTONOMY_MIN_FREE_DISK_GB:-8}"
MIN_MEMORY_GB="${AUTONOMY_MIN_AVAILABLE_MEMORY_GB:-4}"

disk_kb="$(df -Pk / | awk 'NR == 2 {print $4}')"
memory_kb="$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)"
disk_gb=$((disk_kb / 1024 / 1024))
memory_gb=$((memory_kb / 1024 / 1024))

printf 'Autonomy resource preflight: disk_free=%sGiB memory_available=%sGiB\n' \
  "$disk_gb" "$memory_gb"

if [ "$disk_gb" -lt "$MIN_DISK_GB" ]; then
  printf 'Need at least %sGiB free disk before local model/research activation.\n' \
    "$MIN_DISK_GB" >&2
  exit 1
fi

if [ "$memory_gb" -lt "$MIN_MEMORY_GB" ]; then
  printf 'Need at least %sGiB available memory before local model activation.\n' \
    "$MIN_MEMORY_GB" >&2
  exit 1
fi

echo 'Autonomy resource preflight passed.'
