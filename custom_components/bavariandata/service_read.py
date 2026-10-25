"""The read services: what the history and the live state say, answered as a response."""

from __future__ import annotations

import logging
from typing import Any
import voluptuous as vol
from homeassistant.util import dt as dt_util
from . import service_answers
from .evcc import BridgeConfig, bridge_payloads
from .history.stations import charging_places
from .history.summary import (
    driving_summary,
    sessions_in_month,
    summarise,
    trips_in_month,
)
from .coverage_issues import coverage_reports
from .service_context import (
    ServiceContext,
    month_bounds,
    service_bound,
)

_LOGGER = logging.getLogger(__name__)


GET_SESSIONS_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): str,
        vol.Optional("device_id"): str,
        vol.Optional("vin"): str,
        vol.Optional("from"): str,
        vol.Optional("to"): str,
        vol.Optional("limit"): vol.All(int, vol.Range(min=1)),
    }
)


async def async_handle_get_charging_sessions(ctx: ServiceContext, call: Any) -> dict[str, Any]:
    _entry_id, _entry, runtime, vin = ctx.target_car(call, required=False)
    if runtime.history is None:
        return {"sessions": []}
    sessions = runtime.history.sessions(
        vin,
        start=service_bound(call, "from"),
        end=service_bound(call, "to", end_of_day=True),
        limit=service_answers.as_limit(call.data.get("limit")),
    )
    return {"sessions": [session.to_dict() for session in sessions]}


GET_PLACES_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): str,
        vol.Optional("device_id"): str,
        vol.Optional("vin"): str,
        vol.Optional("from"): str,
        vol.Optional("to"): str,
    }
)


async def async_handle_get_charging_places(ctx: ServiceContext, call: Any) -> dict[str, Any]:
    # The places a car charged at, worked out from the stored sessions
    # (history/stations.py). Local only -- no quota. Without a car named on
    # a multi-car account it covers them all, like get_charging_sessions.
    _entry_id, _entry, runtime, vin = ctx.target_car(call, required=False)
    if runtime.history is None:
        return {"places": [], "locations_remembered": False}
    sessions = runtime.history.sessions(
        vin,
        start=service_bound(call, "from"),
        end=service_bound(call, "to", end_of_day=True),
    )
    return {
        "places": charging_places(sessions, home_zone=runtime.coordinator.home_zone_name()),
        "locations_remembered": runtime.history.remember_locations,
    }


GET_TRIPS_SCHEMA = GET_SESSIONS_SCHEMA


GET_DRIVING_SUMMARY_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): str,
        vol.Optional("device_id"): str,
        vol.Optional("vin"): str,
        vol.Optional("month"): str,  # "YYYY-MM"; defaults to this month
    }
)


GET_EFFICIENCY_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): str,
        vol.Optional("device_id"): str,
        vol.Optional("vin"): str,
    }
)


GET_EVCC_CONFIG_SCHEMA = GET_EFFICIENCY_SCHEMA


GET_MILEAGE_REPORT_SCHEMA = GET_EFFICIENCY_SCHEMA


GET_FUEL_HISTORY_SCHEMA = GET_EFFICIENCY_SCHEMA


async def async_handle_get_trips(ctx: ServiceContext, call: Any) -> dict[str, Any]:
    _entry_id, _entry, runtime, vin_filter = ctx.target_car(call, required=False)
    if runtime.history is None:
        return {"trips": [], "open_trips": []}
    start = service_bound(call, "from")
    end = service_bound(call, "to", end_of_day=True)
    trips = runtime.history.trips(
        vin_filter,
        start=start,
        end=end,
        limit=service_answers.as_limit(call.data.get("limit")),
    )
    # A drive still under way is not in the store -- it only exists in the
    # coordinator's memory -- so it rides alongside the recorded trips
    # rather than inside them: it has no end, no distance we'd stand
    # behind yet and nothing to classify.
    open_trips: list[dict[str, Any]] = []
    if service_answers.window_holds(start, end, dt_util.utcnow()):
        coordinator = runtime.coordinator
        for vin in coordinator.open_trip_vins():
            if vin_filter and vin != vin_filter:
                continue
            progress = coordinator.open_trip_progress(vin, include_track=True)
            if progress is not None:
                open_trips.append(progress)
    return {
        "trips": [trip.to_dict() for trip in trips],
        "open_trips": open_trips,
    }


async def async_handle_get_mileage_report(ctx: ServiceContext, call: Any) -> dict[str, Any]:
    """How far the car is driven and where it stands against its allowance.

    Read off the daily odometer log (``mileage.py``); no quota.
    """

    _entry_id, _entry, runtime, vin = ctx.target_car(call)
    return {"vin": vin, **runtime.coordinator.mileage_report(vin)}


async def async_handle_get_fuel_history(ctx: ServiceContext, call: Any) -> dict[str, Any]:
    """Refuels and fuel consumption, from the tank reading (``history/fuel.py``).

    Plus BMW's lifetime counters where the car sends them. No quota.
    """

    _entry_id, _entry, runtime, vin = ctx.target_car(call)
    return {"vin": vin, "fuel": runtime.coordinator.fuel_report(vin)}


