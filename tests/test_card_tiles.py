"""The overview's tile grid: what each drivetrain shows by default, and `tiles:`.

A petrol or diesel car used to get at most one tile (the odometer), because the
grid was the electric car's charging tiles with those taken away. It now gets
what any car streams -- tyres, location, distance this month -- and a `tiles:`
list replaces the grid whole, with ready-made tiles or any entity (discussion
#185: a diesel X5 owner wanted tyre pressure and location there).
"""

from __future__ import annotations

import json
import subprocess

import pytest

from .test_card_drivetrain import _CARD, I5, M2, NODE, _render, _sensor
from .test_card_snapshots import _render as _render_frozen

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")


def _tyre(axle: str, side: str, metric: str, kpa: str) -> dict:
    st = _sensor(f"vehicle.chassis.axle.{axle}.wheel.{side}.tire.{metric}", kpa, "kPa")
    attributes = {
        **st["attributes"],
        "cluster": "tire",
        "tire_axle": axle,
        "tire_side": side,
        "tire_metric": metric,
    }
    return {**st, "attributes": attributes}


TYRES = {
    f"sensor.car_tyre_{axle}_{side}_{metric}": _tyre(axle, side, metric, kpa)
    for axle in ("row1", "row2")
    for side in ("left", "right")
    for metric, kpa in (("pressure", "250" if axle == "row1" else "260"), ("pressureTarget", "250"))
}
EXTRAS = {
    **TYRES,
    "device_tracker.car_location": {
        "state": "home",
        "attributes": {"friendly_name": "Car Location"},
        "last_changed": "2026-09-08T15:16:31Z",
    },
    "sensor.car_distance_month": _sensor("driving_distance_month", "412", "km"),
    "sensor.car_lock": _sensor("vehicle.cabin.door.status", "LOCKED"),
    "sensor.car_12v": _sensor("vehicle.electricalSystem.battery.stateOfCharge", "81", "%"),
    "sensor.car_coolant": _sensor(
        "vehicle.drivetrain.internalCombustionEngine.engine.ect",
        "88",
        "°C",
        device_class="temperature",
        friendly_name="BMW Coolant temperature",
    ),
}
DIESEL = {**M2, **EXTRAS}


def _cells(html: str) -> list[str]:
    """The tile labels, in order."""

    return [part.split("</span>")[0] for part in html.split('class="cell__lbl">')[1:]]


def test_a_combustion_car_fills_its_grid_with_what_it_streams():
    rendered = _render(DIESEL)
    assert rendered["drivetrain"] == "ice"
    # The odometer already sits beside the ring, where the M2's tank in litres
    # leaves it room.
    assert _cells(rendered["html"]) == ["Tires", "Location", "Driven this month"]
    # The pressure spread, colored by the same verdict as the tire view.
    assert "250 – 260 kPa" in rendered["html"]
    assert 'data-entity="device_tracker.car_location"' in rendered["html"]


def test_a_combustion_car_without_that_data_keeps_the_grid_it_had():
    assert _cells(_render(M2)["html"]) == []


def test_the_electric_grid_is_unchanged_by_the_new_tiles():
    assert _cells(_render({**I5, **EXTRAS})["html"]) == ["Target", "Odometer"]


def test_tiles_replace_the_grid_in_their_order():
    config = {"tiles": ["lock", "sensor.car_coolant", "battery_12v", "tires"]}
    html = _render(DIESEL, config)["html"]
    # The entity's name loses the car's ("BMW", the device name here), and its
    # icon comes from its device class.
    assert _cells(html) == ["Lock", "Coolant temperature", "12 V battery", "Tires"]
    assert "Locked" in html
    assert 'icon="mdi:thermometer"' in html


def test_a_tile_without_data_or_an_unknown_name_is_left_out():
    config = {"tiles": ["sensor.gone", "nonsense", "plug", "odometer"]}
    assert _cells(_render(DIESEL, config)["html"]) == ["Odometer"]


def test_a_yaml_tile_may_name_and_icon_its_entity():
    config = {
        "tiles": [{"entity": "sensor.car_coolant", "name": "Engine <b>", "icon": "mdi:engine"}]
    }
    html = _render(DIESEL, config)["html"]
    assert _cells(html) == ["Engine &lt;b&gt;"]
    assert 'icon="mdi:engine"' in html


def test_the_default_and_a_custom_grid_render_as_before(snapshot):
    assert _render_frozen(DIESEL, {}) == snapshot(name="default")
    assert _render_frozen(DIESEL, {"tiles": ["tires", "lock", "sensor.car_coolant"]}) == snapshot(
        name="custom"
    )


_EDITOR = r"""
const fs = require("fs");
globalThis.HTMLElement = class {};
globalThis.customElements = { define() {}, get() {}, whenDefined() {} };
globalThis.window = globalThis;
console.info = () => {};
const src = fs.readFileSync(process.argv[1], "utf8");
const Editor = new Function(src + "\nreturn BavarianDataCardEditor;")();
const { config, change } = JSON.parse(fs.readFileSync(0, "utf8"));
const editor = Object.create(Editor.prototype);
editor._hass = {
  devices: {},
  language: "en",
  locale: { language: "en" },
  entities: {
    "sensor.car_coolant": { entity_id: "sensor.car_coolant", device_id: "car" },
    "sensor.other": { entity_id: "sensor.other", device_id: "elsewhere" },
  },
  states: { "sensor.car_coolant": { attributes: { friendly_name: "Car Coolant temperature" } } },
};
editor._config = config;
editor._form = {};
let stored = null;
editor.dispatchEvent = (ev) => { stored = ev.detail.config; };
const field = editor._schema().find((f) => f.name === "tiles");
if (change) editor._valueChanged({ stopPropagation() {}, detail: { value: change } });
process.stdout.write(JSON.stringify({ field: field || null, stored }));
"""


def _editor(config: dict, change: dict | None = None) -> dict:
    result = subprocess.run(
        [NODE, "-e", _EDITOR, str(_CARD)],
        input=json.dumps({"config": config, "change": change}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return json.loads(result.stdout)


def test_the_editor_offers_ready_made_tiles_then_the_cars_entities():
    select = _editor({"device": "car"})["field"]["selector"]["select"]
    assert select["multiple"] and select["reorder"]
    values = [option["value"] for option in select["options"]]
    assert values[:3] == ["odometer", "tank", "total_range"]
    assert values[-1] == "sensor.car_coolant"
    assert "sensor.other" not in values
    assert select["options"][6] == {"value": "tires", "label": "Tires"}


def test_the_editor_leaves_a_yaml_tile_list_to_yaml():
    assert _editor({"tiles": [{"entity": "sensor.car_coolant", "name": "Engine"}]})["field"] is None


def test_the_editor_offers_tiles_on_the_overview_only():
    assert _editor({"view": "trips"})["field"] is None


def test_clearing_the_tiles_stores_no_key():
    stored = _editor({"tiles": ["tires"]}, {"cluster": "overview", "tiles": []})["stored"]
    assert "tiles" not in stored
