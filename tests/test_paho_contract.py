"""The part of paho-mqtt the stream depends on, pinned in one place.

``paho-mqtt`` is the one shipped dependency nothing else watches: Home Assistant
pins it, Dependabot never sees it, and the stream tests fake the network
boundary. A paho release that renamed a call or renumbered a reason code would
reach users before any test noticed. These tests fail first instead, and the
weekly canary in ``.github/workflows/paho-canary.yml`` runs them against the
newest paho on PyPI (pre-releases included), before Home Assistant moves to it.

Deliberately not guarded by capping the requirement at ``<3``: Home Assistant
pins paho in its own constraints, so on the day it moves to 3.x a cap would make
our requirement unsatisfiable and the integration would not load at all.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from .test_stream_reconnect import _STREAM

mqtt = _STREAM.mqtt


def _client() -> mqtt.Client:
    # The exact arguments ``_start_client`` passes.
    return mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="gcid",
        clean_session=True,
        userdata={"topic": "gcid/+"},
        protocol=mqtt.MQTTv311,
        transport="tcp",
    )


def test_the_client_builds_with_the_stream_s_arguments():
    client = _client()
    assert client.user_data_get() == {"topic": "gcid/+"}


@pytest.mark.parametrize(
    "method",
    [
        "username_pw_set",
        "tls_set_context",
        "tls_insecure_set",
        "reconnect_delay_set",
        "connect",
        "loop_start",
        "loop_stop",
        "subscribe",
        "disconnect",
    ],
)
def test_every_call_the_stream_makes_exists(method):
    assert callable(getattr(_client(), method))


def test_loop_stop_takes_no_arguments():
    # The stream calls ``loop_stop()`` bare, and its test fakes mirror that
    # signature. If paho grows or renames arguments, the fakes have to follow.
    assert list(inspect.signature(mqtt.Client.loop_stop).parameters) == ["self"]


@pytest.mark.parametrize(
    ("connack", "expected"),
    [
        ("CONNACK_REFUSED_BAD_USERNAME_PASSWORD", _STREAM._RC_BAD_CREDENTIALS),
        ("CONNACK_REFUSED_NOT_AUTHORIZED", _STREAM._RC_NOT_AUTHORIZED),
    ],
)
def test_bmw_s_refusals_arrive_as_the_codes_the_stream_checks(connack, expected):
    # BMW speaks MQTT 3.1.1; VERSION2 callbacks hand its numeric CONNACK codes
    # over as MQTTv5 reason codes. The token renewal hangs on this mapping.
    from paho.mqtt.enums import ConnackCode

    reason = mqtt.convert_connack_rc_to_reason_code(getattr(ConnackCode, connack))
    assert reason.value == expected
    assert reason != 0


def test_success_and_a_clean_disconnect_compare_equal_to_zero():
    from paho.mqtt.enums import ConnackCode

    accepted = mqtt.convert_connack_rc_to_reason_code(ConnackCode.CONNACK_ACCEPTED)
    assert accepted == 0
    clean = mqtt.convert_disconnect_error_code_to_reason_code(mqtt.MQTT_ERR_SUCCESS)
    assert clean == 0
    assert str(clean)


def test_a_refusal_right_after_a_disconnect_schedules_a_retry(monkeypatch):
    # BMW refuses a reconnect within ~10 s of a disconnect with rc 5. The stream
    # stops the refused client from paho's own network thread and retries. That
    # stop once passed ``force=True``, which paho 2.x's ``loop_stop`` does not
    # take: the TypeError killed the thread before the retry was scheduled, and
    # the stream stayed down until the next token renewal. The fakes in
    # test_stream_reconnect.py accepted the argument, so only a real paho
    # client shows it.
    from paho.mqtt.packettypes import PacketTypes
    from paho.mqtt.reasoncodes import ReasonCode

    from .test_stream_reconnect import _new_manager

    async def scenario() -> tuple[list[None], object]:
        manager = _new_manager()
        retries: list[None] = []
        monkeypatch.setattr(manager, "_schedule_retry", lambda: retries.append(None))
        client = _client()
        manager._client = client
        manager._last_disconnect = _STREAM.time.monotonic()
        manager._handle_connect(
            client,
            client.user_data_get(),
            None,
            ReasonCode(PacketTypes.CONNACK, identifier=_STREAM._RC_NOT_AUTHORIZED),
        )
        return retries, manager.client

    retries, client_left = asyncio.run(scenario())

    assert retries == [None]
    assert client_left is None
