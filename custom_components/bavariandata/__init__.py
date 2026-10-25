"""BMW CarData integration for Home Assistant."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Any, Mapping

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
)
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started

from . import entry_setup
from .account import (
    async_on_new_vehicle,
    async_on_reconnect,
    async_quota_manager,
    async_request_reauth,
    async_retitle,
    async_run_bootstrap,
    async_startup_refresh,
    async_handle_stream_error,
    login_refused,
    async_renew_tokens,
    telematic_poll_loop,
    account_vins,
)
from .account_rest import (
    LAST_TELEMATIC_POLL,
)
from .bridge import REPUBLISH_INTERVAL_S, VehicleBridge, async_clear_published
from .const import (
    BOOTSTRAP_COMPLETE,
    DEFAULT_STATISTICS_IMPORT,
    DEFAULT_STREAM_HOST,
    DEFAULT_STREAM_PORT,
    DOMAIN,
    LAST_STARTUP_REFRESH,
    MQTT_KEEPALIVE,
    OPTION_CHARGE_LOCATIONS,
    OPTION_MQTT_KEEPALIVE,
    OPTION_STATISTICS_IMPORT,
    OPTION_STREAM_SECTIONS,
    VEHICLE_METADATA,
)
from .container import (
    KINDS,
    CardataContainerManager,
)
from .coordinator import CardataCoordinator
from .coverage import coverage_issue_id, legacy_coverage_issue_id
from .coverage_issues import refresh_coverage_issues
from .coverage_store import CoverageStore
from .debug import (
    forget_entry as forget_entry_debug,
)
from .debug import (
    set_debug_enabled,
)
from .descriptor_metadata import DESCRIPTOR_META
from .device_flow import CardataAuthError
from .distance import apply_sensor_unit, async_apply_sensor_units
from .evcc import BridgeConfig
from .frontend import async_register_frontend_card
from .history.backfill import StatisticsPublisher
from .history.store import HistoryStore
from .reauth import refresh_loop
from .refusal import Refusal, as_ha_error
from .rest import RestGateway, TokenKeeper
from .runtime import CardataConfigEntry, CardataRuntimeData
from .startup_refresh import (
    last_fetch_at,
)
from .stream import CardataStreamManager
from .tyre_store import TyreStore
from .vehicle_removal import async_forget_account_cars

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.IMAGE,
    Platform.CALENDAR,
]


# Configured through the UI only; the schema tells Home Assistant so.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register the services, once for the whole Home Assistant run."""

    # Imported here: services.py reads names from this package, which is only
    # complete once this module has finished loading.
    from .assist_api import async_register_llm_api  # noqa: PLC0415
    from .services import async_setup_services  # noqa: PLC0415

    async_setup_services(hass)
    async_register_llm_api(hass)
    return True


def _not_ready(key: str, **placeholders: Any) -> ConfigEntryNotReady:
    """Why the account cannot start yet, in the user's language; HA retries it."""

    return as_ha_error(Refusal(key, **placeholders), ConfigEntryNotReady, DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: CardataConfigEntry) -> bool:
    """Set an account up: stores, coordinator, stream, then Home Assistant's side.

    What each step decides lives in ``entry_setup.py``; this function builds the
    parts in their order and makes the Home Assistant calls.
    """

    domain_data = hass.data.setdefault(DOMAIN, {})

    _LOGGER.debug("Setting up BMW CarData entry %s", entry.entry_id)

    await async_register_frontend_card(hass)

    # Home Assistant's shared session (Gold: inject-websession): it outlives the
    # entry, so nothing here closes it.
    session = async_get_clientsession(hass)

    data: Mapping[str, Any] = entry.data
    options = dict(entry.options) if entry.options else {}
    set_debug_enabled(entry_setup.debug_flag(options), entry_id=entry.entry_id)
    should_bootstrap = not data.get(BOOTSTRAP_COMPLETE)
    client_id = data["client_id"]
    gcid = data.get("gcid")
    id_token = data.get("id_token")
    if not gcid or not id_token:
        raise _not_ready("missing_credentials")

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
    coordinator.on_new_vehicle = partial(async_on_new_vehicle, hass, entry)
    # A stream outage loses whatever the cars sent meanwhile, exactly like a
    # restart does, so a long one ends with the same catch-up.
    coordinator.on_reconnect_after_outage = partial(async_on_reconnect, hass, entry)
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

    quota_manager = await async_quota_manager(hass, entry.entry_id)
    cleaned = entry_setup.without_retired_keys(data)
    if cleaned is not None:
        data = cleaned
        hass.config_entries.async_update_entry(entry, data=data)

    async def handle_stream_error(reason: str) -> None:
        await async_handle_stream_error(hass, entry, reason)

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
    tokens = TokenKeeper(partial(async_renew_tokens, entry, session, manager), lambda: entry.data)
    rest = RestGateway(session, tokens, quota_manager.async_claim)
    containers = {
        kind.key: CardataContainerManager(
            rest=rest,
            entry_id=entry.entry_id,
            initial_container_id=data.get(kind.id_key),
            kind=kind,
        )
        for kind in KINDS
    }

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
        raise _not_ready("token_refresh_failed", error=err) from err

    if manager.client is None:
        try:
            await manager.async_start()
        except Exception as err:
            if refreshed_token:
                raise _not_ready("stream_failed_after_refresh", error=err) from err
            raise _not_ready("stream_failed", error=err) from err

    refresh_wake = asyncio.Event()
    refresh_task = entry.async_create_background_task(
        hass,
        refresh_loop(tokens, refresh_wake, partial(async_request_reauth, hass, entry)),
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
    runtime_data.reauth.on_refused = partial(login_refused, hass, coordinator)
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
            hass, async_run_bootstrap(hass, entry), f"{DOMAIN}_bootstrap"
        )

    runtime_data.telematic_task = entry.async_create_background_task(
        hass, telematic_poll_loop(hass, entry.entry_id), f"{DOMAIN}_telematic_poll"
    )
    entry.async_create_background_task(
        hass, async_startup_refresh(hass, entry.entry_id), f"{DOMAIN}_startup_refresh"
    )

    _async_wire_stop_flush(hass, entry, coordinator, history_store)

    return True


def _async_wire_statistics(
    hass: HomeAssistant,
    entry: CardataConfigEntry,
    coordinator: CardataCoordinator,
    history_store: HistoryStore,
    options: dict[str, Any],
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
        refresh_coverage_issues(hass, entry.entry_id)

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

    async_retitle(hass, entry, coordinator)

    @callback
    def _retitle_on_name(_vin: str, _name: str) -> None:
        # A callback, not a lambda: the dispatcher runs anything else on a
        # worker thread, where updating the entry raises.
        async_retitle(hass, entry, coordinator)

    entry.async_on_unload(
        async_dispatcher_connect(hass, f"{DOMAIN}_{entry.entry_id}_name", _retitle_on_name)
    )


def _async_wire_bridge(
    hass: HomeAssistant,
    entry: CardataConfigEntry,
    coordinator: CardataCoordinator,
    options: dict[str, Any],
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
    def _units_for_new_entity(event: Event[er.EventEntityRegistryUpdatedData]) -> None:
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

    async def _flush_on_stop(_event: Event) -> None:
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
        async_forget_account_cars(hass, config_entry.entry_id, vins), f"{DOMAIN}_forget_vehicle"
    )
    return True
