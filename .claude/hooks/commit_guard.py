"""Two things this repo has to get right, checked while a commit is still cheap.

Both rules are written down in CLAUDE.md and both fail quietly: a German page
that never got its English page's change is only noticed by a German reader,
and an empty ``## [Unreleased]`` is only noticed by ``release.sh`` refusing to
run -- at which point the changelog has to be reconstructed from the log,
backwards, which is exactly the opposite of "the changelog is the release".

The EN/DE pairing rule mirrors ``tests/test_wiki_links.py``: an English page
``X.md`` under docs/wiki/ pairs with ``docs/wiki/de/DE-X.md``. This never
blocks -- it asks, because there are legitimate reasons to split the work (a
dead-code removal genuinely needs no changelog entry).

**An ``ask`` only stops someone who is there to answer.** In a non-interactive
or auto-approving session it resolves to "allow" and says nothing, so the
findings also go out as a ``systemMessage``, which is displayed either way.
That is the difference between this hook and ``guard_generated``: refusing a
write to a generated file is always right, so that one denies.
"""

from __future__ import annotations

import subprocess
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from _common import ROOT, allow, emit, payload  # noqa: E402

CHROME = {"README.md", "_Sidebar.md", "_Footer.md"}
SHIPPED = "custom_components/bavariandata/"


def _staged() -> list[str]:
    try:
        done = subprocess.run(  # noqa: S603
            ["git", "diff", "--cached", "--name-only"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in done.stdout.splitlines() if line.strip()]


def _unreleased_is_empty() -> bool:
    """True when CHANGELOG.md has an `## [Unreleased]` heading with nothing under it."""

    path = ROOT / "CHANGELOG.md"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False

    for i, line in enumerate(lines):
        if line.strip().lower().startswith("## [unreleased]"):
            for rest in lines[i + 1 :]:
                if rest.startswith("## "):
                    return True  # next release heading, nothing in between
                if rest.strip():
                    return False
            return True
    return False


def _pairing_gaps(staged: list[str]) -> list[str]:
    english = {
        p.rsplit("/", 1)[-1][:-3]
        for p in staged
        if p.startswith("docs/wiki/")
        and p.endswith(".md")
        and "/de/" not in p
        and p.rsplit("/", 1)[-1] not in CHROME
    }
    german = {
        p.rsplit("/", 1)[-1][3:-3]
        for p in staged
        if p.startswith("docs/wiki/de/DE-") and p.endswith(".md")
    }

    gaps = [f"docs/wiki/{s}.md changed, docs/wiki/de/DE-{s}.md did not" for s in sorted(english - german)]
    gaps += [f"docs/wiki/de/DE-{s}.md changed, docs/wiki/{s}.md did not" for s in sorted(german - english)]
    return gaps


def main() -> None:
    data = payload()
    command = (data.get("tool_input") or {}).get("command") or ""
    if "git commit" not in command:
        allow()

    staged = _staged()
    if not staged:
        allow()

    problems = _pairing_gaps(staged)

    if any(p.startswith(SHIPPED) for p in staged) and _unreleased_is_empty():
        problems.append(
            "shipped code is staged but CHANGELOG.md's `## [Unreleased]` is empty "
            "-- those notes are the release, and release.sh refuses to run without them"
        )

    if problems:
        detail = "Before this commit:\n- " + "\n- ".join(problems)
        emit(
            {
                # Shown whether or not anyone is around to answer the `ask`.
                "systemMessage": detail,
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": detail,
                },
            }
        )

    allow()


if __name__ == "__main__":
    main()
