"""Unit canonicalisation, shared by the runtime and the catalogue pipeline.

BMW does not spell a unit the same way twice. The catalogue export says
``percent``, ``Celsius``, ``degrees``, ``l``; the live stream, for the same
descriptors on the same cars, says ``kpa`` and ``degrees``. There used to be a
table for each side -- 24 entries in ``tools/generate_metadata.py``, one in
``units.py`` -- so the catalogue's ``kPa`` canonicalised and the stream's ``kpa``
did not.

The consequence is not cosmetic on the pipeline side: ``device_and_state_class``
matches the canonical string exactly, so a unit that misses this table matches
nothing and the descriptor loses its device class *and* state class -- unit
conversion and long-term statistics with them, silently.
"""

from __future__ import annotations

import pytest

from .conftest import load_module

units = load_module("units")
normalize_unit = units.normalize_unit
is_known = units.is_known


# --------------------------------------------------------------------------
# The spellings BMW actually sends
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, canonical, source",
    [
        ("percent", "%", "catalogue export, 29 descriptors"),
        ("%", "%", "catalogue export, 4 descriptors"),
        ("celsius", "°C", "catalogue export, 2 descriptors"),
        ("Celsius", "°C", "catalogue export, 5 descriptors"),
        ("degrees", "°", "catalogue export and the live stream"),
        ("l", "L", "catalogue export, 3 descriptors"),
        ("kPa", "kPa", "catalogue export, 8 descriptors"),
        ("kpa", "kPa", "the live stream -- 1,034 arrivals on an iX"),
        ("m", "m", "the live stream: GPS altitude, absent from the catalogue"),
    ],
)
def test_real_spellings_land_on_one_symbol(raw, canonical, source) -> None:
    assert normalize_unit(raw) == canonical, source
    assert is_known(raw), source


@pytest.mark.parametrize("raw", ["", "  ", "-", "null", "NULL", None])
def test_no_unit_tokens_become_none(raw) -> None:
    assert normalize_unit(raw) is None
    assert is_known(raw)


def test_case_folding_covers_variants_without_an_entry_each() -> None:
    for raw in ("KPA", "kPA", "DEGREES", "PerCent", "KM/H"):
        assert normalize_unit(raw) in units.CANONICAL_UNITS


def test_whitespace_is_not_a_difference() -> None:
    assert normalize_unit("  kPa ") == "kPa"


# --------------------------------------------------------------------------
# What it refuses to do
# --------------------------------------------------------------------------


def test_an_unknown_unit_passes_through_but_is_not_known() -> None:
    """Pass-through keeps the car's own label on the 37 unclassified sensors.

    It must never be mistaken for a result: ``is_known`` is what the generator
    and the catalogue tests gate on, so a gap in the table surfaces at build
    time instead of becoming an entity with a device class quietly missing.
    """

    assert normalize_unit("kilopascal") == "kilopascal"
    assert not is_known("kilopascal")
    assert not is_known("miles")


def test_case_folding_disables_itself_where_case_is_load_bearing() -> None:
    """mW and MW differ by a factor of a billion.

    Nothing in the table collides today; this pins the guard for the unit that
    gets added later, so folding can never silently equate two real units.
    """

    canonical = units.CANONICAL_UNITS | {"mW", "MW"}
    aliases = dict(units.UNIT_ALIASES)
    original_canonical, original_aliases = units.CANONICAL_UNITS, units.UNIT_ALIASES
    try:
        units.CANONICAL_UNITS, units.UNIT_ALIASES = canonical, aliases
        folded = units._fold_index()
    finally:
        units.CANONICAL_UNITS, units.UNIT_ALIASES = original_canonical, original_aliases

    assert "mw" not in folded, "mW/MW folded together -- they are not the same unit"
    assert folded["kpa"] == "kPa", "unambiguous folding still works alongside it"


def test_every_alias_targets_a_canonical_unit() -> None:
    for variant, canonical in units.UNIT_ALIASES.items():
        assert canonical in units.CANONICAL_UNITS, f"{variant!r} -> {canonical!r}"


def test_canonical_units_are_their_own_canonical_form() -> None:
    """No entry may rewrite itself, or normalisation would not be idempotent."""

    for unit in units.CANONICAL_UNITS:
        assert normalize_unit(unit) == unit
        assert normalize_unit(normalize_unit(unit)) == unit
