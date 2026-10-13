"""The tyre view shows remaining tyre life in the owner's distance unit.

BMW's tyre diagnosis reaches the card as an attribute (``due_mileage_km``), so
Home Assistant's unit conversion never touches it the way it does a sensor
state. The card printed it as km whatever the install used, and a UK owner with
every distance sensor switched to miles still read "46,000 km" (issue #100). It
now follows the odometer's unit, then the car's other distance sensors, then
the unit system.

Runs the shipped card under Node, like ``test_card_steering.py``; skipped where
Node is not installed.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CARD = _ROOT / "custom_components" / "bavariandata" / "www" / "bavariandata-card.js"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

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
card._config = { cluster: "tire" };
card._hass = {
  states: input.states,
  language: "en",
  locale: { language: "en" },
  config: { unit_system: { length: input.system } },
  devices: { device: { name: "Car" } },
};
card.shadowRoot = { innerHTML: "" };
card._wireTaps = () => {};
card._styles = () => "";
card._renderTires("device", Object.keys(input.states));
process.stdout.write(JSON.stringify({ html: card.shadowRoot.innerHTML }));
"""

WHEEL = "sensor.car_tyre_front_left"
ODOMETER = "sensor.car_odometer"


def _render(*, odometer_unit: str | None, system: str = "km") -> str:
    states = {
        WHEEL: {
            "entity_id": WHEEL,
            "state": "green",
            "attributes": {
                "cluster": "tire",
                "tire_axle": "row1",
                "tire_side": "left",
                "tire_metric": "diagnosis",
                "due_mileage_km": 46000,
            },
        }
    }
    if odometer_unit:
        states[ODOMETER] = {
            "entity_id": ODOMETER,
            "state": "1200",
            "attributes": {
                "descriptor": "vehicle.vehicle.travelledDistance",
                "device_class": "distance",
                "unit_of_measurement": odometer_unit,
            },
        }
    result = subprocess.run(
        [NODE, "-e", _HARNESS, str(_CARD)],
        input=json.dumps({"states": states, "system": system}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return json.loads(result.stdout)["html"]


def test_metric_install_shows_kilometres() -> None:
    html = _render(odometer_unit="km")
    assert "46,000 km" in html
    assert " mi<" not in html


def test_odometer_switched_to_miles_shows_miles_on_a_metric_system() -> None:
    html = _render(odometer_unit="mi", system="km")
    assert "28,583 mi" in html
    assert "46,000 km" not in html


def test_imperial_system_shows_miles_without_a_distance_sensor() -> None:
    html = _render(odometer_unit=None, system="mi")
    assert "28,583 mi" in html


def test_metric_system_without_a_distance_sensor_stays_in_kilometres() -> None:
    assert "46,000 km" in _render(odometer_unit=None, system="km")
