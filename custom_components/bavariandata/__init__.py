"""BMW CarData integration for Home Assistant."""

from __future__ import annotations

import asyncio
import inspect
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
from homeassistant.config_entries import ConfigEntry, ConfigEntryState, SOURCE_REAUTH
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError

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
    DOMAIN,
    MQTT_KEEPALIVE,
    BOOTSTRAP_COMPLETE,
    REQUEST_LOG,
    REQUEST_LOG_VERSION,
    REQUEST_LIMIT,
    REQUEST_WINDOW_SECONDS,
    TELEMATIC_POLL_INTERVAL,
    VEHICLE_METADATA,
    OPTION_MQTT_KEEPALIVE,
    OPTION_STATISTICS_IMPORT,
    DEFAULT_STATISTICS_IMPORT,
    OPTION_CHARGE_LOCATIONS,
    LAST_STARTUP_REFRESH,
    OPTION_STREAM_SECTIONS,
    LOVELACE_CARD_FILENAME,
    LOVELACE_CARD_URL,
)
from .device_flow import CardataAuthError, refresh_tokens
from .container import (
    KINDS,
    CardataContainerManager,
)
from .rest import CardataQuotaError, RestGateway, TokenKeeper
from .coverage import coverage_issue_id, legacy_coverage_issue_id
from .coverage_store import CoverageStore

from .startup_refresh import (
    STARTUP_REFRESH_DELAY_S,
    last_fetch_at,
    outage_needs_catch_up,
)
from .vehicle_options import without_vehicle
from .distance import apply_sensor_unit, async_apply_sensor_units
from .vehicle_support import motorcycle_issue_id
from .tyre_store import TyreStore
from .vehicles import (
    forget_in_entry_data,
    known_vins,
    left_account_issue_id,
    new_account_title,
)
from .history.backfill import StatisticsPublisher
from .bridge import REPUBLISH_INTERVAL_S, VehicleBridge, async_clear_published
from .descriptor_metadata import DESCRIPTOR_META
from .evcc import BridgeConfig
from .history.store import HistoryStore

