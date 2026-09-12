"""Charging cost arithmetic.

Home Assistant-free (see ``models.py``). Cost is accumulated *incrementally*,
alongside the energy integration, rather than computed once at the end: with a
dynamic tariff the price changes during the session, so a single end-of-session
multiplication would bill the whole plug-in at whatever the last price happened
to be. Sampling the price as energy arrives also means we never have to query
recorder history, which may already have been purged.

The guiding rule is that a wrong cost is worse than no cost: every path here can
return ``None``, and a partially-priced session says so instead of quietly
understating.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# The option keys are read here and written by the options flow; sharing the
# constants keeps a rename from silently reverting everyone to the defaults.
from ..const import (
    OPTION_BATTERY_POWER_ENTITY,
    OPTION_BATTERY_POWER_INVERT,
    OPTION_CHARGING_LOSS_PERCENT,
    OPTION_GRID_ENERGY_ENTITY,
    OPTION_GRID_POWER_ENTITY,
    OPTION_PRICE_CURRENCY,
    OPTION_PRICE_ENTITY,
    OPTION_PRICE_FIXED,
    OPTION_PRICE_MODE,
    OPTION_PRICE_SOLAR,
    OPTION_PV_POWER_ENTITY,
)

# Below this share of unpriced energy the total is treated as trustworthy --
# a single missed sample at a tariff boundary shouldn't flag a whole session.
PARTIAL_TOLERANCE = 0.05

MODE_NONE = "none"
MODE_FIXED = "fixed"
MODE_ENTITY = "entity"
DEFAULT_CURRENCY = "EUR"


@dataclass
class PricingConfig:
    """How this entry turns kWh into money.

    Parsed from the config entry's options, kept HA-free so the parsing rules
    (and the "is this even configured?" question) are testable.
    """

    mode: str = MODE_NONE
    currency: str = DEFAULT_CURRENCY
    fixed_price: Optional[float] = None
    price_entity: Optional[str] = None
    grid_energy_entity: Optional[str] = None
    loss_percent: float = 0.0
    # Where the energy came from (see ``energy_mix.py``). Independent of the
    # tariff: a user with no price set can still see their solar share, and a
    # user with a tariff and no PV sensors sees costs exactly as before.
    grid_power_entity: Optional[str] = None
    pv_power_entity: Optional[str] = None
    battery_power_entity: Optional[str] = None
    battery_power_invert: bool = False
    solar_price: Optional[float] = None

    @property
    def mix_enabled(self) -> bool:
        """True when a charge's energy can be attributed to its sources.

        Both meters are required. With only one of them the split would have to
        assume the other, which is exactly the guesswork this feature exists to
        replace.
        """

        return bool(self.grid_power_entity and self.pv_power_entity)

    @property
    def enabled(self) -> bool:
        """True when a cost can actually be computed.

        A mode set to ``fixed`` without a price, or ``entity`` without an
        entity, is treated as not configured rather than as zero.
        """

        if self.mode == MODE_FIXED:
            return self.fixed_price is not None
        if self.mode == MODE_ENTITY:
            return bool(self.price_entity)
        return False

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> "PricingConfig":
        def _float(key: str) -> Optional[float]:
            try:
                value = options.get(key)
                return None if value is None or value == "" else float(value)
            except (TypeError, ValueError):
                return None

        return cls(
            mode=options.get(OPTION_PRICE_MODE) or MODE_NONE,
            currency=options.get(OPTION_PRICE_CURRENCY) or DEFAULT_CURRENCY,
            fixed_price=_float(OPTION_PRICE_FIXED),
            price_entity=options.get(OPTION_PRICE_ENTITY) or None,
            grid_energy_entity=options.get(OPTION_GRID_ENERGY_ENTITY) or None,
            loss_percent=_float(OPTION_CHARGING_LOSS_PERCENT) or 0.0,
            grid_power_entity=options.get(OPTION_GRID_POWER_ENTITY) or None,
            pv_power_entity=options.get(OPTION_PV_POWER_ENTITY) or None,
            battery_power_entity=options.get(OPTION_BATTERY_POWER_ENTITY) or None,
            battery_power_invert=bool(options.get(OPTION_BATTERY_POWER_INVERT)),
            solar_price=_float(OPTION_PRICE_SOLAR),
        )


@dataclass
class CostAccumulator:
    """Running cost for one charging session."""

    currency: str
    amount: float = 0.0
    priced_kwh: float = 0.0
    unpriced_kwh: float = 0.0

    def add(self, energy_kwh: Optional[float], price_per_kwh: Optional[float]) -> None:
        """Bill an energy delta at the price in force right now.

        A delta that arrives while the price is unknown (price entity
        unavailable, or still starting up) is remembered as unpriced instead of
        being billed at zero.
        """

        if not energy_kwh or energy_kwh <= 0:
            return
        if price_per_kwh is None:
            self.unpriced_kwh += energy_kwh
            return
        # Deliberately unrounded: rounding each delta would drift over the
        # hundreds of samples in a long session. Rounding happens in as_cost().
        self.amount += energy_kwh * price_per_kwh
        self.priced_kwh += energy_kwh

    def to_dict(self) -> dict[str, Any]:
        """Snapshot the running cost so a restart doesn't bill from zero."""

        return {
            "currency": self.currency,
            "amount": self.amount,
            "priced_kwh": self.priced_kwh,
            "unpriced_kwh": self.unpriced_kwh,
        }

    @classmethod
    def from_dict(
        cls, data: Optional[dict[str, Any]], *, currency: str
    ) -> "CostAccumulator":
        """Rebuild a snapshotted accumulator, or start a fresh one.

        ``currency`` is the one configured *now*. A snapshot taken under a
        different one is dropped rather than added to: money in two currencies
        does not sum, and half a session billed in each would be a wrong number
        wearing a right one's clothes. The energy already delivered is not lost
        -- it simply reverts to unpriced, which the record reports honestly.
        """

        fresh = cls(currency=currency)
        if not isinstance(data, dict) or data.get("currency") != currency:
            if isinstance(data, dict):
                try:
                    fresh.unpriced_kwh = float(data.get("priced_kwh") or 0.0) + float(
                        data.get("unpriced_kwh") or 0.0
                    )
                except (TypeError, ValueError):
                    fresh.unpriced_kwh = 0.0
            return fresh
        try:
            fresh.amount = float(data.get("amount") or 0.0)
            fresh.priced_kwh = float(data.get("priced_kwh") or 0.0)
            fresh.unpriced_kwh = float(data.get("unpriced_kwh") or 0.0)
        except (TypeError, ValueError):
            return cls(currency=currency)
        return fresh

    @property
    def total_kwh(self) -> float:
        return self.priced_kwh + self.unpriced_kwh

    @property
    def is_partial(self) -> bool:
        total = self.total_kwh
        if not total:
            return False
        return (self.unpriced_kwh / total) > PARTIAL_TOLERANCE

    def as_cost(self) -> Optional[dict[str, Any]]:
        """The cost record, or ``None`` when nothing could be priced."""

        if self.priced_kwh <= 0:
            return None
        cost: dict[str, Any] = {
            "amount": round(self.amount, 2),
            "currency": self.currency,
            "source": "tariff",
        }
        if self.is_partial:
            # The consumer decides what to do with an understated total; it must
            # never be presented as if it were complete.
            cost["partial"] = True
            cost["unpriced_kwh"] = round(self.unpriced_kwh, 3)
        return cost


