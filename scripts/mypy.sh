#!/usr/bin/env bash
# Strict typing against a real Home Assistant (pyproject.toml's [tool.mypy]) --
# the Quality Scale's Platinum ``strict-typing`` rule. Linux only, like
# scripts/test_ha.sh, whose venv and WSL copy it shares:
#
#   bash scripts/mypy.sh                      # the whole package
#   bash scripts/mypy.sh path/to/module.py    # mypy arguments pass through
#
# CI runs ``mypy`` in its "Home Assistant tests" job (.github/workflows/test.yml).

set -euo pipefail

if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]]; then
  repo="$(cd "$(dirname "$0")/.." && pwd -W)"
  wsl_repo="$(MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu -- wslpath -a "$repo" | tr -d '\r')"
  exec env MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu -- bash "$wsl_repo/scripts/mypy.sh" "$@"
fi

repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo"
export PATH="$HOME/.local/bin:$PATH"

# Off the Windows mount for the same reason as the HA suite: every stat across
# it is slow, and mypy stats every module it reads.
if [[ "$repo" == /mnt/* ]]; then
  copy="${BAVARIANDATA_HA_COPY:-$HOME/bd-ha/src}"
  mkdir -p "$copy"
  rsync -a --delete --exclude .git --exclude node_modules --exclude site \
    --exclude __pycache__ --exclude .venv --exclude .mypy_cache "$repo/" "$copy/"
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

python -m mypy "$@"
