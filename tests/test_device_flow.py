"""Unit tests for OAuth device-flow helpers (device_flow.py)."""

from __future__ import annotations

import asyncio
from typing import Any, List

import pytest

from .conftest import load_module

device_flow = load_module("device_flow")


class _PollResponse:
    def __init__(self, status: int, body: Any, headers: dict | None = None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    async def __aenter__(self) -> "_PollResponse":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def json(self, content_type=None) -> Any:
        return self._body


class _PollSession:
    """Replays a queue of responses for ``session.post`` calls."""

    def __init__(self, responses: List[_PollResponse]):
        self._responses = list(responses)
        self.calls = 0

    def post(self, url, **kwargs):
        self.calls += 1
        return self._responses.pop(0)


async def _poll(session: _PollSession):
    return await device_flow.poll_for_tokens(
        session,
        client_id="cid",
        device_code="dc",
        code_verifier="cv",
        interval=0,
        timeout=60,
    )


def test_safe_error_prefers_description():
    msg = device_flow._safe_error(400, {"error": "invalid_grant", "error_description": "bad"})
    assert "bad" in msg
    assert "400" in msg


def test_safe_error_falls_back_to_error_code():
    msg = device_flow._safe_error(400, {"error": "authorization_pending"})
    assert "authorization_pending" in msg


def test_safe_error_never_leaks_tokens():
    # A (hypothetical) body containing a token must not be echoed verbatim.
    body = {"access_token": "SECRET-TOKEN", "error": "invalid_grant"}
    msg = device_flow._safe_error(400, body)
    assert "SECRET-TOKEN" not in msg


def test_safe_error_handles_non_dict():
    assert "non-JSON" in device_flow._safe_error(500, "<html>oops</html>")


def test_poll_retries_transient_500(monkeypatch):
    """A transient BMW 500 must not abort the flow; polling continues to success."""

    monkeypatch.setattr(device_flow.asyncio, "sleep", _no_sleep)
    session = _PollSession(
        [
            _PollResponse(500, {"error": "internal_error", "error_description": "oops"}),
            _PollResponse(500, None),  # non-JSON-ish body, still transient
            _PollResponse(200, {"access_token": "ok"}),
        ]
    )
    result = asyncio.run(_poll(session))
    assert result == {"access_token": "ok"}
    assert session.calls == 3


def test_poll_retries_network_error(monkeypatch):
    """A transient network error during the POST is retried, not fatal."""

    monkeypatch.setattr(device_flow.asyncio, "sleep", _no_sleep)
    session = _PollSession(
        [
            _RaisingResponse(device_flow.aiohttp.ClientError("boom")),
            _PollResponse(200, {"access_token": "ok"}),
        ]
    )
    result = asyncio.run(_poll(session))
    assert result == {"access_token": "ok"}


def test_poll_gives_up_after_persistent_500(monkeypatch):
    """Persistent server errors eventually surface as a CardataAuthError."""

    monkeypatch.setattr(device_flow.asyncio, "sleep", _no_sleep)
    session = _PollSession([_PollResponse(503, {"error": "down"}) for _ in range(20)])
    with pytest.raises(device_flow.CardataAuthError):
        asyncio.run(_poll(session))


def test_poll_fatal_error_not_retried(monkeypatch):
    """A genuine terminal auth error (e.g. expired_token) fails immediately."""

    monkeypatch.setattr(device_flow.asyncio, "sleep", _no_sleep)
    session = _PollSession([_PollResponse(400, {"error": "expired_token"})])
    with pytest.raises(device_flow.CardataAuthError):
        asyncio.run(_poll(session))
    assert session.calls == 1


def test_poll_access_denied_within_grace_then_success(monkeypatch):
    """A just-approved grant that briefly reports access_denied still succeeds.

    BMW can take up to ~1 minute for the grant to propagate; access_denied during
    that window must not abort the flow.
    """

    monkeypatch.setattr(device_flow.asyncio, "sleep", _no_sleep)
    session = _PollSession(
        [
            _PollResponse(403, {"error": "access_denied"}),
            _PollResponse(403, {"error": "access_denied"}),
            _PollResponse(200, {"access_token": "ok"}),
        ]
    )
    result = asyncio.run(_poll(session))
    assert result == {"access_token": "ok"}
    assert session.calls == 3


def test_poll_access_denied_persists_then_fails(monkeypatch):
    """A real decline (past the grace window) surfaces structured detail for the UI."""

    monkeypatch.setattr(device_flow.asyncio, "sleep", _no_sleep)
    # Collapse the grace window so a persistent access_denied is terminal at once.
    monkeypatch.setattr(device_flow, "ACCESS_DENIED_GRACE", -1)
    session = _PollSession(
        [
            _PollResponse(
                403,
                {
                    "error": "access_denied",
                    "error_description": "The user has declined authorization",
                },
                headers={"x-correlation-id": "abc-123"},
            )
        ]
    )
    with pytest.raises(device_flow.CardataAuthError) as excinfo:
        asyncio.run(_poll(session))
    err = excinfo.value
    assert err.status == 403
    assert err.error_code == "access_denied"
    assert err.error_description == "The user has declined authorization"
    assert err.correlation_id == "abc-123"
    assert "abc-123" in str(err)


def test_poll_slow_down_raises_interval(monkeypatch):
    """slow_down permanently increases the polling interval (don't flood BMW)."""

    sleeps: list = []

    async def _record_sleep(seconds, *_a, **_k):
        sleeps.append(seconds)

    monkeypatch.setattr(device_flow.asyncio, "sleep", _record_sleep)
    session = _PollSession(
        [
            _PollResponse(400, {"error": "slow_down"}),
            _PollResponse(400, {"error": "authorization_pending"}),
            _PollResponse(200, {"access_token": "ok"}),
        ]
    )
    result = asyncio.run(_poll(session))
    assert result == {"access_token": "ok"}
    # interval starts at the floor (5); slow_down bumps it to 10 for all later waits.
    assert sleeps == [10, 10]


async def _no_sleep(*_args, **_kwargs):
    return None


class _RaisingResponse:
    def __init__(self, exc: Exception):
        self._exc = exc

    def __enter__(self):  # pragma: no cover - not used as sync cm
        raise self._exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *exc) -> bool:
        return False
