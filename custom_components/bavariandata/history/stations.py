"""The places a car charges at, worked out from its stored charging sessions.

Home Assistant-free on purpose (see the note in ``models.py``): the grouping
and the performance figures are pure functions over ``ChargingSession``
records, unit-tested in ``tests/test_stations.py``.

Nothing here is stored. A place is derived from the sessions every time it is
asked for, so retention, a re-import or a deleted history change the places
with them and nothing is ever orphaned.

How sessions become places, most reliable first:

1. A session in an HA zone belongs to that zone ("Home", "Work").
2. One with coordinates (only kept when *Remember charging locations* is on)
   joins the nearest place within ``MERGE_RADIUS_M``, else starts one.
3. One with only BMW's address joins the coordinate place carrying the same
   address, else the place named by that address.
4. One with no position at all is a charge at home, the same rule the tariff
   and the wallbox meter follow (``sessions.house_supplies_this_session``),
   flagged ``assumed``.
5. A known position that has neither zone, coordinates nor address can't be
   told apart from any other such charge: they share one ``unnamed`` place,
   which reports visits and energy but no performance, since it mixes
   chargers.

Performance never compares unlike charges. Each visit is AC or DC
(:func:`charge_kind`), and the figures a place reports come from its
dominant kind only. A median needs ``MIN_VISITS_FOR_MEDIAN`` usable visits;
with fewer the card says "1 visit" instead of presenting one charge as typical.
"""

from __future__ import annotations

import math
from statistics import median
from typing import Any, Iterable, Optional

from .models import ChargingSession

# Two charges this close together are the same charging place. A charging park
# spreads its posts over tens of metres and GPS adds its own jitter; two
# different sites are rarely this close.
MERGE_RADIUS_M = 75.0
# No AC charging a BMW can do goes above 22 kW (the i3/i4/iX top out at 11 or
# 22 kW AC). A peak above that is a DC charger.
AC_MAX_KW = 22.0
MIN_VISITS_FOR_MEDIAN = 2
# "Minutes per 10 %" is only fair over the steep part of a charge. Above 80 %
# a DC charge tapers to a fraction of its rate, so a visit that went further
# would make a good charger look slow. Below ``MIN_SOC_SPAN`` the minute count
# is dominated by the handshake and rounding of the displayed SoC.
MAX_SOC_FOR_RATE = 80.0
MIN_SOC_SPAN = 20.0
# Power curves shown when a place is expanded: the latest few visits only, each
# thinned to a sketch.
MAX_CURVES = 5
MAX_CURVE_POINTS = 60

KIND_AC = "ac"
KIND_DC = "dc"


# What a car streams about the plug that is charging it. ``dcStatus`` covers the
# DC socket only; ``charging.method`` names the AC plug (BMW lists no DC value).
CHARGE_METHOD_DESCRIPTORS = (
    "vehicle.body.chargingPort.dcStatus",
    "vehicle.drivetrain.electricEngine.charging.method",
)


def streamed_charge_method(values: dict[str, Any]) -> Optional[str]:
    """ "dc" or "ac" from the car's own plug descriptors, ``None`` if they don't say.

    ``values`` maps descriptor -> current streamed value. A connected DC socket
    is DC; an AC plug type is AC. Anything else -- no plug, ``INVALID``, a
    descriptor the car doesn't stream -- is left to the peak power.
    """

    dc = values.get("vehicle.body.chargingPort.dcStatus")
    if isinstance(dc, str) and dc.strip().lower() == "connected":
        return KIND_DC
    method = values.get("vehicle.drivetrain.electricEngine.charging.method")
    if isinstance(method, str) and method.strip().lower().startswith("ac_"):
        return KIND_AC
    return None


