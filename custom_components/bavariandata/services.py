"""The integration's services, registered once for the whole Home Assistant run.

Every handler finds the account it acts on through :func:`_resolve_target`
(an explicit ``entry_id``, a picked ``device_id``, the device that owns a
``vin``, or the only loaded account), never through the setup that happened to
register it. They used to be closures inside ``async_setup_entry`` of whichever
account loaded first, which once filed a second account's car under the first.
A handler that acts on one car takes it from :func:`_target_car`, which refuses
a call that could mean either of two (``service_target.py`` holds the rules).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, time as dt_time, timedelta
from typing import Any, Optional

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import (
    async_dispatcher_send,
)
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    VEHICLE_METADATA,
    OPTION_STREAM_SECTIONS,
)
from .device_flow import CardataAuthError
from .api import (
    CardataApiError,
    async_get_basic_data,
    async_get_charging_history,
    async_get_location_based_charging_settings,
    async_get_tyre_diagnosis,
    async_get_vehicle_mappings,
)
from .rest import CardataQuotaError
from .descriptors import descriptors_for_sections
from .stream_activation import (
    DEFAULT_STREAM_ATTRIBUTES,
    PortalSession,
    PortalStreamClient,
    StreamActivationError,
)
from .service_target import (
    TargetError,
    choose_account,
    choose_vin,
    device_owner,
    requested_vin,
)
from .vehicles import known_vins, left_account_issue_id, left_account_vins, primary_vins
from .evcc import ALL_TOPICS, BridgeConfig, bridge_payloads, evcc_yaml
from .history.export import (
    MIME_CSV,
    MIME_HTML,
    month_report_html,
    sessions_csv,
    trips_csv,
)
from .history.pricing import MODE_FIXED, fixed_cost
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

from . import (
    CardataRuntimeData,
    _async_on_new_vehicle,
    _async_perform_telematic_fetch,
    _async_store_vehicle_metadata,
    _async_update_last_telematic_poll,
    _coverage_reports,
    account_vins,
)

_LOGGER = logging.getLogger(__name__)


def _cardata_timestamp(value: datetime) -> str:
    """Format a datetime as the ISO 8601 UTC string BMW's REST API expects."""

    return dt_util.as_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_cardata_timestamp(value: Any) -> Optional[str]:
    """Normalise a service-call date/time into BMW's ISO 8601 UTC format.

    The datetime selector delivers a naive local ``datetime`` (or its string
    form); a hand-typed value may already be a full ISO 8601 string. Empty means
    "not supplied" so the caller can fall back to a default window.
    """

    if value in (None, ""):
        return None
    parsed = value if isinstance(value, datetime) else dt_util.parse_datetime(value)
    if parsed is None:
        # Not a datetime we recognise -- pass the raw string through unchanged.
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return _cardata_timestamp(parsed)


