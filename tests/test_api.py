"""Unit tests for the CarData REST client (api.py)."""

from __future__ import annotations

import asyncio

import aiohttp
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


def test_an_error_names_bmws_reason_without_the_vin():
    # Issue #130: a bare "HTTP 400" left nobody able to say what BMW objected to.
    body = {
        "exveErrorId": "CU-105",
        "exveErrorMsg": "No permission for specified containerId",
        "exveNote": "Container of WBA12345678901234 is not allocated to the user.",
    }
    session = FakeSession([FakeResponse(400, body)])
    with pytest.raises(api.CardataApiError) as exc:
        _run(api.async_get_telematic_data(session, "tok", "WBA12345678901234", "c1"))
    assert exc.value.error_id == "CU-105"
    assert "(CU-105: No permission for specified containerId) for GET" in str(exc.value)


def test_an_error_leaves_the_vin_out_of_the_path():
    # The daily service-items warning logs this text; users paste such logs
    # into public issues, and the request path carries the VIN.
    vin = "WBA12345678901234"
    session = FakeSession([FakeResponse(400, {"exveErrorId": "CU-400"})])
    with pytest.raises(api.CardataApiError) as exc:
        _run(api.async_get_telematic_data(session, "tok", vin, "c1"))
    assert vin not in str(exc.value)
    assert "for GET /customers/vehicles/<VIN>/telematicData" in str(exc.value)

    class OfflineSession:
        def request(self, *args, **kwargs):
            raise aiohttp.ClientConnectionError(f"Cannot connect for {vin}")

    for call in (
        api.async_get_telematic_data(OfflineSession(), "tok", vin, "c1"),
        api.async_get_vehicle_image(OfflineSession(), "tok", vin),
    ):
        with pytest.raises(api.CardataApiError) as exc:
            _run(call)
        assert vin not in str(exc.value)


def test_an_error_note_is_shortened_and_masked():
    detail = api._error_detail({"exveNote": "VIN WBA12345678901234 " + "x" * 300})
    assert detail.startswith("VIN <VIN> x") and len(detail) == 160
    assert api._error_detail("  plain\n text  ") == "plain text"
    assert api._error_detail({}) is None


def test_an_html_error_page_is_its_title_alone():
    # BMW's front server answered the Oct 2026 outage with nginx's whole page.
    page = (
        "<html>\r\n<head><title>502 Bad Gateway</title></head>\r\n<body>\r\n"
        "<center><h1>502 Bad Gateway</h1></center>\r\n<hr><center>nginx/1.31.3</center>\r\n"
        "</body>\r\n</html>\r\n"
    )
    err = api._api_error(502, "GET", "/customers/vehicles/WBA12345678901234/telematicData", page)
    assert str(err) == "HTTP 502 (Bad Gateway) for GET /customers/vehicles/<VIN>/telematicData"
    assert api._error_detail("<html><body>oops</body></html>") is None


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
    session = FakeSession([FakeResponse(200, b"\x89PNG", headers={"Content-Type": "image/png"})])
    data, content_type = _run(api.async_get_vehicle_image(session, "tok", "VIN0"))
    assert data == b"\x89PNG"
    assert content_type == "image/png"
