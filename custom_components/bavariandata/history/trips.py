"""Persisted trip records and their retention rules.

Home Assistant-free on purpose (see ``models.py``): a trip's shape, its distance
and duration arithmetic, and its retention all have to be unit-testable without
an HA install. Everything here survives a ``json.dumps``/``loads`` round trip.

Privacy is baked into the record, not bolted on: a trip stores *places*, never
coordinates. A place is the resolved Home Assistant zone name when the endpoint
sits inside a zone, an optional reverse-geocoded address string when it does not,
and never a latitude/longitude. See ``docs/roadmap.md`` (Phase 3) for why.
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


def place(
    zone: Optional[str] = None, address: Optional[str] = None
) -> dict[str, Any]:
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
        """

        if not self.distance_km or self.distance_km <= 0 or self.energy_kwh is None:
            return None
        return round(self.energy_kwh / self.distance_km * 100, 1)

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
            "classification": self.classification,
            "classification_source": self.classification_source,
            "stats": self.stats,
            "location_assumed": self.location_assumed,
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
