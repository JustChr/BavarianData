"""Where a car is, in Home Assistant's terms (``places.py``).

The coordinator's scenarios cover the paths a charge or trip takes; these pin
the lookups on their own: which position wins, which zone a record is filed
under, and the answers the route weather asks for when nothing is there.
"""

from __future__ import annotations

from datetime import datetime, UTC

import pytest

from .conftest import load_module
from .fake_ha import Clock, FakeHass, install_fake_ha, load_with_fake_ha

places = load_with_fake_ha("places")
car_state = load_module("car_state")

VIN = "WBA00000000000001"
NAV = "vehicle.cabin.infotainment.navigation.currentLocation.{}"
SEGMENT = "vehicle.trip.segment.end.vehicleLocation.gpsPosition.{}"
HOUSE = (48.1000, 11.5000)


@pytest.fixture
def hass(monkeypatch: pytest.MonkeyPatch) -> FakeHass:
    # The zone lookup imports Home Assistant at call time.
    install_fake_ha(monkeypatch)
    return FakeHass(Clock(datetime(2026, 11, 21, 12, tzinfo=UTC)))


def _places(hass, readings=None, *, remember=False, work_zone=None):
    readings = readings or {}

    def get_state(vin, descriptor):
        if vin != VIN or descriptor not in readings:
            return None
        return car_state.DescriptorState(readings[descriptor], None, None)

    return places.Places(
        hass,
        get_state,
        remember_locations=lambda: remember,
        work_zone_entity=lambda vin: work_zone,
    )


def _at(lat, lon, template=NAV):
    return {template.format("latitude"): lat, template.format("longitude"): lon}


def test_the_live_fix_wins_and_a_bad_value_falls_through(hass):
    readings = {**_at(1.0, 2.0, SEGMENT), NAV.format("latitude"): "not a number"}
    found = _places(hass, readings)
    assert found.coordinate(VIN, "latitude") == 1.0
    assert found.coordinate(VIN, "longitude") == 2.0
    assert _places(hass, _at(3.0, 4.0)).coordinate(VIN, "latitude") == 3.0
    assert _places(hass).coordinate(VIN, "latitude") is None


def test_a_smaller_zone_at_the_house_is_filed_as_home(hass):
    hass.add_zone("Home", *HOUSE, radius_m=100)
    hass.add_zone("Carport", *HOUSE, radius_m=10)
    found = _places(hass, _at(*HOUSE))
    # Home Assistant names the smallest zone, and zone events keep it ...
    assert found.current_ha_zone(VIN) == ("Carport", "zone.carport")
    # ... but a charge there is a charge at home.
    assert found.charging_location(VIN) == {"zone": "Home"}


def test_a_zone_away_from_the_house_keeps_its_name(hass):
    hass.add_zone("Home", *HOUSE, radius_m=100)
    hass.add_zone("Office", 48.2, 11.6, radius_m=100)
    assert _places(hass).zone_name(48.2, 11.6) == "Office"
    assert _places(hass).zone_name(0.0, 0.0) is None


def test_the_point_is_kept_only_when_the_user_asks(hass):
    readings = _at(48.123456789, 11.987654321)
    assert _places(hass, readings).charging_location(VIN) == {"zone": None}
    assert _places(hass, readings, remember=True).charging_location(VIN) == {
        "zone": None,
        "lat": 48.12346,
        "lon": 11.98765,
    }
    assert _places(hass).charging_location(VIN) is None
    assert _places(hass).current_ha_zone(VIN) is None


def test_a_failing_zone_lookup_is_no_zone(hass, monkeypatch):
    import sys

    def broken(*_args, **_kwargs):
        raise RuntimeError("zone integration not loaded")

    monkeypatch.setattr(sys.modules["homeassistant.components.zone"], "async_active_zone", broken)
    assert _places(hass).ha_zone(*HOUSE) is None


def test_where_the_route_weather_looks(hass):
    hass.add_zone("Office", 48.2, 11.6)
    found = _places(hass)
    assert found.zone_location("Office") == (48.2, 11.6)
    assert found.zone_location("Gym") is None
    # No position in Home Assistant's configuration, no home to ask about.
    assert found.home_location() is None
    hass.config.latitude, hass.config.longitude = HOUSE
    assert found.home_location() == HOUSE


def test_home_and_work_are_named_as_home_assistant_names_them(hass):
    # Without a Home zone, the records still go under "Home".
    assert _places(hass).home_zone_name() == "Home"
    hass.add_zone("Home", *HOUSE)
    assert _places(hass).home_zone_name() == "Home"
    assert _places(hass).work_zone_name(VIN) is None
    assert _places(hass, work_zone="zone.office").work_zone_name(VIN) is None
    hass.add_zone("Office", 48.2, 11.6)
    assert _places(hass, work_zone="zone.office").work_zone_name(VIN) == "Office"