from . import entry_setup
from .reauth import ReauthGate, refresh_loop, renewed_entry_data
from .account_rest import (
    LAST_TELEMATIC_POLL,
    AccountRest,
    FetchFailed,
    seconds_until_poll,
)
from .stream import CardataStreamManager
from .coordinator import CardataCoordinator
from .debug import (
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
    reports = runtime.coverage.reports(
        live,
        drive_trains={
            known_vin: runtime.coordinator.drive_train(known_vin)
            for known_vin in set(live) | set(runtime.coordinator.device_metadata)
        },
    )
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
    # Whether a reauth flow is open, and when we last tried (reauth.py).
    reauth: ReauthGate = field(default_factory=ReauthGate)
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

    def _account_name(self) -> str:
        entry = self._hass.config_entries.async_get_entry(self._entry_id)
        return getattr(entry, "title", None) or "BavarianData"

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
                        "account": self._account_name(),
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
    from .assist_api import async_register_llm_api  # noqa: PLC0415
    from .services import async_setup_services  # noqa: PLC0415

    async_setup_services(hass)
    async_register_llm_api(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CardataConfigEntry) -> bool:
    """Set an account up: stores, coordinator, stream, then Home Assistant's side.

    What each step decides lives in ``entry_setup.py``; this function builds the
    parts in their order and makes the Home Assistant calls.
    """

    domain_data = hass.data.setdefault(DOMAIN, {})

    _LOGGER.debug("Setting up BMW CarData entry %s", entry.entry_id)

    await _async_register_frontend_card(hass)

    # Home Assistant's shared session (Gold: inject-websession): it outlives the
    # entry, so nothing here closes it.
    session = async_get_clientsession(hass)

    data = entry.data
    options = dict(entry.options) if entry.options else {}
    set_debug_enabled(entry_setup.debug_flag(options), entry_id=entry.entry_id)
    should_bootstrap = not data.get(BOOTSTRAP_COMPLETE)
    client_id = data["client_id"]
    gcid = data.get("gcid")
    id_token = data.get("id_token")
    if not gcid or not id_token:
        raise ConfigEntryNotReady("Missing GCID or ID token")

    # History is a bonus, not a prerequisite: a failure to read it must never
    # stop the stream from setting up, so HistoryStore.async_load swallows its
    # own errors and starts empty.
    history_store = HistoryStore(
        hass,
        entry.entry_id,
        retain_months=entry_setup.retain_months(options),
        remember_locations=bool(options.get(OPTION_CHARGE_LOCATIONS)),
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
    entry_setup.repair_history(history_store, coordinator.zone_at)
    coordinator.coverage = coverage_store
    # A car added to the account after setup has no bootstrap of its own; this
    # is what gives it a name, a model and a device entry.
    coordinator.on_new_vehicle = partial(_async_on_new_vehicle, hass, entry)
    # A stream outage loses whatever the cars sent meanwhile, exactly like a
    # restart does, so a long one ends with the same catch-up.
    coordinator.on_reconnect_after_outage = partial(_async_on_reconnect, hass, entry)
    entry_setup.configure(coordinator, options, session)
    # A charge that was still running when we last stopped is picked up here --
    # after the tariff is known and before the platforms load, so the
    # session-energy sensor restores on top of the real figure instead of
    # seeding it.
    coordinator.async_restore_open_sessions()
    # A car on end-of-trip reports may take its route from a phone.
    coordinator.watch_phone_routes()

    statistics = _async_wire_statistics(hass, entry, coordinator, history_store, options)
    _async_wire_coverage(hass, entry)
    last_fetch_ts = last_fetch_at(data.get(LAST_TELEMATIC_POLL), data.get(LAST_STARTUP_REFRESH))
    if last_fetch_ts is not None:
        coordinator.last_telematic_api_at = datetime.fromtimestamp(last_fetch_ts, timezone.utc)
    _async_restore_devices(hass, entry, coordinator)
    _async_wire_retitle(hass, entry, coordinator)

    quota_manager = await QuotaManager.async_create(hass, entry.entry_id)
    cleaned = entry_setup.without_retired_keys(data)
    if cleaned is not None:
        data = cleaned
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
        keepalive=options.get(OPTION_MQTT_KEEPALIVE, MQTT_KEEPALIVE),
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
        raise ConfigEntryNotReady(f"Initial token refresh failed: {err}") from err

    if manager.client is None:
        try:
            await manager.async_start()
        except Exception as err:
            if refreshed_token:
                raise ConfigEntryNotReady(
                    f"Unable to connect to BMW MQTT after token refresh: {err}"
                ) from err
            raise ConfigEntryNotReady(f"Unable to connect to BMW MQTT: {err}") from err

    refresh_wake = asyncio.Event()
    refresh_task = entry.async_create_background_task(
        hass,
        refresh_loop(tokens, refresh_wake, partial(_async_request_reauth, hass, entry)),
        f"{DOMAIN}_token_refresh",
    )

    bridge = _async_wire_bridge(hass, entry, coordinator, options)

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
    )
    entry.runtime_data = runtime_data
    # Track loaded entries so services (registered process-wide) are torn down
    # once the last entry unloads. Runtime itself lives on entry.runtime_data.
    domain_data.setdefault("_entries", set()).add(entry.entry_id)

    await coordinator.async_handle_connection_event("connecting")
    await coordinator.async_start_watchdog()

    entity_registry = _async_repair_registry(hass, entry, coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _async_wire_units(hass, entry, coordinator, entity_registry)

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

    _async_wire_stop_flush(hass, entry, coordinator, history_store)

    return True


def _async_wire_statistics(
    hass: HomeAssistant,
    entry: CardataConfigEntry,
    coordinator: CardataCoordinator,
    history_store: HistoryStore,
    options: dict,
) -> StatisticsPublisher:
    """Publish the recorded history into Home Assistant's long-term statistics.

    Under our own "bavariandata:" namespace, so charging from before the
    install -- or from while HA was down -- still lands on the Energy
    dashboard. See history/backfill.py.
    """

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
    return statistics


def _async_wire_coverage(hass: HomeAssistant, entry: CardataConfigEntry) -> None:
    """Judge the descriptor coverage shortly after start, then every six hours."""

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


@callback
def _async_restore_devices(
    hass: HomeAssistant, entry: CardataConfigEntry, coordinator: CardataCoordinator
) -> None:
    """Bring every stored car back as a device, without a REST request."""

    device_registry = dr.async_get(hass)
    for vin, fields in entry_setup.restore_metadata(
        coordinator, entry.data.get(VEHICLE_METADATA, {})
    ):
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, vin)}, **fields
        )


