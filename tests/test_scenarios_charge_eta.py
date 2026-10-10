"""Time to the charge target, and BMW's progress figures once a charge is over.

Two halves of one complaint (2026-10-10): the card said "1 h" to go on an i5
that had reached its 80 % target. BMW stops *sending* its time to full and its
charging power when a charge ends rather than sending a zero, so both stayed at
their last value (32 min, 10.56 kW) for good. And while the charge ran, BMW's
figure came every ~40 minutes and ran 7-26 minutes long, where one worked out
from the wallbox meter -- (target - estimate) / meter rate -- was 1-3 off.

So at home the integration works out its own (``charge_eta``), and when a
charge closes on the car's word BMW's figures go to zero.

Times are minutes after 08:00 UTC; the car and the ledger are the meter
scenarios' (``test_scenarios_meter.py``): 1.25 points per wallbox kWh.
"""

from __future__ import annotations

import pytest

from .harness import CoordinatorHarness
from .test_scenarios_meter import METER, PER_KWH, _seed

TTF = "vehicle.drivetrain.electricEngine.charging.timeToFullyCharged"
REMAINING = "vehicle.drivetrain.electricEngine.charging.timeRemaining"
POWER = "vehicle.powertrain.electric.battery.charging.power"
KW = 7.4  # what the wallbox delivers


def _eta(h: CoordinatorHarness):
    return h.coordinator.get_charge_eta(h.vin)


def _value(h: CoordinatorHarness, descriptor: str):
    state = h.coordinator.get_state(h.vin, descriptor)
    return None if state is None else state.value


def _home_charge(h: CoordinatorHarness, until: int, *, target: float = 80) -> None:
    """A 7.4 kW home charge from 38 %, the meter reporting every minute."""

    h.send(10, target=target, step=f"target {target:g}")
    h.meter(10, 1000.0)
    h.send(10, soc=38, status="CHARGINGACTIVE", power=3480)
    for minute in range(11, until + 1):
        h.meter(minute, 1000.0 + KW * (minute - 10) / 60)


@pytest.fixture
def h():
    harness = CoordinatorHarness(wallbox_meter=METER)
    _seed(harness)
    yield harness
    harness.close()


# --- our own figure -----------------------------------------------------------


def test_the_time_to_target_follows_the_meter(h):
    _home_charge(h, 40)
    h.advance_to(40)

    rate = KW * PER_KWH  # 9.25 %/h
    expected = (80 - h.estimate()) / rate * 60
    assert _eta(h) == round(expected)
    assert 200 < _eta(h) < 260


def test_it_counts_down_and_stops_at_the_target(h):
    _home_charge(h, 40)
    h.advance_to(40)
    first = _eta(h)
    for minute in range(41, 70):
        h.meter(minute, 1000.0 + KW * (minute - 10) / 60)
    h.advance_to(70)
    assert _eta(h) < first

    # Far enough that the estimate reaches the target and holds there.
    for minute in range(70, 400, 5):
        h.meter(minute, 1000.0 + KW * (minute - 10) / 60)
    h.advance_to(400)
    assert h.estimate() == 80
    assert _eta(h) == 0


def test_no_figure_until_the_meter_rate_settles(h):
    """The first minutes leave the card on BMW's own figure."""

    _home_charge(h, 12)
    h.advance_to(12)

    assert _eta(h) is None


def test_a_paused_solar_charge_has_no_figure(h):
    _home_charge(h, 30)
    h.advance_to(60)  # nothing has flowed for half an hour

    assert _eta(h) is None


def test_a_charge_away_from_home_leaves_bmw_to_answer():
    """Only the stream's power there, and a DC charge tapers: no straight line."""

    with CoordinatorHarness(wallbox_meter=METER) as h:
        _seed(h, location={"zone": "Home"})
        h.coordinator._home_zone_name = lambda: "Home"
        h.coordinator._charging_location = lambda vin: {"zone": "Supercharger"}
        _home_charge(h, 40)
        h.advance_to(40)

        assert h.coordinator.soc_estimate_attributes(h.vin)["estimate_source"] == "stream"
        assert _eta(h) is None


def test_a_car_not_charging_needs_no_time(h):
    h.send(10, soc=60, target=80, status="NOCHARGING")

    assert _eta(h) == 0


def test_without_a_meter_bound_there_is_no_figure_at_all():
    with CoordinatorHarness() as h:
        h.send(10, soc=38, target=80, status="CHARGINGACTIVE", power=11000)
        h.advance_to(40)
        assert _eta(h) is None
        h.send(41, status="NOCHARGING")
        assert _eta(h) is None


# --- BMW's figures once the charge is over ------------------------------------


def _bmw_charge(h: CoordinatorHarness) -> None:
    h.send(10, soc=74, target=80, status="CHARGINGACTIVE", power=10560)
    h.send(12, extra={TTF: 32, REMAINING: 118}, step="BMW: 32 min to full")


def test_a_finished_charge_zeroes_what_bmw_left_behind():
    """The i5 on 2026-10-10: ended at its target, then said nothing more."""

    with CoordinatorHarness() as h:
        _bmw_charge(h)
        h.send(40, soc=80, status="CHARGINGENDED")
        assert _value(h, TTF) == 32  # still inside the flap debounce

        h.advance_to(43)
        assert _value(h, TTF) == 0
        assert _value(h, REMAINING) == 0
        assert _value(h, POWER) == 0
        assert len(h.sessions()) == 1


def test_a_status_flap_leaves_them_alone():
    """A blip that comes straight back must not zero a value BMW won't resend."""

    with CoordinatorHarness() as h:
        _bmw_charge(h)
        h.send(40, status="NOCHARGING")
        h.send(41, status="CHARGINGACTIVE")
        h.advance_to(50)

        assert _value(h, TTF) == 32
        assert _value(h, POWER) == 10560


def test_a_car_that_never_sent_them_gains_nothing():
    with CoordinatorHarness() as h:
        h.send(10, soc=74, target=80, status="CHARGINGACTIVE")
        h.send(40, soc=80, status="CHARGINGENDED")
        h.advance_to(43)

        assert h.coordinator.get_state(h.vin, TTF) is None
        assert h.coordinator.get_state(h.vin, REMAINING) is None


def test_a_charge_that_ended_across_a_restart_is_zeroed_too():
    with CoordinatorHarness() as h:
        _bmw_charge(h)
        h.restart(downtime_s=600)
        h.send(25, soc=80, status="CHARGINGENDED")

        assert len(h.sessions()) == 1
        assert _value(h, TTF) == 0
        assert _value(h, POWER) == 0


def test_a_restart_that_never_hears_back_leaves_them_alone():
    """The grace timer can't know the charge ended; it may still be running."""

    with CoordinatorHarness() as h:
        _bmw_charge(h)
        h.restart(downtime_s=60)
        h.advance_to(40)  # past the 15-minute grace, and BMW said nothing

        assert _value(h, TTF) == 32
