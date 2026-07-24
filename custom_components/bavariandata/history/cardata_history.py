"""Map BMW CarData's REST charging history into our own session records.

Home Assistant-free on purpose (see the note in ``models.py``): the payload
shape and the enrichment/merge rules are pure data transforms, so they can be
unit-tested without an HA install. ``store.py`` orchestrates and persists.

BMW's ``GET /chargingHistory`` returns, per session, the *grid* energy actually
delivered, the displayed SoC either side, the odometer, a charging location and
a per-block power curve -- but no battery-side energy and (for home charging)
often no cost. So an imported session populates ``grid_kwh`` (the measured
figure), never the battery-side ``energy_kwh``, and gets a cost only if a fixed
tariff can be applied to the grid energy.

The tricky part is not the mapping but the merge: a charge that also streamed
live already has a session, recorded from the moment ``charging.status`` went
active -- which is seconds-to-minutes off BMW's ``startTime``, so the ids differ.
Importing must therefore *enrich the overlapping session in place* rather than
insert a duplicate.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from .models import ChargingSession

# Keep the stored curve small: BMW returns one block per power change (hundreds
# for a long AC charge), but the card only sketches a shape, and the record has
# to survive in a JSON store kept for years.
MAX_CURVE_POINTS = 120
# A live session's start (first CHARGINGACTIVE on the stream) and BMW's own
# ``startTime`` for the same plug-in are close but not equal; this window decides
# whether an imported session enriches an existing one or is added as new.
DEFAULT_MATCH_TOLERANCE_S = 30 * 60

CostFn = Callable[[Optional[float]], Optional[dict[str, Any]]]
# (latitude, longitude) -> resolved Home Assistant zone name, or None.
ZoneFn = Callable[[float, float], Optional[str]]


def _epoch_to_dt(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mileage_km(value: Any, units: Any) -> Optional[float]:
    km = _as_float(value)
    if km is None:
        return None
    unit = str(units or "").strip().upper()
    if unit in ("MILES", "MI"):
        return round(km * 1.609344, 1)
    return round(km, 1)


def _address_label(raw: dict[str, Any]) -> Optional[str]:
    """BMW's own human-readable address for the charge, or ``None``.

    BMW has already reverse-geocoded the location for us (``formattedAddress`` is
    a required field of ``ChargingLocationDto``), so a public charge can be
    labelled without ever touching Nominatim.
    """

    for key in ("formattedAddress", "streetAddress"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _location(raw: Any, zone_fn: Optional[ZoneFn]) -> Optional[dict[str, Any]]:
    """Resolve BMW's charge location to an HA zone, else BMW's address string.

    Mirrors the trip path (see ``geocoding.py``): a matching HA zone (Home/Work/
    ...) is preferred because it is what costing and the card key on, and only
    when the point falls in no zone is BMW's ``formattedAddress`` kept as a
    label. Either way the raw latitude/longitude are used and then dropped, never
    persisted -- a stored address *string* is no more sensitive than the trip
    endpoints we already keep, but a list of exact coordinates would be.
    """

    if not isinstance(raw, dict):
        return None
    lat = _as_float(raw.get("mapMatchedLatitude"))
    lon = _as_float(raw.get("mapMatchedLongitude"))
    zone = zone_fn(lat, lon) if (zone_fn and lat is not None and lon is not None) else None
    if zone:
        return {"zone": zone}
    address = _address_label(raw)
    if address:
        return {"zone": None, "address": address}
    if lat is not None and lon is not None:
        # Coordinates existed but resolved to nothing we can name -- record the
        # empty slot so the card shows "public" rather than "assumed home".
        return {"zone": None}
    return None


def _location_rank(location: Any) -> int:
    """How informative a stored location is: zone (2) > address (1) > nothing.

    Used by the merge so a re-import can *upgrade* a session's location (e.g. a
    legacy import that stored ``{"zone": None}`` before imports resolved zones)
    without ever downgrading a resolved zone back to an address or blank.
    """

    if not isinstance(location, dict):
        return 0
    if location.get("zone"):
        return 2
    if location.get("address"):
        return 1
    return 0


def _power_curve(
    blocks: Any, start_epoch: int
) -> tuple[list[list[float]], Optional[float]]:
    """[[seconds_since_start, kw], ...] downsampled, plus the peak kW.

    The peak is taken across every block before downsampling, so thinning the
    curve for storage never lowers the reported peak power.
    """

    if not isinstance(blocks, list):
        return [], None
    points: list[list[float]] = []
    peak: Optional[float] = None
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kw = _as_float(block.get("averagePowerGridKw"))
        if kw is None:
            continue
        peak = kw if peak is None else max(peak, kw)
        block_start = block.get("startTime")
        if block_start is None:
            continue
        try:
            offset = int(block_start) - int(start_epoch)
        except (TypeError, ValueError):
            continue
        if offset < 0:
            continue
        points.append([float(offset), round(kw, 3)])
    if len(points) > MAX_CURVE_POINTS:
        stride = (len(points) + MAX_CURVE_POINTS - 1) // MAX_CURVE_POINTS
        points = points[::stride]
    return points, (round(peak, 3) if peak is not None else None)


def session_from_cardata(
    vin: str,
    raw: Any,
    *,
    cost_fn: Optional[CostFn] = None,
    zone_fn: Optional[ZoneFn] = None,
) -> Optional[ChargingSession]:
    """One BMW history entry -> a ChargingSession, or ``None`` if unusable.

    A session that has not ended yet (no ``endTime``, no grid energy -- the
    top-of-list ongoing charge) is skipped: importing a half-finished charge
    would record a wrong total and then never be corrected.
    """

    if not isinstance(raw, dict):
        return None
    start_epoch = raw.get("startTime")
    end_epoch = raw.get("endTime")
    if start_epoch is None or end_epoch is None:
        return None
    start = _epoch_to_dt(start_epoch)
    end = _epoch_to_dt(end_epoch)
    if start is None or end is None or end < start:
        return None

    grid_kwh = _as_float(raw.get("energyConsumedFromPowerGridKwh"))
    curve, peak = _power_curve(raw.get("chargingBlocks"), int(start_epoch))
    return ChargingSession(
        vin=vin,
        start=start,
        end=end,
        soc_start=_as_float(raw.get("displayedStartSoc")),
        soc_end=_as_float(raw.get("displayedSoc")),
        grid_kwh=grid_kwh,
        peak_power_kw=peak,
        power_curve=curve,
        location=_location(raw.get("chargingLocation"), zone_fn),
        location_assumed=False,
        cost=cost_fn(grid_kwh) if cost_fn else None,
        mileage_km=_mileage_km(raw.get("mileage"), raw.get("mileageUnits")),
        enriched=True,
    )


def _interval(session: ChargingSession) -> tuple[datetime, datetime]:
    return session.start, session.end or session.start


def _find_overlap(
    sessions: list[ChargingSession], incoming: ChargingSession, tolerance: timedelta
) -> Optional[ChargingSession]:
    inc_start, inc_end = _interval(incoming)
    best: Optional[ChargingSession] = None
    best_gap: Optional[timedelta] = None
    for session in sessions:
        s_start, s_end = _interval(session)
        # Intervals overlap (padded by the tolerance), so back-to-back charges
        # stay distinct while the same charge under two clocks still matches.
        if inc_start <= s_end + tolerance and s_start <= inc_end + tolerance:
            gap = abs(s_start - inc_start)
            if best_gap is None or gap < best_gap:
                best, best_gap = session, gap
    return best


def _enrich_in_place(target: ChargingSession, incoming: ChargingSession) -> None:
    """Fold BMW's measured figures into an existing session, keeping its id."""

    if incoming.grid_kwh is not None:
        target.grid_kwh = incoming.grid_kwh
    if target.soc_start is None:
        target.soc_start = incoming.soc_start
    if target.soc_end is None:
        target.soc_end = incoming.soc_end
    if target.mileage_km is None:
        target.mileage_km = incoming.mileage_km
    # Adopt the incoming location when it is strictly more informative -- this is
    # what lets a re-import correct a legacy record that stored no zone (or only
    # BMW's address) once the point now resolves to a zone. A resolved zone is
    # never downgraded, so a live-recorded Home isn't lost to a blank import.
    if incoming.location and (
        target.location is None
        or _location_rank(incoming.location) > _location_rank(target.location)
    ):
        target.location = incoming.location
    if not target.power_curve and incoming.power_curve:
        target.power_curve = incoming.power_curve
    if target.peak_power_kw is None:
        target.peak_power_kw = incoming.peak_power_kw
    if target.end is None and incoming.end is not None:
        target.end = incoming.end
    # BMW's own billed cost (source "bmw") outranks a tariff estimate; otherwise
    # adopt the backfilled tariff cost so an imported session isn't left blank.
    if incoming.cost and (not target.cost or target.cost.get("source") != "bmw"):
        target.cost = incoming.cost
    target.enriched = True


def merge_cardata_sessions(
    existing: list[ChargingSession],
    incoming: list[ChargingSession],
    *,
    tolerance_s: int = DEFAULT_MATCH_TOLERANCE_S,
) -> tuple[list[ChargingSession], int, int]:
    """Enrich overlapping sessions in place, add the rest; newest first.

    Returns the merged list plus counts of (added, updated). Idempotent: a
    second import of the same data matches the sessions it created last time and
    updates them in place rather than duplicating.
    """

    tolerance = timedelta(seconds=max(0, tolerance_s))
    result = list(existing)
    added = updated = 0
    for inc in incoming:
        match = _find_overlap(result, inc, tolerance)
        if match is None:
            result.append(inc)
            added += 1
        else:
            _enrich_in_place(match, inc)
            updated += 1
    result.sort(key=lambda item: item.start, reverse=True)
    return result, added, updated
