"""Tests for the guided-onboarding snippet generator and result parser.

Home Assistant-free: verify the snippet embeds the chosen descriptor set and the
right endpoints, and that the result blob the snippet prints round-trips through
the parser (including client-id selection and the failure modes a paste can hit).
"""

from __future__ import annotations

import base64
import json

import pytest

from .conftest import load_module

OB = load_module("onboarding")
D = load_module("descriptors")


def _make_blob(obj: dict) -> str:
    """Reproduce what the in-browser snippet emits: PREFIX + base64(utf8 json)."""
    return OB.RESULT_PREFIX + base64.b64encode(
        json.dumps(obj).encode("utf-8")
    ).decode("ascii")


# --- snippet generation ---------------------------------------------------------


def test_snippet_embeds_sorted_descriptor_ids():
    sections = D.default_sections()
    expected = D.descriptors_for_sections(sections)
    snippet = OB.build_onboarding_snippet(sections)
    # The ids are embedded as a JSON array assigned to IDS.
    assert "const IDS = [" in snippet
    for descriptor in expected[:5]:
        assert descriptor in snippet
    # Deterministic: same input -> identical snippet.
    assert snippet == OB.build_onboarding_snippet(sections)


def test_snippet_calls_the_three_onboarding_endpoints():
    snippet = OB.build_onboarding_snippet(D.default_sections())
    assert "/utilities/bmw/api/cd/applications" in snippet
    assert "/mybmw/api/mapped-vehicle/" in snippet
    assert "/utilities/bmw/api/cd/streams/" in snippet
    # Same-origin session use + activation payload shape.
    assert "credentials: 'include'" in snippet
    assert "attributes: IDS" in snippet
    assert OB.RESULT_PREFIX in snippet  # emits a parseable blob


def test_empty_selection_still_produces_a_valid_snippet():
    snippet = OB.build_onboarding_snippet([])
    assert "const IDS = []" in snippet


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
        "activated": {"status": 201, "requested": 42, "accepted": 42, "ok": True},
        "errors": [],
    }
    base.update(over)
    return base


def test_parse_roundtrip_and_field_extraction():
    res = OB.parse_onboarding_result(_make_blob(_sample()))
    assert res.origin == "https://www.bmw.at"
    assert res.locale == "de-at"
    assert res.mapped_vehicle_id == "90d3dd"
    assert len(res.clients) == 2
    assert res.vehicle["mappingStatus"] == "CONFIRMED"
    assert res.activated_count == 42
    assert res.activation_ok is True


def test_primary_client_id_prefers_api_read_scope():
    # bbbb2222 has cardata:api:read, even though aaaa1111 comes first.
    res = OB.parse_onboarding_result(_make_blob(_sample()))
    assert res.primary_client_id == "bbbb2222"


def test_primary_client_id_falls_back_to_first_when_no_scope_match():
    obj = _sample(clientIds=[{"apikey": "only1", "scopes": ["openid"]}])
    res = OB.parse_onboarding_result(_make_blob(obj))
    assert res.primary_client_id == "only1"


def test_no_clients_gives_none():
    res = OB.parse_onboarding_result(_make_blob(_sample(clientIds=[])))
    assert res.primary_client_id is None


def test_parse_tolerates_surrounding_whitespace_and_quotes():
    blob = _make_blob(_sample())
    res = OB.parse_onboarding_result(f'  "{blob}"  \n')
    assert res.mapped_vehicle_id == "90d3dd"


def test_errors_are_surfaced():
    res = OB.parse_onboarding_result(_make_blob(_sample(errors=["streams: 403"])))
    assert res.errors == ["streams: 403"]
    assert res.activation_ok is True  # activation still reported ok independently


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "not a bavariandata blob",
        "BAVARIANDATA1:!!!notbase64!!!",
    ],
)
def test_bad_input_raises_parse_error(bad):
    with pytest.raises(OB.OnboardingParseError):
        OB.parse_onboarding_result(bad)


def test_wrong_version_is_rejected():
    with pytest.raises(OB.OnboardingParseError):
        OB.parse_onboarding_result(_make_blob(_sample(v=99)))
