"""Persisted history records and their retention rules.

Home Assistant-free on purpose: the whole point of the history layer is that its
maths and bookkeeping can be unit-tested without an HA install (see
``tests/conftest.py``), so nothing here may import ``homeassistant``.

Records are stored as plain JSON via ``history.store``; every value that lands in
a record must therefore survive a ``json.dumps``/``loads`` round trip, which is
why timestamps are serialised as ISO 8601 strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

# Bumped whenever a record's on-disk shape changes incompatibly. ``store.py``
# refuses to load data written by a newer schema than it understands.
# v2 added the ``trips`` section alongside ``sessions`` (roadmap Phase 3).
SCHEMA_VERSION = 2


def _iso(value: Optional[datetime]) -> Optional[str]:
    return None if value is None else value.astimezone(timezone.utc).isoformat()


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # Treat a naive stored timestamp as UTC rather than dropping the record;
    # older writes (or a hand-edited store) shouldn't lose a whole session.
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass
class ChargingSession:
    """One plug-in, from the moment charging starts until it stops.

    ``energy_kwh`` is battery-side: it comes from integrating the streamed
    charging power, so it undercounts what the grid delivered by roughly the
    charging losses. ``grid_kwh`` is the real grid figure and is only ever
    populated from a source that actually measures it (BMW's charging history,
    or a wallbox energy entity the user bound) -- never estimated. Anything
    presenting a number to the user must say which of the two it is.
    """

    vin: str
    start: datetime
    end: Optional[datetime] = None
    soc_start: Optional[float] = None
    soc_end: Optional[float] = None
    target_soc: Optional[float] = None
    energy_kwh: Optional[float] = None
    grid_kwh: Optional[float] = None
    peak_power_kw: Optional[float] = None
    # [[seconds_since_start, kw], ...] -- downsampled, bounded (see sessions.py).
    power_curve: list[list[float]] = field(default_factory=list)
    # {"zone": "home"|"work"|..., "lat": float|None, "lon": float|None}
    location: Optional[dict[str, Any]] = None
    # True when no GPS was available and the home tariff was applied anyway.
    location_assumed: bool = False
    # {"amount": float, "currency": "EUR", "source": "tariff"|"bmw"}
    cost: Optional[dict[str, Any]] = None
    end_reason: Optional[str] = None
    # Odometer at the end of the session; lets cost be expressed per distance.
    mileage_km: Optional[float] = None
    # Set once BMW's charging history has been merged in.
    enriched: bool = False

    @property
    def id(self) -> str:
        """Stable identifier, also used to deduplicate on merge."""

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
    def avg_power_kw(self) -> Optional[float]:
        """Derived from energy over time rather than averaging the curve.

        The curve is downsampled and unevenly spaced, so averaging its points
        would weight a quiet hour the same as a busy minute.
        """

        duration = self.duration_s
        if not duration or self.energy_kwh is None:
            return None
        return round(self.energy_kwh / (duration / 3600.0), 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "vin": self.vin,
            "start": _iso(self.start),
            "end": _iso(self.end),
            "soc_start": self.soc_start,
            "soc_end": self.soc_end,
            "target_soc": self.target_soc,
            "energy_kwh": self.energy_kwh,
            "grid_kwh": self.grid_kwh,
            "peak_power_kw": self.peak_power_kw,
            "power_curve": [list(point) for point in self.power_curve],
            "location": self.location,
            "location_assumed": self.location_assumed,
            "cost": self.cost,
            "end_reason": self.end_reason,
            "mileage_km": self.mileage_km,
            "enriched": self.enriched,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Optional["ChargingSession"]:
        """Rebuild a record, or return ``None`` if it is unusable.

        A stored session without a VIN or start time can't be placed on a
        timeline, so it is dropped rather than resurrected half-formed.
        """

        vin = data.get("vin")
        start = _parse(data.get("start"))
        if not vin or start is None:
            return None
        return cls(
            vin=vin,
            start=start,
            end=_parse(data.get("end")),
            soc_start=data.get("soc_start"),
            soc_end=data.get("soc_end"),
            target_soc=data.get("target_soc"),
            energy_kwh=data.get("energy_kwh"),
            grid_kwh=data.get("grid_kwh"),
            peak_power_kw=data.get("peak_power_kw"),
            power_curve=[list(point) for point in data.get("power_curve") or []],
            location=data.get("location"),
            location_assumed=bool(data.get("location_assumed")),
            cost=data.get("cost"),
            end_reason=data.get("end_reason"),
            mileage_km=data.get("mileage_km"),
            enriched=bool(data.get("enriched")),
        )


def prune_sessions(
    sessions: list[ChargingSession],
    *,
    now: datetime,
    retain_months: Optional[int],
    max_entries: int,
) -> list[ChargingSession]:
    """Apply the retention policy, newest first.

    Two independent bounds: the user's retention window, and a hard cap so a
    pathological stream (or "keep forever") can't grow the store without limit.
    ``retain_months`` of ``None`` means keep forever -- the cap still applies.
    """

    ordered = sorted(sessions, key=lambda item: item.start, reverse=True)
    if retain_months:
        # Calendar months vary; 30-day months are close enough for a retention
        # window and avoid a dateutil dependency for a user-facing "12 months".
        cutoff = now - timedelta(days=30 * retain_months)
        ordered = [item for item in ordered if item.start >= cutoff]
    return ordered[:max_entries]


def merge_session(
    sessions: list[ChargingSession], session: ChargingSession
) -> list[ChargingSession]:
    """Insert or replace by ``id``, keeping the list newest-first.

    Replacement matters for enrichment: re-fetching BMW's charging history must
    update an existing session in place instead of duplicating it.
    """

    remaining = [item for item in sessions if item.id != session.id]
    remaining.append(session)
    remaining.sort(key=lambda item: item.start, reverse=True)
    return remaining
