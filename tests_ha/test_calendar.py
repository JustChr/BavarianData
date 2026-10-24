"""The service calendar as Home Assistant serves it.

The events are ``service_calendar.py``'s, tested in ``tests/``; this checks the
platform: the calendar comes back from the registry before any data, follows
the car's service list, and answers the calendar's own service.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.bavariandata.const import DOMAIN
from custom_components.bavariandata.service_calendar import CBS

from .conftest import VIN
from .conftest import make_entry as _entry
from .conftest import set_up as _set_up

ITEMS = (
    '[{"date":"2027-10","description":"-","id":3,"messageType":"CBS","status":"OK",'
    '"title":"Brake fluid","text":"-","unitOfLengthRemaining":"-"}]'
)


async def test_the_calendar_follows_the_service_list(hass: HomeAssistant, no_bmw) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    # Enabled up front (it is off by default), and restored before any data.
    calendar_id = (
        er.async_get(hass)
        .async_get_or_create("calendar", DOMAIN, f"{VIN}_service_calendar", config_entry=entry)
        .entity_id
    )
    await _set_up(hass, entry)
    assert hass.states.get(calendar_id).state == "off"

    await entry.runtime_data.coordinator.async_handle_message(
        {"vin": VIN, "data": {CBS: {"value": ITEMS, "timestamp": dt_util.utcnow().isoformat()}}}
    )
    await hass.async_block_till_done()

    state = hass.states.get(calendar_id)
    assert state.attributes["message"] == "Brake fluid"
    assert state.attributes["all_day"] is True

    answer = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": calendar_id,
            "start_date_time": "2027-09-01 00:00:00",
            "end_date_time": "2027-12-31 00:00:00",
        },
        blocking=True,
        return_response=True,
    )
    assert [event["summary"] for event in answer[calendar_id]["events"]] == ["Brake fluid"]

    nothing = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": calendar_id,
            "start_date_time": "2026-01-01 00:00:00",
            "end_date_time": "2026-02-01 00:00:00",
        },
        blocking=True,
        return_response=True,
    )
    assert nothing[calendar_id]["events"] == []

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_a_second_car_gets_its_calendar_from_its_service_list(
    hass: HomeAssistant, no_bmw
) -> None:
    entry = _entry()
    await _set_up(hass, entry)
    other = "WBATEST0000000002"

    await entry.runtime_data.coordinator.async_handle_message(
        {"vin": other, "data": {CBS: {"value": ITEMS, "timestamp": dt_util.utcnow().isoformat()}}}
    )
    await hass.async_block_till_done()

    assert er.async_get(hass).async_get_entity_id("calendar", DOMAIN, f"{other}_service_calendar")
