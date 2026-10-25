"""An entry set up, fed and unloaded by a real Home Assistant.

Bronze ``test-before-setup`` and Silver ``config-entry-unloading``. BMW stays
out: the token refresh, the MQTT stream's start and stop, and the three REST
loops that run after setup are patched; the coordinator, the stores, the
platforms and the entity registry are the real thing, fed one stream batch.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.bavariandata.const import BOOTSTRAP_COMPLETE, DOMAIN
from custom_components.bavariandata.api import CardataApiError
from custom_components.bavariandata.device_flow import CardataAuthError

from custom_components.bavariandata import async_request_reauth, async_handle_stream_error

from .conftest import VIN
from .conftest import make_entry as _entry
from .conftest import set_up as _set_up
from .test_config_flow import DEVICE, FLOW

SOC = "vehicle.drivetrain.batteryManagement.header"


async def test_an_entry_sets_up_streams_and_unloads(hass: HomeAssistant, no_bmw) -> None:
    entry = _entry()
    await _set_up(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    no_bmw["start"].assert_awaited_once()
    coordinator = entry.runtime_data.coordinator
    assert coordinator.connection_status == "connecting"

    await coordinator.async_handle_message(
        {"vin": VIN, "data": {SOC: {"value": 63, "unit": "%", "timestamp": "2026-10-24T08:00:00Z"}}}
    )
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    soc = [
        e for e in er.async_entries_for_config_entry(registry, entry.entry_id) if SOC in e.unique_id
    ]
    assert soc, "the first SoC reading creates its sensor"
    assert hass.states.get(soc[0].entity_id).state == "63"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    no_bmw["shutdown"].assert_awaited()


async def test_a_refused_login_makes_the_cars_unavailable_until_it_works(
    hass: HomeAssistant, no_bmw, caplog: pytest.LogCaptureFixture
) -> None:
    """No data can arrive while BMW refuses the login; the account's own
    diagnostics stay up to say so, and the log says it once each way."""

    caplog.set_level(logging.INFO, logger="custom_components.bavariandata")
    entry = _entry()
    await _set_up(hass, entry)
    runtime = entry.runtime_data
    await runtime.coordinator.async_handle_message(
        {"vin": VIN, "data": {SOC: {"value": 63, "unit": "%", "timestamp": "2026-10-24T08:00:00Z"}}}
    )
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    soc = next(
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if SOC in e.unique_id
    )
    status = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_diagnostics_connection_status"
    )

    with (
        patch(f"{FLOW}.request_device_code", AsyncMock(return_value=DEVICE)),
        patch(f"{FLOW}.poll_for_tokens", AsyncMock(side_effect=CardataAuthError("waiting"))),
    ):
        await async_request_reauth(hass, entry, "Token refresh rejected")
        await async_request_reauth(hass, entry, "Token refresh rejected")
        await hass.async_block_till_done()
        assert hass.states.get(soc).state == "unavailable"
        assert hass.states.get(status).state != "unavailable"
        assert caplog.text.count("BMW refuses the login") == 1

        await async_handle_stream_error(hass, entry, "recovered")
        await hass.async_block_till_done()
    assert hass.states.get(soc).state == "63"
    assert not hass.config_entries.flow.async_progress(), "the stream closed the reauth"
    assert caplog.text.count("BMW accepts the login") == 1


async def test_an_entry_without_tokens_is_retried_not_failed(hass: HomeAssistant, no_bmw) -> None:
    entry = _entry(id_token=None)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)

    assert entry.state is ConfigEntryState.SETUP_RETRY
    no_bmw["start"].assert_not_awaited()


async def test_a_stream_that_cannot_connect_is_retried(hass: HomeAssistant, no_bmw) -> None:
    no_bmw["start"].side_effect = OSError("broker unreachable")
    entry = _entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_a_reload_comes_back_loaded(hass: HomeAssistant, no_bmw) -> None:
    entry = _entry()
    await _set_up(hass, entry)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert no_bmw["start"].await_count == 2


async def test_stored_basic_data_makes_the_device_at_setup(hass: HomeAssistant, no_bmw) -> None:
    entry = _entry(
        vehicle_metadata={VIN: {"modelName": "i5 eDrive40", "brand": "BMW", "puStep": "0723"}}
    )
    await _set_up(hass, entry)

    # Not ``async_get_device``: 2026.10 deprecates it, and its successor is too
    # new for the 2026.3 floor.
    (device,) = [
        d
        for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if (DOMAIN, VIN) in d.identifiers
    ]
    assert device.model == "i5 eDrive40"
    assert device.sw_version == "0723"


async def test_the_bootstrap_names_the_cars_bmw_maps(hass: HomeAssistant, no_bmw) -> None:
    """The REST side (account_rest.py) through the entry it serves.

    The bootstrap itself is the fixture's no-op during setup; run for real
    afterwards, it writes the entry and the device registry through
    ``__init__.EntryHost``.
    """

    from custom_components.bavariandata.account import _account  # noqa: PLC0415

    async def bmw(_session, _method, path, *_args, **_kwargs):
        if path.endswith("/mappings"):
            return [{"vin": VIN, "mappingType": "PRIMARY"}]
        if path.endswith("/basicData"):
            return {"modelName": "i5 eDrive40", "brand": "BMW"}
        raise CardataApiError("no telematics in this test", status=503)

    no_bmw["rest_json"].side_effect = bmw
    entry = _entry(**{BOOTSTRAP_COMPLETE: False})
    await _set_up(hass, entry)

    await _account(hass, entry).bootstrap()
    await hass.async_block_till_done()

    (device,) = [
        d
        for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if (DOMAIN, VIN) in d.identifiers
    ]
    assert device.model == "i5 eDrive40"
    assert entry.data[BOOTSTRAP_COMPLETE] is True
    assert entry.data["vehicle_metadata"][VIN]["modelName"] == "i5 eDrive40"


async def test_a_spent_quota_raises_a_repair_that_its_next_claim_clears(
    hass: HomeAssistant, no_bmw
) -> None:
    from homeassistant.helpers import issue_registry as ir  # noqa: PLC0415

    from custom_components.bavariandata.const import REQUEST_LIMIT  # noqa: PLC0415
    from custom_components.bavariandata.rest import CardataQuotaError  # noqa: PLC0415

    entry = _entry()
    await _set_up(hass, entry)
    quota = entry.runtime_data.quota_manager
    issue_id = f"api_quota_exhausted_{entry.entry_id}"

    for _ in range(REQUEST_LIMIT):
        await quota.async_claim()
    with pytest.raises(CardataQuotaError):
        await quota.async_claim()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders["account"] == "BavarianData"

    quota._timestamps.popleft()  # the oldest claim ages out
    await quota.async_claim()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
