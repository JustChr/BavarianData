"""What the setup and Configure screens work out, apart from Home Assistant.

``config_flow.py``, ``options_flow.py`` and ``options_cars.py`` build the forms
and apply what is saved; the decisions between are here, where the test suite
runs them (``tests/test_flow_answers.py``): the details shown when BMW refuses
a login, the entry a login becomes, what the activator's report says on screen,
the cars' labels in a picker, the evcc block for every car, which form values
are a car's own, a tariff missing its price, the mileage form's arithmetic and
checks, and the units each car is shown in.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Optional

from .const import OPTION_PRICE_ENTITY, OPTION_PRICE_FIXED, OPTION_PRICE_MODE
from .const import OPTION_TRIP_DETECTION, OPTION_TRIP_TRACK, TRIP_DETECTION_REPORTS
from .history.pricing import MODE_ENTITY, MODE_FIXED, MODE_NONE
from .mileage import (
    KEY_END,
    KEY_KIND,
    KEY_PER_YEAR,
    KEY_START,
    KEY_START_KM,
    KIND_LEASE,
    allowance_settings,
    anniversary,
    parse_date,
    yearly_period,
)
from .service_calendar import KM_PER_MILE

# The section holding the settings every car shares, on a screen with per-car
# settings (Trips, Charging costs, evcc bridge) of an account with two cars.
SHARED_SECTION = "all_vehicles"

_FUEL_LABELS = {"l": "l/100 km", "l_mpg_uk": "mpg (UK)", "gal_us": "mpg (US)"}


# --- setup ------------------------------------------------------------------


def auth_error_details(err: Any, requested_scope: str) -> str:
    """The details block on the authorize-failed screen.

    The raw OAuth pieces BMW returned (``device_flow.CardataAuthError``), so the
    user -- or a bug report -- sees what was rejected instead of one opaque line.
    """

    lines = [str(err)]
    if err.status is not None:
        lines.append(f"- HTTP status: {err.status}")
    if err.error_code:
        lines.append(f"- Error code: {err.error_code}")
    if err.error_description:
        lines.append(f"- Description: {err.error_description}")
    if err.correlation_id:
        lines.append(f"- BMW reference: {err.correlation_id}")
    # The scope is the usual culprit behind an `access_denied`, so always show
    # what we asked BMW to grant.
    lines.append(f"- Requested scope: {requested_scope}")
    return "\n".join(lines)


def token_entry_data(client_id: str, tokens: Mapping[str, Any], now: float) -> dict[str, Any]:
    """The config entry's data for a login BMW just granted."""

    return {
        "client_id": client_id,
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "id_token": tokens.get("id_token"),
        "expires_in": tokens.get("expires_in"),
        "scope": tokens.get("scope"),
        "gcid": tokens.get("gcid"),
        "token_type": tokens.get("token_type"),
        "received_at": now,
    }


def activation_done_placeholders(result: Any) -> dict[str, str]:
    """The activation-done screen's numbers, for a report or none.

    The text works for both outcomes; on a problem, the last reported error
    is an extra line (empty on success).
    """

    total = result.activated_count if result else None
    added = result.added_count if result else None
    ok = bool(result and result.activation_ok)
    error = "" if ok else (result.errors[-1] if result and result.errors else "")
    return {
        "field_count": str(total) if total is not None else "the selected",
        "added_count": str(added) if added is not None else "0",
        "error": f"\n\n⚠️ {error}" if error else "",
    }


def guided_done_placeholders(result: Any) -> dict[str, str]:
    """The last guided screen's numbers: fields turned on, and for which car."""

    count = result.activated_count if result else None
    return {
        "field_count": str(count) if count is not None else "the selected",
        "vehicle": (result.mapped_vehicle_id[:8] + "…")
        if result and result.mapped_vehicle_id
        else "your vehicle",
    }


def in_catalogue_order(labels: Iterable[str], picked: Iterable[str]) -> list[str]:
    """The picked clusters in the catalogue's order, whatever order they were ticked in."""

    chosen = set(picked)
    return [slug for slug in labels if slug in chosen]


def typed_client_id(value: Any) -> str:
    """A client id as typed, trimmed; anything that is not text is none."""

    return value.strip() if isinstance(value, str) else ""


# --- the per-car screens ----------------------------------------------------


