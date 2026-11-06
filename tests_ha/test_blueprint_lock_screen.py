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


async def _after_power(hass: HomeAssistant) -> None:
    """A change in power refreshes the card after a 3 s pause (a charge ending
    drops its power a moment before the car reports the end)."""

    await _settle()
    await _later(hass, 0.06)


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
    assert card["critical_text"] == f"52{NB}→{NB}80{NB}% · 10.8{NB}kW"
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
    assert first.data["data"]["critical_text"] == f"52{NB}→{NB}80{NB}%"
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

    assert calls[0].data["data"]["critical_text"] == f"52{NB}→{NB}80{NB}% · 4.0{NB}kW"


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
    await _after_power(hass)
    assert len(calls) == 1

    hass.states.async_set(power, "7.2", {**hass.states.get(power).attributes})
    await _after_power(hass)
    assert len(calls) == 2
    assert calls[-1].data["data"]["critical_text"] == f"52{NB}→{NB}80{NB}% · 7.2{NB}kW"

    # Measured from the card's new 7.2 kW, not from where the charge started.
    hass.states.async_set(power, "7.6", {**hass.states.get(power).attributes})
    await _after_power(hass)
    assert len(calls) == 2

    # Power gone (the car paused): the card drops it at once.
    hass.states.async_set(power, "0", {**hass.states.get(power).attributes})
    await _after_power(hass)
    assert len(calls) == 3
    assert calls[-1].data["data"]["critical_text"] == f"52{NB}→{NB}80{NB}%"


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
    assert calls[-1].data["data"]["critical_text"] == f"52{NB}→{NB}80{NB}% · 10.8{NB}kW"

    # It starts: the card switches to it at once.
    hass.states.async_set(wallbox, "4120", {**hass.states.get(wallbox).attributes})
    await _after_power(hass)
    assert len(calls) == 2
    assert calls[-1].data["data"]["critical_text"] == f"52{NB}→{NB}80{NB}% · 4.1{NB}kW"

    # The current goes up; BMW's value stays where it was.
    hass.states.async_set(wallbox, "10150", {**hass.states.get(wallbox).attributes})
    await _after_power(hass)
    assert len(calls) == 3
    assert calls[-1].data["data"]["critical_text"] == f"52{NB}→{NB}80{NB}% · 10.2{NB}kW"

    # Away from home the wallbox isn't this car's: BMW's value again.
    hass.states.async_set(tracker, "not_home")
    await _later(hass, 2.1)
    assert calls[-1].data["data"]["critical_text"] == f"52{NB}→{NB}80{NB}% · 10.8{NB}kW"


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


async def test_a_tap_opens_the_start_page(hass: HomeAssistant) -> None:
    await _car(hass)
    calls = await _automation(hass, keep_minutes=0)

    hass.bus.async_fire("bavariandata_charging_started", {"vin": VIN, "soc": 40, "target_soc": 80})
    await _settle()
    card = calls[0].data["data"]
    assert (card["url"], card["clickAction"]) == ("/", "/")


@pytest.mark.parametrize(
    "picked",
    [
        "/dashboard-car/charging",
        "dashboard-car/charging",
        " http://ha.local:8123/dashboard-car/charging ",
    ],
)
async def test_a_tap_opens_the_picked_dashboard(hass: HomeAssistant, picked: str) -> None:
    await _car(hass)
    calls = await _automation(hass, open_on_tap=picked, keep_minutes=0)

    hass.bus.async_fire("bavariandata_charging_started", {"vin": VIN, "soc": 40, "target_soc": 80})
    await _settle()
    hass.bus.async_fire(
        "bavariandata_charging_stopped",
        {"vin": VIN, "soc": 80, "target_soc": 80, "energy_kwh": 20, "cost": None, "complete": True},
    )
    await _settle()

    charging, result = calls[0], calls[-2]
    assert result.data["message"].startswith("✓")
    for card in (charging.data["data"], result.data["data"]):
        assert (card["url"], card["clickAction"]) == (
            "/dashboard-car/charging",
            "/dashboard-car/charging",
        )


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


