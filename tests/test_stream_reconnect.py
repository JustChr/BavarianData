"""The MQTT stream must recover from outages without ever running two clients.

When BMW's broker drops the connection, ``_handle_disconnect`` schedules
``_async_reconnect``. Seen in the field: during a DNS outage that single attempt
failed with ``[Errno -3] Try again`` and nothing was scheduled after it, so the
stream stayed disconnected for nine hours -- until Home Assistant restarted --
although DNS was back within two.

Recovery has to keep trying, but BMW allows one stream per account (the client
id is the GCID): a second client makes the broker kick the first, and the two
then push each other off indefinitely. So every test that lets connects overlap
also counts the clients that are alive at the same time.

The stream module imports Home Assistant only for type names, so the test stubs
those and drives the real ``CardataStreamManager``. Only the network boundary is
faked: ``_start_client`` (build the paho client and connect) runs on a real
executor thread, takes as long as the scenario says, and fails the way a DNS
outage does for a scripted number of attempts.
"""

from __future__ import annotations

import asyncio
import math
import socket
import sys
import threading
import time
import types

import pytest
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

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
    """Let the stream module's backoff waits pass instantly."""

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
        return await self.loop.run_in_executor(None, target, *args)


class _FakeConfigEntry:
    """``ConfigEntry.async_create_background_task`` without the entry lifecycle."""

    def async_create_background_task(self, hass, target, name, eager_start=True):
        return hass.loop.create_task(target, name=name)


class _Registry:
    """Counts paho clients that are connected and not yet stopped."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.live = 0
        self.max_live = 0

    def opened(self) -> None:
        with self._lock:
            self.live += 1
            self.max_live = max(self.max_live, self.live)

    def closed(self) -> None:
        with self._lock:
            self.live -= 1


class _ConnectedClient:
    """The paho client ``_start_client`` leaves behind; only a stop touches it."""

    def __init__(self, registry: _Registry, manager) -> None:
        self._registry = registry
        self._manager = manager
        self._userdata: dict = {}
        self._stopped = False
        registry.opened()

    def disconnect(self) -> None:
        # paho answers a requested disconnect with on_disconnect(rc 0).
        self._manager._handle_disconnect(
            self,
            self._userdata,
            None,
            ReasonCode(PacketTypes.DISCONNECT, "Normal disconnection"),
        )

    def loop_stop(self, force: bool = False) -> None:
        if not self._stopped:
            self._stopped = True
            self._registry.closed()


class _Network:
    """Refuses connection attempts like a DNS outage, then lets them through."""

    def __init__(self, manager, failures: float, connect_time: float) -> None:
        self._manager = manager
        self._failures = failures
        self._connect_time = connect_time
        self.registry = _Registry()
        self.attempts = 0

    def start_client(self) -> None:
        self.attempts += 1
        attempt = self.attempts
        time.sleep(self._connect_time)
        if attempt <= self._failures:
            raise socket.gaierror(-3, "Try again")
        self._manager._client = _ConnectedClient(self.registry, self._manager)


def _manager_behind(*, failures: float, connect_time: float = 0.0):
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
    network = _Network(manager, failures, connect_time)
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


def _from_the_network_thread(target) -> None:
    """Run ``target`` the way paho's callbacks do: on a thread of their own."""

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()


# -- Recovery keeps trying ---------------------------------------------------


def test_reconnect_keeps_trying_until_the_outage_is_over(instant_backoff):
    async def scenario():
        manager, network = _manager_behind(failures=3)
        await manager._async_reconnect(manager.client)
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


def test_a_retry_scheduled_from_the_network_thread_connects(instant_backoff):
    async def scenario():
        manager, _network = _manager_behind(failures=0)
        _from_the_network_thread(manager._schedule_retry)
        return await _eventually(lambda: manager.client is not None)

    assert asyncio.run(scenario())


# -- A stop ends recovery ----------------------------------------------------


def test_stopping_the_stream_ends_a_pending_reconnect(instant_backoff):
    async def scenario():
        manager, network = _manager_behind(failures=math.inf)
        await manager._async_reconnect(manager.client)
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


