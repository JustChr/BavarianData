"""The shipped card as one script Node can evaluate, for the card tests.

The card is an ES module that imports its strings from
``bavariandata-card-translations.js`` (with top-level ``await`` and
``import.meta``). The tests evaluate it the way they always have, with
``new Function(src + "return BavarianDataCard;")``, which takes a plain script.
So :func:`evaluable_card` writes the two files out as one: the strings with their
``export`` dropped, then the card with its import block cut out, leaving both
``TRANSLATIONS`` and the card's classes at the top level.
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


def translations_script(text: str) -> str:
    """The strings module as a plain script that declares ``TRANSLATIONS``."""

    assert text.count(_EXPORT) == 1, f"{CARD_TRANSLATIONS.name} no longer opens with {_EXPORT!r}"
    return text.replace(_EXPORT, "const TRANSLATIONS = {")


def card_script(text: str) -> str:
    """The card without the block that imports its strings."""

    script, cut = _IMPORT_BLOCK.subn("", text)
    assert cut == 1, f"{CARD.name} lost its translations-import-start/-end markers"
    return script


@functools.cache
def evaluable_card() -> Path:
    """A file holding the strings, then the card, as one evaluable script."""

    source = translations_script(CARD_TRANSLATIONS.read_text(encoding="utf-8")) + card_script(
        CARD.read_text(encoding="utf-8")
    )
    path = Path(tempfile.mkdtemp(prefix="bavariandata-card-")) / CARD.name
    path.write_text(source, encoding="utf-8")
    return path
