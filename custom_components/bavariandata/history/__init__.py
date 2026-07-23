"""Persistent vehicle history: the records BMW and Home Assistant both forget.

BMW's app keeps no usable detail and Home Assistant's recorder purges after ten
days, so charging sessions (and later trips) are kept here instead, derived from
the MQTT stream at no REST-quota cost.

Layout: ``models``, ``sessions`` and ``pricing`` are Home Assistant-free so the
maths is unit-testable without an HA install; ``store`` holds the only HA
dependency. See ``docs/roadmap.md`` for the phases this supports.
"""

from __future__ import annotations

from .models import SCHEMA_VERSION, ChargingSession, merge_session, prune_sessions
from .pricing import (
    CostAccumulator,
    PricingConfig,
    billable_energy,
    bmw_cost,
    fixed_cost,
    resolve_cost,
)
from .sessions import SessionBuilder

__all__ = [
    "SCHEMA_VERSION",
    "ChargingSession",
    "CostAccumulator",
    "PricingConfig",
    "SessionBuilder",
    "billable_energy",
    "bmw_cost",
    "fixed_cost",
    "merge_session",
    "prune_sessions",
    "resolve_cost",
]
