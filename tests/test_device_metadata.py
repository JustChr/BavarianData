"""The device registry fields built from BMW's basic data.

Home Assistant takes text only for a device's name, model, versions and serial
number: from 2026.10 anything else is reported as a deprecation, and from
2026.12 it breaks. BMW's basic data is not typed, so every field is made text.
"""

from __future__ import annotations

from .harness import VIN, CoordinatorHarness

REGISTRY_FIELDS = ("name", "manufacturer", "model", "sw_version", "hw_version", "serial_number")


def test_untyped_basic_data_reaches_the_registry_as_text() -> None:
    with CoordinatorHarness() as h:
        metadata = h.coordinator.apply_basic_data(
            VIN, {"modelName": 5, "brand": "BMW", "puStep": 723, "bodyType": 60}
        )

    for field in REGISTRY_FIELDS:
        assert isinstance(metadata[field], str), field
    assert metadata["sw_version"] == "723"
    assert metadata["name"] == "5"


def test_text_basic_data_is_left_as_it_is() -> None:
    with CoordinatorHarness() as h:
        metadata = h.coordinator.apply_basic_data(
            VIN, {"modelName": "i5 eDrive40", "puStep": "0723", "bodyType": "G60"}
        )

    assert metadata["model"] == "i5 eDrive40"
    assert metadata["sw_version"] == "0723"
    assert metadata["hw_version"] == "G60"
