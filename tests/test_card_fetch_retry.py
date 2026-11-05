"""A view whose service call fails asks again later, not on its next paint.

Every view that reads a service (charging, places, trips, map, efficiency,
fuel, mileage) repaints when the answer arrives, and a paint asks the service
whenever nothing is cached. A failed answer caches nothing, so each refusal
used to start the next call at once: while an account reloaded (saving a
Configure screen does that), one open dashboard logged "No loaded BavarianData
account" 172 times in a second (#277). A failure now waits ``FETCH_RETRY_MS``.

Runs the shipped card under Node, like the other card tests: the loop is
behaviour across promise callbacks, which a text match would not see.
"""

from __future__ import annotations

import functools
import json
import shutil
import subprocess

import pytest

from .card_source import evaluable_card

_CARD = evaluable_card()

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

# Each view renders against a service that always refuses; `_render` paints
# the same view again, as the card's own `_render` would. The clock is the
# card's: `Date.now()` is moved forward by hand, and the retry timer is caught
# rather than waited for.
_HARNESS = r"""
const fs = require("fs");
globalThis.HTMLElement = class {};
globalThis.customElements = { define() {}, get() {}, whenDefined() {} };
globalThis.window = globalThis;
console.info = () => {};
let now = Date.UTC(2026, 10, 5, 12, 0, 0);
Date.now = () => now;
const timers = [];
globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
const src = fs.readFileSync(process.argv[1], "utf8");
const Card = new Function(src + "\nreturn BavarianDataCard;")();

const VIEWS = {
  charging: (c) => c._renderCharging("dev", []),
  places: (c) => c._renderPlaces("dev", [], "VIN", "entry", ""),
  trips: (c) => c._renderTrips("dev", []),
  map: (c) => c._renderMap("dev", []),
  efficiency: (c) => c._renderEfficiency("dev", []),
  fuel: (c) => c._renderFuel("dev", []),
  mileage: (c) => c._renderMileage("dev", []),
};
const settle = async () => { for (let i = 0; i < 200; i++) await null; };

(async () => {
  const out = {};
  for (const [view, paint] of Object.entries(VIEWS)) {
    timers.length = 0;
    let calls = 0;
    const card = Object.create(Card.prototype);
    card._config = { view };
    card._hass = {
      states: {},
      devices: {},
      // Past 50 calls it stops answering, so the old loop ends and fails
      // the count instead of spinning Node forever.
      callService: () => {
        calls++;
        return calls > 50 ? new Promise(() => {}) : Promise.reject(new Error("not loaded"));
      },
    };
    card.shadowRoot = { querySelector: () => null, innerHTML: "" };
    card._deviceVin = () => "VIN";
    card._deviceEntry = () => "entry";
    card._drivetrain = () => (view === "fuel" ? "ice" : "bev");
    card._ensureMapLib = () => {};
    for (const name of Object.getOwnPropertyNames(Card.prototype)) {
      if (/^_paint/.test(name)) card[name] = () => {};
    }
    card._render = () => paint(card);
    paint(card);
    await settle();
    const first = calls;
    // The retry timer fires: one more call, then quiet again.
    const waits = timers.map((t) => t.ms);
    now += Math.max(0, ...waits);
    timers.splice(0).forEach((t) => t.fn());
    await settle();
    out[view] = { first, afterRetry: calls, waits };
  }
  process.stdout.write(JSON.stringify(out));
})();
"""


@functools.cache
def _run() -> dict:
    proc = subprocess.run(
        [NODE, "-e", _HARNESS, "--", str(_CARD)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# The service calls one fetch makes: trips asks for the month's summary too.
CALLS_PER_FETCH = {
    "charging": 1,
    "places": 1,
    "trips": 2,
    "map": 1,
    "efficiency": 1,
    "fuel": 1,
    "mileage": 1,
}


@pytest.mark.parametrize("view", sorted(CALLS_PER_FETCH))
def test_a_refused_call_is_asked_once_then_again_after_the_wait(view: str) -> None:
    result = _run()[view]
    per_fetch = CALLS_PER_FETCH[view]
    assert result["first"] == per_fetch, result
    assert result["waits"] and min(result["waits"]) >= 10000, result
    assert result["afterRetry"] == 2 * per_fetch, result