def charge_kind(session: ChargingSession) -> Optional[str]:
    """AC or DC for one visit, or ``None`` when nothing says which.

    The car's own word wins when it sent one (recorded on the session as
    ``charge_method`` at the start of a live charge). Otherwise the peak power
    decides: BMW's ``charging.method`` names AC plugs only, and its charging
    history carries no plug type at all, so a peak above :data:`AC_MAX_KW` is
    the one signal every charge has.
    """

    if session.charge_method in (KIND_AC, KIND_DC):
        return session.charge_method
    peak = session.peak_power_kw
    if peak is None:
        return None
    return KIND_DC if peak > AC_MAX_KW else KIND_AC


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres (haversine)."""

    radius = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(min(1.0, a)))


def _coords(location: Any) -> Optional[tuple[float, float]]:
    if not isinstance(location, dict):
        return None
    lat, lon = location.get("lat"), location.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    return float(lat), float(lon)


class _Place:
    """Sessions collected under one place while grouping."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.zone: Optional[str] = None
        self.assumed = False
        self.unnamed = False
        self.sessions: list[ChargingSession] = []
        self.points: list[tuple[float, float]] = []

    def add(self, session: ChargingSession) -> None:
        self.sessions.append(session)
        point = _coords(session.location)
        if point is not None:
            self.points.append(point)

    def centroid(self) -> Optional[tuple[float, float]]:
        if not self.points:
            return None
        return (
            sum(point[0] for point in self.points) / len(self.points),
            sum(point[1] for point in self.points) / len(self.points),
        )

    def _most_common(self, field: str) -> Optional[str]:
        counts: dict[str, int] = {}
        for session in self.sessions:
            value = (session.location or {}).get(field)
            if isinstance(value, str) and value.strip():
                counts[value] = counts.get(value, 0) + 1
        if not counts:
            return None
        return max(counts.items(), key=lambda item: (item[1], item[0]))[0]

    @property
    def address(self) -> Optional[str]:
        return self._most_common("address")

    @property
    def provider(self) -> Optional[str]:
        return self._most_common("provider")


def _group(sessions: Iterable[ChargingSession], home_zone: Optional[str]) -> list[_Place]:
    places: dict[str, _Place] = {}
    coordinate_places: list[_Place] = []
    address_only: list[tuple[ChargingSession, str]] = []
    counter = 0

    def place(key: str) -> _Place:
        if key not in places:
            places[key] = _Place(key)
        return places[key]

    # Oldest first, so a place's id and centroid don't depend on query order.
    for session in sorted(sessions, key=lambda item: item.start):
        location = session.location
        if not isinstance(location, dict):
            found = place(f"zone:{home_zone or 'home'}")
            found.zone = found.zone or home_zone
            found.assumed = True
            found.add(session)
            continue
        zone = location.get("zone")
        if zone:
            found = place(f"zone:{zone}")
            found.zone = zone
            found.add(session)
            continue
        point = _coords(location)
        if point is not None:
            nearest: Optional[_Place] = None
            best = MERGE_RADIUS_M
            for candidate in coordinate_places:
                centre = candidate.centroid()
                if centre is None:
                    continue
                gap = distance_m(point[0], point[1], centre[0], centre[1])
                if gap <= best:
                    nearest, best = candidate, gap
            if nearest is None:
                counter += 1
                nearest = place(f"point:{counter}")
                coordinate_places.append(nearest)
            nearest.add(session)
            continue
        if address := location.get("address"):
            address_only.append((session, address))
            continue
        found = place("unnamed")
        found.unnamed = True
        found.add(session)

    for session, address in address_only:
        target = next((p for p in coordinate_places if p.address == address), None)
        if target is None:
            target = place(f"address:{address}")
        target.add(session)

    # A place that was only ever "assumed" Home but also holds real Home
    # charges is simply Home; the flag means "every visit here was assumed".
    for found in places.values():
        if found.assumed:
            found.assumed = all(not isinstance(s.location, dict) for s in found.sessions)
    return list(places.values())


def _visit_power_kw(session: ChargingSession) -> Optional[float]:
    """The average power one visit delivered, or ``None`` if it can't be trusted.

    A session that started late or lost a stretch to a restart has an energy
    figure that is only a floor, so its average would understate the charger.
    """

    if session.late_start or session.interrupted:
        return None
    duration = session.duration_s
    energy = session.effective_energy_kwh
    if not duration or energy is None or energy <= 0:
        return None
    return energy / (duration / 3600.0)


def _minutes_per_10(session: ChargingSession) -> Optional[float]:
    if session.late_start or session.interrupted:
        return None
    if session.soc_start is None or session.soc_end is None or not session.duration_s:
        return None
    span = session.soc_end - session.soc_start
    if span < MIN_SOC_SPAN or session.soc_end > MAX_SOC_FOR_RATE:
        return None
    return (session.duration_s / 60.0) / (span / 10.0)


