"""The history store against Home Assistant's real storage.

The repair rules are ``history/sessions.py``'s, tested in ``tests/``; this
checks what the store does with what it reads back, and that each one-time
repair saves only when it changed something.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from homeassistant.core import HomeAssistant

from custom_components.bavariandata.debug import set_debug_enabled
from custom_components.bavariandata.history.models import ChargingSession
from custom_components.bavariandata.history.store import SCHEMA_VERSION, HistoryStore
from custom_components.bavariandata.history.trips import Trip

from .conftest import VIN

KEY = "bavariandata_entry-1_history"
START = datetime.now(timezone.utc) - timedelta(days=3)
OTHER = "WBATEST0000000002"


def _session(vin: str = VIN, hours: int = 0, **fields) -> ChargingSession:
    start = START + timedelta(hours=hours)
    return ChargingSession(vin=vin, start=start, end=start + timedelta(hours=1), **fields)


async def test_an_unreadable_store_starts_empty(hass: HomeAssistant, caplog) -> None:
    store = HistoryStore(hass, "entry-1")
    with patch("homeassistant.helpers.storage.Store.async_load", side_effect=ValueError("bad")):
        await store.async_load()

    assert "starting empty" in caplog.text
    assert store.sessions() == []


async def test_a_newer_schema_is_left_untouched(hass: HomeAssistant, hass_storage, caplog) -> None:
    hass_storage[KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": KEY,
        "data": {"schema": SCHEMA_VERSION + 1, "sessions": {VIN: [{"bogus": True}]}},
    }
    store = HistoryStore(hass, "entry-1")
    await store.async_load()

    assert "newer version" in caplog.text
    assert store.sessions() == []


async def test_the_one_time_repairs_change_only_what_they_must(hass: HomeAssistant, caplog) -> None:
    store = HistoryStore(hass, "entry-1", remember_locations=True)
    await store.async_load()
    store.add_session(_session(hours=0, location={"zone": None, "lat": 48.1, "lon": 11.5}))
    store.add_session(_session(hours=2, location={"zone": None, "lat": 1.0, "lon": 1.0}))
    store.add_session(_session(hours=4, location={"zone": None}))
    store.add_session(_session(hours=6, location={"zone": "Home"}))
    store.add_session(
        _session(
            hours=8,
            location={"zone": None, "address": "Motorway services"},
            energy_mix={"grid_kwh": 10.0},
            cost={"amount": 3.0, "currency": "EUR", "source": "tariff"},
        )
    )
    store.add_session(
        _session(
            hours=10,
            location={"zone": "Home"},
            grid_source="meter",
            grid_kwh=40.0,
            energy_mix={"grid_kwh": 10.0},
            cost={"amount": 3.0, "currency": "EUR", "source": "tariff"},
        )
    )

    def home_only(lat, lon):
        return "Home" if lat == 48.1 else None

    set_debug_enabled(True, entry_id="entry-1")
    try:
        with caplog.at_level(logging.DEBUG, logger="custom_components.bavariandata"):
            assert store.reresolve_zones(None) == 0
            assert store.reresolve_zones(home_only) == 1
            assert store.drop_house_figures_elsewhere() == 1
            assert store.flag_unbilled_meter_energy() == 1
    finally:
        set_debug_enabled(False, entry_id="entry-1")

    # Idempotent: a second run finds nothing.
    assert store.reresolve_zones(home_only) == 0
    assert store.drop_house_figures_elsewhere() == 0
    assert store.flag_unbilled_meter_energy() == 0
    resolved = next(s for s in store.sessions(VIN) if s.start == START)
    assert resolved.location == {"zone": "Home", "lat": 48.1, "lon": 11.5}
    assert "re-resolved 1" in caplog.text
    assert "cleared house mix/cost" in caplog.text
    assert "short-billed" in caplog.text


async def test_reads_filter_by_car_time_and_count(hass: HomeAssistant) -> None:
    store = HistoryStore(hass, "entry-1")
    await store.async_load()
    for hours in (0, 24, 48):
        store.add_session(_session(hours=hours))
        trip = Trip(
            vin=OTHER, start=START + timedelta(hours=hours), end=START + timedelta(hours=hours + 1)
        )
        trip.distance_km = 10.0
        store.add_trip(trip)
    store.add_session(_session(vin=OTHER, hours=1))

    assert [s.vin for s in store.sessions()][:2] == [VIN, VIN]
    assert len(store.sessions(start=START + timedelta(hours=12))) == 2
    assert len(store.sessions(VIN, end=START + timedelta(hours=12))) == 1
    assert len(store.sessions(limit=2)) == 2
    assert len(store.trips()) == 3
    assert (
        len(store.trips(OTHER, start=START + timedelta(hours=12), end=START + timedelta(hours=30)))
        == 1
    )
    assert len(store.trips(limit=1)) == 1


async def test_an_import_that_brings_nothing_new_saves_nothing(hass: HomeAssistant) -> None:
    store = HistoryStore(hass, "entry-1")
    await store.async_load()

    assert store.import_cardata_sessions(VIN, None) == (0, 0)
    assert store.import_cardata_sessions(VIN, [{"no": "session"}]) == (0, 0)


async def test_an_import_seen_before_adds_nothing(hass: HomeAssistant) -> None:
    store = HistoryStore(hass, "entry-1")
    await store.async_load()
    begin = int(START.timestamp())
    raw = {
        "startTime": begin,
        "endTime": begin + 3600,
        "energyConsumedFromPowerGridKwh": 15.9,
        "displayedStartSoc": 66,
        "displayedSoc": 82,
    }

    assert store.import_cardata_sessions(VIN, [raw]) == (1, 0)
    # The merge counts every overlap as updated, changed or not.
    assert store.import_cardata_sessions(VIN, [raw]) == (0, 1)
    assert len(store.sessions(VIN)) == 1
