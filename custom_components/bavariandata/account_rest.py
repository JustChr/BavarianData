"""What an account asks BMW over REST, and when, apart from Home Assistant.

The containers (created or replaced when their descriptor set changes), the
first-run bootstrap, each car's telematics, basic data and tyre diagnosis,
the catch-up after a start or a stream outage, and the daily refresh. Every
request goes through ``host.rest`` (rest.py: token, then quota, then the
call); everything else this needs from Home Assistant -- the entry's data, a
way to write it, the device registry, the account's cars -- comes through the
small :class:`AccountHost` that ``__init__.py`` implements.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Mapping, Optional, Protocol, cast

from .api import (
    CardataApiError,
    async_get_basic_data,
    async_get_telematic_data,
    async_get_tyre_diagnosis,
    async_get_vehicle_mappings,
)
from .const import (
    BOOTSTRAP_COMPLETE,
    DEFAULT_REFRESH_ON_START,
    LAST_STARTUP_REFRESH,
    OPTION_REFRESH_ON_START,
    TELEMATIC_POLL_INTERVAL,
    VEHICLE_METADATA,
)
from .container import FULL, KINDS, SERVICE, CardataContainerError, ContainerKind
from .debug import debug_enabled, mask_vin
from .device_flow import CardataAuthError
from .entry_setup import device_fields
from .refusal import Refusal
from .rest import CardataQuotaError
from .startup_refresh import last_fetch_at, startup_refresh_vins
from .vehicles import primary_vins

if TYPE_CHECKING:
    from .container import CardataContainerManager
    from .coordinator import CardataCoordinator
    from .rest import RestGateway

_LOGGER = logging.getLogger(__name__)

# Where the entry keeps the time of its last daily refresh.
LAST_TELEMATIC_POLL = "last_telematic_poll"


class FetchFailed(Refusal):
    """A strict fetch (a service call) could not fetch everything it was asked."""


class AccountHost(Protocol):
    """What the REST side needs of the entry it serves."""

    entry_id: str
    coordinator: CardataCoordinator
    rest: RestGateway
    containers: dict[str, CardataContainerManager]
    container_lock: asyncio.Lock

    @property
    def data(self) -> Mapping[str, Any]:
        """The entry's data, as it is now."""

    @property
    def options(self) -> Mapping[str, Any]:
        """The entry's options."""

    @property
    def quota_remaining(self) -> Optional[int]:
        """Requests left in the rolling window, if known."""

    def update_data(self, **values: Any) -> None:
        """Write ``values`` into the entry's data."""

    def register_device(self, vin: str, fields: Mapping[str, Any]) -> None:
        """Create or update the car's device."""

    def account_vins(self) -> list[str]:
        """Every car of the account (``__init__.account_vins``)."""

    def diagnostics_changed(self) -> None:
        """Tell the diagnostics sensors that BMW was asked."""


def seconds_until_poll(last_poll: Optional[float], now: float) -> float:
    """How long until the daily refresh is due; zero or less means now."""

    return TELEMATIC_POLL_INTERVAL - (now - (last_poll or 0.0))


