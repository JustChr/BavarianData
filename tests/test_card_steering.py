"""The closures view draws the driver's door on the driver's side.

BMW keys the side doors and windows by seat -- ``door.row1.driver``, not
``door.front.left`` -- so on a right-hand-drive car the driver's door is the
right front one. The card used to draw it on the left whatever the car, so a UK
owner opening their own door saw the passenger's door swing open on the
diagram. The side comes from BMW's basic data (``steering_side``, see
``steering.py``) or, where that is missing or wrong, the card's ``steering:``.

Runs the shipped card under Node, like ``test_card_drivetrain.py``; skipped
where Node is not installed.
"""

from __future__ import annotations

import json
import pathlib
import re
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
card._config = input.config;
card._hass = {
  states: input.states,
  language: "en",
  locale: { language: "en" },
  devices: { device: { name: "Car" } },
};
card.shadowRoot = { innerHTML: "" };
card._wireTaps = () => {};
card._styles = () => "";
card._renderClosures("device", Object.keys(input.states));
process.stdout.write(JSON.stringify({ html: card.shadowRoot.innerHTML }));
"""

DRIVER_DOOR = "binary_sensor.car_driver_door"
PASSENGER_DOOR = "binary_sensor.car_passenger_door"
REAR_PASSENGER_WINDOW = "sensor.car_rear_passenger_window"


def _states(steering_side: str | None) -> dict:
    basic = {"vehicle_basic_data": {"steering_side": steering_side}} if steering_side else {}

    def entity(entity_id: str, descriptor: str, state: str) -> dict:
        attributes = {"descriptor": descriptor, **basic}
        return {"entity_id": entity_id, "state": state, "attributes": attributes}

    return {
        DRIVER_DOOR: entity(DRIVER_DOOR, "vehicle.cabin.door.row1.driver.isOpen", "on"),
        PASSENGER_DOOR: entity(PASSENGER_DOOR, "vehicle.cabin.door.row1.passenger.isOpen", "off"),
        REAR_PASSENGER_WINDOW: entity(
            REAR_PASSENGER_WINDOW, "vehicle.cabin.window.row2.passenger.status", "open"
        ),
    }


def _render(steering_side: str | None, config: dict | None = None) -> str:
    result = subprocess.run(
        [NODE, "-e", _HARNESS, str(_CARD)],
        input=json.dumps(
            {"states": _states(steering_side), "config": {"cluster": "closures", **(config or {})}}
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return json.loads(result.stdout)["html"]


def _listed_as(html: str, entity_id: str) -> str:
    """The label the list under the diagram gives an open part."""

    match = re.search(
        rf'data-entity="{re.escape(entity_id)}">\s*<span class="item__name" title="([^"]+)"', html
    )
    assert match, f"{entity_id} is not listed as open"
    return match.group(1)


def _drawn_on_right(html: str, entity_id: str) -> bool:
    """Whether the diagram's tap zone for a door sits on the car's right side.

    The car is drawn once, for its left side; the right side is the same path
    mirrored, which is what the ``transform`` on the zone says.
    """

    match = re.search(
        rf'<path d="[^"]+" class="cldiag__hit" data-entity="{re.escape(entity_id)}"( transform)?',
        html,
    )
    assert match, f"{entity_id} has no tap zone on the diagram"
    return match.group(1) is not None


def test_left_hand_drive_puts_the_driver_on_the_left() -> None:
    html = _render("left")
    assert _listed_as(html, DRIVER_DOOR) == "Front left · Door"
    assert _listed_as(html, REAR_PASSENGER_WINDOW) == "Rear right · Window"
    assert not _drawn_on_right(html, DRIVER_DOOR)
    assert _drawn_on_right(html, PASSENGER_DOOR)


def test_right_hand_drive_puts_the_driver_on_the_right() -> None:
    html = _render("right")
    assert _listed_as(html, DRIVER_DOOR) == "Front right · Door"
    assert _listed_as(html, REAR_PASSENGER_WINDOW) == "Rear left · Window"
    assert _drawn_on_right(html, DRIVER_DOOR)
    assert not _drawn_on_right(html, PASSENGER_DOOR)


def test_a_car_that_has_not_said_keeps_left_hand_drive() -> None:
    html = _render(None)
    assert _listed_as(html, DRIVER_DOOR) == "Front left · Door"


@pytest.mark.parametrize(
    "basic, wanted, label",
    [
        ("left", "right", "Front right · Door"),
        ("right", "left", "Front left · Door"),
        (None, "right", "Front right · Door"),
    ],
)
def test_the_cards_steering_option_overrides_basic_data(basic, wanted, label) -> None:
    html = _render(basic, {"steering": wanted})
    assert _listed_as(html, DRIVER_DOOR) == label
