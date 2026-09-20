"""Persisted trip records and their retention rules.

Home Assistant-free on purpose (see ``models.py``): a trip's shape, its distance
and duration arithmetic, and its retention all have to be unit-testable without
an HA install. Everything here survives a ``json.dumps``/``loads`` round trip.

Privacy is baked into the record, not bolted on: a trip's *endpoints* are always
stored as *places*, never coordinates. A place is the resolved Home Assistant
zone name when the endpoint sits inside a zone, an optional reverse-geocoded
address string when it does not, and never a latitude/longitude. See
``docs/roadmap.md`` (Phase 3) for why.

The one exception is the optional ``track``: a route polyline of raw fixes,
each ``[lat, lon]`` or ``[lat, lon, t]`` where ``t`` is whole seconds since the
trip started, recorded only when the user explicitly opts in (the ``trip_track``
option, off by default) so a map can draw where -- and, with ``t``, when -- the
car went. It is empty on every trip unless route recording is on, which is why
it is the sole place in the history layer coordinates ever reach disk. Points
written before timestamps shipped are two-element and read back time-less.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from .models import _iso, _parse  # shared ISO (de)serialisation helpers

# The three buckets a trip can fall into. ``None`` means "not classified yet".
CLASS_BUSINESS = "business"
CLASS_PRIVATE = "private"
CLASS_COMMUTE = "commute"
CLASSIFICATIONS = (CLASS_BUSINESS, CLASS_PRIVATE, CLASS_COMMUTE)

# Where a classification came from: an automatic guess the user may correct, or
# the user's own explicit choice (which auto-classification must never overwrite).
SOURCE_AUTO = "auto"
SOURCE_USER = "user"

# Smallest SoC drop a trip's consumption figure may be derived from.
#
# Trip energy is SoC delta x pack capacity (see ``coordinator._trip_energy_kwh``)
# and BMW streams SoC as a whole percent, so the energy is quantised at roughly
# 0.8 kWh on a 78 kWh pack -- a step that is a rounding error over 30 km and the
# entire measurement over 1 km. Worse, the quantisation is *one-sided* in what
# survives: a short drive that rounds to a 0 % drop yields no energy at all and
# drops out, while one that rounds to 1 % keeps a full step it did not use. So
# the short trips that reach an average are exactly the ones that over-read, and
# no amount of averaging removes a bias that only points one way.
#
# 3 % keeps the quantisation error under roughly a sixth of the figure, which is
# the point where a consumption number is worth showing at all. Below it the
# honest answer is "not measurable", not a number that happens to be printable.
# There is no finer signal to reach for: the only other energy descriptor the
# stream carries (``smeEnergyDeltaFullyCharged``) is the same quantity rounded
# to whole kWh, i.e. coarser still.
MIN_CONSUMPTION_SOC_DELTA = 3.0


def place(zone: Optional[str] = None, address: Optional[str] = None) -> dict[str, Any]:
    """Build a place record from a zone name and/or an address string.

    ``label`` is what the card shows: the zone name if we have one, else the
    address, else a generic "Unknown". Coordinates are deliberately absent -- a
    place is only ever a name.
    """

    label = zone or address or "Unknown"
    return {"zone": zone, "address": address, "label": label}


@dataclass
class Trip:
    """One drive, from the moment the car starts moving until it stops.

    ``distance_km`` is the odometer delta over the trip (BMW's own
    ``travelledDistance`` is used as a fallback when the odometer didn't tick).
    ``stats`` carries BMW's per-segment figures -- consumption, recuperation, the
    eco-drive fractions and the accel/brake driving-style stars -- so per-trip
    detail rides the record and is served through ``get_trips`` rather than
    spawning an entity per data point (roadmap rule 1).
    """

    vin: str
    start: datetime
    end: Optional[datetime] = None
    # {"zone": str|None, "address": str|None, "label": str} -- never lat/lon.
    start_place: Optional[dict[str, Any]] = None
    end_place: Optional[dict[str, Any]] = None
    distance_km: Optional[float] = None
    soc_start: Optional[float] = None
    soc_end: Optional[float] = None
    energy_kwh: Optional[float] = None
    classification: Optional[str] = None
    classification_source: Optional[str] = None
    stats: dict[str, Any] = field(default_factory=dict)
    # True when an endpoint's place had to be assumed (no GPS, no geocode).
    location_assumed: bool = False
    # Optional route polyline along the drive, downsampled and bounded (see
    # trip_builder.py). Each point is [lat, lon] or [lat, lon, t] (t = seconds
    # since ``start``); a track mixes neither -- it is all-timestamped or, for
    # records predating timestamps, all not. Empty unless the user opted into
    # route recording (``trip_track``) -- the only coordinates the layer persists.
    track: list[list[float]] = field(default_factory=list)
    # True when the car is a plug-in hybrid. Its energy is still the battery
    # drop, but the distance may have been driven partly on fuel, so dividing
    # one by the other reads low -- see ``consumption_kwh_per_100km``.
    hybrid: bool = False

    @property
    def id(self) -> str:
        """Stable identifier, also used to deduplicate on merge/override."""

        return f"{self.vin}-{_iso(self.start)}"

    @property
    def duration_s(self) -> Optional[int]:
        if self.end is None:
            return None
        return max(0, int((self.end - self.start).total_seconds()))

    @property
    def soc_delta(self) -> Optional[float]:
        if self.soc_start is None or self.soc_end is None:
            return None
        return round(self.soc_end - self.soc_start, 1)

    @property
    def consumption_kwh_per_100km(self) -> Optional[float]:
        """Energy used per 100 km, from the trip's own energy and distance.

        Kept as a property rather than a stored field so it can't disagree with
        ``energy_kwh``/``distance_km`` after an enrichment updates them.

        ``None`` when the SoC drop behind ``energy_kwh`` is too small to divide
        by (see :data:`MIN_CONSUMPTION_SOC_DELTA`) -- a 1 km hop that ticked one
        percent is not a 79 kWh/100 km drive, and printing it as one poisons
        every average and "worst trip" it lands in. The gate only applies when
        the record carries both SoC readings, which is exactly when the energy
        was derived from them; a car that one day reports its own trip energy
        directly is not held to a resolution limit that isn't its.

        ``None`` on a plug-in hybrid too. The battery drop is real, but nothing
        says how much of the distance the engine drove: a 40 km run that used
        4 kWh and a litre of petrol would print 10 kWh/100 km, a figure no part of
        the car achieved. The stream carries no electric-only distance to divide
        by instead, so the energy stays on the record and the ratio is withheld.
        """

        if self.hybrid:
            return None
        if not self.distance_km or self.distance_km <= 0 or self.energy_kwh is None:
            return None
        drop = self.soc_drop
        if drop is not None and drop < MIN_CONSUMPTION_SOC_DELTA:
            return None
        return round(self.energy_kwh / self.distance_km * 100, 1)

    @property
    def soc_drop(self) -> Optional[float]:
        """How far the SoC fell over the drive, or ``None`` if unknown.

        The positive counterpart to :attr:`soc_delta`: a drive spends charge, so
        the figure everything downstream reasons about is the drop.
        """

        if self.soc_start is None or self.soc_end is None:
            return None
        return round(self.soc_start - self.soc_end, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "vin": self.vin,
            "start": _iso(self.start),
            "end": _iso(self.end),
            "start_place": self.start_place,
            "end_place": self.end_place,
            "distance_km": self.distance_km,
            "soc_start": self.soc_start,
            "soc_end": self.soc_end,
            "energy_kwh": self.energy_kwh,
            # Derived, but shipped rather than left to the reader: the card used
            # to divide energy by distance itself, which quietly re-introduced
            # the very figures the property refuses to produce. One rule, one
            # place. ``from_dict`` ignores it -- the property stays the source
            # of truth on the way back in.
            "consumption_kwh_per_100km": self.consumption_kwh_per_100km,
            "classification": self.classification,
            "classification_source": self.classification_source,
            "stats": self.stats,
            "location_assumed": self.location_assumed,
            "track": [list(point) for point in self.track],
            "hybrid": self.hybrid,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Optional["Trip"]:
        """Rebuild a record, or return ``None`` if it can't be placed on a timeline."""

        vin = data.get("vin")
        start = _parse(data.get("start"))
        if not vin or start is None:
            return None
        classification = data.get("classification")
        if classification not in CLASSIFICATIONS:
            classification = None
        return cls(
            vin=vin,
            start=start,
            end=_parse(data.get("end")),
            start_place=data.get("start_place"),
            end_place=data.get("end_place"),
            distance_km=data.get("distance_km"),
            soc_start=data.get("soc_start"),
            soc_end=data.get("soc_end"),
            energy_kwh=data.get("energy_kwh"),
            classification=classification,
            classification_source=data.get("classification_source"),
            stats=dict(data.get("stats") or {}),
            location_assumed=bool(data.get("location_assumed")),
            track=[list(point) for point in data.get("track") or []],
            hybrid=bool(data.get("hybrid")),
        )


def prune_trips(
    trips: list[Trip],
    *,
    now: datetime,
    retain_months: Optional[int],
    max_entries: int,
) -> list[Trip]:
    """Apply the retention policy, newest first.

    Mirrors :func:`models.prune_sessions`: a user retention window plus a hard
    cap so "keep forever" still can't grow the store without bound.
    """

    ordered = sorted(trips, key=lambda item: item.start, reverse=True)
    if retain_months:
        cutoff = now - timedelta(days=30 * retain_months)
        ordered = [item for item in ordered if item.start >= cutoff]
    return ordered[:max_entries]


def merge_trip(trips: list[Trip], trip: Trip) -> list[Trip]:
    """Insert or replace by ``id``, keeping the list newest-first.

    Replacement is what makes enrichment and reclassification idempotent: a later
    write for the same start time updates the record in place, never duplicates it.
    """

    remaining = [item for item in trips if item.id != trip.id]
    remaining.append(trip)
    remaining.sort(key=lambda item: item.start, reverse=True)
    return remaining