@callback
def _async_wire_retitle(
    hass: HomeAssistant, entry: CardataConfigEntry, coordinator: CardataCoordinator
) -> None:
    """Name the entry after its cars, now and whenever BMW names a car anew.

    That is what tells two accounts apart in every list, repair and reauth card.
    """

    _async_retitle(hass, entry, coordinator)

    @callback
    def _retitle_on_name(_vin: str, _name: str) -> None:
        # A callback, not a lambda: the dispatcher runs anything else on a
        # worker thread, where updating the entry raises.
        _async_retitle(hass, entry, coordinator)

    entry.async_on_unload(
        async_dispatcher_connect(hass, f"{DOMAIN}_{entry.entry_id}_name", _retitle_on_name)
    )


def _async_wire_bridge(
    hass: HomeAssistant,
    entry: CardataConfigEntry,
    coordinator: CardataCoordinator,
    options: dict,
) -> VehicleBridge:
    """The evcc / wallbox bridge, publishing on every SoC or descriptor change.

    Publishes the live state of charge -- the one thing a charge controller
    cannot get cheaply -- onto Home Assistant's own MQTT broker, so evcc or
    openWB can charge on our zero-quota stream instead of polling a
    rate-limited vendor API. Off unless the user switched it on.
    """

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
    return bridge


@callback
def _async_repair_registry(
    hass: HomeAssistant, entry: CardataConfigEntry, coordinator: CardataCoordinator
) -> er.EntityRegistry:
    """Make the registry changes ``entry_setup.registry_fixes`` names.

    Before the platforms load: the sensor platform re-creates whatever the
    registry lists, so an entity left there would come back empty.
    """

    entity_registry = er.async_get(hass)
    for fix in entry_setup.registry_fixes(
        er.async_entries_for_config_entry(entity_registry, entry.entry_id),
        DESCRIPTOR_META,
        combustion_only=coordinator.is_combustion_only,
        plug_in_hybrid=coordinator.is_plug_in_hybrid,
    ):
        if fix.remove:
            _LOGGER.info("Removing %s: %s", fix.entity_id, fix.reason)
            entity_registry.async_remove(fix.entity_id)
        else:
            _LOGGER.info("Enabling %s: %s", fix.entity_id, fix.reason)
            entity_registry.async_update_entity(fix.entity_id, disabled_by=None)
    return entity_registry


@callback
def _async_wire_units(
    hass: HomeAssistant,
    entry: CardataConfigEntry,
    coordinator: CardataCoordinator,
    entity_registry: er.EntityRegistry,
) -> None:
    """Configure -> Units: show the sensors in the units the account chose.

    A sensor is also created later, on its descriptor's first arrival, so a new
    registry entry gets the same treatment.
    """

    async_apply_sensor_units(hass, entry.entry_id, coordinator)

    @callback
    def _units_for_new_entity(event) -> None:
        if event.data.get("action") != "create":
            return
        created = entity_registry.async_get(event.data["entity_id"])
        if created is not None and created.config_entry_id == entry.entry_id:
            apply_sensor_unit(entity_registry, created, coordinator)

    entry.async_on_unload(
        hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _units_for_new_entity)
    )


@callback
def _async_wire_stop_flush(
    hass: HomeAssistant,
    entry: CardataConfigEntry,
    coordinator: CardataCoordinator,
    history_store: HistoryStore,
) -> None:
    """Save in-flight records when Home Assistant shuts down.

    Home Assistant does *not* unload config entries on shutdown -- it fires
    the stop event and writes storage -- so ``async_unload_entry`` never runs
    on a restart. Without this listener an open charge or an open trip reached
    disk only if it happened to be snapshotted in the last few minutes.
    """

    async def _flush_on_stop(_event) -> None:
        await coordinator.async_flush_trips()
        coordinator.async_flush_charging()
        with suppress(Exception):
            await history_store.async_save_now()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _flush_on_stop))


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


