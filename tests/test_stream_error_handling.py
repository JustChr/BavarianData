"""A stream waiting for credentials must neither stall nor start reauth on a network error.

When BMW's broker refuses the MQTT login (typically because the ID token expired
during an outage that outlasted it), the stream stops and waits for new
credentials, and ``async_handle_stream_error`` refreshes the token. If that refresh
fails because the network is still unreliable, the credentials are not wrong:
starting a reauth flow would ask the user to log in again for nothing, and giving
up would leave the stream waiting for the next regular refresh, up to 45 minutes
later. It has to ask the refresh loop to try again soon instead.

The handler is ``reauth.ReauthGate.stream_error``, run here against a host that
records the flows it starts and the notices it dismisses.
"""

from __future__ import annotations

import asyncio

import aiohttp
import pytest

from .conftest import load_module

reauth = load_module("reauth")
_DEVICE_FLOW = load_module("device_flow")


class _Tokens:
    """Stands in for the entry's ``TokenKeeper``; ``error`` makes renewal fail."""

    def __init__(self, error: Exception | None) -> None:
        self._error = error
        self.renewed = 0

    async def renew(self) -> None:
        self.renewed += 1
        if self._error is not None:
            raise self._error


class _Host:
    def __init__(self, error: Exception | None = None) -> None:
        self.entry_id = "entry-id"
        self.tokens = _Tokens(error)
        self.refresh_wake = asyncio.Event()
        self.started: list[str] = []
        self.aborted: list[str] = []
        self.dismissed = 0

    async def start_reauth(self) -> str:
        self.started.append("reauth")
        return f"flow-{len(self.started)}"

    async def abort_flow(self, flow_id: str) -> None:
        self.aborted.append(flow_id)

    def dismiss_notice(self) -> None:
        self.dismissed += 1


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def _scenario(refresh_error: Exception):
    host = _Host(refresh_error)
    gate = reauth.ReauthGate()
    asyncio.run(gate.stream_error(host, "unauthorized"))
    return host, gate


def test_a_network_error_during_the_refresh_asks_for_a_retry_instead_of_reauth():
    host, _gate = _scenario(
        aiohttp.ClientConnectionError("Cannot connect to host customer.bmwgroup.com:443")
    )

    assert host.started == []
    assert host.refresh_wake.is_set()


def test_a_refresh_bmw_rejects_still_starts_reauth():
    host, gate = _scenario(
        _DEVICE_FLOW.CardataAuthError(
            "Token refresh failed (400: invalid_grant)", status=400, error_code="invalid_grant"
        )
    )

    # Home Assistant posts the notice for a reauth flow itself, naming the
    # entry; the gate posts none of its own.
    assert host.started == ["reauth"]
    assert gate.in_progress and gate.flow_id == "flow-1"
    assert not host.refresh_wake.is_set()


def test_bmw_having_a_bad_moment_is_retried_not_reauthorized():
    """A 503 from the token endpoint says nothing about the login.

    Before refresh errors carried a status it looked like a rejection, and a
    stream refused during a BMW hiccup sent the user off to re-authorize.
    """

    host, _gate = _scenario(
        _DEVICE_FLOW.CardataAuthError("Token refresh failed (503: unavailable)", status=503)
    )

    assert host.started == []
    assert host.refresh_wake.is_set()


def test_a_refresh_that_works_ends_it_without_a_flow():
    host, gate = _scenario(None)

    assert host.tokens.renewed == 1
    assert host.started == [] and not gate.in_progress and not gate.pending


def test_a_second_unauthorized_while_a_flow_is_open_is_ignored():
    host, gate = _scenario(_DEVICE_FLOW.CardataAuthError("invalid_grant", status=400))
    asyncio.run(gate.stream_error(host, "unauthorized"))

    assert host.started == ["reauth"]
    assert host.tokens.renewed == 1