def test_a_retry_scheduled_from_the_network_thread_before_a_stop_never_runs(
    instant_backoff,
):
    async def scenario():
        manager, network = _manager_behind(failures=0)
        # paho schedules the retry on its own thread; the task is only created
        # once the event loop gets to it, and the stop lands in between.
        _from_the_network_thread(manager._schedule_retry)
        await manager.async_stop()
        await asyncio.sleep(0.05)
        return network.attempts

    assert asyncio.run(scenario()) == 0


def test_a_retry_scheduled_after_shutdown_never_connects(instant_backoff):
    async def scenario():
        manager, network = _manager_behind(failures=0)
        await manager.async_shutdown()
        # A callback from the client that was just stopped can still arrive.
        _from_the_network_thread(manager._schedule_retry)
        await asyncio.sleep(0.05)
        return network.attempts

    assert asyncio.run(scenario()) == 0


def test_a_credential_update_after_shutdown_opens_no_stream(instant_backoff):
    async def scenario():
        manager, network = _manager_behind(failures=0)
        await manager.async_shutdown()
        # A token refresh that was already in flight (a service call, the vehicle
        # image) still hands its new token to the stream after the unload.
        await manager.async_update_credentials(id_token="renewed-id-token")
        await asyncio.sleep(0.05)
        return network.attempts

    assert asyncio.run(scenario()) == 0


def test_stopping_before_the_first_retry_opens_no_connection():
    async def scenario():
        manager, network = _manager_behind(failures=math.inf)
        manager._min_reconnect_interval = 0.05
        reconnect = asyncio.get_running_loop().create_task(manager._async_reconnect(manager.client))
        await asyncio.sleep(0.01)  # the reconnect is now waiting to retry
        await manager.async_stop()
        await reconnect
        await asyncio.sleep(0.15)
        return network.attempts

    assert asyncio.run(scenario()) == 0


# -- Never two clients -------------------------------------------------------


def test_a_pending_retry_and_a_credential_update_share_one_stream():
    async def scenario():
        manager, network = _manager_behind(failures=0, connect_time=0.3)
        manager._min_reconnect_interval = 0.5
        # A failed attempt left a retry pending when the refresh loop delivers a
        # renewed token, just as the network comes back.
        manager._schedule_retry()
        await asyncio.sleep(0.05)
        await manager.async_update_credentials(id_token="renewed-id-token")
        await asyncio.sleep(1.0)
        return network.registry.max_live, network.registry.live

    max_live, live = asyncio.run(scenario())

    assert max_live == 1
    assert live == 1


def test_a_credential_update_while_a_reconnect_waits_opens_one_stream():
    async def scenario():
        manager, network = _manager_behind(failures=0, connect_time=0.1)
        manager._min_reconnect_interval = 0.3
        # The broker dropped the connection and the reconnect waits to retry.
        reconnect = asyncio.get_running_loop().create_task(manager._async_reconnect(manager.client))
        await asyncio.sleep(0.05)
        await manager.async_update_credentials(id_token="renewed-id-token")
        await reconnect
        await asyncio.sleep(0.6)
        return network.registry.max_live, network.registry.live

    max_live, live = asyncio.run(scenario())

    assert max_live == 1
    assert live == 1


class _Broker:
    """Accepts a connection only with a valid ID token, like BMW's broker.

    It answers on a thread of its own, the way paho's network loop does. A
    refused CONNACK (135, "not authorized") is followed by on_disconnect with
    "Unspecified error", which is what paho 2.1 does after a refused connect.
    """

    def __init__(self, manager, valid_tokens: set[str]) -> None:
        self._manager = manager
        self._valid_tokens = valid_tokens
        self.registry = _Registry()
        self.attempts = 0
        self.accepted = 0

    def start_client(self) -> None:
        self.attempts += 1
        manager = self._manager
        client = _ConnectedClient(self.registry, manager)
        manager._client = client
        accepted = manager._password in self._valid_tokens

        def network_loop() -> None:
            if accepted:
                self.accepted += 1
                manager._handle_connect(
                    client, client._userdata, None, ReasonCode(PacketTypes.CONNACK, identifier=0)
                )
                return
            manager._handle_connect(
                client, client._userdata, None, ReasonCode(PacketTypes.CONNACK, identifier=135)
            )
            manager._handle_disconnect(
                client,
                client._userdata,
                None,
                ReasonCode(PacketTypes.DISCONNECT, "Unspecified error"),
            )

        threading.Thread(target=network_loop, daemon=True).start()


