"""The device tracker and the device it hangs on, in a real Home Assistant.

A tracker has to come back from the registry before BMW streams a position
again (that can take hours), seeded from its last state.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import mock_restore_cache

from custom_components.bavariandata.const import DOMAIN

from .conftest import VIN
from .conftest import make_entry as _entry
from .conftest import set_up as _set_up

LAT = "vehicle.cabin.infotainment.navigation.currentLocation.latitude"
LON = "vehicle.cabin.infotainment.navigation.currentLocation.longitude"
OTHER = "WBATEST0000000002"


async def test_a_tracker_comes_back_where_it_was(hass: HomeAssistant, no_bmw) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    tracker = registry.async_get_or_create(
        "device_tracker", DOMAIN, f"{VIN}_tracker", config_entry=entry
    ).entity_id
    # A car the user turned the tracker off for stays without one.
    registry.async_get_or_create(
        "device_tracker",
        DOMAIN,
        f"{OTHER}_tracker",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    mock_restore_cache(hass, [State(tracker, "not_home", {"latitude": 48.1, "longitude": 11.5})])

    await _set_up(hass, entry)

    state = hass.states.get(tracker)
    assert state.attributes["latitude"] == 48.1
    assert state.attributes["longitude"] == 11.5
    coordinator = entry.runtime_data.coordinator
    assert coordinator.get_state(VIN, LAT).value == 48.1
    assert coordinator.get_state(OTHER, LAT) is None


async def test_nothing_to_restore_and_a_bad_coordinate(hass: HomeAssistant, no_bmw) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    tracker = registry.async_get_or_create(
        "device_tracker", DOMAIN, f"{VIN}_tracker", config_entry=entry
    ).entity_id
    mock_restore_cache(hass, [State(tracker, "not_home", {"latitude": 48.1})])
    await _set_up(hass, entry)
    coordinator = entry.runtime_data.coordinator
    assert coordinator.get_state(VIN, LAT) is None

    stamp = dt_util.utcnow().isoformat()
    await coordinator.async_handle_message(
        {
            "vin": VIN,
            "data": {
                LAT: {"value": "somewhere", "unit": None, "timestamp": stamp},
                LON: {"value": 11.5, "unit": None, "timestamp": stamp},
            },
        }
    )
    await hass.async_block_till_done()

    assert hass.states.get(tracker).attributes.get("latitude") is None


async def test_the_device_carries_basic_data_and_new_fields_get_a_name(
    hass: HomeAssistant, no_bmw
) -> None:
    entry = _entry(
        vehicle_metadata={
            VIN: {"brand": "BMW", "modelName": "i5 eDrive40", "puStep": "0724", "bodyType": "G60"}
        }
    )
    await _set_up(hass, entry)
    coordinator = entry.runtime_data.coordinator
    await coordinator.async_handle_message(
        {
            "vin": VIN,
            "data": {
                "vehicle.body.madeUp.newField": {
                    "value": 1,
                    "unit": None,
                    "timestamp": dt_util.utcnow().isoformat(),
                }
            },
        }
    )
    await hass.async_block_till_done()

    (device,) = [
        d
        for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if (DOMAIN, VIN) in d.identifiers
    ]
    assert (device.sw_version, device.hw_version) == ("0724", "G60")
    sensor = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{VIN}_vehicle.body.madeUp.newField"
    )
    assert hass.states.get(sensor).name.endswith("Body Madeup Newfield")


async def test_the_tracker_moves_on_whole_fixes_only(hass: HomeAssistant, no_bmw) -> None:
    """A half fix plots no phantom point, and a repeated fix writes no new row.

    Home Assistant's tracker forces every write when it isn't polled; on the
    live i5 that left 94 % of the tracker's recorder rows copies of the one
    before, and three in nine position changes a mixed point for ~40 ms.
    """

    entry = _entry()
    await _set_up(hass, entry)
    coordinator = entry.runtime_data.coordinator

    async def send(**halves: tuple[float, str]) -> None:
        data = {
            {"lat": LAT, "lon": LON}[part]: {"value": value, "unit": None, "timestamp": stamp}
            for part, (value, stamp) in halves.items()
        }
        await coordinator.async_handle_message({"vin": VIN, "data": data})
        await hass.async_block_till_done()

    fix1, fix2 = "2026-11-21T10:39:57.000Z", "2026-11-21T10:40:04.000Z"
    await send(lat=(48.1, fix1), lon=(11.5, fix1))
    tracker = er.async_get(hass).async_get_entity_id("device_tracker", DOMAIN, f"{VIN}_tracker")
    first = hass.states.get(tracker)
    assert (first.attributes["latitude"], first.attributes["longitude"]) == (48.1, 11.5)

    changes: list[State] = []
    hass.bus.async_listen(
        "state_changed",
        lambda event: (
            changes.append(event.data["new_state"]) if event.data["entity_id"] == tracker else None
        ),
    )

    await send(lat=(48.1, fix1))  # BMW redelivers a half of the same fix
    await send(lon=(11.6, fix2))  # the next fix's first half
    assert changes == []
    assert hass.states.get(tracker).last_updated == first.last_updated

    await send(lat=(48.2, fix2))
    assert [(s.attributes["latitude"], s.attributes["longitude"]) for s in changes] == [
        (48.2, 11.6)
    ]
