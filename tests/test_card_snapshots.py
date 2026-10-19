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
copied, so an electric/petrol/hybrid/unproven car means the same thing in both
files.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

import pytest

from .test_card_drivetrain import I5, M2, MINI, PHEV

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
if (input.mileage) {
  // A view whose figures come from a service: paint it with a response, since
  // the stubbed callService never answers.
  card._mil = { vin: "WBS1", trigger: "", data: input.mileage, loading: false };
  card._render = () => { card._sig = null; card._paintMileage("device", input.unit || "km"); };
}
if (input.fuel) {
  card._fuel = { vin: "WBS1", trigger: "", data: input.fuel, loading: false };
  card._render = () => { card._sig = null; card._paintFuel("device", input.unit || "km"); };
}
if (input.places) {
  card._chgTab = "places";
  card._plc = { vin: "WBS1", trigger: "", data: input.places, loading: false };
  card._plcExpanded = input.placesExpanded || null;
  card._render = () => { card._sig = null; card._paintPlaces("device"); };
}
if (input.efficiency) {
  card._eff = { vin: "WBS1", trigger: "", data: input.efficiency, loading: false };
  card._render = () => { card._sig = null; card._paintEfficiency("device", null); };
}
let error = null;
try { card._render(); } catch (e) { error = String((e && e.message) || e); }
process.stdout.write(JSON.stringify({ html: card.shadowRoot.innerHTML, error }));
"""

# "mini" is the fourth case: a car that proves no drivetrain at all (issue
# #23), which gets its own bare overview and must never be shown a battery.
CARS = {"i5": I5, "m2": M2, "phev": PHEV, "mini": MINI}

# Every screen the card can show, addressed the way a user's YAML addresses it.
SCREENS = {
    "overview": {},
    "charging": {"view": "charging"},
    "trips": {"view": "trips"},
    "map": {"view": "map"},
    "health": {"view": "health"},
    "efficiency": {"view": "efficiency"},
    "mileage": {"view": "mileage"},
    "fuel": {"view": "fuel"},
    "equipment": {"view": "equipment"},
    "cluster-tire": {"cluster": "tire"},
    "cluster-closures": {"cluster": "closures"},
    "cluster-electric": {"cluster": "electric"},
}


def _render(states: dict, config: dict, **extra) -> str:
    """The card's shadow-root HTML, one tag per line so a diff reads."""

    states = {entity_id: {**st, "entity_id": entity_id} for entity_id, st in states.items()}
    result = subprocess.run(
        [NODE, "-e", _HARNESS, str(_CARD)],
        input=json.dumps({"states": states, "config": config, **extra}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
        # Times render in the machine's zone; pinned, or a snapshot taken in
        # Vienna fails on CI's UTC runner.
        env={**os.environ, "TZ": "UTC"},
    )
    payload = json.loads(result.stdout)
    assert payload["error"] is None, f"the card threw while rendering: {payload['error']}"
    return payload["html"].replace("><", ">\n<").strip()


@pytest.mark.parametrize("car", sorted(CARS))
@pytest.mark.parametrize("screen", sorted(SCREENS))
def test_the_card_renders_what_it_rendered_before(car: str, screen: str, snapshot) -> None:
    assert _render(CARS[car], SCREENS[screen]) == snapshot


# What ``get_mileage_report`` answers for a car with a lease, and for one
# without an allowance whose log is three weeks old.
_MONTHS = [{"month": f"2025-{m:02d}", "km": None, "partial": False} for m in (10, 11, 12)] + [
    {"month": f"2026-{m:02d}", "km": km, "partial": m == 1 or m == 9}
    for m, km in zip(
        range(1, 10), (310, 1150, 1240, 1320, 1400, 1180, 1600, 2100, 380), strict=True
    )
]
MILEAGE = {
    "lease": {
        "odometer_km": 23412.0,
        "since": "2026-01-12",
        "this_month": {"km": 380, "partial": False},
        "this_year": {"km": 10680, "partial": True, "estimated_km": 10910},
        "km_per_day": 41.2,
        "projected_year_km": 15038,
        "months": _MONTHS,
        "allowance": {
            "kind": "lease",
            "start": "2025-09-01",
            "end": "2028-08-31",
            "allowance_km": 45000,
            "per_year_km": 15000,
            "status": "ok",
            "used_km": 23400,
            "plan_km": 24220,
            "balance_km": 820,
            "remaining_km": 21600,
            "days_left": 722,
            "km_per_day_left": 29.9,
            "projected_km": 44100,
            "projected_over_km": -900,
        },
    },
    "young": {
        "odometer_km": 812.0,
        "since": "2026-08-20",
        "this_month": {"km": 380, "partial": False},
        "this_year": {"km": 790, "partial": True},
        "km_per_day": None,
        "projected_year_km": None,
        "months": [{"month": "2026-08", "km": 410, "partial": True}]
        + [{"month": "2026-09", "km": 380, "partial": True}],
        "allowance": None,
    },
}


@pytest.mark.parametrize("case", sorted(MILEAGE))
@pytest.mark.parametrize("unit", ["km", "mi"])
def test_the_mileage_view_with_figures(case: str, unit: str, snapshot) -> None:
    assert _render(I5, SCREENS["mileage"], mileage=MILEAGE[case], unit=unit) == snapshot


# What ``get_fuel_history`` answers for a diesel with three refuels on record,
# BMW's lifetime counters among them, and for one that has never refuelled here.
FUEL = {
    "logged": {
        "refuels": [
            {
                "t": "2026-09-08T17:40:00+00:00",
                "km": 23_900.0,
                "added_l": 47.0,
                "added_percent": None,
                "after_l": 63.0,
                "after_percent": None,
                "l_per_100km": 7.1,
                "window_km": 480.0,
            },
            {
                "t": "2026-09-01T08:10:00+00:00",
                "km": 23_420.0,
                "added_l": 36.0,
                "added_percent": None,
                "after_l": 61.0,
                "after_percent": None,
                "l_per_100km": None,
                "window_km": None,
            },
        ],
        "average": {"l_per_100km": 7.1, "km": 480.0, "litres": 34.0, "windows": 1},
        "since_refuel": {"km": 120.0, "litres": 9.0, "l_per_100km": None},
        "tank": {"litres": 54.0, "percent": None, "range_km": 720.0},
        "bmw_lifetime": {"fuel_l_per_100km": 7.4, "distance_km": 23_000.0},
    },
    "empty": {
        "refuels": [],
        "average": None,
        "since_refuel": None,
        "tank": {"litres": 38.0, "percent": None, "range_km": 510.0},
        "bmw_lifetime": None,
    },
}


@pytest.mark.parametrize("case", sorted(FUEL))
@pytest.mark.parametrize("unit", ["km", "mi"])
def test_the_fuel_view_with_figures(case: str, unit: str, snapshot) -> None:
    assert _render(M2, SCREENS["fuel"], fuel=FUEL[case], unit=unit) == snapshot


# A plug-in hybrid's efficiency view, from BMW's lifetime counters.
HYBRID_EFFICIENCY = {
    "status": "plug_in_hybrid",
    "bmw_lifetime": {
        "fuel_l_per_100km": 1.5,
        "distance_km": 20_000.0,
        "electric_kwh_per_100km": 20.0,
        "electric_km": 9_000.0,
        "grid_kwh_per_100km": 10.0,
        "electric_share_percent": 45.0,
    },
}


def test_a_hybrid_shows_bmws_lifetime_figures(snapshot) -> None:
    assert _render(PHEV, SCREENS["efficiency"], efficiency=HYBRID_EFFICIENCY) == snapshot


def test_the_fuel_figures_render_in_every_language() -> None:
    for case in FUEL.values():
        result = subprocess.run(
            [NODE, "-e", _EVERY_LANGUAGE, str(_CARD)],
            input=json.dumps({"states": {}, "config": SCREENS["fuel"], "fuel": case}),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=60,
        )
        assert json.loads(result.stdout) == {}


def test_rendering_is_deterministic() -> None:
    """A snapshot is only worth having if a second run agrees with the first.

    The clock is frozen in the harness; this is what proves nothing else
    (ordering, an id counter, a locale default) leaks in.
    """

    first = _render(I5, SCREENS["overview"])
    second = _render(I5, SCREENS["overview"])
    assert first == second


# The snapshots above are English only, so a translated table is never rendered
# by them. This renders every screen in every language the card ships and only
# asserts that nothing throws: a locale-dependent formatter (a month label, a
# number) is the kind of thing that works in "en" and breaks in "pt".
_EVERY_LANGUAGE = _HARNESS.replace(
    "let error = null;\ntry { card._render(); } catch (e) { error = String((e && e.message) || e); }\n"
    "process.stdout.write(JSON.stringify({ html: card.shadowRoot.innerHTML, error }));",
    'const T = new Function(src + "\\nreturn TRANSLATIONS;")();\n'
    "const errors = {};\n"
    "for (const lang of Object.keys(T)) {\n"
    "  card._hass.language = lang;\n"
    "  card._hass.locale = { language: lang };\n"
    "  try { card._render(); } catch (e) { errors[lang] = String((e && e.message) || e); }\n"
    "}\n"
    "process.stdout.write(JSON.stringify(errors));",
)


def test_the_mileage_figures_render_in_every_language() -> None:
    for case in MILEAGE.values():
        result = subprocess.run(
            [NODE, "-e", _EVERY_LANGUAGE, str(_CARD)],
            input=json.dumps({"states": {}, "config": SCREENS["mileage"], "mileage": case}),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=60,
        )
        assert json.loads(result.stdout) == {}


@pytest.mark.parametrize("car", sorted(CARS))
@pytest.mark.parametrize("screen", sorted(SCREENS))
def test_every_screen_renders_in_every_language(car: str, screen: str) -> None:
    assert _EVERY_LANGUAGE != _HARNESS, "the harness changed; update the replacement"
    states = {eid: {**st, "entity_id": eid} for eid, st in CARS[car].items()}
    result = subprocess.run(
        [NODE, "-e", _EVERY_LANGUAGE, str(_CARD)],
        input=json.dumps({"states": states, "config": SCREENS[screen]}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    assert json.loads(result.stdout) == {}


# What ``get_charging_places`` answers, built by the server's own code from
# stored sessions, so the card is pinned against the real response shape.
def _places(remembered: bool) -> list:
    from datetime import datetime, timedelta, timezone

    from .conftest import load_module

    models = load_module("history.models")
    stations = load_module("history.stations")
    t0 = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)

    def point(lat, lon):
        return {"lat": lat, "lon": lon} if remembered else {}

    def charge(day, location, minutes, kwh, soc, peak, **extra):
        start = t0 + timedelta(days=day)
        curve = [[0, peak * 0.9], [minutes * 20, peak], [minutes * 60, peak * 0.4]]
        return models.ChargingSession(
            vin="WBS1",
            start=start,
            end=start + timedelta(minutes=minutes),
            soc_start=soc[0],
            soc_end=soc[1],
            energy_kwh=kwh,
            peak_power_kw=peak,
            power_curve=curve,
            location=location,
            **extra,
        )

    ionity = {"zone": None, "address": "A9 Holledau", "provider": "Ionity", **point(48.6, 11.6)}
    sessions = [
        charge(0, {"zone": "Home", **point(48.1, 11.5)}, 190, 33.0, (40, 80), 11.0),
        charge(2, {"zone": "Home", **point(48.1, 11.5)}, 180, 31.0, (42, 80), 11.0),
        charge(4, ionity, 18, 42.0, (20, 60), 180.0, preconditioned=True),
        charge(6, ionity, 24, 41.0, (22, 62), 160.0, preconditioned=False),
        charge(7, {"zone": None, "address": "Rosenheim"}, 60, 10.9, (50, 64), 11.0),
        charge(8, {"zone": None}, 30, 6.0, (60, 70), 11.0),
    ]
    return stations.charging_places(sessions, home_zone="Home")


@pytest.mark.parametrize("remembered", [False, True], ids=["list", "map"])
def test_the_places_tab(remembered: bool, snapshot) -> None:
    places = _places(remembered)
    ionity = next(p for p in places if p["provider"] == "Ionity")
    assert _render(I5, SCREENS["charging"], places=places, placesExpanded=ionity["id"]) == snapshot
