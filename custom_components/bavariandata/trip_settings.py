"""Trip settings, per car.

An account can hold a company car and a private one, or a car that streams
while driving next to one that only reports once parked, so the settings that
describe a car (or the person driving it) are kept per VIN. The two that
describe the house instead -- address lookup and the trip-capture diagnostics
-- stay account-wide and are not handled here.

Storage and the per-car rules are ``vehicle_options.py``'s; this module
turns a car's stored values into the settings the coordinator reads.

No Home Assistant imports: the resolution rules are unit-tested directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .const import (
    DEFAULT_TRIP_COMMUTE_GAP_MIN,
    DEFAULT_TRIP_DEFAULT_CLASS,
    OPTION_TRIP_COMMUTE_GAP,
    OPTION_TRIP_DEFAULT_CLASS,
    OPTION_TRIP_DETECTION,
    OPTION_TRIP_PHONE_IN_CAR,
    OPTION_TRIP_PHONE_TRACKER,
    OPTION_TRIP_TRACK,
    OPTION_TRIP_WORK_ZONE,
    TRIP_DETECTION_CHOICES,
    TRIP_DETECTION_LIVE,
    TRIP_DETECTION_REPORTS,
)
from .history.classify import trip_class_setting
from .vehicle_options import TRIP_OPTIONS, cars_with_own


@dataclass(frozen=True)
class TripSettings:
    """How one car's trips are detected, recorded and classified."""

    work_zone_entity: Optional[str] = None
    # ``None`` means "leave unclassified" (see ``trip_class_setting``).
    default_class: Optional[str] = DEFAULT_TRIP_DEFAULT_CLASS
    # 0 switches commute chaining off.
    commute_gap_s: int = DEFAULT_TRIP_COMMUTE_GAP_MIN * 60
    detection: str = TRIP_DETECTION_LIVE
    record_track: bool = False
    # A phone's ``device_tracker`` and the binary sensor that says it is in the
    # car (``phone_route.py``). Kept as set even where unused, so switching
    # back to reports finds them where they were.
    phone_tracker: Optional[str] = None
    phone_in_car: Optional[str] = None

    @property
    def report_trips(self) -> bool:
        """True when trips are built from vehicle reports, not the live stream."""

        return self.detection == TRIP_DETECTION_REPORTS

    @property
    def phone_route(self) -> bool:
        """True when the route of a report trip comes from a phone."""

        return self.report_trips and self.record_track and bool(self.phone_tracker)

    @classmethod
    def from_values(cls, values: Mapping[str, Any]) -> "TripSettings":
        """Parse the stored option values; anything unrecognised is the default."""

        detection = values.get(OPTION_TRIP_DETECTION)
        try:
            gap_min = int(values.get(OPTION_TRIP_COMMUTE_GAP, DEFAULT_TRIP_COMMUTE_GAP_MIN) or 0)
        except TypeError, ValueError:
            gap_min = DEFAULT_TRIP_COMMUTE_GAP_MIN
        return cls(
            work_zone_entity=values.get(OPTION_TRIP_WORK_ZONE) or None,
            default_class=trip_class_setting(
                values.get(OPTION_TRIP_DEFAULT_CLASS, DEFAULT_TRIP_DEFAULT_CLASS)
            ),
            commute_gap_s=max(gap_min, 0) * 60,
            detection=detection if detection in TRIP_DETECTION_CHOICES else TRIP_DETECTION_LIVE,
            record_track=bool(values.get(OPTION_TRIP_TRACK)),
            phone_tracker=values.get(OPTION_TRIP_PHONE_TRACKER) or None,
            phone_in_car=values.get(OPTION_TRIP_PHONE_IN_CAR) or None,
        )


def account_trip_settings(options: Mapping[str, Any]) -> TripSettings:
    """The settings of every car without trip settings of its own."""

    return TripSettings.from_values(options)


def vehicle_trip_settings(options: Mapping[str, Any]) -> dict[str, TripSettings]:
    """The settings of each car that has trip settings of its own, by VIN."""

    return {
        vin: TripSettings.from_values(values)
        for vin, values in cars_with_own(options, TRIP_OPTIONS).items()
    }
