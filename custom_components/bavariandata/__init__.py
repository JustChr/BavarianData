"""BMW CarData integration for Home Assistant."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from datetime import datetime, time as dt_time, timedelta, timezone
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional

import aiohttp
import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.config_entries import ConfigEntry, SOURCE_REAUTH
from homeassistant.core import HomeAssistant, SupportsResponse, callback
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError

from homeassistant.components import persistent_notification
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_SCOPE,
    DEFAULT_STREAM_HOST,
    DEFAULT_STREAM_PORT,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
    MQTT_KEEPALIVE,
    DIAGNOSTIC_LOG_INTERVAL,
    HV_BATTERY_DESCRIPTORS,
    BOOTSTRAP_COMPLETE,
    REQUEST_LOG,
    REQUEST_LOG_VERSION,
    REQUEST_LIMIT,
    REQUEST_WINDOW_SECONDS,
    TELEMATIC_POLL_INTERVAL,
    VEHICLE_METADATA,
    OPTION_MQTT_KEEPALIVE,
    OPTION_DEBUG_LOG,
    OPTION_DIAGNOSTIC_INTERVAL,
    OPTION_HISTORY_RETAIN_MONTHS,
    DEFAULT_HISTORY_RETAIN_MONTHS,
    OPTION_TRIP_WORK_ZONE,
    OPTION_TRIP_GEOCODE,
    OPTION_STATISTICS_IMPORT,
    DEFAULT_STATISTICS_IMPORT,
    OPTION_STREAM_SECTIONS,
    DEBUG_LOG,
    LOVELACE_CARD_FILENAME,
    LOVELACE_CARD_URL,
)
from .device_flow import CardataAuthError, refresh_tokens
from .api import (
    CardataApiError,
    async_get_basic_data,
    async_get_charging_history,
    async_get_location_based_charging_settings,
    async_get_telematic_data,
    async_get_tyre_diagnosis,
    async_get_vehicle_mappings,
)
from .container import CardataContainerError, CardataContainerManager
from .coverage_store import CoverageStore
from .history.backfill import StatisticsPublisher
from .history.export import (
    MIME_CSV,
    MIME_HTML,
    month_report_html,
    sessions_csv,
    trips_csv,
)
from .history.geocoding import ReverseGeocoder
from .history.pricing import MODE_FIXED, PricingConfig, fixed_cost
from .history.store import HistoryStore
from .history.summary import (
    driving_summary,
    sessions_in_month,
    summarise,
    trips_in_month,
)
from .history.trips import CLASSIFICATIONS, SOURCE_USER
from .stream import CardataStreamManager
from .coordinator import CardataCoordinator
from .debug import set_debug_enabled

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.IMAGE,
]

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
    except (TypeError, ValueError):
        return None


def _retain_months(options: dict) -> Optional[int]:
    """Retention in months, where 0 (or nonsense) means keep forever."""

    try:
        months = int(options.get(OPTION_HISTORY_RETAIN_MONTHS, DEFAULT_HISTORY_RETAIN_MONTHS))
    except (TypeError, ValueError):
        return DEFAULT_HISTORY_RETAIN_MONTHS
    return months if months > 0 else None


def _coverage_reports(runtime: "CardataRuntimeData", vin: Optional[str] = None):
    """Build the coverage self-test reports, folding in live descriptor state.

    The persisted store outlives a disabled entity; the live coordinator state
    covers a wiped store or a descriptor seen before the store existed. Their
    union is what the report should judge against.
    """

    if runtime.coverage is None:
        return []
    live = {
        known_vin: set(descriptors)
        for known_vin, descriptors in runtime.coordinator.data.items()
    }
    reports = runtime.coverage.reports(live)
    if vin is not None:
        reports = [report for report in reports if report.vin == vin]
    return reports


def _coverage_issue_id(entry_id: str, vin: str) -> str:
    return f"stream_coverage_gaps_{entry_id}_{vin}"


@callback
def _refresh_coverage_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Create or clear a per-vehicle repair issue from the coverage self-test."""

    entry = hass.config_entries.async_get_entry(entry_id)
    runtime: Optional["CardataRuntimeData"] = (
        getattr(entry, "runtime_data", None) if entry else None
    )
    if runtime is None or runtime.coverage is None:
        return
    for report in _coverage_reports(runtime):
        issue_id = _coverage_issue_id(entry_id, report.vin)
        overdue_clusters = report.overdue_clusters()
        if not overdue_clusters:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
            continue
        vehicle = runtime.coordinator.names.get(report.vin, report.vin)
        cluster_labels = ", ".join(cluster.label for cluster in overdue_clusters)
        # A handful of concrete descriptors makes the issue actionable without
        # dumping the whole missing list into a notification body.
        examples = ", ".join(report.overdue[:5])
        if len(report.overdue) > 5:
            examples = f"{examples}, ..."
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="stream_coverage_gaps",
            translation_placeholders={
                "vehicle": vehicle,
                "count": str(len(report.overdue)),
                "days": str(report.grace_days),
                "clusters": cluster_labels,
                "descriptors": examples,
            },
        )


@dataclass
class CardataRuntimeData:
    stream: CardataStreamManager
    refresh_task: asyncio.Task
    session: aiohttp.ClientSession
    coordinator: CardataCoordinator
    container_manager: Optional[CardataContainerManager]
    history: Optional[HistoryStore] = None
    statistics: Optional[StatisticsPublisher] = None
    coverage: Optional[CoverageStore] = None
    bootstrap_task: asyncio.Task | None = None
    quota_manager: "QuotaManager" | None = None
    telematic_task: asyncio.Task | None = None
    reauth_in_progress: bool = False
    reauth_flow_id: str | None = None
    last_reauth_attempt: float = 0.0
    last_refresh_attempt: float = 0.0
    reauth_pending: bool = False


# Per-entry runtime lives on ``entry.runtime_data`` (HA 2026.3+), so a typed
# ConfigEntry alias gives callers ``entry.runtime_data`` with the right type and
# removes the old ``hass.data[DOMAIN][entry.entry_id]`` lookups. Process-wide
# state (registered services, the shared vehicle-image cache) still lives on
# ``hass.data[DOMAIN]``.
CardataConfigEntry = ConfigEntry[CardataRuntimeData]


class CardataQuotaError(Exception):
    """Raised when API quota would be exceeded."""