def _as_limit(value: Any) -> Optional[int]:
    """Coerce a service-call ``limit`` to an int (the number selector yields a
    float, which would break list slicing). Empty/garbage means no limit."""

    if value in (None, ""):
        return None
    try:
        return int(value)
    except TypeError, ValueError:
        return None


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register every service. Called once, from ``async_setup``."""

    def _entry_id_for_vin(vin: Optional[str]) -> Optional[str]:
        """The config entry that owns ``vin``, via its device.

        The device registry is the authority here rather than the
        coordinator's live data: an entry that has not received anything
        from the stream yet still has its devices, so a call right after a
        restart resolves the same way as one an hour later.
        """

        if not vin:
            return None
        # Entry by entry: since HA 2026.10 the same VIN may be a device in two
        # accounts, and a lookup by identifier alone is deprecated. The first
        # account holding it wins, as the primary entry did before.
        registry = dr.async_get(hass)
        for entry in hass.config_entries.async_entries(DOMAIN):
            for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
                if (DOMAIN, vin) in device.identifiers:
                    return entry.entry_id
        return None

    def _device_target(device_id: Any) -> tuple[Optional[str], Optional[str]]:
        """``(entry_id, vin)`` of a picked device.

        The VIN is ``None`` for an account's debug device, whose identifier is
        its entry id: it names an account, not a car, which is all an
        account-wide service needs.
        """

        if not device_id:
            return None, None
        device = dr.async_get(hass).async_get(str(device_id))
        ours = [value for domain, value in device.identifiers if domain == DOMAIN] if device else []
        if not ours:
            raise TargetError(f"Device {device_id} is not a BavarianData car")
        entries = {entry.entry_id for entry in hass.config_entries.async_entries(DOMAIN)}
        vin = next((value for value in ours if value not in entries), None)
        return device_owner(device), vin

    def _resolve_target(call: Any) -> tuple[str, ConfigEntry, CardataRuntimeData, Optional[str]]:
        """The account a call acts on, and the car it names, if it names one.

        Raises :class:`ServiceValidationError` naming what to pass instead, so
        an automation that cannot be placed fails visibly rather than logging
        and reporting success.
        """

        loaded = {
            loaded_entry.entry_id: loaded_entry
            for loaded_entry in hass.config_entries.async_entries(DOMAIN)
            if getattr(loaded_entry, "runtime_data", None) is not None
        }
        try:
            device_entry, device_vin = _device_target(call.data.get("device_id"))
            vin = requested_vin(call.data.get("vin"), device_vin)
            target_entry_id = choose_account(
                {entry_id: entry.title for entry_id, entry in loaded.items()},
                entry_id=call.data.get("entry_id"),
                device_entry=device_entry,
                vin=vin,
                vin_entry=_entry_id_for_vin(vin),
            )
        except TargetError as err:
            raise ServiceValidationError(str(err)) from err
        target_entry = loaded[target_entry_id]
        return target_entry_id, target_entry, target_entry.runtime_data, vin

    def _account_vins(entry: ConfigEntry, runtime: CardataRuntimeData) -> list[str]:
        return account_vins(hass, entry, runtime.coordinator)

    def _pick_vin(
        entry: ConfigEntry,
        runtime: CardataRuntimeData,
        vin: Optional[str],
        *,
        required: bool,
    ) -> Optional[str]:
        try:
            return choose_vin(
                vin, _account_vins(entry, runtime), runtime.coordinator.names, required=required
            )
        except TargetError as err:
            raise ServiceValidationError(str(err)) from err

    def _target_car(
        call: Any, *, required: bool = True
    ) -> tuple[str, ConfigEntry, CardataRuntimeData, Optional[str]]:
        """:func:`_resolve_target`, with the car checked against the account.

        ``required`` services act on one car and refuse to guess between
        several; the others read every car when none is named.
        """

        entry_id, entry, runtime, vin = _resolve_target(call)
        return entry_id, entry, runtime, _pick_vin(entry, runtime, vin, required=required)

    async def async_handle_fetch(call) -> None:
        _entry_id, target_entry, runtime, vin = _target_car(call, required=False)
        success = await _async_perform_telematic_fetch(
            hass,
            target_entry,
            runtime,
            vin_override=vin,
            strict=True,
        )
        if success:
            _async_update_last_telematic_poll(hass, target_entry, time.time())

    async def async_handle_fetch_mappings(call) -> None:
        prepared = await _prepare_api_call(call, "fetch_vehicle_mappings", require_vin=False)
        target_entry, runtime, _vin, access_token = prepared
        try:
            payload = await async_get_vehicle_mappings(runtime.session, access_token)
        except CardataApiError as err:
            _LOGGER.error("Cardata fetch_vehicle_mappings failed: %s", err)
            raise HomeAssistantError(f"fetch_vehicle_mappings: {err}") from err
        count = len(payload) if isinstance(payload, (list, dict)) else 0
        _LOGGER.info("Fetched %s vehicle mapping(s)", count)
        _LOGGER.debug("Cardata vehicle mappings: %s", payload)
        # Discovery, not just a log line: a car the account lists but this
        # entry has never seen becomes a device. The adopt path spends one
        # request per new car, once; an explicit call retries a car whose
        # earlier attempt failed, so the guard is lifted first.
        known = set(
            known_vins(
                stored_metadata=target_entry.data.get(VEHICLE_METADATA),
                coordinator_data=runtime.coordinator.data,
            )
        )
        for vin in primary_vins(payload):
            if vin not in known:
                runtime.basic_data_attempted.discard(vin)
                _async_on_new_vehicle(hass, target_entry, vin)
        # ...and the other way round: a car the account no longer lists (sold,
        # or handed to another primary user) sends nothing more, and its
        # entities would sit frozen with nothing saying why.
        account_vins = _account_vins(target_entry, runtime)
        left = left_account_vins(account_vins, payload)
        if left is None:
            return
        for vin in account_vins:
            issue_id = left_account_issue_id(target_entry.entry_id, vin)
            if vin not in left:
                ir.async_delete_issue(hass, DOMAIN, issue_id)
                continue
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="vehicle_left_account",
                translation_placeholders={
                    # A car BMW never named is "named" by its VIN; mask that.
                    "vehicle": (
                        name
                        if (name := runtime.coordinator.names.get(vin)) and name != vin
                        else mask_vin(vin)
                    ),
                    "account": target_entry.title,
                },
            )

    async def async_handle_fetch_basic_data(call) -> None:
        prepared = await _prepare_api_call(call, "fetch_basic_data", require_vin=True)
        # ``target_entry``, never the ``entry`` this closure was created
        # with: the services are registered once, by whichever entry set up
        # first, so writing to that one files a second account's car under
        # the first account -- and fails outright once that first entry is
        # removed while the other stays loaded.
        target_entry, runtime, vin, access_token = prepared
        try:
            payload = await async_get_basic_data(runtime.session, access_token, vin)
        except CardataApiError as err:
            _LOGGER.error("Cardata fetch_basic_data failed for %s: %s", mask_vin(vin), err)
            raise HomeAssistantError(f"fetch_basic_data: {err}") from err
        _LOGGER.info("Fetched basic vehicle data for %s", mask_vin(vin))
        _LOGGER.debug("Cardata basic data for %s: %s", vin, payload)
        if isinstance(payload, dict):
            metadata = runtime.coordinator.apply_basic_data(vin, payload)
            if metadata:
                _async_store_vehicle_metadata(
                    hass,
                    target_entry,
                    vin,
                    metadata.get("raw_data") or payload,
                )
                device_registry = dr.async_get(hass)
                device_registry.async_get_or_create(
                    config_entry_id=target_entry.entry_id,
                    identifiers={(DOMAIN, vin)},
                    manufacturer=metadata.get("manufacturer", "BMW"),
                    name=metadata.get("name", vin),
                    model=metadata.get("model"),
                    sw_version=metadata.get("sw_version"),
                    hw_version=metadata.get("hw_version"),
                    serial_number=metadata.get("serial_number"),
                )

    async def _prepare_api_call(
        call, label: str, *, require_vin: bool
    ) -> tuple[ConfigEntry, "CardataRuntimeData", Optional[str], str]:
        """Resolve target, take a token, claim quota and return call context.

        Shared by the read-only CarData API services. Returns
        ``(entry, runtime, vin, access_token)``; a call that cannot be placed,
        has no token or no quota left raises instead of reporting success.
        """

        target_entry_id, target_entry, runtime, vin = _target_car(call, required=require_vin)

        try:
            access_token = await runtime.tokens.access_token()
        except CardataAuthError as err:
            _LOGGER.error(
                "Cardata %s: no valid token for entry %s: %s",
                label,
                target_entry_id,
                err,
            )
            raise HomeAssistantError(f"{label}: no valid BMW token; reauthorize") from err
        try:
            await runtime.rest.claim()
        except CardataQuotaError as err:
            _LOGGER.warning("Cardata %s blocked: %s", label, err)
            raise HomeAssistantError(f"{label}: {err}") from err

        return target_entry, runtime, vin, access_token

    async def async_handle_fetch_charging_history(call) -> None:
        prepared = await _prepare_api_call(call, "fetch_charging_history", require_vin=True)
        _entry, runtime, vin, access_token = prepared
        # BMW requires both from/to; default to the last 30 days so a bare
        # call from the UI succeeds instead of returning HTTP 400.
        to_date = _as_cardata_timestamp(call.data.get("to")) or _cardata_timestamp(dt_util.utcnow())
        from_date = _as_cardata_timestamp(call.data.get("from")) or _cardata_timestamp(
            dt_util.utcnow() - timedelta(days=30)
        )
        try:
            payload = await async_get_charging_history(
                runtime.session,
                access_token,
                vin,
                from_date=from_date,
                to_date=to_date,
            )
        except CardataApiError as err:
            _LOGGER.error("Cardata fetch_charging_history failed for %s: %s", mask_vin(vin), err)
            raise HomeAssistantError(f"fetch_charging_history: {err}") from err
        sessions = payload.get("data") if isinstance(payload, dict) else None
        # Import BMW's server-side history into our own store so it shows on
        # the card, enriching any live-recorded session in place. Cost is
        # backfilled only for a fixed tariff -- a dynamic tariff's historical
        # prices can't be reconstructed after the fact.
        pricing = runtime.coordinator.pricing

        def _cost_fn(grid_kwh):
            if grid_kwh and pricing.mode == MODE_FIXED:
                return fixed_cost(grid_kwh, pricing.fixed_price, pricing.currency)
            return None

        imported = updated = 0
        if runtime.history is not None and isinstance(sessions, list):
            imported, updated = runtime.history.import_cardata_sessions(
                vin,
                sessions,
                cost_fn=_cost_fn,
                zone_fn=runtime.coordinator.zone_at,
                home_zone=runtime.coordinator.home_zone_name(),
            )
            if imported or updated:
                async_dispatcher_send(hass, runtime.coordinator.signal_history, vin)
        _LOGGER.info(
            "Fetched charging history for %s (%s session(s); imported %s, updated %s)",
            mask_vin(vin),
            len(sessions) if isinstance(sessions, list) else 0,
            imported,
            updated,
        )
        _LOGGER.debug("Cardata charging history for %s: %s", vin, payload)

    async def async_handle_fetch_tyre_diagnosis(call) -> None:
        prepared = await _prepare_api_call(call, "fetch_tyre_diagnosis", require_vin=True)
        _entry, runtime, vin, access_token = prepared
        try:
            payload = await async_get_tyre_diagnosis(runtime.session, access_token, vin)
        except CardataApiError as err:
            _LOGGER.error("Cardata fetch_tyre_diagnosis failed for %s: %s", mask_vin(vin), err)
            raise HomeAssistantError(f"fetch_tyre_diagnosis: {err}") from err
        parsed = runtime.coordinator.apply_tyre_diagnosis(vin, payload)
        _LOGGER.info(
            "Fetched tyre diagnosis for %s (%s wheel(s) reported)",
            mask_vin(vin),
            len(parsed.get("wheels") or {}),
        )
        _LOGGER.debug("Cardata tyre diagnosis for %s: %s", vin, payload)

    async def async_handle_fetch_location_charging(call) -> None:
        prepared = await _prepare_api_call(
            call, "fetch_location_charging_settings", require_vin=True
        )
        _entry, runtime, vin, access_token = prepared
        try:
            payload = await async_get_location_based_charging_settings(
                runtime.session, access_token, vin
            )
        except CardataApiError as err:
            _LOGGER.error(
                "Cardata fetch_location_charging_settings failed for %s: %s", mask_vin(vin), err
            )
            raise HomeAssistantError(f"fetch_location_charging_settings: {err}") from err
        settings = payload.get("data") if isinstance(payload, dict) else None
        _LOGGER.info(
            "Fetched location-based charging settings for %s (%s entr(y/ies))",
            mask_vin(vin),
            len(settings) if isinstance(settings, list) else 0,
        )
        _LOGGER.debug("Cardata location-based charging settings for %s: %s", vin, payload)

    async def async_handle_fetch_vehicle_image(call) -> None:
        # Resolve the target entry/vin (this also refreshes tokens and claims a
        # quota slot); the shared image helper performs the actual fetch and
        # updates the ``image`` entity's cache + Store so the render shows up in
        # the Lovelace card. Token refresh / quota are handled inside the helper
        # as well, so we resolve the target here without claiming twice.
        # The VIN goes into the request path and keys the stored picture, so
        # only a car this account knows (``_target_car`` refuses anything
        # else): a stranger spends a request and leaves a junk entry in the
        # image Store for good.
        _target_id, target_entry, runtime, vin = _target_car(call)
        from .image import async_refresh_vehicle_image

        await async_refresh_vehicle_image(hass, target_entry, runtime, vin, strict=True)

    telematic_service_schema = vol.Schema(
        {
            vol.Optional("entry_id"): str,
            vol.Optional("device_id"): str,
            vol.Optional("vin"): str,
        }
    )
    mapping_service_schema = vol.Schema(
        {vol.Optional("entry_id"): str, vol.Optional("device_id"): str}
    )
    basic_data_service_schema = vol.Schema(
        {
            vol.Optional("entry_id"): str,
            vol.Optional("device_id"): str,
            vol.Optional("vin"): str,
        }
    )
    vin_service_schema = vol.Schema(
        {
            vol.Optional("entry_id"): str,
            vol.Optional("device_id"): str,
            vol.Optional("vin"): str,
        }
    )
    charging_history_schema = vol.Schema(
        {
            vol.Optional("entry_id"): str,
            vol.Optional("device_id"): str,
            vol.Optional("vin"): str,
            vol.Optional("from"): str,
            vol.Optional("to"): str,
        }
    )
    get_sessions_schema = vol.Schema(
        {
            vol.Optional("entry_id"): str,
            vol.Optional("device_id"): str,
            vol.Optional("vin"): str,
            vol.Optional("from"): str,
            vol.Optional("to"): str,
            vol.Optional("limit"): vol.All(int, vol.Range(min=1)),
        }
    )

    def _service_bound(call: Any, key: str, *, end_of_day: bool = False) -> Optional[datetime]:
        """A ``from``/``to`` argument as an instant, or ``None`` if absent.

        A bare date names a whole day, so which end of it we mean depends on
        which bound it is: ``from: 2026-08-01`` starts at midnight, while
        ``to: 2026-08-31`` has to run to the end of the 31st or a request for
        August silently drops everything that happened on its last day.
        An explicit date/time is always taken at its word.
        """

        raw = call.data.get(key)
        if not raw:
            return None
        parsed = dt_util.parse_datetime(raw) or dt_util.parse_date(raw)
        if parsed is None:
            raise ServiceValidationError(f"Could not read '{key}' as a date or date/time: {raw!r}")
        if isinstance(parsed, datetime):
            return dt_util.as_utc(parsed)
        return dt_util.as_utc(datetime.combine(parsed, dt_time.max if end_of_day else dt_time.min))

    async def async_handle_get_charging_sessions(call: Any) -> dict:
        _entry_id, _entry, runtime, vin = _target_car(call, required=False)
        if runtime.history is None:
            return {"sessions": []}
        sessions = runtime.history.sessions(
            vin,
            start=_service_bound(call, "from"),
            end=_service_bound(call, "to", end_of_day=True),
            limit=_as_limit(call.data.get("limit")),
        )
        return {"sessions": [session.to_dict() for session in sessions]}

    def _month_bounds(raw: Optional[str]) -> tuple[int, int]:
        """Parse a 'YYYY-MM' argument, defaulting to the current local month.

        Local, not UTC: a month is what the user's calendar says it is.
        """

        if not raw:
            now_local = dt_util.now()
            return now_local.year, now_local.month
        try:
            year, month = (int(part) for part in str(raw).split("-", 1))
        except (ValueError, TypeError) as err:
            raise ServiceValidationError(f"'month' must be 'YYYY-MM', got {raw!r}") from err
        if not 1 <= month <= 12:
            raise ServiceValidationError(f"'month' has no month {month}: {raw!r}")
        return year, month

    get_trips_schema = get_sessions_schema
    get_driving_summary_schema = vol.Schema(
        {
            vol.Optional("entry_id"): str,
            vol.Optional("device_id"): str,
            vol.Optional("vin"): str,
            vol.Optional("month"): str,  # "YYYY-MM"; defaults to this month
        }
    )
    get_efficiency_schema = vol.Schema(
        {
            vol.Optional("entry_id"): str,
            vol.Optional("device_id"): str,
            vol.Optional("vin"): str,
        }
    )
    get_evcc_config_schema = get_efficiency_schema
    get_mileage_report_schema = get_efficiency_schema
    get_fuel_history_schema = get_efficiency_schema
    mark_trip_start_schema = vol.Schema(
        {
            vol.Optional("entry_id"): str,
            vol.Optional("device_id"): str,
            vol.Optional("vin"): str,
            vol.Optional("at"): str,
        }
    )
    set_trip_class_schema = vol.Schema(
        {
            vol.Optional("entry_id"): str,
            vol.Optional("device_id"): str,
            vol.Optional("vin"): str,
            vol.Required("trip_id"): str,
            vol.Required("classification"): vol.In(list(CLASSIFICATIONS)),
        }
    )

    async def async_handle_get_trips(call: Any) -> dict:
        _entry_id, _entry, runtime, vin_filter = _target_car(call, required=False)
        if runtime.history is None:
            return {"trips": [], "open_trips": []}
        start = _service_bound(call, "from")
        end = _service_bound(call, "to", end_of_day=True)
        trips = runtime.history.trips(
            vin_filter,
            start=start,
            end=end,
            limit=_as_limit(call.data.get("limit")),
        )
        # A drive still under way is not in the store -- it only exists in the
        # coordinator's memory -- so it rides alongside the recorded trips
        # rather than inside them: it has no end, no distance we'd stand
        # behind yet and nothing to classify. It belongs to whatever window
        # contains *now*: asking for March plainly doesn't mean the drive
        # happening today, but asking for this month just as plainly does --
        # which is what the card's month view asks for every time.
        now = dt_util.utcnow()
        in_window = (start is None or start <= now) and (end is None or now <= end)
        open_trips: list[dict] = []
        if in_window:
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

    async def async_handle_get_mileage_report(call: Any) -> dict:
        """How far the car is driven and where it stands against its allowance.

        Read off the daily odometer log (``mileage.py``); no quota.
        """

        _entry_id, _entry, runtime, vin = _target_car(call)
        return {"vin": vin, **runtime.coordinator.mileage_report(vin)}

    async def async_handle_get_fuel_history(call: Any) -> dict:
        """Refuels and fuel consumption, from the tank reading (``history/fuel.py``).

        Plus BMW's lifetime counters where the car sends them. No quota.
        """

        _entry_id, _entry, runtime, vin = _target_car(call)
        return {"vin": vin, "fuel": runtime.coordinator.fuel_report(vin)}

    async def async_handle_get_driving_summary(call: Any) -> dict:
        _entry_id, _entry, runtime, vin = _target_car(call)
        if runtime.history is None:
            return {"summary": {}, "month": None}
        coordinator = runtime.coordinator
        history = runtime.history

        year, month = _month_bounds(call.data.get("month"))
        prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)

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

    async def async_handle_get_efficiency(call: Any) -> dict:
        """Measured consumption, real range and the seasonal trend.

        The same profile the real-range entity publishes, plus the month
        trend it deliberately keeps out of the recorder -- one code path, so
        the card and the entity can never quote different numbers.
        """

        _entry_id, _entry, runtime, vin = _target_car(call)
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

    async def async_handle_get_evcc_config(call: Any) -> dict:
        """The evcc ``custom`` vehicle block for this car, ready to paste.

        Generated rather than documented as a static example, for the same
        reason the Data Selection snippet is: it carries the user's own VIN,
        their own topic prefix and their own pack size, so there is nothing
        left to edit and get wrong. It also reports which topics are
        actually live, because a plugin pointed at a topic that never gets a
        message makes evcc log a read error on every update cycle.
        """

        _entry_id, target_entry, runtime, vin = _target_car(call)
        coordinator = runtime.coordinator
        bridge = runtime.bridge
        config = (
            bridge.config
            if bridge is not None
            else BridgeConfig.from_options(dict(target_entry.options))
        )
        live = tuple(bridge_payloads(coordinator.bridge_snapshot(vin)))
        metadata = coordinator.device_metadata.get(vin, {})
        return {
            "vin": vin,
            "enabled": config.publishes(vin),
            # Stated plainly: the snippet is useless without a broker, and
            # this is the one thing a user cannot tell from the YAML itself.
            "mqtt_available": bridge is not None and bridge.available(),
            "topic_prefix": config.prefix,
            "topics": [f"{config.prefix}/{vin}/{suffix}" for suffix in ALL_TOPICS],
            "published_topics": list(live),
            "yaml": evcc_yaml(
                prefix=config.prefix,
                vin=vin,
                title=metadata.get("name") or coordinator.names.get(vin),
                capacity_kwh=coordinator.battery_capacity_kwh(vin),
                topics=live or None,
            ),
        }

    async def async_handle_set_trip_class(call: Any) -> None:
        _entry_id, entry, runtime, vin = _resolve_target(call)
        if runtime.history is None:
            raise ServiceValidationError("Trip history is not available")
        coordinator = runtime.coordinator
        trip_id = call.data["trip_id"]
        if vin is None:
            # A trip id starts with its car's VIN, so it already says which.
            vin = next(
                (
                    known
                    for known in _account_vins(entry, runtime)
                    if trip_id.startswith(f"{known}-")
                ),
                None,
            )
        vin = _pick_vin(entry, runtime, vin, required=True)
        trip = runtime.history.find_trip(vin, trip_id)
        if trip is None:
            # A trip id starts with its VIN; neither goes into the message whole.
            shown = trip_id.replace(vin, mask_vin(vin))
            raise ServiceValidationError(f"No trip {shown!r} found for {mask_vin(vin)}")
        trip.classification = call.data["classification"]
        trip.classification_source = SOURCE_USER
        runtime.history.add_trip(trip)
        async_dispatcher_send(hass, coordinator.signal_trips, vin)

    async def async_handle_mark_trip_start(call: Any) -> None:
        """Note when a drive began, for a car that reports only once parked.

        Local only -- no quota. Refused outside the ``reports`` trip mode
        rather than stored for nothing: the live detector never reads it.
        """

        _entry_id, _entry, runtime, vin = _target_car(call)
        if runtime.history is None:
            raise ServiceValidationError("Trip history is not available")
        coordinator = runtime.coordinator
        if not coordinator.report_trips(vin):
            raise ServiceValidationError(
                "mark_trip_start is used only when trips are detected from the "
                "car's reports (Configure -> Trips -> Trip detection)"
            )
        at = _service_bound(call, "at") or dt_util.utcnow()
        coordinator.mark_trip_start(vin, at)

    # --- Phase 4: statistics backfill and export ------------------------

    export_schema = vol.Schema(
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
    import_statistics_schema = vol.Schema(
        {
            vol.Optional("entry_id"): str,
            vol.Optional("device_id"): str,
            vol.Optional("vin"): str,
        }
    )
    coverage_report_schema = vol.Schema(
        {
            vol.Optional("entry_id"): str,
            vol.Optional("device_id"): str,
            vol.Optional("vin"): str,
        }
    )

    async def async_handle_get_coverage_report(call: Any) -> dict:
        """Run the descriptor-coverage self-test and return it as data.

        Reads only the local coverage store and live stream state, so it
        spends no BMW API quota.
        """

        _entry_id, _entry, runtime, vin = _target_car(call, required=False)
        if runtime.coverage is None:
            return {"reports": []}
        reports = _coverage_reports(runtime, vin)
        return {"reports": [report.to_dict() for report in reports]}

    async def async_handle_export_history(call: Any) -> dict:
        """Render the recorded month as files the user keeps.

        Returns the file *contents* rather than writing to disk: the card
        turns them into a browser download, which needs no path
        configuration, no allowlisted directory, and works from a phone.
        """

        _entry_id, _entry, runtime, vin = _target_car(call)
        if runtime.history is None:
            return {"files": [], "month": None}
        history = runtime.history
        year, month = _month_bounds(call.data.get("month"))
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

        # A file name the user can find again after ten monthly exports.
        stem = f"bavariandata-{(vin or 'vehicle').lower()}-{label}"
        files: list[dict[str, Any]] = []

        if fmt == "html":
            # Same aggregations the card renders, so a printed report and
            # the dashboard can never disagree about a month's totals.
            hybrid = bool(vin) and runtime.coordinator.is_plug_in_hybrid(vin)
            ledger = summarise(sessions, hybrid=hybrid)
            vehicle = (
                runtime.coordinator.device_metadata.get(vin or "", {}).get("name") or vin or "BMW"
            )
            files.append(
                {
                    "filename": f"{stem}.html",
                    "mime": MIME_HTML,
                    "rows": len(sessions) + len(trips),
                    "content": month_report_html(
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
                    ),
                }
            )
        else:
            if wanted in ("charging", "both"):
                files.append(
                    {
                        "filename": f"{stem}-charging.csv",
                        "mime": MIME_CSV,
                        "rows": len(sessions),
                        "content": sessions_csv(sessions, localize=dt_util.as_local),
                    }
                )
            if wanted in ("trips", "both"):
                files.append(
                    {
                        "filename": f"{stem}-trips.csv",
                        "mime": MIME_CSV,
                        "rows": len(trips),
                        "content": trips_csv(trips, localize=dt_util.as_local),
                    }
                )

        return {"month": label, "vin": vin, "files": files}

    async def async_handle_import_statistics(call: Any) -> dict:
        """Force a statistics rebuild, bypassing the change fingerprint."""

        _entry_id, _entry, runtime, vin = _target_car(call, required=False)
        if runtime.statistics is None:
            return {"statistics": {}}
        publisher = runtime.statistics
        if not publisher.enabled:
            raise ServiceValidationError(
                "Statistics import is turned off for this vehicle "
                "(Configure -> Charging costs & history)."
            )
        if not publisher.available:
            raise ServiceValidationError(
                "Home Assistant's recorder is not set up, so there are no "
                "long-term statistics to import into."
            )
        written = await publisher.async_publish(vin=vin, force=True)
        _LOGGER.info("Imported BavarianData statistics: %s", written)
        return {"statistics": written}

    activate_stream_fields_schema = vol.Schema(
        {
            vol.Optional("entry_id"): str,
            vol.Optional("device_id"): str,
            vol.Required("base_url"): str,
            vol.Required("locale"): str,
            vol.Required("mapped_vehicle_id"): str,
            vol.Required("cookie"): str,
            vol.Optional("attributes"): [str],
            vol.Optional("sections"): [str],
        }
    )

    async def async_handle_activate_stream_fields(call: Any) -> dict:
        """Replace a vehicle's streamed attribute selection via the BMW portal.

        Stream-attribute selection has no CarData API; it lives behind the
        market portal, which authenticates with an interactive browser
        session. The caller therefore supplies a captured portal session
        (``base_url``/``locale``/``cookie``) and the portal ``mapped_vehicle_id``
        (a portal hash, not the VIN -- take it from the stream-setup URL).

        Desired attributes come from, in order: an explicit ``attributes``
        list, the clusters named in ``sections``, this entry's stored
        "Choose streamed data" selection, or the default cluster set.
        """

        # Resolve the entry only for its shared HTTP session; the portal call
        # spends no CarData API quota and needs no token.
        _entry_id, entry, runtime, _vin = _resolve_target(call)

        if call.data.get("attributes"):
            attributes = list(call.data["attributes"])
        elif call.data.get("sections"):
            attributes = descriptors_for_sections(call.data["sections"])
        elif entry.data.get(OPTION_STREAM_SECTIONS):
            attributes = descriptors_for_sections(entry.data[OPTION_STREAM_SECTIONS])
        else:
            attributes = list(DEFAULT_STREAM_ATTRIBUTES)

        if not attributes:
            raise ServiceValidationError(
                "No attributes to activate; pass 'attributes' or 'sections', "
                "or choose streamed-data clusters in the options."
            )

        session = PortalSession(
            base_url=call.data["base_url"],
            locale=call.data["locale"],
            cookie=call.data["cookie"],
        )
        client = PortalStreamClient(runtime.session, session)
        mapped_vehicle_id = call.data["mapped_vehicle_id"]
        try:
            result = await client.async_set_stream_attributes(mapped_vehicle_id, attributes)
        except StreamActivationError as err:
            # Turn the classified failure into a user-facing message. The
            # commonest is an expired session -- portal cookies (incl. the
            # bot-defense ones) are short-lived and cannot be refreshed here.
            if err.kind == "auth":
                raise ServiceValidationError(
                    "BMW rejected the portal session (expired or invalid). "
                    "Capture a fresh session from the stream-setup page and "
                    "retry."
                ) from err
            if err.kind == "not_found":
                raise ServiceValidationError(
                    "BMW returned 404 -- check the mapped_vehicle_id and locale."
                ) from err
            raise ServiceValidationError(str(err)) from err

        return {
            "mapped_vehicle_id": mapped_vehicle_id,
            "requested": result.requested_count,
            "accepted": result.accepted_count,
            "unchanged": result.unchanged,
        }

    hass.services.async_register(
        DOMAIN,
        "fetch_telematic_data",
        async_handle_fetch,
        schema=telematic_service_schema,
    )
    hass.services.async_register(
        DOMAIN,
        "fetch_vehicle_mappings",
        async_handle_fetch_mappings,
        schema=mapping_service_schema,
    )
    hass.services.async_register(
        DOMAIN,
        "fetch_basic_data",
        async_handle_fetch_basic_data,
        schema=basic_data_service_schema,
    )
    hass.services.async_register(
        DOMAIN,
        "fetch_charging_history",
        async_handle_fetch_charging_history,
        schema=charging_history_schema,
    )
    hass.services.async_register(
        DOMAIN,
        "fetch_tyre_diagnosis",
        async_handle_fetch_tyre_diagnosis,
        schema=vin_service_schema,
    )
    hass.services.async_register(
        DOMAIN,
        "fetch_location_charging_settings",
        async_handle_fetch_location_charging,
        schema=vin_service_schema,
    )
    hass.services.async_register(
        DOMAIN,
        "fetch_vehicle_image",
        async_handle_fetch_vehicle_image,
        schema=vin_service_schema,
    )
    # Returns data instead of creating entities: a session list would need
    # dozens of entities to express, and none of them is a question anyone
    # asks of their dashboard. Reads the local store, so it spends no quota.
    hass.services.async_register(
        DOMAIN,
        "get_charging_sessions",
        async_handle_get_charging_sessions,
        schema=get_sessions_schema,
        supports_response=SupportsResponse.ONLY,
    )
    # Trips (roadmap Phase 3). Reads/writes the local store only -- no quota.
    hass.services.async_register(
        DOMAIN,
        "get_trips",
        async_handle_get_trips,
        schema=get_trips_schema,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "get_driving_summary",
        async_handle_get_driving_summary,
        schema=get_driving_summary_schema,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "get_mileage_report",
        async_handle_get_mileage_report,
        schema=get_mileage_report_schema,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "get_fuel_history",
        async_handle_get_fuel_history,
        schema=get_fuel_history_schema,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "get_efficiency",
        async_handle_get_efficiency,
        schema=get_efficiency_schema,
        supports_response=SupportsResponse.ONLY,
    )
    # The evcc bridge's paste-ready config. Reads local state only -- no
    # quota, no broker round trip.
    hass.services.async_register(
        DOMAIN,
        "get_evcc_config",
        async_handle_get_evcc_config,
        schema=get_evcc_config_schema,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "set_trip_class",
        async_handle_set_trip_class,
        schema=set_trip_class_schema,
    )
    hass.services.async_register(
        DOMAIN,
        "mark_trip_start",
        async_handle_mark_trip_start,
        schema=mark_trip_start_schema,
    )
    # Export (roadmap Phase 4). Returns file contents; the card downloads
    # them. Local store only -- no quota.
    hass.services.async_register(
        DOMAIN,
        "export_history",
        async_handle_export_history,
        schema=export_schema,
        supports_response=SupportsResponse.ONLY,
    )
    # Statistics backfill happens automatically as records land; this is the
    # manual rebuild, and it reports the row counts so it can be verified.
    hass.services.async_register(
        DOMAIN,
        "import_statistics",
        async_handle_import_statistics,
        schema=import_statistics_schema,
        supports_response=SupportsResponse.OPTIONAL,
    )
    # Descriptor-coverage self-test. Reads the local store + live stream
    # state only -- no quota.
    hass.services.async_register(
        DOMAIN,
        "get_coverage_report",
        async_handle_get_coverage_report,
        schema=coverage_report_schema,
        supports_response=SupportsResponse.ONLY,
    )
    # Portal stream-attribute activation. Talks to the market portal with a
    # supplied browser session, not the CarData API -- spends no quota, needs
    # no token. Returns the requested/accepted counts.
    hass.services.async_register(
        DOMAIN,
        "activate_stream_fields",
        async_handle_activate_stream_fields,
        schema=activate_stream_fields_schema,
        supports_response=SupportsResponse.OPTIONAL,
    )
