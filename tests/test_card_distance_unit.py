"""The card picks miles or km by the same rule as the integration (issue #126).

``units.distance_unit`` decides the unit for the settings screens and the
service calendar; the card's ``_lengthUnit`` must agree, or a distance typed in
one unit would be shown in another. Same cases as ``test_distance_unit.py``:
Metric + miles (the UK), Metric + km, US customary.

Runs the shipped card under Node; skipped where Node is not installed.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from .conftest import load_module
from .test_card_snapshots import _CARD, NODE

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

units = load_module("units")

_HARNESS = r"""
const fs = require("fs");
globalThis.HTMLElement = class {};
globalThis.customElements = { define() {}, get() {}, whenDefined() {} };
globalThis.window = globalThis;
console.info = () => {};
const src = fs.readFileSync(process.argv[1], "utf8");
const Card = new Function(src + "\nreturn BavarianDataCard;")();
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const out = cases.map(({ states, system }) => {
  const card = Object.create(Card.prototype);
  card._hass = { states, config: { unit_system: { length: system } } };
  return card._lengthUnit(Object.keys(states));
});
process.stdout.write(JSON.stringify(out));
"""

ODOMETER = "vehicle.vehicle.travelledDistance"
DISPLAY = "vehicle.cabin.infotainment.displayUnit.distance"
HMI = "vehicle.cabin.infotainment.hmi.distanceUnit"


def _states(sensors: list, car) -> dict:
    """The odometer first, then other distance sensors, then the car's setting."""

    states = {}
    for i, unit in enumerate(sensors):
        attributes = {"device_class": "distance", "unit_of_measurement": unit}
        if i == 0:
            attributes["descriptor"] = ODOMETER
        states[f"sensor.distance_{i}"] = {"state": "1", "attributes": attributes}
    if car is not None:
        descriptor = HMI if car == "kilometers" else DISPLAY
        states["sensor.display_unit"] = {"state": car, "attributes": {"descriptor": descriptor}}
    return states


# (sensor units, odometer first; the car's own setting; HA's length unit)
CASES = [
    (["mi"], None, "km"),  # Metric + miles: the odometer shown in miles
    ([], "miles", "km"),  # Metric + miles: only the car says so
    (["km"], "miles", "km"),  # a sensor the user set to km wins
    ([None, "mi"], None, "km"),
    (["km", "km"], "km", "km"),  # Metric + km
    ([], None, "km"),
    ([], "kilometers", "km"),
    ([], None, "mi"),  # US customary
    (["mi"], "miles", "mi"),
    (["km"], None, "mi"),
    ([], "unknown", "km"),
    ([], " Miles ", "km"),
]


def test_the_card_and_the_integration_pick_the_same_unit():
    payload = [
        {"states": _states(sensors, car), "system": system} for sensors, car, system in CASES
    ]
    result = subprocess.run(
        [NODE, "-e", _HARNESS, str(_CARD)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    card = json.loads(result.stdout)
    python = [units.distance_unit(sensors, car, system) for sensors, car, system in CASES]
    assert card == python
    assert python[:2] == ["mi", "mi"] and python[5] == "km" and python[7] == "mi"
