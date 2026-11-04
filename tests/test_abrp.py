"""The ABRP push: what a point holds, which cars send, and what a send returns.

``abrp.py`` is Home Assistant-free; the timing glue in ``abrp_push.py`` runs in
``tests_ha``. The rule throughout is the bridge's: *unknown sends nothing*.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import aiohttp
import pytest

from .conftest import load_module

abrp = load_module("abrp")
evcc = load_module("evcc")
flow_answers = load_module("flow_answers")
vehicle_options = load_module("vehicle_options")
const = load_module("const")

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
UTC = int(NOW.timestamp())
VIN = "WBAEXAMPLE0000001"
OTHER = "WBAEXAMPLE0000002"


def snap(*, parked=None, dc=None, lat=None, lon=None, alt=None, **bridge):
    return abrp.AbrpSnapshot(
        bridge=evcc.BridgeSnapshot(vin=VIN, **bridge),
        parked=parked,
        dc=dc,
        latitude=lat,
        longitude=lon,
        altitude=alt,
    )


def point(snapshot, *, position=False):
    return abrp.telemetry(snapshot, now=NOW, position=position)


# --------------------------------------------------------------------------
# The point
# --------------------------------------------------------------------------


def test_no_charge_level_sends_nothing():
    assert point(snap(charging=True, charge_power_kw=11, parked=True)) is None


def test_only_what_is_known():
    assert point(snap(soc=84.04)) == {"utc": UTC, "soc": 84.0}


def test_parked_not_charging():
    assert point(snap(soc=84, charging=False, parked=True, odometer_km=22115, range_km=335)) == {
        "utc": UTC,
        "soc": 84.0,
        "is_charging": 0,
        "is_dcfc": 0,
        "power": 0,
        "is_parked": 1,
        "speed": 0,
        "odometer": 22115.0,
        "est_battery_range": 335.0,
    }


def test_charging_power_is_negative():
    sent = point(snap(soc=50, charging=True, charge_power_kw=10.987, parked=True, dc=False))
    assert sent["power"] == -10.99
    assert sent["is_charging"] == 1
    assert sent["is_dcfc"] == 0


def test_dc_charge():
    assert point(snap(soc=50, charging=True, charge_power_kw=150, dc=True))["is_dcfc"] == 1


def test_charging_without_a_known_method_says_nothing_of_dc():
    assert "is_dcfc" not in point(snap(soc=50, charging=True, charge_power_kw=7))


def test_driving_sends_no_power_and_no_speed():
    sent = point(snap(soc=60, charging=False, parked=False))
    assert sent["is_parked"] == 0
    assert "power" not in sent
    assert "speed" not in sent


def test_unknown_parked_state_sends_neither_way():
    sent = point(snap(soc=60, charging=False))
    assert "is_parked" not in sent
    assert "power" not in sent


def test_position_only_when_switched_on():
    where = snap(soc=60, lat=48.1234567, lon=11.7654321, alt=520.4)
    assert "lat" not in point(where)
    sent = point(where, position=True)
    assert (sent["lat"], sent["lon"], sent["elevation"]) == (48.123457, 11.765432, 520.0)


def test_half_a_fix_is_no_position():
    assert "lat" not in point(snap(soc=60, lat=48.1), position=True)


# --------------------------------------------------------------------------
# Reading the coordinator
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("DC_DYNAMIC", True),
        ("dc_dynamic_malibu", True),
        ("AC_TYPE2PLUG", False),
        ("ac_type1plug", False),
        ("NOCHARGING", None),
        (None, None),
        (3, None),
    ],
)
def test_dc_charging(method, expected):
    assert abrp.dc_charging(method) is expected


class FakeCoordinator:
    def __init__(self, values=None, *, open_trip=False, reports=False, **bridge):
        self.values = values or {}
        self.open_trip = open_trip
        self.reports = reports
        self.bridge = bridge

    def bridge_snapshot(self, vin):
        return evcc.BridgeSnapshot(vin=vin, **self.bridge)

    def get_state(self, vin, descriptor):
        if descriptor not in self.values:
            return None
        return SimpleNamespace(value=self.values[descriptor])

    def has_open_trip(self, vin):
        return self.open_trip

    def report_trips(self, vin):
        return self.reports


def test_snapshot_reads_motion_first():
    moving = FakeCoordinator({abrp.DESC_MOVING: True}, open_trip=False)
    assert abrp.abrp_snapshot(moving, VIN).parked is False


def test_snapshot_parked_from_the_trip_detector():
    assert abrp.abrp_snapshot(FakeCoordinator(open_trip=True), VIN).parked is False
    assert abrp.abrp_snapshot(FakeCoordinator(open_trip=False), VIN).parked is True


def test_a_car_reporting_trips_afterwards_is_never_called_parked():
    assert abrp.abrp_snapshot(FakeCoordinator(reports=True), VIN).parked is None


def test_snapshot_dc_only_while_charging():
    values = {abrp.DESC_CHARGING_METHOD: "DC_DYNAMIC"}
    assert abrp.abrp_snapshot(FakeCoordinator(values, charging=True), VIN).dc is True
    assert abrp.abrp_snapshot(FakeCoordinator(values, charging=False), VIN).dc is None


def test_snapshot_position():
    values = {abrp.DESC_GPS_LAT: "48.1", abrp.DESC_GPS_LON: 11.2, abrp.DESC_ALTITUDE: "bad"}
    got = abrp.abrp_snapshot(FakeCoordinator(values), VIN)
    assert (got.latitude, got.longitude, got.altitude) == (48.1, 11.2, None)
    half = abrp.abrp_snapshot(FakeCoordinator({abrp.DESC_GPS_LAT: 48.1}), VIN)
    assert half.latitude is None and half.longitude is None


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def _options(**cars):
    return {const.OPTION_VEHICLES: cars}


def test_config_per_car(monkeypatch):
    monkeypatch.setattr(abrp, "PROJECT_API_KEY", "project-key")
    config = abrp.AbrpConfig.from_options(
        _options(
            **{
                VIN: {"abrp_enabled": True, "abrp_token": " tok ", "abrp_position": True},
                OTHER: {"abrp_enabled": True, "abrp_token": ""},
            }
        )
    )
    assert config.api_key == "project-key"
    assert config.sends(VIN)
    assert config.car(VIN).token == "tok"
    assert config.car(VIN).position
    # Switched on without a token: nothing to send with.
    assert not config.sends(OTHER)
    assert not config.sends("UNKNOWN")
    assert config.any_enabled


def test_own_key_wins(monkeypatch):
    monkeypatch.setattr(abrp, "PROJECT_API_KEY", "project-key")
    assert abrp.api_key({"abrp_api_key": " mine "}) == "mine"
    assert abrp.api_key({"abrp_api_key": ""}) == "project-key"


def test_no_key_sends_nothing(monkeypatch):
    monkeypatch.setattr(abrp, "PROJECT_API_KEY", "")
    config = abrp.AbrpConfig.from_options(
        _options(**{VIN: {"abrp_enabled": True, "abrp_token": "tok"}})
    )
    assert not config.sends(VIN)
    assert not config.any_enabled


def test_git_copy_ships_no_key():
    # The release workflow fills this in; a key committed here would be in
    # the public history for good.
    assert load_module("abrp_key").PROJECT_API_KEY == ""


def test_settings_are_always_the_cars_own():
    stored = vehicle_options.store_abrp(
        {"abrp_api_key": "mine"},
        VIN,
        {"abrp_enabled": True, "abrp_token": "tok", "abrp_position": False},
    )
    assert stored[const.OPTION_VEHICLES][VIN] == {
        "abrp_enabled": True,
        "abrp_token": "tok",
        "abrp_position": False,
    }
    assert "abrp_token" not in stored
    assert vehicle_options.abrp_values(stored, VIN)["abrp_token"] == "tok"
    assert vehicle_options.abrp_values(stored, OTHER) == {}


# --------------------------------------------------------------------------
# The form's checks
# --------------------------------------------------------------------------


def test_form_errors():
    assert flow_answers.abrp_error(enabled=False, token="", key="") is None
    assert flow_answers.abrp_error(enabled=True, token="", key="k") == (
        "abrp_token",
        "abrp_token_missing",
    )
    assert flow_answers.abrp_error(enabled=True, token="t", key="") == (
        "abrp_api_key",
        "abrp_key_missing",
    )
    assert flow_answers.abrp_error(enabled=True, token="t", key="k") is None


def test_check_errors():
    result = abrp.SendResult
    assert flow_answers.abrp_check_error(result.OK) is None
    assert flow_answers.abrp_check_error(result.REJECTED) == ("abrp_token", "abrp_token_rejected")
    assert flow_answers.abrp_check_error(result.UNREACHABLE) == ("base", "abrp_unreachable")


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def json(self, content_type=None):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, status=200, body=None, error=None):
        self.status = status
        self.body = {"status": "ok"} if body is None else body
        self.error = error
        self.calls = []

    def post(self, url, *, headers, data, timeout):
        self.calls.append((url, headers, data))
        if self.error is not None:
            raise self.error
        return FakeResponse(self.status, self.body)


def _send(session, payload=None):
    return asyncio.run(
        abrp.async_send(session, key="KEY", token="TOK", point=payload or {"utc": 1, "soc": 50})
    )


def test_send_ok_and_its_wire_shape():
    session = FakeSession()
    assert _send(session) is abrp.SendResult.OK
    url, headers, data = session.calls[0]
    assert url == "https://api.iternio.com/1/tlm/send"
    assert headers == {"Authorization": "APIKEY KEY"}
    assert data == {"token": "TOK", "tlm": '{"utc":1,"soc":50}'}


@pytest.mark.parametrize(
    ("session", "expected"),
    [
        (FakeSession(status=401), abrp.SendResult.REJECTED),
        (FakeSession(status=403), abrp.SendResult.REJECTED),
        (FakeSession(body={"status": "error"}), abrp.SendResult.REJECTED),
        (FakeSession(status=500), abrp.SendResult.UNREACHABLE),
        (FakeSession(body=ValueError("not json")), abrp.SendResult.UNREACHABLE),
        (FakeSession(error=aiohttp.ClientError()), abrp.SendResult.UNREACHABLE),
        (FakeSession(error=TimeoutError()), abrp.SendResult.UNREACHABLE),
    ],
)
def test_send_failures_are_results(session, expected):
    assert _send(session) is expected


def test_check_sends_only_the_time():
    session = FakeSession()
    result = asyncio.run(abrp.async_check(session, key="KEY", token="TOK", now=NOW))
    assert result is abrp.SendResult.OK
    assert session.calls[0][2]["tlm"] == f'{{"utc":{UTC}}}'
