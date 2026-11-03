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
    "charged_energy_session": "6.34",
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
    assert first.data["message"] == f"→{NB}80{NB}% · +6.3{NB}kWh"
    card = first.data["data"]
    assert card["tag"] == f"bavariandata-charging-{device}"
    assert card["live_update"] is True
    # The bar is the battery, up to the target.
    assert (card["progress"], card["progress_max"]) == (52, 80)
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
    assert done.data["message"] == f"✓{NB}80{NB}% · +23.4{NB}kWh · 6.10{NB}EUR"
    assert (done.data["data"]["progress"], done.data["data"]["progress_max"]) == (80, 80)
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
        "charged_energy_session": "0",
        "vehicle.powertrain.electric.battery.charging.power": "0",
    }
    await _car(hass, states=states)
    calls = await _automation(hass)

    hass.bus.async_fire("bavariandata_charging_started", {"vin": VIN, "soc": 40, "target_soc": 80})
    await _settle()

    (first,) = calls
    assert first.data["title"] == "i5 eDrive40"
    assert first.data["message"] == f"→{NB}80{NB}%"
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

    assert calls[0].data["data"]["critical_text"] == f"52{NB}% · 4.0{NB}kW"


async def test_a_change_in_power_refreshes_the_card_at_once(hass: HomeAssistant) -> None:
    await _car(hass)
    power = next(
        state.entity_id
        for state in hass.states.async_all("sensor")
        if state.attributes.get("descriptor")
        == "vehicle.powertrain.electric.battery.charging.power"
    )
    calls = await _automation(hass)

    hass.bus.async_fire("bavariandata_charging_started", {"vin": VIN, "soc": 40, "target_soc": 80})
    await _settle()
    assert len(calls) == 1

    # Less than a tenth off what the card shows (10.8 kW): it waits for its interval.
    hass.states.async_set(power, "10.0", {**hass.states.get(power).attributes})
    await _settle()
    assert len(calls) == 1

    hass.states.async_set(power, "7.2", {**hass.states.get(power).attributes})
    await _settle()
    assert len(calls) == 2
    assert calls[-1].data["data"]["critical_text"] == f"52{NB}% · 7.2{NB}kW"

    # Measured from the card's new 7.2 kW, not from where the charge started.
    hass.states.async_set(power, "7.6", {**hass.states.get(power).attributes})
    await _settle()
    assert len(calls) == 2

    # Power gone (the car paused): the card drops it at once.
    hass.states.async_set(power, "0", {**hass.states.get(power).attributes})
    await _settle()
    assert len(calls) == 3
    assert calls[-1].data["data"]["critical_text"] == f"52{NB}%"


def _tracker(hass: HomeAssistant, device: str, state: str) -> str:
    entity = er.async_get(hass).async_get_or_create(
        "device_tracker", DOMAIN, f"{VIN}_location", device_id=device
    )
    hass.states.async_set(entity.entity_id, state)
    return entity.entity_id


async def test_the_wallbox_power_is_shown_while_the_car_is_at_home(hass: HomeAssistant) -> None:
    device = await _car(hass)
    tracker = _tracker(hass, device, "home")
    wallbox = "sensor.wallbox_power"
    hass.states.async_set(wallbox, "0", {"unit_of_measurement": "W", "device_class": "power"})
    calls = await _automation(hass, wallbox_power=wallbox)

    # The wallbox hasn't started yet: BMW's value.
    hass.bus.async_fire("bavariandata_charging_started", {"vin": VIN, "soc": 40, "target_soc": 80})
    await _settle()
    assert calls[-1].data["data"]["critical_text"] == f"52{NB}% · 10.8{NB}kW"

    # It starts: the card switches to it at once.
    hass.states.async_set(wallbox, "4120", {**hass.states.get(wallbox).attributes})
    await _settle()
    assert len(calls) == 2
    assert calls[-1].data["data"]["critical_text"] == f"52{NB}% · 4.1{NB}kW"

    # The current goes up; BMW's value stays where it was.
    hass.states.async_set(wallbox, "10150", {**hass.states.get(wallbox).attributes})
    await _settle()
    assert len(calls) == 3
    assert calls[-1].data["data"]["critical_text"] == f"52{NB}% · 10.2{NB}kW"

    # Away from home the wallbox isn't this car's: BMW's value again.
    hass.states.async_set(tracker, "not_home")
    await _later(hass, 2.1)
    assert calls[-1].data["data"]["critical_text"] == f"52{NB}% · 10.8{NB}kW"


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
    assert stopped.data["message"] == f"61{NB}%{NB}/{NB}80{NB}%"
    assert stopped.data["data"]["progress"] == 61
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
            f"→{NB}80{NB}% · +6.3{NB}kWh",
            f"✓{NB}80{NB}% · +20.0{NB}kWh",
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
    assert (resumed.data["data"]["progress"], resumed.data["data"]["progress_max"]) == (52, 80)


async def test_the_result_says_how_long_the_charge_took(hass: HomeAssistant, freezer) -> None:
    await _car(hass)
    calls = await _automation(hass, keep_minutes=0)

    hass.bus.async_fire("bavariandata_charging_started", {"vin": VIN, "soc": 40, "target_soc": 80})
    await _settle()
    freezer.tick(timedelta(minutes=112))
    hass.bus.async_fire(
        "bavariandata_charging_stopped",
        {
            "vin": VIN,
            "soc": 80,
            "target_soc": 80,
            "energy_kwh": 15.9,
            "cost": None,
            "complete": True,
        },
    )
    await _settle()

    assert calls[-2].data["message"] == f"✓{NB}80{NB}% · +15.9{NB}kWh · 1{NB}h{NB}52{NB}min"


async def test_a_card_picked_up_after_a_restart_leaves_the_duration_out(
    hass: HomeAssistant, freezer
) -> None:
    await _car(hass)
    calls = await _automation(hass, keep_minutes=0)

    hass.bus.async_fire("bavariandata_lock_screen_resume", {"vin": VIN})
    await _settle()
    freezer.tick(timedelta(minutes=30))
    hass.bus.async_fire(
        "bavariandata_charging_stopped",
        {
            "vin": VIN,
            "soc": 80,
            "target_soc": 80,
            "energy_kwh": 5.0,
            "cost": None,
            "complete": True,
        },
    )
    await _settle()

    assert calls[-2].data["message"] == f"✓{NB}80{NB}% · +5.0{NB}kWh"
