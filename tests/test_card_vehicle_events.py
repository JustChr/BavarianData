"""The card binds to a car, and its Vehicle events view shows Check Control.

Two reports from the maintainer's own dashboard (2026-09-14):

* **The debug device.** Every account gets a "CarData Debug Device" holding the
  stream/quota diagnostics. It is a ``bavariandata`` device like the cars, so
  HA's integration-filtered device picker offered it, and -- its quota sensor
  registered first -- the card picker preselected it. A card bound to it shows
  nothing. A car's device identifier is its VIN; the debug device's is its own
  config entry id, which is what tells them apart.
* **"No events" with a warning on the dash.** The car reported "washer fluid
  level is low", yet ``cluster: events`` said there were no events: that view
  listed BMW's *Vehicle events* cluster, which holds only two teleservice
  timestamps, while Check Control messages are filed under usage-based data.

Runs the shipped card under Node on synthetic ``hass`` objects, like
``test_card_soc_pick.py``. The Check Control payload is the live wire shape.
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
const [Card, Editor] = new Function(src + "\nreturn [BavarianDataCard, BavarianDataCardEditor];")();
const { op, hass, config, expand } = JSON.parse(fs.readFileSync(0, "utf8"));
let out;
if (op === "stub") {
  out = Card.getStubConfig(hass);
} else if (op === "resolve") {
  const card = Object.create(Card.prototype);
  card._hass = hass;
  card._config = config;
  out = card._resolveDeviceId() || null;
} else if (op === "render") {
  const card = Object.create(Card.prototype);
  card._hass = hass;
  card._config = config;
  card.shadowRoot = { innerHTML: "", querySelectorAll: () => [] };
  card._render();
  // What a tap does: on a message row, or on the "Earlier messages" fold.
  for (const key of expand) key === "earlier" ? card._toggleCcEarlier() : card._toggleCc(key);
  out = card.shadowRoot.innerHTML;
} else if (op === "editor") {
  const editor = Object.create(Editor.prototype);
  editor._hass = hass;
  editor._config = config;
  out = editor._schema().find((field) => field.name === "device").selector;
}
process.stdout.write(JSON.stringify(out));
"""

ENTRY = "01KYDMS7JW5ABEQAH7HXQAQ3F7"
DEBUG = "dev-debug"
CAR = "dev-car"
CC = "sensor.i5_check_control"

WASHER = (
    "The washer fluid level is low in the window washer reservoir. Please add "
    "washer fluid as soon as possible. See Owner´s Manual for more information."
)
# Exactly what the i5 carried on 2026-09-14.
_WASHER_ITEM = {
    "date": None,
    "description": None,
    "id": 164,
    "messageType": "CCM",
    "status": "NULL",
    "title": None,
    "text": WASHER,
    "unitOfLengthRemaining": "19599",
}


