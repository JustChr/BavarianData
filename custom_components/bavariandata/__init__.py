"""BMW CarData integration for Home Assistant."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from contextlib import suppress
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Deque, Dict, List, Optional

import aiohttp

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.config_entries import ConfigEntry, SOURCE_REAUTH
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady

from homeassistant.components import persistent_notification
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started


from .const import (
    DEFAULT_SCOPE,
    DEFAULT_STREAM_HOST,
    DEFAULT_STREAM_PORT,
    DEFAULT_REFRESH_INTERVAL,
    TOKEN_REFRESH_RETRY_DELAY,
    TOKEN_REFRESH_RETRY_MAX,
    DOMAIN,
    MQTT_KEEPALIVE,
    DIAGNOSTIC_LOG_INTERVAL,
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
    OPTION_TRIP_GEOCODE,
    OPTION_TRIP_DEBUG,
    OPTION_STATISTICS_IMPORT,
    DEFAULT_STATISTICS_IMPORT,
    OPTION_REFRESH_ON_START,
    DEFAULT_REFRESH_ON_START,
    LAST_STARTUP_REFRESH,
    OPTION_STREAM_SECTIONS,
    DEBUG_LOG,
    LOVELACE_CARD_FILENAME,
    LOVELACE_CARD_URL,
)
from .device_flow import CardataAuthError, refresh_rejected, refresh_tokens
from .api import (
    CardataApiError,
    async_get_basic_data,
    async_get_telematic_data,
    async_get_tyre_diagnosis,
    async_get_vehicle_mappings,
)
from .container import (
    FULL,
    KINDS,
    RETIRED_ENTRY_KEYS,
    SERVICE,
    CardataContainerError,
    CardataContainerManager,
    ContainerKind,
)
from .rest import CardataQuotaError, RestGateway, TokenKeeper
from .coverage import coverage_issue_id, legacy_coverage_issue_id
from .coverage_store import CoverageStore

from .startup_refresh import (
    STARTUP_REFRESH_DELAY_S,
    outage_needs_catch_up,
    startup_refresh_vins,
)
from .trip_settings import account_trip_settings, vehicle_trip_settings
from .vehicle_options import mileage_allowances
from .tyre_store import TyreStore
from .vehicles import known_vins, primary_vins
from .history.backfill import StatisticsPublisher
from .bridge import REPUBLISH_INTERVAL_S, VehicleBridge, async_clear_published
from .descriptor_metadata import DESCRIPTOR_META
from .registry_repair import (
    entities_to_reenable,
    ev_entities_to_remove,
    hybrid_entities_to_remove,
    retired_entities_to_remove,
)
from .evcc import BridgeConfig
from .history.geocoding import ReverseGeocoder
from .history.pricing import PricingConfig, vehicle_pricing
from .history.store import HistoryStore

from .stream import CardataStreamManager
from .coordinator import CardataCoordinator
from .debug import (
    debug_enabled,
    forget_entry as forget_entry_debug,
    mask_vin,
    set_debug_enabled,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.IMAGE,
    Platform.CALENDAR,
]


def _retain_months(options: dict) -> Optional[int]:
    """Retention in months, where 0 (or nonsense) means keep forever."""

    try:
        months = int(options.get(OPTION_HISTORY_RETAIN_MONTHS, DEFAULT_HISTORY_RETAIN_MONTHS))
    except TypeError, ValueError:
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
        known_vin: set(descriptors) for known_vin, descriptors in runtime.coordinator.data.items()
    }
    reports = runtime.coverage.reports(live)
    if vin is not None:
        reports = [report for report in reports if report.vin == vin]
    return reports


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
        issue_id = coverage_issue_id(entry_id, report.vin)
        # Clear the pre-0.9.6 id unconditionally: it embedded the raw VIN, and
        # leaving it in place would both orphan a duplicate repair and keep the
        # VIN in the issue registry that diagnostics downloads dump.
        ir.async_delete_issue(hass, DOMAIN, legacy_coverage_issue_id(entry_id, report.vin))
        overdue_clusters = report.overdue_clusters()
        if not overdue_clusters:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
            continue
        # Falls back to the masked VIN, never the raw one: a repair notification
        # is a screenshot away from a public issue (see debug.mask_vin).
        vehicle = runtime.coordinator.names.get(report.vin) or mask_vin(report.vin)
        cluster_labels = ", ".join(cluster.label for cluster in overdue_clusters)
        # A handful of concrete descriptors makes the issue actionable without
        # dumping the whole missing list into a notification body.
        silent = [d for cluster in overdue_clusters for d in cluster.missing]
        examples = ", ".join(silent[:5])
        if len(silent) > 5:
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
                "count": str(len(silent)),
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
    # The entry's containers by ``ContainerKind.key`` (container.py).
    containers: dict[str, CardataContainerManager]
    # Set to have the token-refresh loop refresh now instead of at its next turn.
    refresh_wake: asyncio.Event
    # The way into BMW's REST API (rest.py): every request takes its token from
    # ``tokens`` and its quota slot through ``rest``.
    tokens: TokenKeeper
    rest: RestGateway
    history: Optional[HistoryStore] = None
    statistics: Optional[StatisticsPublisher] = None
    coverage: Optional[CoverageStore] = None
    tyre: Optional[TyreStore] = None
    bridge: Optional[VehicleBridge] = None
    bootstrap_task: asyncio.Task | None = None
    quota_manager: "QuotaManager" | None = None
    telematic_task: asyncio.Task | None = None
    reauth_in_progress: bool = False
    reauth_flow_id: str | None = None
    last_reauth_attempt: float = 0.0
    last_refresh_attempt: float = 0.0
    reauth_pending: bool = False
    # VINs whose basic data has been fetched (or attempted) since this entry
    # loaded, so a car that BMW answers nothing for cannot spend a quota
    # request on every stream message. See _async_on_new_vehicle.
    basic_data_attempted: set[str] = field(default_factory=set)
    # Held while the container is checked and, if need be, created or replaced,
    # so two fetches at once cannot both create one.
    container_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


# Per-entry runtime lives on ``entry.runtime_data`` (HA 2026.3+), so a typed
# ConfigEntry alias gives callers ``entry.runtime_data`` with the right type and
# removes the old ``hass.data[DOMAIN][entry.entry_id]`` lookups. Process-wide
# state (registered services, the shared vehicle-image cache) still lives on
# ``hass.data[DOMAIN]``.
CardataConfigEntry = ConfigEntry[CardataRuntimeData]


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
                except TypeError, ValueError:
                    try:
                        value = datetime.fromisoformat(item.replace("Z", "+00:00")).timestamp()
                    except TypeError, ValueError:
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
    """Serve the bundled Lovelace card and make dashboards load it.

    Runs once per Home Assistant process (guarded by a module-level flag so it
    survives config-entry reloads) making the ``custom:bavariandata-card`` element
    available without the user manually adding a dashboard resource.

    The card is published as a **Lovelace resource** rather than through
    ``add_extra_js_url``. The latter renders a bare, unawaited ``import()`` into
    the index HTML, and the frontend's service worker can serve that page from
    cache without it — leaving every placed card stuck on "Configuration error"
    after a plain reload until the browser session is restarted
    (home-assistant/frontend#18728). Resources travel with the Lovelace config
    instead and are loaded before any dashboard renders.
    """

    global _FRONTEND_REGISTERED
    if _FRONTEND_REGISTERED:
        return

    card_path = os.path.join(os.path.dirname(__file__), "www", LOVELACE_CARD_FILENAME)
    if not os.path.isfile(card_path):
        _LOGGER.warning("Cardata: bundled Lovelace card missing at %s", card_path)
        return

    from homeassistant.components.http import StaticPathConfig

    # Claim before the first await. Home Assistant sets up every entry of this
    # domain concurrently, so with two accounts a second entry would otherwise
    # pass the check above while the first is still registering, add the same
    # route again and fail its setup.
    _FRONTEND_REGISTERED = True
    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(LOVELACE_CARD_URL, card_path, cache_headers=False)]
        )
    except BaseException:
        # Release the claim so reloading the entry can try again. BaseException,
        # not Exception: a setup cancelled mid-await must not leave the flag set,
        # or the card stays unserved until Home Assistant restarts.
        _FRONTEND_REGISTERED = False
        raise

    # Cache-bust on integration version so browsers pick up card updates.
    # Read the manifest off the event loop — open() is a blocking call.
    version = await hass.async_add_executor_job(_integration_version)
    url = f"{LOVELACE_CARD_URL}?v={version}"

    # ``hass.data["lovelace"]`` only exists once the lovelace integration has set
    # itself up, which is not guaranteed while our entry is starting.
    async def _publish(_hass: HomeAssistant) -> None:
        if not await _async_register_lovelace_resource(hass, url):
            _async_add_frontend_module(hass, url)

    async_at_started(hass, _publish)
    _LOGGER.debug("Cardata: serving Lovelace card at %s", url)


async def _async_register_lovelace_resource(hass: HomeAssistant, url: str) -> bool:
    """Add or refresh the card in Lovelace's resource collection.

    Returns ``True`` once the resource is registered. ``False`` means Lovelace is
    unavailable or its resources are YAML-managed (the collection is read-only
    then), so the caller should fall back to the frontend-module path.
    """

    lovelace = hass.data.get("lovelace")
    resources = getattr(lovelace, "resources", None)
    if resources is None:
        _LOGGER.debug("Cardata: lovelace resources unavailable; using frontend module")
        return False

    # HA 2026.2 split the resource mode off ``mode`` so YAML dashboards can still
    # manage resources through the UI. Prefer the newer attribute when present.
    mode = getattr(lovelace, "resource_mode", None) or getattr(lovelace, "mode", None)
    if mode != "storage":
        _LOGGER.debug("Cardata: lovelace resources are %s-managed; using frontend module", mode)
        return False

    # The collection is loaded lazily — async_items() stays empty until it is.
    if not resources.loaded:
        await resources.async_load()
        resources.loaded = True

    try:
        for item in resources.async_items():
            # Match on the path so an older ?v= from a previous version is found.
            if str(item.get("url", "")).split("?", 1)[0] != LOVELACE_CARD_URL:
                continue
            if item.get("url") != url:
                await resources.async_update_item(item["id"], {"res_type": "module", "url": url})
                _LOGGER.debug("Cardata: updated Lovelace resource to %s", url)
            return True

        await resources.async_create_item({"res_type": "module", "url": url})
    except Exception:  # noqa: BLE001 - never block setup over a dashboard resource
        _LOGGER.exception("Cardata: could not register Lovelace resource")
        return False

    _LOGGER.debug("Cardata: registered Lovelace resource %s", url)
    return True


@callback
def _async_add_frontend_module(hass: HomeAssistant, url: str) -> None:
    """Load the card as a frontend module (fallback for YAML-managed resources).

    Subject to frontend#18728 — the card may need a fresh browser session after a
    reload — but it is the only automatic option when resources are not in
    storage mode.
    """

    try:
        from homeassistant.components.frontend import add_extra_js_url
    except ImportError:  # pragma: no cover - frontend always present in practice
        _LOGGER.debug("Cardata: frontend component unavailable; card not auto-loaded")
        return

    add_extra_js_url(hass, url)
    _LOGGER.debug("Cardata: added frontend module %s", url)


def _integration_version() -> str:
    try:
        manifest_path = os.path.join(os.path.dirname(__file__), "manifest.json")
        with open(manifest_path, encoding="utf-8") as handle:
            return json.load(handle).get("version", "0")
    except OSError, ValueError:
        return "0"


# Configured through the UI only; the schema tells Home Assistant so.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the services, once for the whole Home Assistant run."""

    # Imported here: services.py reads names from this package, which is only
    # complete once this module has finished loading.
    from .services import async_setup_services  # noqa: PLC0415

    async_setup_services(hass)
    return True


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

    set_debug_enabled(debug_flag, entry_id=entry.entry_id)
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

    # The tyre diagnosis costs a REST request and is refreshed at most daily, so
    # it is kept across restarts instead of leaving the tyre sensors blank until
    # the next fetch comes due. Like history, a failure to read it is not fatal.
    tyre_store = TyreStore(hass, entry.entry_id)
    stored_tyre = await tyre_store.async_load()

    coordinator = CardataCoordinator(hass=hass, entry_id=entry.entry_id)
    coordinator.history = history_store
    coordinator.tyre_store = tyre_store
    # Seeded before the platforms load, so the tyre entities are re-created with
    # their wheels known and come up showing the last reading.
    coordinator.restore_tyre_diagnosis(stored_tyre)
    # One-time repair: legacy imports stored coordinates but no zone (see
    # HistoryStore.reresolve_zones). Now that the coordinator's zone lookup is
    # available, resolve them locally so a charge at Home stops showing "public".
    history_store.reresolve_zones(coordinator.zone_at)
    # And charges made away from home lose the house mix and tariff cost they
    # were given before #97. After the line above, so a zone it resolves counts.
    history_store.drop_house_figures_elsewhere()
    coordinator.coverage = coverage_store
    # A car added to the account after setup has no bootstrap of its own; this
    # is what gives it a name, a model and a device entry.
    coordinator.on_new_vehicle = partial(_async_on_new_vehicle, hass, entry)
    # A stream outage loses whatever the cars sent meanwhile, exactly like a
    # restart does, so a long one ends with the same catch-up.
    coordinator.on_reconnect_after_outage = partial(_async_on_reconnect, hass, entry)
    coordinator.pricing = PricingConfig.from_options(options)
    coordinator.vehicle_pricing = vehicle_pricing(options)
    # A charge that was still running when we last stopped is picked up here --
    # after the tariff is known (the restored cost is only kept if its currency
    # still matches) and before the platforms load, so the session-energy sensor
    # restores on top of the real figure instead of seeding it.
    coordinator.async_restore_open_sessions()
    # Trips: the shared HA client session drives the (opt-in) reverse geocoder;
    # the work zone drives commute classification. Both are refreshed on reload.
    coordinator.geocoder = ReverseGeocoder(
        async_get_clientsession(hass),
        enabled=bool(options.get(OPTION_TRIP_GEOCODE)),
    )
    # Per car (trip_settings.py): every car without settings of its own uses
    # the account-wide ones.
    coordinator.trip_settings = account_trip_settings(options)
    coordinator.vehicle_trip_settings = vehicle_trip_settings(options)
    # A car on end-of-trip reports may take its route from a phone.
    coordinator.watch_phone_routes()
    coordinator.trip_debug = bool(options.get(OPTION_TRIP_DEBUG))
    coordinator.diagnostic_interval = diagnostic_interval
    coordinator.mileage_allowances = mileage_allowances(options)

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
        entry.async_create_background_task(hass, statistics.async_publish(), f"{DOMAIN}_statistics")

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
    entry.async_on_unload(async_track_time_interval(hass, _evaluate_coverage, timedelta(hours=6)))
    last_poll_ts = data.get("last_telematic_poll")
    if isinstance(last_poll_ts, (int, float)) and last_poll_ts > 0:
        coordinator.last_telematic_api_at = datetime.fromtimestamp(last_poll_ts, timezone.utc)
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
    if any(key in data for key in RETIRED_ENTRY_KEYS):
        # v0.9.20-beta.6's fallback container. Its BMW side goes when the full
        # container is next replaced (``retired_names``), which the full set's
        # new signature makes happen on the first fetch.
        data = {key: value for key, value in data.items() if key not in RETIRED_ENTRY_KEYS}
        hass.config_entries.async_update_entry(entry, data=data)
    containers = {
        kind.key: CardataContainerManager(
            session=session,
            entry_id=entry.entry_id,
            initial_container_id=data.get(kind.id_key),
            claim=quota_manager.async_claim,
            kind=kind,
        )
        for kind in KINDS
    }

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
    tokens = TokenKeeper(partial(_refresh_tokens, entry, session, manager), lambda: entry.data)
    rest = RestGateway(session, tokens, quota_manager.async_claim)

    refreshed_token = False
    try:
        await tokens.renew()
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

    refresh_wake = asyncio.Event()
    refresh_task = entry.async_create_background_task(
        hass,
        _refresh_loop(hass, entry, tokens, refresh_wake),
        f"{DOMAIN}_token_refresh",
    )

    # evcc / wallbox bridge. Publishes the live state of charge -- the one thing
    # a charge controller cannot get cheaply -- onto Home Assistant's own MQTT
    # broker, so evcc or openWB can charge on our zero-quota stream instead of
    # polling a rate-limited vendor API. Off unless the user switched it on.
    bridge = VehicleBridge(hass, coordinator, config=BridgeConfig.from_options(options))

    @callback
    def _bridge_publish(vin: Any = None, _descriptor: Any = None) -> None:
        # Two callers, two signatures: the SoC signal hands us a VIN, the
        # descriptor signal a VIN and a descriptor. Defensive on the type so a
        # third caller cannot turn a stray argument into a VIN.
        bridge.async_schedule(vin if isinstance(vin, str) else None)

    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_soc_estimate, _bridge_publish)
    )
    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_update, _bridge_publish)
    )

    @callback
    def _bridge_heartbeat(_now: Any = None) -> None:
        # Re-sends everything unchanged. A parked car streams nothing for days
        # and its SoC is no less true for that, so without this a charge
        # controller's staleness check would blank it -- see bridge.py.
        entry.async_create_background_task(
            hass, bridge.async_publish_all(force=True), f"{DOMAIN}_bridge"
        )

    entry.async_on_unload(
        async_track_time_interval(hass, _bridge_heartbeat, timedelta(seconds=REPUBLISH_INTERVAL_S))
    )
    # The MQTT integration may still be starting when we are, and the stream has
    # usually delivered nothing yet either; both are settled by the time Home
    # Assistant has finished starting.
    entry.async_on_unload(async_at_started(hass, _bridge_heartbeat))
    entry.async_on_unload(bridge.async_shutdown)

    runtime_data = CardataRuntimeData(
        stream=manager,
        refresh_task=refresh_task,
        session=session,
        coordinator=coordinator,
        containers=containers,
        refresh_wake=refresh_wake,
        tokens=tokens,
        rest=rest,
        history=history_store,
        statistics=statistics,
        coverage=coverage_store,
        tyre=tyre_store,
        bridge=bridge,
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

    # Undo disabled states left behind by old defaults. Home Assistant fixes an
    # entity's enabled state when it is first registered, so a descriptor whose
    # ``enabled_default`` a later release turned on stays off on every install
    # that already had it -- the measured state of charge, off until v0.9.6, is
    # the case that prompted this. Only entries *we* disabled are touched, never
    # a user's choice (see registry_repair.py). Home Assistant answers any
    # ``disabled_by`` change with one delayed reload of the entry; that costs a
    # single stream reconnect, once, on the first start after upgrading.
    entity_registry = er.async_get(hass)
    for entity_id in entities_to_reenable(
        er.async_entries_for_config_entry(entity_registry, entry.entry_id),
        DESCRIPTOR_META,
    ):
        _LOGGER.info(
            "Enabling %s: an earlier release created it disabled by default",
            entity_id,
        )
        entity_registry.async_update_entity(entity_id, disabled_by=None)

    # Drop the battery-only sensors a petrol or diesel car was given before they
    # were gated on a high-voltage battery. They can never hold a value, and the
    # sensor platform re-creates whatever the registry lists, so without this
    # they would stay "unknown" forever. Decided on the persisted coverage record,
    # which needs a fuel system seen *and* no battery ever seen; removal (unlike
    # a ``disabled_by`` change) does not reload the entry.
    for entity_id in ev_entities_to_remove(
        er.async_entries_for_config_entry(entity_registry, entry.entry_id),
        coordinator.is_combustion_only,
    ):
        _LOGGER.info(
            "Removing %s: this car has a fuel system and no high-voltage battery",
            entity_id,
        )
        entity_registry.async_remove(entity_id)
    # Likewise the two odometer ratios a plug-in hybrid was given before they
    # were withheld there: they can never hold a value on such a car again.
    for entity_id in hybrid_entities_to_remove(
        er.async_entries_for_config_entry(entity_registry, entry.entry_id),
        coordinator.is_plug_in_hybrid,
    ):
        _LOGGER.info(
            "Removing %s: this car is a plug-in hybrid, so its consumption cannot "
            "be measured from the charging history",
            entity_id,
        )
        entity_registry.async_remove(entity_id)
    for entity_id in retired_entities_to_remove(
        er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    ):
        _LOGGER.info("Removing %s: this release no longer makes it", entity_id)
        entity_registry.async_remove(entity_id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if should_bootstrap:
        runtime_data.bootstrap_task = entry.async_create_background_task(
            hass, _async_run_bootstrap(hass, entry), f"{DOMAIN}_bootstrap"
        )

    runtime_data.telematic_task = entry.async_create_background_task(
        hass, _telematic_poll_loop(hass, entry.entry_id), f"{DOMAIN}_telematic_poll"
    )
    entry.async_create_background_task(
        hass, _async_startup_refresh(hass, entry.entry_id), f"{DOMAIN}_startup_refresh"
    )

    async def _flush_on_stop(_event) -> None:
        """Save in-flight records when Home Assistant shuts down.

        Home Assistant does *not* unload config entries on shutdown -- it fires
        this event and writes storage -- so ``async_unload_entry`` never runs on
        a restart. Without this listener an open charge or an open trip reached
        disk only if it happened to be snapshotted in the last few minutes.
        """

        await coordinator.async_flush_trips()
        coordinator.async_flush_charging()
        with suppress(Exception):
            await history_store.async_save_now()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _flush_on_stop))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: CardataConfigEntry) -> bool:
    data: CardataRuntimeData | None = getattr(entry, "runtime_data", None)
    if data is None:
        return True
    domain_data = hass.data.get(DOMAIN, {})
    domain_data.get("_entries", set()).discard(entry.entry_id)
    # An unloaded entry no longer gets a say in the debug level.
    forget_entry_debug(entry.entry_id)
    await data.coordinator.async_stop_watchdog()
    data.coordinator.stop_watching_phone_routes()
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
            ir.async_delete_issue(hass, DOMAIN, coverage_issue_id(entry.entry_id, vin))
            ir.async_delete_issue(hass, DOMAIN, legacy_coverage_issue_id(entry.entry_id, vin))
    if data.tyre:
        # Same reason as history: the save is debounced, and a fetch immediately
        # before a reload would otherwise be lost.
        with suppress(Exception):
            await data.tyre.async_save_now()
    await data.stream.async_shutdown()
    await data.session.close()
    if not domain_data.get("_entries"):
        hass.data.pop(DOMAIN, None)
    return True