def fixed_cost(
    energy_kwh: Optional[float], price_per_kwh: Optional[float], currency: str
) -> Optional[dict[str, Any]]:
    """Cost at a single flat price -- for a fixed tariff or a backfilled session."""

    if not energy_kwh or energy_kwh <= 0 or price_per_kwh is None:
        return None
    return {
        "amount": round(energy_kwh * price_per_kwh, 2),
        "currency": currency,
        "source": "tariff",
    }


def bmw_cost(payload: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Normalise BMW's ``chargingCostInformation`` into our cost record.

    BMW bills the session itself for public charging, so where it has a figure
    that figure wins -- it reflects what was actually charged to the card,
    including session fees our tariff maths knows nothing about.
    """

    if not payload:
        return None
    amount = payload.get("calculatedChargingCost")
    currency = payload.get("currency")
    if amount is None or not currency:
        return None
    cost: dict[str, Any] = {
        "amount": round(float(amount), 2),
        "currency": currency,
        "source": "bmw",
    }
    savings = payload.get("calculatedSavings")
    if savings is not None:
        cost["savings"] = round(float(savings), 2)
    return cost


def resolve_cost(
    *,
    bmw: Optional[dict[str, Any]] = None,
    accumulated: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Pick the cost to store: BMW's own figure, then ours, then nothing."""

    return bmw or accumulated or None


def billable_energy(
    *,
    battery_kwh: Optional[float],
    grid_kwh: Optional[float] = None,
    loss_percent: float = 0.0,
) -> tuple[Optional[float], str]:
    """Return the energy to bill and a label for where it came from.

    A measured grid figure always wins. Otherwise we bill the battery-side
    energy we integrated, optionally grossed up by a user-supplied loss
    percentage -- which defaults to zero because inventing a plausible-looking
    correction would present an estimate as a measurement.
    """

    if grid_kwh is not None and grid_kwh > 0:
        return round(grid_kwh, 3), "grid"
    if battery_kwh is None or battery_kwh <= 0:
        return None, "none"
    if loss_percent:
        grossed = battery_kwh / (1 - min(loss_percent, 90.0) / 100.0)
        return round(grossed, 3), "battery_adjusted"
    return round(battery_kwh, 3), "battery"
