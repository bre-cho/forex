#!/usr/bin/env bash
set -euo pipefail

BASE_REF="${1:-origin/main}"

if ! git rev-parse --verify "$BASE_REF" >/dev/null 2>&1; then
  echo "[drift-guard] Base ref '$BASE_REF' not found. Skipping check."
  exit 0
fi

changed_files=$(git diff --name-only "$BASE_REF"...HEAD)

if [[ -z "$changed_files" ]]; then
  echo "[drift-guard] No changed files detected."
  exit 0
fi

backend_changes=$(printf '%s\n' "$changed_files" | grep '^backend/' || true)
if [[ -z "$backend_changes" ]]; then
  echo "[drift-guard] No legacy backend changes detected."
  exit 0
fi

sync_changes=$(printf '%s\n' "$changed_files" | grep -E '^(apps/api/|services/|docs/adr/|docs/architecture/|tests/)' || true)
if [[ -n "$sync_changes" ]]; then
  echo "[drift-guard] Legacy backend change is paired with monorepo/docs/test updates."
  exit 0
fi

echo "[drift-guard] Detected changes under backend/ without corresponding monorepo sync updates."
echo "[drift-guard] To avoid logic drift, include at least one related change in:"
echo "  - apps/api/"
echo "  - services/"
echo "  - docs/adr/ or docs/architecture/"
echo "  - tests/"
echo ""
echo "[drift-guard] backend/ changes found:"
printf '%s\n' "$backend_changes"

exit 1
