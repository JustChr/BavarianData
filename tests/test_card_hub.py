"""The hub: an overview with `hub: true` lists the rooms under it.

Each row says what its room would say first, from sensors the car already has
(the month's charging and driving, the real range, the car's banner); a tap
opens the room in the same card, and the arrow at its head goes back. Without
`hub: true` nothing changes (the golden renders in test_card_snapshots.py pin
that byte for byte).

The taps go through the card's own listener on the shadow root, fed the event
path a browser would give it, so the wiring is covered and not only the room
switch behind it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from .card_source import evaluable_card
from .test_card_drivetrain import I5, M2, MINI, PHEV

_CARD = evaluable_card()

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

_HARNESS = r"""
const fs = require("fs");
const FIXED = Date.parse("2026-11-09T12:00:00Z");
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
const [Card, Editor] = new Function(src + "\nreturn [BavarianDataCard, BavarianDataCardEditor];")();
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const hass = {
  states: input.states,
  language: "en",
  locale: { language: "en" },
  devices: input.devices || { device: { id: "device", name: "Car", identifiers: [["bavariandata", "WBS1"]] } },
  callService: () => new Promise(() => {}),
};

(() => {
if (input.editor) {
  Card._vehicleDevices = () => []; // the car picker reads a registry this fake has not
  const ed = Object.create(Editor.prototype);
  ed._hass = hass;
  ed._config = { type: "custom:bavariandata-card" };
  ed._form = {};
  const out = [];
  ed.dispatchEvent = (ev) => out.push(ev.detail.config);
  for (const value of input.editor) {
    ed._valueChanged({ stopPropagation() {}, detail: { value } });
  }
  process.stdout.write(JSON.stringify(out));
  return;
}

const card = Object.create(Card.prototype);
let listener = null;
const root = {
  innerHTML: "",
  querySelectorAll: () => [],
  querySelector: () => null,
  addEventListener: (type, fn) => { if (type === "click") listener = fn; },
};
card.shadowRoot = root;
card.attachShadow = () => root;
card._wireTaps = () => {};
card._styles = () => "";
card._resolveDeviceId = () => "device";
card._deviceEntities = (id) => (input.by_device ? input.by_device[id] || [] : Object.keys(input.states));
if (input.fuel) {
  card._fuel = { vin: "WBS1", trigger: "", data: input.fuel, loading: false };
  card._ensureFuel = () => {};
}
card.setConfig(input.config);
card.hass = hass;
const pages = [root.innerHTML];
const opened = [];
if (input.open) {
  // A tap on a small card, with the browser's navigation and more-info seen.
  globalThis.history = { pushState: (_s, _t, path) => opened.push(["navigate", path]) };
  globalThis.dispatchEvent = (ev) => opened.push(["event", ev.type]);
  card.dispatchEvent = (ev) => opened.push(["more-info", ev.detail.entityId]);
  card._openCar(input.open);
}
for (const step of input.taps || []) {
  // What a browser hands the listener: the tapped element (or its icon
  // inside it), then up through the shadow root.
  const el = step === "back"
    ? { hasAttribute: (a) => a === "data-room-back", getAttribute: () => null }
    : { hasAttribute: (a) => a === "data-room", getAttribute: () => step };
  const icon = { hasAttribute: () => false };
  listener({ composedPath: () => [icon, el, root, card], stopPropagation() {} });
  pages.push(root.innerHTML);
}
process.stdout.write(
  JSON.stringify({ pages, room: card._room, view: card._config.view || null, grid: card.getGridOptions(), opened })
);
})();
"""


def _run(payload: dict):
    result = subprocess.run(
        [NODE, "-e", _HARNESS, str(_CARD)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return json.loads(result.stdout)


def _card(states: dict, config: dict, taps: list | None = None, **extra) -> dict:
    states = {eid: {**st, "entity_id": eid} for eid, st in states.items()}
    return _run({"states": states, "config": config, "taps": taps or [], **extra})


def _rows(html: str) -> dict[str, str]:
    """Each room row's subtitle, by room, as plain text."""

    rows = {}
    for room, body in re.findall(r'data-room="(\w+)">(.*?)</button>', html, re.S):
        sub = re.search(r'class="room__s[^"]*">(.*?)</span>', body, re.S)
        rows[room] = sub.group(1).strip() if sub else ""
    return rows


def _sensor(descriptor: str, state: str, unit: str | None = None, **attrs) -> dict:
    attributes = {"descriptor": descriptor, **attrs}
    if unit:
        attributes["unit_of_measurement"] = unit
    return {"state": state, "attributes": attributes, "last_changed": "2026-11-09T11:00:00Z"}


MONTH = {
    "sensor.i5_energy_month": _sensor("charging_energy_month", "124.7", "kWh"),
    "sensor.i5_cost_month": _sensor("charging_cost_month", "23.72", "EUR"),
    "sensor.i5_distance_month": _sensor("driving_distance_month", "395", "km", trip_count=24),
    "sensor.i5_real_range": _sensor("real_range", "272", "km", consumption_kwh_per_100km=22.4),
}