@callback
def account_vins(hass: HomeAssistant, entry: ConfigEntry, coordinator: Any = None) -> list[str]:
    """Every car of the account: stored, streamed, configured, or only a device.

    The one answer to "which cars does this account have", so a service that
    refuses to guess between two cars and a fetch that serves each car count
    the same cars. The registry counts because a car that has sent nothing
    since the restart is still the account's car; ``coordinator`` is ``None``
    once the entry is unloaded.
    """

    vins = known_vins(
        stored_metadata=entry.data.get(VEHICLE_METADATA),
        coordinator_data=None if coordinator is None else coordinator.data,
        configured_vin=entry.data.get("vin"),
    )
    vins.extend(_registered_vins(hass, entry, exclude=set(vins)))
    return vins


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
            vins = account_vins(hass, entry)
            await async_clear_published(hass, prefix=bridge_config.prefix, vins=vins)
    with suppress(Exception):
        # The pictures live in stores every account shares, keyed by VIN, so
        # only these cars' go, the same list of cars as above.
        from .image import async_forget_vehicles  # noqa: PLC0415 - pulls in HA's image component

        await async_forget_vehicles(hass, account_vins(hass, entry))


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Let a car's device be deleted (a car sold or given up), taking its data along.

    The account's own diagnostics device stays: its identifier is the entry id,
    and the account needs it as long as the entry exists. A car still on the
    BMW account comes back with its next stream message, starting afresh.
    """

    vins = [
        value
        for domain, value in device_entry.identifiers
        if domain == DOMAIN and value != config_entry.entry_id
    ]
    if not vins:
        return False
    hass.async_create_background_task(
        _async_forget_vehicles(hass, config_entry.entry_id, vins), f"{DOMAIN}_forget_vehicle"
    )
    return True


# One cleanup at a time per entry. Two cars deleted seconds apart each start
# one, and the second, finding the entry mid-unload, would open the same stores
# alongside the first: whichever saved last brought the other car's data back.
# Module-level, not in ``hass.data``: unloading the last entry empties that.
_FORGET_LOCKS: Dict[str, asyncio.Lock] = {}


async def _async_forget_vehicles(hass: HomeAssistant, entry_id: str, vins: list[str]) -> None:
    """:func:`_async_forget_vehicles_now`, after any cleanup already under way."""

    async with _FORGET_LOCKS.setdefault(entry_id, asyncio.Lock()):
        await _async_forget_vehicles_now(hass, entry_id, vins)


async def _async_forget_vehicles_now(hass: HomeAssistant, entry_id: str, vins: list[str]) -> None:
    """Delete everything kept for these cars, the way removing the entry does for all.

    The entry is unloaded first: its coordinator and stores hold the cars in
    memory and would write them straight back on the next save. Each store is
    then opened with the entry's own settings (a default retention would prune
    the other cars' history on load), and the entry set up again.
    """

    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        return
    reload = entry.state is ConfigEntryState.LOADED
    if reload and not await hass.config_entries.async_unload(entry_id):
        _LOGGER.warning("Could not unload entry %s to delete a car's data", entry_id)
        return
    with suppress(Exception):
        history = HistoryStore(
            hass, entry_id, retain_months=entry_setup.retain_months(dict(entry.options))
        )
        await history.async_load()
        publisher = StatisticsPublisher(hass, history, name_for=lambda vin: vin)
        for vin in vins:
            history.forget_vehicle(vin)
            await publisher.async_remove_vehicle(vin)
        await history.async_save_now()
    with suppress(Exception):
        coverage = CoverageStore(
            hass, entry_id, sections=entry.data.get(OPTION_STREAM_SECTIONS) or []
        )
        await coverage.async_load()
        for vin in vins:
            coverage.forget_vehicle(vin)
        await coverage.async_save_now()
    with suppress(Exception):
        tyre = TyreStore(hass, entry_id)
        await tyre.async_load()
        for vin in vins:
            tyre.forget_vehicle(vin)
        await tyre.async_save_now()
    bridge_config = BridgeConfig.from_options(dict(entry.options))
    if bridge_config.any_enabled:
        with suppress(Exception):
            await async_clear_published(hass, prefix=bridge_config.prefix, vins=vins)
    with suppress(Exception):
        from .image import async_forget_vehicles  # noqa: PLC0415 - pulls in HA's image component

        await async_forget_vehicles(hass, vins)
    data: dict = dict(entry.data)
    options: dict = dict(entry.options)
    for vin in vins:
        data = forget_in_entry_data(data, vin, VEHICLE_METADATA)
        options = without_vehicle(options, vin)
        for issue_id in (
            coverage_issue_id(entry_id, vin),
            legacy_coverage_issue_id(entry_id, vin),
            motorcycle_issue_id(entry_id, vin),
            left_account_issue_id(entry_id, vin),
        ):
            ir.async_delete_issue(hass, DOMAIN, issue_id)
    hass.config_entries.async_update_entry(entry, data=data, options=options)
    _LOGGER.info("Deleted the stored data of %s car(s) on entry %s", len(vins), entry_id)
    if reload:
        await hass.config_entries.async_setup(entry_id)


class _ReauthHost:
    """``reauth.ReauthHost`` over a loaded entry."""

    def __init__(self, hass: HomeAssistant, entry: CardataConfigEntry) -> None:
        runtime: CardataRuntimeData = entry.runtime_data
        self._hass = hass
        self._entry = entry
        self.entry_id = entry.entry_id
        self.tokens = runtime.tokens
        self.refresh_wake = runtime.refresh_wake

    async def start_reauth(self) -> Optional[str]:
        result = await self._hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_REAUTH, "entry_id": self.entry_id},
            data={**self._entry.data, "entry_id": self.entry_id},
        )
        return result.get("flow_id") if isinstance(result, dict) else None

    async def abort_flow(self, flow_id: str) -> None:
        # A callback in Home Assistant, not a coroutine: awaited only if a
        # release ever makes it one.
        result = self._hass.config_entries.flow.async_abort(flow_id)
        if inspect.isawaitable(result):
            await result

    def dismiss_notice(self) -> None:
        persistent_notification.async_dismiss(self._hass, f"{DOMAIN}_reauth_{self.entry_id}")


async def _handle_stream_error(hass: HomeAssistant, entry: CardataConfigEntry, reason: str) -> None:
    await entry.runtime_data.reauth.stream_error(_ReauthHost(hass, entry), reason)


async def _async_request_reauth(hass: HomeAssistant, entry: CardataConfigEntry, why: str) -> None:
    """Ask the user to authorize again, unless that is already under way."""

    await entry.runtime_data.reauth.request(_ReauthHost(hass, entry), why)


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

    token_data = await refresh_tokens(
        session,
        client_id=client_id,
        refresh_token=refresh_token,
        scope=data.get("scope") or DEFAULT_SCOPE,
    )
    try:
        data = renewed_entry_data(data, token_data, time.time())
    except KeyError as err:
        raise CardataAuthError("Token refresh response did not include id_token") from err
    hass.config_entries.async_update_entry(entry, data=data)
    await manager.async_update_credentials(gcid=data.get("gcid"), id_token=data["id_token"])
    runtime = getattr(entry, "runtime_data", None)
    if runtime:
        runtime.reauth.renewed()


async def async_manual_refresh_tokens(hass: HomeAssistant, entry: CardataConfigEntry) -> None:
    runtime: CardataRuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime is None:
        raise CardataAuthError("Integration runtime not ready")
    await runtime.tokens.renew()


class _EntryHost:
    """``account_rest.AccountHost`` over a loaded entry."""

    def __init__(self, hass: HomeAssistant, entry: CardataConfigEntry) -> None:
        runtime: CardataRuntimeData = entry.runtime_data
        self._hass = hass
        self._entry = entry
        self._quota = runtime.quota_manager
        self.entry_id = entry.entry_id
        self.coordinator = runtime.coordinator
        self.rest = runtime.rest
        self.tokens = runtime.tokens
        self.containers = runtime.containers
        self.container_lock = runtime.container_lock

    @property
    def data(self) -> Any:
        return self._entry.data

    @property
    def options(self) -> Any:
        return self._entry.options

    @property
    def quota_remaining(self) -> Optional[int]:
        return self._quota.remaining if self._quota else None

    def update_data(self, **values: Any) -> None:
        self._hass.config_entries.async_update_entry(
            self._entry, data={**self._entry.data, **values}
        )

    def register_device(self, vin: str, fields: Any) -> None:
        dr.async_get(self._hass).async_get_or_create(
            config_entry_id=self.entry_id, identifiers={(DOMAIN, vin)}, **fields
        )

    def account_vins(self) -> list[str]:
        return account_vins(self._hass, self._entry, self.coordinator)

    def diagnostics_changed(self) -> None:
        async_dispatcher_send(self._hass, self.coordinator.signal_diagnostics)


def _account(hass: HomeAssistant, entry: CardataConfigEntry) -> AccountRest:
    """The REST side of a loaded entry (account_rest.py)."""

    return AccountRest(_EntryHost(hass, entry))


def _loaded(hass: HomeAssistant, entry_id: str) -> Optional[CardataConfigEntry]:
    """The entry, if it exists and is set up."""

    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or getattr(entry, "runtime_data", None) is None:
        return None
    return entry


async def _async_run_bootstrap(hass: HomeAssistant, entry: CardataConfigEntry) -> None:
    if getattr(entry, "runtime_data", None) is not None:
        await _account(hass, entry).bootstrap()


async def _async_adopt_new_vehicle(hass: HomeAssistant, entry_id: str, vin: str) -> None:
    """:meth:`AccountRest.adopt`, if the entry is still loaded.

    Costs one request against the daily quota per car, once per Home Assistant
    run: ``basic_data_attempted`` is claimed before the call, so a car BMW has
    no basic data for cannot spend the quota over and over as it keeps
    streaming.
    """

    entry = _loaded(hass, entry_id)
    if entry is not None:
        await _account(hass, entry).adopt(vin)


@callback
def _async_on_reconnect(hass: HomeAssistant, entry: CardataConfigEntry, down_s: float) -> None:
    """Schedule a catch-up when the stream comes back from a long outage.

    Called from the connection-event path, so it only schedules; the rules that
    decide whether a request is actually made live in ``AccountRest.catch_up``.
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