class AccountRest:
    """An account's REST work, served through its :class:`AccountHost`."""

    def __init__(self, host: AccountHost) -> None:
        self._host = host

    # -- the entry's own records ----------------------------------------------

    def _update_data(self, **values: Any) -> None:
        """Write ``values`` into the entry's data, unless they are there already."""

        data = self._host.data
        if all(data.get(key) == value for key, value in values.items()):
            return
        self._host.update_data(**values)

    def mark_bootstrap_complete(self) -> None:
        self._update_data(**{BOOTSTRAP_COMPLETE: True})

    def update_last_telematic_poll(self, timestamp: float) -> None:
        existing = self._host.data.get(LAST_TELEMATIC_POLL)
        if existing and abs(existing - timestamp) < 1:
            return
        self._host.update_data(**{LAST_TELEMATIC_POLL: timestamp})

    def store_vehicle_metadata(self, vin: str, payload: dict[str, Any]) -> None:
        """Keep a car's basic data on the entry, so a restart needs no request."""

        existing = self._host.data.get(VEHICLE_METADATA, {})
        if not isinstance(existing, dict):
            existing = {}
        if existing.get(vin) == payload:
            return
        self._host.update_data(**{VEHICLE_METADATA: {**existing, vin: payload}})

    def vins_without_basic_data(self) -> list[str]:
        """Every car this entry has, by its data or its devices, that has no basic data."""

        have = self._host.data.get(VEHICLE_METADATA) or {}
        return [vin for vin in self._host.account_vins() if vin not in have]

    # -- containers -----------------------------------------------------------

    async def container_id(self, kind: ContainerKind = FULL) -> Optional[str]:
        """The entry's container of ``kind``, created -- or replaced -- first if need be.

        Replaced when the integration now wants a different descriptor set than
        the stored container holds (its signature), so the old one is deleted
        instead of idling against BMW's 10-container limit. ``None`` when BMW
        would not create one (already logged). Raises ``CardataAuthError`` when
        no token can be had.
        """

        host = self._host
        manager = host.containers[kind.key]
        async with host.container_lock:
            stored_id = host.data.get(kind.id_key)
            stored_signature = host.data.get(kind.signature_key)
            if stored_id and stored_signature in (manager.descriptor_signature, None):
                # An entry from before signatures were stored is taken as current.
                self._update_data(**{kind.signature_key: manager.descriptor_signature})
                manager.sync_from_entry(stored_id)
                return cast(str, stored_id)
            manager.sync_from_entry(None)
            try:
                if stored_id:
                    container_id = await manager.async_reset_hv_container()
                else:
                    container_id = await manager.async_ensure_hv_container()
            except CardataContainerError as err:
                _LOGGER.warning(
                    "Unable to set up the %s REST container for entry %s: %s",
                    kind.key,
                    host.entry_id,
                    err,
                )
                return None
            if not container_id:
                return None
            self._update_data(
                **{kind.id_key: container_id, kind.signature_key: manager.descriptor_signature}
            )
            return container_id

    # -- the bootstrap --------------------------------------------------------

    async def bootstrap(self) -> None:
        """First run: find the account's cars, give each a device, data and a name."""

        host = self._host
        _LOGGER.debug("Starting bootstrap sequence for entry %s", host.entry_id)

        try:
            vins = await self.fetch_primary_vins()
        except CardataAuthError as err:
            # Not marked complete: the next start tries again.
            _LOGGER.warning("Bootstrap for entry %s has no valid token: %s", host.entry_id, err)
            return
        if not vins:
            self.mark_bootstrap_complete()
            return

        coordinator = host.coordinator
        for vin in vins:
            coordinator.data.setdefault(vin, {})
            host.register_device(
                vin, {"manufacturer": "BMW", "name": coordinator.names.get(vin, vin)}
            )

        if await self.fetch_telematics(vins, label="Bootstrap", seed=True):
            self.update_last_telematic_poll(time.time())
        # Whatever the telematics fetch did: the basic data is what names a car and
        # tells its drivetrain, steering side and picture, and nothing else would
        # fetch it for a car that already has a device.
        await self.fetch_basic_data(vins)
        self.mark_bootstrap_complete()

    async def fetch_primary_vins(self) -> list[str]:
        """The account's own cars (BMW's ``PRIMARY`` mappings); empty on failure."""

        entry_id = self._host.entry_id
        try:
            payload = await self._host.rest.call(async_get_vehicle_mappings)
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

    # -- the fetches ----------------------------------------------------------

    async def fetch_telematics(
        self,
        vins: list[str],
        *,
        label: str = "Cardata fetch_telematic_data",
        seed: bool = False,
        strict: bool = False,
    ) -> bool:
        """Fetch each car's containers and feed them in like a stream message.

        One request per car and container (``KINDS``). Each stands alone: BMW
        refuses a whole container for one field it won't serve, so the service
        items, which it refused from 16 Oct 2026 (#142), are fetched apart and
        cost only themselves. ``seed`` skips a car that already has data (a car
        found by the bootstrap or Discover gets its entities before it streams).
        Returns whether BMW was asked at all. ``strict`` (a service call) raises
        :class:`FetchFailed` for what it could not fetch, once every car has had
        its turn; the service items alone never count, BMW refuses those daily.
        """

        host = self._host
        coordinator = host.coordinator
        container_ids: dict[str, Optional[str]] = {}
        failures: list[str] = []

        async def container_id(kind: ContainerKind) -> Optional[str]:
            if kind.key not in container_ids:
                container_ids[kind.key] = await self.container_id(kind)
            return container_ids[kind.key]

        async def fetch(vin: str) -> bool:
            """One car; whether BMW was asked."""

            asked = False
            for kind in KINDS:
                found = await container_id(kind)
                if not found:
                    _LOGGER.error(
                        "%s: entry %s has no %s REST container", label, host.entry_id, kind.key
                    )
                    if kind is not SERVICE:
                        failures.append(f"no {kind.key} REST container")
                    continue
                asked = True
                try:
                    payload = await host.rest.call(async_get_telematic_data, vin, found)
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
                        failures.append(f"{mask_vin(vin)}: {err}")
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
                failures.append(str(err))
                break
            except CardataAuthError as err:
                _LOGGER.error("%s: no valid token for %s: %s", label, mask_vin(vin), err)
                failures.append("no valid BMW token; reauthorize")
                break

        if attempted:
            coordinator.last_telematic_api_at = datetime.now(timezone.utc)
            host.diagnostics_changed()
        if strict and failures:
            raise FetchFailed("fetch_incomplete", service=label, error="; ".join(failures))
        return attempted

    async def fetch_basic_data(self, vins: list[str], label: str = "Bootstrap") -> None:
        """Fetch, apply and keep each car's basic data, and name its device by it."""

        host = self._host
        coordinator = host.coordinator
        for vin in vins:
            try:
                payload = await host.rest.call(async_get_basic_data, vin)
            except CardataQuotaError as err:
                _LOGGER.warning(
                    "%s basic data request skipped for %s: %s", label, mask_vin(vin), err
                )
                break
            except CardataAuthError as err:
                _LOGGER.warning(
                    "%s basic data for %s: no valid token: %s", label, mask_vin(vin), err
                )
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
            self.store_vehicle_metadata(vin, metadata.get("raw_data") or payload)
            host.register_device(vin, device_fields(vin, metadata))

    async def fetch_tyres(self) -> bool:
        """Refresh the smart-maintenance tyre diagnosis for every vehicle.

        Its own endpoint, so its own request per car against the daily quota --
        it cannot be folded into the container call. Like that call, it used to
        serve a single VIN, which left a second car's tread wear frozen forever
        (issue #13).
        """

        host = self._host
        refreshed = False
        for vin in host.account_vins():
            try:
                payload = await host.rest.call(async_get_tyre_diagnosis, vin)
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

            parsed = host.coordinator.apply_tyre_diagnosis(vin, payload)
            _LOGGER.debug(
                "Daily tyre diagnosis for %s: %s wheel(s)",
                mask_vin(vin),
                len(parsed.get("wheels") or {}),
            )
            refreshed = True
        return refreshed

    async def refresh(self, *, vin: Optional[str] = None, strict: bool = False) -> bool:
        """Fetch the containers of one car, or of every car of the account.

        One account can hold several cars and the container endpoint is per VIN,
        so a refresh means one request *per vehicle*. Picking a single VIN here
        left every other car's non-streamable fields frozen at their setup value
        for good -- issue #13. An explicit ``vin`` still targets just that car.
        """

        vins = [vin] if vin else self._host.account_vins()
        if not vins:
            _LOGGER.error("Cardata fetch_telematic_data: no VIN available; provide vin parameter")
            if strict:
                raise FetchFailed("no_car")
            return False
        return await self.fetch_telematics(vins, strict=strict)

    async def daily_refresh(self) -> None:
        """Everything BMW cannot stream: the containers, then the tyre diagnosis."""

        await self.refresh()
        await self.fetch_tyres()
        self.update_last_telematic_poll(time.time())

    # -- a new car, and catching up ------------------------------------------

    async def adopt(self, vin: str) -> None:
        """Fetch the basic data of a car that has none, then seed its entities.

        The bootstrap runs once per entry and is what turns a VIN into a named
        device -- model, software version, the name shown on the dashboard, and
        the Motorrad warning all come from that one REST call. A car added to the
        account later (a second BMW, or one enrolled in CarData after setup)
        streams perfectly well, so its entities appear, but it used to stay a
        bare VIN on the device page for good, because nothing ever revisited the
        bootstrap. The same goes for a car whose basic-data request failed then.
        """

        if vin in (self._host.data.get(VEHICLE_METADATA) or {}):
            return
        _LOGGER.info("Vehicle %s has no basic data yet; fetching it", mask_vin(vin))
        await self.fetch_basic_data([vin], label="New vehicle")
        # A car found by Discover (rather than by its own first message) has no
        # entities yet, and a parked car may not stream for days. Seed them from
        # one telematics fetch; a car that already streamed has data and is
        # skipped.
        await self.fetch_telematics([vin], label="New vehicle", seed=True)

    async def catch_up(self, occasion: str) -> None:
        """Fetch what the cars reported while nobody was listening.

        Runs after a start and after a stream outage. One container fetch per
        car, subject to the rules in ``startup_refresh.py`` (skipped when BMW was
        asked within the hour, charges a restart left unconfirmed first, never
        into the last of the quota). The answers go through the ordinary message
        path, so a CHARGINGACTIVE resumes a restored session and a NOCHARGING
        closes it -- before its grace timer would have filed it as ended by the
        restart.
        """

        host = self._host
        # The daily refresh counts as asking BMW too: right after it ran, a
        # catch-up would fetch the same snapshot again.
        now = time.time()
        vins = startup_refresh_vins(
            enabled=bool(host.options.get(OPTION_REFRESH_ON_START, DEFAULT_REFRESH_ON_START)),
            vins=host.account_vins(),
            unconfirmed=host.coordinator.unconfirmed_restored_charges(),
            last_fetch_at=last_fetch_at(
                host.data.get(LAST_STARTUP_REFRESH), host.data.get(LAST_TELEMATIC_POLL)
            ),
            now=now,
            remaining=host.quota_remaining,
        )
        if not vins:
            return
        # Stamped before the requests, so a restart loop that crashes mid-fetch
        # is still spaced out.
        host.update_data(**{LAST_STARTUP_REFRESH: now})
        _LOGGER.info(
            "Catching up %s: fetching the current state of %d vehicle(s) "
            "(one request each of the daily quota)",
            occasion,
            len(vins),
        )
        for vin in vins:
            await self.refresh(vin=vin)
