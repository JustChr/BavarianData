"""Deciding when a restored Home Assistant state may be reused as a native value.

Home Assistant persists what it *displayed*, not what the integration reported.
For a sensor with a device class, the state written to the machine is the native
value converted into the display unit -- the one the unit system picked, or the
one the user chose in the entity settings. ``RestoreEntity.async_get_last_state``
hands that converted number back on the next start.

Assigning it straight to ``_attr_native_value`` therefore re-applies the
conversion once per restart, and the reading walks away from reality by one
conversion factor every time. A tyre pressure held natively in kPa but shown in
bar is divided by 100 per restart -- 250 kPa read as 2.5, then 0.025, and after
five restarts the 2.5e-10 bar of issue #7. Minutes shown as hours divide by 60,
metres shown as feet by 3.28. Nothing self-corrects, because the unit is pinned
from the catalogue and so never disagrees with itself; only a fresh message from
the stream resets the value, which is why slow descriptors like tyre pressure are
where it becomes visible.

``RestoreSensor`` is the cure: it stores the native value *and* the native unit,
so there is nothing left to infer. This module is the check applied to whatever
comes back -- the saved native unit where there is one, and the display unit of a
bare state where there is not, which is all that exists on the first start after
upgrading from a build that saved no native data. The rule is deliberately
blunt: reuse the value only when its unit says it cannot have been converted, and
otherwise restore nothing and wait for the stream. A gap of one message is
recoverable; a plausible-looking wrong number that then keeps decaying is not.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from .units import normalize_unit

# States that carry no value to restore. Deliberately not ``"none"``: BMW's
# enums use it as a real value (no preconditioning running, no charging reason).
UNUSABLE_STATES = frozenset({"unknown", "unavailable", ""})


def _canonical(unit: Optional[str]) -> Optional[str]:
    """Canonical form of a unit string, or ``None`` when there is no unit."""

    canonical = normalize_unit(unit)
    if not isinstance(canonical, str):
        return None
    canonical = canonical.strip()
    return canonical or None


def units_agree(stored_unit: Optional[str], native_unit: Optional[str]) -> bool:
    """Whether a state stored in ``stored_unit`` is already a native reading.

    Compared exactly (after canonicalisation) rather than case-insensitively:
    case is load-bearing in unit symbols -- mW and MW differ by a factor of a
    billion -- so a near-match is treated as a mismatch and the value is left
    for the stream to supply.
    """

    stored = _canonical(stored_unit)
    native = _canonical(native_unit)
    # No native unit pinned: nothing was converted on the way out, because
    # Home Assistant only converts what it has a unit and a device class for.
    if native is None:
        return True
    # No unit on the stored state: a converted sensor always carries one, so
    # this is a unitless or not-yet-classified reading.
    if stored is None:
        return True
    return stored == native


def restore_native(
    state: Any,
    stored_unit: Optional[str],
    native_unit: Optional[str],
) -> Tuple[Any, Optional[str]]:
    """Return the ``(value, unit)`` to restore, given the unit it was saved in.

    ``(None, None)`` means the stored state cannot be trusted as a native
    reading and must be discarded -- the entity stays unknown until the stream
    delivers a real one.
    """

    if state is None:
        return None, None
    if isinstance(state, str) and state.strip().lower() in UNUSABLE_STATES:
        return None, None
    if not units_agree(stored_unit, native_unit):
        return None, None
    # The native unit wins when there is one: it is what the value is now
    # declared in, and what the coordinator must record alongside it.
    return state, _canonical(native_unit) or _canonical(stored_unit)
