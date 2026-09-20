"""The card writes its own figures in the user's number format.

Home Assistant formats entity states itself (``formatEntityState``), honouring
the profile's language and *Number format* setting. The card's own figures --
consumption, charged energy, capacity, distances from the history services --
were interpolated as bare JavaScript numbers, which always print a point. On
the maintainer's German dashboard (2026-09-13) the Energy tab read "19.8" and
"0.2 kWh" beside Home Assistant's "110,10 kWh".

SVG coordinates are the opposite case: they must stay dot-decimal in every
locale, or ``points="18,4 20,1"`` silently draws a different chart.

Runs the shipped card under Node, like ``test_card_soc_pick.py``; skipped where
Node is not installed. The last test is a text guard and always runs.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CARD = _ROOT / "custom_components" / "bavariandata" / "www" / "bavariandata-card.js"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

# Loads the card with just enough DOM stubbed to define its classes, then calls
# the named methods on a bare instance.
_HARNESS = r"""
const fs = require("fs");
globalThis.HTMLElement = class {};
globalThis.customElements = { define() {}, get() {}, whenDefined() {} };
globalThis.window = globalThis;
console.info = () => {};
const src = fs.readFileSync(process.argv[1], "utf8");
const Card = new Function(src + "\nreturn BavarianDataCard;")();
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const card = Object.create(Card.prototype);
card._config = {};
card._hass = { states: {}, language: input.language, locale: input.locale };
const out = {};
for (const [key, [method, ...args]] of Object.entries(input.calls)) {
  out[key] = card[method](...args);
}
process.stdout.write(JSON.stringify(out));
"""


def _run(calls: dict, *, language: str = "de", number_format: str = "language") -> dict:
    payload = {
        "language": language,
        "locale": {"language": language, "number_format": number_format},
        "calls": calls,
    }
    result = subprocess.run(
        [NODE, "-e", _HARNESS, str(_CARD)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(result.stdout)


@needs_node
def test_a_german_dashboard_gets_decimal_commas():
    out = _run(
        {
            "consumption": ["_dec", 19.84, 1],
            "session": ["_dec", 0.165, 1],
            "odometer": ["_km", 12345],
        }
    )
    assert out == {"consumption": "19,8", "session": "0,2", "odometer": "12.345 km"}


@needs_node
def test_an_english_dashboard_keeps_decimal_points():
    out = _run({"consumption": ["_dec", 19.84, 1], "odometer": ["_km", 12345]}, language="en")
    assert out == {"consumption": "19.8", "odometer": "12,345 km"}


@needs_node
def test_the_number_format_setting_outranks_the_language():
    assert _run({"v": ["_dec", 19.84, 1]}, language="de", number_format="comma_decimal") == {
        "v": "19.8"
    }
    assert _run({"v": ["_dec", 19.84, 1]}, language="en", number_format="decimal_comma") == {
        "v": "19,8"
    }
    assert _run({"v": ["_dec", 12345.67, 1]}, number_format="none") == {"v": "12345.7"}


@needs_node
def test_rounding_edge_cases():
    out = _run(
        {"neg_zero": ["_dec", -0.04, 1], "junk": ["_dec", "n/a", 1], "whole": ["_dec", 77, 1]}
    )
    assert out == {"neg_zero": "0", "junk": "—", "whole": "77"}


@needs_node
def test_efficiency_view_text_is_localised_but_its_chart_coordinates_are_not():
    figures = {
        "nowKm": 379,
        "soc": 85,
        "consumption": 19.84,
        "side": "battery",
        "window": 30,
        "capacity": 77,
        "capacitySource": "bmw",
    }
    trend = [
        {"month": "2026-08", "kwh_per_100km": 18.25},
        {"month": "2026-09", "kwh_per_100km": 19.84},
    ]
    html = _run({"html": ["_efBody", figures, trend, {}]})["html"]
    assert "19,8 kWh/100 km" in html
    assert "19.8" not in html
    # A bar whose top is not on a whole pixel, written the only way SVG reads it.
    assert re.search(r'<rect class="ef__bar" [^>]*y="\d+\.\d"', html), html


def _method_body(source: str, name: str) -> tuple[int, int]:
    match = re.search(rf"^  {re.escape(name)}\(", source, re.MULTILINE)
    assert match, f"card: {name}() not found -- update this guard"
    end = re.search(r"\n  [A-Za-z_$][\w$]*\(", source[match.end() :])
    return match.start(), match.end() + (end.start() if end else len(source))


def test_raw_rounding_never_reaches_the_page():
    # `_round` returns a number, and a number interpolated into a template prints
    # a point in every locale. Display text goes through `_dec`, SVG coordinates
    # through `_px`; nothing else may call `_round`.
    source = _CARD.read_text(encoding="utf-8")
    allowed = [_method_body(source, "_dec"), _method_body(source, "_px")]
    stray = [
        source.count("\n", 0, m.start()) + 1
        for m in re.finditer(r"this\._round\(", source)
        if not any(start <= m.start() < end for start, end in allowed)
    ]
    assert not stray, (
        f"bavariandata-card.js calls _round() directly on line(s) {stray}: use "
        "_dec() for text shown to the user, _px() for an SVG coordinate"
    )
