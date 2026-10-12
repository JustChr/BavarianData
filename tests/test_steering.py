"""Which side the driver sits on, read from BMW's basic data.

BMW keys side doors and windows by seat (``door.row1.driver``), so the card can
only draw an open driver's door on the correct side of the car if it knows the
steering side. Basic data's ``steering`` says it; the coordinator hands it to
every entity as ``vehicle_basic_data.steering_side``.
"""

from __future__ import annotations

import pytest

from .conftest import load_module
from .harness import VIN, CoordinatorHarness

steering = load_module("steering")


# The basic data of a right-hand-drive car, trimmed to what matters here.
RHD_CAR = {
    "vin": VIN,
    "brand": "BMW",
    "modelName": "i5 eDrive40",
    "countryCode": "GB",
    "driveTrain": "BEV",
    "steering": "RL",
}


@pytest.mark.parametrize("raw, side", [("LL", "left"), ("RL", "right")])
def test_bmws_steering_codes_name_the_drivers_side(raw: str, side: str) -> None:
    # BMW's shorthand: Linkslenker / Rechtslenker.
    assert steering.steering_side(raw) == side


@pytest.mark.parametrize("raw", [None, "", "XX", 1, ["RL"]])
def test_anything_else_is_unknown(raw) -> None:
    assert steering.steering_side(raw) is None


def test_basic_data_exposes_the_steering_side() -> None:
    with CoordinatorHarness(capacity_kwh=None) as h:
        metadata = h.coordinator.apply_basic_data(VIN, RHD_CAR)
        assert metadata["extra_attributes"]["steering_side"] == "right"
        assert metadata["raw_data"]["steering"] == "RL"


def test_basic_data_without_steering_says_nothing() -> None:
    with CoordinatorHarness(capacity_kwh=None) as h:
        metadata = h.coordinator.apply_basic_data(
            VIN, {key: value for key, value in RHD_CAR.items() if key != "steering"}
        )
        assert metadata["extra_attributes"]["steering_side"] is None
