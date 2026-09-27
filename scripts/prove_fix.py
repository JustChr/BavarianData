#!/usr/bin/env python3
"""Prove that a test guards a fix: it passes with the fix and fails without it.

CLAUDE.md asks every coordinator bug fix to add a scenario or replay "that fails
without the fix -- prove it by reverting the fix once". This does the revert, and
puts everything back however the run ends:

1. run the named tests on the working tree -- they must **pass**;
2. put the base version back for every changed file outside ``tests/`` (a file
   the change added is removed for the run), and run them again -- they must
   **fail**;
3. restore the working tree byte for byte, and check it matches.

    python scripts/prove_fix.py tests/test_scenarios_charging.py::test_x
    python scripts/prove_fix.py tests/test_foo.py --base v0.9.13
    python scripts/prove_fix.py tests/test_foo.py --revert custom_components/bavariandata/coordinator.py

``--base`` defaults to where this branch left ``origin/main`` (on ``main`` with
uncommitted work: ``HEAD``), so both committed and uncommitted changes count as
"the fix". ``--revert`` narrows what is put back when the change also touches
unrelated code. Reviewing a pull request is the same call from its checkout.

Exit status: 0 proven; 1 not proven (passes without the fix, or fails with it);
2 usage or git error.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

# Changes under these never count as "the fix": they are the proof, or prose.
NOT_THE_FIX = ("tests/", "docs/", ".claude/", "screenshots/")
NOT_THE_FIX_FILES = {"CHANGELOG.md", "README.md", "CLAUDE.md"}


def git(repo: Path, *args: str, binary: bool = False):
    out = subprocess.run(["git", *args], cwd=repo, capture_output=True)
    if out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {out.stderr.decode(errors='replace')}")
    return out.stdout if binary else out.stdout.decode("utf-8")


def default_base(repo: Path) -> str:
    try:
        return git(repo, "merge-base", "HEAD", "origin/main").strip()
    except SystemExit:
        return "HEAD"


def changed_files(repo: Path, base: str) -> list[str]:
    tracked = git(repo, "diff", "--name-only", base).split()
    untracked = git(repo, "ls-files", "--others", "--exclude-standard").split()
    return sorted(set(tracked) | set(untracked))


def is_the_fix(path: str) -> bool:
    return not path.startswith(NOT_THE_FIX) and path not in NOT_THE_FIX_FILES


def base_content(repo: Path, base: str, path: str) -> Optional[bytes]:
    """The file at ``base``, or ``None`` if the change added it."""

    listed = git(repo, "ls-tree", "--name-only", base, "--", path).strip()
    return git(repo, "show", f"{base}:{path}", binary=True) if listed else None


def run_tests(repo: Path, tests: list[str]) -> tuple[bool, str]:
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    tail = "\n".join(out.stdout.strip().splitlines()[-15:])
    # 5 = no tests collected: never a pass, and not a failure caused by the revert.
    if out.returncode == 5:
        raise SystemExit(f"pytest collected no tests for {tests}:\n{tail}")
    return out.returncode == 0, tail


def prove(repo: Path, tests: list[str], base: str, revert: Optional[list[str]]) -> int:
    fix = revert if revert else [p for p in changed_files(repo, base) if is_the_fix(p)]
    if not fix:
        print(f"Nothing to revert: no change outside tests/ since {base}.", file=sys.stderr)
        return 2

    print(f"Base: {base}")
    print("Reverting for the proof:")
    for path in fix:
        print(f"  {path}")

    ok, tail = run_tests(repo, tests)
    if not ok:
        print("\nNOT PROVEN: the tests fail *with* the fix.\n" + tail)
        return 1
    print("\n1/3 with the fix: pass")

    saved: dict[str, Optional[bytes]] = {}
    for path in fix:
        target = repo / path
        saved[path] = target.read_bytes() if target.exists() else None
    # A copy on disk too, so a killed process still leaves a way back.
    backup = Path(tempfile.mkdtemp(prefix="prove_fix_"))
    for path, data in saved.items():
        if data is not None:
            (backup / path).parent.mkdir(parents=True, exist_ok=True)
            (backup / path).write_bytes(data)

    try:
        for path in fix:
            old = base_content(repo, base, path)
            target = repo / path
            if old is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(old)
        failed_without, tail_without = run_tests(repo, tests)
        failed_without = not failed_without
    finally:
        for path, data in saved.items():
            target = repo / path
            if data is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)

    for path, data in saved.items():
        target = repo / path
        now = target.read_bytes() if target.exists() else None
        if now != data:
            print(f"\nRESTORE FAILED for {path}; your copy is in {backup}", file=sys.stderr)
            return 2
    print(f"   (backup of the working tree copies, safe to delete: {backup})")

    if not failed_without:
        print("\nNOT PROVEN: the tests also pass *without* the fix -- they do not guard it.")
        print(tail_without)
        return 1
    print("2/3 without the fix: fail (as it should)")
    print("--- what failed without the fix ---\n" + tail_without + "\n---")

    ok, tail = run_tests(repo, tests)
    if not ok:
        print("\nThe tests fail after restoring the working tree:\n" + tail)
        return 1
    print("3/3 restored: pass\n\nPROVEN: the tests fail without the fix and pass with it.")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("tests", nargs="+", help="pytest node ids or files")
    parser.add_argument("--base", help="git ref the fix is measured against")
    parser.add_argument(
        "--revert", action="append", metavar="PATH", help="revert only these (repeatable)"
    )
    parser.add_argument("--repo", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    repo = args.repo or Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
    base = args.base or default_base(repo)
    return prove(repo, args.tests, base, args.revert)


if __name__ == "__main__":
    raise SystemExit(main())
