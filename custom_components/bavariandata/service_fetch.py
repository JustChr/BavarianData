"""The services that ask BMW now: REST fetches (each spends quota) and the portal activation."""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any
import voluptuous as vol
from homeassistant.core import ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import (
    async_dispatcher_send,
)
from homeassistant.util import dt as dt_util
from . import service_answers
from .const import (
    DOMAIN,
    VEHICLE_METADATA,
    OPTION_STREAM_SECTIONS,
)
from .api import (
    CardataApiError,
    async_get_basic_data,
    async_get_charging_history,
    async_get_location_based_charging_settings,
    async_get_tyre_diagnosis,
    async_get_vehicle_mappings,
)
from .refusal import Refusal
from .stream_activation import (
    PortalSession,
    PortalStreamClient,
    StreamActivationError,
)
from .vehicles import known_vins, left_account_issue_id, left_account_vins, primary_vins
from .debug import (
    mask_vin,
)
from .account import (
    async_on_new_vehicle,
    async_perform_telematic_fetch,
    async_store_vehicle_metadata,
    async_update_last_telematic_poll,
)
from .service_context import (
    ServiceContext,
    as_cardata_timestamp,
    failed,
    invalid,
)

_LOGGER = logging.getLogger(__name__)


async def async_handle_fetch(ctx: ServiceContext, call: ServiceCall) -> None:
    hass = ctx.hass
    _entry_id, target_entry, runtime, vin = ctx.target_car(call, required=False)
    success = await async_perform_telematic_fetch(
        hass,
        target_entry,
        runtime,
        vin_override=vin,
        strict=True,
    )
    if success:
        async_update_last_telematic_poll(hass, target_entry, time.time())


async def async_handle_fetch_mappings(ctx: ServiceContext, call: ServiceCall) -> None:
    hass = ctx.hass
    prepared = await ctx.prepare_api_call(call, "fetch_vehicle_mappings", require_vin=False)
    target_entry, runtime, _vin, access_token = prepared
    try:
        payload = await async_get_vehicle_mappings(runtime.session, access_token)
    except CardataApiError as err:
        _LOGGER.error("Cardata fetch_vehicle_mappings failed: %s", err)
        raise failed("bmw_error", service="fetch_vehicle_mappings", error=err) from err
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
            async_on_new_vehicle(hass, target_entry, vin)
    # ...and the other way round: a car the account no longer lists (sold,
    # or handed to another primary user) sends nothing more, and its
    # entities would sit frozen with nothing saying why.
    account_vins = ctx.account_vins(target_entry, runtime)
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


