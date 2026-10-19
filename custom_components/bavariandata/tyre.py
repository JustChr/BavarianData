"""Parse BMW's smart-maintenance tyre diagnosis into a flat per-wheel shape.

``GET /customers/vehicles/{vin}/smartMaintenanceTyreDiagnosis`` returns a deeply
nested document (``SmartMaintenanceTyreDiagnosisDto``) in which every leaf is an
object carrying a localized ``label`` and a display ``value`` alongside the datum
itself. This module flattens the mounted set into one dict per wheel so the
entities and the Lovelace card do not have to know that shape.

Kept free of Home Assistant imports so it is unit-testable without an HA harness
(see ``tests/test_tyre.py``).

Every field is optional: BMW omits whole branches for vehicles that have never
had a tyre service recorded, and returns ``errors`` instead of ``passengerCar``
when the upstream system is unavailable. Nothing here raises on a shape it does
not recognise -- it returns what it could read.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# BMW's camelCase wheel keys -> our snake_case position slugs. The slugs match
# the ``tire_axle``/``tire_side`` attribute values the streamed pressure sensors
# already expose, so the card can place both on the same diagram.
WHEEL_POSITIONS: tuple[tuple[str, str], ...] = (
    ("frontLeft", "front_left"),
    ("frontRight", "front_right"),
    ("rearLeft", "rear_left"),
    ("rearRight", "rear_right"),
)

_MILES_TO_KM = 1.609344


def _obj(value: Any) -> Dict[str, Any]:
    """Return ``value`` when it is a dict, else an empty one."""

    return value if isinstance(value, dict) else {}


def _text(node: Any, *keys: str) -> Optional[str]:
    """First non-empty string among ``keys`` of ``node``.

    BMW puts the datum under a field named after the concept (``season``,
    ``qualityStatus``, ``status``) and a human-readable rendering under
    ``value``; which of the two is populated varies by field, so callers pass
    their preference order.
    """

    node = _obj(node)
    for key in keys:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _status_color(node: Any) -> Optional[str]:
    """The RED/YELLOW/GREEN/GREY traffic light, lower-cased.

    Lower-cased because it becomes an entity state: Home Assistant states are
    conventionally lowercase slugs, and the card translates them for display.
    """

    color = _text(node, "statusColor")
    return color.lower() if color else None


def _due_mileage_km(wear: Any) -> Optional[float]:
    """``tyreWear.dueMileage`` normalized to kilometres.

    BMW reports the unit per field (``KILOMETER``/``MILE``) rather than per
    account, so it has to be read and converted here -- a mile figure silently
    treated as km would understate the remaining life by 60%.
    """

    wear = _obj(wear)
    raw = wear.get("dueMileage")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    unit = _text(wear, "unit") or "KILOMETER"
    km = float(raw) * (_MILES_TO_KM if unit.upper().startswith("MILE") else 1.0)
    return round(km, 1)


def _bool(node: Any, key: str) -> Optional[bool]:
    value = _obj(node).get(key)
    return value if isinstance(value, bool) else None


def parse_wheel(data: Any) -> Dict[str, Any]:
    """Flatten one ``TyreDataDto`` into plain scalars."""

    data = _obj(data)
    wear = data.get("tyreWear")
    defect = data.get("tyreDefect")
    return {
        "wear_status": _text(wear, "status", "value"),
        "wear_status_color": _status_color(wear),
        "wear_value": _text(wear, "value"),
        "due_mileage_km": _due_mileage_km(wear),
        "defect_status": _text(defect, "status", "value"),
        "defect_status_color": _status_color(defect),
        "quality_status": _text(data.get("qualityStatus"), "qualityStatus", "value"),
        "season": _text(data.get("season"), "season", "value"),
        "tread": _text(data.get("tread"), "treadDesign", "value"),
        "tread_manufacturer": _text(data.get("tread"), "manufacturer"),
        "dimension": _text(data.get("dimension"), "value"),
        "mounting_date": _text(data.get("mountingDate"), "mountingDate", "value"),
        "production_date": _text(data.get("tyreProductionDate"), "value"),
        "production_status_color": _status_color(data.get("tyreProductionDate")),
        "part_number": _text(data.get("partNumber"), "partNumber", "value"),
        "run_flat": _bool(data.get("runFlat"), "runFlat"),
    }


def wheel_has_data(wheel: Dict[str, Any]) -> bool:
    """Whether a parsed wheel says anything a user could act on.

    Not merely "some field is set": cars that have no tyre service record on
    file (seen on a 2018 F20, #174) send every wheel with ``dueMileage: 0`` and
    ``runFlat: false`` and nothing else. Those two are BMW's defaults, not a
    reading -- a wheel built from them alone can only ever show "unknown".
    """

    for key, value in wheel.items():
        if key == "due_mileage_km":
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                return True
        elif key == "run_flat":
            if value is True:
                return True
        elif value is not None:
            return True
    return False


def has_tyre_data(diagnosis: Any) -> bool:
    """Whether a diagnosis gives the tyre sensors anything to show.

    False is the "BMW keeps no tyre data for this car" answer, so the sensors
    made for it are removed rather than left reading "unknown". An ``errors``
    branch counts as data: an upstream outage empties the wheels too, and it
    must not cost the sensors their history.
    """

    diagnosis = _obj(diagnosis)
    return bool(
        diagnosis.get("wheels") or diagnosis.get("aggregated_status") or diagnosis.get("errors")
    )


def _errors(payload: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for item in payload.get("errors") or []:
        if not isinstance(item, dict):
            continue
        message = item.get("message")
        if isinstance(message, dict):
            message = _text(message, "value", "text", "detail")
        text = message if isinstance(message, str) else item.get("type")
        if isinstance(text, str) and text.strip():
            out.append(text.strip())
    return out


def parse_tyre_diagnosis(payload: Any) -> Dict[str, Any]:
    """Flatten a tyre-diagnosis response.

    Returns ``{"aggregated_status", "aggregated_label", "wheels", "errors"}``.
    ``wheels`` maps a position slug to :func:`parse_wheel`'s output and only
    contains wheels BMW actually reported, so an empty dict means "no tyre
    service data for this vehicle" rather than "all four are fine".
    """

    payload = _obj(payload)
    mounted = _obj(_obj(payload.get("passengerCar")).get("mountedTyres"))
    aggregated = mounted.get("aggregatedQualityStatus")

    wheels: Dict[str, Dict[str, Any]] = {}
    for bmw_key, slug in WHEEL_POSITIONS:
        raw = mounted.get(bmw_key)
        if not isinstance(raw, dict) or not raw:
            continue
        parsed = parse_wheel(raw)
        # A wheel that tells us nothing would create an entity that can only
        # ever show "unknown".
        if wheel_has_data(parsed):
            wheels[slug] = parsed

    return {
        "aggregated_status": _text(aggregated, "qualityStatus", "value"),
        "aggregated_label": _text(aggregated, "label"),
        "wheels": wheels,
        "errors": _errors(payload),
    }


def restore_diagnosis(record: Any) -> Optional[Dict[str, Any]]:
    """Rebuild one persisted record (see ``tyre_store``) into a diagnosis.

    ``record`` is ``{"fetched_at": iso, "diagnosis": {...}}`` as written to
    ``.storage``. Returns ``None`` when there is nothing usable in it, so a
    half-written or hand-edited store degrades to "not fetched yet" rather than
    handing the entities a shape they will index into and crash on -- hence the
    per-container type checks rather than trusting what we wrote last time.
    """

    diagnosis = _obj(_obj(record).get("diagnosis"))
    if not diagnosis:
        return None

    wheels = diagnosis.get("wheels")
    # Filtered again rather than trusted: a store written before #174 holds the
    # default-only wheels this module now drops on parse.
    diagnosis["wheels"] = {
        str(position): wheel
        for position, wheel in (wheels.items() if isinstance(wheels, dict) else ())
        if isinstance(wheel, dict) and wheel_has_data(wheel)
    }
    errors = diagnosis.get("errors")
    diagnosis["errors"] = (
        [item for item in errors if isinstance(item, str)] if isinstance(errors, list) else []
    )

    fetched_at = _obj(record).get("fetched_at")
    if isinstance(fetched_at, str) and fetched_at.strip():
        diagnosis["fetched_at"] = fetched_at
    else:
        diagnosis.pop("fetched_at", None)
    return diagnosis