async def async_handle_get_driving_summary(ctx: ServiceContext, call: Any) -> dict[str, Any]:
    _entry_id, _entry, runtime, vin = ctx.target_car(call)
    if runtime.history is None:
        return {"summary": {}, "month": None}
    coordinator = runtime.coordinator
    history = runtime.history

    year, month = month_bounds(call.data.get("month"))
    prev_year, prev_month = service_answers.previous_month(year, month)

    all_trips = history.trips(vin)
    month_trips = trips_in_month(all_trips, year=year, month=month, localize=dt_util.as_local)
    prev_trips = trips_in_month(
        all_trips, year=prev_year, month=prev_month, localize=dt_util.as_local
    )

    # The month's charging is needed twice over: for the estimated
    # driving cost (trips × the charging ledger's own cost-per-100km)
    # and for the plug-side energy balance, which is the headline
    # consumption figure precisely because it never touches a trip.
    month_sessions = sessions_in_month(
        history.sessions(vin),
        year=year,
        month=month,
        localize=dt_util.as_local,
    )
    hybrid = coordinator.is_plug_in_hybrid(vin)
    cost_per_100km = None
    if coordinator.pricing.enabled:
        cost_per_100km = summarise(month_sessions, hybrid=hybrid).get("cost_per_100km")

    summary = driving_summary(
        month_trips,
        prev_trips=prev_trips,
        cost_per_100km=cost_per_100km,
        currency=coordinator.pricing.currency,
        sessions=month_sessions,
        battery_capacity_kwh=coordinator.battery_capacity_kwh(vin),
        hybrid=hybrid,
    )
    return {"summary": summary, "month": f"{year:04d}-{month:02d}"}


async def async_handle_get_efficiency(ctx: ServiceContext, call: Any) -> dict[str, Any]:
    """Measured consumption, real range and the seasonal trend.

    The same profile the real-range entity publishes, plus the month
    trend it deliberately keeps out of the recorder -- one code path, so
    the card and the entity can never quote different numbers.
    """

    _entry_id, _entry, runtime, vin = ctx.target_car(call)
    if runtime.history is None:
        return {"efficiency": {}}
    coordinator = runtime.coordinator
    profile = coordinator.efficiency(vin)
    # The ledger's own cost-per-100 km and the month's solar share are
    # what turn an efficiency view into a running-costs one; they come
    # from the same sessions, so they are answered here rather than
    # making the card place a second call and risk a different window.
    month = dt_util.now()
    month_summary = summarise(
        sessions_in_month(
            runtime.history.sessions(vin),
            year=month.year,
            month=month.month,
            localize=dt_util.as_local,
        ),
        hybrid=coordinator.is_plug_in_hybrid(vin),
    )
    profile["cost_per_100km"] = month_summary.get("cost_per_100km")
    profile["currency"] = coordinator.pricing.currency if coordinator.pricing.enabled else None
    profile["energy_mix"] = month_summary.get("energy_mix")
    # BMW's own lifetime counters: on a plug-in hybrid the only honest
    # consumption figure there is (``history/obfcm.py``).
    profile["bmw_lifetime"] = coordinator.obfcm_lifetime(vin)
    return {"efficiency": profile, "vin": vin}


async def async_handle_get_evcc_config(ctx: ServiceContext, call: Any) -> dict[str, Any]:
    """The evcc ``custom`` vehicle block for this car, ready to paste.

    Generated rather than documented as a static example, for the same
    reason the Data Selection snippet is: it carries the user's own VIN,
    their own topic prefix and their own pack size, so there is nothing
    left to edit and get wrong. It also reports which topics are
    actually live, because a plugin pointed at a topic that never gets a
    message makes evcc log a read error on every update cycle.
    """

    _entry_id, target_entry, runtime, vin = ctx.target_car(call)
    coordinator = runtime.coordinator
    bridge = runtime.bridge
    config = (
        bridge.config
        if bridge is not None
        else BridgeConfig.from_options(dict(target_entry.options))
    )
    metadata = coordinator.device_metadata.get(vin, {})
    return service_answers.evcc_config(
        vin=vin,
        config=config,
        live=tuple(bridge_payloads(coordinator.bridge_snapshot(vin))),
        mqtt_available=bridge is not None and bridge.available(),
        title=metadata.get("name") or coordinator.names.get(vin),
        capacity_kwh=coordinator.battery_capacity_kwh(vin),
    )


COVERAGE_REPORT_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): str,
        vol.Optional("device_id"): str,
        vol.Optional("vin"): str,
    }
)


async def async_handle_get_coverage_report(ctx: ServiceContext, call: Any) -> dict[str, Any]:
    """Run the descriptor-coverage self-test and return it as data.

    Reads only the local coverage store and live stream state, so it
    spends no BMW API quota.
    """

    _entry_id, _entry, runtime, vin = ctx.target_car(call, required=False)
    if runtime.coverage is None:
        return {"reports": []}
    reports = coverage_reports(runtime, vin)
    return {"reports": [report.to_dict() for report in reports]}
