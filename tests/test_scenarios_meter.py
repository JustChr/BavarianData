"""The wallbox meter driving the SoC estimate, run through the real coordinator.

With a wallbox energy meter bound and enough home charges in the ledger, the
estimate moves by what the meter counted -- ``points per kWh`` read off those
charges (``history/meter_rate.py``) -- instead of extrapolating BMW's sparse
charging power. These scenarios pin when the meter may drive, and that every
way of losing it hands the estimate back to the stream without a jump.

Times are minutes after 08:00 UTC. The car is a 75 kWh pack; BMW reports
3.48 kW (4.64 %/h) while the wallbox really delivers 7.4 kW, which is the
gap the meter exists to close. The seeded charges gain 30 points for 24 kWh:
1.25 points per kWh, a 94 % efficient charge.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from .harness import START, CoordinatorHarness, store_mod

METER = "sensor.wallbox_energy"
STREAM_RATE = 3.48 / 75 * 100  # %/h, what BMW's power implies
PER_KWH = 30 / 24  # points per wallbox kWh in the seeded ledger
ORDERS = ["descriptors-first", "derived-first"]


def _seed(h: CoordinatorHarness, count: int = 3, *, location=None) -> None:
    """Home charges from the days before, each 30 -> 60 % for 24 kWh."""

    for day in range(1, count + 1):
        start = START - timedelta(days=day)
        h.coordinator.history.add_session(
            store_mod.ChargingSession(
                vin=h.vin,
                start=start,
                end=start + timedelta(hours=4),
                soc_start=30.0,
                soc_end=60.0,
                energy_kwh=22.0,
                grid_kwh=24.0,
                location=location,
            )
        )


def _charge(h: CoordinatorHarness, minute: float = 10, soc: float = 38) -> None:
    h.send(minute, target=100, step="target 100")
    h.meter(minute, 1000.0)
    h.send(minute, soc=soc, status="CHARGINGACTIVE", power=3480)


@pytest.fixture
def h():
    harness = CoordinatorHarness(wallbox_meter=METER)
    _seed(harness)
    yield harness
    harness.close()


def _source(h: CoordinatorHarness) -> str:
    return h.coordinator.soc_estimate_attributes(h.vin).get("estimate_source")


# --- the meter drives -------------------------------------------------------


def test_the_meter_drives_a_silent_charge(h, snapshot):
    """BMW says 3.48 kW and then nothing; the wallbox counted 14.8 kWh."""

    _charge(h)
    h.meter(70, 1007.4)
    h.meter(130, 1014.8)
    h.advance_to(140)

    assert _source(h) == "meter"
    assert h.estimate() == pytest.approx(38 + 14.8 * PER_KWH, abs=0.01)
    # The stream alone would have said 47.3.
    assert h.estimate() > 38 + STREAM_RATE * 130 / 60 + 8
    assert h.render() == snapshot


def test_the_charging_rate_follows_the_meter(h):
    """BMW says 4.64 %/h; the wallbox delivers 7.4 kW, which is 9.25 %/h."""

    _charge(h)
    assert h.rate() == pytest.approx(STREAM_RATE, abs=0.01)
    for minute in range(11, 40):
        h.meter(minute, 1000.0 + 7.4 * (minute - 10) / 60)
    h.advance_to(40)

    assert h.rate() == pytest.approx(7.4 * PER_KWH, abs=0.1)


def test_a_meter_reporting_once_an_hour_gives_its_hourly_rate(h):
    """Not one hour's energy packed into the few minutes after it arrived."""

    _charge(h)
    h.meter(70, 1007.4)
    h.advance_to(71)

    assert h.rate() == pytest.approx(7.4 * PER_KWH, abs=0.1)


def test_a_paused_charge_shows_no_rate(h):
    _charge(h)
    h.meter(20, 1001.0)
    h.advance_to(40)  # the wallbox paused: nothing flowed for twenty minutes

    assert h.rate() is None


def test_letting_go_of_the_meter_gives_the_stream_rate_back(h):
    _charge(h)
    for minute in range(11, 30):
        h.meter(minute, 1000.0 + 7.4 * (minute - 10) / 60)
    h.meter(30, None)
    h.advance_to(31)

    assert h.rate() == pytest.approx(STREAM_RATE, abs=0.01)


def test_too_few_learned_charges_leave_the_stream_driving():
    with CoordinatorHarness(wallbox_meter=METER) as h:
        _seed(h, count=2)
        _charge(h)
        h.meter(130, 1014.8)

        assert _source(h) == "stream"
        assert h.estimate() == pytest.approx(38 + STREAM_RATE * 120 / 60, abs=0.05)


def test_without_a_meter_bound_nothing_changes():
    with CoordinatorHarness() as h:
        _seed(h)
        h.send(10, soc=38, status="CHARGINGACTIVE", power=3480)
        h.advance_to(130)

        assert h.coordinator.soc_estimate_attributes(h.vin) == {}
        assert h.estimate() == pytest.approx(38 + STREAM_RATE * 120 / 60, abs=0.05)


def test_the_attributes_say_what_was_learned(h):
    _charge(h)
    h.meter(40, 1002.0)
    h.advance_to(41)

    attrs = h.coordinator.soc_estimate_attributes(h.vin)
    assert attrs["meter_sessions"] == 3
    assert attrs["meter_percent_per_kwh"] == pytest.approx(PER_KWH)
    # All three seeded charges ran at one power: no overhead can be told apart.
    assert attrs["meter_overhead_kw"] == 0.0
    assert attrs["meter_reading_kwh"] == 1002.0


def test_the_meter_stops_at_the_charge_target(h):
    h.send(10, target=50, step="target 50")
    h.meter(10, 1000.0)
    h.send(10, soc=45, status="CHARGINGACTIVE", power=3480)
    h.meter(100, 1010.0)  # 12.5 points' worth, past the target
    h.advance_to(101)

    assert h.estimate() == 50


