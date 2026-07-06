"""Unit tests for the CarData REST client (api.py)."""

from __future__ import annotations

import asyncio

import pytest

from .conftest import FakeResponse, FakeSession, load_module

api = load_module("api")


def _run(coro):
    return asyncio.run(coro)


def test_extract_error_id_finds_cu_code():
    assert api._extract_error_id({"errorId": "CU-429"}) == "CU-429"
    assert api._extract_error_id({"code": "CU-104"}) == "CU-104"
    assert api._extract_error_id({"message": "boom"}) is None
    assert api._extract_error_id("not a dict") is None


def test_request_json_success():
    session = FakeSession([FakeResponse(200, {"hello": "world"})])
    result = _run(api.async_request_json(session, "GET", "/x", "tok"))
    assert result == {"hello": "world"}
    # auth + version headers must be present on every request
    headers = session.calls[0]["headers"]
    assert headers["Authorization"] == "Bearer tok"
    assert headers["x-version"] == "v1"


def test_request_json_error_raises_with_error_id():
    session = FakeSession([FakeResponse(429, {"errorId": "CU-429"})])
    with pytest.raises(api.CardataApiError) as exc:
        _run(api.async_request_json(session, "GET", "/x", "tok"))
    assert exc.value.status == 429
    assert exc.value.error_id == "CU-429"


def test_charging_history_follows_pagination():
    session = FakeSession(
        [
            FakeResponse(200, {"data": [1, 2], "next_token": "abc"}),
            FakeResponse(200, {"data": [3], "next_token": ""}),
        ]
    )
    result = _run(api.async_get_charging_history(session, "tok", "VIN0"))
    assert result["data"] == [1, 2, 3]
    assert result["pages"] == 2
    # second call must carry the nextToken query parameter
    assert session.calls[1]["params"]["nextToken"] == "abc"


def test_location_settings_single_page():
    session = FakeSession([FakeResponse(200, {"data": [{"x": 1}]})])
    result = _run(api.async_get_location_based_charging_settings(session, "tok", "VIN0"))
    assert result["data"] == [{"x": 1}]
    assert result["pages"] == 1


def test_vehicle_image_returns_bytes():
    session = FakeSession(
        [FakeResponse(200, b"\x89PNG", headers={"Content-Type": "image/png"})]
    )
    data, content_type = _run(api.async_get_vehicle_image(session, "tok", "VIN0"))
    assert data == b"\x89PNG"
    assert content_type == "image/png"
