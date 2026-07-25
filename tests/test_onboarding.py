"""Tests for the guided-onboarding activator, bookmarklet, helper page and parser.

Home Assistant-free: verify the activator embeds the wanted attributes + report
URL and hits the right endpoints, the bookmarklet round-trips, the helper page
carries both delivery methods, and the result blob round-trips through the parser
(client-id selection, activation counts, and the failure modes a paste can hit).
"""

from __future__ import annotations

import base64
import json
import urllib.parse

import pytest

from .conftest import load_module

OB = load_module("onboarding")
D = load_module("descriptors")


def _make_blob(obj: dict) -> str:
    """Reproduce what the activator emits: PREFIX + base64(utf8 json)."""
    return OB.RESULT_PREFIX + base64.b64encode(
        json.dumps(obj).encode("utf-8")
    ).decode("ascii")


# --- activator generation -------------------------------------------------------


def test_activator_embeds_sorted_attributes_and_endpoints():
    attrs = D.descriptors_for_sections(D.default_sections())
    js = OB.build_activator_js(attrs)
    assert "const WANT = [" in js
    for descriptor in attrs[:5]:
        assert descriptor in js
    # All three onboarding endpoints + additive/idempotent + same-origin session.
    assert "/utilities/bmw/api/cd/applications" in js
    assert "/mybmw/api/mapped-vehicle/" in js
    assert "/utilities/bmw/api/cd/streams/" in js
    assert "credentials: 'include'" in js
    assert "?includeAttributes=true" in js  # reads current before writing
    # Filters wanted ids against BMW's streamable catalogue (avoids the 500 that
    # posting a non-streamable descriptor causes).
    assert "catalogue?streamable=true" in js
    assert "WANT.filter(a => valid.has(a))" in js
    assert OB.RESULT_PREFIX in js
    # Deterministic.
    assert js == OB.build_activator_js(attrs)


def test_activator_embeds_report_url_or_empty():
    with_report = OB.build_activator_js(["vehicle.a"], report_url="https://ha/api/webhook/xyz")
    assert 'const REPORT = "https://ha/api/webhook/xyz"' in with_report
    without = OB.build_activator_js(["vehicle.a"])
    assert 'const REPORT = ""' in without


def test_activator_is_page_aware_and_additive():
    js = OB.build_activator_js(["vehicle.a"])
    # Guards for wrong origin / missing mapped-vehicle id.
    assert "indexOf('bmw')" in js
    assert "stream setup" in js
    # Additive union, not a blind replace.
    assert "new Set(current)" in js


# --- bookmarklet ----------------------------------------------------------------


def test_bookmarklet_is_javascript_scheme_and_roundtrips():
    bm = OB.build_bookmarklet(["vehicle.a", "vehicle.b"])
    assert bm.startswith("javascript:")
    decoded = urllib.parse.unquote(bm[len("javascript:"):])
    assert decoded == OB.build_activator_js(["vehicle.a", "vehicle.b"])
    # Percent-encoded: no raw spaces/quotes that would break an href.
    assert " " not in bm and '"' not in bm


# --- helper page ----------------------------------------------------------------


def test_helper_page_carries_bookmarklet_and_console():
    bm = OB.build_bookmarklet(["vehicle.a"], report_url="https://ha/api/webhook/xyz")
    console = OB.build_console_snippet(["vehicle.a"], report_url="https://ha/api/webhook/xyz")
    html = OB.build_helper_page(bookmarklet=bm, console_js=console, attribute_count=42)
    assert "<!doctype html>" in html.lower()
    # Bookmarklet lands in the anchor href (HTML-escaped).
    assert "Activate BMW data" in html
    assert "javascript:" in html
    assert "42" in html
    # Console fallback present, escaped into a textarea.
    assert "F12" in html or "Console" in html


# --- result parsing -------------------------------------------------------------


def _sample(**over) -> dict:
    base = {
        "v": 1,
        "origin": "https://www.bmw.at",
        "locale": "de-at",
        "mappedVehicleId": "90d3dd",
        "clientIds": [
            {"apikey": "aaaa1111", "scopes": ["openid"]},
            {"apikey": "bbbb2222", "scopes": ["cardata:api:read", "openid"]},
        ],
        "vehicle": {"mappingStatus": "CONFIRMED", "isElectricOrHybrid": True},
        "activated": {"status": 201, "current": 180, "added": 48, "total": 228,
                      "skipped": 35, "ok": True},
        "errors": [],
    }
    base.update(over)
    return base


def test_parse_roundtrip_and_field_extraction():
    res = OB.parse_onboarding_result(_make_blob(_sample()))
    assert res.origin == "https://www.bmw.at"
    assert res.mapped_vehicle_id == "90d3dd"
    assert len(res.clients) == 2
    assert res.vehicle["mappingStatus"] == "CONFIRMED"
    assert res.activated_count == 228
    assert res.added_count == 48
    assert res.skipped_count == 35
    assert res.activation_ok is True


def test_primary_client_id_prefers_api_read_scope():
    res = OB.parse_onboarding_result(_make_blob(_sample()))
    assert res.primary_client_id == "bbbb2222"


def test_primary_client_id_falls_back_to_first_when_no_scope_match():
    obj = _sample(clientIds=[{"apikey": "only1", "scopes": ["openid"]}])
    assert OB.parse_onboarding_result(_make_blob(obj)).primary_client_id == "only1"


def test_no_clients_gives_none():
    assert OB.parse_onboarding_result(_make_blob(_sample(clientIds=[]))).primary_client_id is None


def test_nothing_to_do_run_reports_ok_zero_added():
    obj = _sample(activated={"status": 200, "current": 228, "added": 0, "total": 228, "ok": True})
    res = OB.parse_onboarding_result(_make_blob(obj))
    assert res.added_count == 0
    assert res.activated_count == 228
    assert res.activation_ok is True


def test_parse_tolerates_surrounding_whitespace_and_quotes():
    blob = _make_blob(_sample())
    assert OB.parse_onboarding_result(f'  "{blob}"  \n').mapped_vehicle_id == "90d3dd"


def test_errors_are_surfaced():
    res = OB.parse_onboarding_result(_make_blob(_sample(errors=["streams-write: 403"])))
    assert res.errors == ["streams-write: 403"]


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "not a bavariandata blob", "BAVARIANDATA1:!!!notbase64!!!"],
)
def test_bad_input_raises_parse_error(bad):
    with pytest.raises(OB.OnboardingParseError):
        OB.parse_onboarding_result(bad)


def test_wrong_version_is_rejected():
    with pytest.raises(OB.OnboardingParseError):
        OB.parse_onboarding_result(_make_blob(_sample(v=99)))
