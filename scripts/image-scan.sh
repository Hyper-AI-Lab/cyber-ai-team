#!/usr/bin/env bash
set -euo pipefail

TRIVY_IMAGE="${TRIVY_IMAGE:-aquasec/trivy:latest}"
IMAGE_SCAN_SEVERITY="${IMAGE_SCAN_SEVERITY:-HIGH,CRITICAL}"
IMAGE_SCAN_EXIT_CODE="${IMAGE_SCAN_EXIT_CODE:-1}"
IMAGE_SCAN_IGNORE_UNFIXED="${IMAGE_SCAN_IGNORE_UNFIXED:-1}"
IMAGE_SCAN_SCANNERS="${IMAGE_SCAN_SCANNERS:-vuln}"
IMAGE_SCAN_CACHE_VOLUME="${IMAGE_SCAN_CACHE_VOLUME:-cyberteam-trivy-cache}"

if [ "$#" -eq 0 ]; then
  echo "Usage: $0 IMAGE [IMAGE ...]" >&2
  exit 2
fi

scan_args=(
  image
  --scanners "$IMAGE_SCAN_SCANNERS"
  --severity "$IMAGE_SCAN_SEVERITY"
  --exit-code "$IMAGE_SCAN_EXIT_CODE"
)

if [ "$IMAGE_SCAN_IGNORE_UNFIXED" = "1" ]; then
  scan_args+=(--ignore-unfixed)
fi

for image in "$@"; do
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "Image not found locally: $image" >&2
    exit 1
  fi

  echo "Scanning image: $image"
  docker run --rm \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "$IMAGE_SCAN_CACHE_VOLUME:/root/.cache/" \
    "$TRIVY_IMAGE" \
    "${scan_args[@]}" \
    "$image"
done