@callback
def _registered_vins(hass: HomeAssistant, entry: ConfigEntry, *, exclude: set[str]) -> list[str]:
    """VINs this entry has a device for, minus ``exclude``.

    The last word on which cars an entry ever had: the coordinator is gone by
    removal time and a car that streamed without ever answering a basic-data
    request is in no stored record, but it does have a device. The debug device
    is identified by the entry id rather than a VIN, so it is skipped.
    """

    found: list[str] = []
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id):
        for domain, value in device.identifiers:
            if domain != DOMAIN or value == entry.entry_id or value in exclude:
                continue
            exclude.add(value)
            found.append(value)
    return found


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
    with suppress(Exception):
        # Likewise the tyre diagnosis -- it names the car's fitted tyres and
        # their service dates, so it goes with everything else.
        await TyreStore(hass, entry.entry_id).async_clear()
    bridge_config = BridgeConfig.from_options(dict(entry.options))
    if bridge_config.any_enabled:
        with suppress(Exception):
            # ...and anything the evcc bridge published. Retained MQTT messages
            # are held by the broker, not by us, so they are the one thing this
            # entry leaves behind that removing it would not otherwise touch --
            # and a charge controller would go on charging against a state of
            # charge frozen at the moment the integration was deleted. Only when
            # the bridge was actually on: an install that never used it has
            # nothing out there, and publishing into a broker it may not even
            # have would be noise for nothing.
            # Every VIN the entry ever had, not just the ones with stored
            # basic data: a car added after setup may have streamed (and so
            # been published) for months without ever appearing in that record,
            # and the whole point here is that nothing is left retained on the
            # broker. The coordinator is gone by now, so the entry's own data
            # plus its devices are what is left to ask.
            data = entry.data or {}
            vins = known_vins(
                stored_metadata=data.get(VEHICLE_METADATA),
                configured_vin=data.get("vin"),
            )
            vins.extend(_registered_vins(hass, entry, exclude=set(vins)))
            await async_clear_published(hass, prefix=bridge_config.prefix, vins=vins)


