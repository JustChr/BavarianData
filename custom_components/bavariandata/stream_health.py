"""Whether the stream is healthy, and when that becomes a repair for the user.

A diagnostics download turns "it doesn't work" into 30-second triage, but a
repair issue is what actually gets a stuck stream in front of the user. Two
are raised, both decided here:

* **unauthorized** -- an MQTT rc=5 that persists ``UNAUTHORIZED_REPAIR_AFTER_S``
  without a successful connect (reauth failed to clear it), and
* **no data** -- not one message for ``NO_DATA_REPAIR_AFTER_S``, measured from
  the last message or, on a stream that never delivered one, from its start.

An rc=5 that a renewed token gets as well is not a login problem: BMW keeps
the account's one stream for whichever client holds it and turns every other
away with the same rc=5 (measured 2026-11-20). The stream reports that case
with :data:`REFUSED_RENEWED`, and the repair then says another program holds
the stream instead of asking for new tokens.

The rc=5 repair wins: while it stands, "no data" would only restate the same
outage more vaguely. :class:`StreamHealth` also keeps what the diagnostics
download shows -- the status, the last disconnect reason and a short history
of transitions -- and measures each outage, so a long one can end with a
catch-up. The coordinator raises and clears the issues; nothing here imports
Home Assistant.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Both repairs link to the Wiki's Troubleshooting page so the fix is one click
# away; the URL lives here rather than in the translation strings because
# hassfest forbids URLs inside translations/*.json.
WIKI_TROUBLESHOOTING = "https://github.com/JustChr/BavarianData/wiki/Troubleshooting-and-FAQ"
# How long the stream may go without a single message before we raise a repair.
# Long enough that a car simply parked in a garage for a weekend (BMW streams
# most descriptors only on a state change) doesn't trip it, short enough that a
# genuinely broken selection surfaces within a couple of days.
NO_DATA_REPAIR_AFTER_S = 48 * 60 * 60
# How long an "unauthorized" (MQTT rc=5) condition must persist -- reauth having
# failed to clear it -- before it is a repair rather than a transient blip the
# reconnect logic is already handling.
UNAUTHORIZED_REPAIR_AFTER_S = 30 * 60
# Bounded ring buffer of connection transitions (status + rc/reason), surfaced in
# the diagnostics download so a reconnect storm is visible after the fact.
CONNECTION_HISTORY_LEN = 30
# The reason the stream gives when a renewed token is refused as well: the login
# works, so another client holds the account's one stream.
REFUSED_RENEWED = "MQTT rc=5 with a renewed token"


@dataclass
class StreamHealth:
    """The stream's state as the diagnostics and the repairs see it."""

    status: str = "connecting"
    last_disconnect_reason: str | None = None
    # Recent connection transitions (status + rc/reason + time).
    history: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=CONNECTION_HISTORY_LEN), repr=False
    )
    # When the stream last stopped being connected; None while connected.
    down_since: datetime | None = field(default=None, repr=False)
    # First time the stream went "unauthorized" without recovering since.
    # Cleared on the next successful connect.
    unauthorized_since: datetime | None = None
    # A renewed token was refused too since then: another client holds the
    # stream. Cleared, like ``unauthorized_since``, on the next connect.
    held_elsewhere: bool = False

    def note(self, status: str, reason: str | None, now: datetime) -> float | None:
        """Record one connection event; the outage's length when it just ended.

        An outage is measured from the first event that was not "connected"
        after a connect, so the very first connect after setup -- with no
        outage behind it -- returns ``None``; the startup catch-up covers that.
        """

        outage_s: float | None = None
        if status == "connected":
            down_since, self.down_since = self.down_since, None
            if down_since is not None:
                outage_s = (now - down_since).total_seconds()
        elif self.down_since is None and self.status == "connected":
            self.down_since = now
        self.status = status
        if reason:
            self.last_disconnect_reason = reason
        elif status == "connected":
            self.last_disconnect_reason = None
        self.history.append({"at": now.isoformat(), "status": status, "reason": reason})
        # The unresolved "unauthorized" window opens on the first unauthorized
        # event and closes only on a successful connect: exactly the condition
        # the persisting-rc=5 repair flags.
        if status == "unauthorized":
            if self.unauthorized_since is None:
                self.unauthorized_since = now
            if reason == REFUSED_RENEWED:
                self.held_elsewhere = True
        elif status == "connected":
            self.unauthorized_since = None
            self.held_elsewhere = False
        return outage_s

    def repairs_due(
        self,
        now: datetime,
        *,
        last_message_at: datetime | None,
        started_at: datetime | None,
    ) -> tuple[bool, bool]:
        """Which repairs should stand now: ``(unauthorized, no_data)``."""

        unauthorized = (
            self.unauthorized_since is not None
            and (now - self.unauthorized_since).total_seconds() >= UNAUTHORIZED_REPAIR_AFTER_S
        )
        reference = last_message_at or started_at
        no_data = (
            not unauthorized
            and reference is not None
            and (now - reference).total_seconds() >= NO_DATA_REPAIR_AFTER_S
        )
        return unauthorized, no_data
