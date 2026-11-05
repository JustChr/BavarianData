"""How much more the car uses in the cold, from its own trips.

The ledger (``efficiency.py``) answers "how much does the car use" without a
single trip; it cannot say *when* the energy went. This answers the seasonal
question per drive instead: each trip that carries both a consumption figure
(``Trip.consumption_kwh_per_100km``, which already refuses tiny SoC drops and
plug-in hybrids) and the weather along it (``route_weather``, opt-in) lands in
a 5 °C band of its time-weighted outside temperature.

A band's figure is its energy over its distance, so a long motorway run counts
for what it drove, not as one vote like a 3 km hop. A band shows a figure only
once :data:`BAND_MIN_KM` stand behind it, and the **cold penalty** -- below
:data:`COLD_BELOW_C` against the mild :data:`MILD_C` -- only once both sides
have :data:`PENALTY_MIN_KM`. Below those it is noise, and says so by being
``None``.

Figures are trip-side: the battery drop over the drive, so charging losses are
not in them, and they are not comparable one to one with the ledger's grid-side
figure. Home Assistant-free, like the rest of ``history/``.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Optional

from .trips import Trip

BAND_C = 5
# The open ends: everything below the first edge, everything from the last.
LOWEST_EDGE_C = 0
HIGHEST_EDGE_C = 25
BAND_MIN_KM = 30.0
COLD_BELOW_C = 5.0
MILD_C = (15.0, 25.0)
PENALTY_MIN_KM = 100.0

STATUS_OK = "ok"
STATUS_NO_WEATHER = "no_weather"
STATUS_PLUG_IN_HYBRID = "plug_in_hybrid"


def _band_floor(temp_c: float) -> Optional[int]:
    """The lower edge of a trip's band; ``None`` for the open band below 0 °C."""

    if temp_c < LOWEST_EDGE_C:
        return None
    return min(HIGHEST_EDGE_C, int(math.floor(temp_c / BAND_C)) * BAND_C)


def _rate(kwh: float, km: float) -> Optional[float]:
    return round(kwh / km * 100, 1) if km > 0 else None


def consumption_by_temperature(trips: Iterable[Trip], *, hybrid: bool = False) -> dict[str, Any]:
    """Consumption per outside-temperature band, and the cold penalty."""

    if hybrid:
        return {
            "status": STATUS_PLUG_IN_HYBRID,
            "bands": [],
            "cold_penalty_percent": None,
            "cold_kwh_per_100km": None,
            "mild_kwh_per_100km": None,
            "trips": 0,
        }
    rows: list[tuple[float, float, float]] = []
    for trip in trips:
        weather = trip.weather or {}
        temp = weather.get("temp_c")
        if not isinstance(temp, (int, float)) or trip.consumption_kwh_per_100km is None:
            continue
        if trip.energy_kwh is None or not trip.distance_km:
            continue  # never, while the figure exists; for the type checker
        rows.append((float(temp), trip.energy_kwh, trip.distance_km))

    edges: list[Optional[int]] = [None, *range(LOWEST_EDGE_C, HIGHEST_EDGE_C + 1, BAND_C)]
    bands = []
    for low in edges:
        inside = [(kwh, km) for temp, kwh, km in rows if _band_floor(temp) == low]
        km = sum(item[1] for item in inside)
        kwh = sum(item[0] for item in inside)
        high = LOWEST_EDGE_C if low is None else (None if low >= HIGHEST_EDGE_C else low + BAND_C)
        bands.append(
            {
                "from_c": low,
                "to_c": high,
                "trips": len(inside),
                "km": round(km, 1),
                "kwh_per_100km": _rate(kwh, km) if km >= BAND_MIN_KM else None,
            }
        )

    def _side(test: Any) -> tuple[float, float]:
        side = [(kwh, km) for temp, kwh, km in rows if test(temp)]
        return sum(item[0] for item in side), sum(item[1] for item in side)

    cold_kwh, cold_km = _side(lambda temp: temp < COLD_BELOW_C)
    mild_kwh, mild_km = _side(lambda temp: MILD_C[0] <= temp < MILD_C[1])
    cold = _rate(cold_kwh, cold_km) if cold_km >= PENALTY_MIN_KM else None
    mild = _rate(mild_kwh, mild_km) if mild_km >= PENALTY_MIN_KM else None
    penalty = round((cold / mild - 1) * 100) if cold is not None and mild else None
    return {
        "status": STATUS_OK if rows else STATUS_NO_WEATHER,
        "bands": bands,
        "cold_penalty_percent": penalty,
        "cold_kwh_per_100km": cold,
        "mild_kwh_per_100km": mild,
        "trips": len(rows),
    }
