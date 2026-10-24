#!/usr/bin/env python3
"""Per-module coverage, against the Quality Scale's bar of 95 % per module.

Silver's ``test-coverage`` rule counts every module on its own, not the
total, so this lists each module of the integration with what the HA-free
suite (``tests/``) and the real-Home-Assistant suite (``tests_ha/``) cover
between them, worst first:

    coverage combine .coverage.unit .coverage.ha
    coverage json -o coverage.json
    python scripts/coverage_modules.py coverage.json            # report
    python scripts/coverage_modules.py coverage.json --gate     # fail below the bar

Generated modules (``descriptor_metadata.py``) are data, not code, and are
left out. Exit status: 0, or 1 with ``--gate`` when a module is below the bar.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BAR = 95.0
GENERATED = {"descriptor_metadata.py"}


def modules(report: dict) -> list[tuple[str, float, int, int]]:
    rows = []
    for path, data in report["files"].items():
        name = path.replace("\\", "/").split("custom_components/bavariandata/", 1)[-1]
        if Path(name).name in GENERATED:
            continue
        summary = data["summary"]
        rows.append(
            (
                name,
                summary["percent_covered"],
                summary["missing_lines"],
                summary["num_statements"],
            )
        )
    return sorted(rows, key=lambda row: (row[1], -row[3]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report", type=Path, help="the output of `coverage json`")
    parser.add_argument("--gate", action="store_true", help=f"exit 1 below {BAR:g} %%")
    args = parser.parse_args(argv)

    rows = modules(json.loads(args.report.read_text(encoding="utf-8")))
    below = [row for row in rows if row[1] < BAR]
    print(f"{'module':<40} {'cover':>6} {'missed':>7} {'lines':>6}")
    for name, percent, missed, total in rows:
        flag = "  <" if percent < BAR else ""
        print(f"{name:<40} {percent:5.1f}% {missed:7d} {total:6d}{flag}")
    print(f"\n{len(below)} of {len(rows)} modules below {BAR:g} %")
    return 1 if args.gate and below else 0


if __name__ == "__main__":
    sys.exit(main())