# --- corrections ------------------------------------------------------------


def test_a_real_reading_restarts_the_count_from_the_meter(h):
    _charge(h)
    h.meter(130, 1014.8)
    h.advance_to(130.5)
    h.send(131, soc=55, status="CHARGINGACTIVE", step="app wakes the car")
    assert h.estimate() == 55

    h.meter(160, 1016.8)
    h.advance_to(161)
    assert h.estimate() == pytest.approx(55 + 2.0 * PER_KWH, abs=0.01)


def test_a_meter_reset_is_a_new_baseline_not_a_negative_charge(h):
    _charge(h)
    h.meter(70, 1007.4)
    h.advance_to(71)
    before = h.estimate()
    h.meter(80, 3.0)  # replaced or reset
    h.advance_to(81)
    assert h.estimate() == before

    h.meter(90, 4.0)
    h.advance_to(91)
    assert h.estimate() == pytest.approx(before + 1.0 * PER_KWH, abs=0.01)


def test_a_jump_no_wallbox_can_make_is_not_added(h):
    _charge(h)
    h.meter(20, 1500.0)  # 500 kWh in ten minutes
    h.advance_to(21)

    assert h.estimate() == 38


def test_an_unavailable_meter_hands_back_to_the_stream_without_a_jump(h, snapshot):
    _charge(h)
    h.meter(70, 1007.4)
    h.advance_to(70.5)
    at_handover = h.estimate()
    h.meter(70, None)
    h.advance_to(130)

    assert _source(h) == "stream"
    assert h.estimate() == pytest.approx(at_handover + STREAM_RATE * 59.5 / 60, abs=0.05)
    assert h.render() == snapshot


# --- when the meter may not drive -------------------------------------------


def test_a_charge_away_from_home_never_reads_the_meter():
    home = (48.1, 11.5)
    with CoordinatorHarness(wallbox_meter=METER, home=home) as h:
        h.hass.add_zone("Home", *home, radius_m=100)
        _seed(h)
        h.fix(5, 48.3, 11.9)  # somewhere else, inside no zone
        _charge(h)
        h.meter(130, 1014.8)  # someone else charging on the wallbox at home

        assert _source(h) == "stream"
        assert h.estimate() == pytest.approx(38 + STREAM_RATE * 120 / 60, abs=0.05)


def test_two_cars_charging_leave_the_meter_to_neither(h):
    _charge(h)
    other = {
        "vehicle.drivetrain.electricEngine.charging.status": {"value": "CHARGINGACTIVE"},
        "vehicle.drivetrain.batteryManagement.header": {"value": 50},
    }
    h._run(h.coordinator.async_handle_message({"vin": "WBATEST0000000002", "data": other}))
    h.meter(130, 1014.8)

    assert _source(h) == "stream"


def test_an_unplugged_car_never_follows_the_meter(h):
    """BMW forgot to end the charge; the cable is out, so the wallbox's kWh
    belong to whatever is plugged in now -- another car."""

    _charge(h)
    h.send(20, plug="DISCONNECTED", step="unplugged, status left on")
    h.meter(80, 1008.0)
    h.advance_to(81)

    assert _source(h) == "stream"


def test_a_plugged_car_follows_the_meter(h):
    _charge(h)
    h.send(11, plug="CONNECTED")
    h.meter(70, 1007.4)
    h.advance_to(71)

    assert _source(h) == "meter"


def test_a_charge_that_ends_lets_go_of_the_meter(h):
    _charge(h)
    h.meter(70, 1007.4)
    h.send(71, status="NOCHARGING", power=0, step="unplugged")
    frozen = h.estimate()
    h.meter(130, 1020.0)  # another car on the wallbox

    assert _source(h) == "stream"
    assert h.estimate() == frozen


# --- restarts ---------------------------------------------------------------


@pytest.mark.parametrize("order", ORDERS)
def test_a_restart_adds_what_the_meter_counted_while_ha_was_down(order, snapshot):
    with CoordinatorHarness(wallbox_meter=METER) as h:
        _seed(h)
        _charge(h)
        h.meter(70, 1007.4)
        h.advance_to(70.5)
        # Half an hour down; the wallbox kept counting.
        h.hass.set_state(METER, 1011.1, unit_of_measurement="kWh")
        h.restart(downtime_s=1800, order=order)
        h.advance_to(101)

        assert _source(h) == "meter"
        assert h.estimate() == pytest.approx(38 + 11.1 * PER_KWH, abs=0.01)
        assert h.render() == snapshot


# --- learning, end to end ---------------------------------------------------


def test_three_metered_home_charges_teach_the_fourth():
    """No seeded ledger: the rate comes from charges this harness ran."""

    with CoordinatorHarness(wallbox_meter=METER) as h:
        meter = 1000.0
        minute = 10.0
        for _ in range(3):
            h.meter(minute, meter)
            h.send(minute, soc=30, status="CHARGINGACTIVE", power=7400)
            meter += 24.0
            h.meter(minute + 180, meter)
            h.send(minute + 181, soc=60, status="CHARGINGACTIVE", power=7400)
            h.send(minute + 182, status="NOCHARGING", power=0, step="done")
            minute += 400
        h.advance_to(minute)
        assert h.coordinator.learned_meter_rate(h.vin).samples == 3

        h.meter(minute, meter)
        h.send(minute, soc=40, status="CHARGINGACTIVE", power=3480)
        h.meter(minute + 60, meter + 8.0)
        h.advance_to(minute + 61)

        assert _source(h) == "meter"
        assert h.estimate() == pytest.approx(40 + 8.0 * PER_KWH, abs=0.5)
