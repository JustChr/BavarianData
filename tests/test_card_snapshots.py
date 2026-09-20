"""Golden renders of every card view, for each drivetrain.

The card is the part of this integration users actually look at, and it is the
part nothing could review: a layout change is a diff in a 5,600-line template
string, and the only way to see what it did was to open Home Assistant and
squint. So every view is rendered here under Node and pinned to a snapshot,
which turns "did this move something" into a readable diff in the change
itself.

These are deliberately *not* assertions about correctness -- the behavioural
card tests next door already own that. A snapshot's job is to make an
unintended change impossible to miss, and an intended one easy to approve
(``pytest --snapshot-update``).

Two things are pinned so a re-run cannot drift:

* **The clock.** Three places in the card render relative times from
  ``Date.now()``; without freezing it the snapshots would rot daily.
* **The language.** Rendering is forced to English, as the docs screenshots are.

The drivetrain fixtures come from ``test_card_drivetrain.py`` rather than being
copied, so an electric/petrol/hybrid car means the same thing in both files.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

from .test_card_drivetrain import I5, M2, PHEV

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CARD = _ROOT / "custom_components" / "bavariandata" / "www" / "bavariandata-card.js"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

# Renders through the card's own dispatch (``_render``) rather than calling a
# view method directly, so the view-vs-cluster routing is covered too. The
# device lookup is stubbed because it needs a real Home Assistant registry.
_HARNESS = r"""
const fs = require("fs");
const FIXED = Date.parse("2026-09-09T12:00:00Z");
const RealDate = Date;
class FrozenDate extends RealDate {
  constructor(...a) { if (a.length === 0) super(FIXED); else super(...a); }
  static now() { return FIXED; }
}
globalThis.Date = FrozenDate;
globalThis.HTMLElement = class {};
globalThis.customElements = { define() {}, get() {}, whenDefined() {} };
globalThis.window = globalThis;
console.info = () => {};
const src = fs.readFileSync(process.argv[1], "utf8");
const Card = new Function(src + "\nreturn BavarianDataCard;")();
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const card = Object.create(Card.prototype);
card._config = input.config;
card._hass = {
  states: input.states,
  language: "en",
  locale: { language: "en" },
  devices: { device: { name: "Car", identifiers: [["bavariandata", "WBS1"]] } },
  callService: () => new Promise(() => {}),
};
card.shadowRoot = { innerHTML: "", querySelectorAll: () => [], querySelector: () => null };
card.attachShadow = () => card.shadowRoot;
card._wireTaps = () => {};
card._styles = () => "";
card._resolveDeviceId = () => "device";
card._deviceEntities = () => Object.keys(input.states);
let error = null;
try { card._render(); } catch (e) { error = String((e && e.message) || e); }
process.stdout.write(JSON.stringify({ html: card.shadowRoot.innerHTML, error }));
"""

CARS = {"i5": I5, "m2": M2, "phev": PHEV}

# Every screen the card can show, addressed the way a user's YAML addresses it.
SCREENS = {
    "overview": {},
    "charging": {"view": "charging"},
    "trips": {"view": "trips"},
    "map": {"view": "map"},
    "health": {"view": "health"},
    "efficiency": {"view": "efficiency"},
    "cluster-tire": {"cluster": "tire"},
    "cluster-closures": {"cluster": "closures"},
    "cluster-electric": {"cluster": "electric"},
}


def _render(states: dict, config: dict) -> str:
    """The card's shadow-root HTML, one tag per line so a diff reads."""

    states = {entity_id: {**st, "entity_id": entity_id} for entity_id, st in states.items()}
    result = subprocess.run(
        [NODE, "-e", _HARNESS, str(_CARD)],
        input=json.dumps({"states": states, "config": config}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    payload = json.loads(result.stdout)
    assert payload["error"] is None, f"the card threw while rendering: {payload['error']}"
    return payload["html"].replace("><", ">\n<").strip()


@pytest.mark.parametrize("car", sorted(CARS))
@pytest.mark.parametrize("screen", sorted(SCREENS))
def test_the_card_renders_what_it_rendered_before(car: str, screen: str, snapshot) -> None:
    assert _render(CARS[car], SCREENS[screen]) == snapshot


def test_rendering_is_deterministic() -> None:
    """A snapshot is only worth having if a second run agrees with the first.

    The clock is frozen in the harness; this is what proves nothing else
    (ordering, an id counter, a locale default) leaks in.
    """

    first = _render(I5, SCREENS["overview"])
    second = _render(I5, SCREENS["overview"])
    assert first == second