def _hass(
    items: list | None = None,
    *,
    cc_state: str = "1",
    teleservice: bool = False,
    car: bool = True,
    resolved: list | None = None,
) -> dict:
    states = {
        "sensor.cardata_api_quota": {
            "entity_id": "sensor.cardata_api_quota",
            "state": "48",
            "attributes": {},
        },
    }
    entities = {
        # Registry order: the debug device's quota sensor comes first, which is
        # how the debug device got preselected.
        "sensor.cardata_api_quota": {
            "entity_id": "sensor.cardata_api_quota",
            "platform": "bavariandata",
            "device_id": DEBUG,
            "entity_category": "diagnostic",
        },
    }
    if car:
        mileage = "sensor.i5_mileage"
        states[mileage] = {
            "entity_id": mileage,
            "state": "12345",
            "attributes": {
                "descriptor": "vehicle.vehicle.travelledDistance",
                "unit_of_measurement": "km",
                "cluster": "status",
                "friendly_name": "i5 eDrive40 Mileage",
            },
        }
        entities[mileage] = {"entity_id": mileage, "platform": "bavariandata", "device_id": CAR}
    if items is not None:
        states[CC] = {
            "entity_id": CC,
            "state": cc_state,
            "attributes": {
                "descriptor": "vehicle.status.checkControlMessages",
                "cluster": "usage",
                "category": "Service",
                "friendly_name": "i5 eDrive40 Check Control messages",
                "items": items,
                **({"resolved": resolved} if resolved is not None else {}),
            },
        }
        entities[CC] = {"entity_id": CC, "platform": "bavariandata", "device_id": CAR}
    if teleservice:
        tid = "sensor.i5_last_teleservice_report"
        states[tid] = {
            "entity_id": tid,
            "state": "2026-09-01T08:00:00+00:00",
            "attributes": {
                "descriptor": "vehicle.channel.teleservice.lastTeleserviceReportTime",
                "cluster": "events",
                "friendly_name": "i5 eDrive40 Last teleservice report",
            },
        }
        entities[tid] = {"entity_id": tid, "platform": "bavariandata", "device_id": CAR}
    return {
        "language": "en",
        "locale": {"language": "en", "number_format": "language"},
        "states": states,
        "entities": entities,
        "devices": {
            DEBUG: {
                "id": DEBUG,
                "name": "CarData Debug Device",
                "identifiers": [["bavariandata", ENTRY]],
                "config_entries": [ENTRY],
            },
            CAR: {
                "id": CAR,
                "name": "i5 eDrive40",
                "identifiers": [["bavariandata", "WBYTESTVIN0000001"]],
                "config_entries": [ENTRY],
            },
        },
    }


