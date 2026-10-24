"""Every error a user reads is translated, and English says what the code says."""

from __future__ import annotations

import ast
import json
import pathlib
import re

import pytest

from .conftest import load_module

refusal = load_module("refusal")

_PKG = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "bavariandata"
# Home Assistant's errors a user sees, by the name they are raised under.
_HA_ERRORS = {
    "HomeAssistantError",
    "ServiceValidationError",
    "ConfigEntryNotReady",
    "ConfigEntryAuthFailed",
    "ConfigEntryError",
}


def test_english_is_the_codes_text_word_for_word() -> None:
    english = json.loads((_PKG / "translations" / "en.json").read_text(encoding="utf-8"))
    assert {key: entry["message"] for key, entry in english["exceptions"].items()} == (
        refusal.MESSAGES
    )


def test_a_refusal_reads_as_its_english_text_with_string_placeholders() -> None:
    err = refusal.Refusal("pick_car", count=2, cars="i5, iX1")
    assert str(err) == ("This account has 2 cars (i5, iX1); pick one with device_id, or pass vin")
    assert err.key == "pick_car"
    assert err.placeholders == {"count": "2", "cars": "i5, iX1"}


def test_a_placeholder_may_be_called_key() -> None:
    err = refusal.Refusal("bad_date", key="'from'", value="'soon'")
    assert str(err) == "Could not read 'from' as a date or date/time: 'soon'"


def test_an_unknown_key_is_a_bug_not_a_message() -> None:
    with pytest.raises(KeyError):
        refusal.Refusal("no_such_key")


def test_home_assistants_error_gets_the_key_and_placeholders() -> None:
    class _Error(Exception):
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    err = refusal.as_ha_error(refusal.Refusal("no_token", service="x"), _Error, "bavariandata")
    assert err.kwargs == {
        "translation_domain": "bavariandata",
        "translation_key": "no_token",
        "translation_placeholders": {"service": "x"},
    }


def _untranslated_raises(path: pathlib.Path) -> list[str]:
    """``raise SomeHAError("text")``: an error raised with a message, not a key."""

    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
            continue
        func = node.exc.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        keywords = {keyword.arg for keyword in node.exc.keywords}
        if name in _HA_ERRORS and "translation_key" not in keywords:
            found.append(f"{path.name}:{node.lineno}")
    return found


def test_no_home_assistant_error_is_raised_in_english_only() -> None:
    found = [hit for path in sorted(_PKG.rglob("*.py")) for hit in _untranslated_raises(path)]
    assert not found, f"raise these through refusal.as_ha_error: {found}"


def _strings(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _strings(value, f"{path}.{key}" if path else key)
    elif isinstance(node, str):
        yield path, node


def test_no_placeholder_sits_inside_single_quotes() -> None:
    """Hassfest refuses ``'{name}'`` in a translation; quote in the value instead."""

    quoted = re.compile(r"'\{[^}]+\}'")
    found = [
        f"{path.name}: {where}"
        for path in sorted((_PKG / "translations").glob("*.json"))
        for where, text in _strings(json.loads(path.read_text(encoding="utf-8")))
        if quoted.search(text)
    ]
    assert not found, found
