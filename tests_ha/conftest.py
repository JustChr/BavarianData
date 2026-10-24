"""Fixtures for the real-Home-Assistant suite.

``pytest-homeassistant-custom-component`` provides ``hass`` and the rest of
Home Assistant's own test fixtures; everything here only makes the integration
loadable and keeps BMW out of every test: no fixture may let a request reach
the network (the plugin's socket guard enforces it).
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bavariandata.api import CardataApiError
from custom_components.bavariandata.const import BOOTSTRAP_COMPLETE, DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load ``custom_components/bavariandata``."""

    yield


VIN = "WBATEST0000000001"
PACKAGE = "custom_components.bavariandata"


def make_entry(**data) -> MockConfigEntry:
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


async def set_up(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