async def _handle_stream_error(hass: HomeAssistant, entry: CardataConfigEntry, reason: str) -> None:
    runtime: CardataRuntimeData = entry.runtime_data
    notification_id = f"{DOMAIN}_reauth_{entry.entry_id}"
    if reason == "unauthorized":
        if runtime.reauth_in_progress:
            _LOGGER.debug(
                "Ignoring duplicate unauthorized notification for entry %s", entry.entry_id
            )
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
                await runtime.tokens.renew()
                runtime.reauth_in_progress = False
                runtime.last_reauth_attempt = 0.0
                runtime.reauth_pending = False
                return
            except Exception as err:  # pylint: disable=broad-except
                if not refresh_rejected(err):
                    # The network or BMW failed, not the credentials: no reauth.
                    # Have the refresh loop retry soon, or the stream waiting for
                    # a new token would sit idle until the next regular refresh.
                    _LOGGER.warning(
                        "Token refresh after unauthorized failed for entry %s, retrying: %s",
                        entry.entry_id,
                        err,
                    )
                    runtime.refresh_wake.set()
                    return
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

        await _async_request_reauth(hass, entry, "BMW stream unauthorized")
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


async def _async_request_reauth(hass: HomeAssistant, entry: CardataConfigEntry, why: str) -> None:
    """Ask the user to authorize again, unless that is already under way."""

    runtime: CardataRuntimeData = entry.runtime_data
    now = time.time()
    if runtime.reauth_in_progress or now - runtime.last_reauth_attempt < 30:
        _LOGGER.debug(
            "Reauth already requested for entry %s; skipping new flow",
            entry.entry_id,
        )
        return

    runtime.reauth_in_progress = True
    runtime.last_reauth_attempt = now
    runtime.reauth_pending = False
    _LOGGER.error("%s; starting reauth flow", why)
    if runtime.reauth_flow_id:
        with suppress(Exception):
            await hass.config_entries.flow.async_abort(runtime.reauth_flow_id)
        runtime.reauth_flow_id = None
    persistent_notification.async_create(
        hass,
        "Authorization failed for BMW CarData. Please reauthorize the integration.",
        title="BavarianData: Connect Home Assistant to BMW CarData",
        notification_id=f"{DOMAIN}_reauth_{entry.entry_id}",
    )
    flow_result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data={**entry.data, "entry_id": entry.entry_id},
    )
    if isinstance(flow_result, dict):
        runtime.reauth_flow_id = flow_result.get("flow_id")


