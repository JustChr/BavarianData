"""Automatic business/private/commute classification of a trip.

Home Assistant-free (see ``models.py``) so the rule is unit-testable. The rule is
deliberately simple and explainable: a drive between home and work (in either
direction) is a *commute*; anything else defaults to *private*. It never guesses
*business* -- that is a claim only the driver can make, so business is reached
only through the manual override, never by this function.

The whole point is a sensible default the user can correct, not a clever
inference the user has to argue with (roadmap rule 4). ``classify_trip`` returns
``None`` when it genuinely can't tell (an endpoint with no known zone), so such a
trip stays unclassified rather than being mislabelled.
"""

from __future__ import annotations

from typing import Any, Optional

from .trips import CLASS_COMMUTE, CLASS_PRIVATE


def _zone_of(place: Optional[dict[str, Any]]) -> Optional[str]:
    if not place:
        return None
    zone = place.get("zone")
    return zone.casefold() if isinstance(zone, str) and zone else None


def classify_trip(
    start_place: Optional[dict[str, Any]],
    end_place: Optional[dict[str, Any]],
    *,
    home: Optional[str],
    work: Optional[str],
) -> Optional[str]:
    """Best-effort class for a trip between two places.

    ``home`` and ``work`` are zone names (``home`` is normally HA's ``zone.home``;
    ``work`` comes from options and may be unset). Matching is case-insensitive.
    A trip that connects the home zone and the work zone -- either way round --
    is a commute; any trip where both endpoints are known is otherwise private;
    a trip with an unknown endpoint returns ``None``.
    """

    home_name = home.casefold() if home else None
    work_name = work.casefold() if work else None

    start_zone = _zone_of(start_place)
    end_zone = _zone_of(end_place)
    if start_zone is None and end_zone is None:
        return None

    endpoints = {start_zone, end_zone}
    if (
        home_name is not None
        and work_name is not None
        and home_name in endpoints
        and work_name in endpoints
    ):
        return CLASS_COMMUTE

    # Both ends known but not the home/work pair -> a private trip. If only one
    # end is known we can't responsibly default, so we say nothing.
    if start_zone is not None and end_zone is not None:
        return CLASS_PRIVATE
    return None
