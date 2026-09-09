"""Restoring a sensor value across a restart without re-converting it.

Issue #7: an M240i's tyre pressures read 2.5e-10 bar. Nothing scaled them --
Home Assistant saves the value it *displayed*, the integration read that back as
if it were the native reading, and the kPa->bar conversion was applied again on
every restart. Five restarts is 100^5. The same fault ran on the maintainer's own
instance against a minutes sensor shown in hours, where the recorder caught the
whole descent: 0.0364 -> 0.000606 -> 1.01e-05 -> 1.68e-07 -> 2.81e-09 -> 4.68e-11,
each step exactly /60.

``restore_units`` decides whether a saved state may be treated as native at all.
It guards both what ``RestoreSensor`` saves and, on the first restart after
upgrading, the bare display state that is all an older build left behind --
which is exactly where a wrong answer becomes permanent. So the rule is
conservative: reuse the value only when its unit says it cannot have been
converted, and otherwise restore nothing and let the stream refill it.
"""

from __future__ import annotations

import pytest

from .conftest import load_module

restore_units = load_module("restore_units")


# --------------------------------------------------------------------------
# When a saved state is still a native reading
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stored, native, recorded",
    [
        ("kPa", "kPa", "kPa"),  # no display override: the common case
        ("%", "%", "%"),  # percent has no converter at all
        (" kPa ", "kPa", "kPa"),  # whitespace is not a difference
        ("percent", "%", "%"),  # normalize_unit's one alias
        (None, "kPa", "kPa"),  # unitless state: nothing was converted
        ("kPa", None, "kPa"),  # nothing pinned: unit comes from the payload
        (None, None, None),  # enum / string sensors
    ],
)
def test_units_agree_means_the_state_is_native(stored, native, recorded) -> None:
    assert restore_units.units_agree(stored, native) is True
    assert restore_units.restore_native("250", stored, native) == ("250", recorded)


# --------------------------------------------------------------------------
# When it is not -- the two faults seen in the wild, and their neighbours
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stored, native, why",
    [
        ("bar", "kPa", "issue #7: tyre pressure shown in bar, /100 per restart"),
        ("h", "min", "the maintainer's own charge-time sensor, /60 per restart"),
        ("psi", "kPa", "what the US customary unit system picks, unprompted"),
        ("mi", "km", "US customary distance -- would rot the odometer"),
        ("ft", "m", "US customary altitude"),
        ("°F", "°C", "offset conversion: diverges upward instead of decaying"),
        ("Wh", "kWh", "a user-chosen energy unit"),
        ("MW", "mW", "case is load-bearing; a near-match is a mismatch"),
    ],
)
def test_converted_states_are_never_reused(stored, native, why) -> None:
    assert restore_units.units_agree(stored, native) is False, why
    assert restore_units.restore_native("2.5", stored, native) == (None, None), why


def test_states_with_nothing_in_them_are_skipped() -> None:
    for state in ("unknown", "unavailable", "", "  ", "UNKNOWN", None):
        assert restore_units.restore_native(state, "kPa", "kPa") == (None, None)


def test_none_is_a_value_not_an_absence() -> None:
    """BMW enums use NONE as a real state (no preconditioning, no charge reason)."""

    assert restore_units.restore_native("none", None, None) == ("none", None)
    assert restore_units.restore_native("NONE", None, None) == ("NONE", None)


def test_the_native_unit_is_what_gets_recorded() -> None:
    """The coordinator stores this unit; it must be the native one, not the display one.

    Diagnostics for issue #7 reported the tyre pressures as ``bar`` with zero
    arrivals -- the display unit, written back into the coordinator by the old
    restore path. That is why the report looked like BMW had changed units.
    """

    assert restore_units.restore_native("250", "kPa", "kPa") == ("250", "kPa")
    assert restore_units.restore_native("250", "percent", "%") == ("250", "%")
    # A payload spelling is canonicalised on the way in (units.py), so an
    # unclassified sensor records "kPa" rather than the stream's "kpa".
    assert restore_units.restore_native("250", "kpa", None) == ("250", "kPa")


def test_a_spelling_difference_is_not_a_unit_difference() -> None:
    """``kpa`` off the stream and ``kPa`` from the catalogue are one unit.

    Before the tables were merged these compared unequal, which would have made
    the restore rule discard a value that needed no conversion at all.
    """

    assert restore_units.units_agree("kpa", "kPa") is True
    assert restore_units.restore_native("250", "kpa", "kPa") == ("250", "kPa")
    assert restore_units.restore_native("20", "celsius", "°C") == ("20", "°C")


# --------------------------------------------------------------------------
# The regression itself
# --------------------------------------------------------------------------


def test_the_old_path_decayed_and_the_new_one_cannot() -> None:
    """Five restarts of a 250 kPa tyre pressure displayed in bar.

    The whole bug in one loop: each restart took the displayed number as the
    native one, and Home Assistant divided by 100 again on the way back out.
    """

    displayed = 250.0 / 100  # 2.5 bar, correct
    for _ in range(5):
        displayed = displayed / 100
    assert displayed == pytest.approx(2.5e-10)  # the figure issue #7 reported

    # The same restarts under the new rule: the state is never reused, so there
    # is nothing to divide. The entity stays unknown until the stream refills it.
    for _ in range(5):
        assert restore_units.restore_native("0.025", "bar", "kPa") == (None, None)


def test_an_agreeing_unit_survives_any_number_of_restarts() -> None:
    """The other half: installs with no override must keep their value."""

    state = "250"
    for _ in range(10):
        state, unit = restore_units.restore_native(state, "kPa", "kPa")
        assert state == "250"
        assert unit == "kPa"
