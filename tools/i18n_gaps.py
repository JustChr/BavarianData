#!/usr/bin/env python3
"""One to-do list of every translation that is missing, broken or stale.

User-facing text lives in four places, each with its own table per language:

* ``translations/<lang>.json`` -- the hand-maintained sections (everything but
  the generated ``entity`` block, which the pipeline fills from BMW's catalogues);
* ``TRANSLATIONS`` in ``www/bavariandata-card.js`` -- the dashboard card;
* ``STRINGS`` in ``history/export.py`` -- the printable month report;
* the ``language`` options of ``export_history`` in ``services.yaml``.

The parity tests fail on the first gap they meet, one test at a time. This lists
all of them at once, grouped by language, so a change to English text can be
carried into every language in one pass:

* **missing** -- English has the key, the language does not;
* **extra** -- the language has a key English no longer has;
* **placeholders** / **code spans** -- ``{vars}`` or backtick count differ, which
  Home Assistant answers by silently dropping the string;
* **stale** -- the English string changed since ``--since`` (default: where this
  branch left ``origin/main``, or ``HEAD`` on ``main``) but the translation did
  not, so it still says what the old English said.

    python tools/i18n_gaps.py              # human-readable worklist
    python tools/i18n_gaps.py --json       # the same, for a script
    python tools/i18n_gaps.py --since v0.9.13
    python tools/i18n_gaps.py --no-stale   # structure only (no git needed)

Exits 1 when anything is listed. Regional deltas (``en-GB``) are derived by
``generate_en_gb.py`` and are not checked here.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

import yaml

REPO = Path(__file__).resolve().parents[1]
PKG_REL = "custom_components/bavariandata"
TRANS_REL = f"{PKG_REL}/translations"
CARD_REL = f"{PKG_REL}/www/bavariandata-card.js"
EXPORT_REL = f"{PKG_REL}/history/export.py"
SERVICES_REL = f"{PKG_REL}/services.yaml"

PLACEHOLDER = re.compile(r"\{[a-z_]+\}")

# Evaluates the card's TRANSLATIONS table without a browser, the way the tests do.
_CARD_DUMP = r"""
const fs = require("fs");
globalThis.HTMLElement = class {};
globalThis.customElements = { define() {}, get() {}, whenDefined() {} };
globalThis.window = globalThis;
console.info = () => {};
const src = fs.readFileSync(process.argv[1], "utf8");
process.stdout.write(JSON.stringify(new Function(src + "\nreturn TRANSLATIONS;")()));
"""

Tables = dict[str, dict[str, str]]  # lang -> key -> text


# --- reading a source at the working tree or at a git ref -------------------


def _git(*args: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8"
        )
    except OSError:
        return None
    return out.stdout if out.returncode == 0 else None


def _read(rel: str, ref: Optional[str]) -> Optional[str]:
    """A file's text in the working tree (``ref=None``) or at a git ref."""

    if ref is None:
        path = REPO / rel
        return path.read_text(encoding="utf-8") if path.exists() else None
    return _git("show", f"{ref}:{rel}")


def _leaves(node: object, path: str = "") -> dict[str, str]:
    if isinstance(node, dict):
        out: dict[str, str] = {}
        for key, value in node.items():
            out.update(_leaves(value, f"{path}.{key}" if path else key))
        return out
    return {path: node} if isinstance(node, str) else {}


def ha_tables(ref: Optional[str]) -> Tables:
    """Flow text of every full language in ``translations/`` (no ``entity``)."""

    if ref is None:
        names = [p.name for p in (REPO / TRANS_REL).glob("*.json")]
    else:
        listing = _git("ls-tree", "--name-only", f"{ref}:{TRANS_REL}") or ""
        names = [n for n in listing.split() if n.endswith(".json")]
    tables: Tables = {}
    for name in sorted(names):
        lang = name[: -len(".json")]
        if "-" in lang:  # a regional delta, derived by generate_en_gb.py
            continue
        text = _read(f"{TRANS_REL}/{name}", ref)
        if text is None:
            continue
        doc = json.loads(text)
        tables[lang] = _leaves({k: v for k, v in doc.items() if k != "entity"})
    return tables


def card_tables(ref: Optional[str]) -> Tables:
    node = shutil.which("node")
    text = _read(CARD_REL, ref)
    if node is None or text is None:
        return {}
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(text)
        tmp = f.name
    try:
        out = subprocess.run(
            [node, "-e", _CARD_DUMP, tmp], capture_output=True, text=True, encoding="utf-8"
        )
    finally:
        Path(tmp).unlink(missing_ok=True)
    if out.returncode != 0:
        raise SystemExit(f"could not evaluate the card's TRANSLATIONS:\n{out.stderr}")
    raw = json.loads(out.stdout)
    return {lang: {k: str(v) for k, v in t.items()} for lang, t in raw.items() if "-" not in lang}


def report_tables(ref: Optional[str]) -> Tables:
    """``STRINGS`` from export.py, read with ``ast`` (the module is not importable alone)."""

    text = _read(EXPORT_REL, ref)
    if text is None:
        return {}
    for node in ast.parse(text).body:
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == "STRINGS" and node.value is not None:
            return ast.literal_eval(node.value)
    return {}


