"""Canonical measurement units, shared by the runtime and the catalogue pipeline.

BMW spells the same unit differently depending on where you read it. The
catalogue export writes ``percent``, ``Celsius``, ``degrees`` and ``l``; the live
MQTT payload for the same car writes ``kpa`` and ``degrees``. Both have to land
on one Home Assistant symbol, or one measurement ends up with two identities.

This table is the single source for both sides, loaded by
``tools/generate_metadata.py`` the way ``keys.py`` and ``catalogue_enums.py``
already are. It is one table on purpose: there used to be two, the pipeline's
with 24 entries and the runtime's with one, so ``kpa`` off the stream stayed
``kpa`` all the way to the entity while the catalogue's ``kPa`` did not.

Why this matters past cosmetics: the pipeline decides a descriptor's device class
and state class by matching the canonical string *exactly*
(``generate_metadata.device_and_state_class``). A unit it does not recognise is
passed through unchanged and matches nothing, so a catalogue export that spelled
kPa as ``kpa`` would quietly turn eight tyre-pressure sensors into unclassified
ones -- no pressure device class, no unit conversion and, because the state class
goes with it, no long-term statistics. Same shape as issue #6, where a substring
match decided what an entity was. So an unknown unit is *reported* rather than
guessed at: ``is_known`` is what the generator and the tests check, and adding a
unit here is the deliberate act of deciding what it means.
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, Iterable, Optional, Tuple

# Every unit symbol the project emits. Anything reaching an entity or the
# metadata registry is one of these -- or an unrecognised string that survived
# pass-through, which the tests are there to prevent.
CANONICAL_UNITS: FrozenSet[str] = frozenset(
    {
        "%",
        "°",
        "°C",
        "km",
        "km/h",
        "kW",
        "kWh",
        "W",
        "V",
        "A",
        "kPa",
        "L",
        "gal",
        "m",
        "min",
        "s",
        "h",
        "weeks",
        "months",
        "stars",
    }
)

# Spellings BMW actually uses, mapped onto the symbol above. Case variants do
# not need an entry -- see the fold index below.
UNIT_ALIASES: Dict[str, str] = {
    # Catalogue export.
    "percent": "%",
    "celsius": "°C",
    "degrees": "°",
    "l": "L",
    # The catalogue documents the tank in "litres or gallons". Only the litre
    # spelling has been seen (an F87 M2 streams ``l``); these cover the other
    # half without guessing past Home Assistant's own symbols.
    "gallon": "gal",
    "gallons": "gal",
    # Stream payload. Observed live on a G42 and an iX (1,034 arrivals of
    # ``kpa`` while the catalogue for the same descriptors says ``kPa``).
    "kpa": "kPa",
}

# A distance BMW reports in miles. Seen on a UK iX1 (issue #62): one range
# descriptor arrived as ``miles`` while its neighbours stayed ``km``, so the car
# decides per descriptor. Everything downstream -- the catalogue's unit, the
# trips, the ledger, the card -- speaks kilometres.
MILE_UNITS: FrozenSet[str] = frozenset({"mi", "mile", "miles"})
KM_PER_MILE = 1.609344

# Unit fields that mean "this reading has no unit". BMW uses all three.
NO_UNIT_TOKENS: FrozenSet[str] = frozenset({"", "-", "null"})


def _fold_index() -> Dict[str, str]:
    """Case-insensitive lookup, built only where case cannot be load-bearing.

    Folding is what lets ``kpa``/``KPA``/``Celsius`` resolve without an entry
    each. It is built per-unit rather than applied blanket, because case *is*
    meaningful in unit symbols -- mW and MW differ by a factor of a billion. If a
    variant and a canonical unit ever fold to the same string with different
    meanings, that key is dropped and both must be spelled out exactly. Nothing
    in the current table collides; the guard is for the one that gets added
    later.
    """

    index: Dict[str, str] = {}
    ambiguous = set()
    for variant, canonical in (
        *((unit, unit) for unit in CANONICAL_UNITS),
        *UNIT_ALIASES.items(),
    ):
        key = variant.casefold()
        if key in index and index[key] != canonical:
            ambiguous.add(key)
        index[key] = canonical
    for key in ambiguous:
        del index[key]
    return index


_FOLDED = _fold_index()


def normalize_unit(unit: Optional[str]) -> Optional[str]:
    """Return the canonical symbol for a raw BMW unit string.

    ``None`` for anything that means "no unit". An *unrecognised* unit is passed
    through unchanged rather than dropped: it is still the best label the car
    gave us, and blanking it would strip the unit off the 37 descriptors whose
    entity unit comes from the payload. Use :func:`is_known` to tell the two
    apart -- pass-through is a gap in this table, not a result.
    """

    if not isinstance(unit, str):
        return unit

    stripped = unit.strip()
    if not stripped or stripped.casefold() in NO_UNIT_TOKENS:
        return None
    if stripped in CANONICAL_UNITS:
        return stripped
    mapped = UNIT_ALIASES.get(stripped)
    if mapped is not None:
        return mapped
    folded = _FOLDED.get(stripped.casefold())
    if folded is not None:
        return folded
    return stripped


def is_known(unit: Optional[str]) -> bool:
    """Whether this table recognises ``unit`` (including as "no unit")."""

    canonical = normalize_unit(unit)
    return canonical is None or canonical in CANONICAL_UNITS


def miles_to_km(value: Any, unit: Optional[str]) -> Tuple[Any, Optional[str]]:
    """Return ``(value, unit)`` in kilometres when the car sent miles.

    Anything else -- another unit, or a miles label on a value that is not a
    plain number -- comes back untouched, so the caller can apply it blindly.
    """

    if (
        isinstance(unit, str)
        and unit.strip().casefold() in MILE_UNITS
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        return round(value * KM_PER_MILE, 3), "km"
    return value, unit


# The car's own display setting for distances, newest descriptor first. BMW
# spells the values ``km``/``miles`` in one and ``kilometers``/``miles`` in the
# other.
DESC_DISPLAY_DISTANCE = (
    "vehicle.cabin.infotainment.displayUnit.distance",
    "vehicle.cabin.infotainment.hmi.distanceUnit",
)
_KM_UNITS: FrozenSet[str] = frozenset({"km", "kilometer", "kilometers", "kilometre", "kilometres"})


def _mi_or_km(unit: Any) -> Optional[str]:
    if not isinstance(unit, str):
        return None
    folded = unit.strip().casefold()
    if folded in MILE_UNITS:
        return "mi"
    if folded in _KM_UNITS:
        return "km"
    return None


def distance_unit(sensor_units: Iterable[Any], car_setting: Any, system_unit: Any) -> str:
    """``"mi"`` or ``"km"``: the unit a car's distances are entered and shown in.

    1. The unit the car's distance sensors are displayed in, odometer first. A
       UK install keeps Home Assistant's metric unit system (Celsius, litres)
       and switches the distance sensors to miles, so this is the user's own
       choice for this car.
    2. The car's own display setting (:data:`DESC_DISPLAY_DISTANCE`), when the
       car streams it.
    3. Home Assistant's unit system: miles only for US customary.

    Settings screens, the service calendar and the card all follow this order
    (the card's ``_lengthUnit`` mirrors it in JavaScript), so a distance is
    never typed in one unit and stored as another (issue #126).
    """

    for unit in sensor_units:
        found = _mi_or_km(unit)
        if found:
            return found
    return _mi_or_km(car_setting) or ("mi" if _mi_or_km(system_unit) == "mi" else "km")
