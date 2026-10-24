"""The sensors across a restart, and what each one listens to, in a real Home Assistant.

A derived sensor (the SoC estimate, the charged-energy counters) is the only
place its figure survives a restart: it hands it back to the coordinator when it
is restored, and a garbled saved value must not take the platform down. After
that every sensor listens to its own signal, for its own car only.
"""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    mock_restore_cache,
    mock_restore_cache_with_extra_data,
)

from custom_components.bavariandata import sensor_plan
from custom_components.bavariandata.const import DOMAIN
from custom_components.bavariandata.structured_values import CHECK_CONTROL

from .conftest import VIN
from .conftest import make_entry as _entry
from .conftest import set_up as _set_up

OTHER = "WBATEST0000000002"
ODOMETER = "vehicle.vehicle.travelledDistance"
TRIP_END_SOC = "vehicle.trip.segment.end.drivetrain.batteryManagement.hvSoc"
MADE_UP = "vehicle.madeUp.distanceToSomewhere"
STAMP = "2026-10-20T08:00:00+00:00"

DERIVED = (
    "soc_estimate",
    "soc_rate",
    "charge_eta",
    "charged_energy_total",
    "charged_energy_session",
    # Not the cost per 100 km: that one waits for an odometer reading.
    sensor_plan.CHARGING_ENERGY_MONTH,
    *sensor_plan.CHARGING_COST,
    sensor_plan.BATTERY_HEALTH,
    sensor_plan.DRIVING_DISTANCE,
    sensor_plan.REAL_RANGE,
    sensor_plan.VEHICLE_REPORT,
    sensor_plan.MILEAGE_ALLOWANCE,
    "tyre_status",
    "tyre_front_left",
)
PRICED = {"price_mode": "fixed", "price_fixed": 0.3, "price_currency": "EUR"}


def _registered(hass: HomeAssistant, entry, keys) -> dict[str, str]:
    registry = er.async_get(hass)
    return {
        key: registry.async_get_or_create(
            "sensor", DOMAIN, f"{VIN}_{key}", config_entry=entry
        ).entity_id
        for key in keys
    }


def _saved(entity_id: str, native, unit=None, **attributes):
    return (
        State(entity_id, str(native), attributes),
        {"native_value": native, "native_unit_of_measurement": unit},
    )


