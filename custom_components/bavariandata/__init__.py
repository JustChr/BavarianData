"""BMW CarData integration for Home Assistant."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional

import aiohttp
import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.config_entries import ConfigEntry, SOURCE_REAUTH
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from homeassistant.components import persistent_notification
from homeassistant.helpers.storage import Store
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send

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

@dataclass
class CardataRuntimeData:
    stream: CardataStreamManager
    refresh_task: asyncio.Task
    session: aiohttp.ClientSession
    coordinator: CardataCoordinator
    container_manager: Optional[CardataContainerManager]
    bootstrap_task: asyncio.Task | None = None
    quota_manager: "QuotaManager" | None = None
    telematic_task: asyncio.Task | None = None
    reauth_in_progress: bool = False
    reauth_flow_id: str | None = None
    last_reauth_attempt: float = 0.0
    last_refresh_attempt: float = 0.0
    reauth_pending: bool = False


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

    async def async_claim(self) -> None:
        async with self._lock:
            now = time.time()
            self._prune(now)
            if len(self._timestamps) >= REQUEST_LIMIT:
                raise CardataQuotaError(
                    "BMW CarData API limit reached; try again after quota resets"
                )
            self._timestamps.append(now)
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

    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(LOVELACE_CARD_URL, card_path, cache_headers=False)]
        )
    except ImportError:
        # Home Assistant < 2024.7 lacks StaticPathConfig / async_register_static_paths.
        hass.http.register_static_path(LOVELACE_CARD_URL, card_path, cache_headers=False)

    try:
        from homeassistant.components.frontend import add_extra_js_url

        # Cache-bust on integration version so browsers pick up card updates.
        add_extra_js_url(hass, f"{LOVELACE_CARD_URL}?v={_integration_version()}")
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


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
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

    coordinator = CardataCoordinator(hass=hass, entry_id=entry.entry_id)
    coordinator.diagnostic_interval = diagnostic_interval
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

    refresh_task = hass.loop.create_task(
        _refresh_loop(hass, entry, session, manager, container_manager)
    )

    stored_container_manager = container_manager

    runtime_data = CardataRuntimeData(
        stream=manager,
        refresh_task=refresh_task,
        session=session,
        coordinator=coordinator,
        container_manager=stored_container_manager,
        bootstrap_task=None,
        quota_manager=quota_manager,
        telematic_task=None,
        reauth_in_progress=False,
        reauth_flow_id=None,
    )
    hass.data[DOMAIN][entry.entry_id] = runtime_data

    await coordinator.async_handle_connection_event("connecting")
    await coordinator.async_start_watchdog()

    if not domain_data.get("_service_registered"):

        def _resolve_target(call: Any) -> tuple[str, ConfigEntry, CardataRuntimeData] | None:
            entries = {
                key: value
                for key, value in hass.data.get(DOMAIN, {}).items()
                if not key.startswith("_")
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
            try:
                payload = await async_get_charging_history(
                    runtime.session,
                    access_token,
                    vin,
                    from_date=call.data.get("from"),
                    to_date=call.data.get("to"),
                )
            except CardataApiError as err:
                _LOGGER.error("Cardata fetch_charging_history failed for %s: %s", vin, err)
                return
            sessions = payload.get("data") if isinstance(payload, dict) else None
            _LOGGER.info(
                "Fetched charging history for %s (%s session(s))",
                vin,
                len(sessions) if isinstance(sessions, list) else 0,
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
            }
        )
        domain_data["_service_registered"] = True

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if should_bootstrap:
        runtime_data.bootstrap_task = hass.loop.create_task(
            _async_run_bootstrap(hass, entry)
        )

    runtime_data.telematic_task = hass.loop.create_task(
        _telematic_poll_loop(hass, entry.entry_id)
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    domain_data = hass.data.get(DOMAIN)
    if not domain_data or entry.entry_id not in domain_data:
        return True
    data: CardataRuntimeData = domain_data.pop(entry.entry_id)
    await data.coordinator.async_stop_watchdog()
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
    await data.stream.async_stop()
    await data.session.close()
    remaining_entries = [k for k in domain_data.keys() if not k.startswith("_")]
    if not remaining_entries:
        registered_services = domain_data.get("_registered_services", set())
        for service in list(registered_services):
            if hass.services.has_service(DOMAIN, service):
                hass.services.async_remove(DOMAIN, service)
        domain_data.pop("_service_registered", None)
        domain_data.pop("_registered_services", None)
    if not domain_data or not remaining_entries:
        hass.data.pop(DOMAIN, None)
    return True


async def _handle_stream_error(hass: HomeAssistant, entry: ConfigEntry, reason: str) -> None:
    runtime: CardataRuntimeData = hass.data[DOMAIN][entry.entry_id]
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
                    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
                    if runtime and runtime.container_manager:
                        runtime.container_manager.sync_from_entry(container_id)

    hass.config_entries.async_update_entry(entry, data=data)
    await manager.async_update_credentials(
        gcid=data.get("gcid"),
        id_token=new_id_token,
    )
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime:
        runtime.reauth_pending = False


async def async_manual_refresh_tokens(hass: HomeAssistant, entry: ConfigEntry) -> None:
    runtime: CardataRuntimeData | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is None:
        raise CardataAuthError("Integration runtime not ready")
    await _refresh_tokens(
        entry,
        runtime.session,
        runtime.stream,
        runtime.container_manager,
    )


async def _async_run_bootstrap(hass: HomeAssistant, entry: ConfigEntry) -> None:
    domain_entries = hass.data.get(DOMAIN, {})
    runtime: CardataRuntimeData | None = domain_entries.get(entry.entry_id)
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
    runtime: CardataRuntimeData = hass.data[DOMAIN][entry.entry_id]
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
                hass.data.get(DOMAIN, {}).get(entry_id)
                if hass.data.get(DOMAIN)
                else None
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
