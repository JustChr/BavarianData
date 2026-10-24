"""Binary sensors and device triggers in a real Home Assistant.

The rules are tested HA-free (``tests/test_binary_sensor_plan.py``,
``tests/test_vehicle_triggers.py``); this is the half that only runs inside
Home Assistant: the entities' states and device classes, the registry bringing
them back on a reload, and a device trigger offered, attached and fired off the
bus.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.bavariandata import device_trigger
from custom_components.bavariandata.const import DOMAIN, EVENT_SITUATION, EVENT_ZONE_ARRIVED

from .conftest import VIN, make_entry, set_up

DOOR = "vehicle.cabin.door.row1.driver.isOpen"
LOCK = "vehicle.cabin.door.status"
STAMP = "2026-10-24T08:00:00Z"


async def _car(hass: HomeAssistant, no_bmw):
    entry = make_entry(vehicle_metadata={VIN: {"modelName": "i5 eDrive40", "brand": "BMW"}})
    await set_up(hass, entry)
    coordinator = entry.runtime_data.coordinator
    await coordinator.async_handle_message(
        {
            "vin": VIN,
            "data": {
                DOOR: {"value": True, "timestamp": STAMP},
                LOCK: {"value": "LOCKED", "timestamp": STAMP},
            },
        }
    )
    await hass.async_block_till_done()
    (device,) = [
        d
        for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if (DOMAIN, VIN) in d.identifiers
    ]
    return entry, device


def _binary(hass: HomeAssistant, entry, suffix: str):
    registry = er.async_get(hass)
    (found,) = [
        e
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.domain == "binary_sensor" and e.unique_id == f"{VIN}_{suffix}"
    ]
    return found


async def test_a_streamed_boolean_is_a_binary_sensor_with_its_class(
    hass: HomeAssistant, no_bmw
) -> None:
    entry, _device = await _car(hass, no_bmw)

    door = _binary(hass, entry, DOOR)
    state = hass.states.get(door.entity_id)
    assert state.state == "on"
    assert state.attributes["device_class"] == "opening"

    trip = hass.states.get(_binary(hass, entry, "trip_in_progress").entity_id)
    assert trip.state == "off"
    assert "started" not in trip.attributes

    # A reload brings both back from the registry, as themselves.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(door.entity_id).state == "on"
    assert hass.states.get(_binary(hass, entry, "trip_in_progress").entity_id) is not None


async def test_a_car_is_offered_only_the_triggers_it_can_fire(hass: HomeAssistant, no_bmw) -> None:
    _entry, device = await _car(hass, no_bmw)

    triggers = await device_trigger.async_get_triggers(hass, device.id)
    types = {t["type"] for t in triggers}
    assert "parked_unlocked" in types  # the lock state is known
    assert "charging_started" not in types  # never streamed a charging status
    assert all(t["device_id"] == device.id and t["domain"] == DOMAIN for t in triggers)

    assert await device_trigger.async_get_triggers(hass, "no-such-device") == []


async def test_trigger_capabilities(hass: HomeAssistant) -> None:
    zone = await device_trigger.async_get_trigger_capabilities(hass, {"type": "zone_arrived"})
    assert "zone" in zone["extra_fields"].schema
    situation = await device_trigger.async_get_trigger_capabilities(
        hass, {"type": "parked_unlocked"}
    )
    (field,) = situation["extra_fields"].schema
    assert field.default()["minutes"] == 10
    assert await device_trigger.async_get_trigger_capabilities(hass, {"type": "alarm"}) == {}


async def test_a_zone_trigger_fires_for_its_car_and_zone_only(hass: HomeAssistant, no_bmw) -> None:
    _entry, device = await _car(hass, no_bmw)
    calls: list[dict] = []
    config = device_trigger.TRIGGER_SCHEMA(
        {
            "platform": "device",
            "domain": DOMAIN,
            "device_id": device.id,
            "type": "zone_arrived",
            "zone": "zone.home",
        }
    )
    detach = await device_trigger.async_attach_trigger(
        hass, config, lambda run, context=None: calls.append(run), {"trigger_data": {"id": "0"}}
    )

    hass.bus.async_fire(EVENT_ZONE_ARRIVED, {"vin": VIN, "zone_entity_id": "zone.work"})
    hass.bus.async_fire(EVENT_ZONE_ARRIVED, {"vin": "OTHER", "zone_entity_id": "zone.home"})
    hass.bus.async_fire(EVENT_ZONE_ARRIVED, {"vin": VIN, "zone_entity_id": "zone.home"})
    await hass.async_block_till_done()

    (call,) = calls
    trigger = call["trigger"]
    assert trigger["type"] == "zone_arrived"
    assert trigger["id"] == "0"
    assert trigger["data"]["zone_entity_id"] == "zone.home"

    detach()
    hass.bus.async_fire(EVENT_ZONE_ARRIVED, {"vin": VIN, "zone_entity_id": "zone.home"})
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_a_situation_fires_once_it_has_lasted(hass: HomeAssistant, no_bmw) -> None:
    _entry, device = await _car(hass, no_bmw)
    calls: list[dict] = []
    config = device_trigger.TRIGGER_SCHEMA(
        {
            "platform": "device",
            "domain": DOMAIN,
            "device_id": device.id,
            "type": "parked_unlocked",
            "for": {"minutes": 2},
        }
    )
    detach = await device_trigger.async_attach_trigger(
        hass, config, lambda run, context=None: calls.append(run), {"trigger_data": {}}
    )
    started = {"vin": VIN, "situation": "parked_unlocked", "active": True}

    # Ended before it lasted: nothing.
    hass.bus.async_fire(EVENT_SITUATION, started)
    hass.bus.async_fire(EVENT_SITUATION, {**started, "active": False})
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=3))
    await hass.async_block_till_done()
    assert calls == []

    # Another situation of the car, or another car's: nothing either.
    hass.bus.async_fire(EVENT_SITUATION, {**started, "situation": "parked_window_open"})
    hass.bus.async_fire(EVENT_SITUATION, {**started, "vin": "OTHER"})

    hass.bus.async_fire(EVENT_SITUATION, started)
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=3))
    await hass.async_block_till_done()
    (call,) = calls
    assert call["trigger"]["type"] == "parked_unlocked"

    detach()


async def test_the_trip_flag_follows_an_open_trip(hass: HomeAssistant, no_bmw) -> None:
    entry, _device = await _car(hass, no_bmw)
    coordinator = entry.runtime_data.coordinator
    entity_id = _binary(hass, entry, "trip_in_progress").entity_id
    progress = {"start": STAMP, "start_place": {"label": "Home"}, "distance_km": 1.0}

    with (
        patch.object(coordinator, "has_open_trip", return_value=True),
        patch.object(coordinator, "open_trip_progress", return_value=progress),
    ):
        async_dispatcher_send(hass, coordinator.signal_trip_active, VIN)
        await hass.async_block_till_done()
        state = hass.states.get(entity_id)
        assert state.state == "on"
        assert state.attributes["start_location"] == "Home"

        # Within the minute, a stream message does not rewrite the figures.
        progress["distance_km"] = 2.0
        async_dispatcher_send(hass, coordinator.signal_update, VIN, DOOR)
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).attributes["distance_km"] == 1.0

    # The closing edge is written at once.
    async_dispatcher_send(hass, coordinator.signal_trip_active, VIN)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "off"
    assert "distance_km" not in hass.states.get(entity_id).attributes

    # A value that is not a boolean leaves the door sensor as it was.
    door = _binary(hass, entry, DOOR).entity_id
    await coordinator.async_handle_message(
        {"vin": VIN, "data": {DOOR: {"value": 3, "timestamp": STAMP}}}
    )
    await hass.async_block_till_done()
    assert hass.states.get(door).state == "on"
