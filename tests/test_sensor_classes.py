"""The step from descriptor metadata to a sensor entity's classes and unit.

``test_catalogue.py`` checks what the generator *writes*; this checks what the
sensor platform *does* with it. The gap between the two is issue #25: v0.9.10
took the battery device class off the fuel tank level and kept its
``state_class: measurement`` on purpose, a test confirmed the metadata still
said so -- and the entity lost its state class anyway, because the platform
only read it when a device class was present. Home Assistant stopped the tank's
long-term statistics and offered to delete them.

``sensor.py`` imports Home Assistant and cannot be loaded here, so the decision
lives in ``sensor_classes`` and is checked against every descriptor; the last
test holds the platform to going through it.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from .conftest import load_module

_PKG = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "bavariandata"

META = load_module("descriptor_metadata").DESCRIPTOR_META
sensor_classes = load_module("sensor_classes").sensor_classes

_WITH_STATE_CLASS = sorted(
    d for d, m in META.items() if m.get("state_class") and not m.get("options")
)


@pytest.mark.parametrize("descriptor", _WITH_STATE_CLASS)
def test_every_state_class_in_the_metadata_reaches_the_entity(descriptor: str) -> None:
    meta = META[descriptor]
    classes = sensor_classes(meta)
    assert classes.state_class == meta["state_class"], (
        f"{descriptor}: the metadata says state_class={meta['state_class']!r} but "
        f"the entity would get {classes.state_class!r} -- Home Assistant then "
        "drops its long-term statistics (issue #25)."
    )
    if meta.get("unit"):
        # Statistics are kept in one unit; a sensor that has them must not take
        # a different unit string off the stream.
        assert classes.unit == meta["unit"], descriptor


def test_the_fuel_tank_level_keeps_its_statistics() -> None:
    """Issue #25 itself: no device class, but still a ``%`` measurement."""

    classes = sensor_classes(META["vehicle.drivetrain.fuelSystem.level"])
    assert classes.device_class is None
    assert (classes.state_class, classes.unit) == ("measurement", "%")


def test_device_classed_sensors_get_exactly_their_metadata() -> None:
    for descriptor, meta in META.items():
        if not meta.get("device_class"):
            continue
        classes = sensor_classes(meta)
        assert (classes.device_class, classes.state_class, classes.unit, classes.options) == (
            meta["device_class"],
            meta.get("state_class"),
            meta.get("unit") or None,
            (),
        ), descriptor


def test_enum_sensors_get_neither_a_state_class_nor_a_unit() -> None:
    enums = [d for d, m in META.items() if m.get("options") and not m.get("device_class")]
    assert enums
    for descriptor in enums:
        classes = sensor_classes(META[descriptor])
        assert classes.is_enum, descriptor
        assert classes.state_class is None and classes.unit is None, descriptor
    # Defensive: Home Assistant rejects a state class on an enum sensor even if
    # the metadata ever carried one.
    classes = sensor_classes({"options": ["a", "b"], "state_class": "measurement", "unit": "%"})
    assert (classes.state_class, classes.unit) == (None, None)


def test_classless_sensors_keep_the_unit_from_the_stream() -> None:
    """Hours, weeks, stars, euros: no class, so no unit is pinned -- as before."""

    classless = [
        d
        for d, m in META.items()
        if m.get("unit") and not (m.get("device_class") or m.get("state_class") or m.get("options"))
    ]
    assert classless
    for descriptor in classless:
        classes = sensor_classes(META[descriptor])
        assert (classes.state_class, classes.unit) == (None, None), descriptor


def test_no_metadata_means_no_classes() -> None:
    classes = sensor_classes(None)
    assert (classes.device_class, classes.state_class, classes.unit, classes.is_enum) == (
        None,
        None,
        None,
        False,
    )


def test_the_sensor_platform_decides_through_sensor_classes() -> None:
    """``CardataSensor`` must not read the class fields off the metadata itself.

    Reading them inline is what hid issue #25: the platform can't be imported
    here, so any rule written there is untested.
    """

    path = _PKG / "sensor.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CardataSensor")
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")

    calls = [
        n
        for n in ast.walk(init)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "sensor_classes"
    ]
    assert calls, (
        f"sensor.py:{init.lineno}: CardataSensor.__init__ no longer calls sensor_classes()"
    )

    fields = {"device_class", "state_class", "unit", "options"}
    for node in ast.walk(init):
        key = None
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            key = node.slice.value
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            key = node.args[0].value
        assert key not in fields, (
            f"sensor.py:{node.lineno}: CardataSensor.__init__ reads {key!r} from the "
            "metadata directly. Decide it in sensor_classes.py, where "
            "tests/test_sensor_classes.py checks it for every descriptor (issue #25)."
        )
