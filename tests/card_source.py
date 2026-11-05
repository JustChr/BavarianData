"""The shipped card as one script Node can evaluate, for the card tests.

The card is an ES module that imports its strings from
``bavariandata-card-translations.js`` and its class's parts from
``bavariandata-card-<part>.js`` (with top-level ``await`` and ``import.meta``).
The tests evaluate it the way they always have, with
``new Function(src + "return BavarianDataCard;")``, which takes a plain script.
So :func:`evaluable_card` writes the files out as one: the strings with their
``export`` dropped, each part as a named factory, then the card with its import
blocks swapped for those names, leaving ``TRANSLATIONS`` and the card's classes
at the top level.

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
CARD_TRANSLATIONS = WWW / "bavariandata-card-translations.js"

_IMPORT_BLOCK = re.compile(
    r"^// translations-import-start\b.*?^// translations-import-end\n", re.MULTILINE | re.DOTALL
)
_EXPORT = "export const TRANSLATIONS = {"
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


def translations_script(text: str) -> str:
    """The strings module as a plain script that declares ``TRANSLATIONS``."""

    assert text.count(_EXPORT) == 1, f"{CARD_TRANSLATIONS.name} no longer opens with {_EXPORT!r}"
    return text.replace(_EXPORT, "const TRANSLATIONS = {")


def _factory(part: str) -> str:
    return f"__bavariandataPart_{part}"


def part_script(part: str, text: str) -> str:
    """A part as a plain script that declares its factory under its own name."""

    name = part_file(part).name
    assert text.count(_PART_EXPORT) == 1, f"{name} no longer opens with {_PART_EXPORT!r}"
    return text.replace(_PART_EXPORT, f"const {_factory(part)} = (")


def card_script(text: str) -> str:
    """The card with its import blocks swapped for what they would load."""

    script, cut = _IMPORT_BLOCK.subn("", text)
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
    source = (
        translations_script(CARD_TRANSLATIONS.read_text(encoding="utf-8"))
        + parts
        + card_script(CARD.read_text(encoding="utf-8"))
    )
    path = Path(tempfile.mkdtemp(prefix="bavariandata-card-")) / CARD.name
    path.write_text(source, encoding="utf-8")
    return path