class QuotaManager:
    """Manage the rolling 24 hour request quota."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        store: Store,
        timestamps: Deque[float],
    ) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._store = store
        self._timestamps: Deque[float] = timestamps
        self._lock = asyncio.Lock()

    @classmethod
    async def async_create(cls, hass: HomeAssistant, entry_id: str) -> "QuotaManager":
        store = Store(hass, REQUEST_LOG_VERSION, f"{DOMAIN}_{entry_id}_{REQUEST_LOG}")
        data = await store.async_load() or {}
        raw_timestamps = data.get("timestamps", [])
        values: List[float] = []
        for item in raw_timestamps:
            value: Optional[float] = None
            if isinstance(item, (int, float)):
                value = float(item)
            elif isinstance(item, str):
                try:
                    value = float(item)
                except (TypeError, ValueError):
                    try:
                        value = datetime.fromisoformat(item.replace("Z", "+00:00")).timestamp()
                    except (TypeError, ValueError):
                        value = None
            if value is None:
                continue
            values.append(value)
        normalized: Deque[float] = deque(sorted(values))
        manager = cls(hass, entry_id, store, normalized)
        async with manager._lock:
            manager._prune(time.time())
            await manager._async_save_locked()
        return manager

    def _prune(self, now: float) -> None:
        cutoff = now - REQUEST_WINDOW_SECONDS
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()

    @property
    def _issue_id(self) -> str:
        return f"api_quota_exhausted_{self._entry_id}"

    async def async_claim(self) -> None:
        async with self._lock:
            now = time.time()
            self._prune(now)
            if len(self._timestamps) >= REQUEST_LIMIT:
                reset = self.next_reset_iso or "later today"
                ir.async_create_issue(
                    self._hass,
                    DOMAIN,
                    self._issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="api_quota_exhausted",
                    translation_placeholders={
                        "limit": str(REQUEST_LIMIT),
                        "next_reset": reset,
                    },
                    learn_more_url=(
                        "https://github.com/JustChr/BavarianData/wiki/"
                        "Troubleshooting-and-FAQ#quota-exhausted"
                    ),
                )
                raise CardataQuotaError(
                    "BMW CarData API limit reached; try again after quota resets"
                )
            self._timestamps.append(now)
            # A successful claim means we're back under the limit; clear any
            # exhaustion repair that a previous window raised.
            ir.async_delete_issue(self._hass, DOMAIN, self._issue_id)
            await self._async_save_locked()

    @property
    def used(self) -> int:
        self._prune(time.time())
        return len(self._timestamps)

    @property
    def remaining(self) -> int:
        return max(0, REQUEST_LIMIT - self.used)

    @property
    def next_reset_epoch(self) -> Optional[float]:
        self._prune(time.time())
        if len(self._timestamps) < REQUEST_LIMIT:
            return None
        return self._timestamps[0] + REQUEST_WINDOW_SECONDS

    @property
    def next_reset_iso(self) -> Optional[str]:
        ts = self.next_reset_epoch
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, timezone.utc).isoformat()

    async def async_close(self) -> None:
        async with self._lock:
            self._prune(time.time())
            await self._async_save_locked()

    async def _async_save_locked(self) -> None:
        await self._store.async_save({"timestamps": list(self._timestamps)})

_FRONTEND_REGISTERED = False


async def _async_register_frontend_card(hass: HomeAssistant) -> None:
    """Serve the bundled Lovelace card and register it as a frontend resource.

    Runs once per Home Assistant process (guarded by a module-level flag so it
    survives config-entry reloads) making the ``custom:bmw-cardata-card`` element
    available without the user manually adding a dashboard resource.
    """

    global _FRONTEND_REGISTERED
    if _FRONTEND_REGISTERED:
        return

    card_path = os.path.join(os.path.dirname(__file__), "www", LOVELACE_CARD_FILENAME)
    if not os.path.isfile(card_path):
        _LOGGER.warning("Cardata: bundled Lovelace card missing at %s", card_path)
        return

    from homeassistant.components.http import StaticPathConfig

    await hass.http.async_register_static_paths(
        [StaticPathConfig(LOVELACE_CARD_URL, card_path, cache_headers=False)]
    )

    try:
        from homeassistant.components.frontend import add_extra_js_url

        # Cache-bust on integration version so browsers pick up card updates.
        # Read the manifest off the event loop — open() is a blocking call.
        version = await hass.async_add_executor_job(_integration_version)
        add_extra_js_url(hass, f"{LOVELACE_CARD_URL}?v={version}")
    except ImportError:  # pragma: no cover - frontend always present in practice
        _LOGGER.debug("Cardata: frontend component unavailable; card not auto-loaded")

    _FRONTEND_REGISTERED = True
    _LOGGER.debug("Cardata: registered Lovelace card at %s", LOVELACE_CARD_URL)


def _integration_version() -> str:
    try:
        manifest_path = os.path.join(os.path.dirname(__file__), "manifest.json")
        with open(manifest_path, encoding="utf-8") as handle:
            return json.load(handle).get("version", "0")
    except (OSError, ValueError):
        return "0"


async def async_setup_entry(hass: HomeAssistant, entry: CardataConfigEntry) -> bool:
    domain_data = hass.data.setdefault(DOMAIN, {})

    _LOGGER.debug("Setting up BMW CarData entry %s", entry.entry_id)

    await _async_register_frontend_card(hass)

    session = aiohttp.ClientSession()

    data = entry.data
    options = dict(entry.options) if entry.options else {}
    mqtt_keepalive = options.get(OPTION_MQTT_KEEPALIVE, MQTT_KEEPALIVE)
    diagnostic_interval = options.get(OPTION_DIAGNOSTIC_INTERVAL, DIAGNOSTIC_LOG_INTERVAL)
    debug_option = options.get(OPTION_DEBUG_LOG)
    debug_flag = DEBUG_LOG if debug_option is None else bool(debug_option)

    set_debug_enabled(debug_flag)
    should_bootstrap = not data.get(BOOTSTRAP_COMPLETE)
    client_id = data["client_id"]
    gcid = data.get("gcid")
    id_token = data.get("id_token")
    if not gcid or not id_token:
        await session.close()
        raise ConfigEntryNotReady("Missing GCID or ID token")

    # History is a bonus, not a prerequisite: a failure to read it must never
    # stop the stream from setting up, so HistoryStore.async_load swallows its
    # own errors and starts empty.
    history_store = HistoryStore(
        hass,
        entry.entry_id,
        retain_months=_retain_months(options),
    )
    await history_store.async_load()

    # Descriptor-coverage self-test ("Beyond the roadmap"). Tracks which of the
    # selected stream clusters' descriptors have actually arrived, so a cluster
    # the car never produces -- or a portal selection that silently doesn't match
    # -- is surfaced instead of looking like a healthy-but-empty stream. The
    # selection lives on the entry (stream_sections); an entry from before the
    # picker existed has none, which yields an empty expectation and no alarms.
    coverage_store = CoverageStore(
        hass,
        entry.entry_id,
        sections=data.get(OPTION_STREAM_SECTIONS) or [],
    )
    await coverage_store.async_load()

    coordinator = CardataCoordinator(hass=hass, entry_id=entry.entry_id)
    coordinator.history = history_store
    # One-time repair: legacy imports stored coordinates but no zone (see
    # HistoryStore.reresolve_zones). Now that the coordinator's zone lookup is
    # available, resolve them locally so a charge at Home stops showing "public".
    history_store.reresolve_zones(coordinator.zone_at)
    coordinator.coverage = coverage_store
    coordinator.pricing = PricingConfig.from_options(options)
    # Trips: the shared HA client session drives the (opt-in) reverse geocoder;
    # the work zone drives commute classification. Both are refreshed on reload.
    coordinator.geocoder = ReverseGeocoder(
        async_get_clientsession(hass),
        enabled=bool(options.get(OPTION_TRIP_GEOCODE)),
    )
    coordinator.work_zone_entity = options.get(OPTION_TRIP_WORK_ZONE) or None
    coordinator.diagnostic_interval = diagnostic_interval

    # Statistics backfill (roadmap Phase 4). Publishes the recorded history into
    # Home Assistant's long-term statistics under our own "bavariandata:"
    # namespace, so charging from before the install -- or from while HA was
    # down -- still lands on the Energy dashboard. See history/backfill.py.
    def _vehicle_name(vin: str) -> str:
        metadata = coordinator.device_metadata.get(vin, {})
        return metadata.get("name") or coordinator.names.get(vin, vin)

    statistics = StatisticsPublisher(
        hass,
        history_store,
        name_for=_vehicle_name,
        enabled=bool(options.get(OPTION_STATISTICS_IMPORT, DEFAULT_STATISTICS_IMPORT)),
    )

    @callback
    def _refresh_statistics(_caller: Any = None) -> None:
        # One handler, three callers, none of whose argument we need: the
        # dispatcher hands us a VIN and async_at_started hands us `hass`.
        #
        # Fires whenever a session or trip is recorded (or reclassified). The
        # publisher itself is fingerprint-gated, so a signal that changed nothing
        # -- a reclassification of an already-published trip, say -- costs a
        # dictionary comparison rather than a database rewrite.
        entry.async_create_background_task(
            hass, statistics.async_publish(), f"{DOMAIN}_statistics"
        )

    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_history, _refresh_statistics)
    )
    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_trips, _refresh_statistics)
    )
    # The recorder may not be up yet when we are: statistics is its feature, so
    # the first publish waits for Home Assistant to have finished starting.
    entry.async_on_unload(async_at_started(hass, _refresh_statistics))

    @callback
    def _evaluate_coverage(_now: Any = None) -> None:
        # Raise or clear a repair issue per vehicle when a selected cluster's
        # descriptors have gone unseen past the grace window -- "you enabled
        # cluster X but descriptor Y has never arrived". A repair issue (not a
        # notification) keeps this dismissible and out of the way until it
        # actually matters, and only appears once past grace so a fresh install
        # never cries wolf. Cheap enough to run on a slow timer.
        _refresh_coverage_issues(hass, entry.entry_id)

    # Once shortly after start (descriptors have restored by then), then on a
    # slow timer -- a "7 days" test does not need a fast cadence.
    entry.async_on_unload(async_at_started(hass, _evaluate_coverage))
    entry.async_on_unload(
        async_track_time_interval(hass, _evaluate_coverage, timedelta(hours=6))
    )
    last_poll_ts = data.get("last_telematic_poll")
    if isinstance(last_poll_ts, (int, float)) and last_poll_ts > 0:
        coordinator.last_telematic_api_at = datetime.fromtimestamp(
            last_poll_ts, timezone.utc
        )
    stored_metadata = data.get(VEHICLE_METADATA, {})
    if isinstance(stored_metadata, dict):
        device_registry = dr.async_get(hass)
        for vin, payload in stored_metadata.items():
            if not isinstance(payload, dict):
                continue
            try:
                metadata = coordinator.apply_basic_data(vin, payload)
            except Exception:  # pragma: no cover - defensive
                _LOGGER.debug("Failed to restore metadata for %s", vin, exc_info=True)
                continue
            if metadata:
                device_registry.async_get_or_create(
                    config_entry_id=entry.entry_id,
                    identifiers={(DOMAIN, vin)},
                    manufacturer=metadata.get("manufacturer", "BMW"),
                    name=metadata.get("name", vin),
                    model=metadata.get("model"),
                    sw_version=metadata.get("sw_version"),
                    hw_version=metadata.get("hw_version"),
                    serial_number=metadata.get("serial_number"),
                )
    quota_manager = await QuotaManager.async_create(hass, entry.entry_id)
    container_manager: Optional[CardataContainerManager] = CardataContainerManager(
        session=session,
        entry_id=entry.entry_id,
        initial_container_id=data.get("hv_container_id"),
    )

    async def handle_stream_error(reason: str) -> None:
        await _handle_stream_error(hass, entry, reason)

    manager = CardataStreamManager(
        hass=hass,
        client_id=client_id,
        gcid=gcid,
        id_token=id_token,
        host=data.get("mqtt_host", DEFAULT_STREAM_HOST),
        port=data.get("mqtt_port", DEFAULT_STREAM_PORT),
        keepalive=mqtt_keepalive,
        config_entry=entry,
        error_callback=handle_stream_error,
    )
    manager.set_message_callback(coordinator.async_handle_message)
    manager.set_status_callback(coordinator.async_handle_connection_event)

    refreshed_token = False
    try:
        await _refresh_tokens(entry, session, manager, container_manager)
        refreshed_token = True
    except CardataAuthError as err:
        _LOGGER.warning(
            "Initial token refresh failed for entry %s: %s; continuing with stored token",
            entry.entry_id,
            err,
        )
    except Exception as err:  # pylint: disable=broad-except
        await session.close()
        raise ConfigEntryNotReady(f"Initial token refresh failed: {err}") from err

    if not refreshed_token and container_manager:
        try:
            container_manager.sync_from_entry(entry.data.get("hv_container_id"))
            await container_manager.async_ensure_hv_container(entry.data.get("access_token"))
        except CardataContainerError as err:
            _LOGGER.warning(
                "Unable to ensure HV container for entry %s: %s",
                entry.entry_id,
                err,
            )

    if manager.client is None:
        try:
            await manager.async_start()
        except Exception as err:
            await session.close()
            if refreshed_token:
                raise ConfigEntryNotReady(
                    f"Unable to connect to BMW MQTT after token refresh: {err}"
                ) from err
            raise ConfigEntryNotReady(f"Unable to connect to BMW MQTT: {err}") from err

    refresh_task = entry.async_create_background_task(
        hass,
        _refresh_loop(hass, entry, session, manager, container_manager),
        f"{DOMAIN}_token_refresh",
    )

    stored_container_manager = container_manager

    runtime_data = CardataRuntimeData(
        stream=manager,
        refresh_task=refresh_task,
        session=session,
        coordinator=coordinator,
        container_manager=stored_container_manager,
        history=history_store,
        statistics=statistics,
        coverage=coverage_store,
        bootstrap_task=None,
        quota_manager=quota_manager,
        telematic_task=None,
        reauth_in_progress=False,
        reauth_flow_id=None,
    )
    entry.runtime_data = runtime_data
    # Track loaded entries so services (registered process-wide) are torn down
    # once the last entry unloads. Runtime itself lives on entry.runtime_data.
    domain_data.setdefault("_entries", set()).add(entry.entry_id)

    await coordinator.async_handle_connection_event("connecting")
    await coordinator.async_start_watchdog()

    if not domain_data.get("_service_registered"):

        def _resolve_target(call: Any) -> tuple[str, ConfigEntry, CardataRuntimeData] | None:
            entries = {
                loaded_entry.entry_id: loaded_entry.runtime_data
                for loaded_entry in hass.config_entries.async_entries(DOMAIN)
                if getattr(loaded_entry, "runtime_data", None) is not None
            }

            target_entry_id = call.data.get("entry_id")
            if target_entry_id:
                runtime = entries.get(target_entry_id)
                target_entry = hass.config_entries.async_get_entry(target_entry_id)
                if runtime is None or target_entry is None:
                    _LOGGER.error(
                        "Cardata service call: unknown entry_id %s",
                        target_entry_id,
                    )
                    return None
                return target_entry_id, target_entry, runtime

            if len(entries) != 1:
                _LOGGER.error(
                    "Cardata service call: multiple entries configured; specify entry_id"
                )
                return None

            target_entry_id, runtime = next(iter(entries.items()))
            target_entry = hass.config_entries.async_get_entry(target_entry_id)
            if target_entry is None:
                _LOGGER.error(
                    "Cardata service call: unable to resolve config entry %s",
                    target_entry_id,
                )
                return None

            return target_entry_id, target_entry, runtime

        async def async_handle_fetch(call) -> None:
            resolved = _resolve_target(call)
            if not resolved:
                return

            target_entry_id, target_entry, runtime = resolved
            success = await _async_perform_telematic_fetch(
                hass,
                target_entry,
                runtime,
                vin_override=call.data.get("vin"),
            )
            if success:
                _async_update_last_telematic_poll(hass, target_entry, time.time())

        async def async_handle_fetch_mappings(call) -> None:
            prepared = await _prepare_api_call(
                call, "fetch_vehicle_mappings", require_vin=False
            )
            if not prepared:
                return
            _entry, runtime, _vin, access_token = prepared
            try:
                payload = await async_get_vehicle_mappings(runtime.session, access_token)
            except CardataApiError as err:
                _LOGGER.error("Cardata fetch_vehicle_mappings failed: %s", err)
                return
            count = len(payload) if isinstance(payload, (list, dict)) else 0
            _LOGGER.info("Fetched %s vehicle mapping(s)", count)
            _LOGGER.debug("Cardata vehicle mappings: %s", payload)

        async def async_handle_fetch_basic_data(call) -> None:
            prepared = await _prepare_api_call(call, "fetch_basic_data", require_vin=True)
            if not prepared:
                return
            _entry, runtime, vin, access_token = prepared
            try:
                payload = await async_get_basic_data(runtime.session, access_token, vin)
            except CardataApiError as err:
                _LOGGER.error("Cardata fetch_basic_data failed for %s: %s", vin, err)
                return
            _LOGGER.info("Fetched basic vehicle data for %s", vin)
            _LOGGER.debug("Cardata basic data for %s: %s", vin, payload)
            if isinstance(payload, dict):
                metadata = runtime.coordinator.apply_basic_data(vin, payload)
                if metadata:
                    _async_store_vehicle_metadata(
                        hass,
                        entry,
                        vin,
                        metadata.get("raw_data") or payload,
                    )
                    device_registry = dr.async_get(hass)
                    device_registry.async_get_or_create(
                        config_entry_id=entry.entry_id,
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
        ) -> tuple[ConfigEntry, "CardataRuntimeData", Optional[str], str] | None:
            """Resolve target, refresh tokens, claim quota and return call context.

            Shared by the read-only CarData API services. Returns
            ``(entry, runtime, vin, access_token)`` or ``None`` if the call cannot
            proceed (already logged).
            """

            resolved = _resolve_target(call)
            if not resolved:
                return None
            target_entry_id, target_entry, runtime = resolved

            vin = call.data.get("vin") or target_entry.data.get("vin")
            if vin is None and runtime.coordinator.data:
                vin = next(iter(runtime.coordinator.data))
            if require_vin and not vin:
                _LOGGER.error("Cardata %s: no VIN available; provide vin parameter", label)
                return None

            try:
                await _refresh_tokens(
                    target_entry,
                    runtime.session,
                    runtime.stream,
                    runtime.container_manager,
                )
            except CardataAuthError as err:
                _LOGGER.error(
                    "Cardata %s: token refresh failed for entry %s: %s",
                    label,
                    target_entry_id,
                    err,
                )
                return None

            access_token = target_entry.data.get("access_token")
            if not access_token:
                _LOGGER.error("Cardata %s: access token missing after refresh", label)
                return None

            quota = runtime.quota_manager
            if quota:
                try:
                    await quota.async_claim()
                except CardataQuotaError as err:
                    _LOGGER.warning("Cardata %s blocked: %s", label, err)
                    return None

            return target_entry, runtime, vin, access_token

        async def async_handle_fetch_charging_history(call) -> None:
            prepared = await _prepare_api_call(
                call, "fetch_charging_history", require_vin=True
            )
            if not prepared:
                return
            _entry, runtime, vin, access_token = prepared
            # BMW requires both from/to; default to the last 30 days so a bare
            # call from the UI succeeds instead of returning HTTP 400.
            to_date = _as_cardata_timestamp(call.data.get("to")) or _cardata_timestamp(
                dt_util.utcnow()
            )
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
                _LOGGER.error("Cardata fetch_charging_history failed for %s: %s", vin, err)
                return
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
                )
                if imported or updated:
                    async_dispatcher_send(
                        hass, runtime.coordinator.signal_history, vin
                    )
            _LOGGER.info(
                "Fetched charging history for %s (%s session(s); imported %s, updated %s)",
                vin,
                len(sessions) if isinstance(sessions, list) else 0,
                imported,
                updated,
            )
            _LOGGER.debug("Cardata charging history for %s: %s", vin, payload)

        async def async_handle_fetch_tyre_diagnosis(call) -> None:
            prepared = await _prepare_api_call(
                call, "fetch_tyre_diagnosis", require_vin=True
            )
            if not prepared:
                return
            _entry, runtime, vin, access_token = prepared
            try:
                payload = await async_get_tyre_diagnosis(runtime.session, access_token, vin)
            except CardataApiError as err:
                _LOGGER.error("Cardata fetch_tyre_diagnosis failed for %s: %s", vin, err)
                return
            _LOGGER.info("Fetched tyre diagnosis for %s", vin)
            _LOGGER.debug("Cardata tyre diagnosis for %s: %s", vin, payload)

        async def async_handle_fetch_location_charging(call) -> None:
            prepared = await _prepare_api_call(
                call, "fetch_location_charging_settings", require_vin=True
            )
            if not prepared:
                return
            _entry, runtime, vin, access_token = prepared
            try:
                payload = await async_get_location_based_charging_settings(
                    runtime.session, access_token, vin
                )
            except CardataApiError as err:
                _LOGGER.error(
                    "Cardata fetch_location_charging_settings failed for %s: %s", vin, err
                )
                return
            settings = payload.get("data") if isinstance(payload, dict) else None
            _LOGGER.info(
                "Fetched location-based charging settings for %s (%s entr(y/ies))",
                vin,
                len(settings) if isinstance(settings, list) else 0,
            )
            _LOGGER.debug(
                "Cardata location-based charging settings for %s: %s", vin, payload
            )

        async def async_handle_fetch_vehicle_image(call) -> None:
            # Resolve the target entry/vin (this also refreshes tokens and claims a
            # quota slot); the shared image helper performs the actual fetch and
            # updates the ``image`` entity's cache + Store so the render shows up in
            # the Lovelace card. Token refresh / quota are handled inside the helper
            # as well, so we resolve the target here without claiming twice.
            resolved = _resolve_target(call)
            if not resolved:
                return
            _target_id, target_entry, runtime = resolved
            vin = call.data.get("vin") or target_entry.data.get("vin")
            if vin is None and runtime.coordinator.data:
                vin = next(iter(runtime.coordinator.data))
            if not vin:
                _LOGGER.error(
                    "Cardata fetch_vehicle_image: no VIN available; provide vin parameter"
                )
                return
            from .image import async_refresh_vehicle_image

            await async_refresh_vehicle_image(hass, target_entry, runtime, vin)

        telematic_service_schema = vol.Schema(
            {
                vol.Optional("entry_id"): str,
                vol.Optional("vin"): str,
            }
        )
        mapping_service_schema = vol.Schema({vol.Optional("entry_id"): str})
        basic_data_service_schema = vol.Schema(
            {
                vol.Optional("entry_id"): str,
                vol.Optional("vin"): str,
            }
        )
        vin_service_schema = vol.Schema(
            {
                vol.Optional("entry_id"): str,
                vol.Optional("vin"): str,
            }
        )
        charging_history_schema = vol.Schema(
            {
                vol.Optional("entry_id"): str,
                vol.Optional("vin"): str,
                vol.Optional("from"): str,
                vol.Optional("to"): str,
            }
        )
        get_sessions_schema = vol.Schema(
            {
                vol.Optional("entry_id"): str,
                vol.Optional("vin"): str,
                vol.Optional("from"): str,
                vol.Optional("to"): str,
                vol.Optional("limit"): vol.All(int, vol.Range(min=1)),
            }
        )

        async def async_handle_get_charging_sessions(call: Any) -> dict:
            target = _resolve_target(call)
            if target is None or target[2].history is None:
                return {"sessions": []}
            _entry_id, _entry, runtime = target

            def _bound(key: str) -> Optional[datetime]:
                raw = call.data.get(key)
                if not raw:
                    return None
                parsed = dt_util.parse_datetime(raw) or dt_util.parse_date(raw)
                if parsed is None:
                    raise ServiceValidationError(
                        f"Could not read '{key}' as a date or date/time: {raw!r}"
                    )
                if isinstance(parsed, datetime):
                    return dt_util.as_utc(parsed)
                return dt_util.as_utc(datetime.combine(parsed, dt_time.min))

            sessions = runtime.history.sessions(
                call.data.get("vin"),
                start=_bound("from"),
                end=_bound("to"),
                limit=_as_limit(call.data.get("limit")),
            )
            return {"sessions": [session.to_dict() for session in sessions]}

        def _service_bound(call: Any, key: str) -> Optional[datetime]:
            raw = call.data.get(key)
            if not raw:
                return None
            parsed = dt_util.parse_datetime(raw) or dt_util.parse_date(raw)
            if parsed is None:
                raise ServiceValidationError(
                    f"Could not read '{key}' as a date or date/time: {raw!r}"
                )
            if isinstance(parsed, datetime):
                return dt_util.as_utc(parsed)
            return dt_util.as_utc(datetime.combine(parsed, dt_time.min))

        def _default_vin(runtime: CardataRuntimeData, vin: Optional[str]) -> Optional[str]:
            if vin is not None:
                return vin
            data = runtime.coordinator.data
            return next(iter(data)) if data else None

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
                raise ServiceValidationError(
                    f"'month' must be 'YYYY-MM', got {raw!r}"
                ) from err
            if not 1 <= month <= 12:
                raise ServiceValidationError(f"'month' has no month {month}: {raw!r}")
            return year, month

        get_trips_schema = get_sessions_schema
        get_driving_summary_schema = vol.Schema(
            {
                vol.Optional("entry_id"): str,
                vol.Optional("vin"): str,
                vol.Optional("month"): str,  # "YYYY-MM"; defaults to this month
            }
        )
        set_trip_class_schema = vol.Schema(
            {
                vol.Optional("entry_id"): str,
                vol.Optional("vin"): str,
                vol.Required("trip_id"): str,
                vol.Required("classification"): vol.In(list(CLASSIFICATIONS)),
            }
        )

        async def async_handle_get_trips(call: Any) -> dict:
            target = _resolve_target(call)
            if target is None or target[2].history is None:
                return {"trips": []}
            _entry_id, _entry, runtime = target
            trips = runtime.history.trips(
                call.data.get("vin"),
                start=_service_bound(call, "from"),
                end=_service_bound(call, "to"),
                limit=_as_limit(call.data.get("limit")),
            )
            return {"trips": [trip.to_dict() for trip in trips]}

        async def async_handle_get_driving_summary(call: Any) -> dict:
            target = _resolve_target(call)
            if target is None or target[2].history is None:
                return {"summary": {}, "month": None}
            _entry_id, _entry, runtime = target
            coordinator = runtime.coordinator
            history = runtime.history
            vin = _default_vin(runtime, call.data.get("vin"))

            year, month = _month_bounds(call.data.get("month"))
            prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)

            all_trips = history.trips(vin)
            month_trips = trips_in_month(
                all_trips, year=year, month=month, localize=dt_util.as_local
            )
            prev_trips = trips_in_month(
                all_trips, year=prev_year, month=prev_month, localize=dt_util.as_local
            )

            # The estimated driving cost reuses the charging ledger's own
            # cost-per-100km for the same month -- trips × the charging record.
            cost_per_100km = None
            if coordinator.pricing.enabled:
                ledger = summarise(
                    sessions_in_month(
                        history.sessions(vin),
                        year=year,
                        month=month,
                        localize=dt_util.as_local,
                    )
                )
                cost_per_100km = ledger.get("cost_per_100km")

            summary = driving_summary(
                month_trips,
                prev_trips=prev_trips,
                cost_per_100km=cost_per_100km,
                currency=coordinator.pricing.currency,
            )
            return {"summary": summary, "month": f"{year:04d}-{month:02d}"}

        async def async_handle_set_trip_class(call: Any) -> None:
            target = _resolve_target(call)
            if target is None or target[2].history is None:
                raise ServiceValidationError("Trip history is not available")
            _entry_id, _entry, runtime = target
            coordinator = runtime.coordinator
            vin = _default_vin(runtime, call.data.get("vin"))
            trip_id = call.data["trip_id"]
            trip = runtime.history.find_trip(vin, trip_id)
            if trip is None:
                raise ServiceValidationError(
                    f"No trip {trip_id!r} found for vehicle {vin}"
                )
            trip.classification = call.data["classification"]
            trip.classification_source = SOURCE_USER
            runtime.history.add_trip(trip)
            async_dispatcher_send(hass, coordinator.signal_trips, vin)

        # --- Phase 4: statistics backfill and export ------------------------

        export_schema = vol.Schema(
            {
                vol.Optional("entry_id"): str,
                vol.Optional("vin"): str,
                vol.Optional("month"): str,  # "YYYY-MM"; defaults to this month
                vol.Optional("type", default="both"): vol.In(
                    ("charging", "trips", "both")
                ),
                vol.Optional("format", default="csv"): vol.In(("csv", "html")),
                vol.Optional("language"): str,
            }
        )
        import_statistics_schema = vol.Schema(
            {
                vol.Optional("entry_id"): str,
                vol.Optional("vin"): str,
            }
        )
        coverage_report_schema = vol.Schema(
            {
                vol.Optional("entry_id"): str,
                vol.Optional("vin"): str,
            }
        )

        async def async_handle_get_coverage_report(call: Any) -> dict:
            """Run the descriptor-coverage self-test and return it as data.

            Reads only the local coverage store and live stream state, so it
            spends no BMW API quota.
            """

            target = _resolve_target(call)
            if target is None or target[2].coverage is None:
                return {"reports": []}
            _entry_id, _entry, runtime = target
            reports = _coverage_reports(runtime, call.data.get("vin"))
            return {"reports": [report.to_dict() for report in reports]}

        async def async_handle_export_history(call: Any) -> dict:
            """Render the recorded month as files the user keeps.

            Returns the file *contents* rather than writing to disk: the card
            turns them into a browser download, which needs no path
            configuration, no allowlisted directory, and works from a phone.
            """

            target = _resolve_target(call)
            if target is None or target[2].history is None:
                return {"files": [], "month": None}
            _entry_id, _entry, runtime = target
            history = runtime.history
            vin = _default_vin(runtime, call.data.get("vin"))
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
                trips_in_month(
                    history.trips(vin), year=year, month=month, localize=dt_util.as_local
                )
                if wanted in ("trips", "both")
                else []
            )

            # A file name the user can find again after ten monthly exports.
            stem = f"bavariandata-{(vin or 'vehicle').lower()}-{label}"
            files: list[dict[str, Any]] = []

            if fmt == "html":
                # Same aggregations the card renders, so a printed report and
                # the dashboard can never disagree about a month's totals.
                ledger = summarise(sessions)
                vehicle = (
                    runtime.coordinator.device_metadata.get(vin or "", {}).get("name")
                    or vin
                    or "BMW"
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
                            "content": sessions_csv(
                                sessions, localize=dt_util.as_local
                            ),
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

            target = _resolve_target(call)
            if target is None or target[2].statistics is None:
                return {"statistics": {}}
            _entry_id, _entry, runtime = target
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
            written = await publisher.async_publish(
                vin=call.data.get("vin"), force=True
            )
            _LOGGER.info("Imported BavarianData statistics: %s", written)
            return {"statistics": written}

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
            "set_trip_class",
            async_handle_set_trip_class,
            schema=set_trip_class_schema,
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
        registered_services = domain_data.setdefault("_registered_services", set())
        registered_services.update(
            {
                "fetch_telematic_data",
                "fetch_vehicle_mappings",
                "fetch_basic_data",
                "fetch_charging_history",
                "fetch_tyre_diagnosis",
                "fetch_location_charging_settings",
                "fetch_vehicle_image",
                "get_charging_sessions",
                "get_trips",
                "get_driving_summary",
                "set_trip_class",
                "export_history",
                "import_statistics",
                "get_coverage_report",
            }
        )
        domain_data["_service_registered"] = True

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if should_bootstrap:
        runtime_data.bootstrap_task = entry.async_create_background_task(
            hass, _async_run_bootstrap(hass, entry), f"{DOMAIN}_bootstrap"
        )

    runtime_data.telematic_task = entry.async_create_background_task(
        hass, _telematic_poll_loop(hass, entry.entry_id), f"{DOMAIN}_telematic_poll"
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: CardataConfigEntry) -> bool:
    data: CardataRuntimeData | None = getattr(entry, "runtime_data", None)
    if data is None:
        return True
    domain_data = hass.data.get(DOMAIN, {})
    domain_data.get("_entries", set()).discard(entry.entry_id)
    await data.coordinator.async_stop_watchdog()
    # Drop the stream-health repairs this entry raised, so a reconfigure or
    # removal doesn't leave a stale "no data" / "unauthorized" warning behind.
    data.coordinator.async_clear_stream_repairs()
    # Close any in-progress trip before the debounced save below, so a reload
    # mid-drive doesn't lose it.
    await data.coordinator.async_flush_trips()
    # Likewise commit a charge whose debounced close was still pending, so a
    # reload during the flap grace window doesn't drop the session.
    data.coordinator.async_flush_charging()
    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    data.refresh_task.cancel()
    with suppress(asyncio.CancelledError):
        await data.refresh_task
    if data.bootstrap_task:
        data.bootstrap_task.cancel()
        with suppress(asyncio.CancelledError):
            await data.bootstrap_task
    if data.telematic_task:
        data.telematic_task.cancel()
        with suppress(asyncio.CancelledError):
            await data.telematic_task
    if data.quota_manager:
        await data.quota_manager.async_close()
    if data.history:
        # Saves are debounced, so a reload or a restart right after a session
        # ended would otherwise drop it.
        with suppress(Exception):
            await data.history.async_save_now()
    if data.coverage:
        with suppress(Exception):
            await data.coverage.async_save_now()
        # Drop any coverage repair issues this entry raised, so a reconfigure or
        # removal doesn't leave a stale "descriptor missing" warning behind.
        for vin in list(data.coordinator.data):
            ir.async_delete_issue(hass, DOMAIN, _coverage_issue_id(entry.entry_id, vin))
    await data.stream.async_stop()
    await data.session.close()
    remaining_entries = domain_data.get("_entries") or set()
    if not remaining_entries:
        registered_services = domain_data.get("_registered_services", set())
        for service in list(registered_services):
            if hass.services.has_service(DOMAIN, service):
                hass.services.async_remove(DOMAIN, service)
        domain_data.pop("_service_registered", None)
        domain_data.pop("_registered_services", None)
        domain_data.pop("_entries", None)
        hass.data.pop(DOMAIN, None)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete this entry's recorded history when the integration is removed.

    Charging history outlives the recorder by design, so it also has to be
    cleaned up deliberately -- removing the integration should not leave a
    record of where and when the car charged behind in .storage.
    """

    store = HistoryStore(hass, entry.entry_id)
    with suppress(Exception):
        # Load first: the published statistic ids are derived from the VINs in
        # the store, so they have to be read before the store is emptied.
        await store.async_load()
        publisher = StatisticsPublisher(hass, store, name_for=lambda vin: vin)
        await publisher.async_remove()
    with suppress(Exception):
        await store.async_clear()
    with suppress(Exception):
        # The coverage self-test keeps its own small store; remove it too so
        # nothing of this entry is left behind in .storage.
        await CoverageStore(hass, entry.entry_id, sections=[]).async_clear()


