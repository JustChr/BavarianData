"""Shared plumbing for the BavarianData hooks.

Every hook is handed the tool call as JSON on stdin and answers on stdout. The
helpers here keep that contract in one place so a hook body is only its rule.
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

# .claude/hooks/_common.py -> repo root
ROOT = pathlib.Path(__file__).resolve().parents[2]


def payload() -> dict[str, Any]:
    """The hook input, or an empty dict when stdin holds nothing usable."""

    try:
        return json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, OSError):
        return {}


def touched_path(data: dict[str, Any]) -> pathlib.Path | None:
    """Absolute path of the file an Edit/Write call targets."""

    raw = (data.get("tool_input") or {}).get("file_path")
    if not raw:
        return None
    try:
        return pathlib.Path(raw).resolve()
    except (OSError, ValueError):
        return None


def repo_relative(path: pathlib.Path) -> str | None:
    """``docs/wiki/Home.md`` style path, or None when outside the repo.

    Comparison is lowercased: Windows hands back whatever case the caller used
    and a case-sensitive match would let a generated file through.
    """

    try:
        return path.relative_to(ROOT).as_posix().lower()
    except ValueError:
        return None


def emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj))
    sys.exit(0)


def allow() -> None:
    """Say nothing and get out of the way.

    Silence is the common case: a hook that speaks on every call trains the
    reader to ignore it.
    """

    sys.exit(0)
