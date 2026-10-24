"""An entry set up, fed and unloaded by a real Home Assistant.

Bronze ``test-before-setup`` and Silver ``config-entry-unloading``. BMW stays
out: the token refresh, the MQTT stream's start and stop, and the three REST
loops that run after setup are patched; the coordinator, the stores, the
platforms and the entity registry are the real thing, fed one stream batch.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bavariandata.const import BOOTSTRAP_COMPLETE, DOMAIN
from custom_components.bavariandata.api import CardataApiError

VIN = "WBATEST0000000001"
PACKAGE = "custom_components.bavariandata"
SOC = "vehicle.drivetrain.batteryManagement.header"


def _entry(**data) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="client-1",
        title="BavarianData",
        data={
            "client_id": "client-1",
            "gcid": "gcid-1",
            "id_token": "id",
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 3600,
            BOOTSTRAP_COMPLETE: True,
            **data,
        },
    )


@pytest.fixture
def no_bmw():
    """Everything that would talk to BMW, replaced by a no-op."""

    with ExitStack() as stack:
        mocks = {
            name: stack.enter_context(patch(target, new_callable=AsyncMock))
            for name, target in {
                "refresh": f"{PACKAGE}._refresh_tokens",
                "start": f"{PACKAGE}.stream.CardataStreamManager.async_start",
                "shutdown": f"{PACKAGE}.stream.CardataStreamManager.async_shutdown",
                "poll": f"{PACKAGE}._telematic_poll_loop",
                "startup": f"{PACKAGE}._async_startup_refresh",
                "bootstrap": f"{PACKAGE}._async_run_bootstrap",
            }.items()
        }
        # Every request to BMW's REST API ends in one of ``api.py``'s two
        # request functions; each answers as an unreachable BMW does, which
        # every caller already handles.
        unavailable = CardataApiError("no BMW in tests", status=503)
        for name, target in {
            "rest_json": f"{PACKAGE}.api.async_request_json",
            "rest_bytes": f"{PACKAGE}.api.async_request_bytes",
            "container_json": f"{PACKAGE}.container.async_request_json",
        }.items():
            mocks[name] = stack.enter_context(
                patch(target, new_callable=AsyncMock, side_effect=unavailable)
            )
        yield mocks


async def _set_up(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


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
    ``__init__._EntryHost``.
    """

    from custom_components.bavariandata import _account  # noqa: PLC0415

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
