"""Stable Home Assistant translation keys derived from BMW descriptors.

Kept dependency-free so both the code generators (``tools/``) and the runtime
entities share one derivation and can never drift apart.
"""

from __future__ import annotations

import re


def translation_key(descriptor: str) -> str:
    """Return a slug usable as a Home Assistant ``translation_key``.

    Home Assistant keys must match ``[a-z0-9_]+``; the dotted camelCase
    descriptor is lower-cased and every run of other characters collapses to a
    single underscore. The mapping is injective across the catalogue (verified
    in tests), so keys are unique per descriptor.
    """

    return re.sub(r"[^a-z0-9]+", "_", descriptor.lower()).strip("_")
