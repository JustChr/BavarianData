"""BMW's own lifetime consumption figures, from the OBFCM counters.

Cars type-approved in the EU since about 2021 keep *On-Board Fuel and energy
Consumption Monitoring* counters: fuel burnt, grid energy charged and the
distances they cover, over the car's whole life. BMW serves them in the daily
REST container (they cannot be streamed), so they cost no extra quota.

For a plug-in hybrid they are the one honest source of a consumption figure.
The charging ledger divided by the odometer cannot be one, because the
odometer also counts the kilometres the engine drove (``summary.energy_balance``
withholds it there). The OBFCM counters split the distance by whether the
engine ran, so electric driving gets its own kWh per 100 km.

They are lifetime figures -- since the car was built, not since it was set up
here -- and only shown once the distance behind them is long enough to mean
something. Many cars send none (a MINI, #130's X5 and 118i); then this answers
``None`` and the views carry on without it.

Home Assistant-free on purpose.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

_FUEL = "vehicle.drivetrain.fuelSystem.consumptionOverLifeTime."
_GRID = "vehicle.drivetrain.electricEngine.charging.consumptionOverLifeTime."

FUEL_OVERALL = _FUEL + "overall.fuel"
DISTANCE_OVERALL = _FUEL + "overall.referenceDistance"
FUEL_DEPLETING = _FUEL + "inChargeDepleting.fuel"
DISTANCE_DEPLETING_ENGINE_ON = _FUEL + "inChargeDepleting.referenceDistanceEngineOn"
DISTANCE_DEPLETING_ENGINE_OFF = _FUEL + "inChargeDepleting.referenceDistanceEngineOff"
GRID_OVERALL = _GRID + "overall.gridEnergy"
GRID_DISTANCE_OVERALL = _GRID + "overall.referenceDistance"
GRID_ENGINE_OFF = _GRID + "engineOff.gridEnergy"
GRID_DISTANCE_ENGINE_OFF = _GRID + "engineOff.referenceDistance"

DESCRIPTORS = (
    FUEL_OVERALL,
    DISTANCE_OVERALL,
    FUEL_DEPLETING,
    DISTANCE_DEPLETING_ENGINE_ON,
    DISTANCE_DEPLETING_ENGINE_OFF,
    GRID_OVERALL,
    GRID_DISTANCE_OVERALL,
    GRID_ENGINE_OFF,
    GRID_DISTANCE_ENGINE_OFF,
)

# Below this, a lifetime figure is a delivery drive and a test lap.
MIN_DISTANCE_KM = 500.0
# A figure outside these is a counter that has not been filled in yet (zero
# litres over thousands of kilometres) or a unit we misread, not the car.
FUEL_RANGE = (1.0, 30.0)
ELECTRIC_RANGE = (8.0, 50.0)

Reader = Callable[[str], Optional[float]]


def _ratio(
    amount: Optional[float], distance: Optional[float], bounds: tuple[float, float]
) -> Optional[float]:
    if amount is None or distance is None or distance < MIN_DISTANCE_KM:
        return None
    value = amount / distance * 100.0
    if not bounds[0] <= value <= bounds[1]:
        return None
    return round(value, 1)


def lifetime(read: Reader, *, hybrid: bool = False) -> Optional[dict[str, Any]]:
    """BMW's lifetime figures for one car, or ``None`` when it has none.

    ``read`` returns a descriptor's current numeric value (litres, kWh, km).
    ``hybrid`` adds the electric figures; on any other car the grid counters
    are not looked at.
    """

    distance = read(DISTANCE_OVERALL)
    out: dict[str, Any] = {}
    fuel = _ratio(read(FUEL_OVERALL), distance, FUEL_RANGE)
    if fuel is not None and distance is not None:
        out["fuel_l_per_100km"] = fuel
        out["distance_km"] = round(distance, 0)

    if hybrid:
        electric_km = read(GRID_DISTANCE_ENGINE_OFF)
        electric = _ratio(read(GRID_ENGINE_OFF), electric_km, ELECTRIC_RANGE)
        if electric is not None and electric_km is not None:
            out["electric_kwh_per_100km"] = electric
            out["electric_km"] = round(electric_km, 0)
        grid_distance = read(GRID_DISTANCE_OVERALL) or distance
        grid = _ratio(read(GRID_OVERALL), grid_distance, (1.0, ELECTRIC_RANGE[1]))
        if grid is not None:
            out["grid_kwh_per_100km"] = grid
        total = grid_distance or distance
        if electric_km is not None and total and total >= MIN_DISTANCE_KM:
            share = electric_km / total * 100.0
            if 0.0 <= share <= 100.0:
                out["electric_share_percent"] = round(share, 0)
        if "distance_km" not in out and total and total >= MIN_DISTANCE_KM:
            out["distance_km"] = round(total, 0)

    return out or None
