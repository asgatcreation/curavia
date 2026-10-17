#!/usr/bin/env bash
# Keep the tenant escape hatch rare and visible.
#
# The guard in apps/core/tenancy.py only protects anything if bypassing it
# stays exceptional. Two things bypass it:
#
#   * Model.unscoped  — the unscoped manager
#   * unscoped(...)   — the context manager
#
# Both are legitimate in a handful of places: platform-level aggregates, the
# nightly demo reset, migrations, and test setup. This script fails the build
# if either appears in a file that is not listed in
# docs/cross-tenant-allowlist.txt — so adding one is a deliberate act that
# shows up in review, rather than a quiet convenience that spreads.
#
# Usage:  bash scripts/check-cross-tenant-allowlist.sh

set -euo pipefail

cd "$(dirname "$0")/.."

ALLOWLIST="docs/cross-tenant-allowlist.txt"

if [[ ! -f "$ALLOWLIST" ]]; then
  echo "error: $ALLOWLIST is missing." >&2
  exit 1
fi

# Strip comments and blank lines.
mapfile -t allowed < <(grep -vE '^[[:space:]]*(#|$)' "$ALLOWLIST" | sed 's/[[:space:]]*$//')

# Every file that uses an escape hatch. The frontend has no database access,
# so only the backend is scanned. Migrations are generated, not written.
mapfile -t offenders < <(
  grep -rlE '(\.unscoped\b|[^_a-zA-Z]unscoped\()' backend \
    --include='*.py' \
    --exclude-dir='.venv' \
    --exclude-dir='migrations' \
    --exclude-dir='__pycache__' \
    2>/dev/null | sed 's|\\|/|g' | sort
)

violations=()
for file in "${offenders[@]:-}"; do
  [[ -z "$file" ]] && continue
  found=0
  for entry in "${allowed[@]:-}"; do
    [[ -z "$entry" ]] && continue
    if [[ "$file" == "$entry" ]]; then
      found=1
      break
    fi
  done
  if [[ "$found" -eq 0 ]]; then
    violations+=("$file")
  fi
done

if [[ ${#violations[@]} -gt 0 ]]; then
  {
    echo "Cross-tenant escape hatch used in files that are not allowlisted:"
    echo
    printf '  - %s\n' "${violations[@]}"
    echo
    echo "Either scope the query properly, or — if this really is a cross-hospital"
    echo "operation — add the file to $ALLOWLIST with a one-line reason explaining why."
  } >&2
  exit 1
fi

# Warn about allowlist rot: entries for files that no longer need to be there.
stale=()
for entry in "${allowed[@]:-}"; do
  [[ -z "$entry" ]] && continue
  if [[ ! -f "$entry" ]]; then
    stale+=("$entry (file does not exist)")
  elif ! grep -qE '(\.unscoped\b|[^_a-zA-Z]unscoped\()' "$entry" 2>/dev/null; then
    stale+=("$entry (no longer uses it)")
  fi
done

if [[ ${#stale[@]} -gt 0 ]]; then
  echo "warning: $ALLOWLIST has stale entries:" >&2
  printf '  - %s\n' "${stale[@]}" >&2
fi

echo "ok: ${#offenders[@]} file(s) use the cross-tenant escape hatch, all allowlisted."
