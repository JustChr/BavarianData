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

PHEV = {
    **I5,
    "sensor.phev_tank": _sensor("vehicle.drivetrain.fuelSystem.level", "55", "%"),
    "sensor.phev_total_range": _sensor(
        "vehicle.drivetrain.lastRemainingRange", "690", "km", device_class="distance"
    ),
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
  callService: () => new Promise(() => {}),
};
card.shadowRoot = { innerHTML: "", querySelectorAll: () => [], querySelector: () => null };
card._wireTaps = () => {};
card._styles = () => "";
const entities = Object.keys(input.states);
const method = { charging: "_renderCharging", health: "_renderHealth", efficiency: "_renderEfficiency" }[input.config.view];
card[method]("device", entities);
process.stdout.write(JSON.stringify({ html: card.shadowRoot.innerHTML }));
"""


def _render_view(states: dict, view: str, config: dict | None = None) -> str:
    states = {entity_id: {**st, "entity_id": entity_id} for entity_id, st in states.items()}
    result = subprocess.run(
        [NODE, "-e", _VIEW_HARNESS, str(_CARD)],
        input=json.dumps({"states": states, "config": {"view": view, **(config or {})}}),
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


def test_the_petrol_notice_is_escaped():
    html = _render_view(M2, "health", {"title": "<b>M2</b>"})
    assert "<b>" not in html
    assert "&lt;b&gt;M2&lt;/b&gt;" in html
