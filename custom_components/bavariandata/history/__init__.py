"""Persistent vehicle history: the records BMW and Home Assistant both forget.

BMW's app keeps no usable detail and Home Assistant's recorder purges after ten
days, so charging sessions (and later trips) are kept here instead, derived from
the MQTT stream at no REST-quota cost.

Layout: ``models``, ``sessions`` and ``pricing`` are Home Assistant-free so the
maths is unit-testable without an HA install; ``store`` holds the only HA
dependency. See ``docs/roadmap.md`` for the phases this supports.
"""

from __future__ import annotations

from .classify import classify_trip
from .geocoding import ReverseGeocoder, format_address
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
from .trips import Trip, merge_trip, place, prune_trips
from .trip_builder import TripBuilder, is_noise_trip

__all__ = [
    "SCHEMA_VERSION",
    "ChargingSession",
    "CostAccumulator",
    "PricingConfig",
    "ReverseGeocoder",
    "SessionBuilder",
    "Trip",
    "TripBuilder",
    "billable_energy",
    "bmw_cost",
    "classify_trip",
    "fixed_cost",
    "format_address",
    "is_noise_trip",
    "merge_session",
    "merge_trip",
    "place",
    "prune_sessions",
    "prune_trips",
    "resolve_cost",
]