async def test_the_charging_figures_come_back_into_the_coordinator(
    hass: HomeAssistant, no_bmw
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    ids = _registered(
        hass,
        entry,
        ("soc_estimate", "soc_rate", "charged_energy_total", "charged_energy_session"),
    )
    mock_restore_cache_with_extra_data(
        hass,
        [
            _saved(ids["soc_estimate"], 62.5, "%", timestamp=STAMP, meter_reading_kwh=1234.5),
            # No timestamp saved: the state's own last change places it.
            _saved(ids["soc_rate"], 12.0, "%/h"),
            _saved(ids["charged_energy_total"], 500.25, "kWh"),
            _saved(ids["charged_energy_session"], 7.5, "kWh", last_reset=STAMP),
        ],
    )

    await _set_up(hass, entry)

    coordinator = entry.runtime_data.coordinator
    assert coordinator.get_soc_estimate(VIN) == 62.5
    assert coordinator.get_soc_rate(VIN) == 12.0
    assert coordinator.get_lifetime_energy_kwh(VIN) == 500.25
    assert coordinator.get_session_energy_kwh(VIN) == 7.5
    assert coordinator.get_session_start(VIN) == dt_util.parse_datetime(STAMP)
    assert hass.states.get(ids["soc_estimate"]).state == "62.5"
    assert hass.states.get(ids["charged_energy_total"]).state == "500.25"


async def test_a_garbled_saved_figure_is_dropped_not_fatal(hass: HomeAssistant, no_bmw) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    keys = ("soc_estimate", "soc_rate", "charged_energy_total", "charged_energy_session")
    ids = _registered(hass, entry, keys)
    mock_restore_cache_with_extra_data(hass, [_saved(ids[key], "garbled", None) for key in keys])

    await _set_up(hass, entry)

    coordinator = entry.runtime_data.coordinator
    assert coordinator.get_soc_estimate(VIN) is None
    assert coordinator.get_soc_rate(VIN) is None
    assert coordinator.get_lifetime_energy_kwh(VIN) is None
    assert coordinator.get_session_energy_kwh(VIN) is None
    for key in keys:
        assert hass.states.get(ids[key]).state == "unknown"


async def test_descriptor_sensors_come_back_as_they_were_saved(hass: HomeAssistant, no_bmw) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    ids = _registered(hass, entry, (CHECK_CONTROL, TRIP_END_SOC, MADE_UP, ODOMETER))
    messages = [{"type": "tyre", "id": 1, "title": "Check tyre pressure"}]
    resolved = [{"type": "oil", "id": 2, "title": "Top up oil"}]
    mock_restore_cache(
        hass,
        [
            # Saved by a build that kept no native data: the state is all there is.
            State(
                ids[CHECK_CONTROL],
                "1",
                {"items": messages, "resolved": resolved, "timestamp": STAMP},
            ),
            # BMW's zero for "no reading", saved before that was recognised.
            State(ids[TRIP_END_SOC], "0", {"unit_of_measurement": "%"}),
            # A field the catalogue does not know, in a length unit.
            State(ids[MADE_UP], "12.5", {"unit_of_measurement": "km"}),
            # Converted on the way out by an imperial install: not restored.
            State(ids[ODOMETER], "6213.7", {"unit_of_measurement": "mi"}),
        ],
    )

    await _set_up(hass, entry)

    coordinator = entry.runtime_data.coordinator
    check = hass.states.get(ids[CHECK_CONTROL])
    assert check.state == "1"
    assert check.attributes["resolved"] == resolved
    assert coordinator.get_state(VIN, CHECK_CONTROL).value == messages
    assert coordinator.get_state(VIN, TRIP_END_SOC) is None
    made_up = hass.states.get(ids[MADE_UP])
    assert made_up.state == "12.5"
    assert made_up.attributes["device_class"] == "distance"
    restored = coordinator.get_state(VIN, MADE_UP)
    assert restored.unit == "km"
    assert restored.timestamp  # from the state's last change
    assert coordinator.get_state(VIN, ODOMETER) is None


async def test_every_sensor_listens_for_its_own_car_only(hass: HomeAssistant, no_bmw) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options=PRICED)
    ids = _registered(hass, entry, DERIVED)

    await _set_up(hass, entry)
    for key, entity_id in ids.items():
        assert hass.states.get(entity_id) is not None, key

    coordinator = entry.runtime_data.coordinator
    # Another car's news changes nothing here; this car's rewrites the state.
    for signal in (
        coordinator.signal_soc_estimate,
        coordinator.signal_energy,
        coordinator.signal_history,
        coordinator.signal_trips,
        coordinator.signal_mileage,
        coordinator.signal_tyre,
    ):
        async_dispatcher_send(hass, signal, OTHER)
        async_dispatcher_send(hass, signal, VIN)
    for descriptor in (ODOMETER, "vehicle.drivetrain.batteryManagement.header", MADE_UP):
        async_dispatcher_send(hass, coordinator.signal_update, OTHER, descriptor)
        async_dispatcher_send(hass, coordinator.signal_update, VIN, descriptor)
    # The heartbeat twice: the second finds nothing moved.
    async_dispatcher_send(hass, coordinator.signal_diagnostics)
    async_dispatcher_send(hass, coordinator.signal_diagnostics)
    await hass.async_block_till_done()

    # Just past midnight the month's figures and the allowance are rewritten.
    async_fire_time_changed(
        hass,
        dt_util.start_of_local_day(dt_util.now() + timedelta(days=1)) + timedelta(seconds=10),
    )
    await hass.async_block_till_done()

    assert hass.states.get(ids["charging_cost_month"]).attributes["unit_of_measurement"] == "EUR"
    assert hass.states.get(ids[sensor_plan.DRIVING_DISTANCE]).state == "0"

    # Unloading lets go of every signal and timer.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    for signal in (coordinator.signal_soc_estimate, coordinator.signal_energy):
        async_dispatcher_send(hass, signal, VIN)
    await hass.async_block_till_done()


