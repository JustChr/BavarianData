#!/usr/bin/env python3
"""The refactoring gate: prove a change left the coordinator's behaviour alone.

Every coordinator test drives the real coordinator through the harness
(``tests/harness.py``). With tracing on, each harness writes down everything
the outside world can see after every step (``tests/trace.py``): events and
their payloads, dispatcher signals, repairs, store writes, timers and every
public getter, for every car. Two traces that match line for line are two
coordinators nobody can tell apart.

    python scripts/golden.py gate                # all three checks below
    python scripts/golden.py gate --base v0.9.23
    python scripts/golden.py record OUT [--twin 7.25] [-- pytest args]
    python scripts/golden.py compare A B [--car A]

``gate`` runs:

1. **determinism** -- the working tree traced twice must match, or a diff
   later would mean nothing;
2. **behaviour** -- the working tree's tests run once against the base's
   integration code and once against the working tree's, and the traces must
   match. The *tests* are the same both times (the base gets this tree's
   ``tests/`` laid over it), so only the code under them differs;
3. **isolation** -- for every ``--twin`` offset, the working tree traced with a
   second car in the account that repeats everything the first car does that
   many minutes later. The first car's lines must match its trace alone:
   nothing one car does may change what another car shows.

``--base`` defaults to where this branch left ``origin/main``. A trace that
differs prints the first differing lines of each test; the full traces stay
in the printed directory for a real diff tool.

Exit status: 0 all checks pass; 1 a check failed; 2 usage or git error.
"""

from __future__ import annotations

import argparse
import difflib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[1]
TRACE_DIR_ENV = "BAVARIANDATA_TRACE_DIR"
TWIN_ENV = "BAVARIANDATA_TWIN"
# Two twins: one a few minutes behind (its timers interleave with the first
# car's), one in lockstep (both cars' batches land on the same instant).
DEFAULT_TWINS = ("7.25", "0")
DIFF_LINES = 40


def git(*args: str, cwd: Path = REPO) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout


def default_base() -> str:
    try:
        return git("merge-base", "HEAD", "origin/main").strip()
    except SystemExit:
        return "HEAD"


def harness_tests(root: Path) -> list[str]:
    """Every test module that drives the coordinator through the harness."""

    found = []
    for path in sorted((root / "tests").glob("test_*.py")):
        if re.search(r"^from \.harness import", path.read_text(encoding="utf-8"), re.M):
            found.append(str(path.relative_to(root)).replace(os.sep, "/"))
    return found


class Run:
    """One traced pytest run, started in the background."""

    def __init__(
        self,
        out: Path,
        *,
        root: Path = REPO,
        twin: Optional[str] = None,
        pytest_args: Optional[list[str]] = None,
    ) -> None:
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        env = {**os.environ, TRACE_DIR_ENV: str(out), "PYTHONDONTWRITEBYTECODE": "1"}
        env.pop(TWIN_ENV, None)
        if twin is not None:
            env[TWIN_ENV] = twin
        args = pytest_args or harness_tests(root)
        command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args]
        self.label = f"{'twin ' + twin if twin is not None else 'solo'} in {root.name}"
        self.out = out
        self._log = out.with_suffix(".log")
        self._handle = self._log.open("w", encoding="utf-8")
        print(f"  tracing {self.label} -> {out}", flush=True)
        self._process = subprocess.Popen(
            command, cwd=root, env=env, stdout=self._handle, stderr=subprocess.STDOUT
        )

    def wait(self) -> int:
        """The pytest exit code, once the run is over; its summary printed."""

        code = self._process.wait()
        self._handle.close()
        output = self._log.read_text(encoding="utf-8", errors="replace")
        tail = output.strip().splitlines()[-1:] or ["(no output)"]
        print(f"  {self.label}: {tail[0]}", flush=True)
        if code != 0:
            print(output[-4000:])
        return code


def record(out: Path, **kwargs) -> int:
    """Trace the harness tests into ``out``; the pytest exit code."""

    return Run(out, **kwargs).wait()


# Left out of a one-car comparison: every car shares one debounced store, so
# when a car's section is written depends on the other cars' saves by design.
# What the section holds is compared through the ``history.*`` getters.
WRITE_TIMING = ("store ", "final ")


def _lines(path: Path, car: Optional[str]) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if car is None:
        return lines
    tag = f"  [{car}] "
    return [
        line
        for line in lines
        if not line.startswith("  [")
        or (line.startswith(tag) and not line[len(tag) :].startswith(WRITE_TIMING))
    ]


