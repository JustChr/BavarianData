"""Lint the one file that was just written, straight after writing it.

CI already runs ruff over the whole tree, but that verdict arrives minutes
later on a push. The selected rules are bug patterns rather than cosmetics
(pyflakes, bugbear, flake8-logging -- see pyproject.toml), so a hit here is
worth interrupting for, and the fix is cheapest while the change is still the
subject of the turn.

The bundled card gets `node --check`: it ships unbundled and unminified
straight to users' browsers, so a syntax error would be discovered by them.
"""

from __future__ import annotations

import subprocess
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from _common import ROOT, allow, emit, payload, repo_relative, touched_path  # noqa: E402


def _blocked(reason: str) -> None:
    emit({"decision": "block", "reason": reason})


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(  # noqa: S603
            cmd, cwd=ROOT, capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        # A missing linter must never stand between the model and its work.
        return 0, ""
    return done.returncode, (done.stdout + done.stderr).strip()


def main() -> None:
    data = payload()
    path = touched_path(data)
    if path is None:
        allow()

    rel = repo_relative(path)
    if rel is None:  # outside the repo -- not ours to police
        allow()

    if rel.endswith(".py"):
        code, out = _run([sys.executable, "-m", "ruff", "check", str(path)])
        if code != 0 and out:
            _blocked(f"ruff flagged {rel}:\n\n{out}")

    elif rel.endswith(".js"):
        code, out = _run(["node", "--check", str(path)])
        if code != 0 and out:
            _blocked(f"{rel} is not valid JavaScript:\n\n{out}")

    allow()


if __name__ == "__main__":
    main()
