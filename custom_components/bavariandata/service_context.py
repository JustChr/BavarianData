"""What every service handler shares: the account and car a call names, and its errors."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal, Optional, overload
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from . import service_answers
from .const import (
    DOMAIN,
)
from .device_flow import CardataAuthError
from .refusal import Refusal, as_ha_error
from .rest import CardataQuotaError
from .service_target import (
    TargetError,
    choose_account,
    choose_vin,
    device_owner,
    requested_vin,
)
from .account import (
    account_vins,
)
from .runtime import CardataRuntimeData

_LOGGER = logging.getLogger(__name__)


def as_cardata_timestamp(value: Any) -> Optional[str]:
    """A service-call date/time in BMW's format (``service_answers``)."""

    parsed = value if isinstance(value, datetime) else None
    if parsed is None and isinstance(value, str) and value:
        parsed = dt_util.parse_datetime(value)
    return service_answers.as_cardata_timestamp(value, parsed, dt_util.DEFAULT_TIME_ZONE)


def failed(key: str, **placeholders: Any) -> HomeAssistantError:
    """A call BMW (or the account) could not answer, in the user's language."""

    return as_ha_error(Refusal(key, **placeholders), HomeAssistantError, DOMAIN)


def invalid(err: Refusal) -> ServiceValidationError:
    """A call that got its arguments or its target wrong, in the user's language."""

    return as_ha_error(err, ServiceValidationError, DOMAIN)


def service_bound(call: Any, key: str, *, end_of_day: bool = False) -> Optional[datetime]:
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
        raise invalid(Refusal("bad_date", key=repr(key), value=repr(raw)))
    return service_answers.day_bound(parsed, end_of_day=end_of_day)


def month_bounds(raw: Optional[str]) -> tuple[int, int]:
    """A 'YYYY-MM' argument, defaulting to the current local month."""

    try:
        return service_answers.month_arg(raw, dt_util.now().date())
    except Refusal as err:
        raise invalid(err) from err


class ServiceContext:
    """The Home Assistant the services run in, and how a call finds its account and car."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    def entry_id_for_vin(self, vin: Optional[str]) -> Optional[str]:
        """The config entry that owns ``vin``, via its device.

        The device registry is the authority here rather than the
        coordinator's live data: an entry that has not received anything
        from the stream yet still has its devices, so a call right after a
        restart resolves the same way as one an hour later.
        """

        hass = self.hass
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

    def device_target(self, device_id: Any) -> tuple[Optional[str], Optional[str]]:
        """``(entry_id, vin)`` of a picked device.

        The VIN is ``None`` for an account's debug device, whose identifier is
        its entry id: it names an account, not a car, which is all an
        account-wide service needs.
        """

        hass = self.hass
        if not device_id:
            return None, None
        device = dr.async_get(hass).async_get(str(device_id))
        ours = [value for domain, value in device.identifiers if domain == DOMAIN] if device else []
        if not ours:
            raise TargetError("device_not_a_car", device=device_id)
        entries = {entry.entry_id for entry in hass.config_entries.async_entries(DOMAIN)}
        vin = next((value for value in ours if value not in entries), None)
        return device_owner(device), vin

    def resolve_target(
        self, call: Any
    ) -> tuple[str, ConfigEntry, CardataRuntimeData, Optional[str]]:
        """The account a call acts on, and the car it names, if it names one.

        Raises :class:`ServiceValidationError` naming what to pass instead, so
        an automation that cannot be placed fails visibly rather than logging
        and reporting success.
        """

        hass = self.hass
        loaded = {
            loaded_entry.entry_id: loaded_entry
            for loaded_entry in hass.config_entries.async_entries(DOMAIN)
            if getattr(loaded_entry, "runtime_data", None) is not None
        }
        try:
            device_entry, device_vin = self.device_target(call.data.get("device_id"))
            vin = requested_vin(call.data.get("vin"), device_vin)
            target_entry_id = choose_account(
                {entry_id: entry.title for entry_id, entry in loaded.items()},
                entry_id=call.data.get("entry_id"),
                device_entry=device_entry,
                vin=vin,
                vin_entry=self.entry_id_for_vin(vin),
            )
        except TargetError as err:
            raise invalid(err) from err
        target_entry = loaded[target_entry_id]
        return target_entry_id, target_entry, target_entry.runtime_data, vin

    def account_vins(self, entry: ConfigEntry, runtime: CardataRuntimeData) -> list[str]:
        hass = self.hass
        return account_vins(hass, entry, runtime.coordinator)

    @overload
    def pick_vin(
        self,
        entry: ConfigEntry,
        runtime: CardataRuntimeData,
        vin: Optional[str],
        *,
        required: Literal[True],
    ) -> str: ...

    @overload
    def pick_vin(
        self,
        entry: ConfigEntry,
        runtime: CardataRuntimeData,
        vin: Optional[str],
        *,
        required: bool,
    ) -> Optional[str]: ...

    def pick_vin(
        self,
        entry: ConfigEntry,
        runtime: CardataRuntimeData,
        vin: Optional[str],
        *,
        required: bool,
    ) -> Optional[str]:
        try:
            return choose_vin(
                vin, self.account_vins(entry, runtime), runtime.coordinator.names, required=required
            )
        except TargetError as err:
            raise invalid(err) from err

    @overload
    def target_car(
        self, call: Any, *, required: Literal[True] = ...
    ) -> tuple[str, ConfigEntry, CardataRuntimeData, str]: ...

    @overload
    def target_car(
        self, call: Any, *, required: bool
    ) -> tuple[str, ConfigEntry, CardataRuntimeData, Optional[str]]: ...

    def target_car(
        self, call: Any, *, required: bool = True
    ) -> tuple[str, ConfigEntry, CardataRuntimeData, Optional[str]]:
        """:func:`_resolve_target`, with the car checked against the account.

        ``required`` services act on one car and refuse to guess between
        several; the others read every car when none is named.
        """

        entry_id, entry, runtime, vin = self.resolve_target(call)
        return entry_id, entry, runtime, self.pick_vin(entry, runtime, vin, required=required)

    @overload
    async def prepare_api_call(
        self, call: ServiceCall, label: str, *, require_vin: Literal[True]
    ) -> tuple[ConfigEntry, "CardataRuntimeData", str, str]: ...

    @overload
    async def prepare_api_call(
        self, call: ServiceCall, label: str, *, require_vin: bool
    ) -> tuple[ConfigEntry, "CardataRuntimeData", Optional[str], str]: ...

    async def prepare_api_call(
        self, call: ServiceCall, label: str, *, require_vin: bool
    ) -> tuple[ConfigEntry, "CardataRuntimeData", Optional[str], str]:
        """Resolve target, take a token, claim quota and return call context.

        Shared by the read-only CarData API services. Returns
        ``(entry, runtime, vin, access_token)``; a call that cannot be placed,
        has no token or no quota left raises instead of reporting success.
        """

        target_entry_id, target_entry, runtime, vin = self.target_car(call, required=require_vin)

        try:
            access_token = await runtime.tokens.access_token()
        except CardataAuthError as err:
            _LOGGER.error(
                "Cardata %s: no valid token for entry %s: %s",
                label,
                target_entry_id,
                err,
            )
            raise failed("no_token", service=label) from err
        try:
            await runtime.rest.claim()
        except CardataQuotaError as err:
            _LOGGER.warning("Cardata %s blocked: %s", label, err)
            raise failed("request_blocked", service=label, error=err) from err

        return target_entry, runtime, vin, access_token