def _run(op: str, hass: dict, config: dict | None = None, expand: tuple[str, ...] = ()):
    result = subprocess.run(
        [NODE, "-e", _HARNESS, str(_CARD)],
        input=json.dumps({"op": op, "hass": hass, "config": config or {}, "expand": list(expand)}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return json.loads(result.stdout)


# ---- the car, never the debug device ---------------------------------------


def test_card_picker_prefills_the_car_not_the_debug_device():
    assert _run("stub", _hass([_WASHER_ITEM])) == {"device": CAR}


def test_card_picker_prefills_nothing_without_a_car():
    assert _run("stub", _hass(None, car=False)) == {}


def test_a_card_saved_with_the_debug_device_shows_the_car():
    assert _run("resolve", _hass([_WASHER_ITEM]), {"device": DEBUG}) == CAR


def test_a_card_pinned_to_a_car_keeps_it():
    assert _run("resolve", _hass([_WASHER_ITEM]), {"device": CAR}) == CAR


def test_editor_lists_cars_only():
    selector = _run("editor", _hass([_WASHER_ITEM]))
    values = [option["value"] for option in selector["select"]["options"]]
    assert values == [CAR]


# ---- Vehicle events shows Check Control ------------------------------------


def test_events_view_shows_the_washer_fluid_warning():
    html = _run("render", _hass([_WASHER_ITEM]), {"cluster": "events"})
    assert "washer fluid level is low" in html
    assert "No <b>" not in html  # the "No Vehicle events entities" empty state


def test_events_view_says_when_there_are_no_messages():
    html = _run("render", _hass([], cc_state="0"), {"cluster": "events"})
    assert "No Check Control messages reported." in html


def test_events_view_keeps_the_teleservice_timestamps():
    html = _run("render", _hass([_WASHER_ITEM], teleservice=True), {"cluster": "events"})
    assert "washer fluid level is low" in html
    assert "sensor.i5_last_teleservice_report" in html


def test_events_view_without_check_control_keeps_the_old_empty_state():
    html = _run("render", _hass(None), {"cluster": "events"})
    assert "No <b>" in html


def test_check_control_text_is_escaped():
    item = dict(_WASHER_ITEM, text='<img src=x onerror="alert(1)">')
    html = _run("render", _hass([item]), {"cluster": "events"})
    assert "<img src=x" not in html
    assert "&lt;img" in html


def test_other_cluster_views_do_not_grow_check_control():
    html = _run("render", _hass([_WASHER_ITEM]), {"cluster": "usage"})
    assert "Check Control messages</div>" not in html


# ---- tapping a message shows its details -----------------------------------


def test_a_message_is_collapsed_until_tapped():
    html = _run("render", _hass([_WASHER_ITEM]), {"cluster": "events"})
    assert '<div class="chg__detail">' not in html
    assert 'data-cc="164"' in html


def test_tapping_a_message_shows_its_details():
    html = _run("render", _hass([_WASHER_ITEM]), {"cluster": "events"}, expand=("164",))
    assert '<div class="chg__detail">' in html
    # unitOfLengthRemaining is the odometer when the car last showed it.
    assert "Last shown at" in html and "19,599 km" in html
    assert "CCM 164" in html
    assert "NULL" not in html  # BMW's placeholder status is not a detail


def test_tapping_an_open_message_closes_it():
    html = _run("render", _hass([_WASHER_ITEM]), {"cluster": "events"}, expand=("164", "164"))
    assert '<div class="chg__detail">' not in html


def test_placeholder_mileage_is_not_shown():
    item = dict(_WASHER_ITEM, unitOfLengthRemaining="-", description="-")
    html = _run("render", _hass([item]), {"cluster": "events"}, expand=("164",))
    assert "Last shown at" not in html
    assert '<p class="cc__text">-</p>' not in html


def test_detail_text_is_escaped():
    item = dict(_WASHER_ITEM, title="Washer fluid", description="<b>x</b>")
    html = _run("render", _hass([item]), {"cluster": "events"}, expand=("164",))
    assert "<b>x</b>" not in html
    assert "&lt;b&gt;x" in html


# ---- a message the car no longer reports ------------------------------------
# The i5, 2026-09-27: washer fluid topped up, BMW's next reply held no messages.
# The warning leaves the list, folded away under "Earlier messages".

_WASHER_GONE = dict(
    _WASHER_ITEM,
    unitOfLengthRemaining="20039",
    last_reported="2026-09-26T10:54:19.664Z",
    resolved_at="2026-09-27T17:00:00+00:00",
)


def test_a_cleared_message_is_not_shown_as_current():
    html = _run("render", _hass([], cc_state="0", resolved=[_WASHER_GONE]), {"cluster": "events"})
    assert "No Check Control messages reported." in html
    assert "washer fluid level is low" not in html
    assert "Earlier messages (1)" in html
    assert "0 values" in html  # the header counts current messages only


def test_the_earlier_fold_opens_on_tap():
    html = _run(
        "render",
        _hass([], cc_state="0", resolved=[_WASHER_GONE]),
        {"cluster": "events"},
        expand=("earlier",),
    )
    assert "washer fluid level is low" in html
    assert "mdi:check-circle-outline" in html
    assert "mdi:alert-circle-outline" not in html


def test_a_cleared_message_shows_when_it_was_last_reported():
    key = "past:164:2026-09-27T17:00:00+00:00"
    html = _run(
        "render",
        _hass([], cc_state="0", resolved=[_WASHER_GONE]),
        {"cluster": "events"},
        expand=("earlier", key),
    )
    assert "Last reported" in html and "No longer reported" in html
    assert "Sent by the car" not in html  # that stamp belongs to the current list
    assert "20,039 km" in html


def test_no_fold_without_history():
    html = _run("render", _hass([_WASHER_ITEM], resolved=[]), {"cluster": "events"})
    assert "Earlier messages" not in html


def test_a_cleared_message_is_escaped():
    item = dict(_WASHER_GONE, text='<img src=x onerror="alert(1)">', resolved_at='"><b>')
    html = _run(
        "render",
        _hass([], cc_state="0", resolved=[item]),
        {"cluster": "events"},
        expand=("earlier",),
    )
    assert "<img src=x" not in html
    assert '"><b>' not in html
