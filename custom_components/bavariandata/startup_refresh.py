"""Which cars to fetch over REST to catch up after we stopped listening.

The stream only carries what changes while we are listening. Whatever the car
reported while Home Assistant was down, or while the stream was cut off by a
network outage -- a charge that ended, doors locked, a drive -- went to nobody,
and BMW does not replay it on reconnect. One container call per car catches up,
because BMW's backend returns the last value the car sent for every field. It
cannot wake the car, so it never yields a newer reading than the car last sent;
it closes the gap in what *we* heard.

The quota (50 requests a day) is what shapes the rules below. Kept free of Home
Assistant imports so they can be unit-tested.
"""

from __future__ import annotations

from typing import Iterable, List, Optional

# Give the stream a head start: BMW often republishes the charging status soon
# after a reconnect, which settles a restored charge for free. Well inside the
# restored session's grace (``coordinator.RESTORED_SESSION_GRACE_S``), so the
# answer lands before that session would be filed as ended by the restart.
STARTUP_REFRESH_DELAY_S = 120

# A catch-up is pointless when BMW was asked this recently -- and it is what
# stops a restart loop (config edits, a crashing custom component) from
# spending a request per car on every restart.
STARTUP_REFRESH_FRESH_S = 60 * 60

# A charge the restart left unconfirmed is worth asking about sooner: until
# it is settled the session record and the SoC estimate are guesses.
STARTUP_CHARGE_REFRESH_SPACING_S = 30 * 60

# A stream that was down at least this long before reconnecting gets the same
# catch-up as a start. Shorter gaps are the stream's own reconnects, and the
# freshness rule above already caps a flapping connection at one request per car
# per hour.
OUTAGE_CATCH_UP_AFTER_S = 5 * 60

# Never spend the last of the day's quota on a catch-up: leave room for the
# daily refresh and for the user's own service calls.
STARTUP_REFRESH_RESERVE = 10


def _unique(values: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(values))


def startup_refresh_vins(
    *,
    enabled: bool,
    vins: Iterable[str],
    unconfirmed: Iterable[str],
    last_fetch_at: Optional[float],
    now: float,
    remaining: Optional[int],
) -> List[str]:
    """The cars to fetch after a start, most urgent first.

    Every car, unless BMW was asked within the last hour; even then, a car whose
    charge the restart left unconfirmed, unless it was asked within the last
    half hour. Whatever is picked is cut to the quota left above the reserve.
    """

    if not enabled:
        return []
    urgent = _unique(unconfirmed)
    everyone = _unique([*urgent, *vins])
    age = None if last_fetch_at is None else now - last_fetch_at
    if age is None or age >= STARTUP_REFRESH_FRESH_S:
        chosen = everyone
    elif age >= STARTUP_CHARGE_REFRESH_SPACING_S:
        chosen = urgent
    else:
        chosen = []
    if remaining is not None:
        chosen = chosen[: max(remaining - STARTUP_REFRESH_RESERVE, 0)]
    return chosen


def outage_needs_catch_up(down_seconds: Optional[float]) -> bool:
    """Whether a reconnect followed an outage long enough to have missed data."""

    return down_seconds is not None and down_seconds >= OUTAGE_CATCH_UP_AFTER_S
