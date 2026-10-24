"""Fixtures for the real-Home-Assistant suite.

``pytest-homeassistant-custom-component`` provides ``hass`` and the rest of
Home Assistant's own test fixtures; everything here only makes the integration
loadable and keeps BMW out of every test: no fixture may let a request reach
the network (the plugin's socket guard enforces it).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load ``custom_components/bavariandata``."""

    yield
