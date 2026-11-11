"""The diagnostics download of a real, fed entry.

Users attach this file to public issues, so the test reads it the way a
stranger would: as text, looking for the VIN and the tokens.
"""

from __future__ import annotations

import json

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.bavariandata.diagnostics import async_get_config_entry_diagnostics

from .conftest import DOMAIN, VIN
from .conftest import make_entry as _entry
from .conftest import set_up as _set_up

SOC = "vehicle.drivetrain.batteryManagement.header"


async def test_the_download_says_what_arrived_and_names_no_one(hass: HomeAssistant, no_bmw) -> None:
    entry = _entry(scope="openid cardata:streaming:read cardata:api:read")
    await _set_up(hass, entry)
    coordinator = entry.runtime_data.coordinator
    await coordinator.async_handle_message(
        {"vin": VIN, "data": {SOC: {"value": 63, "unit": "%", "timestamp": "2026-10-24T08:00:00Z"}}}
    )
    await hass.async_block_till_done()

    payload = await async_get_config_entry_diagnostics(hass, entry)

    assert payload["integration"]["domain"] == "bavariandata"
    assert payload["config_entry"]["bootstrap_complete"] is True
    scopes = payload["config_entry"]["scopes"]
    assert scopes["api"] is True and scopes["streaming"] is True
    assert payload["quota"]["configured"] is True
    assert payload["quota"]["limit"] == 50
    (car,) = payload["vehicles"]
    assert car["arrivals_total"] == 1
    assert car["descriptor_count"] >= 1
    assert car["last_message_at"]
    assert car["vin_fingerprint"]

    text = json.dumps(payload, default=str)
    for secret in (VIN, "gcid-1", "client-1", "refresh", "access"):
        assert f'"{secret}"' not in text, secret
    assert car["vin"] == "**REDACTED**"


async def test_a_car_without_basic_data_or_coverage_still_downloads(
    hass: HomeAssistant, no_bmw
) -> None:
    entry = _entry()
    await _set_up(hass, entry)

    payload = await async_get_config_entry_diagnostics(hass, entry)

    assert payload["vehicles"] == []
    assert payload["stream"]["connection_status"] == "connecting"


async def test_the_download_says_how_bmw_answered_rest(hass: HomeAssistant, no_bmw) -> None:
    # #296: the stream ran for days while BMW refused every fetch, and the file
    # could not show it. A fetch service's refusal is counted like any other.
    entry = _entry()
    await _set_up(hass, entry)
    before = (await async_get_config_entry_diagnostics(hass, entry))["rest"]
    assert before["last_failure"] is None

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "fetch_vehicle_mappings", {}, blocking=True)

    rest = (await async_get_config_entry_diagnostics(hass, entry))["rest"]
    assert rest["failed"] == before["failed"] + 1
    failure = rest["last_failure"]
    assert (failure["request"], failure["status"]) == ("get_vehicle_mappings", 503)
    assert failure["at"]
