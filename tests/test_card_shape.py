"""The top-down car is drawn in the shape of the owner's car where the card has one.

The tyre and closures views drew a BMW saloon for everyone; a MINI Countryman
owner (issue #100) drew one for their car. BMW's basic data names the model
(``model_name``), so the card picks the shape from it, and ``shape:`` overrides
the pick either way. Every shape uses the same frame and parts, so the overlays
(doors swinging open, glowing glass, the padlock) need nothing per shape -- the
golden renders below pin that they still land on the drawing.

Runs the shipped card under Node; skipped where Node is not installed.
"""

from __future__ import annotations

import pytest

from .test_card_drivetrain import MINI, _sensor
from .test_card_snapshots import NODE, _render

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

# The first move of each shape's body outline, enough to tell them apart.
SALOON = 'd="M474 12 C420 12'
COUNTRYMAN = 'd="M474 30 C372 30'

VIEWS = {"tire": {"cluster": "tire"}, "closures": {"cluster": "closures"}}


def _tyre(axle: str, side: str, kpa: str) -> dict:
    st = _sensor(f"vehicle.chassis.axle.{axle}.wheel.{side}.tire.pressure", kpa, "kPa")
    attributes = {**st["attributes"], "cluster": "tire", "tire_axle": axle, "tire_side": side}
    return {**st, "attributes": {**attributes, "tire_metric": "pressure"}}


# The MINI from issue #23, with the driver's door and a window open so the
# overlays are drawn too, and all four tyres so the tyre view draws its car.
CAR = {
    **MINI,
    "sensor.mini_driver_door": _sensor("vehicle.cabin.door.row1.driver.isOpen", "on"),
    "sensor.mini_window": _sensor("vehicle.cabin.window.row1.driver.status", "open"),
    **{
        f"sensor.mini_tyre_{axle}_{side}": _tyre(axle, side, "230")
        for axle in ("row1", "row2")
        for side in ("left", "right")
    },
}


def _with_model(states: dict, model_name: str) -> dict:
    return {
        entity_id: {
            **st,
            "attributes": {
                **st.get("attributes", {}),
                "vehicle_basic_data": {"model_name": model_name},
            },
        }
        for entity_id, st in states.items()
    }


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_the_countryman_renders_what_it_rendered_before(view: str, snapshot) -> None:
    assert _render(CAR, {**VIEWS[view], "shape": "countryman"}) == snapshot


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_a_countryman_is_drawn_as_one(view: str) -> None:
    html = _render(_with_model(CAR, "JCW Countryman ALL4"), VIEWS[view])
    assert COUNTRYMAN in html
    assert SALOON not in html


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_any_other_car_keeps_the_saloon(view: str) -> None:
    html = _render(_with_model(CAR, "i5 eDrive40"), VIEWS[view])
    assert SALOON in html
    assert COUNTRYMAN not in html


def test_shape_overrides_the_model() -> None:
    html = _render(_with_model(CAR, "JCW Countryman ALL4"), {**VIEWS["closures"], "shape": "sedan"})
    assert SALOON in html
