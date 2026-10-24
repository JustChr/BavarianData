"""One car's state in one object, and the empty car a read sees.

The coordinator reads a car through ``_peek`` and writes it through ``_car``.
A read of a car never heard of gets :data:`NO_CAR`, which must stay empty: a
getter asked about any VIN may not leave a car behind, nor fill one in.
"""

from __future__ import annotations

import pytest

from .conftest import load_module

car_state = load_module("car_state")


def test_a_new_car_has_every_field_unset():
    car = car_state.Vehicle("WBATEST0000000001")

    assert car.charge.tracking is None and car.charge.soc_estimate is None
    assert car.session.builder is None and car.session.stopped is None
    assert car.trip.builder is None and car.trip.zone_left_fired is False
    assert car.gps.tracker is None and car.gps.pending_parts is None
    assert car.report.timer is None and car.report.phone_route is None
    assert car.live_descriptors == set()


def test_cars_share_nothing():
    first, second = car_state.Vehicle("A"), car_state.Vehicle("B")
    first.live_descriptors.add("vehicle.isMoving")
    first.charge.tracker().update_status("CHARGINGACTIVE")

    assert second.live_descriptors == set()
    assert second.charge.tracking is None
    assert first.charge is not second.charge


def test_the_tracker_is_made_once():
    charge = car_state.ChargeState()

    assert charge.tracker() is charge.tracker()


def test_take_reads_and_clears():
    session = car_state.SessionState(gap_credit_soc=41.0)

    assert session.take("gap_credit_soc") == 41.0
    assert session.gap_credit_soc is None
    assert session.take("gap_credit_soc") is None


@pytest.mark.parametrize("part", ["charge", "session", "trip", "gps", "report"])
def test_no_car_refuses_writes(part):
    with pytest.raises(AttributeError, match="read-only"):
        getattr(car_state.NO_CAR, part).anything = 1


def test_no_car_reads_as_unset_and_take_writes_nothing():
    assert car_state.NO_CAR.charge.soc_estimate is None
    assert car_state.NO_CAR.session.take("builder") is None
    assert car_state.NO_CAR.trip.zone_left_fired is False
    with pytest.raises(AttributeError):
        car_state.NO_CAR.live_descriptors.add("vehicle.isMoving")


def test_no_car_cannot_grow_a_tracker():
    with pytest.raises(AttributeError, match="read-only"):
        car_state.NO_CAR.charge.tracker()
