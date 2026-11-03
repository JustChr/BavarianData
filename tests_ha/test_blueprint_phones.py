"""The phone picker of the two older notifying blueprints, in a real Home Assistant.

*Notify when charging completes* and *Stop charging at target %* took the phone
as typed text before they had a picker. The text field moved into a collapsed
section and still works, so an automation made before keeps notifying after a
re-import; these run both blueprints with the picker, the old field, and both.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from .conftest import VIN

FOLDER = Path(__file__).parents[1] / "blueprints" / "automation" / "bavariandata"


@pytest.fixture(autouse=True)
def own_config_folder(hass: HomeAssistant, tmp_path: Path) -> None:
    """Blueprint files go to this test's own folder, not the plugin's shared one."""

    hass.config.config_dir = str(tmp_path)


def _phone(hass: HomeAssistant, name: str) -> str:
    entry = MockConfigEntry(domain="mobile_app", title=name)
    entry.add_to_hass(hass)
    return (
        dr.async_get(hass)
        .async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={("mobile_app", name)}, name=name
        )
        .id
    )


async def _automation(hass: HomeAssistant, blueprint: str, inputs: dict) -> None:
    target = Path(hass.config.path("blueprints", "automation", "bavariandata", blueprint))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FOLDER / blueprint, target)
    assert await async_setup_component(
        hass,
        "automation",
        {"automation": {"use_blueprint": {"path": f"bavariandata/{blueprint}", "input": inputs}}},
    )
    await hass.async_block_till_done()


# (picked phones, typed service) -> which mocked services hear from it
CASES = {
    "picker": (["Pixel 10"], "", {"pixel_10"}),
    "older field": ([], "notify.mobile_app_iphone", {"iphone"}),
    "both": (["Pixel 10"], "notify.mobile_app_iphone", {"pixel_10", "iphone"}),
    "neither": ([], "", set()),
}


@pytest.mark.parametrize(("phones", "typed", "expected"), CASES.values(), ids=CASES.keys())
async def test_charging_complete_reaches_the_phones(
    hass: HomeAssistant, phones: list[str], typed: str, expected: set[str]
) -> None:
    calls = {
        name: async_mock_service(hass, "notify", f"mobile_app_{name}")
        for name in ("pixel_10", "iphone")
    }
    inputs = {"phones": [_phone(hass, name) for name in phones]}
    if typed:
        inputs["notify_target"] = typed
    await _automation(hass, "notify_charging_complete.yaml", inputs)

    hass.bus.async_fire(
        "bavariandata_charging_stopped",
        {"vin": VIN, "soc": 80.0, "target_soc": 80.0, "complete": True, "vehicle": "i5 eDrive40"},
    )
    await hass.async_block_till_done()

    assert {name for name, got in calls.items() if got} == expected
    for name in expected:
        (call,) = calls[name]
        assert call.data["title"].strip() == "i5 eDrive40: charging complete"
        assert call.data["message"].strip() == "Battery at 80% (target 80%)."


@pytest.mark.parametrize(("phones", "typed", "expected"), CASES.values(), ids=CASES.keys())
async def test_stop_at_target_reaches_the_phones(
    hass: HomeAssistant, phones: list[str], typed: str, expected: set[str]
) -> None:
    calls = {
        name: async_mock_service(hass, "notify", f"mobile_app_{name}")
        for name in ("pixel_10", "iphone")
    }
    # The switch-off is homeassistant.turn_off.
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"charger": {"initial": True}}}
    )
    hass.states.async_set(
        "sensor.i5_soc", "70", {"device_class": "battery", "unit_of_measurement": "%"}
    )
    inputs = {
        "soc_sensor": "sensor.i5_soc",
        "target_soc": 80,
        "charge_switch": "input_boolean.charger",
        "phones": [_phone(hass, name) for name in phones],
    }
    if typed:
        inputs["notify_target"] = typed
    await _automation(hass, "stop_charge_at_target.yaml", inputs)

    hass.states.async_set(
        "sensor.i5_soc", "81", {"device_class": "battery", "unit_of_measurement": "%"}
    )
    await hass.async_block_till_done()

    assert hass.states.get("input_boolean.charger").state == "off"
    assert {name for name, got in calls.items() if got} == expected
    for name in expected:
        (call,) = calls[name]
        assert call.data["title"] == "Charging stopped"