async def _handle_stream_error(hass: HomeAssistant, entry: CardataConfigEntry, reason: str) -> None:
    runtime: CardataRuntimeData = entry.runtime_data
    notification_id = f"{DOMAIN}_reauth_{entry.entry_id}"
    if reason == "unauthorized":
        if runtime.reauth_in_progress:
            _LOGGER.debug("Ignoring duplicate unauthorized notification for entry %s", entry.entry_id)
            return

        now = time.time()

        if runtime.reauth_pending:
            _LOGGER.debug(
                "Reauth pending for entry %s after failed refresh; starting flow",
                entry.entry_id,
            )
        elif now - runtime.last_refresh_attempt >= 30:
            runtime.last_refresh_attempt = now
            try:
                _LOGGER.info(
                    "Attempting token refresh after unauthorized response for entry %s",
                    entry.entry_id,
                )
                await _refresh_tokens(
                    entry,
                    runtime.session,
                    runtime.stream,
                    runtime.container_manager,
                )
                runtime.reauth_in_progress = False
                runtime.last_reauth_attempt = 0.0
                runtime.reauth_pending = False
                return
            except CardataAuthError as err:
                _LOGGER.warning(
                    "Token refresh after unauthorized failed for entry %s: %s",
                    entry.entry_id,
                    err,
                )
        else:
            runtime.reauth_pending = True
            _LOGGER.debug(
                "Token refresh attempted recently for entry %s; will trigger reauth",
                entry.entry_id,
            )

        if now - runtime.last_reauth_attempt < 30:
            _LOGGER.debug(
                "Recent reauth already attempted for entry %s; skipping new flow",
                entry.entry_id,
            )
            return

        runtime.reauth_in_progress = True
        runtime.last_reauth_attempt = now
        runtime.reauth_pending = False
        _LOGGER.error("BMW stream unauthorized; starting reauth flow")
        if runtime.reauth_flow_id:
            with suppress(Exception):
                await hass.config_entries.flow.async_abort(runtime.reauth_flow_id)
            runtime.reauth_flow_id = None
        persistent_notification.async_create(
            hass,
            "Authorization failed for BMW CarData. Please reauthorize the integration.",
            title="BavarianData: Connect Home Assistant to BMW CarData",
            notification_id=notification_id,
        )
        flow_result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
            data={**entry.data, "entry_id": entry.entry_id},
        )
        if isinstance(flow_result, dict):
            runtime.reauth_flow_id = flow_result.get("flow_id")
    elif reason == "recovered":
        if runtime.reauth_in_progress:
            runtime.reauth_in_progress = False
            _LOGGER.info("BMW stream connection restored; dismissing reauth notification")
            persistent_notification.async_dismiss(hass, notification_id)
            if runtime.reauth_flow_id:
                with suppress(Exception):
                    await hass.config_entries.flow.async_abort(runtime.reauth_flow_id)
                runtime.reauth_flow_id = None
        runtime.reauth_pending = False
        runtime.last_reauth_attempt = 0.0