async def _async_perform_telematic_fetch(
    hass: HomeAssistant,
    entry: CardataConfigEntry,
    runtime: CardataRuntimeData,
    *,
    vin_override: Optional[str] = None,
    strict: bool = False,
) -> bool:
    """:meth:`AccountRest.refresh`; ``strict`` raises what it could not fetch."""

    try:
        return await _account(hass, entry).refresh(vin=vin_override, strict=strict)
    except FetchFailed as err:
        raise HomeAssistantError(str(err)) from err


def _async_update_last_telematic_poll(
    hass: HomeAssistant, entry: CardataConfigEntry, timestamp: float
) -> None:
    _account(hass, entry).update_last_telematic_poll(timestamp)


async def _async_startup_refresh(hass: HomeAssistant, entry_id: str) -> None:
    """Catch up after a start, once the stream has had its head start."""

    try:
        await asyncio.sleep(STARTUP_REFRESH_DELAY_S)
    except asyncio.CancelledError:
        return
    entry = _loaded(hass, entry_id)
    if entry is not None:
        # A car whose basic data never arrived (a failed request at setup, say)
        # would otherwise stay unnamed for good: nothing else asks again once
        # it has a device. One request per such car, once per start.
        for vin in _account(hass, entry).vins_without_basic_data():
            _async_on_new_vehicle(hass, entry, vin)
    await _async_catch_up(hass, entry_id, "after startup")


