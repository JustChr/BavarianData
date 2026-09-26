"""The card's overview must fit the car's drivetrain.

An electric car keeps the overview it always had. A petrol or diesel car has no
charge to ring and nothing to plug in, yet BMW streams it an EV charge *target*
(the F87 M2 from issue #8 does) -- so it gets its tank instead, and no charging
tiles. A plug-in hybrid gets both. Detection reads what the car streams, and the
M2 case proves the EV target alone never makes a car look electric.

Runs the shipped card under Node on synthetic ``hass.states``, like
``test_card_soc_pick.py``; skipped where Node is not installed.
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

# Renders the overview into a stub shadow root and reports the detected layout.
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
card._config = input.config;
card._hass = { states: input.states, language: "en", locale: { language: "en" }, devices: {} };
card.shadowRoot = { innerHTML: "" };
card._wireTaps = () => {};
card._styles = () => "";
const entities = Object.keys(input.states);
const drivetrain = card._drivetrain(entities);
card._renderOverview("device", entities);
process.stdout.write(JSON.stringify({ drivetrain, html: card.shadowRoot.innerHTML }));
"""


def _sensor(descriptor: str, state: str, unit: str | None = None, **attrs) -> dict:
    attributes = {"descriptor": descriptor, **attrs}
    if unit:
        attributes["unit_of_measurement"] = unit
    return {"state": state, "attributes": attributes, "last_changed": "2026-09-08T15:16:31Z"}


ODOMETER = _sensor("vehicle.vehicle.travelledDistance", "41234", "km", device_class="distance")
EV_TARGET = _sensor(
    "vehicle.powertrain.electric.battery.stateOfCharge.target", "0", "%", device_class="battery"
)

# The descriptors the F87 M2 actually streamed (issue #8 diagnostics), minus the
# ones the overview never looks at.
M2 = {
    "sensor.m2_remaining_fuel": _sensor("vehicle.drivetrain.fuelSystem.remainingFuel", "38", "L"),
    "sensor.m2_range": _sensor(
        "vehicle.drivetrain.lastRemainingRange", "412", "km", device_class="distance"
    ),
    "sensor.m2_target": EV_TARGET,
    "sensor.m2_coolant": _sensor(
        "vehicle.drivetrain.internalCombustionEngine.engine.ect", "88", "°C"
    ),
    "sensor.m2_odometer": ODOMETER,
}

# A MINI Cooper C, from the diagnostics on issue #23. A petrol car that streams
# *no* combustion descriptor at all -- no fuel system, no engine -- so the
# drivetrain cannot be proved from what it sends. It does carry the trip-end HV
# state of charge, a battery-class percentage that must never reach the ring on
# a car with no high-voltage battery. Trimmed to what the overview reads.
MINI = {
    "sensor.mini_range": _sensor(
        "vehicle.drivetrain.lastRemainingRange", "233", "km", device_class="distance"
    ),
    "sensor.mini_trip_hvsoc": _sensor(
        "vehicle.trip.segment.end.drivetrain.batteryManagement.hvSoc",
        "50",
        "%",
        device_class="battery",
    ),
    "sensor.mini_doors": _sensor("vehicle.cabin.door.status", "closed"),
    "sensor.mini_hood": _sensor("vehicle.body.hood.isOpen", "off"),
    "sensor.mini_trunk": _sensor("vehicle.body.trunk.isOpen", "off"),
    "sensor.mini_sunroof": _sensor("vehicle.cabin.sunroof.status", "closed"),
    "sensor.mini_window": _sensor("vehicle.cabin.window.row1.driver.status", "closed"),
    "sensor.mini_alarm": _sensor("vehicle.vehicle.antiTheftAlarmSystem.alarm.isOn", "off"),
    "sensor.mini_tyre": _sensor("vehicle.chassis.axle.row1.wheel.left.tire.pressure", "230", "kPa"),
    "sensor.mini_odometer": ODOMETER,
}

