"""What the diagnostics download may say about the user's own setup.

Home Assistant-free, so the rules are unit-tested without an HA install;
``diagnostics.py`` builds the payload and applies them.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from .const import (
    OPTION_BATTERY_POWER_ENTITY,
    OPTION_GRID_ENERGY_ENTITY,
    OPTION_GRID_POWER_ENTITY,
    OPTION_PRICE_ENTITY,
    OPTION_PV_POWER_ENTITY,
    OPTION_TRIP_PHONE_IN_CAR,
    OPTION_TRIP_PHONE_IN_CAR_2,
    OPTION_TRIP_PHONE_TRACKER,
    OPTION_TRIP_PHONE_TRACKER_2,
    OPTION_TRIP_WORK_ZONE,
    OPTION_VEHICLES,
)

# Options whose value is an entity id the user picked. Triage needs to know
# whether one is configured, never which one: an entity id is free text chosen by
# whichever integration created it, and it routinely embeds identifiers of its
# own. A real report carried
# ``sensor.octopus_energy_electricity_<meter serial>_<MPAN>_current_accumulative_cost``
# -- an electricity meter serial and the supply point of a home, pasted into a
# public issue by a file we asked for. A phone tracker usually carries its
# owner's name and a work zone its employer's. Reduced to the domain, which is
# all the triage actually used. Every entity picker in the options flow belongs
# here; ``tests/test_diagnostics_privacy.py`` fails on one that is missing.
ENTITY_ID_OPTIONS = frozenset(
    {
        OPTION_PRICE_ENTITY,
        OPTION_GRID_ENERGY_ENTITY,
        OPTION_GRID_POWER_ENTITY,
        OPTION_PV_POWER_ENTITY,
        OPTION_BATTERY_POWER_ENTITY,
        OPTION_TRIP_WORK_ZONE,
        OPTION_TRIP_PHONE_TRACKER,
        OPTION_TRIP_PHONE_IN_CAR,
        OPTION_TRIP_PHONE_TRACKER_2,
        OPTION_TRIP_PHONE_IN_CAR_2,
    }
)


def fingerprint(value: Any) -> Optional[str]:
    """A short, stable, non-reversible stand-in for a redacted identifier.

    The GCID is redacted -- it identifies the account -- but "do these two
    config entries belong to the same account?" is the first question when two
    of them misbehave together, because BMW allows **one concurrent stream per
    GCID**. Redaction made that unanswerable from two dumps (issue #23's
    reporter runs two accounts). A truncated digest answers it and reveals
    nothing: it cannot be reversed, and it only ever matches another dump of
    the same account.
    """

    if not value or not isinstance(value, str):
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def safe_options(options: Any) -> dict[str, Any]:
    """Config-entry options with user-chosen entity ids reduced to their domain."""

    safe: dict[str, Any] = {}
    for key, value in dict(options or {}).items():
        if key == OPTION_VEHICLES and isinstance(value, dict):
            # Keyed by VIN, which ``async_redact_data`` never sees (it redacts
            # values, not keys): each car is filed under its fingerprint, the
            # same one its entry in ``vehicles`` carries.
            safe[key] = {fingerprint(vin): safe_options(own) for vin, own in value.items()}
        elif key in ENTITY_ID_OPTIONS and isinstance(value, str) and value:
            domain = value.split(".", 1)[0]
            safe[key] = f"{domain}.**REDACTED**" if "." in value else "**REDACTED**"
        else:
            safe[key] = value
    return safe
