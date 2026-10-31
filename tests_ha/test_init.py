"""The account's glue in ``__init__.py``, in a real Home Assistant.

What each step decides is tested HA-free (``entry_setup``, ``account_rest``,
``reauth``); this checks the calls around it: the card's dashboard resource,
the ways a setup can fail, the stop flush, deleting a car or the account, the
token refresh and the REST loops that run after setup.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send

import custom_components.bavariandata as integration
from custom_components.bavariandata import account, coverage_issues, frontend
from custom_components.bavariandata.account_rest import LAST_TELEMATIC_POLL
from custom_components.bavariandata.const import (
    BOOTSTRAP_COMPLETE,
    DOMAIN,
    LOVELACE_CARD_URL,
    VEHICLE_METADATA,
)
from custom_components.bavariandata.device_flow import CardataAuthError

from .conftest import PACKAGE, VIN
from .conftest import make_entry as _entry
from .conftest import set_up as _set_up

OTHER = "WBATEST0000000002"
# Taken before ``no_bmw`` replaces them in the package.
_REFRESH_TOKENS = integration.async_renew_tokens
_RUN_BOOTSTRAP = integration.async_run_bootstrap
_STARTUP_REFRESH = integration.async_startup_refresh
_POLL_LOOP = integration.telematic_poll_loop


# -- the card's dashboard resource -------------------------------------------


class _Resources:
    def __init__(self, items=(), *, fail: bool = False) -> None:
        self.loaded = False
        self.items = [dict(item) for item in items]
        self.fail = fail
        self.created: list[dict] = []
        self.updated: list[tuple] = []

    async def async_load(self) -> None:
        pass

    def async_items(self):
        return self.items

    async def async_create_item(self, data):
        if self.fail:
            raise RuntimeError("storage is read-only")
        self.created.append(data)

    async def async_update_item(self, item_id, data):
        self.updated.append((item_id, data))


async def test_the_card_resource_is_created_refreshed_or_left_alone(hass: HomeAssistant) -> None:
    url = f"{LOVELACE_CARD_URL}?v=2"

    # No Lovelace, or YAML-managed resources: the frontend-module fallback's job.
    assert not await frontend._async_register_lovelace_resource(hass, url)
    hass.data["lovelace"] = SimpleNamespace(resources=_Resources(), resource_mode="yaml")
    assert not await frontend._async_register_lovelace_resource(hass, url)

    fresh = _Resources()
    hass.data["lovelace"] = SimpleNamespace(resources=fresh, resource_mode="storage")
    assert await frontend._async_register_lovelace_resource(hass, url)
    assert fresh.loaded
    assert fresh.created == [{"res_type": "module", "url": url}]

    older = _Resources(
        [{"id": "x", "url": "/local/other.js"}, {"id": "card", "url": f"{LOVELACE_CARD_URL}?v=1"}]
    )
    hass.data["lovelace"] = SimpleNamespace(resources=older, mode="storage")
    assert await frontend._async_register_lovelace_resource(hass, url)
    assert older.updated == [("card", {"res_type": "module", "url": url})]
    assert older.created == []

    current = _Resources([{"id": "card", "url": url}])
    hass.data["lovelace"] = SimpleNamespace(resources=current, resource_mode="storage")
    assert await frontend._async_register_lovelace_resource(hass, url)
    assert current.updated == current.created == []

    broken = _Resources(fail=True)
    hass.data["lovelace"] = SimpleNamespace(resources=broken, resource_mode="storage")
    assert not await frontend._async_register_lovelace_resource(hass, url)


async def test_without_a_resource_store_the_card_loads_as_a_frontend_module(
    hass: HomeAssistant, monkeypatch
) -> None:
    monkeypatch.setattr(frontend, "_FRONTEND_REGISTERED", False)
    monkeypatch.setattr(hass, "http", SimpleNamespace(async_register_static_paths=AsyncMock()))
    with patch("homeassistant.components.frontend.add_extra_js_url") as added:
        await integration.async_register_frontend_card(hass)
        await hass.async_block_till_done()
    added.assert_called_once()
    assert added.call_args.args[1].startswith(f"{LOVELACE_CARD_URL}?v=")


async def test_a_missing_card_file_is_reported_and_retried_next_time(
    hass: HomeAssistant, monkeypatch, caplog
) -> None:
    monkeypatch.setattr(frontend, "_FRONTEND_REGISTERED", False)
    with patch(f"{PACKAGE}.frontend.os.path.isfile", return_value=False):
        await integration.async_register_frontend_card(hass)
    assert "bundled Lovelace card files missing" in caplog.text
    assert frontend._FRONTEND_REGISTERED is False


def test_an_unreadable_manifest_versions_the_card_zero() -> None:
    with patch("builtins.open", side_effect=OSError("gone")):
        assert frontend._integration_version() == "0"


# -- setup -------------------------------------------------------------------


async def test_a_refresh_bmw_refuses_at_setup_keeps_the_stored_token(
    hass: HomeAssistant, no_bmw, caplog
) -> None:
    no_bmw["refresh"].side_effect = CardataAuthError("invalid_grant")
    entry = _entry(**{LAST_TELEMATIC_POLL: time.time(), "base_container_id": "old"})
    await _set_up(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert "continuing with stored token" in caplog.text
    assert entry.runtime_data.coordinator.last_telematic_api_at is not None
    # A retired key leaves the entry's data at setup.
    assert "base_container_id" not in entry.data

    # The stream's own errors reach the reauth gate.
    await entry.runtime_data.stream._error_callback("recovered")


async def test_a_refresh_that_breaks_otherwise_retries_the_setup(
    hass: HomeAssistant, no_bmw
) -> None:
    no_bmw["refresh"].side_effect = RuntimeError("socket closed")
    entry = _entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_a_stream_that_fails_on_the_stored_token_retries_the_setup(
    hass: HomeAssistant, no_bmw
) -> None:
    no_bmw["refresh"].side_effect = CardataAuthError("invalid_grant")
    no_bmw["start"].side_effect = OSError("unreachable")
    entry = _entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.error_reason_translation_key == "stream_failed"


async def test_an_entity_no_release_makes_is_removed_at_setup(hass: HomeAssistant, no_bmw) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    retired = registry.async_get_or_create(
        "sensor", DOMAIN, f"{VIN}_soc_estimate_testing", config_entry=entry
    ).entity_id
    await _set_up(hass, entry)
    assert registry.async_get(retired) is None


async def test_stopping_home_assistant_saves_what_is_in_flight(hass: HomeAssistant, no_bmw) -> None:
    entry = _entry()
    await _set_up(hass, entry)
    runtime = entry.runtime_data
    with (
        patch.object(runtime.coordinator, "async_flush_trips", AsyncMock()) as trips,
        patch.object(runtime.coordinator, "async_flush_charging") as charging,
        patch.object(runtime.history, "async_save_now", AsyncMock(side_effect=OSError)) as saved,
    ):
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        await hass.async_block_till_done()
    trips.assert_awaited()
    charging.assert_called()
    saved.assert_awaited()


async def test_a_car_bmw_names_titles_the_account(hass: HomeAssistant, no_bmw) -> None:
    entry = _entry(**{VEHICLE_METADATA: {VIN: {"vin": VIN}}})
    entry.add_to_hass(hass)
    # The title setup gives an entry: ours to replace.
    hass.config_entries.async_update_entry(entry, title="BavarianData (client)")
    await _set_up(hass, entry)
    coordinator = entry.runtime_data.coordinator
    coordinator.names[VIN] = "My i5"
    async_dispatcher_send(hass, f"{DOMAIN}_{entry.entry_id}_name", VIN, "My i5")
    await hass.async_block_till_done()
    assert "My i5" in entry.title


# -- coverage repairs --------------------------------------------------------


def _report(vin: str, *missing: str):
    clusters = [SimpleNamespace(label="Charging", missing=list(missing))] if missing else []
    return SimpleNamespace(vin=vin, grace_days=7, overdue_clusters=lambda: clusters)


async def test_a_silent_cluster_raises_a_repair_that_clears_when_it_speaks(
    hass: HomeAssistant, no_bmw
) -> None:
    entry = _entry()
    await _set_up(hass, entry)
    silent = [f"vehicle.d{n}" for n in range(7)]
    issues = ir.async_get(hass)

    with patch(f"{PACKAGE}.coverage_issues.coverage_reports", return_value=[_report(VIN, *silent)]):
        integration.refresh_coverage_issues(hass, entry.entry_id)
    raised = [
        issue
        for (domain, _id), issue in issues.issues.items()
        if domain == DOMAIN and issue.translation_key == "stream_coverage_gaps"
    ]
    assert len(raised) == 1
    placeholders = raised[0].translation_placeholders
    assert placeholders["clusters"] == "Charging"
    assert placeholders["descriptors"].endswith(", ...")
    assert VIN not in str(placeholders)

    with patch(f"{PACKAGE}.coverage_issues.coverage_reports", return_value=[_report(VIN)]):
        integration.refresh_coverage_issues(hass, entry.entry_id)
    assert issues.async_get_issue(DOMAIN, raised[0].issue_id) is None

    # Nothing to judge without the account or its coverage store.
    integration.refresh_coverage_issues(hass, "no-such-entry")
    assert coverage_issues.coverage_reports(SimpleNamespace(coverage=None)) == []


# -- deleting cars -----------------------------------------------------------


async def test_deleting_a_car_forgets_it_and_keeps_the_account(hass: HomeAssistant, no_bmw) -> None:
    entry = _entry(**{VEHICLE_METADATA: {VIN: {"vin": VIN}, OTHER: {"vin": OTHER}}})
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry,
        options={"bridge_enabled": True, "vehicles": {VIN: {"trips": True}, OTHER: {}}},
    )
    await _set_up(hass, entry)
    devices = dr.async_get(hass)
    (car,) = [
        device
        for device in dr.async_entries_for_config_entry(devices, entry.entry_id)
        if (DOMAIN, VIN) in device.identifiers
    ]
    account = devices.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}
    )

    assert not await integration.async_remove_config_entry_device(hass, entry, account)
    with patch(f"{PACKAGE}.async_forget_account_cars", AsyncMock()) as forget:
        assert await integration.async_remove_config_entry_device(hass, entry, car)
        await asyncio.sleep(0)
    forget.assert_awaited_once_with(hass, entry.entry_id, [VIN])

    # What that background task runs, awaited here: the entry's own background
    # loops never end, so waiting for every background task would hang.
    with patch(f"{PACKAGE}.vehicle_removal.async_clear_published", AsyncMock()) as cleared:
        await integration.async_forget_account_cars(hass, entry.entry_id, [VIN])
        await hass.async_block_till_done()

    cleared.assert_awaited_once()
    assert cleared.await_args.kwargs["vins"] == [VIN]
    assert VIN not in entry.data[VEHICLE_METADATA]
    assert OTHER in entry.data[VEHICLE_METADATA]
    assert VIN not in entry.options["vehicles"]
    assert entry.state is ConfigEntryState.LOADED

    # An account already gone has nothing left to forget.
    await integration.async_forget_account_cars(hass, "no-such-entry", [VIN])


async def test_a_car_on_an_account_that_will_not_unload_is_left_alone(
    hass: HomeAssistant, no_bmw, caplog
) -> None:
    entry = _entry(**{VEHICLE_METADATA: {VIN: {"vin": VIN}}})
    await _set_up(hass, entry)
    with patch.object(hass.config_entries, "async_unload", AsyncMock(return_value=False)):
        await integration.async_forget_account_cars(hass, entry.entry_id, [VIN])
    assert "Could not unload entry" in caplog.text
    assert VIN in entry.data[VEHICLE_METADATA]


async def test_removing_the_account_clears_what_the_bridge_published(
    hass: HomeAssistant, no_bmw
) -> None:
    entry = _entry(**{VEHICLE_METADATA: {VIN: {"vin": VIN}}})
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={"bridge_enabled": True})
    await _set_up(hass, entry)
    # A car that only ever streamed: it has a device and nothing else.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, OTHER)}
    )
    with patch(f"{PACKAGE}.async_clear_published", AsyncMock()) as cleared:
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
    assert sorted(cleared.await_args.kwargs["vins"]) == [VIN, OTHER]


# -- tokens ------------------------------------------------------------------


async def test_a_token_refresh_stores_the_tokens_and_hands_the_stream_its_new_one(
    hass: HomeAssistant, no_bmw
) -> None:
    entry = _entry()
    await _set_up(hass, entry)
    stream = SimpleNamespace(hass=hass, async_update_credentials=AsyncMock())
    answer = {"access_token": "a2", "id_token": "i2", "expires_in": 3600}
    with patch(f"{PACKAGE}.account.refresh_tokens", AsyncMock(return_value=answer)) as refresh:
        await _REFRESH_TOKENS(entry, None, stream)
    assert refresh.await_args.kwargs["refresh_token"] == "refresh"
    assert entry.data["id_token"] == "i2"
    assert entry.data["refresh_token"] == "refresh"
    stream.async_update_credentials.assert_awaited_once_with(gcid="gcid-1", id_token="i2")

    with (
        patch(f"{PACKAGE}.account.refresh_tokens", AsyncMock(return_value={"access_token": "a3"})),
        pytest.raises(CardataAuthError, match="id_token"),
    ):
        await _REFRESH_TOKENS(entry, None, stream)


async def test_a_token_refresh_without_credentials_is_refused(hass: HomeAssistant) -> None:
    entry = _entry(refresh_token=None)
    entry.add_to_hass(hass)
    with pytest.raises(CardataAuthError, match="Missing credentials"):
        await _REFRESH_TOKENS(entry, None, SimpleNamespace(hass=hass))
    with pytest.raises(CardataAuthError, match="not ready"):
        await account.async_manual_refresh_tokens(hass, entry)


async def test_a_manual_refresh_renews_through_the_keeper(hass: HomeAssistant, no_bmw) -> None:
    entry = _entry()
    await _set_up(hass, entry)
    before = no_bmw["refresh"].await_count
    await account.async_manual_refresh_tokens(hass, entry)
    assert no_bmw["refresh"].await_count == before + 1


# -- the REST side after setup -----------------------------------------------


async def test_the_account_host_answers_from_the_loaded_entry(hass: HomeAssistant, no_bmw) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={"debug_log": False})
    await _set_up(hass, entry)
    host = account.EntryHost(hass, entry)
    assert host.options == {"debug_log": False}
    assert host.quota_remaining == entry.runtime_data.quota_manager.remaining
    host.diagnostics_changed()
    account.async_update_last_telematic_poll(hass, entry, 1_700_000_000.0)
    assert entry.data[LAST_TELEMATIC_POLL] == 1_700_000_000.0
    assert account._loaded(hass, "no-such-entry") is None


async def test_the_bootstrap_runs_only_on_a_loaded_entry(hass: HomeAssistant, no_bmw) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(f"{PACKAGE}.account.AccountRest.bootstrap", AsyncMock()) as bootstrap:
        await _RUN_BOOTSTRAP(hass, entry)
        bootstrap.assert_not_awaited()
        await _set_up(hass, entry)
        await _RUN_BOOTSTRAP(hass, entry)
    bootstrap.assert_awaited_once()


async def test_a_new_car_waits_for_the_bootstrap(hass: HomeAssistant, no_bmw) -> None:
    entry = _entry()
    await _set_up(hass, entry)
    hass.config_entries.async_update_entry(entry, data={**entry.data, BOOTSTRAP_COMPLETE: False})
    integration.async_on_new_vehicle(hass, entry, OTHER)
    assert OTHER not in entry.runtime_data.basic_data_attempted


async def test_only_a_long_outage_is_caught_up(hass: HomeAssistant, no_bmw) -> None:
    entry = _entry()
    await _set_up(hass, entry)
    with patch(f"{PACKAGE}.account.AccountRest.catch_up", AsyncMock()) as catch_up:
        integration.async_on_reconnect(hass, entry, 30)
        await hass.async_block_till_done()
        catch_up.assert_not_awaited()
        integration.async_on_reconnect(hass, entry, 3600)
        await hass.async_block_till_done()
    catch_up.assert_awaited_once_with("after a stream outage")


async def test_the_startup_refresh_asks_for_missing_basic_data_then_catches_up(
    hass: HomeAssistant, no_bmw
) -> None:
    entry = _entry()
    await _set_up(hass, entry)
    with (
        patch(f"{PACKAGE}.account.STARTUP_REFRESH_DELAY_S", 0),
        patch(f"{PACKAGE}.account.AccountRest.vins_without_basic_data", return_value=[OTHER]),
        patch(f"{PACKAGE}.account.AccountRest.catch_up", AsyncMock()) as catch_up,
        patch(f"{PACKAGE}.account.AccountRest.adopt", AsyncMock()) as adopt,
    ):
        await _STARTUP_REFRESH(hass, entry.entry_id)
        await hass.async_block_till_done()
    catch_up.assert_awaited_once_with("after startup")
    adopt.assert_awaited_once_with(OTHER)

    # Cancelled while it waits (an unload): nothing more happens.
    task = hass.async_create_task(_STARTUP_REFRESH(hass, entry.entry_id))
    await asyncio.sleep(0)
    task.cancel()
    await task


async def test_the_daily_refresh_waits_its_turn_and_stops_with_the_entry(
    hass: HomeAssistant, no_bmw
) -> None:
    entry = _entry()
    await _set_up(hass, entry)
    with (
        patch(
            f"{PACKAGE}.account.seconds_until_poll", side_effect=[0.001, 0, asyncio.CancelledError]
        ),
        patch(f"{PACKAGE}.account.TELEMATIC_POLL_INTERVAL", 0.001),
        patch(f"{PACKAGE}.account.AccountRest.daily_refresh", AsyncMock()) as daily,
    ):
        await _POLL_LOOP(hass, entry.entry_id)
    daily.assert_awaited_once()

    # An unloaded account ends the loop.
    await _POLL_LOOP(hass, "no-such-entry")
