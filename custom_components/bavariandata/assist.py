"""What the car's Assist tools answer, and what they must never say.

A conversation agent (OpenAI, Google, Anthropic, Ollama...) that is given the
BavarianData LLM API can ask about the car: how it is, whether a drive fits its
range, what charging cost, how far it was driven. The answers come from the
same read services the card uses; this module turns them into small, plain
answers and strips what a cloud model has no business seeing.

Privacy is the point of the module: many agents run in somebody's cloud. No
VIN, no coordinates, no trip ids (they hold the VIN), no tracks, no power
curves. A place is an HA zone name or nothing.

No Home Assistant imports; the HA side is ``assist_api.py``, the rules here are
unit-tested directly (``tests/test_assist.py``).
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Optional, Sequence

__all__ = [
    "KM_PER_MILE",
    "MAX_SESSIONS",
    "charging_answer",
    "in_unit",
    "open_items",
    "range_check",
    "scrub",
    "status_answer",
]

KM_PER_MILE = 1.609344

# A list long enough to answer "what did charging cost this month", short
# enough not to fill the model's context with a year of sessions.
MAX_SESSIONS = 20

# Keys whose values are identifiers or positions. Dropped wherever they occur.
_HIDDEN_KEYS = frozenset(
    {
        "vin",
        "id",
        "trip_id",
        "track",
        "route",
        "power_curve",
        "latitude",
        "longitude",
        "lat",
        "lon",
        "coordinates",
        "gps",
    }
)
_VIN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")


def scrub(value: Any) -> Any:
    """``value`` without identifiers or positions, at any depth.

    A string that carries a VIN anyway (a label built from an id, say) has it
    replaced, so a field this module has never heard of can't leak one.
    """

    if isinstance(value, Mapping):
        return {
            key: scrub(item) for key, item in value.items() if str(key).lower() not in _HIDDEN_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [scrub(item) for item in value]
    if isinstance(value, str):
        return _VIN.sub("(hidden)", value)
    return value


def _mile_key(key: str) -> tuple[str, Optional[float]]:
    """The miles name of a distance key, and the factor its value takes.

    ``kwh_per_100km`` and ``cost_per_100km`` grow (more per longer unit);
    every other ``km`` figure shrinks. Keys without a ``km`` part stay as they
    are (``None``).
    """

    if "per_100km" in key:
        return key.replace("per_100km", "per_100mi"), KM_PER_MILE
    parts = key.split("_")
    if "km" not in parts:
        return key, None
    return "_".join("mi" if part == "km" else part for part in parts), 1 / KM_PER_MILE


def in_unit(value: Any, unit: str) -> Any:
    """``value`` with every distance figure in ``unit`` ("km" or "mi").

    The keys say the unit (``range_km`` becomes ``range_mi``), so the model
    never has to guess what a bare number means.
    """

    if unit != "mi":
        return value
    if isinstance(value, Mapping):
        converted: dict[str, Any] = {}
        for key, item in value.items():
            new_key, factor = _mile_key(str(key))
            if factor is not None and isinstance(item, (int, float)) and not isinstance(item, bool):
                item = round(item * factor, 1)
            converted[new_key] = in_unit(item, unit)
        return converted
    if isinstance(value, list):
        return [in_unit(item, unit) for item in value]
    return value


# --- What is open --------------------------------------------------------

# The lock state, every door, window and lid, and the tailgate lock: the same
# set the "Car left open at night" blueprint checks. The charge flap and the
# cable lock are left out on purpose -- they are open every night the car
# charges, which is not something to warn about.
CHECKED = re.compile(
    r"^vehicle\.(cabin\.door\.status|cabin\.door\.row\d\.\w+\.isOpen"
    r"|cabin\.window\.row\d\.\w+\.status|cabin\.sunroof\.overallStatus"
    r"|cabin\.convertible\.roofStatus|body\.(trunk|hood)(\.\w+)*\.isOpen"
    r"|body\.trunk\.isLocked)$"
)
_FINE = frozenset({"closed", "secured", "locked", "closedsecured", "invalid", "unknown", ""})
_ROWS = {"row1": "front", "row2": "rear", "row3": "third-row"}
_SIDES = {"driver": "driver", "passenger": "passenger"}


def _flag(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "on", "yes"):
        return True
    if text in ("false", "0", "off", "no"):
        return False
    return None


def _word(value: Any) -> str:
    return re.sub(r"[^a-z]", "", str(value).lower())


def _place(descriptor: str) -> str:
    """'front driver' for a row/side descriptor."""

    match = re.search(r"\.(row\d)\.(\w+?)\.", descriptor)
    if not match:
        return ""
    return (
        f"{_ROWS.get(match.group(1), match.group(1))} {_SIDES.get(match.group(2), match.group(2))}"
    )


def _open_item(descriptor: str, value: Any) -> Optional[str]:
    """What is open or unlocked according to one reading, or ``None``."""

    if descriptor.endswith(".isLocked"):
        return "tailgate unlocked" if _flag(value) is False else None
    if descriptor.endswith(".isOpen"):
        if _flag(value) is not True:
            return None
        if ".door.row" in descriptor:
            return f"{_place(descriptor)} door open"
        if descriptor.startswith("vehicle.body.hood"):
            return "hood open"
        part = descriptor.removeprefix("vehicle.body.trunk.").removesuffix("isOpen").rstrip(".")
        names = {
            "": "tailgate open",
            "door": "tailgate door open",
            "window": "tailgate window open",
        }
        return names.get(part, f"tailgate ({part.split('.')[0]}) open")
    word = _word(value)
    if word in _FINE:
        return None
    if descriptor == "vehicle.cabin.door.status":
        return "doors partly locked" if word == "selectivelocked" else "doors unlocked"
    if ".window.row" in descriptor:
        how = "open" if word == "open" else "partly open"
        return f"{_place(descriptor)} window {how}"
    if ".sunroof." in descriptor:
        return "sunroof open" if word == "open" else "sunroof tilted or partly open"
    if ".convertible." in descriptor:
        return "convertible roof open"
    return None


def open_items(readings: Mapping[str, Any]) -> list[str]:
    """Everything open or unlocked, in plain words, from the car's last readings.

    ``readings`` maps descriptors to raw values; anything outside the checked
    set is ignored, and a value the car hasn't sent yet counts as fine.
    """

    items = []
    # The lock state first: "unlocked" is the headline, the doors the detail.
    for descriptor in sorted(readings, key=lambda d: (d != "vehicle.cabin.door.status", d)):
        if CHECKED.match(descriptor):
            item = _open_item(descriptor, readings[descriptor])
            if item:
                items.append(item)
    return items


# --- Answers ---------------------------------------------------------------


def _number(value: Any, digits: int = 0) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), digits) if digits else round(float(value))


def status_answer(
    *,
    name: str,
    soc: Optional[float],
    charging: Optional[bool],
    plugged: Optional[bool],
    target_soc: Optional[float],
    charge_power_kw: Optional[float],
    minutes_to_target: Optional[int],
    range_km: Optional[float],
    bmw_range_km: Optional[float],
    odometer_km: Optional[float],
    zone: Optional[str],
    has_position: bool,
    trip_in_progress: bool,
    open_now: Sequence[str],
    soc_updated: Optional[str],
) -> dict[str, Any]:
    """The car as it is now. ``None`` fields are dropped: unknown, not zero."""

    if zone:
        location: Optional[str] = zone
    elif has_position:
        location = "not in any zone"
    else:
        location = None
    answer = {
        "car": name,
        "battery_percent": _number(soc),
        "charging": charging,
        "plugged_in": plugged,
        "charge_target_percent": _number(target_soc),
        "charging_power_kw": _number(charge_power_kw, 1) if charging else None,
        "minutes_to_charge_target": minutes_to_target if charging else None,
        "range_km": _number(range_km),
        "range_shown_by_car_km": _number(bmw_range_km),
        "odometer_km": _number(odometer_km),
        "location": location,
        "driving": trip_in_progress,
        "open_or_unlocked": list(open_now),
        "battery_reading_time": soc_updated,
    }
    return {key: value for key, value in answer.items() if value is not None}


def range_check(
    distance_km: float,
    *,
    round_trip: bool,
    range_km: Optional[float],
    full_range_km: Optional[float],
    soc: Optional[float],
) -> dict[str, Any]:
    """Whether a drive fits the range left, with a reserve.

    The reserve is a tenth of a full battery's range, at least 20 km: enough
    for a detour or a colder day than the ones the figure was measured on. A
    drive that doesn't fit says how much more charge it needs, when the full
    range is known.
    """

    needed = float(distance_km) * (2 if round_trip else 1)
    if range_km is None:
        return {"needed_km": round(needed), "verdict": "unknown", "reason": "no range reading yet"}
    reserve = max(0.1 * full_range_km, 20.0) if full_range_km else 20.0
    left = range_km - needed
    if left >= reserve:
        verdict = "fits"
    elif left >= 0:
        verdict = "tight"
    else:
        verdict = "does_not_fit"
    answer: dict[str, Any] = {
        "needed_km": round(needed),
        "range_km": round(range_km),
        "range_left_on_arrival_km": round(left),
        "reserve_km": round(reserve),
        "verdict": verdict,
    }
    if verdict != "fits" and full_range_km and soc is not None:
        short = needed + reserve - range_km
        answer["charge_needed_percent"] = min(
            100 - round(soc), max(1, round(100 * short / full_range_km))
        )
    return answer


def charging_answer(sessions: Iterable[Mapping[str, Any]], *, days: int) -> dict[str, Any]:
    """The charging sessions of the last ``days`` days, newest first, with totals.

    Totals cover every session; the list stops at :data:`MAX_SESSIONS` and
    says how many it left out. Costs are summed per currency.
    """

    rows = sorted(sessions, key=lambda s: str(s.get("start") or ""), reverse=True)
    energy = 0.0
    grid = 0.0
    costs: dict[str, float] = {}
    shown = []
    for session in rows:
        energy += session.get("energy_kwh") or 0.0
        grid += session.get("grid_kwh") or 0.0
        cost = session.get("cost") or {}
        if isinstance(cost.get("amount"), (int, float)) and cost.get("currency"):
            costs[cost["currency"]] = costs.get(cost["currency"], 0.0) + cost["amount"]
        if len(shown) < MAX_SESSIONS:
            item = {
                "start": session.get("start"),
                "end": session.get("end"),
                "place": (session.get("location") or {}).get("zone"),
                "battery_from_percent": _number(session.get("soc_start")),
                "battery_to_percent": _number(session.get("soc_end")),
                "kwh_into_battery": _number(session.get("energy_kwh"), 1),
                "kwh_from_grid": _number(session.get("grid_kwh"), 1),
                "cost": _number(cost.get("amount"), 2),
                "currency": cost.get("currency"),
                "ended_because": session.get("end_reason"),
            }
            shown.append({key: value for key, value in item.items() if value is not None})
    return {
        "days": days,
        "session_count": len(rows),
        "kwh_into_battery": round(energy, 1),
        "kwh_from_grid": round(grid, 1),
        "cost": {currency: round(amount, 2) for currency, amount in costs.items()},
        "sessions": shown,
        "sessions_not_listed": max(0, len(rows) - len(shown)),
    }
