"""The setup wizard against a real Home Assistant: the manual path and its errors.

Bronze's ``config-flow-test-coverage`` asks for the whole flow, recovery from
errors included. BMW's two device-flow calls are patched where the flow
imports them; everything else -- the flow manager, the progress steps, the
entry it creates -- is Home Assistant's own.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bavariandata.const import DOMAIN
from custom_components.bavariandata.device_flow import CardataAuthError

CLIENT_ID = "11111111-2222-3333-4444-555555555555"
DEVICE = {
    "device_code": "dev-code",
    "user_code": "ABCD-EFGH",
    "verification_uri": "https://customer.bmwgroup.com/oneid/link",
    "interval": 1,
    "expires_in": 600,
}
TOKENS = {
    "access_token": "access",
    "refresh_token": "refresh",
    "id_token": "id",
    "expires_in": 3600,
    "scope": "openid cardata:streaming:read cardata:api:read",
    "gcid": "gcid-1",
    "token_type": "Bearer",
}
FLOW = "custom_components.bavariandata.config_flow"


class _Poll:
    """BMW's token poll, answering only once the test releases it.

    The real poll waits minutes for the user to log in; an instant fake can
    finish before the flow has shown its progress step at all, which would let
    the flow skip straight past it.
    """

    def __init__(self, answer: dict | Exception = TOKENS) -> None:
        self.answer = answer
        self.released = asyncio.Event()

    async def __call__(self, *_args, **_kwargs) -> dict:
        await self.released.wait()
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


async def _start_manual(hass: HomeAssistant) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["guided", "manual"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"
    return result


async def _answered(hass: HomeAssistant, result: dict, poll: _Poll) -> dict:
    """On the progress step: let BMW answer, land on what follows."""

    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "authorize"
    poll.released.set()
    await hass.async_block_till_done()
    return await hass.config_entries.flow.async_configure(result["flow_id"])


async def _through_authorize(hass: HomeAssistant, result: dict, poll: _Poll) -> dict:
    """Submit the client id, wait out the progress step, land on what follows."""

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"client_id": f"  {CLIENT_ID}  "}
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["description_placeholders"]["user_code"] == "ABCD-EFGH"
    return await _answered(hass, result, poll)


async def test_closing_setup_during_the_login_stops_asking_bmw(hass: HomeAssistant) -> None:
    """The poll is the progress step's task, so Home Assistant cancels it with
    the flow; a poll started any other way would ask BMW for ten more minutes."""

    poll = _Poll()
    with (
        patch(f"{FLOW}.request_device_code", return_value=DEVICE),
        patch(f"{FLOW}.poll_for_tokens", poll),
    ):
        result = await hass.config_entries.flow.async_configure(
            (await _start_manual(hass))["flow_id"], {"client_id": CLIENT_ID}
        )
        assert result["step_id"] == "authorize"
        (task,) = [
            flow._poll_task
            for flow in hass.config_entries.flow._progress.values()
            if flow.flow_id == result["flow_id"]
        ]
        hass.config_entries.flow.async_abort(result["flow_id"])
        await hass.async_block_till_done()

    assert task.cancelled()


async def test_the_manual_path_creates_the_entry(hass: HomeAssistant) -> None:
    poll = _Poll()
    with (
        patch(f"{FLOW}.request_device_code", return_value=DEVICE),
        patch(f"{FLOW}.poll_for_tokens", poll),
        patch("custom_components.bavariandata.async_setup_entry", return_value=True),
    ):
        result = await _through_authorize(hass, await _start_manual(hass), poll)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "select_clusters"
        # Nothing selected: nothing to activate in the portal, so the entry is made.
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"sections": []})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.unique_id == CLIENT_ID  # the pasted id, trimmed
    assert entry.data["client_id"] == CLIENT_ID
    assert entry.data["gcid"] == "gcid-1"
    assert entry.data["refresh_token"] == "refresh"


async def test_a_device_code_failure_keeps_the_form_and_recovers(hass: HomeAssistant) -> None:
    result = await _start_manual(hass)
    with patch(f"{FLOW}.request_device_code", side_effect=CardataAuthError("refused")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"client_id": CLIENT_ID}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "device_code_failed"}

    poll = _Poll()
    with (
        patch(f"{FLOW}.request_device_code", return_value=DEVICE),
        patch(f"{FLOW}.poll_for_tokens", poll),
    ):
        result = await _through_authorize(hass, result, poll)
    assert result["step_id"] == "select_clusters"


async def test_a_declined_authorization_offers_a_fresh_code(hass: HomeAssistant) -> None:
    poll = _Poll(CardataAuthError("denied", status=400, error_code="access_denied"))
    with (
        patch(f"{FLOW}.request_device_code", return_value=DEVICE),
        patch(f"{FLOW}.poll_for_tokens", poll),
    ):
        result = await _through_authorize(hass, await _start_manual(hass), poll)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "authorize_failed"
    assert "access_denied" in result["description_placeholders"]["error"]

    poll = _Poll()
    with (
        patch(f"{FLOW}.request_device_code", return_value=DEVICE),
        patch(f"{FLOW}.poll_for_tokens", poll),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        result = await _answered(hass, result, poll)
    assert result["step_id"] == "select_clusters"


async def test_a_second_client_of_the_same_account_is_refused(hass: HomeAssistant) -> None:
    MockConfigEntry(
        domain=DOMAIN, unique_id="another-client", data={"client_id": "x", "gcid": "gcid-1"}
    ).add_to_hass(hass)
    poll = _Poll()
    with (
        patch(f"{FLOW}.request_device_code", return_value=DEVICE),
        patch(f"{FLOW}.poll_for_tokens", poll),
    ):
        result = await _through_authorize(hass, await _start_manual(hass), poll)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "account_already_configured"


async def test_the_poll_runs_in_the_background(hass: HomeAssistant) -> None:
    """The progress step returns at once; the flow moves on when BMW answers."""

    poll = _Poll()
    with (
        patch(f"{FLOW}.request_device_code", return_value=DEVICE),
        patch(f"{FLOW}.poll_for_tokens", poll),
    ):
        result = await hass.config_entries.flow.async_configure(
            (await _start_manual(hass))["flow_id"], {"client_id": CLIENT_ID}
        )
        result = await _answered(hass, result, poll)
    assert result["step_id"] == "select_clusters"
