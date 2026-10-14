"""The card with Configure -> Vehicle image set to BMW's configurator.

The image entity then carries ``configurator_spin`` (a sprite of the whole
turntable behind HA's login) and ``paint_color``. The card signs the sprite's
path once and shows it as a drag-to-turn hero, and colours the top-down car in
the tyre and closures views with the paint. Without those attributes nothing
changes: the golden renders in test_card_snapshots.py pin that.

Runs the shipped card under Node; skipped where Node is not installed.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from .test_card_drivetrain import I5
from .test_card_shape import CAR as MINI_WITH_TYRES
from .test_card_snapshots import _CARD, _HARNESS, NODE, _render

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

SPIN = {
    "path": "/api/bavariandata/spin/0123456789abcdef01234567/20261014",
    "columns": 6,
    "rows": 6,
    "frames": 36,
    "frame_width": 640,
    "frame_height": 360,
}
PAINT = "#909799"


def _image(**attributes) -> dict:
    return {
        "state": "2026-10-14T12:00:00+00:00",
        "attributes": {"entity_picture": "/api/image_proxy/image.car?token=t", **attributes},
    }


# The same harness, with a callWS that signs paths the way HA does, and the
# output written only once the card has had the chance to repaint.
_SIGNING = _HARNESS.replace(
    "  callService: () => new Promise(() => {}),",
    "  callService: () => new Promise(() => {}),\n"
    "  callWS: (msg) => Promise.resolve({ path: msg.path + '?authSig=signed' }),",
).replace(
    "try { card._render(); } catch (e) { error = String((e && e.message) || e); }\n"
    "process.stdout.write(JSON.stringify({ html: card.shadowRoot.innerHTML, error }));",
    "try { card._render(); } catch (e) { error = String((e && e.message) || e); }\n"
    "setTimeout(() => process.stdout.write("
    "JSON.stringify({ html: card.shadowRoot.innerHTML, error })), 0);",
)
assert _SIGNING != _HARNESS


def _render_signed(states: dict, config: dict) -> str:
    states = {entity_id: {**st, "entity_id": entity_id} for entity_id, st in states.items()}
    result = subprocess.run(
        [NODE, "-e", _SIGNING, str(_CARD)],
        input=json.dumps({"states": states, "config": config}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    payload = json.loads(result.stdout)
    assert payload["error"] is None, payload["error"]
    return payload["html"]


def test_the_hero_turns_the_configurator_spin_once_signed():
    html = _render_signed({**I5, "image.car": _image(configurator_spin=SPIN)}, {})
    assert 'class="hero__spin"' in html
    assert f"url('{SPIN['path']}?authSig=signed')" in html
    assert "background-size:600% 600%" in html
    # Frame 4, BMW's front three-quarter view: column 4 of 6, first row.
    assert "background-position:80% 0%" in html
    assert 'class="hero__img"' not in html


def test_until_signed_the_hero_shows_the_entity_picture():
    html = _render({**I5, "image.car": _image(configurator_spin=SPIN)}, {})
    assert 'class="hero__img"' in html
    assert "hero__spin" not in html


def test_without_a_spin_the_hero_is_the_plain_picture():
    html = _render_signed({**I5, "image.car": _image()}, {})
    assert 'class="hero__img"' in html
    assert "hero__spin" not in html


@pytest.mark.parametrize("view", [{"cluster": "tire"}, {"cluster": "closures"}])
def test_the_top_down_car_wears_the_paint(view):
    painted = _render({**MINI_WITH_TYRES, "image.car": _image(paint_color=PAINT)}, view)
    plain = _render({**MINI_WITH_TYRES, "image.car": _image()}, view)
    # #909799 shaded: 12 % towards black at the edges, 18 % towards white mid.
    assert 'stop-color="#7f8587"' in painted
    assert 'stop-color="#a4aaab"' in painted
    assert 'stop-color="#bcc2ca"' not in painted
    assert 'stop-color="#bcc2ca"' in plain


@pytest.mark.parametrize("junk", ["red", "#12345", "#1234567", "url(x)", 7, None])
def test_a_malformed_paint_is_ignored(junk):
    html = _render({**MINI_WITH_TYRES, "image.car": _image(paint_color=junk)}, {"cluster": "tire"})
    assert 'stop-color="#bcc2ca"' in html


# --- Lights, night, badges, interior, top view, equipment --------------------

LIGHTS = {"state": "on", "attributes": {"descriptor": "vehicle.body.lights.isRunningOn"}}
SPIN_ALL = {**SPIN, "lights_path": SPIN["path"] + "-lights", "night_path": SPIN["path"] + "-night"}
PANO = {
    "path": "/api/bavariandata/spin/0123456789abcdef01234567_interior/1",
    "columns": 3,
    "rows": 2,
    "faces": ["down", "front", "back", "up", "left", "right"],
}


def _hero(states: dict) -> str:
    html = _render_signed(states, {})
    return html.split('<div class="band">')[0]


def test_the_spin_follows_the_lights_and_the_night():
    day = _hero(
        {
            **I5,
            "image.car": _image(configurator_spin=SPIN_ALL),
            "sun.sun": {"state": "above_horizon", "attributes": {}},
        }
    )
    assert f"url('{SPIN['path']}?authSig=signed')" in day
    lights = _hero(
        {**I5, "image.car": _image(configurator_spin=SPIN_ALL), "binary_sensor.lights": LIGHTS}
    )
    assert f"url('{SPIN['path']}-lights?authSig=signed')" in lights
    night = _hero(
        {
            **I5,
            "image.car": _image(configurator_spin=SPIN_ALL),
            "binary_sensor.lights": LIGHTS,
            "sun.sun": {"state": "below_horizon", "attributes": {}},
        }
    )
    assert f"url('{SPIN['path']}-night?authSig=signed')" in night  # night wins over lights


def test_an_open_door_and_an_unlocked_car_show_on_the_photo():
    from .test_card_drivetrain import _sensor

    states = {
        **I5,
        "image.car": _image(configurator_spin=SPIN),
        "sensor.door": _sensor("vehicle.cabin.door.row1.driver.isOpen", "on"),
        "sensor.lock": _sensor("vehicle.cabin.door.status", "UNLOCKED"),
    }
    hero = _hero(states)
    assert "Door open" in hero and "Unlocked" in hero
    assert 'class="hero__inset"' in hero and "hero--inset" in hero
    closed = _hero(
        {
            **I5,
            "image.car": _image(configurator_spin=SPIN),
            "sensor.door": _sensor("vehicle.cabin.door.row1.driver.isOpen", "off"),
        }
    )
    assert "hero__chip" not in closed and "hero__inset" not in closed


def test_badges_need_the_configurator_photo():
    from .test_card_drivetrain import _sensor

    html = _render_signed(
        {
            **I5,
            "image.car": _image(),
            "sensor.door": _sensor("vehicle.cabin.door.row1.driver.isOpen", "on"),
        },
        {},
    )
    assert "hero__chip" not in html


def test_the_interior_switch_appears_with_a_panorama():
    hero = _hero({**I5, "image.car": _image(configurator_spin=SPIN, configurator_interior=PANO)})
    assert "data-hero-mode" in hero and "mdi:car-seat" in hero
    assert "data-hero-mode" not in _hero({**I5, "image.car": _image(configurator_spin=SPIN)})


def _hero_with_storage(states: dict, stored: object) -> str:
    """The hero, rendered in a browser whose storage holds ``stored`` for the
    image entity (raw text, so junk can be tested too)."""
    stub = (
        "const __stored = "
        + json.dumps({"bavariandata-hero:image.car": stored})
        + ";\nObject.defineProperty(globalThis, 'localStorage', { configurable: true, "
        "value: { getItem: (k) => (k in __stored ? __stored[k] : null), setItem() {} } });\n"
    )
    states = {entity_id: {**st, "entity_id": entity_id} for entity_id, st in states.items()}
    result = subprocess.run(
        [NODE, "-e", stub + _SIGNING, str(_CARD)],
        input=json.dumps({"states": states, "config": {}}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    payload = json.loads(result.stdout)
    assert payload["error"] is None, payload["error"]
    return payload["html"].split('<div class="band">')[0]


def test_the_hero_comes_back_as_the_viewer_left_it():
    states = {**I5, "image.car": _image(configurator_spin=SPIN, configurator_interior=PANO)}
    # Frame 9: column 3 of 6, second row.
    turned = _hero_with_storage(states, json.dumps({"frame": 9, "mode": "exterior"}))
    assert "background-position:60% 20%" in turned
    inside = _hero_with_storage(
        states, json.dumps({"frame": 9, "mode": "interior", "yaw": 30, "pitch": -200})
    )
    assert 'class="pano"' in inside and "hero__spin" not in inside
    # A look past straight down is clamped, as dragging would.
    assert "rotateX(-80deg) rotateY(30deg)" in inside


@pytest.mark.parametrize("junk", ["not json", "[1, 2]", json.dumps({"frame": "x", "mode": "up"})])
def test_junk_in_storage_shows_the_default_view(junk):
    hero = _hero_with_storage({**I5, "image.car": _image(configurator_spin=SPIN)}, junk)
    assert "background-position:80% 0%" in hero


def test_the_tire_view_uses_bmws_top_view_where_there_is_one():
    top = {"path": "/api/bavariandata/spin/0123456789abcdef01234567_top/1"}
    html = _render_signed(
        {**MINI_WITH_TYRES, "image.car": _image(configurator_top=top)}, {"cluster": "tire"}
    )
    assert f'src="{top["path"]}?authSig=signed"' in html and "carsvg" not in html
    assert "carsvg" in _render({**MINI_WITH_TYRES, "image.car": _image()}, {"cluster": "tire"})


EQUIPMENT = [
    {"code": "P0C4P", "name": "M Brooklyn Grey metallic", "kind": "paint", "shown": True},
    {"code": "S0337", "name": "M Sport package", "kind": "package", "shown": True},
    {"code": "S06F4", "name": "Bowers & Wilkins <Surround>", "kind": "other", "shown": True},
    {"code": "S08R9", "name": "Refrigerant", "kind": "other", "shown": False},
]


def test_the_equipment_view_groups_and_folds_the_technical_codes():
    html = _render({**I5, "image.car": _image(equipment=EQUIPMENT)}, {"view": "equipment"})
    looks, packages, features = (
        html.index(t) for t in ("Paint, wheels & trim", "Packages", "Features")
    )
    assert (
        looks
        < html.index("M Brooklyn Grey metallic")
        < packages
        < html.index("M Sport package")
        < features
    )
    assert "Bowers &amp; Wilkins &lt;Surround&gt;" in html  # escaped
    assert "Technical codes (1)" in html and html.index("Refrigerant") > html.index("<details")
    assert "3 items" in html


def test_the_equipment_view_says_how_to_turn_it_on():
    html = _render({**I5, "image.car": _image()}, {"view": "equipment"})
    assert "Vehicle image" in html and "eq__row" not in html
