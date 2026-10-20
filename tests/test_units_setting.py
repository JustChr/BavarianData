"""The account's Units setting (Configure -> Units, discussion #183).

One choice per quantity -- distance, fuel, temperature, tire pressure -- each
"auto" by default, where the rules from before apply unchanged. An explicit
choice wins everywhere values are shown: the card, the settings screens, the
service calendar, the month report and Assist, and the sensors' own display
unit in Home Assistant. Nothing recorded changes: trips, the ledger and the
odometer log stay in km, and a sensor keeps its native unit, so switching never
splits a history.

The card applies the same rules in JavaScript; the Node tests at the bottom hold
it to them.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from .conftest import load_module
from .test_card_snapshots import _CARD, NODE

units = load_module("units")
const = load_module("const")


def test_everything_is_automatic_until_the_user_picks_a_unit():
    assert units.unit_choices({}) == {
        "distance": "auto",
        "fuel": "auto",
        "temperature": "auto",
        "pressure": "auto",
    }
    # A value a later version might drop reads as Automatic, not as an error.
    assert units.unit_choices({const.OPTION_UNIT_DISTANCE: "furlong"})["distance"] == "auto"
    stored = {
        const.OPTION_UNIT_DISTANCE: "mi",
        const.OPTION_UNIT_FUEL: "l_mpg_uk",
        const.OPTION_UNIT_TEMPERATURE: "f",
        const.OPTION_UNIT_PRESSURE: "psi",
    }
    assert units.unit_choices(stored) == {
        "distance": "mi",
        "fuel": "l_mpg_uk",
        "temperature": "f",
        "pressure": "psi",
    }


@pytest.mark.parametrize(
    ("sensors", "car", "system", "choice", "expected"),
    [
        # An explicit choice beats every automatic input.
        (["km"], "kilometers", "km", "mi", "mi"),
        (["mi"], "miles", "mi", "km", "km"),
        # Automatic (or nothing stored) is the rule from issue #126.
        (["mi"], None, "km", "auto", "mi"),
        ([], "miles", "km", None, "mi"),
        ([], None, "mi", "auto", "mi"),
        ([], None, "km", "auto", "km"),
    ],
)
def test_an_explicit_distance_wins(sensors, car, system, choice, expected):
    assert units.distance_unit(sensors, car, system, choice) == expected


@pytest.mark.parametrize(
    ("choice", "distance", "system", "expected"),
    [
        ("auto", "km", "km", "l"),  # metric
        ("auto", "mi", "km", "l_mpg_uk"),  # the UK: litres at the pump, imperial mpg
        ("auto", "mi", "mi", "gal_us"),  # US customary
        ("auto", "km", "mi", "gal_us"),
        ("l", "mi", "mi", "l"),  # an explicit choice wins
        ("gal_us", "km", "km", "gal_us"),
        ("l_mpg_uk", "km", "km", "l_mpg_uk"),
        (None, "km", "km", "l"),
    ],
)
def test_fuel_follows_the_distance_unless_chosen(choice, distance, system, expected):
    assert units.fuel_unit(choice, distance, system) == expected


def test_each_sensor_kind_takes_the_unit_its_choice_names():
    choices = {"distance": "mi", "fuel": "gal_us", "temperature": "f", "pressure": "psi"}
    assert units.sensor_display_unit("distance", choices) == "mi"
    assert units.sensor_display_unit("speed", choices) == "mph"
    assert units.sensor_display_unit("volume_storage", choices) == "gal"
    assert units.sensor_display_unit("temperature", choices) == "°F"
    assert units.sensor_display_unit("pressure", choices) == "psi"
    # Home Assistant has no imperial gallon: UK fuel stays in litres on the sensor.
    assert units.sensor_display_unit("volume_storage", {"fuel": "l_mpg_uk"}) == "L"
    # Automatic, or a kind the setting does not cover, leaves the sensor alone.
    assert units.sensor_display_unit("distance", {"distance": "auto"}) is None
    assert units.sensor_display_unit("energy", choices) is None
    assert units.sensor_display_unit(None, choices) is None


def test_switching_back_to_automatic_takes_back_only_what_we_set():
    miles = {"distance": "mi"}
    auto = {"distance": "auto"}
    update = units.sensor_unit_update

    # Miles chosen: the sensor is set and marked as ours.
    assert update("distance", miles, None, None) == ("mi", "mi")
    # Applied already: nothing to write on the next start.
    assert update("distance", miles, "mi", "mi") is None
    # Automatic again: our unit comes off, and so does the mark.
    assert update("distance", auto, "mi", "mi") == (None, None)
    # ... unless the user picked a unit on the entity since: theirs stays.
    assert update("distance", auto, "km", "mi") == ("km", None)
    # A unit the user set before we ever touched the sensor is never undone.
    assert update("distance", auto, "mi", None) is None
    # An explicit choice wins over the user's own pick (it is the newer word).
    assert update("distance", miles, "km", None) == ("mi", "mi")
    # Kinds the setting does not cover are never touched.
    assert update("energy", miles, "Wh", None) is None


def test_only_road_distances_follow_the_distance_choice():
    miles = {"distance": "mi"}
    update = units.sensor_unit_update

    # The GPS altitude is a distance sensor in metres: miles would read 0.11.
    assert units.sensor_display_unit("distance", miles, "m") is None
    assert update("distance", miles, None, None, "m") is None
    # Set on it by an earlier build: taken back even while miles stay chosen.
    assert update("distance", miles, "mi", "mi", "m") == (None, None)
    # Road distances convert, and so do derived sensors whose unit is not known.
    assert update("distance", miles, None, None, "km") == ("mi", "mi")
    assert update("distance", miles, None, None, None) == ("mi", "mi")
    assert units.sensor_display_unit("speed", miles, "km/h") == "mph"
    assert units.sensor_display_unit("speed", miles, "m/s") is None


# --- the card applies the same rules ----------------------------------------------

_HARNESS = r"""
const fs = require("fs");
globalThis.HTMLElement = class {};
globalThis.customElements = { define() {}, get() {}, whenDefined() {} };
globalThis.window = globalThis;
console.info = () => {};
const src = fs.readFileSync(process.argv[1], "utf8");
const Card = new Function(src + "\nreturn [BavarianDataCard, _fuelUnitRule];")();
const [CardClass, fuelRule] = Card;
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const fuel = input.fuel.map(([choice, distance, system]) => fuelRule(choice, distance, system));
const card = Object.create(CardClass.prototype);
const distance = input.distance.map(([choice, system]) => {
  card._hass = {
    config: { unit_system: { length: system } },
    states: { "sensor.connection": { state: "x", attributes: { display_units: { distance: choice } } } },
    devices: {
      car: { identifiers: [["bavariandata", "WBS1"]], config_entries: ["entry1"] },
      account: { identifiers: [["bavariandata", "entry1"]] },
    },
    entities: { "sensor.connection": { platform: "bavariandata", device_id: "account" } },
  };
  return card._distUnit("car", []);
});
process.stdout.write(JSON.stringify({ fuel, distance }));
"""

FUEL_CASES = [
    [choice, distance, system]
    for choice in ("auto", "l", "l_mpg_uk", "gal_us", None)
    for distance in ("km", "mi")
    for system in ("km", "mi")
]
DISTANCE_CASES = [[choice, system] for choice in ("auto", "km", "mi") for system in ("km", "mi")]


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_the_card_resolves_units_like_the_integration():
    result = subprocess.run(
        [NODE, "-e", _HARNESS, str(_CARD)],
        input=json.dumps({"fuel": FUEL_CASES, "distance": DISTANCE_CASES}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    card = json.loads(result.stdout)
    assert card["fuel"] == [units.fuel_unit(*case) for case in FUEL_CASES]
    # The card reads the choice off the account's connection sensor; with no
    # sensors of the car's own, Automatic falls through to the unit system.
    assert card["distance"] == [
        units.distance_unit([], None, system, choice) for choice, system in DISTANCE_CASES
    ]