def compare(a: Path, b: Path, *, car: Optional[str] = None, label: str = "") -> bool:
    """True when every trace in ``a`` matches its namesake in ``b``."""

    names_a = {p.name for p in a.glob("*.trace")}
    names_b = {p.name for p in b.glob("*.trace")}
    ok = True
    for missing in sorted(names_a ^ names_b):
        side = a if missing in names_a else b
        print(f"  only in {side}: {missing}")
        ok = False
    differing = 0
    for name in sorted(names_a & names_b):
        left, right = _lines(a / name, car), _lines(b / name, car)
        if left == right:
            continue
        differing += 1
        ok = False
        if differing <= 5:
            diff = difflib.unified_diff(left, right, str(a / name), str(b / name), lineterm="", n=2)
            print("\n".join(list(diff)[:DIFF_LINES]))
    total = len(names_a & names_b)
    verdict = "match" if ok else f"{differing} of {total} differ"
    print(f"  {label or 'compare'}: {total} traces, {verdict}")
    return ok


def _overlay(base: str, work: Path) -> None:
    """A checkout of ``base`` with this working tree's ``tests/`` laid over it."""

    git("worktree", "add", "--detach", str(work), base)
    shutil.rmtree(work / "tests")
    shutil.copytree(
        REPO / "tests", work / "tests", ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )


def _remove_worktree(work: Path) -> None:
    """Remove the base checkout; never let a locked file fail the gate.

    Windows can hold a file of a just-finished pytest a moment longer, so
    removal is retried, then left to ``git worktree prune`` with a warning.
    """

    for attempt in range(5):
        out = subprocess.run(
            ["git", "worktree", "remove", "--force", str(work)],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        if out.returncode == 0:
            return
        time.sleep(1 + attempt)
    shutil.rmtree(work, ignore_errors=True)
    subprocess.run(["git", "worktree", "prune"], cwd=REPO, capture_output=True)
    print(f"  warning: could not remove {work} ({out.stderr.strip()}); pruned instead")


def gate(base: str, twins: list[str], keep: Path) -> int:
    keep.mkdir(parents=True, exist_ok=True)
    print(f"golden gate: base {base[:12]}, traces in {keep}")
    # Every run is independent of the others, so they all run at once.
    work = Path(tempfile.mkdtemp(prefix="golden-base-")) / "base"
    _overlay(base, work)
    try:
        head = Run(keep / "head")
        again = Run(keep / "head-again")
        on_base = Run(keep / "base", root=work)
        twin_runs = {offset: Run(keep / f"twin-{offset}", twin=offset) for offset in twins}
        codes = {
            "head": head.wait(),
            "again": again.wait(),
            "base": on_base.wait(),
            **{offset: run.wait() for offset, run in twin_runs.items()},
        }
    finally:
        _remove_worktree(work)
    if codes["head"] != 0:
        print("RED: the tests fail on the working tree")
        return 1

    checks: dict[str, bool] = {}
    print("1. determinism")
    checks["determinism"] = codes["again"] == 0 and compare(
        head.out, again.out, label="determinism"
    )
    print("2. behaviour against the base")
    if codes["base"] != 0:
        print("  the working tree's tests fail on the base's code (see above)")
        checks["behaviour"] = False
    else:
        checks["behaviour"] = compare(on_base.out, head.out, label="behaviour")
    print("3. isolation")
    for offset, run in twin_runs.items():
        checks[f"isolation {offset}"] = codes[offset] == 0 and compare(
            head.out, run.out, car="A", label=f"isolation (twin {offset} min)"
        )

    for name, passed in checks.items():
        print(f"  {'ok ' if passed else 'BAD'} {name}")
    green = all(checks.values())
    print("GREEN" if green else "RED")
    return 0 if green else 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_gate = sub.add_parser("gate", help="determinism, behaviour against a base, isolation")
    p_gate.add_argument("--base", default=None)
    p_gate.add_argument("--twin", action="append", default=None, help="twin offset in minutes")
    p_gate.add_argument("--keep", type=Path, default=None, help="where the traces go")
    p_record = sub.add_parser("record", help="trace the harness tests into a directory")
    p_record.add_argument("out", type=Path)
    p_record.add_argument("--twin", default=None)
    p_record.add_argument("pytest_args", nargs="*")
    p_compare = sub.add_parser("compare", help="compare two trace directories")
    p_compare.add_argument("a", type=Path)
    p_compare.add_argument("b", type=Path)
    p_compare.add_argument("--car", default=None, help="compare only this car's lines (A, B)")
    args = parser.parse_args(argv)

    if args.command == "record":
        return record(args.out, twin=args.twin, pytest_args=args.pytest_args or None)
    if args.command == "compare":
        return 0 if compare(args.a, args.b, car=args.car) else 1
    keep = args.keep or Path(tempfile.mkdtemp(prefix="golden-"))
    return gate(args.base or default_base(), args.twin or list(DEFAULT_TWINS), keep)


if __name__ == "__main__":
    sys.exit(main())
