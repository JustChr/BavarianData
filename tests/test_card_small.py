"""The small sizes: `view: glance`, `view: row` and `view: garage`.

They read a car the way the overview does (the same meter, range and top
line), so each test here pins what the small card shows of that reading, how
it sits in a sections dashboard, and what a tap opens. Drawn on the hub's
harness (test_card_hub.py), which renders through the card's own dispatch.
"""

from __future__ import annotations

import re

import pytest

from .test_card_drivetrain import I5, M2
from .test_card_hub import NODE, _card, _run, _sensor

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

CHARGING = {
    **I5,
    "sensor.i5_charging": _sensor(
        "vehicle.drivetrain.electricEngine.charging.status", "chargingactive"
    ),
    "sensor.i5_power": _sensor("vehicle.powertrain.electric.battery.charging.power", "7400", "W"),
    "sensor.i5_lock": _sensor("vehicle.cabin.door.lock.status", "secured"),
}


def _text(html: str, cls: str) -> str:
    m = re.search(r'class="%s[^"]*">(.*?)</span>' % re.escape(cls), html, re.S)
    return m.group(1).strip() if m else ""


def test_the_glance_tile_shows_the_charge_its_power_and_the_top_line():
    page = _card(CHARGING, {"view": "glance"})["pages"][0]
    assert _text(page, "sm__name") == "Car"
    assert _text(page, "sm__live") == "7.4 kW"
    assert "72<i>%</i>" in page
    assert _text(page, "sm__side") == "379 km"
    assert 'class="sm__vd sm__vd--live">Charging' in page
    assert 'data-open="sensor.i5_soc"' in page
    # Locked and plugged in, as glyphs beside the line.
    assert 'class="sm__glyph sm__glyph--ok" icon="mdi:lock"' in page
    assert 'icon="mdi:power-plug"' in page


def test_a_parked_glance_has_no_power():
    page = _card(I5, {"view": "glance"})["pages"][0]
    assert "sm__live" not in page
    assert _text(page, "sm__vd") == "Ready to go"


def test_the_row_names_the_car_beside_the_top_line():
    page = _card(I5, {"view": "row", "title": "My i5"})["pages"][0]
    assert _text(page, "sm__vd") == "Ready to go"
    assert _text(page, "sm__sub").startswith("My i5")


def test_the_garage_lists_every_car_by_its_own_name():
    states = {**I5, **M2}
    devices = {
        "a": {"id": "a", "name": "i5 eDrive40", "identifiers": [["bavariandata", "WBA1"]]},
        "b": {"id": "b", "name": "M2", "identifiers": [["bavariandata", "WBA2"]]},
        # The account itself is a device too, and no car.
        "acct": {
            "id": "acct",
            "name": "Account",
            "identifiers": [["bavariandata", "e1"]],
            "config_entries": ["e1"],
        },
    }
    out = _card(
        states,
        {"view": "garage", "title": "Our cars"},
        devices=devices,
        by_device={"a": list(I5), "b": list(M2)},
    )
    page = out["pages"][0]
    assert _text(page, "chead__title") == "Our cars"
    assert _text(page, "chead__sub") == "2 cars"
    names = re.findall(r'class="sm__name">(.*?)<', page)
    # The card's title names the garage, never a car.
    assert names == ["i5 eDrive40", "M2"]
    assert 'data-open="sensor.i5_soc"' in page
    assert 'data-open="sensor.m2_remaining_fuel"' in page


def test_an_empty_garage_says_there_is_no_car():
    out = _card({}, {"view": "garage"}, devices={})
    assert "msg__title" in out["pages"][0]


@pytest.mark.parametrize(
    ("view", "grid"),
    [
        ("glance", {"columns": 6, "min_columns": 4}),
        ("row", {"columns": 12, "min_columns": 6}),
        ("garage", {"columns": 12, "min_columns": 6}),
        ("charging", {"columns": 12, "min_columns": 6}),
    ],
)
def test_each_view_has_its_place_in_a_sections_dashboard(view: str, grid: dict):
    assert _card(I5, {"view": view})["grid"] == grid


def test_a_tap_opens_the_navigation_path_or_else_the_car():
    out = _card(I5, {"view": "glance", "navigation_path": "/car-hub/0"}, open="sensor.i5_soc")
    assert out["opened"] == [["navigate", "/car-hub/0"], ["event", "location-changed"]]
    out = _card(I5, {"view": "glance"}, open="sensor.i5_soc")
    assert out["opened"] == [["more-info", "sensor.i5_soc"]]


def test_the_editor_keeps_the_navigation_path_only_on_the_small_cards():
    configs = _run(
        {
            "states": {},
            "config": {},
            "editor": [
                {"cluster": "glance", "navigation_path": "/car-hub/0"},
                {"cluster": "overview", "navigation_path": "/car-hub/0"},
            ],
        }
    )
    assert configs[0] == {
        "type": "custom:bavariandata-card",
        "view": "glance",
        "navigation_path": "/car-hub/0",
    }
    assert "navigation_path" not in configs[1]
