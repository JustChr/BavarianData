"""Dynamic debug flag handling, and what may appear in a log line.

The integration handles two identifiers that are personal data: the VIN and the
GPS position. Verbose debug logging carries both, which is precisely why it is
opt-in (``debug_log``) and off by default.

Everything at INFO and above is different: it lands in ``home-assistant.log`` on
every install, and that file is what users paste into public issues. So the VIN
is masked to its last four characters there -- enough to tell two cars apart
while reading a log, not enough to identify a vehicle. Debug lines keep the full
VIN, because triage needs it and the user opted in to that.

The level itself is a property of a *logger*, so it cannot be per config entry:
one Python logger serves the whole integration. What can be per entry is who
asked for it, which is what this module tracks -- the flag is on while **any**
loaded entry wants it. Two accounts used to mean the last entry to set up (or to
have its options saved) silently decided for both, so turning debug on for one
account switched it off for the one that was being diagnosed. Verbose logging
therefore spans both accounts while either has it on; the options screen says so.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .const import DEBUG_LOG

_LOGGER_NAMESPACE = "custom_components.bavariandata"
# What each loaded config entry asked for, by entry id. Not a plain bool: see
# the module docstring -- the flag is the union, so one account cannot turn the
# other's logging off.
_ENTRY_CHOICES: dict[str, bool] = {}
# The build-time default, for calls that carry no entry (module import, tests).
_DEFAULT = DEBUG_LOG
_DEBUG_ENABLED = DEBUG_LOG


def _apply() -> None:
    global _DEBUG_ENABLED
    _DEBUG_ENABLED = _DEFAULT or any(_ENTRY_CHOICES.values())
    logger = logging.getLogger(_LOGGER_NAMESPACE)
    logger.setLevel(logging.DEBUG if _DEBUG_ENABLED else logging.INFO)


def set_debug_enabled(value: bool, *, entry_id: Optional[str] = None) -> None:
    """Record a debug choice and apply the union of every entry's.

    ``entry_id`` names the config entry the choice belongs to; without one the
    build-time default is changed instead, which is what the module's own import
    does. An entry that stops wanting debug logging only turns it off once no
    other entry wants it either -- see :func:`forget_entry`.
    """

    global _DEFAULT
    if entry_id is None:
        _DEFAULT = value
    else:
        _ENTRY_CHOICES[entry_id] = value
    _apply()


def forget_entry(entry_id: str) -> None:
    """Drop an unloaded entry's choice, so a removed account stops voting."""

    if _ENTRY_CHOICES.pop(entry_id, None) is not None:
        _apply()


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