async def test_a_charge_started_just_after_a_restart_gets_one_run(hass: HomeAssistant) -> None:
    """Live, 3 Nov: a charge began 31 s after a restart; its start ran the card,
    and the restart's look a minute later found it charging and ran a second."""

    await _car(
        hass, states={**STATES, "vehicle.drivetrain.electricEngine.charging.status": "nocharging"}
    )
    hass.set_state(CoreState.not_running)
    calls = await _automation(hass)
    await hass.async_start()
    await _settle()

    await _later(hass, 0.5)
    status = _entity(hass, "vehicle.drivetrain.electricEngine.charging.status")
    hass.states.async_set(status, "chargingactive", {**hass.states.get(status).attributes})
    _charge_began(hass)
    hass.bus.async_fire("bavariandata_charging_started", {"vin": VIN, "soc": 40, "target_soc": 80})
    await _settle()
    assert len(calls) == 1

    await _later(hass, 1.1)
    assert len(calls) == 1


def _entity(hass: HomeAssistant, descriptor: str) -> str:
    return next(
        state.entity_id
        for state in hass.states.async_all("sensor")
        if state.attributes.get("descriptor") == descriptor
    )


def _charge_began(hass: HomeAssistant) -> None:
    """The session's energy counter resets as the charge starts: its start time."""

    counter = _entity(hass, "charged_energy_session")
    attributes = {**hass.states.get(counter).attributes, "last_reset": dt_util.utcnow().isoformat()}
    hass.states.async_set(counter, hass.states.get(counter).state, attributes)


def _ended(**data) -> dict:
    return {"vin": VIN, "soc": 80, "target_soc": 80, "cost": None, "complete": True, **data}


async def test_the_result_says_how_long_the_charge_took(hass: HomeAssistant, freezer) -> None:
    await _car(hass)
    calls = await _automation(hass, keep_minutes=0)

    _charge_began(hass)
    hass.bus.async_fire("bavariandata_charging_started", {"vin": VIN, "soc": 40, "target_soc": 80})
    await _settle()
    freezer.tick(timedelta(minutes=112))
    hass.bus.async_fire("bavariandata_charging_stopped", _ended(energy_kwh=15.9))
    await _settle()

    assert calls[-2].data["message"] == f"✓{NB}80{NB}% · +15.9{NB}kWh · 1{NB}h{NB}52{NB}min"


async def test_a_charge_picked_up_after_a_restart_keeps_its_length(
    hass: HomeAssistant, freezer
) -> None:
    """The counter keeps the charge's start across a restart; the card's run doesn't."""

    await _car(hass)
    calls = await _automation(hass, keep_minutes=0)
    _charge_began(hass)
    freezer.tick(timedelta(minutes=90))

    hass.bus.async_fire("bavariandata_lock_screen_resume", {"vin": VIN})
    await _settle()
    freezer.tick(timedelta(minutes=30))
    hass.bus.async_fire("bavariandata_charging_stopped", _ended(energy_kwh=5.0))
    await _settle()

    assert calls[-2].data["message"] == f"✓{NB}80{NB}% · +5.0{NB}kWh · 2{NB}h"


async def test_an_end_while_the_card_is_updating_still_shows_the_result(
    hass: HomeAssistant,
) -> None:
    """Live, 3 Nov: the power fell to 0 in the moment the charge ended, the card's
    run was busy sending that update when the end came, missed it, and the card
    stayed. This phone ends the charge while it is being sent an update."""

    await _car(hass)
    await _automation(hass, keep_minutes=0)
    status = _entity(hass, "vehicle.drivetrain.electricEngine.charging.status")
    power = _entity(hass, "vehicle.powertrain.electric.battery.charging.power")
    sent: list = []
    end_while_sending = False

    async def phone(call) -> None:
        nonlocal end_while_sending
        sent.append(call.data["message"])
        if end_while_sending:
            end_while_sending = False
            hass.states.async_set(status, "chargingended", {**hass.states.get(status).attributes})
            hass.bus.async_fire("bavariandata_charging_stopped", _ended(energy_kwh=11.9))
            await _settle()

    hass.services.async_register("notify", "mobile_app_iphone", phone)
    hass.bus.async_fire("bavariandata_charging_started", {"vin": VIN, "soc": 40, "target_soc": 80})
    await _settle()

    end_while_sending = True
    hass.states.async_set(power, "0", {**hass.states.get(power).attributes})
    await _after_power(hass)
    await _later(hass, 5)

    assert f"✓{NB}80{NB}% · +11.9{NB}kWh" in sent
    # Nothing covers the result.
    assert sent[-2:] == [f"✓{NB}80{NB}% · +11.9{NB}kWh", "clear_notification"]


