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

from typing import Any, Mapping, Optional

__all__ = ["known_vins", "primary_vins"]


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