async def _refresh_loop(
    hass: HomeAssistant,
    entry: ConfigEntry,
    session: aiohttp.ClientSession,
    manager: CardataStreamManager,
    container_manager: Optional[CardataContainerManager],
) -> None:
    try:
        while True:
            await asyncio.sleep(DEFAULT_REFRESH_INTERVAL)
            try:
                await _refresh_tokens(
                    entry,
                    session,
                    manager,
                    container_manager,
                )
            except CardataAuthError as err:
                _LOGGER.error("Token refresh failed: %s", err)
    except asyncio.CancelledError:
        return


async def _refresh_tokens(
    entry: ConfigEntry,
    session: aiohttp.ClientSession,
    manager: CardataStreamManager,
    container_manager: CardataContainerManager | None = None,
) -> None:
    hass = manager.hass
    data = dict(entry.data)
    refresh_token = data.get("refresh_token")
    client_id = data.get("client_id")
    if not refresh_token or not client_id:
        raise CardataAuthError("Missing credentials for refresh")

    requested_scope = data.get("scope") or DEFAULT_SCOPE

    token_data = await refresh_tokens(
        session,
        client_id=client_id,
        refresh_token=refresh_token,
        scope=requested_scope,
    )

    new_id_token = token_data.get("id_token")
    if not new_id_token:
        raise CardataAuthError("Token refresh response did not include id_token")
    data.update(
        {
            "access_token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token", refresh_token),
            "id_token": new_id_token,
            "expires_in": token_data.get("expires_in"),
            "scope": token_data.get("scope", data.get("scope")),
            "token_type": token_data.get("token_type", data.get("token_type")),
            "received_at": time.time(),
        }
    )

    desired_signature = CardataContainerManager.compute_signature(HV_BATTERY_DESCRIPTORS)

    if container_manager:
        hv_container_id = data.get("hv_container_id")
        stored_signature = data.get("hv_descriptor_signature")
        access_token = data.get("access_token")

        if hv_container_id and stored_signature == desired_signature:
            container_manager.sync_from_entry(hv_container_id)
        elif hv_container_id and stored_signature is None:
            data["hv_descriptor_signature"] = desired_signature
            container_manager.sync_from_entry(hv_container_id)
        else:
            container_manager.sync_from_entry(None)
            try:
                container_id = await container_manager.async_ensure_hv_container(
                    access_token
                )
            except CardataContainerError as err:
                _LOGGER.warning(
                    "Unable to ensure HV container for entry %s: %s",
                    entry.entry_id,
                    err,
                )
            else:
                if container_id:
                    data["hv_container_id"] = container_id
                    data["hv_descriptor_signature"] = desired_signature
                    container_manager.sync_from_entry(container_id)
                    runtime = getattr(entry, "runtime_data", None)
                    if runtime and runtime.container_manager:
                        runtime.container_manager.sync_from_entry(container_id)

    hass.config_entries.async_update_entry(entry, data=data)
    await manager.async_update_credentials(
        gcid=data.get("gcid"),
        id_token=new_id_token,
    )
    runtime = getattr(entry, "runtime_data", None)
    if runtime:
        runtime.reauth_pending = False


