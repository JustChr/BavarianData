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

from typing import Dict, FrozenSet, Optional

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
    # Stream payload. Observed live on a G42 and an iX (1,034 arrivals of
    # ``kpa`` while the catalogue for the same descriptors says ``kPa``).
    "kpa": "kPa",
}

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