async def _wait_for_refresh(wake: asyncio.Event, delay: float) -> None:
    """Wait ``delay`` seconds, or less when a refresh is requested sooner."""

    with suppress(TimeoutError):
        async with asyncio.timeout(delay):
            await wake.wait()
    wake.clear()


async def _refresh_loop(
    hass: HomeAssistant,
    entry: ConfigEntry,
    tokens: TokenKeeper,
    wake: asyncio.Event,
) -> None:
    # This loop renews the ID token the stream authenticates with, and restarts a
    # stopped stream through ``async_update_credentials``. Nothing reports an
    # exception that escapes a background task, so no failed refresh may end it:
    # retry well within the token's one-hour lifetime, backing off while it fails.
    # ``wake`` lets a stream whose login was refused ask for a refresh right away.
    # It is also what keeps the access token fresh, so a REST request normally
    # needs no renewal of its own (rest.py).
    delay = DEFAULT_REFRESH_INTERVAL
    retry_delay = TOKEN_REFRESH_RETRY_DELAY
    try:
        while True:
            await _wait_for_refresh(wake, delay)
            try:
                await tokens.renew()
            except Exception as err:  # pylint: disable=broad-except
                if refresh_rejected(err):
                    # Retrying cannot fix a login BMW no longer accepts, so ask
                    # the user once and go back to the regular cadence instead
                    # of logging an error every few minutes until they do.
                    await _async_request_reauth(hass, entry, f"Token refresh rejected ({err})")
                    delay = DEFAULT_REFRESH_INTERVAL
                    retry_delay = TOKEN_REFRESH_RETRY_DELAY
                    continue
                _LOGGER.warning("Token refresh failed, retrying in %ss: %s", retry_delay, err)
                delay = retry_delay
                retry_delay = min(retry_delay * 2, TOKEN_REFRESH_RETRY_MAX)
            else:
                delay = DEFAULT_REFRESH_INTERVAL
                retry_delay = TOKEN_REFRESH_RETRY_DELAY
    except asyncio.CancelledError:
        return


