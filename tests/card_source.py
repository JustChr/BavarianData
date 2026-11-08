"""The shipped card as one script Node can evaluate, for the card tests.

The card is an ES module that imports its strings from one
``bavariandata-card-strings-<lang>.js`` per language and its class's parts from
``bavariandata-card-<part>.js`` (with top-level ``await`` and ``import.meta``).
The tests evaluate it the way they always have, with
``new Function(src + "return BavarianDataCard;")``, which takes a plain script.
So :func:`evaluable_card` writes the files out as one: every language's table
into one ``TRANSLATIONS``, each part as a named factory, then the card with its
import blocks swapped for those names, leaving ``TRANSLATIONS`` and the card's
classes at the top level.

Tests that read the card's text rather than run it use :func:`card_text`, every
file joined, and :func:`where` to name the file and line a match sits on.
"""

from __future__ import annotations

import functools
import re
import tempfile
from pathlib import Path

WWW = Path(__file__).resolve().parents[1] / "custom_components" / "bavariandata" / "www"
CARD = WWW / "bavariandata-card.js"

_IMPORT_BLOCK = re.compile(
    r"^// translations-import-start\b.*?^// translations-import-end\n", re.MULTILINE | re.DOTALL
)
_EXPORT = "export default {"
_LANGUAGES = re.compile(r"^const STRING_LANGUAGES = \[([^\]]*)\];", re.MULTILINE)
_PARTS_BLOCK = re.compile(
    r"^// parts-import-start\b.*?^// parts-import-end\n", re.MULTILINE | re.DOTALL
)
_PARTS_LIST = re.compile(r"^const CARD_PARTS = \[([^\]]*)\];", re.MULTILINE)
_PART_EXPORT = "export default ("


def card_parts() -> list[str]:
    """The parts the card loads, in its own order (``CARD_PARTS``)."""

    match = _PARTS_LIST.search(CARD.read_text(encoding="utf-8"))
    assert match, f"{CARD.name} lost its CARD_PARTS list"
    return re.findall(r'"([a-z]+)"', match.group(1))


def string_languages() -> list[str]:
    """The languages the card has a strings file for (``STRING_LANGUAGES``)."""

    match = _LANGUAGES.search(CARD.read_text(encoding="utf-8"))
    assert match, f"{CARD.name} lost its STRING_LANGUAGES list"
    return re.findall(r'"([A-Za-z-]+)"', match.group(1))


def strings_file(lang: str) -> Path:
    return WWW / f"bavariandata-card-strings-{lang}.js"


def part_file(part: str) -> Path:
    return WWW / f"bavariandata-card-{part}.js"


def card_files() -> list[Path]:
    """The card's own code: the card, then its parts (not the strings)."""

    return [CARD, *(part_file(part) for part in card_parts())]


@functools.cache
def _joined() -> tuple[str, tuple[tuple[int, str], ...]]:
    texts: list[str] = []
    starts: list[tuple[int, str]] = []
    line = 1
    for path in card_files():
        text = path.read_text(encoding="utf-8")
        if not text.endswith("\n"):
            text += "\n"
        texts.append(text)
        starts.append((line, path.name))
        line += text.count("\n")
    return "".join(texts), tuple(starts)


def card_text() -> str:
    """Every file of the card's code, joined: what a text check reads."""

    return _joined()[0]


def where(line: int) -> str:
    """``file:line`` for a line number in :func:`card_text`."""

    first, name = max(start for start in _joined()[1] if start[0] <= line)
    return f"{name}:{line - first + 1}"


def translations_script() -> str:
    """Every language's strings as one plain script that declares ``TRANSLATIONS``."""

    script = "const TRANSLATIONS = {};\n"
    for lang in string_languages():
        path = strings_file(lang)
        text = path.read_text(encoding="utf-8")
        assert text.count(_EXPORT) == 1, f"{path.name} no longer opens with {_EXPORT!r}"
        script += text.replace(_EXPORT, f"TRANSLATIONS[{lang!r}] = {{")
    return script


@functools.cache
def translations_file() -> Path:
    """A file holding just :func:`translations_script`, for tests that read the tables."""

    path = Path(tempfile.mkdtemp(prefix="bavariandata-strings-")) / "strings.js"
    path.write_text(translations_script(), encoding="utf-8")
    return path


def _factory(part: str) -> str:
    return f"__bavariandataPart_{part}"


def part_script(part: str, text: str) -> str:
    """A part as a plain script that declares its factory under its own name."""

    name = part_file(part).name
    assert text.count(_PART_EXPORT) == 1, f"{name} no longer opens with {_PART_EXPORT!r}"
    return text.replace(_PART_EXPORT, f"const {_factory(part)} = (")


def card_script(text: str) -> str:
    """The card with its import blocks swapped for what they would load."""

    # Every table is already in TRANSLATIONS, so there is never one to fetch.
    script, cut = _IMPORT_BLOCK.subn("function _ensureStrings() {\n  return null;\n}\n", text)
    assert cut == 1, f"{CARD.name} lost its translations-import-start/-end markers"
    factories = ", ".join(_factory(part) for part in card_parts())
    script, cut = _PARTS_BLOCK.subn(f"const PART_FACTORIES = [{factories}];\n", script)
    assert cut == 1, f"{CARD.name} lost its parts-import-start/-end markers"
    return script


@functools.cache
def evaluable_card() -> Path:
    """A file holding the strings, the parts, then the card, as one evaluable script."""

    parts = "".join(
        part_script(part, part_file(part).read_text(encoding="utf-8")) for part in card_parts()
    )
    source = translations_script() + parts + card_script(CARD.read_text(encoding="utf-8"))
    path = Path(tempfile.mkdtemp(prefix="bavariandata-card-")) / CARD.name
    path.write_text(source, encoding="utf-8")
    return path
