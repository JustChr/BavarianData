"""The services that change or export the history: trip class, trip start, export, statistics."""

from __future__ import annotations

import logging
from typing import Any
import voluptuous as vol
from homeassistant.helpers.dispatcher import (
    async_dispatcher_send,
)
from homeassistant.util import dt as dt_util
from . import service_answers
from .distance import car_distance_unit
from .refusal import Refusal
from .history.export import (
    month_report_html,
)
from .history.summary import (
    driving_summary,
    sessions_in_month,
    summarise,
    trips_in_month,
)
from .history.trips import CLASSIFICATIONS, SOURCE_USER
from .debug import (
    mask_vin,
)
from .service_context import (
    ServiceContext,
    invalid,
    month_bounds,
    service_bound,
)

_LOGGER = logging.getLogger(__name__)


MARK_TRIP_START_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): str,
        vol.Optional("device_id"): str,
        vol.Optional("vin"): str,
        vol.Optional("at"): str,
    }
)


SET_TRIP_CLASS_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): str,
        vol.Optional("device_id"): str,
        vol.Optional("vin"): str,
        vol.Required("trip_id"): str,
        vol.Required("classification"): vol.In(list(CLASSIFICATIONS)),
    }
)


async def async_handle_set_trip_class(ctx: ServiceContext, call: Any) -> None:
    hass = ctx.hass
    _entry_id, entry, runtime, vin = ctx.resolve_target(call)
    if runtime.history is None:
        raise invalid(Refusal("no_trip_history"))
    coordinator = runtime.coordinator
    trip_id = call.data["trip_id"]
    if vin is None:
        # A trip id starts with its car's VIN, so it already says which.
        vin = service_answers.trip_vin(trip_id, ctx.account_vins(entry, runtime))
    vin = ctx.pick_vin(entry, runtime, vin, required=True)
    trip = runtime.history.find_trip(vin, trip_id)
    if trip is None:
        # A trip id starts with its VIN; neither goes into the message whole.
        shown = trip_id.replace(vin, mask_vin(vin))
        raise invalid(Refusal("trip_not_found", trip=repr(shown), vin=mask_vin(vin)))
    trip.classification = call.data["classification"]
    trip.classification_source = SOURCE_USER
    runtime.history.add_trip(trip)
    async_dispatcher_send(hass, coordinator.signal_trips, vin)


async def async_handle_mark_trip_start(ctx: ServiceContext, call: Any) -> None:
    """Note when a drive began, for a car that reports only once parked.

    Local only -- no quota. Refused outside the ``reports`` trip mode
    rather than stored for nothing: the live detector never reads it.
    """

    _entry_id, _entry, runtime, vin = ctx.target_car(call)
    if runtime.history is None:
        raise invalid(Refusal("no_trip_history"))
    coordinator = runtime.coordinator
    if not coordinator.report_trips(vin):
        raise invalid(Refusal("trips_not_from_reports"))
    at = service_bound(call, "at") or dt_util.utcnow()
    coordinator.mark_trip_start(vin, at)


EXPORT_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): str,
        vol.Optional("device_id"): str,
        vol.Optional("vin"): str,
        vol.Optional("month"): str,  # "YYYY-MM"; defaults to this month
        vol.Optional("type", default="both"): vol.In(("charging", "trips", "both")),
        vol.Optional("format", default="csv"): vol.In(("csv", "html")),
        vol.Optional("language"): str,
    }
)


IMPORT_STATISTICS_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): str,
        vol.Optional("device_id"): str,
        vol.Optional("vin"): str,
    }
)


async def async_handle_export_history(ctx: ServiceContext, call: Any) -> dict[str, Any]:
    """Render the recorded month as files the user keeps.

    Returns the file *contents* rather than writing to disk: the card
    turns them into a browser download, which needs no path
    configuration, no allowlisted directory, and works from a phone.
    """

    hass = ctx.hass
    _entry_id, _entry, runtime, vin = ctx.target_car(call)
    if runtime.history is None:
        return {"files": [], "month": None}
    history = runtime.history
    year, month = month_bounds(call.data.get("month"))
    label = f"{year:04d}-{month:02d}"
    wanted = call.data.get("type", "both")
    fmt = call.data.get("format", "csv")
    lang = call.data.get("language") or hass.config.language or "en"

    sessions = (
        sessions_in_month(
            history.sessions(vin),
            year=year,
            month=month,
            localize=dt_util.as_local,
        )
        if wanted in ("charging", "both")
        else []
    )
    trips = (
        trips_in_month(history.trips(vin), year=year, month=month, localize=dt_util.as_local)
        if wanted in ("trips", "both")
        else []
    )

    stem = service_answers.export_stem(vin, label)
    files: list[dict[str, Any]] = []

    if fmt == "html":
        # Same aggregations the card renders, so a printed report and
        # the dashboard can never disagree about a month's totals.
        hybrid = bool(vin) and runtime.coordinator.is_plug_in_hybrid(vin)
        ledger = summarise(sessions, hybrid=hybrid)
        vehicle = runtime.coordinator.device_metadata.get(vin or "", {}).get("name") or vin or "BMW"
        files.append(
            service_answers.html_file(
                stem,
                len(sessions) + len(trips),
                month_report_html(
                    month=label,
                    vehicle=vehicle,
                    sessions=sessions,
                    trips=trips,
                    charging_summary=ledger,
                    driving=driving_summary(
                        trips,
                        cost_per_100km=ledger.get("cost_per_100km"),
                        currency=runtime.coordinator.pricing.currency,
                        sessions=sessions,
                        battery_capacity_kwh=(
                            runtime.coordinator.battery_capacity_kwh(vin) if vin else None
                        ),
                        hybrid=hybrid,
                    ),
                    lang=lang,
                    localize=dt_util.as_local,
                    now=dt_util.utcnow(),
                    distance_unit=car_distance_unit(hass, runtime.coordinator, vin),
                ),
            )
        )
    else:
        files = service_answers.csv_files(stem, sessions, trips, wanted, dt_util.as_local)

    return {"month": label, "vin": vin, "files": files}


async def async_handle_import_statistics(ctx: ServiceContext, call: Any) -> dict[str, Any]:
    """Force a statistics rebuild, bypassing the change fingerprint."""

    _entry_id, _entry, runtime, vin = ctx.target_car(call, required=False)
    if runtime.statistics is None:
        return {"statistics": {}}
    publisher = runtime.statistics
    if not publisher.enabled:
        raise invalid(Refusal("statistics_off"))
    if not publisher.available:
        raise invalid(Refusal("no_recorder"))
    written = await publisher.async_publish(vin=vin, force=True)
    _LOGGER.info("Imported BavarianData statistics: %s", written)
    return {"statistics": written}
