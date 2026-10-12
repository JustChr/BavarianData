"""Values BMW sends that stand for "no reading".

``vehicle.trip.segment.end.drivetrain.batteryManagement.hvSoc`` is the
high-voltage battery's charge at the end of a trip. A petrol MINI Countryman
(issue #100) has no such battery, yet BMW sent it once, as ``0`` and stamped
days before. Shown as a battery sensor at 0 %, that reads as a flat battery
and trips every low-battery alert a user has. No car with a high-voltage
battery ends a trip at 0 % -- it would not have driven there -- so a zero here
is never a measurement.

A placeholder clears the value rather than being skipped: an entity that
already holds the zero (restored, or from before this rule) must go to unknown,
not keep it for good.

Home Assistant-free on purpose, so the rule is unit-tested; ``sensor.py`` and
the coordinator apply it.
"""

from __future__ import annotations

from typing import Any

TRIP_END_HV_SOC = "vehicle.trip.segment.end.drivetrain.batteryManagement.hvSoc"

# Descriptors whose zero means "no reading".
ZERO_MEANS_NONE: frozenset[str] = frozenset({TRIP_END_HV_SOC})


def is_placeholder(descriptor: str, value: Any) -> bool:
    """Whether ``value`` is BMW's stand-in for no reading of ``descriptor``.

    Restored states come back as text, so ``"0"`` counts as well as ``0``.
    """

    if descriptor not in ZERO_MEANS_NONE or isinstance(value, bool):
        return False
    try:
        return float(value) == 0
    except TypeError, ValueError:
        return False
