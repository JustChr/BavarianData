"""The MQTT stream must keep trying to reconnect until the broker is reachable.

When BMW's broker drops the connection, ``_handle_disconnect`` schedules
``_async_reconnect``. Seen in the field: during a DNS outage that single attempt
failed with ``[Errno -3] Try again`` and nothing was scheduled after it, so the
stream stayed disconnected for nine hours -- until Home Assistant restarted --
although DNS was back within two.

The stream module imports Home Assistant only for type names, so the test stubs
those and drives the real ``CardataStreamManager``. Only the network boundary is
faked: ``_start_client`` (build the paho client and connect) fails the way a DNS
outage does for a scripted number of attempts. ``asyncio.sleep`` is replaced in
the stream module so backoff delays pass instantly.
"""

from __future__ import annotations

import asyncio
import math
import socket
import sys
import types

import pytest

from .conftest import load_module


def _load_stream():
    with pytest.MonkeyPatch.context() as patch:
        if "homeassistant" not in sys.modules:
            package = types.ModuleType("homeassistant")
            package.__path__ = []
            patch.setitem(sys.modules, "homeassistant", package)
        config_entries = types.ModuleType("homeassistant.config_entries")
        config_entries.ConfigEntry = object
        core = types.ModuleType("homeassistant.core")
        core.HomeAssistant = object
        patch.setitem(sys.modules, "homeassistant.config_entries", config_entries)
        patch.setitem(sys.modules, "homeassistant.core", core)
        return load_module("stream")


_STREAM = _load_stream()
_REAL_SLEEP = asyncio.sleep


@pytest.fixture
def instant_backoff(monkeypatch):
    fast_asyncio = types.ModuleType("asyncio")
    fast_asyncio.__dict__.update(vars(asyncio))

    async def sleep(delay, result=None):
        await _REAL_SLEEP(0)
        return result

    fast_asyncio.sleep = sleep
    monkeypatch.setattr(_STREAM, "asyncio", fast_asyncio)


class _FakeHass:
    """The slice of ``HomeAssistant`` the stream manager uses."""

    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()

    async def async_add_executor_job(self, target, *args):
        return target(*args)


class _FakeConfigEntry:
    """``ConfigEntry.async_create_background_task`` without the entry lifecycle."""

    def async_create_background_task(self, hass, target, name, eager_start=True):
        return hass.loop.create_task(target, name=name)


class _ConnectedClient:
    """The paho client ``_start_client`` leaves behind; only a stop touches it."""

    def disconnect(self) -> None:
        pass

    def loop_stop(self, force: bool = False) -> None:
        pass


class _Network:
    """Refuses connection attempts like a DNS outage, then lets one through."""

    def __init__(self, manager, failures: float) -> None:
        self._manager = manager
        self._failures = failures
        self.attempts = 0

    def start_client(self) -> None:
        self.attempts += 1
        if self.attempts <= self._failures:
            raise socket.gaierror(-3, "Try again")
        self._manager._client = _ConnectedClient()


def _manager_behind(*, failures: float):
    manager = _STREAM.CardataStreamManager(
        hass=_FakeHass(),
        client_id="client-id",
        gcid="gcid",
        id_token="id-token",
        host="customer.streaming-cardata.bmwgroup.com",
        port=9000,
        keepalive=30,
        config_entry=_FakeConfigEntry(),
    )
    network = _Network(manager, failures)
    manager._start_client = network.start_client
    return manager, network


async def _eventually(condition, timeout: float = 2.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() > deadline:
            return False
        await asyncio.sleep(0.005)
    return True


def test_reconnect_keeps_trying_until_the_outage_is_over(instant_backoff):
    async def scenario():
        manager, network = _manager_behind(failures=3)
        await manager._async_reconnect()
        connected = await _eventually(lambda: manager.client is not None)
        return connected, network.attempts

    connected, attempts = asyncio.run(scenario())

    assert connected
    assert attempts == 4


@pytest.mark.parametrize(
    ("new_token", "awaiting_new_credentials"),
    [
        pytest.param("renewed-id-token", False, id="token-renewed"),
        pytest.param("id-token", True, id="same-token-after-unauthorized"),
    ],
)
def test_a_credential_update_during_an_outage_keeps_trying(
    instant_backoff, new_token, awaiting_new_credentials
):
    async def scenario():
        manager, network = _manager_behind(failures=2)
        manager._awaiting_new_credentials = awaiting_new_credentials
        await manager.async_update_credentials(id_token=new_token)
        connected = await _eventually(lambda: manager.client is not None)
        return connected, network.attempts

    connected, attempts = asyncio.run(scenario())

    assert connected
    assert attempts == 3


def test_stopping_the_stream_ends_a_pending_reconnect(instant_backoff):
    async def scenario():
        manager, network = _manager_behind(failures=math.inf)
        await manager._async_reconnect()
        retrying = await _eventually(lambda: network.attempts >= 2)
        # What unloading or reloading the config entry does. A retry that
        # outlived it would open a second stream for the same account.
        await manager.async_stop()
        attempts_at_stop = network.attempts
        await asyncio.sleep(0.1)
        return retrying, attempts_at_stop, network.attempts

    retrying, attempts_at_stop, attempts_later = asyncio.run(scenario())

    assert retrying
    assert attempts_later == attempts_at_stop


def test_a_retry_queued_before_a_stop_never_runs(instant_backoff):
    async def scenario():
        manager, network = _manager_behind(failures=math.inf)
        # paho's network thread schedules a retry; the task is only created once
        # the event loop gets to it, and the stop lands in between.
        manager._schedule_retry(0)
        await manager.async_stop()
        await asyncio.sleep(0.05)
        return network.attempts

    assert asyncio.run(scenario()) == 0


def test_stopping_during_the_reconnect_backoff_opens_no_connection():
    async def scenario():
        manager, network = _manager_behind(failures=math.inf)
        manager._reconnect_backoff = 0.05
        manager._min_reconnect_interval = 0
        reconnect = asyncio.get_running_loop().create_task(manager._async_reconnect())
        await asyncio.sleep(0.01)  # the reconnect is now waiting out its backoff
        await manager.async_stop()
        await reconnect
        await asyncio.sleep(0.1)
        return network.attempts

    assert asyncio.run(scenario()) == 0
