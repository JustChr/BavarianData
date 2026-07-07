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
