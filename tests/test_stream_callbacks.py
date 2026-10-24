"""What the stream does with each of paho's callbacks, and with a debug log on.

``test_stream_reconnect.py`` proves recovery never opens two streams; this file
covers the rest of the paho boundary: a client really built and installed, a
message handed on (or not), a CONNACK or DISCONNECT of every kind turned into
the status the connection sensor shows, the reauth hand-off, and the token
updates that need no reconnect. Debug logging is switched on wherever a branch
only logs, so a format string that no longer matches its arguments fails here
instead of in a user's log.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import types

import pytest
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

from .test_stream_reconnect import (
    _REAL_SLEEP,
    _STREAM,
    _eventually,
    _from_the_network_thread,
    _new_manager,
)

mqtt = _STREAM.mqtt


@pytest.fixture
def debug(monkeypatch):
    monkeypatch.setattr(_STREAM, "debug_enabled", lambda: True)


@pytest.fixture
def no_waiting(monkeypatch):
    """Sleeps pass at once and every wait for a disconnect times out."""

    fast = types.ModuleType("asyncio")
    fast.__dict__.update(vars(asyncio))

    async def sleep(delay, result=None):
        await _REAL_SLEEP(0)
        return result

    async def wait_for(awaitable, timeout):
        raise TimeoutError

    fast.sleep = sleep
    fast.wait_for = wait_for
    monkeypatch.setattr(_STREAM, "asyncio", fast)


class _Reason:
    """A reason code paho has no DISCONNECT name for (134 is CONNACK-only there)."""

    def __init__(self, value: int) -> None:
        self.value = value

    def __eq__(self, other) -> bool:
        return self.value == other

    def __str__(self) -> str:
        return f"rc {self.value}"


class _Client:
    def __init__(self) -> None:
        self.subscribed: list[str] = []
        self.stopped = 0
        self.disconnects = 0

    def subscribe(self, topic):
        self.subscribed.append(topic)
        return (0, 1)

    def loop_stop(self) -> None:
        self.stopped += 1

    def disconnect(self) -> None:
        # Never acknowledged: the stop waits for it, then gives up.
        self.disconnects += 1


def _connack(code: int = 0) -> ReasonCode:
    return ReasonCode(PacketTypes.CONNACK, identifier=code)


def _watched(manager) -> tuple[list[tuple], list[str]]:
    """Record the status callback's calls and the error callback's reasons."""

    statuses: list[tuple] = []
    errors: list[str] = []

    async def status(state, reason=None):
        statuses.append((state, reason))

    async def error(reason):
        errors.append(reason)

    manager.set_status_callback(status)
    manager._error_callback = error
    return statuses, errors


# -- the client --------------------------------------------------------------


def test_a_built_client_is_installed_before_its_network_loop_starts(monkeypatch, debug):
    order: list[str] = []
    monkeypatch.setattr(mqtt.Client, "connect", lambda self, *a, **k: order.append("connect"))
    monkeypatch.setattr(mqtt.Client, "loop_start", lambda self: order.append("loop"))

    async def scenario():
        manager = _new_manager()
        await manager.async_start()
        return manager.client

    client = asyncio.run(scenario())

    assert isinstance(client, mqtt.Client)
    assert order == ["connect", "loop"]
    assert client.user_data_get() == {"topic": "gcid/+"}
    assert client.on_message is not None


def test_a_connect_that_fails_stops_its_loop_and_installs_nothing(monkeypatch, debug):
    stopped: list[None] = []

    def refuse(self, *args, **kwargs):
        raise OSError("unreachable")

    monkeypatch.setattr(mqtt.Client, "connect", refuse)
    monkeypatch.setattr(mqtt.Client, "loop_stop", lambda self: stopped.append(None))

    async def scenario():
        manager = _new_manager()
        with pytest.raises(OSError):
            await manager.async_start()
        return manager.client

    assert asyncio.run(scenario()) is None
    assert stopped == [None]


def test_a_start_soon_after_a_disconnect_waits_for_bmw_s_interval(no_waiting, debug):
    async def scenario():
        manager = _new_manager()
        started: list[None] = []
        manager._start_client = lambda: started.append(None)
        manager._last_disconnect = _STREAM.time.monotonic()
        await manager.async_start()
        return started

    assert asyncio.run(scenario()) == [None]


def test_debug_info_never_carries_the_token():
    async def scenario():
        return _new_manager().debug_info

    info = asyncio.run(scenario())
    assert info["id_token_present"] is True
    assert "id-token" not in repr(info)


# -- messages ----------------------------------------------------------------


def test_a_message_is_handed_on_only_when_it_is_json_and_someone_listens(debug):
    def message(payload: bytes):
        msg = mqtt.MQTTMessage(topic=b"gcid/WBA")
        msg.payload = payload
        return msg

    async def scenario():
        manager = _new_manager()
        received: list[dict] = []
        # Nobody listening yet: dropped.
        _from_the_network_thread(lambda: manager._handle_message(None, {}, message(b"{}")))

        async def on_message(data):
            received.append(data)

        manager.set_message_callback(on_message)
        _from_the_network_thread(lambda: manager._handle_message(None, {}, message(b"not json")))
        _from_the_network_thread(
            lambda: manager._handle_message(None, {}, message(b'{"vin": "WBA"}'))
        )
        await _eventually(lambda: received)
        await asyncio.sleep(0.01)
        return received

    assert asyncio.run(scenario()) == [{"vin": "WBA"}]


def test_a_failing_message_task_is_logged_not_lost(caplog):
    cancelled: concurrent.futures.Future = concurrent.futures.Future()
    cancelled.cancel()
    failed: concurrent.futures.Future = concurrent.futures.Future()
    failed.set_exception(ValueError("bad payload"))
    done: concurrent.futures.Future = concurrent.futures.Future()
    done.set_result(None)

    with caplog.at_level(logging.ERROR):
        for future in (cancelled, failed, done):
            _STREAM._log_future_exception(future)

    assert [r.getMessage() for r in caplog.records] == [
        "BMW CarData stream task failed: bad payload"
    ]


def test_a_subscription_is_only_logged(debug, caplog):
    async def scenario():
        with caplog.at_level(logging.DEBUG, logger=_STREAM.__name__):
            _new_manager()._handle_subscribe(None, {}, 1, [0])

    asyncio.run(scenario())
    assert "subscribed mid=1" in caplog.text


# -- CONNACK -----------------------------------------------------------------


def test_a_connect_after_a_refusal_subscribes_and_reports_the_recovery(debug, caplog):
    async def scenario():
        manager = _new_manager()
        statuses, errors = _watched(manager)
        manager._reauth_notified = True
        manager._awaiting_new_credentials = True
        manager._connect_failures = 3
        client = _Client()
        with caplog.at_level(logging.INFO):
            _from_the_network_thread(
                lambda: manager._handle_connect(client, {"topic": "gcid/+"}, None, _connack())
            )
            await _eventually(lambda: statuses and errors)
        return manager, client, statuses, errors

    manager, client, statuses, errors = asyncio.run(scenario())

    assert client.subscribed == ["gcid/+"]
    assert errors == ["recovered"]
    assert statuses == [("connected", None)]
    assert manager._awaiting_new_credentials is False
    assert manager._connect_failures == 0
    assert "reconnected after 3 failed attempts" in caplog.text


def test_a_connect_bmw_turns_down_for_another_reason_is_reported():
    async def scenario():
        manager = _new_manager()
        statuses, _errors = _watched(manager)
        _from_the_network_thread(
            lambda: manager._handle_connect(_Client(), {}, None, _connack(136))
        )
        await _eventually(lambda: statuses)
        return statuses

    assert asyncio.run(scenario()) == [("connection_failed", "Server unavailable")]


def test_a_refused_login_stops_the_stream_and_asks_for_new_credentials(no_waiting, debug):
    async def scenario():
        manager = _new_manager()
        statuses, errors = _watched(manager)
        client = _Client()
        manager._client = client
        _from_the_network_thread(lambda: manager._handle_connect(client, {}, None, _connack(134)))
        await _eventually(lambda: statuses)
        first = (list(errors), list(statuses), manager._awaiting_new_credentials)

        # Refused again while the reauth is open: stopped, not reported twice.
        statuses.clear()
        await manager._handle_unauthorized()
        return first, errors, statuses, client

    first, errors, statuses, client = asyncio.run(scenario())

    assert first == (["unauthorized"], [("unauthorized", "MQTT rc=5")], True)
    assert errors == ["unauthorized"]
    assert statuses == [("unauthorized", "MQTT rc=5")]
    assert client.stopped == 1


def test_a_second_refusal_while_the_first_is_handled_is_dropped():
    async def scenario():
        manager = _new_manager()
        _statuses, errors = _watched(manager)
        manager._unauthorized_retry_in_progress = True
        await manager._handle_unauthorized()
        return errors

    assert asyncio.run(scenario()) == []


def test_a_refusal_right_after_a_disconnect_is_retried_with_a_debug_log(debug):
    async def scenario():
        manager = _new_manager()
        retries: list[None] = []
        manager._schedule_retry = lambda: retries.append(None)
        manager._last_disconnect = _STREAM.time.monotonic()
        client = _Client()
        manager._handle_connect(client, {}, None, _connack(135))
        return retries, client

    retries, client = asyncio.run(scenario())

    assert retries == [None]
    assert client.stopped == 1


# -- DISCONNECT --------------------------------------------------------------


def test_a_clean_disconnect_is_quiet_and_reconnects(debug, caplog):
    async def scenario():
        manager = _new_manager()
        statuses, _errors = _watched(manager)
        reconnects: list[object] = []

        async def reconnect(client):
            reconnects.append(client)

        manager._async_reconnect = reconnect
        client = _Client()
        with caplog.at_level(logging.WARNING):
            _from_the_network_thread(
                lambda: manager._handle_disconnect(
                    client, {}, None, ReasonCode(PacketTypes.DISCONNECT, "Normal disconnection")
                )
            )
            await _eventually(lambda: statuses and reconnects)
        return statuses, reconnects, client

    statuses, reconnects, client = asyncio.run(scenario())

    assert reconnects == [client]
    assert statuses == [("disconnected", "Normal disconnection")]
    assert "disconnected" not in caplog.text


def test_a_transient_not_authorized_disconnect_is_retried(debug):
    async def scenario():
        manager = _new_manager()
        retries: list[None] = []
        manager._schedule_retry = lambda: retries.append(None)
        manager._handle_disconnect(
            _Client(), {}, None, ReasonCode(PacketTypes.DISCONNECT, "Not authorized")
        )
        return retries

    assert asyncio.run(scenario()) == [None]


def test_a_disconnect_for_bad_credentials_starts_the_reauth(no_waiting):
    async def scenario():
        manager = _new_manager()
        statuses, errors = _watched(manager)
        _from_the_network_thread(
            lambda: manager._handle_disconnect(_Client(), {}, None, _Reason(134))
        )
        await _eventually(lambda: errors and len(statuses) >= 2)
        return statuses, errors

    statuses, errors = asyncio.run(scenario())

    assert errors == ["unauthorized"]
    assert sorted(statuses) == [("unauthorized", "MQTT rc=5"), ("unauthorized", "rc 134")]


# -- stopping and retrying ---------------------------------------------------


def test_a_stop_bmw_never_acknowledges_still_ends_the_client(no_waiting, debug):
    async def scenario():
        manager = _new_manager()
        client = _Client()
        manager._client = client
        await manager.async_stop()
        return manager, client

    manager, client = asyncio.run(scenario())

    assert client.disconnects == 1
    assert client.stopped == 1
    assert manager.client is None
    assert manager._disconnect_future is None


def test_a_retry_waits_out_a_disconnect_that_never_comes(no_waiting, debug):
    async def scenario():
        manager = _new_manager()
        started: list[None] = []
        manager._start_client = lambda: started.append(None)
        manager._disconnect_future = asyncio.get_running_loop().create_future()
        await manager._async_retry(0)
        return manager, started

    manager, started = asyncio.run(scenario())

    assert started == [None]
    assert manager._disconnect_future is None


def test_every_further_failed_attempt_is_only_a_debug_line(debug, caplog):
    async def scenario():
        manager = _new_manager()
        manager._schedule_retry = lambda: None
        with caplog.at_level(logging.WARNING):
            manager._connect_failed(OSError("first"))
            manager._connect_failed(OSError("second"))
        return manager._connect_failures

    assert asyncio.run(scenario()) == 2
    assert "first" in caplog.text
    assert "second" not in caplog.text


# -- credentials -------------------------------------------------------------


def test_credentials_that_change_nothing_reconnect_nothing():
    async def scenario():
        manager = _new_manager()
        starts: list[None] = []
        manager._start_client = lambda: starts.append(None)

        await manager.async_update_credentials()
        await manager.async_update_token("id-token")
        # Waiting for new credentials, but a client is already up: leave it.
        manager._awaiting_new_credentials = True
        manager._client = _Client()
        await manager.async_update_token("id-token")
        return manager, starts

    manager, starts = asyncio.run(scenario())

    assert starts == []
    assert manager._awaiting_new_credentials is False


def test_a_new_gcid_reconnects_under_it(no_waiting):
    async def scenario():
        manager = _new_manager()
        starts: list[str] = []
        manager._start_client = lambda: starts.append(manager._gcid)
        await manager.async_update_credentials(gcid="gcid-2")
        return starts, manager.debug_info["topic"]

    assert asyncio.run(scenario()) == (["gcid-2"], "gcid-2/+")
