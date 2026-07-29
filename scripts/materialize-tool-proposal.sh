#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: $0 <sandbox-artifact-directory> [branch-name]" >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACT_DIR="$(realpath "$1")"
PATCH_PATH="$ARTIFACT_DIR/proposal.patch"
PROPOSAL_ID="$(basename "$ARTIFACT_DIR")"
BRANCH="${2:-codex/tool-proposal-${PROPOSAL_ID}}"

case "$BRANCH" in
  codex/tool-proposal-*) ;;
  *) echo "Branch must use the codex/tool-proposal-* namespace." >&2; exit 2 ;;
esac

if [ ! -f "$PATCH_PATH" ] || [ ! -f "$ARTIFACT_DIR/sbom.spdx.json" ]; then
  echo "Sandbox patch and SPDX SBOM are both required." >&2
  exit 1
fi

if [ -n "$(git -C "$ROOT_DIR" status --porcelain)" ]; then
  echo "Refusing to materialize into a dirty worktree." >&2
  exit 1
fi

if git -C "$ROOT_DIR" show-ref --verify --quiet "refs/heads/$BRANCH"; then
  echo "Branch already exists: $BRANCH" >&2
  exit 1
fi

git -C "$ROOT_DIR" apply --check "$PATCH_PATH"
git -C "$ROOT_DIR" switch -c "$BRANCH"
git -C "$ROOT_DIR" apply "$PATCH_PATH"

echo "Proposal materialized on $BRANCH."
echo "No code was staged, committed, pushed, deployed, or activated."
echo "Run the normal review, security scan, tests, and CI before integration."