async def test_a_new_estimate_or_energy_reading_reaches_its_sensor(
    hass: HomeAssistant, no_bmw
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    ids = _registered(
        hass, entry, ("soc_estimate", "soc_rate", "charged_energy_total", "charged_energy_session")
    )
    await _set_up(hass, entry)
    coordinator = entry.runtime_data.coordinator

    coordinator.restore_soc_cache(VIN, estimate=70.0, rate=10.0, timestamp=dt_util.utcnow())
    coordinator.restore_lifetime_energy(VIN, 600.0)
    coordinator.restore_session_energy(VIN, 3.25, dt_util.utcnow())
    async_dispatcher_send(hass, coordinator.signal_soc_estimate, VIN)
    async_dispatcher_send(hass, coordinator.signal_energy, VIN)
    await hass.async_block_till_done()

    assert hass.states.get(ids["soc_estimate"]).state == "70.0"
    assert hass.states.get(ids["soc_rate"]).state == "10.0"
    assert hass.states.get(ids["charged_energy_total"]).state == "600.0"
    assert hass.states.get(ids["charged_energy_session"]).state == "3.25"


async def test_figures_already_held_win_over_saved_ones(hass: HomeAssistant, no_bmw) -> None:
    """A reload keeps the coordinator: what it holds is newer than any saved state."""

    entry = _entry()
    entry.add_to_hass(hass)
    ids = _registered(
        hass, entry, ("soc_estimate", "soc_rate", "charged_energy_total", "charged_energy_session")
    )
    await _set_up(hass, entry)
    coordinator = entry.runtime_data.coordinator
    coordinator.restore_soc_cache(VIN, estimate=55.0, rate=8.0, timestamp=dt_util.utcnow())
    coordinator.restore_lifetime_energy(VIN, 42.0)
    coordinator.restore_session_energy(VIN, 1.5, dt_util.utcnow())

    # Re-add the entities on the same coordinator, as a platform reload does.
    platform = next(
        p
        for p in hass.data["entity_platform"][DOMAIN]
        if p.domain == "sensor" and p.config_entry is entry
    )
    for entity_id in ids.values():
        await platform.async_remove_entity(entity_id)
    await platform.async_add_entities(
        [
            sensor_cls(coordinator, VIN)
            for sensor_cls in (
                _cls("soc_estimate"),
                _cls("soc_rate"),
                _cls("charged_energy_total"),
                _cls("charged_energy_session"),
            )
        ]
    )
    await hass.async_block_till_done()

    assert hass.states.get(ids["soc_estimate"]).state == "55.0"
    assert hass.states.get(ids["soc_rate"]).state == "8.0"
    assert hass.states.get(ids["charged_energy_total"]).state == "42.0"
    assert hass.states.get(ids["charged_energy_session"]).state == "1.5"


def _cls(kind: str):
    from custom_components.bavariandata import sensor

    return sensor._DERIVED[kind]


async def test_the_account_sensors_come_back_and_follow_the_stream(
    hass: HomeAssistant, no_bmw
) -> None:
    entry = _entry(entry_id="acct")
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    ids = {
        kind: registry.async_get_or_create(
            "sensor", DOMAIN, sensor_plan.diagnostic_unique_id("acct", kind), config_entry=entry
        ).entity_id
        for kind in sensor_plan.DIAGNOSTIC_TYPES
    }
    mock_restore_cache(
        hass,
        [
            State(ids["last_message"], STAMP),
            State(ids["last_telematic_api"], "unknown"),
            State(ids["connection_status"], "connected"),
        ],
    )
    await _set_up(hass, entry)
    assert hass.states.get(ids["last_message"]).state == STAMP

    coordinator = entry.runtime_data.coordinator
    coordinator.last_telematic_api_at = dt_util.parse_datetime(STAMP)
    async_dispatcher_send(hass, coordinator.signal_diagnostics)
    await hass.async_block_till_done()
    assert hass.states.get(ids["last_telematic_api"]).state == STAMP
