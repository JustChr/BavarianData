"""Reverse geocoding: the consent gate, the cache, and Nominatim's usage policy.

``format_address`` was already covered. The ``ReverseGeocoder`` around it was
not, and it is the half that carries the promises:

* **Consent.** Reverse geocoding is the one place a coordinate leaves the box.
  It is off by default, and ``enabled`` has to mean *nothing is sent* -- not
  "sent but discarded". A regression here is a privacy incident, not a bug.
* **A stranger's free service.** Nominatim's policy is one request per second
  with a real, contactable User-Agent. Breaking it gets every user of this
  integration blocked, not just whoever broke it.
* **Never breaking a trip.** A missing address must leave the trip recorded
  without one; no network failure may propagate.

The rate limit is asserted by capturing what the code asks to sleep for, so the
suite does not actually wait a second per test.
"""

from __future__ import annotations

import asyncio

import pytest

from .conftest import load_module

geocoding = load_module("history.geocoding")

ReverseGeocoder = geocoding.ReverseGeocoder
format_address = geocoding.format_address
MIN_INTERVAL_S = geocoding.MIN_INTERVAL_S

MUNICH = (48.137154, 11.576124)
BERLIN = (52.520008, 13.404954)

PAYLOAD = {
    "address": {"road": "Marienplatz", "city": "Muenchen"},
    "display_name": "Marienplatz, Altstadt, Muenchen, Bayern, Germany",
}
LABEL = "Marienplatz, Muenchen"


class _Response:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload


class _Broken(_Response):
    async def json(self):
        raise ValueError("not json")


class _Session:
    """Minimal stand-in for the aiohttp session the coordinator supplies."""

    def __init__(self, *responses, raises=None):
        self._responses = list(responses)
        self._raises = raises
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self._raises is not None:
            raise self._raises
        return self._responses.pop(0)


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# Consent
# --------------------------------------------------------------------------


def test_disabled_sends_nothing_at_all():
    """Not "sends and discards" -- sends nothing. This is the privacy promise."""

    session = _Session(_Response(200, PAYLOAD))
    geo = ReverseGeocoder(session, enabled=False)
    assert _run(geo.resolve(*MUNICH)) is None
    assert session.calls == [], "a coordinate left the box with the feature off"


def test_it_is_off_unless_asked_for():
    assert ReverseGeocoder(_Session()).enabled is False


def test_without_a_session_nothing_is_attempted():
    geo = ReverseGeocoder(None, enabled=True)
    assert _run(geo.resolve(*MUNICH)) is None


# --------------------------------------------------------------------------
# Nominatim's usage policy
# --------------------------------------------------------------------------


def test_the_request_identifies_itself_as_nominatim_requires():
    session = _Session(_Response(200, PAYLOAD))
    geo = ReverseGeocoder(session, enabled=True)
    _run(geo.resolve(*MUNICH))
    agent = session.calls[0]["headers"]["User-Agent"]
    assert "BavarianData" in agent
    assert "github.com" in agent, "the policy wants a contactable agent"


def test_consecutive_lookups_wait_out_the_rate_limit(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(geocoding.asyncio, "sleep", fake_sleep)
    session = _Session(_Response(200, PAYLOAD), _Response(200, PAYLOAD))
    geo = ReverseGeocoder(session, enabled=True)

    async def two_different_places():
        await geo.resolve(*MUNICH)
        await geo.resolve(*BERLIN)

    _run(two_different_places())
    assert slept, "the second lookup did not wait"
    assert max(slept) <= MIN_INTERVAL_S


def test_a_repeat_visit_is_served_from_memory():
    session = _Session(_Response(200, PAYLOAD))
    geo = ReverseGeocoder(session, enabled=True)

    async def twice():
        return await geo.resolve(*MUNICH), await geo.resolve(*MUNICH)

    first, second = _run(twice())
    assert first == second == LABEL
    assert len(session.calls) == 1, "the same place was queried twice"


def test_nearby_points_share_a_cache_entry():
    """The grid is ~110 m: the same street must not be queried twice."""

    session = _Session(_Response(200, PAYLOAD))
    geo = ReverseGeocoder(session, enabled=True)

    async def two_nearby():
        await geo.resolve(48.137154, 11.576124)
        await geo.resolve(48.137160, 11.576130)

    _run(two_nearby())
    assert len(session.calls) == 1


def test_a_failed_lookup_is_cached_too():
    """Otherwise an unresolvable spot is re-queried on every trip through it."""

    session = _Session(_Response(404, None))
    geo = ReverseGeocoder(session, enabled=True)

    async def twice():
        await geo.resolve(*MUNICH)
        await geo.resolve(*MUNICH)

    _run(twice())
    assert len(session.calls) == 1


# --------------------------------------------------------------------------
# Failure must never reach the trip
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "session",
    [
        _Session(_Response(500, None)),
        _Session(raises=OSError("no route to host")),
        _Session(_Broken(200, None)),
    ],
    ids=["http-error", "network-error", "unparseable-body"],
)
def test_a_failure_yields_no_address_and_no_exception(session):
    geo = ReverseGeocoder(session, enabled=True)
    assert _run(geo.resolve(*MUNICH)) is None


# --------------------------------------------------------------------------
# Privacy at rest
# --------------------------------------------------------------------------


def test_the_cache_never_reaches_disk():
    """Coordinates are the one thing this module holds; they stay in memory."""

    text = geocoding.__file__ and open(geocoding.__file__, encoding="utf-8").read()
    for forbidden in ("json.dump", "Store(", "async_save", "write_text"):
        assert forbidden not in text, f"geocoding must not persist anything ({forbidden})"


# --------------------------------------------------------------------------
# Label formatting, the branches the existing tests left
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"address": {"road": "Marienplatz", "city": "Muenchen"}}, LABEL),
        ({"address": {"road": "Marienplatz"}}, "Marienplatz"),
        ({"address": {"village": "Kleindorf"}}, "Kleindorf"),
        (
            {"name": "Olympiapark", "address": {"suburb": "Milbertshofen"}},
            "Olympiapark, Milbertshofen",
        ),
        ({"display_name": "A, B, C, D"}, "A, B"),
        ({"address": {}}, None),
        ({}, None),
        (None, None),
    ],
)
def test_the_label_falls_back_sensibly(payload, expected):
    assert format_address(payload) == expected
