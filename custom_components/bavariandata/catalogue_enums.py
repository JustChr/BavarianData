"""Enum-option extraction from the catalogue's value-range strings.

Single source of truth shared by the code generators (``tools/``) and the unit
tests, so metadata ``options`` and the translation state labels are derived the
same way and can never drift apart. Kept dependency-free (no Home Assistant, no
package imports) so it loads standalone.

BMW documents a descriptor's allowed values in a free-form ``value_range``
column. The historic rule only recognised ALL-CAPS tokens (``OPEN, CLOSED,
INVALID``) because BMW's declared ``boolean`` data type is unreliable — some
"boolean" fields actually carry ALL-CAPS string enums, and real booleans are
documented as lowercase ``true, false`` which the ALL-CAPS rule excludes.

That rule silently dropped enums BMW documents in mixed/camelCase (e.g. the
anti-theft ``armStatus``: ``unarmed, doorsOnly, doorsTiltCabin``), leaving them
as untranslated free-text sensors. Those are always declared ``string``, so we
additionally accept a fully "clean" comma list when the declared type is
``string`` — which never matches ``true, false`` (declared boolean) or numeric
ranges (declared numeric), so real booleans stay binary sensors.
"""

from __future__ import annotations

import re

# A single enum value: a letter-led slug of letters/digits/underscore/hyphen.
_ENUM_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_\-]*")
# The historic ALL-CAPS token, matched regardless of declared data type.
_CAPS_TOKEN = re.compile(r"[A-Z][A-Z0-9_\-]+")

# BMW's ``value_range`` column is wrong for a few descriptors (a copy-paste from
# a neighbouring field). Where the descriptor's own *description* documents the
# real enum, pin the true values here so metadata ``options`` and the translation
# state labels match what the stream actually emits, instead of a bogus list.
# Keyed by descriptor; values are the raw (upper-case) enum tokens.
_DESCRIPTOR_ENUM_OVERRIDES: dict[str, tuple[str, ...]] = {
    # value_range claims MANUAL_SELECTION/AUTOMATIC_SELECTION/... (a charging-mode
    # list); the field's description spells out the real charging states, and the
    # stream emits exactly these (e.g. NOCHARGING, CHARGINGACTIVE, INITIALIZATION).
    "vehicle.drivetrain.electricEngine.charging.status": (
        "NOCHARGING",
        "INITIALIZATION",
        "CHARGINGACTIVE",
        "CHARGINGPAUSED",
        "CHARGINGENDED",
        "CHARGINGERROR",
    ),
}


def enum_options(
    descriptor: str, value_range: str, data_type: str = ""
) -> tuple[str, ...]:
    """Enum values for a descriptor: a pinned override if any, else parsed.

    The override wins over the (occasionally wrong) catalogue ``value_range`` so
    the generators and tests all agree on the true set. See
    ``_DESCRIPTOR_ENUM_OVERRIDES``.
    """

    override = _DESCRIPTOR_ENUM_OVERRIDES.get(descriptor)
    if override is not None:
        return override
    return enum_tokens(value_range, data_type)


def enum_tokens(value_range: str, data_type: str = "") -> tuple[str, ...]:
    """Return the enum values (original case, de-duplicated) or ``()``.

    ``()`` means "not an enum" — a real boolean, a numeric range, or free text.
    """

    tokens = [t.strip() for t in (value_range or "").split(",") if t.strip()]

    # Declared string enums: trust the whole list, but only when every token is
    # a clean enum slug. A stray token (``-NA-``, ``1-PHASES``, a numeric range)
    # means it is not a pure enum list, so fall through to the ALL-CAPS rule.
    if data_type == "string":
        words = [t for t in tokens if _ENUM_TOKEN.fullmatch(t)]
        if len(words) >= 2 and len(words) == len(tokens):
            return tuple(dict.fromkeys(words))

    caps = [t for t in tokens if _CAPS_TOKEN.fullmatch(t)]
    # Require at least two enum-looking tokens to avoid misreading free text.
    if len(caps) >= 2:
        return tuple(dict.fromkeys(caps))
    return ()
