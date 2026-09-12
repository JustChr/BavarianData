"""Where the energy in a charging session actually came from.

Home Assistant-free (see ``models.py``): the attribution is pure arithmetic over
four instantaneous power readings, so it is unit-tested without an HA install.
The coordinator owns *when* to sample; this module owns what the sample means.

The question users actually ask is "how much of my charging was sun?", and every
answer built out of template sensors gets it wrong in the same way: it compares
totals over a window instead of attributing energy as it flows. A house with a
battery makes that worse, because the same kilowatt-hour can arrive from the roof
at noon and leave the battery at nine.

So energy is attributed **as it is delivered**, from the site's supply mix at
that instant, and the car is treated as just another load:

    the car's share of PV  =  its draw x (PV serving loads / total supply)

No convention that privileges the car ("solar charges the car first") or
penalises it ("the car is the marginal load, so it gets the grid import"). Those
are both defensible and both flattering to somebody; a proportional split is the
one that does not need an argument. It means the car gets exactly the same mix
the dishwasher got at that moment, which is the only claim the meters support.

Everything here is **grid-side** energy -- what the wallbox pulled, not what
reached the battery -- because that is the side the house meters measure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# The buckets, in the order a user reads them.
SOURCE_PV = "pv"
SOURCE_BATTERY = "battery"
SOURCE_GRID = "grid"
SOURCE_UNKNOWN = "unknown"
SOURCES = (SOURCE_PV, SOURCE_BATTERY, SOURCE_GRID, SOURCE_UNKNOWN)

# Below this the site's supply reads as noise rather than as a mix worth
# splitting: a few watts of measurement offset would otherwise decide the whole
# attribution of an increment. In watts.
MIN_SUPPLY_W = 50.0


def _positive(value: Optional[float]) -> float:
    return 0.0 if value is None or value <= 0 else float(value)


def supply_shares(
    *,
    pv_w: Optional[float],
    grid_w: Optional[float],
    battery_w: Optional[float] = None,
) -> Optional[dict[str, float]]:
    """How the site is being supplied right now, as fractions summing to 1.

    Sign conventions, which are the whole difficulty:

    * ``grid_w`` -- **positive is import**, negative is export (Home Assistant's
      usual convention for a signed grid-power sensor).
    * ``battery_w`` -- **positive is discharge**, negative is charging. Optional:
      a house without storage passes ``None`` and gets a two-way split.
    * ``pv_w`` -- generation, never negative.

    Returns ``None`` when the mix cannot be known -- no grid reading, no PV
    reading, or a site that is supplying itself less than :data:`MIN_SUPPLY_W`.
    ``None`` is a real answer here: the caller books the energy as ``unknown``
    rather than guessing, because a fabricated solar share is worse than an
    admitted gap.
    """

    if pv_w is None or grid_w is None:
        return None

    imported = _positive(grid_w)
    exported = _positive(None if grid_w is None else -grid_w)
    discharging = _positive(battery_w)
    charging = _positive(None if battery_w is None else -battery_w)

    # PV that stayed on site, minus whatever of it went into the battery: that
    # part is not serving a load yet, it is being stored for later (and will be
    # attributed then, as battery).
    pv_on_site = max(_positive(pv_w) - exported, 0.0)
    pv_to_loads = max(pv_on_site - charging, 0.0)

    supply = pv_to_loads + discharging + imported
    if supply < MIN_SUPPLY_W:
        return None
    return {
        SOURCE_PV: pv_to_loads / supply,
        SOURCE_BATTERY: discharging / supply,
        SOURCE_GRID: imported / supply,
    }


def split_energy(
    energy_kwh: Optional[float],
    shares: Optional[dict[str, float]],
) -> dict[str, float]:
    """Divide one energy increment between the sources supplying it.

    An increment with no usable mix lands wholly in ``unknown``. Nothing is ever
    dropped: the buckets always add back up to the energy that was delivered, so
    a session's mix can be checked against its own total.
    """

    amount = 0.0 if not energy_kwh or energy_kwh <= 0 else float(energy_kwh)
    if amount <= 0:
        return {}
    if not shares:
        return {SOURCE_UNKNOWN: amount}
    return {
        source: amount * fraction
        for source, fraction in shares.items()
        if fraction > 0
    }


@dataclass
class MixAccumulator:
    """Running source mix for one charging session, in grid-side kWh.

    Accumulated alongside the cost for the same reason: the price and the mix
    both change while the car charges, and a single sum at the end would bill
    (and attribute) the whole session at whatever the last sample happened to
    be.
    """

    totals: dict[str, float] = field(default_factory=dict)

    def add(
        self,
        energy_kwh: Optional[float],
        shares: Optional[dict[str, float]],
    ) -> dict[str, float]:
        """Book an increment and return how it was split (for the cost step)."""

        split = split_energy(energy_kwh, shares)
        for source, amount in split.items():
            self.totals[source] = self.totals.get(source, 0.0) + amount
        return split

    @property
    def total_kwh(self) -> float:
        return sum(self.totals.values())

    @property
    def known_kwh(self) -> float:
        return self.total_kwh - self.totals.get(SOURCE_UNKNOWN, 0.0)

    def solar_percent(self) -> Optional[float]:
        """Share of the *attributable* energy that came off the roof.

        Measured against the known energy, not the session total: a session that
        was half unattributable would otherwise report a solar share that looks
        like a fact about the sun when it is really a fact about a missing
        sensor. ``None`` when nothing could be attributed at all.
        """

        known = self.known_kwh
        if known <= 0:
            return None
        return round(self.totals.get(SOURCE_PV, 0.0) / known * 100.0, 1)

    def as_record(self) -> Optional[dict[str, Any]]:
        """The stored mix, or ``None`` when there is nothing to say.

        A session whose energy is *entirely* unknown records no mix rather than
        a row of zeroes: "we could not tell" and "none of it was solar" are
        different statements and must not look alike.
        """

        if self.known_kwh <= 0:
            return None
        record: dict[str, Any] = {
            source: round(self.totals[source], 3)
            for source in SOURCES
            if self.totals.get(source)
        }
        solar = self.solar_percent()
        if solar is not None:
            record["solar_percent"] = solar
        return record

    def to_dict(self) -> dict[str, Any]:
        """Snapshot for the in-progress-session store (see ``sessions.py``)."""

        return {"totals": dict(self.totals)}

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> "MixAccumulator":
        """Rebuild a snapshotted accumulator, or start a fresh one."""

        if not isinstance(data, dict):
            return cls()
        raw = data.get("totals")
        if not isinstance(raw, dict):
            return cls()
        totals: dict[str, float] = {}
        for source in SOURCES:
            try:
                value = float(raw.get(source) or 0.0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                totals[source] = value
        return cls(totals=totals)


def merge_mix(
    records: list[Optional[dict[str, Any]]]
) -> Optional[dict[str, Any]]:
    """Sum several sessions' mixes into one, for a monthly total.

    Sessions with no mix contribute nothing at all -- not a zero -- so one
    charge recorded before the sensors were configured cannot drag a month's
    solar share down.
    """

    totals: dict[str, float] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        for source in SOURCES:
            value = record.get(source)
            if isinstance(value, (int, float)) and value > 0:
                totals[source] = totals.get(source, 0.0) + float(value)
    if not totals:
        return None
    accumulator = MixAccumulator(totals=totals)
    return accumulator.as_record()
