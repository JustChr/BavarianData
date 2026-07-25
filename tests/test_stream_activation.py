"""Tests for the portal stream-attribute activation client.

Home Assistant-free: they drive :mod:`stream_activation` against a fake aiohttp
session, verifying the request it builds (URL, headers, flat body), the
set/replace success path, the read-before-write short-circuit and the error
classification -- without any network access.
"""

from __future__ import annotations

import asyncio

import pytest

from .conftest import FakeResponse, load_module

SA = load_module("stream_activation")


class _FakeGetPostSession:
    """Records .get/.post calls and replays queued FakeResponse objects."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self._responses.pop(0)

    def post(self, url, **kwargs):
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return self._responses.pop(0)


def _run(coro):
    return asyncio.run(coro)


def _session():
    return SA.PortalSession(
        base_url="https://www.bmw.at/",  # trailing slash normalized away
        locale="de-at",
        cookie="gcdmToken=secret; ak_bmsc=secret",
    )


MAPPED = "90d3dd3e0ba0ea99abc"


# --- pure helpers ---------------------------------------------------------------


def test_normalize_dedupes_trims_and_sorts():
    out = SA.normalize_attributes(
        [" vehicle.isMoving ", "vehicle.a", "vehicle.a", "", "  "]
    )
    assert out == ["vehicle.a", "vehicle.isMoving"]


def test_default_attributes_sorted_unique_nonempty():
    attrs = SA.DEFAULT_STREAM_ATTRIBUTES
    assert attrs, "expected a non-empty default"
    assert attrs == sorted(set(attrs))
    assert all(a.startswith("vehicle.") for a in attrs)


def test_session_urls_and_headers_match_portal_contract():
    sess = _session()
    assert sess.base_url == "https://www.bmw.at"  # normalized
    assert sess.stream_url(MAPPED) == (
        f"https://www.bmw.at/de-at/utilities/bmw/api/cd/streams/{MAPPED}"
    )
    headers = sess.headers(MAPPED)
    assert headers["Accept"] == "application/json"
    assert headers["Content-Type"] == "application/json"
    assert headers["Origin"] == "https://www.bmw.at"
    assert headers["Referer"] == (
        f"https://www.bmw.at/de-at/mybmw/mapped-vehicle/{MAPPED}/cardata/stream-setup"
    )
    assert headers["x-ocp-stage"] == "prod"
    assert headers["Cookie"] == "gcdmToken=secret; ak_bmsc=secret"
    # Browser-shaped headers are load-bearing: Akamai bot-defense stalls a
    # request without them (verified against the live endpoint).
    assert headers["Sec-Fetch-Dest"] == "empty"
    assert headers["Sec-Fetch-Mode"] == "cors"
    assert headers["Sec-Fetch-Site"] == "same-origin"
    assert headers["Accept-Language"]
    # Only codecs aiohttp always decodes, so the body is never undecodable.
    assert headers["Accept-Encoding"] == "gzip, deflate"


# --- set/replace ----------------------------------------------------------------


def test_set_posts_flat_body_and_reports_counts():
    # skip_if_unchanged=False so only the POST is issued.
    accepted = ["vehicle.a", "vehicle.b", "vehicle.isMoving"]
    http = _FakeGetPostSession(
        [FakeResponse(201, {"data": {"attributes": accepted}, "success": True})]
    )
    client = SA.PortalStreamClient(http, _session())
    result = _run(
        client.async_set_stream_attributes(
            MAPPED,
            ["vehicle.isMoving", "vehicle.b", "vehicle.a", "vehicle.a"],
            skip_if_unchanged=False,
        )
    )
    assert len(http.calls) == 1
    call = http.calls[0]
    assert call["method"] == "POST"
    # Flat body, deduped + sorted, NOT wrapped in {"data": ...}.
    assert call["json"] == {"attributes": ["vehicle.a", "vehicle.b", "vehicle.isMoving"]}
    assert result.requested_count == 3
    assert result.accepted_count == 3
    assert result.unchanged is False


def test_skip_if_unchanged_short_circuits_without_posting():
    current = ["vehicle.a", "vehicle.b"]
    http = _FakeGetPostSession(
        [FakeResponse(200, {"data": {"attributes": current}, "success": True})]
    )
    client = SA.PortalStreamClient(http, _session())
    result = _run(
        client.async_set_stream_attributes(MAPPED, ["vehicle.b", "vehicle.a"])
    )
    # Only the GET pre-read happened; no POST.
    assert [c["method"] for c in http.calls] == ["GET"]
    assert result.unchanged is True
    assert result.accepted_count == 2


def test_changed_selection_reads_then_posts():
    http = _FakeGetPostSession(
        [
            FakeResponse(200, {"data": {"attributes": ["vehicle.a"]}, "success": True}),
            FakeResponse(
                201, {"data": {"attributes": ["vehicle.a", "vehicle.b"]}, "success": True}
            ),
        ]
    )
    client = SA.PortalStreamClient(http, _session())
    result = _run(
        client.async_set_stream_attributes(MAPPED, ["vehicle.a", "vehicle.b"])
    )
    assert [c["method"] for c in http.calls] == ["GET", "POST"]
    assert result.accepted_count == 2


def test_get_returns_selection_and_connection_facts():
    http = _FakeGetPostSession(
        [
            FakeResponse(
                200,
                {
                    "data": {
                        "attributes": ["vehicle.a"],
                        "host": "broker",
                        "port": 9000,
                        "topic": "t",
                        "username": "u",
                    },
                    "success": True,
                },
            )
        ]
    )
    client = SA.PortalStreamClient(http, _session())
    sel = _run(client.async_get_stream_attributes(MAPPED))
    assert sel.attributes == ["vehicle.a"]
    assert sel.host == "broker"
    assert sel.port == 9000
    # A read carries no body.
    assert "json" not in http.calls[0]
    assert http.calls[0]["params"] == {"includeAttributes": "true"}


# --- error classification -------------------------------------------------------


@pytest.mark.parametrize(
    "status,kind,retryable",
    [
        (401, "auth", False),
        (403, "auth", False),
        (400, "validation", False),
        (404, "not_found", False),
        (500, "server", True),
        (503, "server", True),
    ],
)
def test_error_status_is_classified(status, kind, retryable):
    http = _FakeGetPostSession([FakeResponse(status, {"message": "nope"})])
    client = SA.PortalStreamClient(http, _session())
    with pytest.raises(SA.StreamActivationError) as excinfo:
        _run(
            client.async_set_stream_attributes(
                MAPPED, ["vehicle.a"], skip_if_unchanged=False
            )
        )
    assert excinfo.value.kind == kind
    assert excinfo.value.retryable is retryable
    assert excinfo.value.status == status


def test_2xx_with_success_false_is_a_validation_error():
    http = _FakeGetPostSession(
        [FakeResponse(201, {"success": False, "message": "bad attribute x"})]
    )
    client = SA.PortalStreamClient(http, _session())
    with pytest.raises(SA.StreamActivationError) as excinfo:
        _run(
            client.async_set_stream_attributes(
                MAPPED, ["vehicle.a"], skip_if_unchanged=False
            )
        )
    assert excinfo.value.kind == "validation"


def test_failed_preread_falls_through_to_post():
    # A 500 on the pre-read must not block the write we were asked to do.
    http = _FakeGetPostSession(
        [
            FakeResponse(500, {"message": "flaky"}),
            FakeResponse(201, {"data": {"attributes": ["vehicle.a"]}, "success": True}),
        ]
    )
    client = SA.PortalStreamClient(http, _session())
    result = _run(client.async_set_stream_attributes(MAPPED, ["vehicle.a"]))
    assert [c["method"] for c in http.calls] == ["GET", "POST"]
    assert result.accepted_count == 1


class _TimeoutSession:
    """A session whose .get/.post raise asyncio.TimeoutError, like a stalled call."""

    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append("GET")
        raise asyncio.TimeoutError

    def post(self, url, **kwargs):
        self.calls.append("POST")
        raise asyncio.TimeoutError


def test_timeout_is_classified_not_leaked():
    # A stalled portal (Akamai) is the common failure mode; it must surface as a
    # retryable network StreamActivationError, never a raw asyncio.TimeoutError.
    client = SA.PortalStreamClient(_TimeoutSession(), _session())
    with pytest.raises(SA.StreamActivationError) as excinfo:
        _run(
            client.async_set_stream_attributes(
                MAPPED, ["vehicle.a"], skip_if_unchanged=False
            )
        )
    assert excinfo.value.kind == "network"
    assert excinfo.value.retryable is True


def test_cookie_never_appears_in_error_text():
    http = _FakeGetPostSession([FakeResponse(401, {"message": "denied"})])
    client = SA.PortalStreamClient(http, _session())
    with pytest.raises(SA.StreamActivationError) as excinfo:
        _run(
            client.async_set_stream_attributes(
                MAPPED, ["vehicle.a"], skip_if_unchanged=False
            )
        )
    assert "secret" not in str(excinfo.value)