async def async_manual_refresh_tokens(hass: HomeAssistant, entry: CardataConfigEntry) -> None:
    runtime: CardataRuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime is None:
        raise CardataAuthError("Integration runtime not ready")
    await _refresh_tokens(
        entry,
        runtime.session,
        runtime.stream,
        runtime.container_manager,
    )


async def _async_run_bootstrap(hass: HomeAssistant, entry: CardataConfigEntry) -> None:
    runtime: CardataRuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime is None:
        return

    _LOGGER.debug("Starting bootstrap sequence for entry %s", entry.entry_id)

    quota = runtime.quota_manager

    try:
        await _refresh_tokens(
            entry,
            runtime.session,
            runtime.stream,
            runtime.container_manager,
        )
    except CardataAuthError as err:
        _LOGGER.warning(
            "Bootstrap token refresh failed for entry %s: %s",
            entry.entry_id,
            err,
        )
        return

    data = entry.data
    access_token = data.get("access_token")
    if not access_token:
        _LOGGER.debug(
            "Bootstrap aborted for entry %s due to missing access token",
            entry.entry_id,
        )
        return

    vins = await _async_fetch_primary_vins(
        runtime.session,
        access_token,
        entry.entry_id,
        quota,
    )
    if not vins:
        await _async_mark_bootstrap_complete(hass, entry)
        return

    device_registry = dr.async_get(hass)
    coordinator = runtime.coordinator
    for vin in vins:
        coordinator.data.setdefault(vin, {})
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, vin)},
            manufacturer="BMW",
            name=coordinator.names.get(vin, vin),
        )

    created_entities = False
    container_id = entry.data.get("hv_container_id")
    if container_id:
        created_entities = await _async_seed_telematic_data(
            runtime,
            entry.entry_id,
            access_token,
            container_id,
            vins,
            quota,
        )
    else:
        _LOGGER.debug(
            "Bootstrap skipping telematic seed for entry %s due to missing container id",
            entry.entry_id,
        )

    if created_entities:
        await _async_fetch_basic_data_for_vins(
            hass,
            entry,
            access_token,
            vins,
            quota,
        )
        _async_update_last_telematic_poll(hass, entry, time.time())
    else:
        _LOGGER.debug(
            "Bootstrap did not seed new descriptors for entry %s; basic data fetch skipped",
            entry.entry_id,
        )
    # The initial telematics fetch counts as an API call, but we don't set
    # last_telematic_poll_time here because the bootstrap should not delay the
    # first scheduled poll unnecessarily.

    await _async_mark_bootstrap_complete(hass, entry)


