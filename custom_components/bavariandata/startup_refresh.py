"""Whether to ask BMW's REST API about a charge a restart left unconfirmed.

A charge running when Home Assistant stopped is restored as *unconfirmed* and
waits for the stream to say whether it is still going. BMW may not say so for
hours: a car charging at steady power can stay silent, and a car that stopped
while we were away already reported it -- to nobody. One container call answers
both, because BMW's backend returns the last status the car sent. It cannot wake
the car, so it never yields a fresher SoC; it settles *whether* it is charging,
which is what the session record and the SoC estimate need.

Kept free of Home Assistant imports so the decision can be unit-tested.
"""

from __future__ import annotations

from typing import Iterable, List, Optional

# Give the stream a head start: BMW often republishes the charging status soon
# after a reconnect, which answers the question for free. Well inside the
# restored session's grace (``coordinator.RESTORED_SESSION_GRACE_S``), so the
# answer lands before the session would be filed as ended by the restart.
STARTUP_REFRESH_DELAY_S = 120

# A restart loop (config edits, a crashing custom component) must not drain the
# 50-a-day quota: at most one startup refresh per half hour.
STARTUP_REFRESH_MIN_SPACING_S = 30 * 60


def startup_refresh_vins(
    *,
    enabled: bool,
    unconfirmed: Iterable[str],
    last_refresh_at: Optional[float],
    now: float,
) -> List[str]:
    """The cars to fetch after a restart -- usually none, so usually free.

    Only cars whose restored charge the stream has not confirmed yet: a car that
    was not charging, or whose status arrived during the head start, costs no
    request at all.
    """

    if not enabled:
        return []
    vins = list(dict.fromkeys(unconfirmed))
    if not vins:
        return []
    if last_refresh_at is not None and now - last_refresh_at < STARTUP_REFRESH_MIN_SPACING_S:
        return []
    return vins