def test_no_hub_no_rooms():
    page = _card({**I5, **MONTH}, {})["pages"][0]
    assert "data-room" not in page
    assert 'class="rooms"' not in page


def test_a_battery_car_lists_its_four_rooms_with_the_month_in_figures():
    rows = _rows(_card({**I5, **MONTH}, {"hub": True})["pages"][0])
    assert rows == {
        "charging": "124.7 kWh · 23.72 EUR this month",
        "trips": "395 km · 24 trips this month",
        "efficiency": "Real range ≈ 272 km · 22.4 kWh/100 km",
        "car": "All good",
    }


def test_a_month_without_cost_shows_the_energy_alone():
    states = {**I5, **MONTH, "sensor.i5_cost_month": _sensor("charging_cost_month", "0", "EUR")}
    assert _rows(_card(states, {"hub": True})["pages"][0])["charging"] == "124.7 kWh this month"


def test_a_petrol_car_gets_fuel_instead_of_charging_and_efficiency():
    fuel = {
        "refuels": [{"t": "2026-11-02T08:10:00Z", "km": 41000}],
        "average": {"l_per_100km": 6.4, "km": 1200},
    }
    rows = _rows(_card(M2, {"hub": True}, fuel=fuel)["pages"][0])
    assert list(rows) == ["fuel", "trips", "car"]
    assert rows["fuel"] == "Refueled Nov 2 · 6.4 l/100 km"


def test_a_plug_in_hybrid_lists_fuel_after_charging():
    rows = _rows(
        _card(
            PHEV, {"hub": True}, fuel={"refuels": [], "average": {"l_per_100km": 5.1, "km": 900}}
        )["pages"][0]
    )
    assert list(rows) == ["charging", "fuel", "trips", "efficiency", "car"]
    assert rows["fuel"] == "5.1 l/100 km"


def test_a_car_that_proved_no_drivetrain_lists_trips_and_car():
    assert list(_rows(_card(MINI, {"hub": True})["pages"][0])) == ["trips", "car"]


def test_a_charge_and_a_drive_show_live_in_their_rows():
    charging = {
        **I5,
        **MONTH,
        "sensor.i5_charging": _sensor(
            "vehicle.drivetrain.electricEngine.charging.status", "chargingactive"
        ),
        "sensor.i5_session": _sensor(
            "charged_energy_session", "16.2", "kWh", last_reset="2026-11-09T11:05:00Z"
        ),
    }
    page = _card(charging, {"hub": True})["pages"][0]
    assert _rows(page)["charging"] == "Charging now · 16.2 kWh so far"
    assert 'class="room__s room__s--live">Charging now' in page

    driving = {
        **I5,
        **MONTH,
        "binary_sensor.i5_trip": _sensor(
            "trip_in_progress", "on", started="2026-11-09T11:42:00Z", distance_km=23.4
        ),
    }
    assert _rows(_card(driving, {"hub": True})["pages"][0])["trips"] == "Trip in progress · 23.4 km"


def test_an_open_door_turns_the_car_row_red():
    states = {**I5, "sensor.i5_door": _sensor("vehicle.cabin.door.row1.passenger.isOpen", "on")}
    page = _card(states, {"hub": True})["pages"][0]
    assert _rows(page)["car"].startswith("Something's open · ")
    assert "room__s--alert" in page


def test_a_tap_opens_the_room_and_the_arrow_goes_back():
    out = _card({**I5, **MONTH}, {"hub": True, "title": "My i5"}, taps=["charging", "back"])
    overview, room, back = out["pages"]
    assert 'data-room="charging"' in overview
    # The room is the charging view, under its own title, with the way back
    # where its icon was.
    assert "data-room-back" in room
    assert "Charging history" in room
    assert "My i5" not in re.search(r'chead__title">(.*?)<', room).group(1)
    assert 'class="rooms"' not in room
    # And back on the overview, which draws itself afresh.
    assert back == overview
    assert out["room"] is None


def test_a_room_tap_does_nothing_without_the_hub_or_for_an_unknown_room():
    out = _card(I5, {}, taps=["charging"])
    assert out["room"] is None and out["view"] is None
    out = _card(I5, {"hub": True}, taps=["health"])
    assert out["room"] is None and out["view"] is None


def test_a_view_card_has_no_way_back():
    page = _card(I5, {"view": "charging", "hub": True})["pages"][0]
    assert "data-room-back" not in page


def test_the_editor_stores_the_hub_only_on_the_overview_and_only_when_on():
    configs = _run(
        {
            "states": {},
            "config": {},
            "editor": [
                {"cluster": "overview", "hub": True},
                {"cluster": "overview", "hub": False},
                {"cluster": "charging", "hub": True},
            ],
        }
    )
    assert configs[0] == {"type": "custom:bavariandata-card", "hub": True}
    assert "hub" not in configs[1]
    assert configs[2] == {"type": "custom:bavariandata-card", "view": "charging"}
