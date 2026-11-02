"""The "Charging on the lock screen" blueprint, run by a real Home Assistant.

A blueprint is all templates, and none of them can be checked without Home
Assistant's own template engine: these run it as an automation against a car
made of registry entries (no integration loaded) and read what it sends to a
mocked phone.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import timedelta
from pathlib import Path

import pytest
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.bavariandata.const import DOMAIN

from .conftest import VIN

BLUEPRINT = "charging_lock_screen.yaml"
SOURCE = Path(__file__).parents[1] / "blueprints" / "automation" / "bavariandata" / BLUEPRINT
# The card joins numbers and units with a no-break space.
NB = "\u00a0"
OTHER_VIN = "WBATEST0000000002"

STATES = {
    "soc_estimate": "52",
    "vehicle.drivetrain.batteryManagement.header": "50",
    "vehicle.powertrain.electric.battery.stateOfCharge.target": "80",
    "vehicle.powertrain.electric.battery.charging.power": "10.84",
    "charge_eta": "60",
    "vehicle.drivetrain.electricEngine.charging.status": "chargingactive",
}
UNITS = {"vehicle.powertrain.electric.battery.charging.power": "kW"}


@pytest.fixture(autouse=True)
def own_config_folder(hass: HomeAssistant, tmp_path: Path) -> None:
    """Blueprint files go to this test's own folder, not the plugin's shared one."""

    hass.config.config_dir = str(tmp_path)


async def _car(hass: HomeAssistant, vin: str = VIN, states: dict[str, str] = STATES) -> str:
    # ``integration_entities('bavariandata')`` finds a real car's entities by the
    # platform that loaded them; these are registry entries only, so it finds
    # them by the other route it has: a config entry titled with the name.
    entry = MockConfigEntry(domain=DOMAIN, unique_id=vin, title=DOMAIN)
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, vin)}, name="i5 eDrive40"
    )
    registry = er.async_get(hass)
    for number, (descriptor, value) in enumerate(states.items()):
        entity = registry.async_get_or_create(
            "sensor", DOMAIN, f"{vin}_{number}", device_id=device.id, config_entry=entry
        )
        attributes = {"descriptor": descriptor}
        if descriptor in UNITS:
            attributes["unit_of_measurement"] = UNITS[descriptor]
        hass.states.async_set(entity.entity_id, value, attributes)
    return device.id


def _phone(hass: HomeAssistant, name: str) -> str:
    """A Companion app device; the app names its notify action after it."""

    entry = MockConfigEntry(domain="mobile_app", title=name)
    entry.add_to_hass(hass)
    return (
        dr.async_get(hass)
        .async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={("mobile_app", name)}, name=name
        )
        .id
    )


async def _automation(hass: HomeAssistant, **inputs):
    target = Path(hass.config.path("blueprints", "automation", "bavariandata", BLUEPRINT))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(SOURCE, target)
    calls = async_mock_service(hass, "notify", "mobile_app_iphone")
    inputs.setdefault("phones", [_phone(hass, "iPhone")])
    assert await async_setup_component(
        hass,
        "automation",
        {"automation": {"use_blueprint": {"path": f"bavariandata/{BLUEPRINT}", "input": inputs}}},
    )
    await hass.async_block_till_done()
    return calls


async def _settle() -> None:
    """Let the automation take its pending steps.

    Not ``async_block_till_done``: that waits for every running automation, and
    this one runs until the charge ends.
    """

    for _ in range(100):
        await asyncio.sleep(0)


async def _later(hass: HomeAssistant, minutes: float) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=minutes))
    await _settle()


async def test_a_charge_runs_from_start_to_cleared(hass: HomeAssistant, tmp_path) -> None:
    device = await _car(hass)
    calls = await _automation(hass)

    hass.bus.async_fire(
        "bavariandata_charging_started",
        {"vin": VIN, "soc": 40, "target_soc": 80, "vehicle": "i5 eDrive40"},
    )
    await _settle()

    (first,) = calls
    assert first.data["title"] == "i5 eDrive40"
    assert first.data["message"] == f"52{NB}% of 80{NB}% · 10.8{NB}kW"
    card = first.data["data"]
    assert card["tag"] == f"bavariandata-charging-{device}"
    assert card["live_update"] is True
    # The bar runs from the session's start (40 %) to the target (80 %).
    assert (card["progress"], card["progress_max"]) == (12, 40)
    assert card["critical_text"] == f"52{NB}% · 10.8{NB}kW"
    assert card["chronometer"] is True
    assert (card["when"], card["when_relative"]) == (3600, True)

    await _later(hass, 2.1)
    assert len(calls) == 2

    hass.bus.async_fire(
        "bavariandata_charging_stopped",
        {
            "vin": VIN,
            "soc": 80.0,
            "target_soc": 80,
            "energy_kwh": 23.44,
            "cost": {"amount": 6.1, "currency": "EUR"},
            "complete": True,
        },
    )
    await _settle()
    done = calls[-1]
    assert done.data["message"] == f"Charged to 80{NB}% · 23.4{NB}kWh · 6.10{NB}EUR"
    assert (done.data["data"]["progress"], done.data["data"]["progress_max"]) == (40, 40)
    assert done.data["data"]["chronometer"] is False

    await _later(hass, 10.1)
    assert calls[-1].data["message"] == "clear_notification"
    assert calls[-1].data["data"]["tag"] == f"bavariandata-charging-{device}"
    count = len(calls)
    await _later(hass, 30)
    assert len(calls) == count


async def test_without_a_countdown_or_power_the_card_still_shows(hass: HomeAssistant) -> None:
    states = {
        **STATES,
        "charge_eta": "unknown",
        "vehicle.powertrain.electric.battery.charging.power": "0",
    }
    await _car(hass, states=states)
    calls = await _automation(hass)

    hass.bus.async_fire("bavariandata_charging_started", {"vin": VIN, "soc": 40, "target_soc": 80})
    await _settle()

    (first,) = calls
    assert first.data["title"] == "i5 eDrive40"
    assert first.data["message"] == f"52{NB}% of 80{NB}%"
    assert first.data["data"]["critical_text"] == f"52{NB}%"
    assert first.data["data"]["chronometer"] is False


async def test_watts_read_as_kilowatts(hass: HomeAssistant) -> None:
    UNITS["vehicle.powertrain.electric.battery.charging.power"] = "W"
    try:
        await _car(
            hass, states={**STATES, "vehicle.powertrain.electric.battery.charging.power": "3990.0"}
        )
    finally:
        UNITS["vehicle.powertrain.electric.battery.charging.power"] = "kW"
    calls = await _automation(hass)

    hass.bus.async_fire("bavariandata_charging_started", {"vin": VIN, "soc": 40, "target_soc": 80})
    await _settle()

    assert calls[0].data["message"] == f"52{NB}% of 80{NB}% · 4.0{NB}kW"


async def test_a_stop_short_of_the_target_says_so(hass: HomeAssistant) -> None:
    await _car(hass)
    calls = await _automation(hass, keep_minutes=0)

    hass.bus.async_fire("bavariandata_charging_started", {"vin": VIN, "soc": 40, "target_soc": 80})
    await _settle()
    hass.bus.async_fire(
        "bavariandata_charging_stopped",
        {
            "vin": VIN,
            "soc": 61,
            "target_soc": 80,
            "energy_kwh": None,
            "cost": None,
            "complete": False,
        },
    )
    await _settle()

    stopped, cleared = calls[-2:]
    assert stopped.data["message"] == f"Stopped at 61{NB}%"
    assert stopped.data["data"]["progress"] == 21
    assert cleared.data["message"] == "clear_notification"


async def test_every_picked_phone_gets_the_card(hass: HomeAssistant) -> None:
    await _car(hass)
    other = async_mock_service(hass, "notify", "mobile_app_pixel_10")
    calls = await _automation(
        hass, phones=[_phone(hass, "iPhone"), _phone(hass, "Pixel 10")], keep_minutes=0
    )

    hass.bus.async_fire("bavariandata_charging_started", {"vin": VIN, "soc": 40, "target_soc": 80})
    await _settle()
    hass.bus.async_fire(
        "bavariandata_charging_stopped",
        {
            "vin": VIN,
            "soc": 80,
            "target_soc": 80,
            "energy_kwh": 20.0,
            "cost": None,
            "complete": True,
        },
    )
    await _settle()

    for phone in (calls, other):
        assert [c.data["message"] for c in phone] == [
            f"52{NB}% of 80{NB}% · 10.8{NB}kW",
            f"Charged to 80{NB}% · 20.0{NB}kWh",
            "clear_notification",
        ]


async def test_only_the_picked_cars(hass: HomeAssistant) -> None:
    picked = await _car(hass)
    await _car(hass, vin=OTHER_VIN)
    calls = await _automation(hass, vehicles=[picked])

    hass.bus.async_fire(
        "bavariandata_charging_started", {"vin": OTHER_VIN, "soc": 40, "target_soc": 80}
    )
    await _settle()
    assert calls == []

    hass.bus.async_fire("bavariandata_charging_started", {"vin": VIN, "soc": 40, "target_soc": 80})
    await _settle()
    assert len(calls) == 1


async def test_a_restart_picks_up_a_running_charge(hass: HomeAssistant) -> None:
    device = await _car(hass)
    await _car(
        hass,
        vin=OTHER_VIN,
        states={**STATES, "vehicle.drivetrain.electricEngine.charging.status": "nocharging"},
    )
    hass.set_state(CoreState.not_running)
    calls = await _automation(hass)

    await hass.async_start()
    await _settle()
    assert calls == []

    await _later(hass, 1.1)
    (resumed,) = calls
    assert resumed.data["data"]["tag"] == f"bavariandata-charging-{device}"
    # The session's own start is gone after a restart: the bar starts here.
    assert (resumed.data["data"]["progress"], resumed.data["data"]["progress_max"]) == (0, 28)
