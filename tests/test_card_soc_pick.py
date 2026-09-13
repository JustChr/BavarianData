"""The card's state-of-charge gauge must land on the high-voltage battery.

The overview's ring takes the first battery-class ``%`` sensor it finds, and a
car carries several that are not the HV state of charge: the 12 V battery
(``electricalSystem.battery.stateOfCharge``, streamed by every car) and, until
the metadata was fixed, the fuel tank (``fuelSystem.level`` -- tagged
``battery`` because its name contains ``.level``). With no preference among
them, entity-registry order decided, so a plug-in hybrid could show its tank as
its charge and a petrol car its 12 V battery.

Unlike the rest of the suite this runs the shipped card itself, under Node, on
synthetic ``hass.states`` -- picking is behaviour, and a text match would pass
on a rewrite that no longer works. Skipped where Node is not installed; GitHub's
``ubuntu-latest`` runners ship it.
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

# Loads the card with just enough DOM stubbed to define its classes, then runs
# the overview's entity picks without constructing an element.
_HARNESS = r"""
const fs = require("fs");
globalThis.HTMLElement = class {};
globalThis.customElements = { define() {}, get() {}, whenDefined() {} };
globalThis.window = globalThis;
console.info = () => {};  // the card's version banner would precede the JSON
const src = fs.readFileSync(process.argv[1], "utf8");
const Card = new Function(src + "\nreturn BavarianDataCard;")();
const states = JSON.parse(fs.readFileSync(0, "utf8"));
const card = Object.create(Card.prototype);
card._config = {};
card._hass = { states };
process.stdout.write(JSON.stringify(card._overviewEntities(Object.keys(states))));
"""

HV_SOC = "sensor.car_hv_soc"
TWELVE_VOLT = "sensor.car_12v_battery"
TANK = "sensor.car_tank_level"
ESTIMATE = "sensor.car_soc_estimate"


def _sensor(descriptor: str, *, battery: bool = True, name: str = "", state: str = "50") -> dict:
    attrs = {"descriptor": descriptor, "unit_of_measurement": "%", "friendly_name": name}
    if battery:
        attrs["device_class"] = "battery"
    return {"state": state, "attributes": attrs}


_TWELVE_VOLT = _sensor("vehicle.electricalSystem.battery.stateOfCharge", name="Battery")
# Tagged battery the way every install before the metadata fix had it: the card
# must not depend on that fix having reached the entity.
_TANK = _sensor("vehicle.drivetrain.fuelSystem.level", name="Range Tank level (%)")
_HV = _sensor("vehicle.drivetrain.batteryManagement.header", name="HV battery state of charge")
_ESTIMATE = _sensor("soc_estimate", name="State of charge estimate")


def _picks(states: dict) -> dict:
    result = subprocess.run(
        [NODE, "-e", _HARNESS, str(_CARD)],
        input=json.dumps(states),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return json.loads(result.stdout)


def test_electric_car_gauge_skips_the_12v_battery_registered_first():
    # Dict order is registry order: the impostors come first on purpose.
    picks = _picks({TWELVE_VOLT: _TWELVE_VOLT, ESTIMATE: _ESTIMATE, HV_SOC: _HV})
    assert picks.get("soc") == HV_SOC


def test_hybrid_gauge_shows_the_battery_not_the_tank():
    picks = _picks({TANK: _TANK, TWELVE_VOLT: _TWELVE_VOLT, HV_SOC: _HV})
    assert picks.get("soc") == HV_SOC


def test_combustion_car_gauge_shows_neither_tank_nor_12v_battery():
    picks = _picks({TANK: _TANK, TWELVE_VOLT: _TWELVE_VOLT, ESTIMATE: _ESTIMATE})
    assert picks.get("soc") == ESTIMATE


def test_gauge_does_not_blank_on_a_measured_soc_that_has_not_reported_yet():
    # beta.8 on the maintainer's i5: the header entity was re-enabled, had nothing
    # to restore, and the ring read "—" while the estimate held 85 %. The
    # estimate's English name says "Predicted", which the avoid-list rejects, so
    # it must still be reachable by its descriptor.
    hv_unknown = _sensor("vehicle.drivetrain.batteryManagement.header", state="unknown")
    estimate = _sensor("soc_estimate", name="State Of Charge (Predicted on Integration side)", state="85.0")
    picks = _picks({HV_SOC: hv_unknown, TWELVE_VOLT: _TWELVE_VOLT, ESTIMATE: estimate})
    assert picks.get("soc") == ESTIMATE


def test_gauge_binds_to_the_measured_soc_when_nothing_has_a_value():
    # A brand-new install: the ring waits on the real SoC, not the 12 V battery.
    hv_unknown = _sensor("vehicle.drivetrain.batteryManagement.header", state="unknown")
    estimate = _sensor("soc_estimate", state="unknown")
    picks = _picks({ESTIMATE: estimate, TWELVE_VOLT: _TWELVE_VOLT, HV_SOC: hv_unknown})
    assert picks.get("soc") == HV_SOC


def test_combustion_car_fallback_pick_skips_the_12v_battery():
    # With no battery-class candidate left, the keyword fallback ("charge") would
    # otherwise match "state of charge" in the 12 V battery's descriptor.
    twelve_volt = _sensor("vehicle.electricalSystem.battery.stateOfCharge", battery=False)
    tank = _sensor("vehicle.drivetrain.fuelSystem.level", battery=False)
    picks = _picks({TWELVE_VOLT: twelve_volt, TANK: tank})
    assert picks.get("soc") is None
