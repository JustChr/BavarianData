#!/usr/bin/env python3
"""Generate ``translations/en-GB.json`` as a delta over ``en.json``.

``en.json`` is written in US English. British English differs from it in a few
dozen words, so a second full translation file would be 1300 strings duplicated
to change 50 — and the duplicates would rot the moment a name changed, silently,
because nothing regenerates them.

Home Assistant makes the duplication unnecessary. ``helpers/translation.py``
loads ``["en", language]`` and overlays the requested language on top of the
English **key by key**, so a file holding only the strings that differ resolves
correctly and inherits everything else. The card's ``t()`` falls back the same
way (``TRANSLATIONS.en[key]`` when the active table has no entry), which is why
its ``en-GB`` table is a handful of keys rather than a copy.

So this writes only the leaves that ``tools/spelling_en_gb.json`` changes. Run it
after any edit to ``en.json`` — it is step 5 of the pipeline in ``tools/README.md``
and part of the ``regen`` skill. ``tests/test_translations_dialect.py`` fails if
the file on disk is not what this script would write.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TRANS_DIR = REPO_ROOT / "custom_components" / "bavariandata" / "translations"
SPELLING_FILE = Path(__file__).resolve().parent / "spelling_en_gb.json"

SOURCE = "en.json"
TARGET = "en-GB.json"


def load_words() -> dict[str, str]:
    """The curated US -> UK word list (the ``_`` keys are documentation)."""

    doc = json.loads(SPELLING_FILE.read_text(encoding="utf-8"))
    return doc["words"]


def load_never_translate() -> set[str]:
    """Leaf paths whose value is data the user types, not prose to read."""

    doc = json.loads(SPELLING_FILE.read_text(encoding="utf-8"))
    return set(doc["_never_translate"])


def _match_case(source: str, replacement: str) -> str:
    # Preserve the case the author wrote: "Tire" -> "Tyre", "TIRE" -> "TYRE",
    # "tire" -> "tyre". Anything else (a stray "tIre") keeps the replacement as
    # the word list spells it rather than guessing.
    if source.isupper():
        return replacement.upper()
    if source[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


# Text that is an identifier rather than a word, even mid-sentence: a
# ``{placeholder}`` Home Assistant substitutes by name (rename it and HA drops
# the whole string as a placeholder mismatch, silently) and a ``code span``,
# which is how a slug or a descriptor is written into a description.
_VERBATIM = re.compile(r"\{[^{}]*\}|`[^`]*`")


def britishise(text: str, words: dict[str, str]) -> str:
    """Rewrite `text` into British English, whole words only, case preserved."""

    def swap(match: re.Match[str]) -> str:
        word = match.group(0)
        replacement = words.get(word.lower())
        return _match_case(word, replacement) if replacement else word

    # ``\b`` alone would fire inside "tire_status"; ``_`` is a word character to
    # Python's ``\b``, so the lookarounds exclude it explicitly. Translation keys
    # are never touched anyway (only values are walked), but a slug quoted inside
    # a description would be.
    pattern = re.compile(
        r"(?<![A-Za-z_])(" + "|".join(sorted(words, key=len, reverse=True)) + r")(?![A-Za-z_])",
        re.IGNORECASE,
    )
    # Rewrite only the stretches between the verbatim spans, so a placeholder or
    # a code span passes through untouched however it is spelled.
    out: list[str] = []
    cursor = 0
    for verbatim in _VERBATIM.finditer(text):
        out.append(pattern.sub(swap, text[cursor : verbatim.start()]))
        out.append(verbatim.group(0))
        cursor = verbatim.end()
    out.append(pattern.sub(swap, text[cursor:]))
    return "".join(out)


def delta(
    node: object,
    words: dict[str, str],
    never: frozenset[str] | set[str] = frozenset(),
    path: str = "",
) -> object | None:
    """The sub-tree of `node` whose strings differ in British English.

    Returns ``None`` for a branch that changes nothing, so empty dicts never
    reach the output — a delta file should show at a glance what it overrides.
    """

    if isinstance(node, str):
        if path in never:
            return None
        british = britishise(node, words)
        return british if british != node else None
    if isinstance(node, dict):
        changed = {}
        for key, value in node.items():
            sub = delta(value, words, never, f"{path}.{key}")
            if sub is not None:
                changed[key] = sub
        return changed or None
    # Lists and scalars carry no translatable prose in these files; a list would
    # have no way to express "only this element differs" in an overlay anyway.
    return None


def main() -> None:
    words = load_words()
    never = load_never_translate()
    english = json.loads((TRANS_DIR / SOURCE).read_text(encoding="utf-8"))
    british = delta(english, words, never) or {}
    path = TRANS_DIR / TARGET
    path.write_text(json.dumps(british, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    leaves = len(re.findall(r'": "', json.dumps(british)))
    print(f"Wrote {leaves} overridden strings to {path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
