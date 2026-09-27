"""Every full language carries the same hand-written text as English.

The flow sections of ``translations/<lang>.json`` (``config``, ``options``,
``services``, ``issues`` …) are not generated -- they are written by hand, once
per language. Home Assistant hides a gap in them well: a missing key falls back
to English without a word, and a string whose ``{placeholders}`` differ from
English is silently *dropped*, which reads to a user as a half-translated screen
with no way to tell why. So these tests hold every language to English's shape:
the same keys, and the same placeholders in each string.

That makes a new or reworded English string a failing test until every language
has it -- deliberately, since nothing else would notice.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

_PKG = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "bavariandata"
_TRANS = _PKG / "translations"

EN = json.loads((_TRANS / "en.json").read_text(encoding="utf-8"))
# A regional delta (en-GB) overlays its base language and has its own tests.
OTHERS = {
    path.stem: json.loads(path.read_text(encoding="utf-8"))
    for path in sorted(_TRANS.glob("*.json"))
    if "-" not in path.stem and path.stem != "en"
}

_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def _leaves(node: object, path: str = "") -> dict[str, str]:
    if isinstance(node, dict):
        out: dict[str, str] = {}
        for key, value in node.items():
            out.update(_leaves(value, f"{path}.{key}" if path else key))
        return out
    return {path: node} if isinstance(node, str) else {}


def _flow(doc: dict) -> dict[str, str]:
    return _leaves({k: v for k, v in doc.items() if k != "entity"})


EN_FLOW = _flow(EN)


def test_there_are_other_languages() -> None:
    assert len(OTHERS) >= 9


@pytest.mark.parametrize("lang", sorted(OTHERS))
def test_flow_text_has_exactly_the_english_keys(lang: str) -> None:
    theirs = _flow(OTHERS[lang])
    missing = sorted(set(EN_FLOW) - set(theirs))
    extra = sorted(set(theirs) - set(EN_FLOW))
    assert not missing and not extra, f"{lang}.json: missing {missing[:10]}, extra {extra[:10]}"


@pytest.mark.parametrize("lang", sorted(OTHERS))
def test_flow_text_keeps_every_placeholder(lang: str) -> None:
    theirs = _flow(OTHERS[lang])
    wrong = [
        key
        for key, text in EN_FLOW.items()
        if key in theirs
        and sorted(_PLACEHOLDER.findall(text)) != sorted(_PLACEHOLDER.findall(theirs[key]))
    ]
    assert not wrong, f"{lang}.json: placeholders differ from English at {wrong}"


@pytest.mark.parametrize("lang", sorted(OTHERS))
def test_flow_text_keeps_code_spans_and_fences(lang: str) -> None:
    """`PREFIX/VIN/soc`, `evcc.yaml` and the snippet fence must survive translation."""

    theirs = _flow(OTHERS[lang])
    wrong = [
        key
        for key, text in EN_FLOW.items()
        if key in theirs and text.count("`") != theirs[key].count("`")
    ]
    assert not wrong, f"{lang}.json: backtick count differs from English at {wrong}"


@pytest.mark.parametrize("lang", sorted(OTHERS))
def test_entity_block_names_every_english_entity(lang: str) -> None:
    ours = {k for k in _leaves(EN["entity"]) if k.endswith(".name")}
    theirs = {k for k in _leaves(OTHERS[lang]["entity"]) if k.endswith(".name")}
    assert ours == theirs, f"{lang}.json entity names differ: {sorted(ours ^ theirs)[:10]}"
