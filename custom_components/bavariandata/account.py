"""What a loaded account does while it runs: tokens and reauth, the REST bootstrap, catch-up and poll, new cars and their metadata."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from typing import Any, Dict, Optional

import aiohttp
from homeassistant.components import persistent_notification
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import (
    async_dispatcher_send,
)
from homeassistant.helpers.storage import Store

from .account_rest import (
    LAST_TELEMATIC_POLL,
    AccountRest,
    FetchFailed,
    seconds_until_poll,
)
from .const import (
    BOOTSTRAP_COMPLETE,
    DEFAULT_SCOPE,
    DOMAIN,
    REQUEST_LIMIT,
    REQUEST_LOG,
    REQUEST_LOG_VERSION,
    TELEMATIC_POLL_INTERVAL,
    VEHICLE_METADATA,
)
from .coordinator import CardataCoordinator
from .debug import (
    mask_vin,
)
from .device_flow import CardataAuthError, refresh_tokens
from .quota import QuotaManager
from .reauth import renewed_entry_data
from .refusal import as_ha_error
from .runtime import CardataConfigEntry, CardataRuntimeData
from .startup_refresh import (
    STARTUP_REFRESH_DELAY_S,
    outage_needs_catch_up,
)
from .stream import CardataStreamManager
from .vehicle_removal import registered_vins
from .vehicles import (
    known_vins,
    new_account_title,
)

_LOGGER = logging.getLogger(__name__)


async def async_quota_manager(hass: HomeAssistant, entry_id: str) -> QuotaManager:
    """The entry's quota (quota.py), kept in a Store, its exhaustion a repair."""

    issue_id = f"api_quota_exhausted_{entry_id}"

    def exhausted(next_reset: str) -> None:
        entry = hass.config_entries.async_get_entry(entry_id)
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="api_quota_exhausted",
            translation_placeholders={
                "limit": str(REQUEST_LIMIT),
                "next_reset": next_reset,
                "account": getattr(entry, "title", None) or "BavarianData",
            },
            learn_more_url=(
                "https://github.com/JustChr/BavarianData/wiki/Troubleshooting-and-FAQ#quota-exhausted"
            ),
        )

    return await QuotaManager.async_create(
        Store(hass, REQUEST_LOG_VERSION, f"{DOMAIN}_{entry_id}_{REQUEST_LOG}"),
        on_exhausted=exhausted,
        on_available=lambda: ir.async_delete_issue(hass, DOMAIN, issue_id),
    )


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
    vins.extend(registered_vins(hass, entry, exclude=set(vins)))
    return vins


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


@callback
def login_refused(hass: HomeAssistant, coordinator: CardataCoordinator, refused: bool) -> None:
    """BMW refused the login, or takes it again: the cars' entities follow."""

    # Once each way, as the change happens: the gate calls this only on a change.
    if refused:
        _LOGGER.warning(
            "BMW refuses the login of entry %s; its cars are unavailable until it is accepted",
            coordinator.entry_id,
        )
    else:
        _LOGGER.info("BMW accepts the login of entry %s again", coordinator.entry_id)
    coordinator.login_refused = refused
    async_dispatcher_send(hass, coordinator.signal_availability)
    async_dispatcher_send(hass, coordinator.signal_diagnostics)


async def async_handle_stream_error(
    hass: HomeAssistant, entry: CardataConfigEntry, reason: str
) -> None:
    await entry.runtime_data.reauth.stream_error(_ReauthHost(hass, entry), reason)


async def async_request_reauth(hass: HomeAssistant, entry: CardataConfigEntry, why: str) -> None:
    """Ask the user to authorize again, unless that is already under way."""

    await entry.runtime_data.reauth.request(_ReauthHost(hass, entry), why)


async def async_renew_tokens(
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


class EntryHost:
    """``account_rest.AccountHost`` over a loaded entry."""

    def __init__(self, hass: HomeAssistant, entry: CardataConfigEntry) -> None:
        runtime: CardataRuntimeData = entry.runtime_data
        self._hass = hass
        self._entry = entry
        self._quota = runtime.quota_manager
        self.entry_id = entry.entry_id
        self.coordinator = runtime.coordinator
        self.rest = runtime.rest
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

    return AccountRest(EntryHost(hass, entry))


def _loaded(hass: HomeAssistant, entry_id: str) -> Optional[CardataConfigEntry]:
    """The entry, if it exists and is set up."""

    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or getattr(entry, "runtime_data", None) is None:
        return None
    return entry


async def async_run_bootstrap(hass: HomeAssistant, entry: CardataConfigEntry) -> None:
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
def async_on_reconnect(hass: HomeAssistant, entry: CardataConfigEntry, down_s: float) -> None:
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


def async_on_new_vehicle(hass: HomeAssistant, entry: CardataConfigEntry, vin: str) -> None:
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


async def async_perform_telematic_fetch(
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
        raise as_ha_error(err, HomeAssistantError, DOMAIN) from err


def async_update_last_telematic_poll(
    hass: HomeAssistant, entry: CardataConfigEntry, timestamp: float
) -> None:
    _account(hass, entry).update_last_telematic_poll(timestamp)


async def async_startup_refresh(hass: HomeAssistant, entry_id: str) -> None:
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
            async_on_new_vehicle(hass, entry, vin)
    await _async_catch_up(hass, entry_id, "after startup")


async def _async_catch_up(hass: HomeAssistant, entry_id: str, occasion: str) -> None:
    """:meth:`AccountRest.catch_up`, if the entry is still loaded."""

    entry = _loaded(hass, entry_id)
    if entry is not None:
        await _account(hass, entry).catch_up(occasion)


async def telematic_poll_loop(hass: HomeAssistant, entry_id: str) -> None:
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
def async_retitle(hass: HomeAssistant, entry: ConfigEntry, coordinator: Any) -> None:
    """Title the entry with its cars' names, unless the user has named it."""

    vins = account_vins(hass, entry, coordinator)
    # A car without basic data is "named" by its VIN; that never goes in a title.
    names = [coordinator.names.get(vin) for vin in vins if coordinator.names.get(vin) != vin]
    title = new_account_title(entry.title, entry.data.get(_AUTO_TITLE), names)
    if title is not None:
        hass.config_entries.async_update_entry(
            entry, title=title, data={**entry.data, _AUTO_TITLE: title}
        )


def async_store_vehicle_metadata(
    hass: HomeAssistant,
    entry: CardataConfigEntry,
    vin: str,
    payload: Dict[str, Any],
) -> None:
    _account(hass, entry).store_vehicle_metadata(vin, payload)
