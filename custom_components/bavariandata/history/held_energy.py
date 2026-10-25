"""Energy the SoC ceiling is holding back, kept with the mix and price it came at.

Home Assistant-free (see ``models.py``). The session's energy figure is the
integrated charging power bounded by what the pack's SoC says it can have taken
(``energy_ceiling_kwh`` in ``sessions.py``). While the bound holds, the
integration keeps running and the energy is real -- it just cannot be shown
until a SoC reading makes room for it.

A car that sleeps through its charge makes room only once, at the very end: it
reports the SoC at the start and again with the status that ends the charge.
Everything in between waits here. So each increment is booked into this pool at
the instant it arrives, with that instant's supply mix and price, and whatever
the ceiling later releases is drawn from the pool *in proportion*. An overnight
charge released at sunrise is then still filed as the grid it came from, not as
the morning's solar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .energy_mix import MixAccumulator
from .pricing import CostAccumulator


def _float(value: Any) -> float:
    try:
        return max(float(value or 0.0), 0.0)
    except TypeError, ValueError:
        return 0.0


@dataclass
class HeldEnergy:
    """The increments of one session not yet released into its mix and cost.

    ``battery_kwh`` is the battery-side energy held, the same side the ceiling
    measures, so a release can say what fraction of the pool it takes. The mix
    and the cost are grid-side, as everywhere else.
    """

    currency: str
    battery_kwh: float = 0.0
    mix: MixAccumulator = field(default_factory=MixAccumulator)
    cost: CostAccumulator = field(init=False)

    def __post_init__(self) -> None:
        self.cost = CostAccumulator(currency=self.currency)

    def hold(
        self,
        battery_kwh: float,
        billable_kwh: Optional[float],
        shares: Optional[dict[str, float]],
    ) -> dict[str, float]:
        """Book one increment; return how it split, for the caller to bill.

        ``billable_kwh`` of ``None`` holds the energy without a mix -- a charge
        away from home, where the house's meters describe the house.
        """

        if battery_kwh > 0:
            self.battery_kwh += battery_kwh
        if billable_kwh is None:
            return {}
        return self.mix.add(billable_kwh, shares)

    def release(
        self,
        battery_kwh: float,
        mix: Optional[MixAccumulator],
        cost: Optional[CostAccumulator],
    ) -> None:
        """Move the share of the pool that ``battery_kwh`` represents.

        Never more than the whole pool: energy that reached the total some
        other way (the gap credit across a restart) was never sampled, and is
        not given a mix or a price it did not have.
        """

        if battery_kwh <= 0 or self.battery_kwh <= 0:
            return
        fraction = min(battery_kwh / self.battery_kwh, 1.0)
        self.battery_kwh -= self.battery_kwh * fraction
        for source, amount in list(self.mix.totals.items()):
            moved = amount * fraction
            self.mix.totals[source] = amount - moved
            if mix is not None:
                mix.totals[source] = mix.totals.get(source, 0.0) + moved
        held = self.cost
        amount = held.amount * fraction
        priced = held.priced_kwh * fraction
        unpriced = held.unpriced_kwh * fraction
        held.amount -= amount
        held.priced_kwh -= priced
        held.unpriced_kwh -= unpriced
        if cost is not None:
            cost.amount += amount
            cost.priced_kwh += priced
            cost.unpriced_kwh += unpriced

    def to_dict(self) -> dict[str, Any]:
        """Snapshot for the in-progress-session store, beside the mix and cost."""

        return {
            "battery_kwh": self.battery_kwh,
            "mix": self.mix.to_dict(),
            "cost": self.cost.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]], *, currency: str) -> "HeldEnergy":
        """Rebuild a snapshotted pool, or start an empty one.

        A snapshot from before the pool existed has none; the session then
        resumes with nothing held, as it did then.
        """

        if not isinstance(data, dict):
            return cls(currency=currency)
        held = cls(
            currency=currency,
            battery_kwh=_float(data.get("battery_kwh")),
            mix=MixAccumulator.from_dict(data.get("mix")),
        )
        held.cost = CostAccumulator.from_dict(data.get("cost"), currency=currency)
        return held
