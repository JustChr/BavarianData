"""Trip settings, per car.

An account can hold a company car and a private one, or a car that streams
while driving next to one that only reports once parked, so the settings that
describe a car (or the person driving it) are kept per VIN. The two that
describe the house instead -- address lookup and the trip-capture diagnostics
-- stay account-wide and are not handled here.

Storage (see ``OPTION_VEHICLES``): a car with an entry under
``options["vehicles"][VIN]`` uses it; every other car uses the account-wide
keys. There is no partial inheritance -- the Configure form always writes a
car's complete set -- so what the form shows is exactly what applies.

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
    OPTION_TRIP_TRACK,
    OPTION_TRIP_WORK_ZONE,
    OPTION_VEHICLES,
    TRIP_DETECTION_CHOICES,
    TRIP_DETECTION_LIVE,
    TRIP_DETECTION_REPORTS,
)
from .history.classify import trip_class_setting

# The option keys a car can hold for itself, in the order the form shows them.
PER_VEHICLE_TRIP_OPTIONS = (
    OPTION_TRIP_WORK_ZONE,
    OPTION_TRIP_DEFAULT_CLASS,
    OPTION_TRIP_COMMUTE_GAP,
    OPTION_TRIP_DETECTION,
    OPTION_TRIP_TRACK,
)


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

    @property
    def report_trips(self) -> bool:
        """True when trips are built from vehicle reports, not the live stream."""

        return self.detection == TRIP_DETECTION_REPORTS

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
        )


def _own_values(options: Mapping[str, Any], vin: str) -> Optional[Mapping[str, Any]]:
    vehicles = options.get(OPTION_VEHICLES)
    if not isinstance(vehicles, Mapping):
        return None
    own = vehicles.get(vin)
    return own if isinstance(own, Mapping) else None


def vehicle_trip_values(options: Mapping[str, Any], vin: Optional[str]) -> dict[str, Any]:
    """The stored per-car option values in force for ``vin`` (what the form shows)."""

    source = (_own_values(options, vin) if vin else None) or options
    return {key: source[key] for key in PER_VEHICLE_TRIP_OPTIONS if key in source}


def account_trip_settings(options: Mapping[str, Any]) -> TripSettings:
    """The settings of every car without an entry of its own."""

    return TripSettings.from_values(options)


def vehicle_trip_settings(options: Mapping[str, Any]) -> dict[str, TripSettings]:
    """The settings of each car that has an entry of its own, by VIN."""

    vehicles = options.get(OPTION_VEHICLES)
    if not isinstance(vehicles, Mapping):
        return {}
    return {
        vin: TripSettings.from_values(values)
        for vin, values in vehicles.items()
        if isinstance(values, Mapping)
    }


def store_trip_values(
    options: Mapping[str, Any],
    values: Mapping[str, Any],
    *,
    vin: Optional[str],
    per_vehicle: bool,
) -> dict[str, Any]:
    """New options with one form's per-car values stored.

    ``per_vehicle`` is set when the account has more than one car: the values
    then go under that car alone, as a complete set. Otherwise they are the
    account-wide keys, and an entry the one car may still carry from a time the
    account had two is dropped -- it would silently outrank what was just saved.
    """

    updated = dict(options)
    picked = {key: values[key] for key in PER_VEHICLE_TRIP_OPTIONS if key in values}
    if OPTION_TRIP_WORK_ZONE not in values:
        # A cleared entity selector is left out of the submitted data entirely.
        picked[OPTION_TRIP_WORK_ZONE] = None
    vehicles = dict(updated.get(OPTION_VEHICLES) or {})
    if per_vehicle and vin:
        vehicles[vin] = picked
    else:
        updated.update(picked)
        if vin:
            vehicles.pop(vin, None)
    if vehicles:
        updated[OPTION_VEHICLES] = vehicles
    else:
        updated.pop(OPTION_VEHICLES, None)
    return updated