# The shape of the maintainer's i5.
I5 = {
    "sensor.i5_soc": _sensor(
        "vehicle.drivetrain.batteryManagement.header", "72", "%", device_class="battery"
    ),
    "sensor.i5_range": _sensor(
        "vehicle.drivetrain.electricEngine.kombiRemainingElectricRange",
        "379",
        "km",
        device_class="distance",
    ),
    "sensor.i5_charging": _sensor(
        "vehicle.drivetrain.electricEngine.charging.status", "nocharging"
    ),
    "sensor.i5_target": EV_TARGET,
    "sensor.i5_odometer": ODOMETER,
}


def _named(descriptor: str, state: str, name: str, unit: str | None = None, **attrs) -> dict:
    """A sensor carrying the friendly name Home Assistant would give it."""

    return _sensor(descriptor, state, unit, friendly_name=f"X3 30e xDrive {name}", **attrs)


# An X3 30e xDrive, from the diagnostics on issue #25 -- the first plug-in hybrid
# seen. Trimmed to what the overview reads, named as an English install names
# them, and in the registry's alphabetical order, which is what decided the plug
# tile before it named its descriptor: three of these names contain "plug", and
# two of them are lock states. It has no `isPlugged` binary sensor, and BMW's
# basic data spells it PHEV but with the same "BE" propulsion as a battery car.
# The charge had finished at its target, which BMW reports as `chargingended`.
PHEV = {
    "sensor.x3_30e_xdrive_charging_port_plug_post_charge_lock_state": _named(
        "vehicle.body.chargingPort.isHospitalityActive",
        "hospitality_inactive",
        "Charging Port plug post-charge lock state",
    ),
    "sensor.x3_30e_xdrive_charging_port_plug_lock_state": _named(
        "vehicle.body.chargingPort.lockedStatus", "locked", "Charging Port plug lock state"
    ),
    "sensor.x3_30e_xdrive_charging_port_plug_state": _named(
        "vehicle.body.chargingPort.status", "connected", "Charging Port plug state"
    ),
    "sensor.x3_30e_xdrive_battery_hv_state_of_charge": _named(
        "vehicle.drivetrain.batteryManagement.header",
        "72",
        "Battery HV State Of Charge",
        "%",
        device_class="battery",
        vehicle_basic_data={"drive_train": "PHEV", "propulsion_type": "BE"},
    ),
    "sensor.x3_30e_xdrive_charging_ev_charging_state": _named(
        "vehicle.drivetrain.electricEngine.charging.status",
        "chargingended",
        "Charging EV Charging state",
    ),
    "sensor.x3_30e_xdrive_charging_ev_time_to_full_charge": _named(
        "vehicle.drivetrain.electricEngine.charging.timeToFullyCharged",
        "0",
        "Charging EV Time to full charge",
        "min",
    ),
    "sensor.x3_30e_xdrive_range_ev_remaining_range": _named(
        "vehicle.drivetrain.electricEngine.kombiRemainingElectricRange",
        "61",
        "Range EV Remaining range",
        "km",
        device_class="distance",
    ),
    "sensor.x3_30e_xdrive_range_tank_level": _named(
        "vehicle.drivetrain.fuelSystem.level", "55", "Range Tank level", "%"
    ),
    "sensor.x3_30e_xdrive_range_total_range_last_sent": _named(
        "vehicle.drivetrain.lastRemainingRange",
        "590",
        "Range Total range (last sent)",
        "km",
        device_class="distance",
    ),
    "sensor.x3_30e_xdrive_battery_ev_target_state_of_charge": _named(
        "vehicle.powertrain.electric.battery.stateOfCharge.target",
        "80",
        "Battery EV Target state of charge",
        "%",
        device_class="battery",
    ),
    "sensor.x3_30e_xdrive_mileage": ODOMETER,
}