async def _async_catch_up(hass: HomeAssistant, entry_id: str, occasion: str) -> None:
    """:meth:`AccountRest.catch_up`, if the entry is still loaded."""

    entry = _loaded(hass, entry_id)
    if entry is not None:
        await _account(hass, entry).catch_up(occasion)


async def _telematic_poll_loop(hass: HomeAssistant, entry_id: str) -> None:
    """Daily REST refresh of everything BMW cannot stream.

    Two requests per car and run: the containers, covering every
    non-streamable descriptor, plus the tyre diagnosis on its own endpoint. The
    image and basic data are deliberately not here -- they do not change, so
    they stay on the setup path and the manual services.
    """

    try:
        while True:
            entry = _loaded(hass, entry_id)
            if entry is None:
                return
            wait = seconds_until_poll(entry.data.get(LAST_TELEMATIC_POLL), time.time())
            if wait > 0:
                await asyncio.sleep(wait)
                continue
            await _account(hass, entry).daily_refresh()
            await asyncio.sleep(TELEMATIC_POLL_INTERVAL)
    except asyncio.CancelledError:
        return


# The title we last gave the entry, so a later rename by the user is told apart
# from ours and left alone (``vehicles.new_account_title``).
_AUTO_TITLE = "auto_title"


@callback
def _async_retitle(hass: HomeAssistant, entry: ConfigEntry, coordinator: Any) -> None:
    """Title the entry with its cars' names, unless the user has named it."""

    vins = account_vins(hass, entry, coordinator)
    # A car without basic data is "named" by its VIN; that never goes in a title.
    names = [coordinator.names.get(vin) for vin in vins if coordinator.names.get(vin) != vin]
    title = new_account_title(entry.title, entry.data.get(_AUTO_TITLE), names)
    if title is not None:
        hass.config_entries.async_update_entry(
            entry, title=title, data={**entry.data, _AUTO_TITLE: title}
        )


def _async_store_vehicle_metadata(
    hass: HomeAssistant,
    entry: CardataConfigEntry,
    vin: str,
    payload: Dict[str, Any],
) -> None:
    _account(hass, entry).store_vehicle_metadata(vin, payload)