def car_labels(named: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """``(vin, label)`` for each car, in name order.

    Two cars of the same model share a name, so a repeated name gets the end
    of its VIN -- the picker must never offer two identical entries.
    """

    named = list(named)
    counts: dict[str, int] = {}
    for _vin, name in named:
        counts[name] = counts.get(name, 0) + 1
    labelled = [(vin, f"{name} (…{vin[-4:]})" if counts[name] > 1 else name) for vin, name in named]
    return sorted(labelled, key=lambda item: item[1].casefold())


def split_shared(
    submitted: Mapping[str, Any], car_keys: tuple[str, ...]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(the car's own values, the shared ones)`` of a submitted form.

    With two cars the shared values arrive in their own section; with one,
    flat beside the car's, and every key that is not the car's is shared.
    """

    car = dict(submitted)
    shared = car.pop(SHARED_SECTION, None)
    if shared is None:
        shared = {key: car.pop(key) for key in list(car) if key not in car_keys}
    return car, shared


def price_error(shared: Mapping[str, Any]) -> Optional[str]:
    """The error a tariff missing its price gets, or ``None``."""

    mode = shared.get(OPTION_PRICE_MODE, MODE_NONE)
    if mode == MODE_FIXED and shared.get(OPTION_PRICE_FIXED) is None:
        return "price_required"
    if mode == MODE_ENTITY and not shared.get(OPTION_PRICE_ENTITY):
        return "price_entity_required"
    return None


def phone_route_follows(values: Mapping[str, Any]) -> bool:
    """True when Trips is followed by the phone screen: a car on end-of-trip
    reports that records its route, and only such a car."""

    return values.get(OPTION_TRIP_DETECTION) == TRIP_DETECTION_REPORTS and bool(
        values.get(OPTION_TRIP_TRACK)
    )


def evcc_vehicles(blocks: list[str]) -> str:
    """Every car's evcc block under one ``vehicles:`` key.

    Repeating the key in a single YAML file is invalid and evcc refuses to
    start on it, so each block after the first loses its own.
    """

    if not blocks:
        return "(no vehicle seen yet -- wait for the first stream message)"
    joined = "\n".join(
        block if index == 0 else block.split("\n", 1)[1] for index, block in enumerate(blocks)
    )
    return joined.strip()


def units_line(cars: Iterable[tuple[str, str, Optional[str]]]) -> str:
    """What each car is shown in right now: ``i5 eDrive40: mi · l/100 km``.

    ``cars`` holds ``(label, distance unit, fuel unit or None)``; the fuel unit
    is left out when no car on the account burns fuel.
    """

    lines = []
    for label, distance, fuel in cars:
        parts = [distance] if fuel is None else [distance, _FUEL_LABELS[fuel]]
        lines.append(f"{label}: {' · '.join(parts)}")
    return "\n".join(lines) or "—"


# --- mileage allowance ------------------------------------------------------


def to_km(value: Any, miles: bool) -> Optional[float]:
    """A distance typed in the car's unit, in km (empty is none)."""

    if value is None or value == "":
        return None
    number = float(value)
    return round(number * KM_PER_MILE if miles else number, 1)


def to_display(km: Optional[float], miles: bool) -> Optional[float]:
    """A stored km distance, whole, in the car's unit."""

    if km is None:
        return None
    return round(km / KM_PER_MILE if miles else km)


def mileage_typed(user_input: Mapping[str, Any], miles: bool) -> dict[str, Any]:
    """An allowance form's values as stored: dates as typed, distances in km."""

    return {
        KEY_START: user_input.get(KEY_START),
        KEY_END: user_input.get(KEY_END),
        KEY_PER_YEAR: to_km(user_input.get(KEY_PER_YEAR), miles),
        KEY_START_KM: to_km(user_input.get(KEY_START_KM), miles),
    }


def mileage_prefill(values: Mapping[str, Any], kind: str, today: date) -> dict[str, Any]:
    """A fresh form's dates, when the car has none stored for this kind.

    An empty date field reads as today in Home Assistant, which made start and
    end the same day; three years is the usual lease, and the help lines say
    what to change.
    """

    filled = dict(values)
    if not filled.get(KEY_START):
        filled[KEY_START] = today.isoformat()
        if kind == KIND_LEASE:
            filled[KEY_END] = (anniversary(today, today.year + 3) - timedelta(days=1)).isoformat()
    return filled


def mileage_to_store(
    kind: str, values: Mapping[str, Any], today: date
) -> tuple[dict[str, Any], dict[str, str]]:
    """``(what is stored, the form's errors)`` for a submitted allowance.

    A yearly limit has no end, and the odometer typed in belongs to the latest
    start of the year, so that is the start stored; later years read theirs
    from the log.
    """

    stored = {KEY_KIND: kind, **values}
    if kind != KIND_LEASE:
        stored[KEY_END] = None
        if (first := parse_date(values.get(KEY_START))) is not None and first <= today:
            stored[KEY_START] = yearly_period(first, today)[0].isoformat()
    if not values.get(KEY_PER_YEAR):
        return stored, {KEY_PER_YEAR: "invalid_int"}
    if allowance_settings(stored) is None:
        return stored, {KEY_END: "mileage_end_before_start"}
    return stored, {}