def _render(states: dict, config: dict | None = None) -> dict:
    # Home Assistant state objects carry their own id; lookups by descriptor
    # return the state, so the card reads the id off it.
    states = {entity_id: {**st, "entity_id": entity_id} for entity_id, st in states.items()}
    result = subprocess.run(
        [NODE, "-e", _HARNESS, str(_CARD)],
        input=json.dumps({"states": states, "config": config or {}}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return json.loads(result.stdout)


def test_petrol_m2_is_detected_despite_its_ev_charge_target():
    assert _render(M2)["drivetrain"] == "ice"


def test_petrol_overview_shows_the_tank_and_no_charging_tiles():
    html = _render(M2)["html"]
    # The ring carries the tank volume, since the M2 streams no percentage.
    assert 'data-entity="sensor.m2_remaining_fuel"' in html
    assert "38<i>L</i>" in html
    assert 'data-entity="sensor.m2_range"' in html
    assert "Odometer" in html
    for charging_only in ("Target", "charging status", "Plug", "Charge time", "sensor.m2_target"):
        assert charging_only not in html, charging_only


def test_petrol_car_with_a_tank_percentage_rings_the_percentage():
    states = {**M2, "sensor.m2_tank": _sensor("vehicle.drivetrain.fuelSystem.level", "64", "%")}
    html = _render(states)["html"]
    assert "--pct:64" in html
    assert "64<i>%</i>" in html
    # The volume moves beside the ring rather than disappearing.
    assert 'data-entity="sensor.m2_remaining_fuel"' in html
    assert "Tank" in html


def test_electric_overview_is_unchanged():
    rendered = _render(I5)
    assert rendered["drivetrain"] == "bev"
    html = rendered["html"]
    assert "72<i>%</i>" in html
    assert "charging status" in html
    assert "Target" in html
    assert "Tank" not in html


def test_plug_in_hybrid_shows_battery_and_tank():
    rendered = _render(PHEV)
    assert rendered["drivetrain"] == "phev"
    html = rendered["html"]
    assert "72<i>%</i>" in html
    assert "electric range" in html
    assert "Tank" in html and "Total range" in html
    assert "Target" in html


PLUG_STATE = "sensor.x3_30e_xdrive_charging_port_plug_state"


def test_the_plug_tile_reads_the_plug_state_not_a_lock():
    """Issue #25: registry order handed the tile the post-charge lock state."""

    html = _render(PHEV)["html"]
    assert f'data-entity="{PLUG_STATE}"' in html
    assert "lock_state" not in html
    # Plugged in and finished: the plug is in, so the icon says so.
    assert "mdi:power-plug-off" not in html


def test_the_plug_tile_survives_a_german_install():
    """No German name contains "plug", so only the descriptor can find it."""

    # Neutral ids and a German name, so only the descriptor carries English.
    german = {
        f"sensor.x3_{index}": {
            **st,
            "attributes": {**st["attributes"], "friendly_name": "X3 Ladeanschluss"},
        }
        for index, st in enumerate(PHEV.values())
    }
    plug_id = f"sensor.x3_{list(PHEV).index(PLUG_STATE)}"
    html = _render(german)["html"]
    assert f'data-entity="{plug_id}"' in html


def _with_status(status: str) -> dict:
    key = "sensor.x3_30e_xdrive_charging_ev_charging_state"
    return {**PHEV, key: {**PHEV[key], "state": status}}


@pytest.mark.parametrize(
    "status", ["chargingended", "chargingpaused", "chargingerror", "nocharging"]
)
def test_a_charge_that_has_stopped_is_not_shown_as_charging(status: str):
    """Issue #25: `chargingended` starts with "charging", and the heuristic
    read that as active -- a finished car sat under a green ring and a bolt."""

    html = _render(_with_status(status))["html"]
    assert "gauge__bolt" not in html
    assert "Time to full" not in html
    assert "Charge time" in html


def test_an_active_charge_is_still_shown_as_charging():
    html = _render(_with_status("chargingactive"))["html"]
    assert "gauge__bolt" in html
    assert "Time to full" in html


def test_basic_data_bev_outranks_a_stray_fuel_field():
    states = {
        **M2,
        "sensor.m2_odometer": {
            **ODOMETER,
            "attributes": {**ODOMETER["attributes"], "vehicle_basic_data": {"drive_train": "BEV"}},
        },
    }
    assert _render(states)["drivetrain"] == "bev"


def test_nothing_streamed_yet_keeps_the_electric_layout():
    assert _render({"sensor.odometer": ODOMETER})["drivetrain"] == "bev"


def test_a_car_that_proves_no_drivetrain_is_not_called_electric():
    """Issue #23: a petrol MINI streams neither a battery nor a fuel system.

    Calling that electric handed it a charge ring it could never fill -- and the
    ring was fed by the trip-end state of charge, so a petrol car displayed a
    battery percentage.
    """

    assert _render(MINI)["drivetrain"] == "unknown"


def test_the_bare_overview_rings_range_and_shows_no_battery():
    html = _render(MINI)["html"]
    # The range is the ring, in its own unit, and the odometer sits beside it.
    assert 'data-entity="sensor.mini_range"' in html
    assert "233<i>km</i>" in html
    assert "Odometer" in html
    # Nothing electric, and above all not the impostor.
    assert "sensor.mini_trip_hvsoc" not in html
    for electric_only in ("Target", "charging status", "Plug", "Charge time", "Tank"):
        assert electric_only not in html, electric_only


def test_the_trip_end_soc_never_rings_even_where_it_is_the_only_percentage():
    """It is a battery-class percentage that only moves when a drive ends, so
    it reads hours old. Every earlier pick already rejected it; the loosest
    fallback used to take it anyway."""

    states = {"sensor.odometer": ODOMETER, **MINI}
    html = _render(states, {"drivetrain": "bev"})["html"]
    assert "sensor.mini_trip_hvsoc" not in html
    # An honest empty ring instead of a stale number.
    assert "—" in html


def test_a_bare_car_still_takes_a_configured_drivetrain():
    assert _render(MINI, {"drivetrain": "ice"})["drivetrain"] == "ice"


def test_configured_drivetrain_wins():
    assert _render(I5, {"drivetrain": "ice"})["drivetrain"] == "ice"
    assert _render(M2, {"drivetrain": "auto"})["drivetrain"] == "ice"


# Renders one of the battery views. A service call never resolves, so a view
# that reached its fetch shows its loading state rather than the notice.
_VIEW_HARNESS = r"""
const fs = require("fs");
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
  devices: { device: { name: "M2", identifiers: [["bavariandata", "WBS1"]] } },
  // With a `response`, the service answers it and the view repaints; without
  // one it never resolves, leaving the view on its loading state.
  callService: () =>
    input.response === undefined ? new Promise(() => {}) : Promise.resolve({ response: input.response }),
};
card.shadowRoot = { innerHTML: "", querySelectorAll: () => [], querySelector: () => null };
card._wireTaps = () => {};
card._styles = () => "";
const entities = Object.keys(input.states);
card._resolveDeviceId = () => "device";
card._deviceEntities = () => entities;
const method = { charging: "_renderCharging", health: "_renderHealth", efficiency: "_renderEfficiency" }[input.config.view];
card[method]("device", entities);
setTimeout(() => process.stdout.write(JSON.stringify({ html: card.shadowRoot.innerHTML })), 0);
"""


def _render_view(
    states: dict, view: str, config: dict | None = None, *, response: dict | None = None
) -> str:
    states = {entity_id: {**st, "entity_id": entity_id} for entity_id, st in states.items()}
    payload = {"states": states, "config": {"view": view, **(config or {})}}
    if response is not None:
        payload["response"] = response
    result = subprocess.run(
        [NODE, "-e", _VIEW_HARNESS, str(_CARD)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return json.loads(result.stdout)["html"]


def test_battery_views_explain_themselves_on_a_petrol_car():
    for view in ("charging", "health", "efficiency"):
        html = _render_view(M2, view)
        assert "Nothing to show for this car" in html, view
        assert "M2 runs on fuel alone" in html, view


def test_battery_views_are_untouched_on_an_electric_car():
    for view in ("charging", "health", "efficiency"):
        html = _render_view(I5, view)
        assert "Nothing to show for this car" not in html, view


def test_a_plug_in_hybrid_keeps_its_battery_views():
    for view in ("charging", "health", "efficiency"):
        assert "Nothing to show for this car" not in _render_view(PHEV, view), view


def test_the_efficiency_view_explains_why_a_hybrid_has_no_range():
    """Issue #25: not "not enough history yet" -- no amount of it would do."""

    response = {"efficiency": {"status": "plug_in_hybrid", "consumption": None, "range": None}}
    html = _render_view(PHEV, "efficiency", response=response)
    assert "covers part of its distance on fuel" in html
    assert "Not enough charging history" not in html


def test_the_petrol_notice_is_escaped():
    html = _render_view(M2, "health", {"title": "<b>M2</b>"})
    assert "<b>" not in html
    assert "&lt;b&gt;M2&lt;/b&gt;" in html
