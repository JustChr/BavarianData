"""The closures view's side windows: what an open one is called and where it is drawn.

A side window that is partly down was listed as "Tilted", the sunroof's word,
and its amber glow stayed in the body when its door swung open, so an open
window on an open door lit up the empty gap (issue #100). The pane is in the
door: with the door open it is drawn turned on the door's hinge.

Runs the shipped card under Node; skipped where Node is not installed.
"""

from __future__ import annotations

import json
import re
import subprocess

import pytest

from tests.test_card_steering import _CARD, _HARNESS, NODE, _listed_as

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

DOOR = "binary_sensor.car_driver_door"
WINDOW = "sensor.car_driver_window"
SUNROOF = "sensor.car_sunroof"
# The left front door's hinge on the BMW sedan drawing.
HINGE = "rotate(30 186 466)"


def _render(door: str, window: str, sunroof: str = "closed") -> str:
    def entity(entity_id: str, descriptor: str, state: str) -> dict:
        return {"entity_id": entity_id, "state": state, "attributes": {"descriptor": descriptor}}

    states = {
        DOOR: entity(DOOR, "vehicle.cabin.door.row1.driver.isOpen", door),
        WINDOW: entity(WINDOW, "vehicle.cabin.window.row1.driver.status", window),
        SUNROOF: entity(SUNROOF, "vehicle.cabin.sunroof.overallStatus", sunroof),
    }
    result = subprocess.run(
        [NODE, "-e", _HARNESS, str(_CARD)],
        input=json.dumps({"states": states, "config": {"cluster": "closures"}}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return json.loads(result.stdout)["html"]


def _value(html: str, entity_id: str) -> str:
    match = re.search(
        rf'data-entity="{re.escape(entity_id)}">.*?<span class="item__val"[^>]*>([^<]+)<',
        html,
        re.S,
    )
    assert match, f"{entity_id} is not listed as open"
    return match.group(1)


def _window_glow(html: str) -> str:
    """The lit group holding the window's lowered-pane edge."""

    match = re.search(
        r'<g class="cldiag__lit"[^>]*>((?:(?!<g class="cldiag__lit").)*?cldiag__drop)', html, re.S
    )
    assert match, "the open window is not lit"
    return match.group(1)


def test_a_partly_open_window_is_partially_open_not_tilted():
    html = _render("off", "intermediate")
    assert _listed_as(html, WINDOW) == "Front left · Window"
    assert _value(html, WINDOW) == "Partially open"


def test_a_tilted_sunroof_is_still_tilted():
    assert _value(_render("off", "closed", sunroof="intermediate"), SUNROOF) == "Tilted"


def test_an_open_window_on_an_open_door_swings_out_with_it():
    assert f'<g transform="{HINGE}">' in _window_glow(_render("on", "open"))


def test_an_open_window_on_a_closed_door_stays_in_place():
    assert "transform" not in _window_glow(_render("off", "open"))
