"""Dynamic debug flag handling, and what may appear in a log line.

The integration handles two identifiers that are personal data: the VIN and the
GPS position. Verbose debug logging carries both, which is precisely why it is
opt-in (``debug_log``) and off by default.

Everything at INFO and above is different: it lands in ``home-assistant.log`` on
every install, and that file is what users paste into public issues. So the VIN
is masked to its last four characters there -- enough to tell two cars apart
while reading a log, not enough to identify a vehicle. Debug lines keep the full
VIN, because triage needs it and the user opted in to that.
"""

from __future__ import annotations

import logging
from typing import Any

from .const import DEBUG_LOG

_LOGGER_NAMESPACE = "custom_components.bavariandata"
_DEBUG_ENABLED = DEBUG_LOG


def set_debug_enabled(value: bool) -> None:
    """Update the global debug flag and logger level."""
    global _DEBUG_ENABLED
    _DEBUG_ENABLED = value
    logger = logging.getLogger(_LOGGER_NAMESPACE)
    logger.setLevel(logging.DEBUG if value else logging.INFO)


def debug_enabled() -> bool:
    """Return whether verbose debug logging is enabled."""
    return _DEBUG_ENABLED


def mask_vin(vin: Any) -> str:
    """Reduce a VIN to its last four characters for a default-level log line.

    Use this for anything logged at INFO or above; see the module docstring for
    why debug lines are exempt. Short or missing values collapse to a constant
    rather than leaking what little there was.
    """

    if not vin:
        return "***"
    text = str(vin)
    return f"***{text[-4:]}" if len(text) > 4 else "***"

set_debug_enabled(DEBUG_LOG)
