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

from typing import Any, Dict, FrozenSet, Iterable, Mapping, Optional, Tuple

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
    "liter": "L",
    "liters": "L",
    "litre": "L",
    "litres": "L",
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


def distance_unit(
    sensor_units: Iterable[Any], car_setting: Any, system_unit: Any, choice: Any = None
) -> str:
    """``"mi"`` or ``"km"``: the unit a car's distances are entered and shown in.

    0. The account's Units setting, when it names one (``choice``); "auto" or
       nothing goes on down this list.
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

    if choice in ("km", "mi"):
        return choice
    for unit in sensor_units:
        found = _mi_or_km(unit)
        if found:
            return found
    return _mi_or_km(car_setting) or ("mi" if _mi_or_km(system_unit) == "mi" else "km")


# The account's Units setting (Configure -> Units): one choice per quantity,
# "auto" unless the user named a unit. The first value of each is the default.
UNIT_CHOICES: Dict[str, Tuple[str, ...]] = {
    "distance": ("auto", "km", "mi"),
    "fuel": ("auto", "l", "l_mpg_uk", "gal_us"),
    "temperature": ("auto", "c", "f"),
    "pressure": ("auto", "bar", "kpa", "psi"),
}


def unit_choices(stored: Mapping[str, Any]) -> Dict[str, str]:
    """``{"distance": ..., "fuel": ..., ...}`` from options keyed ``unit_<quantity>``.

    Anything missing or unknown reads as "auto", so an older entry, or a value
    a later version removed, falls back to the automatic rules.
    """

    choices = {}
    for quantity, allowed in UNIT_CHOICES.items():
        value = stored.get(f"unit_{quantity}")
        choices[quantity] = value if value in allowed else allowed[0]
    return choices


def fuel_unit(choice: Any, distance: str, system_unit: Any) -> str:
    """``"l"``, ``"l_mpg_uk"`` or ``"gal_us"``: how fuel and its consumption are shown.

    An explicit choice wins. Automatic follows the distance: US customary means
    US gallons and US mpg; a car in miles on a metric install (the UK) buys
    litres but rates them in imperial mpg, as its own display does; everything
    else is litres and l/100 km.
    """

    if choice in UNIT_CHOICES["fuel"][1:]:
        return choice
    if _mi_or_km(system_unit) == "mi":
        return "gal_us"
    return "l_mpg_uk" if distance == "mi" else "l"


# The display unit an explicit choice gives a sensor of each device class. A
# sensor keeps storing its native unit; Home Assistant converts what it shows
# and keeps its statistics in one unit, so switching never splits a history.
# Home Assistant has no imperial gallon: UK fuel is bought in litres anyway.
_SENSOR_UNITS: Dict[str, Tuple[str, Dict[str, str]]] = {
    "distance": ("distance", {"km": "km", "mi": "mi"}),
    "speed": ("distance", {"km": "km/h", "mi": "mph"}),
    "temperature": ("temperature", {"c": "°C", "f": "°F"}),
    "pressure": ("pressure", {"bar": "bar", "kpa": "kPa", "psi": "psi"}),
    "volume_storage": ("fuel", {"l": "L", "l_mpg_uk": "L", "gal_us": "gal"}),
}


def sensor_display_unit(device_class: Any, choices: Mapping[str, str]) -> Optional[str]:
    """The unit a sensor should be shown in, or ``None`` to leave it to Home Assistant."""

    found = _SENSOR_UNITS.get(device_class) if isinstance(device_class, str) else None
    if found is None:
        return None
    quantity, units = found
    return units.get(choices.get(quantity, "auto"))


def sensor_unit_update(
    device_class: Any, choices: Mapping[str, str], current: Optional[str], applied: Optional[str]
) -> Optional[Tuple[Optional[str], Optional[str]]]:
    """What to write for one sensor: ``(display unit, our mark)``, or ``None`` for nothing.

    ``current`` is the display unit set on the entity (``None``: Home Assistant's
    default), ``applied`` the one this integration set last. An explicit choice
    sets its unit. Automatic takes back only what we set, and leaves a unit the
    user has picked on the entity since then alone.
    """

    wanted = sensor_display_unit(device_class, choices)
    if wanted is not None:
        if current == wanted and applied == wanted:
            return None
        return wanted, wanted
    if applied is None:
        return None
    return (None if current == applied else current), None


# The tank's unit when the car gives none. Every car seen sends ``l`` or ``L``
# for ``fuelSystem.remainingFuel``; the catalogue allows gallons, but a car in
# gallons has a unit to say so. Without one, Home Assistant refuses a volume
# with no unit (no statistics, a warning on every update) and the card cannot
# tell litres from anything else.
DEFAULT_FUEL_UNIT = "L"


def entity_unit(device_class: Optional[str], unit: Optional[str]) -> Optional[str]:
    """The unit an entity should carry for a reading whose unit came off the stream."""

    if unit is None and device_class == "volume_storage":
        return DEFAULT_FUEL_UNIT
    return unit
