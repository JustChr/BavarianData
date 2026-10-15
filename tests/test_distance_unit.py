"""Which unit a car's distances are entered and shown in (issue #126).

UK installs keep Home Assistant's metric unit system (Celsius, litres) while
their cars count in miles. The mileage allowance screen and the service
calendar used to read the unit system alone, so a yearly allowance typed in
miles was stored as km. One rule now decides it everywhere
(``units.distance_unit``): the car's distance sensors as displayed (odometer
first), then the car's own display setting, then the unit system. The card's
``_lengthUnit`` applies the same order; ``test_card_distance_unit.py`` holds it
to that.

The figures are made up: 8,000 mi a year from 1 Jul at 2,000 mi, the car on
4,000 mi on 1 Oct, i.e. right on plan.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from .conftest import load_module

units = load_module("units")
mileage = load_module("mileage")
const = load_module("const")
distance_unit = units.distance_unit


@pytest.mark.parametrize(
    ("sensors", "car", "system", "expected"),
    [
        # Metric + miles (the UK): the odometer is displayed in miles.
        (["mi"], None, "km", "mi"),
        # Metric + miles, nothing switched yet, but the car is set to miles.
        (["km"], "miles", "km", "km"),  # a sensor the user set to km wins
        ([], "miles", "km", "mi"),
        ([None, "mi"], None, "km", "mi"),  # odometer unknown, another sensor in miles
        # Metric + km: everything km.
        (["km", "km"], "km", "km", "km"),
        ([], None, "km", "km"),
        ([], "kilometers", "km", "km"),  # the older descriptor's spelling
        # US customary: the unit system is miles.
        ([], None, "mi", "mi"),
        (["mi"], "miles", "mi", "mi"),
        (["km"], None, "mi", "km"),  # the user chose km for this car
    ],
)
def test_the_unit_follows_sensors_then_car_then_system(sensors, car, system, expected):
    assert distance_unit(sensors, car, system) == expected


@pytest.mark.parametrize("junk", [None, "", "ft", 7, "unknown", "unavailable"])
def test_junk_falls_through_to_the_next_step(junk):
    assert distance_unit([junk], junk, "km") == "km"
    assert distance_unit([junk], "Miles ", "km") == "mi"  # spelling and spacing folded


def _yearly(per_year_km: float, start_km: float) -> dict:
    return {
        const.OPTION_MILEAGE_KIND: "yearly",
        const.OPTION_MILEAGE_START: "2026-07-01",
        const.OPTION_MILEAGE_ALLOWANCE_KM: per_year_km,
        const.OPTION_MILEAGE_START_KM: start_km,
    }


def _log() -> dict:
    """The odometer log, in km: 2,000 mi on 1 Jul to 4,000 mi on 1 Oct."""

    start, days = date(2026, 7, 1), 92
    return {
        (start + timedelta(days=i)).isoformat(): (2000 + 2000 * i / days) * units.KM_PER_MILE
        for i in range(days + 1)
    }


def _stored(typed: float, unit: str) -> float:
    """What the settings screen stores for a number typed in ``unit``."""

    return round(typed * units.KM_PER_MILE if unit == "mi" else typed, 1)


def test_a_uk_allowance_typed_in_miles_is_stored_as_km():
    unit = distance_unit(["mi"], None, "km")  # metric HA, odometer shown in miles
    allowance = mileage.mileage_report(
        _log(), date(2026, 10, 1), _yearly(_stored(8000, unit), _stored(2000, unit))
    )["allowance"]
    assert allowance["allowance_km"] == 12875  # 8,000 mi
    assert round(allowance["remaining_km"] / units.KM_PER_MILE) == 6000
    assert 0 <= allowance["balance_km"] < 50  # on plan, not 1,504 mi over


def test_the_old_rule_stored_the_miles_as_km():
    """What #126 reported: the metric unit system alone said km."""

    unit = distance_unit([], None, "km")
    allowance = mileage.mileage_report(
        _log(), date(2026, 10, 1), _yearly(_stored(8000, unit), _stored(2000, unit))
    )["allowance"]
    assert allowance["allowance_km"] == 8000
    assert allowance["balance_km"] < -2000
