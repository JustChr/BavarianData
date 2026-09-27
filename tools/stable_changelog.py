#!/usr/bin/env python3
"""Draft the changelog entry for a stable release from the betas it promotes.

``scripts/release.sh --stable`` publishes ``## [Unreleased]`` as the release
notes. For a stable that section must be one consolidated entry covering
**everything since the last stable** -- every beta section in between, plus
anything still unreleased -- the way ``[0.9.13]`` reads:

* a preamble naming what it promotes (``Promotes X-beta.1 to beta.N.``) and a
  summary line to write by hand;
* ``### Breaking`` / ``Added`` / ``Changed`` / ``Fixed`` (then any other
  heading), each bullet tagged with the beta that shipped it: ``**Lead.** (beta.3)``.

What needs judgment is listed after the draft, not guessed:

* bullets whose bold lead repeats across betas -- intra-beta churn to fold into
  its final state;
* every ``Fixed`` bullet -- keep one only if the bug was in the last stable (a
  fix to something a beta introduced is churn, not news).

    python tools/stable_changelog.py            # print the draft and the notes
    python tools/stable_changelog.py --write    # put the draft into [Unreleased]

``--write`` splices on the ``## [Unreleased]`` line itself -- never on a ``###``
heading, which after a release also matches inside the section just published --
and never touches an already-released section. Edit the result before releasing.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"

VERSION_HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\](?: - (?P<date>\S+))?\s*$")
SUB_HEADING = re.compile(r"^### (?P<name>.+?)\s*$")
BETA = re.compile(r"^(?P<base>\d+\.\d+\.\d+)-beta\.(?P<n>\d+)$")
ORDER = ["Breaking", "Added", "Changed", "Fixed"]
TODO = "TODO: one or two sentences on what this stable brings, for a user."


def sections(text: str) -> list[tuple[str, list[str]]]:
    """``[(version, body_lines)]`` in file order, ``Unreleased`` included."""

    out: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        match = VERSION_HEADING.match(line)
        if match:
            out.append((match["version"], []))
        elif out:
            out[-1][1].append(line)
    return out


def bullets(body: list[str]) -> dict[str, list[str]]:
    """``{heading: [bullet text]}``; a bullet runs until the next one or heading."""

    grouped: dict[str, list[str]] = {}
    heading: Optional[str] = None
    current: Optional[list[str]] = None

    def flush() -> None:
        if heading and current:
            grouped.setdefault(heading, []).append("\n".join(current).rstrip())

    for line in body:
        sub = SUB_HEADING.match(line)
        if sub:
            flush()
            heading, current = sub["name"], None
        elif line.startswith("- "):
            flush()
            current = [line]
        elif current is not None and (line.startswith(" ") or not line.strip()):
            current.append(line)
        elif current is not None:
            flush()
            current = None
    flush()
    return grouped


def tag(bullet: str, label: Optional[str]) -> str:
    """Tag a bullet with its beta the way a stable entry reads: ``**Lead** (beta.3).``

    The lead's own full stop moves after the tag. ``label=None`` (still
    unreleased, so new in this stable) leaves the bullet as it is.
    """

    if label is None:
        return bullet
    match = re.match(r"- \*\*(.+?)\*\*", bullet, flags=re.S)
    if not match:
        return f"- ({label}) {bullet[2:]}"
    inner, rest = match[1], bullet[match.end() :]
    stop = ""
    if inner.endswith((".", "!", "?")):
        inner, stop = inner[:-1], inner[-1]
    elif rest.startswith((".", ":")):
        stop, rest = rest[0], rest[1:]
    return f"- **{inner}** ({label}){stop}{rest}"


def lead(bullet: str) -> Optional[str]:
    match = re.match(r"- \*\*(.+?)\*\*", bullet, flags=re.S)
    return " ".join(match[1].split()).rstrip(".").lower() if match else None


def draft(text: str) -> tuple[str, str, list[str]]:
    """``(version, section_body, notes)`` for the stable that the betas promote."""

    all_sections = sections(text)
    promoted: list[tuple[str, list[str]]] = []  # newest first, as in the file
    last_stable = None
    for version, body in all_sections:
        if version == "Unreleased":
            promoted.append((version, body))
        elif BETA.match(version):
            promoted.append((version, body))
        else:
            last_stable = version
            break
    betas = [v for v, _ in promoted if v != "Unreleased"]
    if not betas:
        raise SystemExit("No beta sections since the last stable -- nothing to promote.")
    bases = {BETA.match(v)["base"] for v in betas}
    if len(bases) != 1:
        raise SystemExit(f"Betas of more than one version since the last stable: {sorted(bases)}")
    base = bases.pop()
    numbers = sorted(int(BETA.match(v)["n"]) for v in betas)

    merged: dict[str, list[str]] = {}
    seen: dict[str, list[str]] = {}
    fixed: list[str] = []
    # Oldest first, so each heading reads in the order things shipped.
    for version, body in reversed(promoted):
        label = f"beta.{BETA.match(version)['n']}" if version != "Unreleased" else None
        for heading, items in bullets(body).items():
            for item in items:
                merged.setdefault(heading, []).append(tag(item, label))
                key = lead(item)
                if key:
                    seen.setdefault(key, []).append(label or "unreleased")
                if heading == "Fixed":
                    fixed.append(f"{label or 'unreleased'}: {lead(item) or item[:70]}")

    first, last = numbers[0], numbers[-1]
    span = f"{base}-beta.{first}" + (f" to beta.{last}" if last != first else "")
    parts = [f"Promotes {span}. {TODO}", ""]
    for heading in ORDER + sorted(set(merged) - set(ORDER)):
        if heading in merged:
            parts.append(f"### {heading}")
            parts.extend(merged[heading])
            parts.append("")

    notes = [
        f"Last stable: {last_stable or '(none found)'}; promoting {', '.join(reversed(betas))}"
        + (
            " and Unreleased"
            if promoted[0][0] == "Unreleased" and any(line.strip() for line in promoted[0][1])
            else ""
        )
        + "."
    ]
    repeats = {k: v for k, v in seen.items() if len(v) > 1}
    for key, labels in sorted(repeats.items()):
        notes.append(f"Repeats across {', '.join(labels)} -- fold into its final state: {key}")
    if fixed:
        notes.append(
            "Keep a Fixed bullet only if the bug was in the last stable "
            f"({last_stable}); drop fixes to things a beta introduced:"
        )
        notes.extend(f"  {line}" for line in fixed)
    notes.append(
        "Replace the TODO in the preamble; mark genuinely disruptive changes ### Breaking."
    )
    return base, "\n".join(parts).rstrip() + "\n", notes


def write(text: str, body: str) -> str:
    """Replace the body of ``## [Unreleased]`` -- splicing on that line only."""

    lines = text.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.startswith("## [Unreleased]"))
    end = next(
        (i for i in range(start + 1, len(lines)) if VERSION_HEADING.match(lines[i].rstrip("\n"))),
        len(lines),
    )
    return "".join(lines[: start + 1]) + "\n" + body + "\n" + "".join(lines[end:])


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true", help="put the draft into [Unreleased]")
    parser.add_argument("--changelog", type=Path, default=CHANGELOG, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    text = args.changelog.read_text(encoding="utf-8")
    version, body, notes = draft(text)
    if args.write:
        args.changelog.write_text(write(text, body), encoding="utf-8")
        print(f"Drafted the {version} entry into ## [Unreleased] of {args.changelog.name}.")
    else:
        print(f"## [Unreleased]   (draft for {version})\n\n{body}")
    print("Before releasing:\n" + "\n".join(f"- {note}" for note in notes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