async def _async_fetch_primary_vins(
    session: aiohttp.ClientSession,
    access_token: str,
    entry_id: str,
    quota: QuotaManager | None,
) -> List[str]:
    if quota:
        try:
            await quota.async_claim()
        except CardataQuotaError as err:
            _LOGGER.warning(
                "Bootstrap mapping request skipped for entry %s: %s",
                entry_id,
                err,
            )
            return []
    try:
        payload = await async_get_vehicle_mappings(session, access_token)
    except CardataApiError as err:
        _LOGGER.warning(
            "Bootstrap mapping request failed for entry %s: %s",
            entry_id,
            err,
        )
        return []

    mappings: List[Dict[str, Any]]
    if isinstance(payload, list):
        mappings = [item for item in payload if isinstance(item, dict)]
    elif isinstance(payload, dict):
        possible = payload.get("mappings") or payload.get("vehicles") or []
        mappings = [item for item in possible if isinstance(item, dict)]
    else:
        mappings = []

    vins: List[str] = []
    for mapping in mappings:
        mapping_type = mapping.get("mappingType")
        if mapping_type and mapping_type.upper() != "PRIMARY":
            continue
        vin = mapping.get("vin")
        if isinstance(vin, str):
            vins.append(vin)

    if not vins:
        _LOGGER.info("Bootstrap mapping for entry %s returned no primary vehicles", entry_id)
    else:
        _LOGGER.debug("Bootstrap found %s mapped vehicle(s) for entry %s", len(vins), entry_id)
    return vins