async def test_a_start_before_the_status_follows_still_shows_the_card(
    hass: HomeAssistant,
) -> None:
    """Live, 3 Nov: ``charging_started`` came in the same second as the status
    turned to charging, but before it: the card's run read the last charge's
    ``chargingended``, took the charge for over and sent nothing."""

    await _car(
        hass,
        states={**STATES, "vehicle.drivetrain.electricEngine.charging.status": "chargingended"},
    )
    calls = await _automation(hass)
    status = _entity(hass, "vehicle.drivetrain.electricEngine.charging.status")

    hass.bus.async_fire("bavariandata_charging_started", {"vin": VIN, "soc": 40, "target_soc": 80})
    await _settle()
    hass.states.async_set(status, "chargingactive", {**hass.states.get(status).attributes})
    await _settle()

    (first,) = calls
    assert first.data["data"]["critical_text"] == f"52{NB}→{NB}80{NB}% · 10.8{NB}kW"
    await _later(hass, 2.1)
    assert len(calls) == 2


async def test_a_start_does_not_show_the_last_charges_energy(hass: HomeAssistant) -> None:
    """Live, 3 Nov: the start came 16 ms before the energy counter reset, and the
    first card said +2.7 kWh, the charge before's."""

    await _car(hass)
    counter = _entity(hass, "charged_energy_session")
    before = (dt_util.utcnow() - timedelta(hours=1)).isoformat()
    hass.states.async_set(
        counter, "2.691", {**hass.states.get(counter).attributes, "last_reset": before}
    )
    calls = await _automation(hass)

    hass.bus.async_fire("bavariandata_charging_started", {"vin": VIN, "soc": 40, "target_soc": 80})
    await _settle()
    assert calls[0].data["message"] == f"→{NB}80{NB}%"

    hass.states.async_set(
        counter,
        "1.24",
        {**hass.states.get(counter).attributes, "last_reset": dt_util.utcnow().isoformat()},
    )
    await _later(hass, 2.1)
    assert calls[-1].data["message"] == f"→{NB}80{NB}% · +1.2{NB}kWh"


async def test_a_stop_short_of_the_target_stops_the_countdown_at_once(
    hass: HomeAssistant,
) -> None:
    """Live, 3 Nov: stopped at 82 of 85 % with the cable in, the integration waits
    five minutes for the car to carry on before it announces the end; the card's
    run stopped without a word and the phone counted down all that time."""

    await _car(hass)
    calls = await _automation(hass, keep_minutes=0)
    status = _entity(hass, "vehicle.drivetrain.electricEngine.charging.status")
    hass.bus.async_fire("bavariandata_charging_started", {"vin": VIN, "soc": 40, "target_soc": 80})
    await _settle()

    hass.states.async_set(status, "nocharging", {**hass.states.get(status).attributes})
    await _settle()
    held = calls[-1].data
    assert held["message"] == f"⏸︎{NB}→{NB}80{NB}% · +6.3{NB}kWh"
    assert held["data"]["chronometer"] is False
    assert held["data"]["critical_text"] == f"52{NB}→{NB}80{NB}%"

    # The car carries on: the countdown comes back.
    await _later(hass, 1)
    hass.states.async_set(status, "chargingactive", {**hass.states.get(status).attributes})
    await _settle()
    assert calls[-1].data["data"]["chronometer"] is True

    # It stops again, and the result follows five minutes later; nothing covers it.
    hass.states.async_set(status, "nocharging", {**hass.states.get(status).attributes})
    await _settle()
    for _ in range(3):
        await _later(hass, 2.1)
    hass.bus.async_fire("bavariandata_charging_stopped", _ended(complete=False, soc=52.0))
    await _settle()
    await _later(hass, 1)
    assert calls[-2].data["message"].startswith(f"52{NB}%{NB}/{NB}80{NB}%")
    assert calls[-1].data["message"] == "clear_notification"
    count = len(calls)
    await _later(hass, 30)
    assert len(calls) == count


async def test_at_the_target_the_corner_drops_the_arrow(hass: HomeAssistant) -> None:
    await _car(hass, states={**STATES, "soc_estimate": "80"})
    calls = await _automation(hass)

    hass.bus.async_fire("bavariandata_charging_started", {"vin": VIN, "soc": 40, "target_soc": 80})
    await _settle()

    assert calls[0].data["data"]["critical_text"] == f"80{NB}% · 10.8{NB}kW"
