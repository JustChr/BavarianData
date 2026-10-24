"""The coverage record kept in Home Assistant's storage.

What a report says is ``coverage.py``'s, tested in ``tests/``; this checks the
store: what it reads back, what it forgives, and when its grace clock restarts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from homeassistant.core import HomeAssistant

from custom_components.bavariandata.coverage_store import CoverageStore

from .conftest import VIN

KEY = "bavariandata_entry-1_coverage"
SOC = "vehicle.drivetrain.batteryManagement.header"


def _stored(hass_storage, data: dict) -> None:
    hass_storage[KEY] = {"version": 1, "minor_version": 1, "key": KEY, "data": data}


async def test_a_record_reads_back_and_a_new_selection_restarts_the_clock(
    hass: HomeAssistant, hass_storage
) -> None:
    _stored(
        hass_storage,
        {
            "seen": {
                VIN: {SOC: "2026-10-01T08:00:00+00:00"},
                # Written before first sightings were kept: recovered from its arrivals.
                "older": {SOC: "2026-09-15T08:00:00+00:00"},
                "broken": "not a dict",
            },
            "first_seen": {VIN: "2026-10-01T08:00:00+00:00"},
            "started_at": "2026-09-01T00:00:00",
            "sections": ["charging"],
        },
    )
    store = CoverageStore(hass, "entry-1", sections=["charging", "tyres"])
    await store.async_load()

    assert store.seen_descriptors(VIN) == {SOC}
    assert store.seen_descriptors("broken") == set()
    # The selection changed while Home Assistant was down.
    assert store.monitoring_since(VIN) > datetime(2026, 10, 1, tzinfo=timezone.utc)


async def test_a_naive_start_reads_as_utc_and_a_bad_one_as_none(
    hass: HomeAssistant, hass_storage
) -> None:
    _stored(hass_storage, {"seen": {}, "started_at": "2026-09-01T00:00:00", "sections": []})
    store = CoverageStore(hass, "entry-1", sections=[])
    await store.async_load()
    assert store.monitoring_since("new car") == datetime(2026, 9, 1, tzinfo=timezone.utc)

    _stored(hass_storage, {"seen": [], "first_seen": [], "started_at": "yesterday"})
    store = CoverageStore(hass, "entry-1", sections=[])
    await store.async_load()
    assert store.monitoring_since("new car") is not None


async def test_an_unreadable_store_starts_fresh(hass: HomeAssistant, caplog) -> None:
    store = CoverageStore(hass, "entry-1", sections=["charging"])
    with patch("homeassistant.helpers.storage.Store.async_load", side_effect=ValueError("corrupt")):
        await store.async_load()

    assert "starting fresh" in caplog.text
    assert store.reports() == []


async def test_sightings_are_kept_and_forgotten_per_car(hass: HomeAssistant) -> None:
    store = CoverageStore(hass, "entry-1", sections=["charging"])
    await store.async_load()

    store.note_seen("", [SOC])
    store.note_seen(VIN, [SOC, ""])
    store.note_seen(VIN, [SOC])
    assert store.seen_descriptors(VIN) == {SOC}
    assert [report.vin for report in store.reports()] == [VIN]

    store.forget_vehicle(VIN)
    assert store.seen_descriptors(VIN) == set()


async def test_a_new_selection_restarts_the_clock_and_the_same_one_does_not(
    hass: HomeAssistant,
) -> None:
    store = CoverageStore(hass, "entry-1", sections=["charging"])
    await store.async_load()
    started = store.monitoring_since("new car")

    store.update_sections(["charging"])
    assert store.monitoring_since("new car") == started

    store.update_sections(["charging", "tyres"])
    assert store.monitoring_since("new car") >= started
