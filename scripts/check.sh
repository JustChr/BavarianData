#!/usr/bin/env bash
# CI's gates, locally, with CI's exact paths (.github/workflows/test.yml).
#
# Runs every gate even when an earlier one fails, so one run shows everything
# that would turn CI red. Exits non-zero if any gate failed.
#
#   bash scripts/check.sh           # all gates
#   bash scripts/check.sh --no-tests # lint/format only (seconds, for a quick pass)
#
# Paths matter: `ruff format .` would also rewrite .claude/hooks/, which CI never
# checks, and naming files instead of directories bypasses the pyproject exclude
# that keeps the generated descriptor_metadata.py out of the formatter.
# Hassfest and the HACS action run only in CI.

set -uo pipefail

cd "$(dirname "$0")/.."

run_tests=1
for arg in "$@"; do
  case "$arg" in
    --no-tests) run_tests=0 ;;
    -h | --help)
      sed -n '2,14p' "$0"
      exit 0
      ;;
    *)
      echo "unknown option: $arg" >&2
      exit 2
      ;;
  esac
done

failed=()

gate() {
  local name="$1"
  shift
  echo "── $name"
  if "$@"; then
    echo "   ok"
  else
    echo "   FAILED: $name"
    failed+=("$name")
  fi
}

gate "ruff check" python -m ruff check custom_components/bavariandata tests
gate "ruff format --check" python -m ruff format --check custom_components/bavariandata tests tools

if command -v node >/dev/null 2>&1; then
  gate "node --check card" node --check custom_components/bavariandata/www/bavariandata-card.js
  if [[ -d node_modules ]]; then
    gate "eslint card" npx --no-install eslint custom_components/bavariandata/www
  else
    echo "── eslint card: skipped (run 'npm ci' once to install the dev linter)"
    failed+=("eslint (not installed)")
  fi
else
  echo "── node: not found — card checks and the card tests will be skipped"
  failed+=("node (not installed)")
fi

# Advisory, never red: an English string that changed while a translation did
# not may be fine (a typo fix) -- but it is usually a sentence left saying the
# old thing in nine languages. Structural gaps fail the parity tests below.
echo "── translations (stale since this branch left origin/main)"
if ! python tools/i18n_gaps.py; then
  echo "   note: see the list above; the translate skill works through it"
fi

if [[ $run_tests == 1 ]]; then
  gate "pytest" python -m pytest tests/ -q
fi

echo
if ((${#failed[@]})); then
  echo "RED — ${#failed[@]} gate(s) failed: ${failed[*]}"
  exit 1
fi
echo "GREEN — every local gate passed (hassfest and HACS run in CI only)"