async def async_handle_fetch_basic_data(ctx: ServiceContext, call: ServiceCall) -> None:
    hass = ctx.hass
    prepared = await ctx.prepare_api_call(call, "fetch_basic_data", require_vin=True)
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
        raise failed("bmw_error", service="fetch_basic_data", error=err) from err
    _LOGGER.info("Fetched basic vehicle data for %s", mask_vin(vin))
    _LOGGER.debug("Cardata basic data for %s: %s", vin, payload)
    if isinstance(payload, dict):
        metadata = runtime.coordinator.apply_basic_data(vin, payload)
        if metadata:
            async_store_vehicle_metadata(
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


async def async_handle_fetch_charging_history(ctx: ServiceContext, call: ServiceCall) -> None:
    hass = ctx.hass
    prepared = await ctx.prepare_api_call(call, "fetch_charging_history", require_vin=True)
    _entry, runtime, vin, access_token = prepared
    # BMW requires both from/to; default to the last 30 days so a bare
    # call from the UI succeeds instead of returning HTTP 400.
    now = dt_util.utcnow()
    to_date = as_cardata_timestamp(call.data.get("to")) or service_answers.cardata_timestamp(now)
    from_date = as_cardata_timestamp(call.data.get("from")) or service_answers.cardata_timestamp(
        now - timedelta(days=30)
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
        raise failed("bmw_error", service="fetch_charging_history", error=err) from err
    sessions = payload.get("data") if isinstance(payload, dict) else None
    # Import BMW's server-side history into our own store so it shows on
    # the card, enriching any live-recorded session in place.
    imported = updated = 0
    if runtime.history is not None and isinstance(sessions, list):
        imported, updated = runtime.history.import_cardata_sessions(
            vin,
            sessions,
            cost_fn=service_answers.backfill_cost(runtime.coordinator.pricing),
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


async def async_handle_fetch_tyre_diagnosis(ctx: ServiceContext, call: ServiceCall) -> None:
    prepared = await ctx.prepare_api_call(call, "fetch_tyre_diagnosis", require_vin=True)
    _entry, runtime, vin, access_token = prepared
    try:
        payload = await async_get_tyre_diagnosis(runtime.session, access_token, vin)
    except CardataApiError as err:
        _LOGGER.error("Cardata fetch_tyre_diagnosis failed for %s: %s", mask_vin(vin), err)
        raise failed("bmw_error", service="fetch_tyre_diagnosis", error=err) from err
    parsed = runtime.coordinator.apply_tyre_diagnosis(vin, payload)
    _LOGGER.info(
        "Fetched tyre diagnosis for %s (%s wheel(s) reported)",
        mask_vin(vin),
        len(parsed.get("wheels") or {}),
    )
    _LOGGER.debug("Cardata tyre diagnosis for %s: %s", vin, payload)


async def async_handle_fetch_location_charging(ctx: ServiceContext, call: ServiceCall) -> None:
    prepared = await ctx.prepare_api_call(
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
        raise failed("bmw_error", service="fetch_location_charging_settings", error=err) from err
    settings = payload.get("data") if isinstance(payload, dict) else None
    _LOGGER.info(
        "Fetched location-based charging settings for %s (%s entr(y/ies))",
        mask_vin(vin),
        len(settings) if isinstance(settings, list) else 0,
    )
    _LOGGER.debug("Cardata location-based charging settings for %s: %s", vin, payload)


async def async_handle_fetch_vehicle_image(ctx: ServiceContext, call: ServiceCall) -> None:
    hass = ctx.hass
    # Resolve the target entry/vin (this also refreshes tokens and claims a
    # quota slot); the shared image helper performs the actual fetch and
    # updates the ``image`` entity's cache + Store so the render shows up in
    # the Lovelace card. Token refresh / quota are handled inside the helper
    # as well, so we resolve the target here without claiming twice.
    # The VIN goes into the request path and keys the stored picture, so
    # only a car this account knows (``_target_car`` refuses anything
    # else): a stranger spends a request and leaves a junk entry in the
    # image Store for good.
    _target_id, target_entry, runtime, vin = ctx.target_car(call)
    from .image import async_refresh_vehicle_image

    await async_refresh_vehicle_image(hass, target_entry, runtime, vin, strict=True)


TELEMATIC_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): str,
        vol.Optional("device_id"): str,
        vol.Optional("vin"): str,
    }
)


MAPPING_SERVICE_SCHEMA = vol.Schema({vol.Optional("entry_id"): str, vol.Optional("device_id"): str})


BASIC_DATA_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): str,
        vol.Optional("device_id"): str,
        vol.Optional("vin"): str,
    }
)


VIN_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): str,
        vol.Optional("device_id"): str,
        vol.Optional("vin"): str,
    }
)


CHARGING_HISTORY_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): str,
        vol.Optional("device_id"): str,
        vol.Optional("vin"): str,
        vol.Optional("from"): str,
        vol.Optional("to"): str,
    }
)


ACTIVATE_STREAM_FIELDS_SCHEMA = vol.Schema(
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


async def async_handle_activate_stream_fields(ctx: ServiceContext, call: Any) -> dict[str, Any]:
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
    _entry_id, entry, runtime, _vin = ctx.resolve_target(call)

    try:
        attributes = service_answers.activation_attributes(
            call.data, entry.data.get(OPTION_STREAM_SECTIONS)
        )
    except Refusal as err:
        raise invalid(err) from err

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
        raise invalid(service_answers.activation_error(err.kind, str(err))) from err

    return {
        "mapped_vehicle_id": mapped_vehicle_id,
        "requested": result.requested_count,
        "accepted": result.accepted_count,
        "unchanged": result.unchanged,
    }