SOURCES: dict[str, tuple[str, Callable[[Optional[str]], Tables]]] = {
    "translations": (TRANS_REL + "/<lang>.json", ha_tables),
    "card": (CARD_REL, card_tables),
    "report": (EXPORT_REL, report_tables),
}


# --- the checks -------------------------------------------------------------


def structural_gaps(tables: Tables) -> list[dict]:
    en = tables.get("en", {})
    gaps: list[dict] = []
    for lang, table in sorted(tables.items()):
        if lang == "en":
            continue
        for key in sorted(set(en) - set(table)):
            gaps.append({"lang": lang, "key": key, "problem": "missing", "en": en[key]})
        for key in sorted(set(table) - set(en)):
            gaps.append({"lang": lang, "key": key, "problem": "extra", "en": ""})
        for key in sorted(set(en) & set(table)):
            if sorted(PLACEHOLDER.findall(en[key])) != sorted(PLACEHOLDER.findall(table[key])):
                gaps.append({"lang": lang, "key": key, "problem": "placeholders", "en": en[key]})
            elif en[key].count("`") != table[key].count("`"):
                gaps.append({"lang": lang, "key": key, "problem": "code spans", "en": en[key]})
    return gaps


def stale_gaps(now: Tables, then: Tables) -> list[dict]:
    en_now, en_then = now.get("en", {}), then.get("en", {})
    changed = {k for k in en_now if k in en_then and en_now[k] != en_then[k]}
    gaps: list[dict] = []
    for lang, table in sorted(now.items()):
        if lang == "en":
            continue
        old = then.get(lang, {})
        for key in sorted(changed):
            if key in table and key in old and table[key] == old[key]:
                gaps.append(
                    {
                        "lang": lang,
                        "key": key,
                        "problem": "stale",
                        "en": en_now[key],
                        "en_before": en_then[key],
                    }
                )
    return gaps


def selector_gaps(report: Tables) -> list[dict]:
    """The export service must offer exactly the report's languages."""

    text = _read(SERVICES_REL, None)
    if text is None or not report:
        return []
    field = yaml.safe_load(text)["export_history"]["fields"]["language"]
    offered = set(field["selector"]["select"]["options"])
    gaps = [
        {"lang": lang, "key": "export_history.language", "problem": "not offered", "en": ""}
        for lang in sorted(set(report) - offered)
    ]
    gaps += [
        {"lang": lang, "key": "export_history.language", "problem": "offered, no report", "en": ""}
        for lang in sorted(offered - set(report))
    ]
    return gaps


def default_since() -> Optional[str]:
    """Where this branch left origin/main; on main itself, the last commit."""

    base = _git("merge-base", "HEAD", "origin/main")
    return base.strip() if base else ("HEAD" if _git("rev-parse", "HEAD") else None)


def collect(since: Optional[str], stale: bool) -> list[dict]:
    gaps: list[dict] = []
    language_sets: dict[str, set[str]] = {}
    for source, (_path, read) in SOURCES.items():
        now = read(None)
        if not now:
            continue
        language_sets[source] = set(now)
        found = structural_gaps(now)
        if stale and since:
            found += stale_gaps(now, read(since))
        if source == "report":
            found += selector_gaps(now)
        for gap in found:
            gap["source"] = source
        gaps += found
    # Every source must ship the same languages, or a user gets a half-translated UI.
    union = set().union(*language_sets.values()) if language_sets else set()
    for source, langs in language_sets.items():
        for lang in sorted(union - langs):
            gaps.append(
                {"source": source, "lang": lang, "key": "*", "problem": "no table", "en": ""}
            )
    return gaps


def _short(text: str, width: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def render(gaps: list[dict], since: Optional[str]) -> str:
    if not gaps:
        return "No translation gaps." + (f" (stale checked against {since})" if since else "")
    lines = [
        f"{len(gaps)} translation gap(s)" + (f"; stale = unchanged since {since}" if since else "")
    ]
    by_lang: dict[str, list[dict]] = {}
    for gap in gaps:
        by_lang.setdefault(gap["lang"], []).append(gap)
    for lang in sorted(by_lang):
        lines.append(f"\n## {lang}  ({len(by_lang[lang])})")
        for gap in sorted(by_lang[lang], key=lambda g: (g["source"], g["key"], g["problem"])):
            line = f"  [{gap['source']}] {gap['key']}: {gap['problem']}"
            if gap["en"]:
                line += f"\n      en: {_short(gap['en'])}"
            if gap.get("en_before"):
                line += f"\n     was: {_short(gap['en_before'])}"
            lines.append(line)
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--since", help="git ref the stale check compares against")
    parser.add_argument("--no-stale", action="store_true", help="skip the git-based stale check")
    parser.add_argument("--json", action="store_true", help="print the gaps as JSON")
    args = parser.parse_args(argv)

    since = None if args.no_stale else (args.since or default_since())
    gaps = collect(since, stale=not args.no_stale)
    if args.json:
        print(json.dumps(gaps, indent=2, ensure_ascii=False))
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(render(gaps, since))
    return 1 if gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