def _figures(sessions: list[ChargingSession]) -> dict[str, Any]:
    """Average power and minutes per 10 % across visits, with their counts."""

    powers = [p for p in (_visit_power_kw(s) for s in sessions) if p is not None]
    rates = [r for r in (_minutes_per_10(s) for s in sessions) if r is not None]
    return {
        "avg_kw": _summary(powers),
        "power_visits": len(powers),
        "minutes_per_10": _summary(rates),
        "rate_visits": len(rates),
    }


def _summary(values: list[float]) -> Optional[float]:
    """The median of two or more visits, the value itself for exactly one."""

    if not values:
        return None
    return round(median(values) if len(values) >= MIN_VISITS_FOR_MEDIAN else values[0], 1)


def _thin(curve: list[list[float]]) -> list[list[float]]:
    if len(curve) <= MAX_CURVE_POINTS:
        return [list(point) for point in curve]
    stride = math.ceil(len(curve) / MAX_CURVE_POINTS)
    return [list(point) for point in curve[::stride]]


def _iso(session: ChargingSession) -> str:
    return session.start.isoformat()


def _describe(found: _Place) -> dict[str, Any]:
    sessions = sorted(found.sessions, key=lambda item: item.start, reverse=True)
    kinds = [charge_kind(s) for s in sessions]
    known = [k for k in kinds if k is not None]
    dominant: Optional[str] = None
    if known:
        dominant = KIND_DC if known.count(KIND_DC) > known.count(KIND_AC) else KIND_AC
    kind = None
    if known:
        kind = dominant if len(set(known)) == 1 else "mixed"
    same_kind = [s for s, k in zip(sessions, kinds, strict=True) if k == dominant or k is None]

    durations = [s.duration_s / 60.0 for s in sessions if s.duration_s]
    energy = [s.effective_energy_kwh for s in sessions if s.effective_energy_kwh is not None]
    centre = found.centroid()
    described: dict[str, Any] = {
        "id": found.key,
        "zone": found.zone,
        "address": found.address,
        "provider": found.provider,
        "latitude": None if centre is None else round(centre[0], 5),
        "longitude": None if centre is None else round(centre[1], 5),
        "assumed": found.assumed,
        "unnamed": found.unnamed,
        "kind": kind,
        "visits": len(sessions),
        "first_visit": _iso(sessions[-1]),
        "last_visit": _iso(sessions[0]),
        "energy_kwh": round(sum(energy), 1) if energy else None,
        "last_energy_kwh": sessions[0].effective_energy_kwh,
        "typical_minutes": round(median(durations)) if durations else None,
    }
    if found.unnamed:
        # Several chargers under one label: their speeds say nothing together.
        described.update(
            avg_kw=None, power_visits=0, best_kw=None, minutes_per_10=None, rate_visits=0
        )
        described["preconditioning"] = None
        described["curves"] = []
        return described

    described.update(_figures(same_kind))
    powers = [p for p in (_visit_power_kw(s) for s in same_kind) if p is not None]
    described["best_kw"] = round(max(powers), 1) if len(powers) >= MIN_VISITS_FOR_MEDIAN else None
    # Only BMW's charging history says whether the battery was preconditioned,
    # so the split exists only for imported visits, and only when both sides
    # have one -- a lone "preconditioned" figure has nothing to be compared with.
    on = [s for s in same_kind if s.preconditioned is True]
    off = [s for s in same_kind if s.preconditioned is False]
    described["preconditioning"] = (
        {"on": {"visits": len(on), **_figures(on)}, "off": {"visits": len(off), **_figures(off)}}
        if on and off
        else None
    )
    described["curves"] = [
        {"start": _iso(s), "kind": charge_kind(s), "points": _thin(s.power_curve)}
        for s in same_kind
        if s.power_curve
    ][:MAX_CURVES]
    return described


def charging_places(
    sessions: Iterable[ChargingSession], *, home_zone: Optional[str] = None
) -> list[dict[str, Any]]:
    """Every place the sessions were charged at, most visited first.

    ``home_zone`` is the name Home Assistant gives the home zone; sessions with
    no position at all are counted there, flagged ``assumed``. Open sessions
    (no end yet) are left out: their energy and duration are still growing.
    """

    finished = [s for s in sessions if s.end is not None]
    places = [_describe(found) for found in _group(finished, home_zone)]
    # Most visits first; among equals, the most recently visited first. The
    # unnamed catch-all is not a place anyone can go back to, so it goes last.
    places.sort(key=lambda item: (item["visits"], item["last_visit"]), reverse=True)
    places.sort(key=lambda item: item["unnamed"])
    return places
