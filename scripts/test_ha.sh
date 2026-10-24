#!/usr/bin/env bash
# Run the real-Home-Assistant suite (tests_ha/) -- Linux only.
#
# On Windows this re-runs itself inside WSL, with a venv kept in WSL's own
# home (~/bd-ha) so the repo stays free of a Linux interpreter:
#
#   bash scripts/test_ha.sh                 # the suite
#   bash scripts/test_ha.sh -k config_flow  # pytest arguments pass through
#   bash scripts/test_ha.sh --cov           # with per-module coverage
#
# CI runs the same command on ubuntu-latest (.github/workflows/test.yml).

set -euo pipefail

if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]]; then
  repo="$(cd "$(dirname "$0")/.." && pwd -W)"
  wsl_repo="$(MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu -- wslpath -a "$repo" | tr -d '\r')"
  exec env MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu -- bash "$wsl_repo/scripts/test_ha.sh" "$@"
fi

repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo"
export PATH="$HOME/.local/bin:$PATH"

# A checkout on a Windows drive (/mnt/c) runs from a copy in WSL's own home:
# Home Assistant's tests run asyncio in debug mode, which stats source files
# for every job it schedules, and each stat across the Windows mount is slow
# (the suite took minutes, 19 s from the copy). Snapshot updates come back.
snapshot_update=false
for arg in "$@"; do
  [[ "$arg" == --snapshot-update ]] && snapshot_update=true
done
if [[ "$repo" == /mnt/* ]]; then
  copy="${BAVARIANDATA_HA_COPY:-$HOME/bd-ha/src}"
  mkdir -p "$copy"
  rsync -a --delete --exclude .git --exclude node_modules --exclude site \
    --exclude __pycache__ --exclude .venv "$repo/" "$copy/"
  cd "$copy"
fi
venv="${BAVARIANDATA_HA_VENV:-$HOME/bd-ha/.venv}"
if [[ ! -x "$venv/bin/python" ]]; then
  echo "── creating $venv (Python 3.14)"
  uv venv -q --python 3.14 "$venv"
fi
# shellcheck disable=SC1091
. "$venv/bin/activate"
uv pip install -q -r requirements_test_ha.txt

cov=()
args=()
for arg in "$@"; do
  if [[ "$arg" == "--cov" ]]; then
    cov=(--cov=custom_components/bavariandata --cov-report=term-missing:skip-covered)
  else
    args+=("$arg")
  fi
done
status=0
python -m pytest -c tests_ha/pytest.ini -q -p no:cacheprovider "${cov[@]}" tests_ha "${args[@]}" \
  || status=$?
if [[ "$snapshot_update" == true && "$PWD" != "$repo" ]]; then
  rsync -a --delete tests_ha/snapshots/ "$repo/tests_ha/snapshots/"
fi
exit "$status"
