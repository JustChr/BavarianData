"""The integration's services, registered once for the whole Home Assistant run.

Every handler finds the account it acts on through
:meth:`ServiceContext.resolve_target` (an explicit ``entry_id``, a picked
``device_id``, the device that owns a ``vin``, or the only loaded account),
never through the setup that happened to register it. They used to be closures
inside ``async_setup_entry`` of whichever account loaded first, which once filed
a second account's car under the first. A handler that acts on one car takes it
from :meth:`ServiceContext.target_car`, which refuses a call that could mean
either of two (``service_target.py`` holds the rules).

The handlers live by what they do: ``service_fetch.py`` asks BMW (quota),
``service_read.py`` answers from what is stored, ``service_history.py`` changes
or exports the history. This module only registers them.
"""

from __future__ import annotations

from functools import partial

from homeassistant.core import HomeAssistant, SupportsResponse

from . import service_fetch, service_history, service_read
from .const import DOMAIN
from .service_context import ServiceContext


def async_setup_services(hass: HomeAssistant) -> None:
    """Register every service. Called once, from ``async_setup``."""

    ctx = ServiceContext(hass)
    hass.services.async_register(
        DOMAIN,
        "fetch_telematic_data",
        partial(service_fetch.async_handle_fetch, ctx),
        schema=service_fetch.TELEMATIC_SERVICE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "fetch_vehicle_mappings",
        partial(service_fetch.async_handle_fetch_mappings, ctx),
        schema=service_fetch.MAPPING_SERVICE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "fetch_basic_data",
        partial(service_fetch.async_handle_fetch_basic_data, ctx),
        schema=service_fetch.BASIC_DATA_SERVICE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "fetch_charging_history",
        partial(service_fetch.async_handle_fetch_charging_history, ctx),
        schema=service_fetch.CHARGING_HISTORY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "fetch_tyre_diagnosis",
        partial(service_fetch.async_handle_fetch_tyre_diagnosis, ctx),
        schema=service_fetch.VIN_SERVICE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "fetch_location_charging_settings",
        partial(service_fetch.async_handle_fetch_location_charging, ctx),
        schema=service_fetch.VIN_SERVICE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "fetch_vehicle_image",
        partial(service_fetch.async_handle_fetch_vehicle_image, ctx),
        schema=service_fetch.VIN_SERVICE_SCHEMA,
    )
    # Returns data instead of creating entities: a session list would need
    # dozens of entities to express, and none of them is a question anyone
    # asks of their dashboard. Reads the local store, so it spends no quota.
    hass.services.async_register(
        DOMAIN,
        "get_charging_sessions",
        partial(service_read.async_handle_get_charging_sessions, ctx),
        schema=service_read.GET_SESSIONS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "get_charging_places",
        partial(service_read.async_handle_get_charging_places, ctx),
        schema=service_read.GET_PLACES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    # Trips (roadmap Phase 3). Reads/writes the local store only -- no quota.
    hass.services.async_register(
        DOMAIN,
        "get_trips",
        partial(service_read.async_handle_get_trips, ctx),
        schema=service_read.GET_TRIPS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "get_driving_summary",
        partial(service_read.async_handle_get_driving_summary, ctx),
        schema=service_read.GET_DRIVING_SUMMARY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "get_mileage_report",
        partial(service_read.async_handle_get_mileage_report, ctx),
        schema=service_read.GET_MILEAGE_REPORT_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "get_fuel_history",
        partial(service_read.async_handle_get_fuel_history, ctx),
        schema=service_read.GET_FUEL_HISTORY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "get_efficiency",
        partial(service_read.async_handle_get_efficiency, ctx),
        schema=service_read.GET_EFFICIENCY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    # The evcc bridge's paste-ready config. Reads local state only -- no
    # quota, no broker round trip.
    hass.services.async_register(
        DOMAIN,
        "get_evcc_config",
        partial(service_read.async_handle_get_evcc_config, ctx),
        schema=service_read.GET_EVCC_CONFIG_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "set_trip_class",
        partial(service_history.async_handle_set_trip_class, ctx),
        schema=service_history.SET_TRIP_CLASS_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "mark_trip_start",
        partial(service_history.async_handle_mark_trip_start, ctx),
        schema=service_history.MARK_TRIP_START_SCHEMA,
    )
    # Export (roadmap Phase 4). Returns file contents; the card downloads
    # them. Local store only -- no quota.
    hass.services.async_register(
        DOMAIN,
        "export_history",
        partial(service_history.async_handle_export_history, ctx),
        schema=service_history.EXPORT_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    # Statistics backfill happens automatically as records land; this is the
    # manual rebuild, and it reports the row counts so it can be verified.
    hass.services.async_register(
        DOMAIN,
        "import_statistics",
        partial(service_history.async_handle_import_statistics, ctx),
        schema=service_history.IMPORT_STATISTICS_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    # Descriptor-coverage self-test. Reads the local store + live stream
    # state only -- no quota.
    hass.services.async_register(
        DOMAIN,
        "get_coverage_report",
        partial(service_read.async_handle_get_coverage_report, ctx),
        schema=service_read.COVERAGE_REPORT_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    # Portal stream-attribute activation. Talks to the market portal with a
    # supplied browser session, not the CarData API -- spends no quota, needs
    # no token. Returns the requested/accepted counts.
    hass.services.async_register(
        DOMAIN,
        "activate_stream_fields",
        partial(service_fetch.async_handle_activate_stream_fields, ctx),
        schema=service_fetch.ACTIVATE_STREAM_FIELDS_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
