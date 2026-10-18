"""Which vehicles a config entry speaks for.

One CarData account can hold several cars, and a single config entry covers all
of them: one OAuth client, one MQTT stream, one REST quota. Anything that walks
the account -- the daily REST refresh above all -- therefore needs the *whole*
list, not whichever VIN happens to be at hand.

Home Assistant-free on purpose (like ``descriptors.py`` and the history layer),
because the selection itself is what went wrong once and a shape guard over the
call site cannot prove the ordering: see ``tests/test_vehicles.py``.

The order matters and is deliberate. Quota is finite and a round can be cut
short halfway through, so the vehicle that was being refreshed before must stay
first -- degrading to the old behaviour is acceptable, starving the car that
used to work is not.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Mapping, Optional

__all__ = [
    "account_title",
    "forget_in_entry_data",
    "known_vins",
    "left_account_issue_id",
    "left_account_vins",
    "new_account_title",
    "primary_vins",
    "setup_title",
]

# The title setup gives an entry before any car is known, in both spellings it
# has had: the 8-character client-id fragment was all that told two accounts
# apart. Matched whole, so a title the user typed is never taken for ours.
_SETUP_TITLE = re.compile(
    r"BavarianData(?:: Connect Home Assistant to BMW CarData)? \([^()]{1,8}\)"
)


def known_vins(
    *,
    stored_metadata: Optional[Mapping[str, Any]] = None,
    coordinator_data: Optional[Mapping[str, Any]] = None,
    configured_vin: Optional[str] = None,
) -> list[str]:
    """Every VIN this entry knows about, most-established first.

    ``configured_vin`` is the legacy single-vehicle key some entries still
    carry; ``stored_metadata`` is what BMW's basic data reported per car and
    survives restarts; ``coordinator_data`` is whatever has since arrived over
    the stream, which is the only source for a car added after setup.

    Duplicates collapse to their first appearance, and anything that is not a
    non-empty string is dropped -- these VINs go straight into request paths.
    """

    ordered: list[str] = []
    seen: set[str] = set()

    def _add(candidate: Any) -> None:
        if not isinstance(candidate, str):
            return
        vin = candidate.strip()
        if not vin or vin in seen:
            return
        seen.add(vin)
        ordered.append(vin)

    _add(configured_vin)
    for source in (stored_metadata, coordinator_data):
        if isinstance(source, Mapping):
            for vin in source:
                _add(vin)

    return ordered


def primary_vins(payload: Any) -> list[str]:
    """The cars BMW's mapping endpoint lists for which the caller is the primary user.

    The endpoint answers with a list, or an object holding one under
    ``mappings`` / ``vehicles``. A mapping without a ``mappingType`` counts as
    primary (models without co-users have no such concept); a SECONDARY one is
    left out, because CarData only serves data for the primary user.
    """

    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, Mapping):
        items = payload.get("mappings") or payload.get("vehicles") or []
    else:
        return []

    found: list[str] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        mapping_type = item.get("mappingType")
        if isinstance(mapping_type, str) and mapping_type.upper() != "PRIMARY":
            continue
        vin = item.get("vin")
        if isinstance(vin, str) and vin.strip() and vin.strip() not in found:
            found.append(vin.strip())
    return found


def setup_title(client_id: str) -> str:
    """The entry's title until its cars are known."""

    return f"BavarianData ({client_id[:8]})"


def account_title(names: Iterable[Any]) -> Optional[str]:
    """The cars of an account as its title: ``"i5 eDrive40, iX1 xDrive30"``."""

    cleaned = [name.strip() for name in names if isinstance(name, str) and name.strip()]
    return ", ".join(cleaned) or None


def new_account_title(
    current: Optional[str], last_auto: Optional[str], names: Iterable[Any]
) -> Optional[str]:
    """The title to give an entry now, or ``None`` to leave it alone.

    Only a title we set -- the setup one, or the car names we wrote last time
    (``last_auto``) -- is replaced; one the user renamed stays theirs.
    """

    wanted = account_title(names)
    if wanted is None or wanted == current:
        return None
    ours = not current or current == last_auto or bool(_SETUP_TITLE.fullmatch(current))
    return wanted if ours else None


def left_account_vins(account_vins: Iterable[str], mapping_payload: Any) -> Optional[list[str]]:
    """The cars this entry has that BMW's mapping no longer lists as primary.

    ``None`` when the payload isn't a mapping answer at all: a malformed reply
    must never read as "every car left".
    """

    if isinstance(mapping_payload, Mapping):
        if not any(key in mapping_payload for key in ("mappings", "vehicles")):
            return None
    elif not isinstance(mapping_payload, list):
        return None
    listed = set(primary_vins(mapping_payload))
    return [vin for vin in account_vins if vin not in listed]


def left_account_issue_id(entry_id: str, vin: str) -> str:
    """The "no longer on your account" repair's id, without the VIN in it.

    Repair ids reach a diagnostics download unredacted (``coverage_issue_id``).
    """

    digest = hashlib.sha256(vin.encode("utf-8")).hexdigest()[:12]
    return f"vehicle_left_account_{entry_id}_{digest}"


def forget_in_entry_data(data: Mapping[str, Any], vin: str, metadata_key: str) -> dict[str, Any]:
    """The entry's data without ``vin``: its basic data and the legacy key."""

    updated = dict(data)
    metadata = updated.get(metadata_key)
    if isinstance(metadata, Mapping) and vin in metadata:
        updated[metadata_key] = {key: value for key, value in metadata.items() if key != vin}
    if updated.get("vin") == vin:
        updated.pop("vin")
    return updated
