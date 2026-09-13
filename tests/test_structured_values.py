"""List-valued descriptors must reach Home Assistant as something it can store.

Found on the maintainer's i5 (2026-09-13): ``conditionBasedServices`` and
``checkControlMessages`` arrive as lists of objects. Passed through as a state,
Home Assistant stringified them, logged "State ... is longer than 255, falling
back to unknown" and recorded ``unknown`` -- the recorder showed both sensors
unknown for as far back as it kept history, so the service dates and the
washer-fluid warning the car had raised were never visible.
"""

from __future__ import annotations

import ast
import pathlib

from .conftest import load_module

structured_values = load_module("structured_values")

_SENSOR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "bavariandata"
    / "sensor.py"
)

# The shape BMW streams for vehicle.status.conditionBasedServices.
CBS = [
    {"date": "2027-10", "id": 3, "messageType": "CBS", "status": "OK", "title": "Brake fluid"},
    {"date": "2027-10", "id": 100, "messageType": "CBS", "status": "OK", "title": "Vehicle check"},
    {"date": "2028-10", "id": 32, "messageType": "CBS", "status": "OK", "title": "Statutory vehicle inspection"},
]


def test_a_list_is_shown_as_its_count_and_kept_whole():
    state, items = structured_values.structured_state(CBS)
    assert state == 3
    assert items is CBS


def test_an_empty_list_is_zero_not_unknown():
    # "No Check Control messages" is a real, automatable answer.
    assert structured_values.structured_state([]) == (0, [])


# The exact text from the i5's log after beta.9 (2026-09-13): the list reached the
# sensor as a JSON string, so the list-only check passed it through and Home
# Assistant rejected it again. Double quotes and ``null`` give it away -- a
# stringified Python list would read ``'…'`` and ``None``.
CBS_WIRE = (
    '[{"date":"2027-10","description":"Next change due at the latest by the stated date.",'
    '"id":3,"messageType":"CBS","status":"OK","title":"Brake fluid","text":"-",'
    '"unitOfLengthRemaining":"-"},{"date":"2027-10","description":"-","id":100,'
    '"messageType":"CBS","status":"OK","title":"Vehicle check","text":"-",'
    '"unitOfLengthRemaining":"-"},{"date":"2028-10","description":"Next statutory vehicle '
    'inspection due by the stated date.","id":32,"messageType":"CBS","status":"OK",'
    '"title":"Statutory vehicle inspection","text":"-","unitOfLengthRemaining":"-"}]'
)
CCM_WIRE = (
    '[{"date":null,"description":null,"id":164,"messageType":"CCM","status":"NULL",'
    '"title":null,"text":"The washer fluid level is low in the window washer reservoir. '
    'Please add washer fluid as soon as possible. See Owner´s Manual for more '
    'information.","unitOfLengthRemaining":"19596"}]'
)


def test_a_json_list_string_is_shown_as_its_count_and_decoded():
    state, items = structured_values.structured_state(CBS_WIRE)
    assert state == 3
    assert [item["title"] for item in items] == [
        "Brake fluid",
        "Vehicle check",
        "Statutory vehicle inspection",
    ]


def test_a_single_check_control_message_string_counts_one():
    state, items = structured_values.structured_state(CCM_WIRE)
    assert state == 1
    assert items[0]["id"] == 164
    assert items[0]["date"] is None


def test_a_json_object_string_is_exposed_but_has_no_count():
    assert structured_values.structured_state('{"a": 1}') == (None, {"a": 1})


def test_strings_that_are_not_a_json_structure_pass_through():
    # Enum tokens, numbers-as-text, and bracketed text that isn't JSON stay values.
    for value in ("locked", "85", "", "[not json", "{oops}", '"quoted"'):
        assert structured_values.structured_state(value) == (value, None)


def test_a_mapping_is_exposed_but_has_no_count():
    state, items = structured_values.structured_state({"a": 1})
    assert state is None
    assert items == {"a": 1}


def test_a_scalar_passes_through_untouched():
    for value in (42, 12.5, "locked", True, None):
        assert structured_values.structured_state(value) == (value, None)


def test_the_list_comes_back_from_the_saved_attribute():
    attrs = {"descriptor": "vehicle.status.conditionBasedServices", "items": CBS}
    assert structured_values.restored_items(attrs) is CBS


def test_nothing_is_restored_without_a_structure():
    assert structured_values.restored_items(None) is None
    assert structured_values.restored_items({}) is None
    assert structured_values.restored_items({"items": "3"}) is None


def _method(tree: ast.Module, cls: str, name: str) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name:
                    return item
    raise AssertionError(f"sensor.py: {cls}.{name} not found -- update this guard")


def _calls(node: ast.AST, name: str) -> bool:
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
        for n in ast.walk(node)
    )


def test_descriptor_sensor_never_hands_a_raw_value_to_home_assistant():
    # sensor.py imports Home Assistant, so the wiring is checked by shape: both
    # places a value becomes the native value must go through structured_state.
    tree = ast.parse(_SENSOR.read_text(encoding="utf-8"))
    for method in ("_handle_update", "async_added_to_hass"):
        assert _calls(_method(tree, "CardataSensor", method), "structured_state"), (
            f"sensor.py: CardataSensor.{method} sets the native value without "
            "structured_state -- a list-valued descriptor will overflow the "
            "255-character state limit again"
        )
    assert _calls(
        _method(tree, "CardataSensor", "async_added_to_hass"), "restored_items"
    ), "sensor.py: CardataSensor.async_added_to_hass no longer restores the list"
