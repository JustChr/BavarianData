"""The overview says what the car is doing, and its tiles follow.

The line under the photo answers "can I go?" in words; first match wins:
something open or unlocked while parked, then charging, on the road, plugged in
below the target, low, ready. Without ``tiles:`` the grid shows the four tiles
that fit that moment. Runs the shipped card under Node, like
``test_card_drivetrain.py``, whose fixtures and renderer it reuses.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from .test_card_drivetrain import EV_TARGET, I5, M2, NODE, _render, _sensor

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

TARGET_80 = {**EV_TARGET, "state": "80"}
CHARGING = _sensor("vehicle.drivetrain.electricEngine.charging.status", "chargingactive")
POWER = _sensor("vehicle.powertrain.electric.battery.charging.power", "7400", "W")
PLUGGED = _sensor("charging_cable", "on", device_class="plug")
UNPLUGGED = _sensor("charging_cable", "off", device_class="plug")
DOOR_OPEN = _sensor("vehicle.cabin.door.row1.driver.isOpen", "on")
UNLOCKED = _sensor("vehicle.cabin.door.status", "UNLOCKED")
LOCKED = _sensor("vehicle.cabin.door.status", "SECURED")


def _session(**attrs) -> dict:
    return _sensor(
        "charged_energy_session",
        "16.2",
        "kWh",
        device_class="energy",
        last_reset=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        **attrs,
    )


def _trip(**attrs) -> dict:
    started = (datetime.now(timezone.utc) - timedelta(minutes=18)).isoformat()
    return _sensor(
        "trip_in_progress",
        "on",
        started=started,
        distance_km=23.4,
        soc_start=80,
        soc_now=74,
        **attrs,
    )


def _verdict(html: str) -> tuple[str, str]:
    match = re.search(r'verdict__t verdict__t--(\w+)">([^<]*)<', html)
    assert match, html
    return match.group(1), match.group(2)


def _cells(html: str) -> list[str]:
    return re.findall(r'<span class="cell__lbl">([^<]*)</span>', html)


def _ev(**extra) -> str:
    return _render({**I5, "sensor.i5_target": TARGET_80, **extra})["html"]


# --- the line on top ---------------------------------------------------------


def test_a_parked_car_with_nothing_to_say_is_ready():
    tone, title = _verdict(_ev(**{"sensor.lock": LOCKED}))
    assert (tone, title) == ("ok", "Ready to go")


def test_an_open_door_comes_first_even_on_a_charge():
    # Left open overnight on the drive must not hide behind "done at".
    html = _ev(**{"sensor.door": DOOR_OPEN, "sensor.i5_charging": CHARGING})
    assert _verdict(html)[0] == "alert"
    # Merely unlocked while charging is a charge.
    html = _ev(**{"sensor.lock": UNLOCKED, "sensor.i5_charging": CHARGING})
    assert _verdict(html)[1] == "Charging"
    html = _ev(**{"sensor.door": DOOR_OPEN, "sensor.lock": UNLOCKED})
    assert _verdict(html) == ("alert", "Something&#39;s open") or _verdict(html) == (
        "alert",
        "Something's open",
    )
    assert "Front left · Door" in html and "Unlocked" in html
    # The tile for it, in the alert tone.
    assert "cell cell--alert" in html


def test_only_unlocked_says_so():
    assert _verdict(_ev(**{"sensor.lock": UNLOCKED})) == ("alert", "Unlocked")


def test_an_open_door_on_the_road_is_not_the_headline():
    html = _ev(**{"sensor.door": DOOR_OPEN, "binary_sensor.trip": _trip()})
    assert _verdict(html) == ("live", "On the road")


def test_a_charge_says_when_it_will_be_done_and_what_it_added():
    eta = _sensor("charge_eta", "90", "min", device_class="duration")
    eta["last_updated"] = datetime.now(timezone.utc).isoformat()
    html = _ev(
        **{
            "sensor.i5_charging": CHARGING,
            "sensor.power": POWER,
            "sensor.eta": eta,
            "sensor.session": _session(
                soc_start=41, cost_so_far=1.32, currency="EUR", solar_percent=64
            ),
        }
    )
    tone, title = _verdict(html)
    assert tone == "live" and title.startswith("Charging · done at ")
    assert "7.4 kW" in html
    # The stripes cover what this charge added: 41 % up to 72 %.
    assert 'class="bar__gain" style="left:41%;width:31%"' in html
    assert "+31 % since" in html
    assert _cells(html) == ["Charging power", "Added so far", "From your solar", "Cost so far"]
    assert "64 %" in html and "€1.32" in html
    # The minutes left are a tap away.
    assert 'class="verdict" data-entity="sensor.eta"' in html


def test_without_a_tariff_or_solar_the_time_tile_takes_their_place():
    eta = _sensor("charge_eta", "90", "min", device_class="duration")
    eta["last_updated"] = datetime.now(timezone.utc).isoformat()
    html = _ev(
        **{
            "sensor.i5_charging": CHARGING,
            "sensor.power": POWER,
            "sensor.eta": eta,
            "sensor.session": _session(soc_start=41),
        }
    )
    assert _cells(html)[:3] == ["Charging power", "Added so far", "Done at"]


def test_on_the_road_the_bar_shows_what_the_drive_used():
    html = _ev(**{"binary_sensor.trip": _trip(), "sensor.lock": LOCKED})
    assert _verdict(html) == ("live", "On the road")
    assert "23.4 km" in html
    assert 'class="bar__used" style="left:74%;width:6%' in html
    assert "−6 % this trip" in html
    assert _cells(html) == ["This trip", "Driving", "Used this trip", "Lock"]


def test_plugged_in_below_its_target_is_waiting():
    html = _ev(**{"binary_sensor.plug": PLUGGED})
    assert _verdict(html) == ("ok", "Waiting to charge")
    assert "target 80 %" in html


def test_plugged_in_at_its_target_is_ready():
    soc = {**I5["sensor.i5_soc"], "state": "80"}
    html = _ev(**{"binary_sensor.plug": PLUGGED, "sensor.i5_soc": soc})
    assert _verdict(html) == ("ok", "Ready to go")
    assert "at its 80 % target" in html


def test_low_and_unplugged_says_charge_soon():
    soc = {**I5["sensor.i5_soc"], "state": "15"}
    html = _ev(**{"binary_sensor.plug": UNPLUGGED, "sensor.i5_soc": soc})
    assert _verdict(html) == ("warn", "Charge soon")


def test_a_petrol_car_low_on_fuel_says_refuel_soon():
    tank = _sensor("vehicle.drivetrain.fuelSystem.level", "12", "%")
    html = _render({**M2, "sensor.m2_tank": tank})["html"]
    assert _verdict(html) == ("warn", "Refuel soon")


# --- the grid ----------------------------------------------------------------


def test_own_tiles_stay_whatever_the_car_does():
    config = {"tiles": ["odometer"]}
    html = _render({**I5, "binary_sensor.trip": _trip()}, config)["html"]
    assert _cells(html) == ["Odometer"]
    assert _verdict(html) == ("live", "On the road")