async def _refresh_tokens(
    entry: ConfigEntry,
    session: aiohttp.ClientSession,
    manager: CardataStreamManager,
) -> None:
    """Renew the tokens, store them and hand the stream its new ID token.

    Called only through the entry's :class:`TokenKeeper`, which serialises
    renewals and skips them while the access token is still good.
    """

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
    await runtime.tokens.renew()


def _async_update_entry_data(hass: HomeAssistant, entry: ConfigEntry, **values: Any) -> None:
    """Write ``values`` into the entry's data, unless they are there already."""

    if all(entry.data.get(key) == value for key, value in values.items()):
        return
    hass.config_entries.async_update_entry(entry, data={**entry.data, **values})


async def _async_container_id(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: CardataRuntimeData,
    kind: ContainerKind = FULL,
) -> Optional[str]:
    """The entry's container of ``kind``, created -- or replaced -- first if need be.

    Replaced when the integration now wants a different descriptor set than
    the stored container holds (its signature), so the old one is deleted
    instead of idling against BMW's 10-container limit. ``None`` when BMW would
    not create one (already logged). Raises ``CardataAuthError`` when no token
    can be had.
    """

    manager = runtime.containers[kind.key]
    async with runtime.container_lock:
        stored_id = entry.data.get(kind.id_key)
        stored_signature = entry.data.get(kind.signature_key)
        if stored_id and stored_signature in (manager.descriptor_signature, None):
            # An entry from before signatures were stored is taken as current.
            _async_update_entry_data(
                hass, entry, **{kind.signature_key: manager.descriptor_signature}
            )
            manager.sync_from_entry(stored_id)
            return stored_id
        access_token = await runtime.tokens.access_token()
        manager.sync_from_entry(None)
        try:
            if stored_id:
                container_id = await manager.async_reset_hv_container(access_token)
            else:
                container_id = await manager.async_ensure_hv_container(access_token)
        except CardataContainerError as err:
            _LOGGER.warning(
                "Unable to set up the %s REST container for entry %s: %s",
                kind.key,
                entry.entry_id,
                err,
            )
            return None
        if not container_id:
            return None
        _async_update_entry_data(
            hass,
            entry,
            **{kind.id_key: container_id, kind.signature_key: manager.descriptor_signature},
        )
        return container_id