async def _async_seed_telematic_data(
    runtime: CardataRuntimeData,
    entry_id: str,
    access_token: str,
    container_id: str,
    vins: List[str],
    quota: QuotaManager | None,
) -> bool:
    session = runtime.session
    coordinator = runtime.coordinator
    created = False

    for vin in vins:
        if coordinator.data.get(vin):
            continue
        if quota:
            try:
                await quota.async_claim()
            except CardataQuotaError as err:
                _LOGGER.warning(
                    "Bootstrap telematic request skipped for %s: %s",
                    vin,
                    err,
                )
                break
        try:
            payload = await async_get_telematic_data(
                session, access_token, vin, container_id
            )
        except CardataApiError as err:
            _LOGGER.debug(
                "Bootstrap telematic request failed for %s: %s",
                vin,
                err,
            )
            continue

        telematic_data = None
        if isinstance(payload, dict):
            telematic_data = payload.get("telematicData") or payload.get("data")
        if not isinstance(telematic_data, dict) or not telematic_data:
            continue
        message = {"vin": vin, "data": telematic_data}
        await coordinator.async_handle_message(message)
        created = True

    return created


async def _async_fetch_basic_data_for_vins(
    hass: HomeAssistant,
    entry: ConfigEntry,
    access_token: str,
    vins: List[str],
    quota: QuotaManager | None,
) -> None:
    runtime: CardataRuntimeData = entry.runtime_data
    session = runtime.session
    coordinator = runtime.coordinator
    device_registry = dr.async_get(hass)

    for vin in vins:
        if quota:
            try:
                await quota.async_claim()
            except CardataQuotaError as err:
                _LOGGER.warning(
                    "Bootstrap basic data request skipped for %s: %s",
                    vin,
                    err,
                )
                break
        try:
            payload = await async_get_basic_data(session, access_token, vin)
        except CardataApiError as err:
            _LOGGER.debug(
                "Bootstrap basic data request failed for %s: %s",
                vin,
                err,
            )
            continue

        if not isinstance(payload, dict):
            continue

        metadata = coordinator.apply_basic_data(vin, payload)
        if not metadata:
            continue

        _async_store_vehicle_metadata(hass, entry, vin, metadata.get("raw_data") or payload)

        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, vin)},
            manufacturer=metadata.get("manufacturer", "BMW"),
            name=metadata.get("name", vin),
            model=metadata.get("model"),
            sw_version=metadata.get("sw_version"),
            hw_version=metadata.get("hw_version"),
            serial_number=metadata.get("serial_number"),
        )


