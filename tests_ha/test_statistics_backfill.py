"""Long-term statistics published into a real recorder.

``history/backfill.py`` is the one history module that needs Home Assistant's
recorder, so it is tested here rather than in ``tests/``: the series land in
the recorder's database and are read back from its metadata.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from homeassistant.components.recorder.statistics import get_metadata
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.bavariandata.debug import set_debug_enabled
from custom_components.bavariandata.history.backfill import StatisticsPublisher, statistic_id
from custom_components.bavariandata.history.models import ChargingSession
from custom_components.bavariandata.history.store import HistoryStore
from custom_components.bavariandata.history.trips import Trip

from .conftest import VIN

START = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
ENERGY = statistic_id(VIN, "charging_energy")
HOME = statistic_id(VIN, "charging_energy_home")
COST = statistic_id(VIN, "charging_cost")
DISTANCE = statistic_id(VIN, "driving_distance")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """The conftest's, with the recorder first: it must exist before ``hass``."""

    yield


def _store(hass: HomeAssistant, *, home: str = "Home") -> HistoryStore:
    store = HistoryStore(hass, "entry-1")
    store.add_session(
        ChargingSession(
            vin=VIN,
            start=START,
            end=START + timedelta(hours=1),
            energy_kwh=10.0,
            location={"zone": home},
            cost={"amount": 3.0, "currency": "EUR", "source": "tariff"},
        )
    )
    store.add_session(
        ChargingSession(
            vin=VIN,
            start=START + timedelta(days=1),
            end=START + timedelta(days=1, hours=1),
            energy_kwh=40.0,
            location={"zone": None, "address": "Motorway services"},
        )
    )
    store.add_trip(
        Trip(
            vin=VIN,
            start=START - timedelta(hours=2),
            end=START - timedelta(hours=1),
            distance_km=30.0,
        )
    )
    return store


async def _published(hass: HomeAssistant) -> dict[str, dict]:
    await async_wait_recording_done(hass)
    found = await hass.async_add_executor_job(
        lambda: get_metadata(hass, statistic_ids={ENERGY, HOME, COST, DISTANCE})
    )
    return {stat_id: meta for stat_id, (_, meta) in found.items()}


async def test_the_store_is_mirrored_into_four_series(hass: HomeAssistant) -> None:
    publisher = StatisticsPublisher(hass, _store(hass), name_for=lambda vin: "i5")

    written = await publisher.async_publish()

    assert written == {ENERGY: 2, HOME: 1, DISTANCE: 1, COST: 1}
    published = await _published(hass)
    assert {k: (m["name"], m["unit_of_measurement"]) for k, m in published.items()} == {
        ENERGY: ("i5 charging energy", "kWh"),
        HOME: ("i5 charging energy at home", "kWh"),
        DISTANCE: ("i5 driving distance", "km"),
        COST: ("i5 charging cost", "EUR"),
    }


async def test_home_is_whatever_the_home_zone_is_called(hass: HomeAssistant) -> None:
    hass.states.async_set("zone.home", "0", {"friendly_name": "Zuhause"})
    publisher = StatisticsPublisher(hass, _store(hass, home="Zuhause"), name_for=lambda vin: "i5")

    assert (await publisher.async_publish())[HOME] == 1


async def test_an_unchanged_store_is_not_rewritten(hass: HomeAssistant) -> None:
    publisher = StatisticsPublisher(hass, _store(hass), name_for=lambda vin: "i5")
    first = await publisher.async_publish(vin=VIN)

    assert await publisher.async_publish(vin=VIN) == {}
    assert await publisher.async_publish(vin=VIN, force=True) == first


async def test_dropping_the_tariff_removes_the_cost_series(hass: HomeAssistant) -> None:
    store = _store(hass)
    publisher = StatisticsPublisher(hass, store, name_for=lambda vin: "i5")
    await publisher.async_publish()
    assert COST in await _published(hass)

    for session in store.sessions(VIN):
        session.cost = None
    written = await publisher.async_publish(force=True)

    assert COST not in written
    assert set(await _published(hass)) == {ENERGY, HOME, DISTANCE}


async def test_an_empty_series_is_counted_but_not_written(hass: HomeAssistant) -> None:
    store = HistoryStore(hass, "entry-1")
    store.add_trip(Trip(vin=VIN, start=START, end=START + timedelta(hours=1), distance_km=5.0))
    publisher = StatisticsPublisher(hass, store, name_for=lambda vin: "i5")

    assert await publisher.async_publish() == {ENERGY: 0, HOME: 0, DISTANCE: 1}
    assert set(await _published(hass)) == {DISTANCE}


async def test_removing_a_car_or_everything_clears_its_series(hass: HomeAssistant) -> None:
    publisher = StatisticsPublisher(hass, _store(hass), name_for=lambda vin: "i5")
    await publisher.async_publish()

    await publisher.async_remove_vehicle(VIN)
    assert await _published(hass) == {}

    await publisher.async_publish(force=True)
    await publisher.async_remove()
    assert await _published(hass) == {}
    # The fingerprints went too, so the next publish writes again.
    assert await publisher.async_publish()


async def test_nothing_is_published_when_turned_off(hass: HomeAssistant) -> None:
    publisher = StatisticsPublisher(hass, _store(hass), name_for=lambda vin: "i5", enabled=False)

    assert await publisher.async_publish() == {}
    assert await _published(hass) == {}


async def test_without_a_recorder_nothing_happens(hass: HomeAssistant) -> None:
    hass.config.components.remove("recorder")
    publisher = StatisticsPublisher(hass, _store(hass), name_for=lambda vin: "i5")

    assert not publisher.available
    assert await publisher.async_publish() == {}
    await publisher.async_remove_vehicle(VIN)
    await publisher.async_remove()


async def test_a_recorder_that_refuses_to_clear_does_not_break_publishing(
    hass: HomeAssistant,
) -> None:
    publisher = StatisticsPublisher(hass, _store(hass), name_for=lambda vin: "i5")

    with patch("homeassistant.components.recorder.get_instance", side_effect=RuntimeError("gone")):
        written = await publisher.async_publish()

    assert written[ENERGY] == 2


async def test_debug_logging_names_what_was_written(hass: HomeAssistant, caplog) -> None:
    publisher = StatisticsPublisher(hass, _store(hass), name_for=lambda vin: "i5")
    set_debug_enabled(True, entry_id="entry-1")
    try:
        with caplog.at_level(logging.DEBUG, logger="custom_components.bavariandata"):
            await publisher.async_publish()
    finally:
        set_debug_enabled(False, entry_id="entry-1")

    assert "[stats] published" in caplog.text