async def _async_run_bootstrap(hass: HomeAssistant, entry: CardataConfigEntry) -> None:
    runtime: CardataRuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime is None:
        return

    _LOGGER.debug("Starting bootstrap sequence for entry %s", entry.entry_id)

    try:
        vins = await _async_fetch_primary_vins(runtime, entry.entry_id)
    except CardataAuthError as err:
        # Not marked complete: the next start tries again.
        _LOGGER.warning("Bootstrap for entry %s has no valid token: %s", entry.entry_id, err)
        return
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

    if await _async_fetch_telematics(hass, entry, runtime, vins, label="Bootstrap", seed=True):
        _async_update_last_telematic_poll(hass, entry, time.time())
    # Whatever the telematics fetch did: the basic data is what names a car and
    # tells its drivetrain, steering side and picture, and nothing else would
    # fetch it for a car that already has a device.
    await _async_fetch_basic_data_for_vins(hass, entry, vins)
    await _async_mark_bootstrap_complete(hass, entry)


async def _async_fetch_primary_vins(runtime: CardataRuntimeData, entry_id: str) -> List[str]:
    try:
        payload = await runtime.rest.call(async_get_vehicle_mappings)
    except CardataQuotaError as err:
        _LOGGER.warning("Bootstrap mapping request skipped for entry %s: %s", entry_id, err)
        return []
    except CardataApiError as err:
        _LOGGER.warning("Bootstrap mapping request failed for entry %s: %s", entry_id, err)
        return []

    vins = primary_vins(payload)

    if not vins:
        _LOGGER.info("Bootstrap mapping for entry %s returned no primary vehicles", entry_id)
    else:
        _LOGGER.debug("Bootstrap found %s mapped vehicle(s) for entry %s", len(vins), entry_id)
    return vins


async def _async_fetch_telematics(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: CardataRuntimeData,
    vins: List[str],
    *,
    label: str = "Cardata fetch_telematic_data",
    seed: bool = False,
) -> bool:
    """Fetch each car's containers and feed them in like a stream message.

    One request per car and container (``KINDS``). Each stands alone: BMW
    refuses a whole container for one field it won't serve, so the service
    items, which it refused from 16 Oct 2026 (#142), are fetched apart and cost
    only themselves. ``seed`` skips a car that already has data (a car found by
    the bootstrap or Discover gets its entities before it streams). Returns
    whether BMW was asked at all.
    """

    coordinator = runtime.coordinator
    container_ids: Dict[str, Optional[str]] = {}

    async def container_id(kind: ContainerKind) -> Optional[str]:
        if kind.key not in container_ids:
            container_ids[kind.key] = await _async_container_id(hass, entry, runtime, kind)
        return container_ids[kind.key]

    async def fetch(vin: str) -> bool:
        """One car; whether BMW was asked."""

        asked = False
        for kind in KINDS:
            found = await container_id(kind)
            if not found:
                _LOGGER.error(
                    "%s: entry %s has no %s REST container", label, entry.entry_id, kind.key
                )
                continue
            asked = True
            try:
                payload = await runtime.rest.call(async_get_telematic_data, vin, found)
            except CardataApiError as err:
                # Neither another container nor another car pays for it.
                if kind is SERVICE:
                    _LOGGER.warning(
                        "%s: BMW did not return the service items for %s (%s); the rest "
                        "of the refresh is unaffected",
                        label,
                        mask_vin(vin),
                        err,
                    )
                else:
                    _LOGGER.error("%s: request failed for %s: %s", label, mask_vin(vin), err)
                continue
            _LOGGER.info("Fetched telematic data (%s) for %s", kind.key, mask_vin(vin))
            if debug_enabled():
                _LOGGER.debug("Cardata telematic data for %s: %s", mask_vin(vin), payload)
            telematic = None
            if isinstance(payload, dict):
                telematic = payload.get("telematicData") or payload.get("data")
            if isinstance(telematic, dict) and telematic:
                await coordinator.async_handle_message(
                    {"vin": vin, "data": telematic, "source": "rest"}
                )
        return asked

    attempted = False
    for vin in vins:
        if seed and coordinator.data.get(vin):
            continue
        try:
            attempted = await fetch(vin) or attempted
        except CardataQuotaError as err:
            # The remaining cars wait for the next round rather than each
            # logging the same exhaustion.
            _LOGGER.warning("%s skipped for %s: %s", label, mask_vin(vin), err)
            break
        except CardataAuthError as err:
            _LOGGER.error("%s: no valid token for %s: %s", label, mask_vin(vin), err)
            break

    if attempted:
        coordinator.last_telematic_api_at = datetime.now(timezone.utc)
        async_dispatcher_send(coordinator.hass, coordinator.signal_diagnostics)
    return attempted


