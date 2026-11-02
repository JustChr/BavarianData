"""The shipped blueprints, put in place when Home Assistant sets the integration up.

What to write is tested HA-free (``tests/test_blueprint_files.py``); this checks
the calls around it in ``async_setup``: the files land in the configuration
folder, the record is stored, automations reload after a file they may use was
replaced, and a folder that can't be written doesn't stop the account.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall

from custom_components.bavariandata import blueprint_files
from custom_components.bavariandata.const import DOMAIN

from .conftest import make_entry, set_up

KEY = f"{DOMAIN}_blueprints"
LOCK_SCREEN = "charging_lock_screen.yaml"


@pytest.fixture(autouse=True)
def own_config_folder(hass: HomeAssistant, tmp_path: Path) -> None:
    """Blueprint files go to this test's own folder, not the plugin's shared one."""

    hass.config.config_dir = str(tmp_path)


def _folder(hass: HomeAssistant) -> Path:
    return Path(hass.config.path(*blueprint_files.TARGET))


def _shipped() -> list[str]:
    return sorted(path.name for path in blueprint_files.SHIPPED.glob("*.yaml"))


async def test_setup_installs_every_blueprint(hass: HomeAssistant, hass_storage, no_bmw) -> None:
    await set_up(hass, make_entry())

    folder = _folder(hass)
    assert sorted(path.name for path in folder.glob("*.yaml")) == _shipped()
    assert (folder / LOCK_SCREEN).read_bytes() == (
        blueprint_files.SHIPPED / LOCK_SCREEN
    ).read_bytes()
    assert set(hass_storage[KEY]["data"]) == set(_shipped())


async def test_an_updated_blueprint_reloads_the_automations(
    hass: HomeAssistant, hass_storage, no_bmw
) -> None:
    old = b"blueprint: {}\n"
    folder = _folder(hass)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / LOCK_SCREEN).write_bytes(old)
    hass_storage[KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": KEY,
        "data": {LOCK_SCREEN: blueprint_files.digest(old)},
    }
    reloads: list[ServiceCall] = []
    hass.services.async_register("automation", "reload", reloads.append)

    await set_up(hass, make_entry())

    assert (folder / LOCK_SCREEN).read_bytes() == (
        blueprint_files.SHIPPED / LOCK_SCREEN
    ).read_bytes()
    assert len(reloads) == 1


async def test_a_new_install_reloads_nothing(hass: HomeAssistant, no_bmw) -> None:
    reloads: list[ServiceCall] = []
    hass.services.async_register("automation", "reload", reloads.append)

    await set_up(hass, make_entry())

    assert reloads == []


async def test_a_folder_that_cannot_be_written_does_not_stop_the_account(
    hass: HomeAssistant, hass_storage, no_bmw, caplog
) -> None:
    entry = make_entry()
    with patch.object(blueprint_files, "sync", side_effect=PermissionError("read-only")):
        await set_up(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert "Could not install the BavarianData blueprints" in caplog.text
    assert KEY not in hass_storage
