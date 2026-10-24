#!/usr/bin/env bash
# Both suites' coverage, combined per module -- what CI's "Home Assistant
# tests" job reports, run locally (Linux only; re-runs itself in WSL):
#
#   bash scripts/coverage.sh             # the per-module table
#   bash scripts/coverage.sh --missing   # + each module's missed lines below 95 %
#
# Runs from the same WSL copy and venv as scripts/test_ha.sh. Leaves
# coverage.json in the repo root (git-ignored) for a closer look.

set -euo pipefail

if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]]; then
  repo="$(cd "$(dirname "$0")/.." && pwd -W)"
  wsl_repo="$(MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu -- wslpath -a "$repo" | tr -d '\r')"
  exec env MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu -- bash "$wsl_repo/scripts/coverage.sh" "$@"
fi

repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo"
export PATH="$HOME/.local/bin:$PATH"
if [[ "$repo" == /mnt/* ]]; then
  copy="${BAVARIANDATA_HA_COPY:-$HOME/bd-ha/src}"
  mkdir -p "$copy"
  # site/ too (not its build): one HA-free test reads the page list.
  rsync -a --delete --exclude .git --exclude node_modules --exclude site/dist \
    --exclude __pycache__ --exclude .venv "$repo/" "$copy/"
  cd "$copy"
fi
# Two venvs, as in CI: the suites pin different pytest versions.
ha="${BAVARIANDATA_HA_VENV:-$HOME/bd-ha/.venv}"
unit="${BAVARIANDATA_UNIT_VENV:-$HOME/bd-ha/.venv-unit}"
for venv in "$ha" "$unit"; do
  [[ -x "$venv/bin/python" ]] || uv venv -q --python 3.14 "$venv"
done
uv pip install -q --python "$ha/bin/python" -r requirements_test_ha.txt
uv pip install -q --python "$unit/bin/python" -r requirements_test.txt

rm -f .coverage.unit .coverage.ha .coverage
COVERAGE_FILE=.coverage.unit "$unit/bin/python" -m pytest tests/ -q -p no:cacheprovider \
  --cov --cov-report= >/dev/null || { echo "tests/ failed"; exit 1; }
COVERAGE_FILE=.coverage.ha "$ha/bin/python" -m pytest -c tests_ha/pytest.ini tests_ha -q \
  -p no:cacheprovider --cov=custom_components/bavariandata --cov-report= >/dev/null \
  || { echo "tests_ha/ failed"; exit 1; }
"$unit/bin/coverage" combine -q .coverage.unit .coverage.ha
"$unit/bin/coverage" json -q -o coverage.json
if [[ "$PWD" != "$repo" ]]; then cp coverage.json "$repo/coverage.json"; fi
"$unit/bin/python" scripts/coverage_modules.py coverage.json "$@"