def test_unauthorized_again_soon_after_a_refresh_goes_straight_to_reauth():
    """A refresh that "worked" but did not satisfy the broker is not tried twice."""

    clock = _Clock()
    host = _Host(None)
    gate = reauth.ReauthGate(clock=clock)

    asyncio.run(gate.stream_error(host, "unauthorized"))
    clock.now += 5
    asyncio.run(gate.stream_error(host, "unauthorized"))

    assert host.tokens.renewed == 1
    assert host.started == ["reauth"]
    assert not gate.pending, "the flow it started took the pending reauth"


def test_a_recovered_stream_closes_the_flow_and_its_notice():
    host, gate = _scenario(_DEVICE_FLOW.CardataAuthError("invalid_grant", status=400))
    asyncio.run(gate.stream_error(host, "recovered"))

    assert host.aborted == ["flow-1"] and host.dismissed == 1
    assert not gate.in_progress and gate.flow_id is None


def test_a_recovered_stream_without_a_flow_touches_nothing():
    host = _Host()
    gate = reauth.ReauthGate()
    gate.pending = True
    asyncio.run(gate.stream_error(host, "recovered"))

    assert host.dismissed == 0 and not gate.pending


def test_a_new_request_replaces_a_stale_flow():
    clock = _Clock()
    host = _Host()
    gate = reauth.ReauthGate(clock=clock)
    gate.flow_id = "old"

    asyncio.run(gate.request(host, "Token refresh rejected"))

    assert host.aborted == ["old"] and gate.flow_id == "flow-1"


def _watched(gate) -> list[bool]:
    heard: list[bool] = []
    gate.on_refused = heard.append
    return heard


def test_a_login_bmw_rejects_is_refused_until_a_sign_in():
    """The cars' entities go unavailable on it: no data can arrive meanwhile."""

    host, gate = _scenario(_DEVICE_FLOW.CardataAuthError("invalid_grant", status=400))
    heard = _watched(gate)
    assert gate.refused

    asyncio.run(gate.stream_error(host, "unauthorized"))  # a second report changes nothing
    gate.authorized()

    assert not gate.refused and heard == [False]


@pytest.mark.parametrize("ending", ["renewed", "recovered"])
def test_a_working_refresh_or_stream_ends_the_refusal(ending):
    host = _Host()
    gate = reauth.ReauthGate()
    heard = _watched(gate)
    asyncio.run(gate.request(host, "Token refresh rejected"))

    if ending == "renewed":
        gate.renewed()
    else:
        asyncio.run(gate.stream_error(host, "recovered"))

    assert heard == [True, False] and not gate.refused


def test_a_network_error_refuses_nothing():
    host, gate = _scenario(aiohttp.ClientError("timeout"))
    assert not gate.refused


def test_a_sign_in_the_user_starts_refuses_nothing():
    gate = reauth.ReauthGate()
    heard = _watched(gate)
    gate.started_by_user()
    gate.abandoned()

    assert not gate.refused and heard == []


def test_the_config_flow_resets_and_sets_the_gate():
    gate = reauth.ReauthGate()
    gate.started_by_user()
    assert gate.in_progress and gate.pending

    gate.flow_id = "flow"
    gate.abandoned()
    assert not gate.in_progress and gate.flow_id is None and gate.pending

    gate.last_reauth_attempt = gate.last_refresh_attempt = 5.0
    gate.authorized()
    assert (gate.pending, gate.last_reauth_attempt, gate.last_refresh_attempt) == (False, 0, 0)


def test_a_refresh_answer_without_an_id_token_is_refused():
    with pytest.raises(KeyError):
        reauth.renewed_entry_data({"refresh_token": "r"}, {"access_token": "a"}, 1.0)


def test_a_refresh_keeps_what_bmw_left_out():
    data = reauth.renewed_entry_data(
        {"refresh_token": "r", "scope": "s", "token_type": "Bearer", "gcid": "g"},
        {"id_token": "i", "access_token": "a", "expires_in": 3600},
        123.0,
    )

    assert data == {
        "refresh_token": "r",
        "scope": "s",
        "token_type": "Bearer",
        "gcid": "g",
        "id_token": "i",
        "access_token": "a",
        "expires_in": 3600,
        "received_at": 123.0,
    }
