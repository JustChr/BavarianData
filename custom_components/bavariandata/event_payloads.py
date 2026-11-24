"""What each of the integration's events carries: the bus's public contract.

Automations, Node-RED flows and the shipped blueprints read these fields by
name, and the wiki's Events table (Feature-Automations, EN and DE) lists them.
Each event's data is declared here once; the engines build it with these
types, so mypy checks every key where it is set, and the tests hold the fired
events (``tests/test_event_payloads.py``) and the wiki to the same lists.

``vehicle`` (the car's name) is added by ``coordinator._fire`` to every event,
so it is in no type below.

No Home Assistant imports. Deliberately without ``from __future__ import
annotations``: it turns every annotation into a string, and a ``TypedDict``
then counts its ``NotRequired`` fields as required at runtime (mypy still reads
them right), which is what :func:`shape_error` relies on. Python 3.14 defers
annotations anyway, so nothing else changes.
"""

from collections.abc import Mapping
from typing import Any, NotRequired, TypedDict

from .const import (
    EVENT_ALARM,
    EVENT_CHARGING_COMPLETE,
    EVENT_CHARGING_INTERRUPTED,
    EVENT_CHARGING_PAUSED,
    EVENT_CHARGING_RESUMED,
    EVENT_CHARGING_STARTED,
    EVENT_CHARGING_STOPPED,
    EVENT_CHECK_CONTROL,
    EVENT_PRECONDITIONING,
    EVENT_SITUATION,
    EVENT_TRIP_ENDED,
    EVENT_VEHICLE_REPORT,
    EVENT_ZONE_ARRIVED,
    EVENT_ZONE_LEFT,
)

# Added to every event when it is fired, not by the engine that builds it.
ADDED_ON_FIRE = frozenset({"vehicle"})


class CarEvent(TypedDict):
    """What every event names: the car and the account."""

    vin: str
    entry_id: str


# --- charging ------------------------------------------------------------------


class ChargingEvent(CarEvent):
    """``charging_started``, ``_paused``, ``_resumed``: where the charge stands."""

    status: str | None
    soc: float | None
    target_soc: float | None


class ChargingEndEvent(ChargingEvent):
    """``charging_stopped`` and ``_complete``: the closed session too.

    The session's figures are missing only when no record was kept for it.
    """

    energy_kwh: NotRequired[float | None]
    # The session's cost, as its record holds it (amount, currency, source).
    cost: NotRequired[dict[str, Any] | None]
    session_id: NotRequired[str]
    complete: bool


class ChargingInterruptedEvent(ChargingEndEvent):
    """``charging_interrupted``: ended short of the target, cable still in."""

    reason: str | None


# --- zones and trips --------------------------------------------------------------


class ZoneLeftEvent(CarEvent):
    zone: str
    zone_entity_id: str
    trip_start: str | None
    soc: float | None


# ``from`` is a Python keyword, so these two take the functional form.
ZoneArrivedEvent = TypedDict(
    "ZoneArrivedEvent",
    {
        "vin": str,
        "entry_id": str,
        "zone": str | None,
        "zone_entity_id": str | None,
        "trip_id": str,
        "trip_start": str,
        "distance_km": float | None,
        "duration_s": float | None,
        "energy_kwh": float | None,
        "soc": float | None,
        "from": str | None,
    },
)


class TripEndedEvent(ZoneArrivedEvent):
    """``trip_ended``: the arrival's fields, plus where and how the trip ended."""

    trip_end: str | None
    to: str | None
    soc_start: float | None
    classification: str | None
    odometer_start_km: float | None
    odometer_end_km: float | None


# --- situations and edges -------------------------------------------------------------


class SituationEvent(CarEvent):
    """``situation``: a situation began (``active``) or ended."""

    situation: str
    active: bool
    since: str
    zone: str | None
    zone_entity_id: str | None
    # plugged_not_charging only
    soc: NotRequired[float | None]
    target_soc: NotRequired[float | None]
    # parked_window_open only
    windows: NotRequired[list[str]]


class EdgeEvent(CarEvent):
    """``alarm``: something started (``active``) or stopped, and where the car is."""

    active: bool
    zone: str | None
    zone_entity_id: str | None


class PreconditioningEvent(EdgeEvent):
    activity: str | None
    error: str | None
    soc: float | None


class CheckControlEvent(EdgeEvent):
    """One message that appeared (``active``) or is gone."""

    id: Any
    title: Any
    text: Any
    message_type: Any
    message: dict[str, Any]


# --- the vehicle report --------------------------------------------------------------


class ReportValues(TypedDict):
    """What came with a report; ``None`` where the car does not send it."""

    fuel_l: float | None
    fuel_percent: float | None
    range_km: float | None
    soc_percent: float | None
    latitude: float | None
    longitude: float | None
    altitude_m: float | None
    heading: float | None


class VehicleReportEvent(ReportValues):
    """``vehicle_report``: a new odometer reading that held for 5 minutes."""

    vin: str
    entry_id: str
    timestamp: str | None
    odometer_km: float
    previous_odometer_km: float | None
    previous_timestamp: str | None
    distance_km: float | None


# Every event the integration fires, and the type of its data.
EVENT_PAYLOADS: dict[str, type[Mapping[str, Any]]] = {
    EVENT_CHARGING_STARTED: ChargingEvent,
    EVENT_CHARGING_PAUSED: ChargingEvent,
    EVENT_CHARGING_RESUMED: ChargingEvent,
    EVENT_CHARGING_STOPPED: ChargingEndEvent,
    EVENT_CHARGING_COMPLETE: ChargingEndEvent,
    EVENT_CHARGING_INTERRUPTED: ChargingInterruptedEvent,
    EVENT_ZONE_LEFT: ZoneLeftEvent,
    EVENT_ZONE_ARRIVED: ZoneArrivedEvent,
    EVENT_TRIP_ENDED: TripEndedEvent,
    EVENT_SITUATION: SituationEvent,
    EVENT_ALARM: EdgeEvent,
    EVENT_PRECONDITIONING: PreconditioningEvent,
    EVENT_CHECK_CONTROL: CheckControlEvent,
    EVENT_VEHICLE_REPORT: VehicleReportEvent,
}


def fields(event_type: str) -> tuple[frozenset[str], frozenset[str]]:
    """``(required, optional)`` keys of an event's data, ``vehicle`` included."""

    payload = EVENT_PAYLOADS[event_type]
    required: frozenset[str] = payload.__required_keys__  # type: ignore[attr-defined]
    optional: frozenset[str] = payload.__optional_keys__  # type: ignore[attr-defined]
    return required | ADDED_ON_FIRE, optional


def shape_error(event_type: str, data: Mapping[str, Any]) -> str | None:
    """What is wrong with a fired event's data, or ``None`` when it is as declared."""

    if event_type not in EVENT_PAYLOADS:
        return f"{event_type} is not a declared event"
    required, optional = fields(event_type)
    missing = sorted(required - set(data))
    unknown = sorted(set(data) - required - optional)
    if not missing and not unknown:
        return None
    return f"{event_type}: missing {missing}, undeclared {unknown}"
