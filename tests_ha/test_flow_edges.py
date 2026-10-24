"""The setup and Configure paths the snapshot walks leave out.

``test_flow_snapshots.py`` pins every screen; these assert the rarer turns a
flow can take -- BMW refusing a code after the activator reported, the guided
path re-adding an account, a helper page without a known URL, a flow closed
mid-wait, the evcc bridge switched off car by car, a mileage with no car --
so Bronze's ``config-flow-test-coverage`` holds for every line of the flows.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.network import NoURLAvailableError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bavariandata.const import DOMAIN
from custom_components.bavariandata.device_flow import CardataAuthError
from custom_components.bavariandata.flow_activator import (
    _ONBOARDING_STORE,
    _handle_onboarding_webhook,
)

from .conftest import PACKAGE, VIN
from .test_config_flow import CLIENT_ID
from .test_flow_snapshots import (
    ACTIVATED,
    VIN_B,
    _account,
    _configure,
    _released,
    _start,
    _step,
    _submit,
    _token,
    _Walk,
)
from . import test_flow_snapshots as walks

# The walks' fixtures, asked for here by name.
bmw_auth = walks.bmw_auth
device_flow = walks.device_flow
https = walks.https

BRIDGE = f"{PACKAGE}.bridge.VehicleBridge"


# --- Setup ------------------------------------------------------------------------


async def test_a_code_bmw_refuses_after_the_activator_reported_can_be_retried(
    hass: HomeAssistant, https, bmw_auth, hass_client_no_auth
) -> None:
    client = await hass_client_no_auth()
    result = await _start(hass, _Walk(), "guided")
    await client.post(f"/api/webhook/{_token(result)}", data=ACTIVATED)
    await hass.async_block_till_done()
    bmw_auth.device_code.side_effect = CardataAuthError("refused")

    result = await _step(hass, _Walk(), result)

    assert result["step_id"] == "authorize_failed"
    assert "refused" in result["description_placeholders"]["error"]


async def test_the_guided_path_re_adds_an_account_with_what_it_activated(
    hass: HomeAssistant, bmw_auth
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=CLIENT_ID,
        data={"client_id": CLIENT_ID, "gcid": "gcid-1", "keep": True},
    )
    entry.add_to_hass(hass)
    result = await _start(hass, _Walk(), "guided")
    result = await _step(hass, _Walk(), result, {"result": ACTIVATED})
    await _released(hass, bmw_auth)
    result = await _step(hass, _Walk(), result)

    assert result["type"] is FlowResultType.ABORT
    assert entry.data["mapped_vehicle_id"] == "abcdef1234567890"
    assert entry.data["stream_sections"]
    assert entry.data["keep"] is True


async def test_without_a_known_url_the_helper_page_link_is_relative(
    hass: HomeAssistant, bmw_auth
) -> None:
    with patch(f"{PACKAGE}.flow_activator.get_url", side_effect=NoURLAvailableError):
        result = await _start(hass, _Walk(), "guided")

    assert f"/{DOMAIN}/onboarding?token=" in result["description_placeholders"]["link_open"]


async def test_a_report_for_no_waiting_flow_is_turned_away(hass: HomeAssistant) -> None:
    response = await _handle_onboarding_webhook(hass, "nobody-waits", None)

    assert response.status == 404


async def test_closing_a_flow_mid_wait_stops_the_wait_and_the_webhook(
    hass: HomeAssistant, https, bmw_auth
) -> None:
    result = await _start(hass, _Walk(), "guided")
    token = _token(result)
    wait = hass.config_entries.flow._progress[result["flow_id"]]._onboarding_wait_task

    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done()

    assert wait.cancelled()
    assert token not in hass.data.get(DOMAIN, {}).get(_ONBOARDING_STORE, {})


# --- Configure --------------------------------------------------------------------


async def test_streamed_data_without_a_report_in_time_offers_the_paste(
    hass: HomeAssistant, https, no_bmw
) -> None:
    entry = await _account(hass, VIN)
    with patch(f"{PACKAGE}.flow_activator.ONBOARDING_WAIT_TIMEOUT", 0):
        result = await _configure(hass, entry, _Walk(), "action_select_clusters")
        result = await _submit(hass, _Walk(), result, {"sections": ["electric"]})
        await hass.async_block_till_done()
        result = await _submit(hass, _Walk(), result)

    assert result["step_id"] == "activate_stream_paste"


async def test_charging_costs_with_statistics_rebuild_them(hass: HomeAssistant, no_bmw) -> None:
    entry = await _account(hass, VIN)
    result = await _configure(hass, entry, _Walk(), "action_charging_costs")
    with patch.object(entry.runtime_data.statistics, "async_publish", AsyncMock()) as publish:
        result = await _submit(
            hass,
            _Walk(),
            result,
            {
                "price_mode": "none",
                "price_currency": "EUR",
                "charging_loss_percent": 0,
                "history_retain_months": 0,
                "statistics_import": True,
                "charge_locations": False,
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    publish.assert_awaited_once_with(force=True)


async def _bridge_for(hass: HomeAssistant, entry, vin: str, enabled: bool) -> dict:
    result = await _configure(hass, entry, _Walk(), "action_evcc_bridge")
    result = await _submit(hass, _Walk(), result, {"vehicle": vin})
    result = await _submit(
        hass,
        _Walk(),
        result,
        {"bridge_enabled": enabled, "all_vehicles": {"bridge_retain": False}},
    )
    snippet = result["description_placeholders"]["snippet"]
    await _submit(hass, _Walk(), result, {})
    return snippet


async def test_switching_the_bridge_off_car_by_car_clears_what_each_published(
    hass: HomeAssistant, no_bmw
) -> None:
    MockConfigEntry(domain="mqtt").add_to_hass(hass)
    entry = await _account(hass, VIN, VIN_B)
    with (
        patch(f"{BRIDGE}.async_publish_all", new_callable=AsyncMock),
        patch(f"{BRIDGE}.async_clear_vehicle", new_callable=AsyncMock) as clear_car,
        patch(f"{BRIDGE}.async_clear_all", new_callable=AsyncMock) as clear_all,
    ):
        await _bridge_for(hass, entry, VIN, True)
        await _bridge_for(hass, entry, VIN_B, True)

        snippet = await _bridge_for(hass, entry, VIN_B, False)
        assert [call.args for call in clear_car.await_args_list] == [(VIN_B,)]
        assert VIN in snippet and VIN_B not in snippet, "only a published car is offered"
        clear_all.assert_not_awaited()

        await _bridge_for(hass, entry, VIN, False)
        clear_all.assert_awaited_once()


async def test_a_mileage_needs_a_car(hass: HomeAssistant, no_bmw) -> None:
    entry = await _account(hass)
    result = await _configure(hass, entry, _Walk(), "action_mileage")
    result = await _submit(hass, _Walk(), result, {"mileage_kind": "yearly"})

    assert result["step_id"] == "action_mileage"
    assert result["errors"] == {"base": "no_vins"}
    assert "vehicles" not in entry.options