async def _async_fetch_basic_data_for_vins(
    hass: HomeAssistant,
    entry: ConfigEntry,
    vins: List[str],
    label: str = "Bootstrap",
) -> None:
    runtime: CardataRuntimeData = entry.runtime_data
    coordinator = runtime.coordinator
    device_registry = dr.async_get(hass)

    for vin in vins:
        try:
            payload = await runtime.rest.call(async_get_basic_data, vin)
        except CardataQuotaError as err:
            _LOGGER.warning("%s basic data request skipped for %s: %s", label, mask_vin(vin), err)
            break
        except CardataAuthError as err:
            _LOGGER.warning("%s basic data for %s: no valid token: %s", label, mask_vin(vin), err)
            break
        except CardataApiError as err:
            # A warning, not debug: without basic data the car has no model
            # name, drivetrain or steering side, and nothing else says why
            # (issue #100 surfaced only as empty fields in diagnostics). The
            # error's own text names the request path, VIN included, so only
            # its status and BMW's error id are logged.
            _LOGGER.warning(
                "%s basic data request failed for %s: %s",
                label,
                mask_vin(vin),
                f"HTTP {err.status} {err.error_id or ''}".rstrip()
                if err.status
                else "network error",
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


async def _async_adopt_new_vehicle(hass: HomeAssistant, entry_id: str, vin: str) -> None:
    """Fetch the basic data of a car that has none.

    The bootstrap runs once per entry and is what turns a VIN into a named
    device -- model, software version, the name shown on the dashboard, and the
    Motorrad warning all come from that one REST call. A car added to the
    account later (a second BMW, or one enrolled in CarData after setup)
    streams perfectly well, so its entities appear, but it used to stay a bare
    VIN on the device page for good, because nothing ever revisited the
    bootstrap. The same goes for a car whose basic-data request failed then.

    Costs one request against the daily quota per car, once per Home Assistant
    run: ``basic_data_attempted`` is claimed before the call, so a car BMW has
    no basic data for cannot spend the quota over and over as it keeps
    streaming.
    """

    entry = hass.config_entries.async_get_entry(entry_id)
    runtime: CardataRuntimeData | None = getattr(entry, "runtime_data", None) if entry else None
    if entry is None or runtime is None:
        return
    if vin in (entry.data.get(VEHICLE_METADATA) or {}):
        return

    _LOGGER.info("Vehicle %s has no basic data yet; fetching it", mask_vin(vin))
    await _async_fetch_basic_data_for_vins(hass, entry, [vin], label="New vehicle")

    # A car found by Discover (rather than by its own first message) has no
    # entities yet, and a parked car may not stream for days. Seed them from one
    # telematics fetch; a car that already streamed has data and is skipped.
    await _async_fetch_telematics(hass, entry, runtime, [vin], label="New vehicle", seed=True)


def _vins_without_basic_data(
    hass: HomeAssistant, entry: ConfigEntry, runtime: CardataRuntimeData
) -> list[str]:
    """Every car this entry has, by its data or its devices, that has no basic data."""

    vins = known_vins(
        stored_metadata=entry.data.get(VEHICLE_METADATA),
        coordinator_data=runtime.coordinator.data,
        configured_vin=entry.data.get("vin"),
    )
    vins.extend(_registered_vins(hass, entry, exclude=set(vins)))
    have = entry.data.get(VEHICLE_METADATA) or {}
    return [vin for vin in vins if vin not in have]


@callback
def _async_on_reconnect(hass: HomeAssistant, entry: CardataConfigEntry, down_s: float) -> None:
    """Schedule a catch-up when the stream comes back from a long outage.

    Called from the connection-event path, so it only schedules; the rules that
    decide whether a request is actually made live in ``_async_catch_up``.
    """

    if not outage_needs_catch_up(down_s):
        return
    _LOGGER.info("BMW stream back after %d minutes offline", round(down_s / 60))
    entry.async_create_background_task(
        hass,
        _async_catch_up(hass, entry.entry_id, "after a stream outage"),
        f"{DOMAIN}_outage_catch_up",
    )


def _async_on_new_vehicle(hass: HomeAssistant, entry: CardataConfigEntry, vin: str) -> None:
    """Schedule :func:`_async_adopt_new_vehicle`, at most once per car per run.

    Called from the message path, so it does nothing that can block or raise.
    Skipped while the bootstrap is still to run: that covers every mapped
    vehicle already, and both racing would spend two quota requests on one car.
    """

    runtime: CardataRuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime is None or not entry.data.get(BOOTSTRAP_COMPLETE):
        return
    if vin in runtime.basic_data_attempted or vin in (entry.data.get(VEHICLE_METADATA) or {}):
        return
    runtime.basic_data_attempted.add(vin)
    entry.async_create_background_task(
        hass,
        _async_adopt_new_vehicle(hass, entry.entry_id, vin),
        # Masked: task names surface in Home Assistant's own debug output.
        f"{DOMAIN}_adopt_{mask_vin(vin)}",
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
    # One account can hold several cars and the container endpoint is per VIN,
    # so a refresh means one request *per vehicle*. Picking a single VIN here
    # left every other car's non-streamable fields frozen at their setup value
    # for good -- issue #13. An explicit vin still targets just that car.
    if vin_override:
        vins = [vin_override]
    else:
        vins = known_vins(
            stored_metadata=entry.data.get(VEHICLE_METADATA),
            coordinator_data=runtime.coordinator.data,
            configured_vin=entry.data.get("vin"),
        )
    if not vins:
        _LOGGER.error("Cardata fetch_telematic_data: no VIN available; provide vin parameter")
        return False
    return await _async_fetch_telematics(hass, entry, runtime, vins)


def _async_update_last_telematic_poll(
    hass: HomeAssistant, entry: ConfigEntry, timestamp: float
) -> None:
    existing = entry.data.get("last_telematic_poll")
    if existing and abs(existing - timestamp) < 1:
        return
    updated = dict(entry.data)
    updated["last_telematic_poll"] = timestamp
    hass.config_entries.async_update_entry(entry, data=updated)


async def _async_perform_tyre_fetch(
    hass: HomeAssistant, entry: ConfigEntry, runtime: CardataRuntimeData
) -> bool:
    """Refresh the smart-maintenance tyre diagnosis for every vehicle.

    Its own endpoint, so its own request per car against the daily quota -- it
    cannot be folded into the container call. Like that call, it used to serve a
    single VIN, which left a second car's tread wear frozen forever (issue #13).
    """

    vins = known_vins(
        stored_metadata=entry.data.get(VEHICLE_METADATA),
        coordinator_data=runtime.coordinator.data,
        configured_vin=entry.data.get("vin"),
    )
    refreshed = False
    for vin in vins:
        try:
            payload = await runtime.rest.call(async_get_tyre_diagnosis, vin)
        except CardataQuotaError as err:
            _LOGGER.warning("Daily tyre diagnosis skipped for %s: %s", mask_vin(vin), err)
            break
        except CardataAuthError as err:
            _LOGGER.warning("Daily tyre diagnosis: no valid token: %s", err)
            break
        except CardataApiError as err:
            # Not every vehicle has tyre service data; a failure here must not
            # stop the other cars, nor the container refresh sharing this loop.
            _LOGGER.debug("Daily tyre diagnosis failed for %s: %s", mask_vin(vin), err)
            continue

        parsed = runtime.coordinator.apply_tyre_diagnosis(vin, payload)
        _LOGGER.debug(
            "Daily tyre diagnosis for %s: %s wheel(s)",
            mask_vin(vin),
            len(parsed.get("wheels") or {}),
        )
        refreshed = True

    return refreshed


async def _async_startup_refresh(hass: HomeAssistant, entry_id: str) -> None:
    """Catch up after a start, once the stream has had its head start."""

    try:
        await asyncio.sleep(STARTUP_REFRESH_DELAY_S)
    except asyncio.CancelledError:
        return
    entry = hass.config_entries.async_get_entry(entry_id)
    runtime: CardataRuntimeData | None = getattr(entry, "runtime_data", None) if entry else None
    if entry is not None and runtime is not None:
        # A car whose basic data never arrived (a failed request at setup, say)
        # would otherwise stay unnamed for good: nothing else asks again once
        # it has a device. One request per such car, once per start.
        for vin in _vins_without_basic_data(hass, entry, runtime):
            _async_on_new_vehicle(hass, entry, vin)
    await _async_catch_up(hass, entry_id, "after startup")


async def _async_catch_up(hass: HomeAssistant, entry_id: str, occasion: str) -> None:
    """Fetch what the cars reported while nobody was listening.

    Runs after a start and after a stream outage. One container fetch per car,
    subject to the rules in ``startup_refresh.py`` (skipped when BMW was asked
    within the hour, charges a restart left unconfirmed first, never into the
    last of the quota). The answers go through the ordinary message path, so a
    CHARGINGACTIVE resumes a restored session and a NOCHARGING closes it --
    before its grace timer would have filed it as ended by the restart.
    """

    entry = hass.config_entries.async_get_entry(entry_id)
    runtime: CardataRuntimeData | None = getattr(entry, "runtime_data", None) if entry else None
    if entry is None or runtime is None:
        return
    # The daily refresh counts as asking BMW too: right after it ran, a catch-up
    # would fetch the same snapshot again.
    stamps = [
        value
        for value in (entry.data.get(LAST_STARTUP_REFRESH), entry.data.get("last_telematic_poll"))
        if isinstance(value, (int, float))
    ]
    now = time.time()
    coordinator = runtime.coordinator
    vins = startup_refresh_vins(
        enabled=bool(entry.options.get(OPTION_REFRESH_ON_START, DEFAULT_REFRESH_ON_START)),
        vins=known_vins(
            stored_metadata=entry.data.get(VEHICLE_METADATA),
            coordinator_data=coordinator.data,
            configured_vin=entry.data.get("vin"),
        ),
        unconfirmed=coordinator.unconfirmed_restored_charges(),
        last_fetch_at=max(stamps) if stamps else None,
        now=now,
        remaining=runtime.quota_manager.remaining if runtime.quota_manager else None,
    )
    if not vins:
        return
    # Stamped before the requests, so a restart loop that crashes mid-fetch is
    # still spaced out.
    updated = dict(entry.data)
    updated[LAST_STARTUP_REFRESH] = now
    hass.config_entries.async_update_entry(entry, data=updated)
    _LOGGER.info(
        "Catching up %s: fetching the current state of %d vehicle(s) "
        "(one request each of the daily quota)",
        occasion,
        len(vins),
    )
    for vin in vins:
        await _async_perform_telematic_fetch(hass, entry, runtime, vin_override=vin)


async def _telematic_poll_loop(hass: HomeAssistant, entry_id: str) -> None:
    """Daily REST refresh of everything BMW cannot stream.

    Two requests per run: one container call covering every non-streamable
    descriptor at once, plus the tyre diagnosis on its own endpoint. The image
    and basic data are deliberately not here -- they do not change, so they stay
    on the setup path and the manual services.
    """

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
            await _async_perform_tyre_fetch(hass, entry, runtime)
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
