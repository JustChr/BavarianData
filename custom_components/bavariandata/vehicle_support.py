"""Vehicles CarData lists but BavarianData cannot serve.

BMW's own CarData guide says streaming is not available for BMW Motorrad. The
account still maps the bike and the REST API still answers, so setup completes
and the device then sits empty with nothing to say why. The coordinator raises a
repair when basic data names the motorcycle brand.

Home Assistant-free so the matching and the issue identity are unit-tested.
"""

from __future__ import annotations

import hashlib
from typing import Any

MOTORCYCLE_ISSUE_PREFIX = "motorcycle_unsupported"


def is_motorcycle(brand: Any) -> bool:
    """Whether basic data's ``brand`` names BMW's motorcycle division.

    BMW's spelling of it in basic data has never been seen here (``BMW
    Motorrad``, ``BMW_MOTORRAD`` and ``MOTORRAD`` are all plausible), so this
    matches the word rather than one guessed string.
    """

    return isinstance(brand, str) and "motorrad" in brand.casefold()


def motorcycle_issue_id(entry_id: str, vin: str) -> str:
    """A stable per-vehicle repair id that does not contain the VIN.

    Same reasoning as ``coverage.coverage_issue_id``: Home Assistant attaches
    repair ids to a diagnostics download outside our redaction.
    """

    digest = hashlib.sha256(vin.encode("utf-8")).hexdigest()[:12]
    return f"{MOTORCYCLE_ISSUE_PREFIX}_{entry_id}_{digest}"
