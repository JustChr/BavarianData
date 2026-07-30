"""Automatic business/private/commute classification of a trip.

Home Assistant-free (see ``models.py``) so the rules are unit-testable. They are
deliberately simple and explainable:

* a drive between home and work, either way round, is a *commute*;
* anything else takes the user's configured default type (``private`` unless
  they changed it, or nothing at all if they set ``unclassified``).

The whole point is a sensible default the user can correct, not a clever
inference the user has to argue with (roadmap rule 4). Every classification this
module produces is recorded as ``SOURCE_AUTO``, which the manual override always
outranks -- so defaulting is never a claim, just a starting point.

``business`` is still never *inferred*: it is reachable only because the user
chose it as their default (a company-car driver making that claim once, up front)
or set it on a trip by hand.

:func:`commute_chain` extends the commute rule across a stop on the way -- the
supermarket between home and work -- which the detector necessarily records as
two separate drives. See its docstring for the exact shape of a chain.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from .trips import (
    CLASS_BUSINESS,
    CLASS_COMMUTE,
    CLASS_PRIVATE,
    CLASSIFICATIONS,
    SOURCE_USER,
    Trip,
)

# Option value meaning "don't label it, I'll triage by hand" -- the behaviour
# before a default type was configurable, kept as an explicit choice.
CLASS_UNCLASSIFIED = "unclassified"
# What the default-type setting may be set to, in the order the options flow
# offers them. ``commute`` is deliberately absent: defaulting every drive to a
# commute would make the home/work rule meaningless.
DEFAULT_CLASS_CHOICES = (CLASS_PRIVATE, CLASS_BUSINESS, CLASS_UNCLASSIFIED)

# Hard bound on how many drives one commute chain may span (the arriving leg
# included). A chain is meant to absorb an errand or two on the way to work; a
# longer sequence of short-gap hops is a day of running around, so it is left to
# the default type rather than silently called a commute.
COMMUTE_CHAIN_MAX_LEGS = 5


def _zone_of(place: Optional[dict[str, Any]]) -> Optional[str]:
    if not place:
        return None
    zone = place.get("zone")
    return zone.casefold() if isinstance(zone, str) and zone else None


def _terminals(home: Optional[str], work: Optional[str]) -> Optional[set[str]]:
    """The two commute endpoints, folded for comparison.

    ``None`` when commute detection can't run at all: either zone unset, or both
    pointing at the same zone (in which case every trip home would look like a
    commute).
    """

    home_name = home.casefold() if home else None
    work_name = work.casefold() if work else None
    if home_name is None or work_name is None or home_name == work_name:
        return None
    return {home_name, work_name}


def is_commute(
    start_place: Optional[dict[str, Any]],
    end_place: Optional[dict[str, Any]],
    *,
    home: Optional[str],
    work: Optional[str],
) -> bool:
    """True when a single drive connects the home zone and the work zone."""

    terminals = _terminals(home, work)
    if terminals is None:
        return False
    return terminals <= {_zone_of(start_place), _zone_of(end_place)}


def classify_trip(
    start_place: Optional[dict[str, Any]],
    end_place: Optional[dict[str, Any]],
    *,
    home: Optional[str],
    work: Optional[str],
    default_class: Optional[str] = CLASS_PRIVATE,
) -> Optional[str]:
    """Best-effort class for a trip between two places.

    ``home`` and ``work`` are zone names (``home`` is normally HA's ``zone.home``;
    ``work`` comes from options and may be unset). Matching is case-insensitive.
    A trip that connects the home zone and the work zone -- either way round --
    is a commute; every other trip takes ``default_class``.

    ``default_class`` is the user's setting: ``private`` out of the box,
    ``business`` for someone whose driving is mostly work, or ``unclassified``
    (also ``None``) to leave non-commutes unlabelled. Note that a trip with an
    unknown endpoint -- no GPS at all, or an endpoint outside every zone -- is
    *also* just "not a recognised commute", so it takes the default too: whether
    we happen to know where the car was says nothing about the trip's purpose.
    """

    if is_commute(start_place, end_place, home=home, work=work):
        return CLASS_COMMUTE
    return default_class if default_class in CLASSIFICATIONS else None


def trip_class_setting(value: Optional[str]) -> Optional[str]:
    """The stored default-type option as a classification.

    ``None`` for ``unclassified``, for an unset option, and for anything
    unrecognised -- so a stale or hand-edited value degrades to "don't label it"
    rather than writing a class no part of the UI knows about.
    """

    return value if value in CLASSIFICATIONS else None


def commute_chain(
    trip: Trip,
    previous: Sequence[Trip],
    *,
    home: Optional[str],
    work: Optional[str],
    gap_s: float,
    max_legs: int = COMMUTE_CHAIN_MAX_LEGS,
) -> Optional[list[Trip]]:
    """The earlier legs that make ``trip`` the end of a commute, or ``None``.

    A commute survives a stop on the way: buying groceries between home and work
    parks the car long enough for the detector to close one drive and open
    another, leaving two records that are individually neither home->work nor
    work->home. This walks back from a just-finished trip and decides whether the
    *chain* it belongs to is a commute.

    A chain is the run of consecutive drives whose gaps -- the time the car stood
    between one leg ending and the next beginning -- are all at most ``gap_s``.
    It is a commute when the chain starts in one commute zone and ``trip`` ends in
    the other. Two rules keep that honest:

    * **A chain ends when it reaches a commute zone.** Walking back stops at a leg
      that *started* at home or work (that leg is the chain's origin) and also at
      one that *ended* there (that arrival completed an earlier chain, so this one
      began afterwards). Without the second rule home->work, work->lunch,
      lunch->work would retro-label the lunch run a commute.
    * **Both ends of the chain are checked, not any endpoint.** home->bakery->home
      stays private, because its chain begins and ends at home.

    ``previous`` is the vehicle's stored trips (``trip`` itself excluded, since it
    isn't stored yet); order doesn't matter. ``gap_s`` of 0 disables chaining.

    Returns ``None`` when the chain is not a commute, otherwise the earlier legs
    that need rewriting to ``commute`` -- which may be an empty list when they all
    already say so or were classified by hand. An empty list is therefore *not*
    the same answer as ``None``: it still means "this is a commute". A plain
    single-leg home->work drive is :func:`classify_trip`'s job, so this is only
    worth calling when that returned something else.
    """

    if gap_s <= 0:
        return None
    terminals = _terminals(home, work)
    if terminals is None:
        return None
    # A chain can only be judged once it arrives somewhere that matters.
    end_zone = _zone_of(trip.end_place)
    if end_zone not in terminals:
        return None

    chain: list[Trip] = []
    cursor = trip
    origin: Optional[str] = None
    for leg in sorted(previous, key=lambda item: item.start, reverse=True):
        if leg.id == trip.id or leg.end is None or leg.start >= cursor.start:
            continue
        if (cursor.start - leg.end).total_seconds() > gap_s:
            break  # the car stood too long: a separate journey
        if _zone_of(leg.end_place) in terminals:
            break  # that arrival closed the previous chain
        chain.append(leg)
        cursor = leg
        leg_origin = _zone_of(leg.start_place)
        if leg_origin in terminals:
            origin = leg_origin
            break
        if len(chain) >= max_legs - 1:
            break

    if origin is None or {origin, end_zone} != terminals:
        return None
    return [
        leg
        for leg in chain
        if leg.classification_source != SOURCE_USER
        and leg.classification != CLASS_COMMUTE
    ]