def _manager_with_broker(*, valid_tokens: set[str]):
    manager, _network = _manager_behind(failures=0)
    broker = _Broker(manager, valid_tokens)
    manager._start_client = broker.start_client
    return manager, broker


def test_a_refused_login_followed_by_a_renewed_token_leaves_one_stream(instant_backoff):
    async def scenario():
        manager, broker = _manager_with_broker(valid_tokens={"renewed-id-token"})

        async def refresh_tokens(reason: str) -> None:
            if reason == "unauthorized":
                await manager.async_update_credentials(id_token="renewed-id-token")

        manager._error_callback = refresh_tokens
        await manager.async_start()  # with the ID token that has expired
        connected = await _eventually(lambda: broker.accepted >= 1)
        await asyncio.sleep(0.2)  # room for a second client to show up
        return connected, broker.registry.max_live, broker.registry.live, broker.attempts

    connected, max_live, live, attempts = asyncio.run(scenario())

    assert connected
    assert (max_live, live, attempts) == (1, 1, 2)


def test_a_refused_login_stops_connecting_until_the_token_is_renewed(instant_backoff):
    async def scenario():
        manager, broker = _manager_with_broker(valid_tokens={"renewed-id-token"})

        async def token_endpoint_unreachable(reason: str) -> None:
            return

        manager._error_callback = token_endpoint_unreachable
        await manager.async_start()
        await asyncio.sleep(0.3)
        attempts_while_waiting = broker.attempts
        await manager.async_update_credentials(id_token="renewed-id-token")
        connected = await _eventually(lambda: broker.accepted >= 1)
        return attempts_while_waiting, connected, broker.attempts

    attempts_while_waiting, connected, attempts = asyncio.run(scenario())

    assert attempts_while_waiting == 1
    assert connected
    assert attempts == 2


def test_a_late_disconnect_of_a_replaced_client_leaves_the_new_one_alone(instant_backoff):
    async def scenario():
        manager, network = _manager_behind(failures=0)
        await manager.async_start()
        replaced = manager.client
        await manager.async_update_credentials(id_token="renewed-id-token")
        current = manager.client
        # paho's on_disconnect for the replaced client scheduled a reconnect that
        # only runs now, after the credential update connected the new client.
        await manager._async_reconnect(replaced)
        await asyncio.sleep(0.05)
        return manager.client is current, network.attempts, network.registry.live

    still_current, attempts, live = asyncio.run(scenario())

    assert still_current
    assert (attempts, live) == (2, 1)


def test_renewing_the_token_of_a_connected_stream_replaces_its_client(instant_backoff):
    async def scenario():
        manager, network = _manager_behind(failures=0)
        await manager.async_start()
        await manager.async_update_credentials(id_token="renewed-id-token")
        await asyncio.sleep(0.05)
        return network.registry.max_live, network.registry.live, network.attempts

    max_live, live, attempts = asyncio.run(scenario())

    assert (max_live, live, attempts) == (1, 1, 2)


def test_a_retry_left_by_a_failed_connect_does_not_open_a_second_stream(instant_backoff):
    async def scenario():
        manager, network = _manager_behind(failures=1, connect_time=0.05)
        # Setup: the connect after the token refresh fails and leaves a retry...
        await manager.async_update_credentials(id_token="renewed-id-token")
        # ...and setup, finding no client, connects directly.
        await manager.async_start()
        await asyncio.sleep(0.1)
        return network.registry.max_live, network.registry.live, network.attempts

    max_live, live, attempts = asyncio.run(scenario())

    assert (max_live, live, attempts) == (1, 1, 2)


def test_a_retry_due_while_a_credential_update_connects_opens_one_stream():
    async def scenario():
        manager, network = _manager_behind(failures=0, connect_time=0.4)
        manager._min_reconnect_interval = 0.2
        manager._schedule_retry()  # due before the update's connect finishes
        await asyncio.sleep(0.1)
        await manager.async_update_credentials(id_token="renewed-id-token")
        await asyncio.sleep(1.0)
        return network.registry.max_live, network.registry.live

    max_live, live = asyncio.run(scenario())

    assert max_live == 1
    assert live == 1