async def _async_mark_bootstrap_complete(hass: HomeAssistant, entry: ConfigEntry) -> None:
    if entry.data.get(BOOTSTRAP_COMPLETE):
        return
    updated = dict(entry.data)
    updated[BOOTSTRAP_COMPLETE] = True
    hass.config_entries.async_update_entry(entry, data=updated)


async def _async_perform_telematic_fetch(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: CardataRuntimeData,
    *,
    vin_override: Optional[str] = None,
) -> bool:
    target_entry_id = entry.entry_id
    vin = vin_override or entry.data.get("vin")
    if not vin and runtime.coordinator.data:
        vin = next(iter(runtime.coordinator.data))
    if not vin:
        _LOGGER.error(
            "Cardata fetch_telematic_data: no VIN available; provide vin parameter"
        )
        return False

    container_id = entry.data.get("hv_container_id")
    if not container_id:
        _LOGGER.error(
            "Cardata fetch_telematic_data: no container_id stored for entry %s",
            target_entry_id,
        )
        return False

    try:
        await _refresh_tokens(
            entry,
            runtime.session,
            runtime.stream,
            runtime.container_manager,
        )
    except CardataAuthError as err:
        _LOGGER.error(
            "Cardata fetch_telematic_data: token refresh failed for entry %s: %s",
            target_entry_id,
            err,
        )
        return False

    access_token = entry.data.get("access_token")
    if not access_token:
        _LOGGER.error(
            "Cardata fetch_telematic_data: access token missing after refresh"
        )
        return False

    quota = runtime.quota_manager
    if quota:
        try:
            await quota.async_claim()
        except CardataQuotaError as err:
            _LOGGER.warning(
                "Cardata fetch_telematic_data blocked for %s: %s",
                vin,
                err,
            )
            return False

    try:
        payload = await async_get_telematic_data(
            runtime.session, access_token, vin, container_id
        )
    except CardataApiError as err:
        _LOGGER.error(
            "Cardata fetch_telematic_data: request failed for %s: %s",
            vin,
            err,
        )
        return True

    _LOGGER.info("Fetched telematic data for %s", vin)
    _LOGGER.debug("Cardata telematic data for %s: %s", vin, payload)
    telematic_payload = None
    if isinstance(payload, dict):
        telematic_payload = payload.get("telematicData") or payload.get("data")
    if isinstance(telematic_payload, dict):
        await runtime.coordinator.async_handle_message(
            {"vin": vin, "data": telematic_payload}
        )
    runtime.coordinator.last_telematic_api_at = datetime.now(timezone.utc)
    async_dispatcher_send(
        runtime.coordinator.hass, runtime.coordinator.signal_diagnostics
    )
    return True


def _async_update_last_telematic_poll(
    hass: HomeAssistant, entry: ConfigEntry, timestamp: float
) -> None:
    existing = entry.data.get("last_telematic_poll")
    if existing and abs(existing - timestamp) < 1:
        return
    updated = dict(entry.data)
    updated["last_telematic_poll"] = timestamp
    hass.config_entries.async_update_entry(entry, data=updated)


async def _telematic_poll_loop(hass: HomeAssistant, entry_id: str) -> None:
    try:
        while True:
            entry = hass.config_entries.async_get_entry(entry_id)
            runtime: CardataRuntimeData | None = (
                getattr(entry, "runtime_data", None) if entry else None
            )
            if entry is None or runtime is None:
                return

            last_poll = entry.data.get("last_telematic_poll", 0.0)
            now = time.time()
            wait = TELEMATIC_POLL_INTERVAL - (now - last_poll)
            if wait > 0:
                await asyncio.sleep(wait)
                continue

            await _async_perform_telematic_fetch(
                hass,
                entry,
                runtime,
            )
            _async_update_last_telematic_poll(hass, entry, time.time())
            await asyncio.sleep(TELEMATIC_POLL_INTERVAL)
    except asyncio.CancelledError:
        return


def _async_store_vehicle_metadata(
    hass: HomeAssistant,
    entry: ConfigEntry,
    vin: str,
    payload: Dict[str, Any],
) -> None:
    existing_metadata = entry.data.get(VEHICLE_METADATA, {})
    if not isinstance(existing_metadata, dict):
        existing_metadata = {}
    current = existing_metadata.get(vin)
    if current == payload:
        return
    updated = dict(entry.data)
    new_metadata = dict(existing_metadata)
    new_metadata[vin] = payload
    updated[VEHICLE_METADATA] = new_metadata
    hass.config_entries.async_update_entry(entry, data=updated)
