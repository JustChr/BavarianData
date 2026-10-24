"""Whether the stream is healthy, and when that becomes a repair for the user.

A diagnostics download turns "it doesn't work" into 30-second triage, but a
repair issue is what actually gets a stuck stream in front of the user. Two
are raised, both decided here:

* **unauthorized** -- an MQTT rc=5 that persists ``UNAUTHORIZED_REPAIR_AFTER_S``
  without a successful connect (reauth failed to clear it), and
* **no data** -- not one message for ``NO_DATA_REPAIR_AFTER_S``, measured from
  the last message or, on a stream that never delivered one, from its start.

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
from typing import Any, Deque, Dict, Optional

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


@dataclass
class StreamHealth:
    """The stream's state as the diagnostics and the repairs see it."""

    status: str = "connecting"
    last_disconnect_reason: Optional[str] = None
    # Recent connection transitions (status + rc/reason + time).
    history: Deque[Dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=CONNECTION_HISTORY_LEN), repr=False
    )
    # When the stream last stopped being connected; None while connected.
    down_since: Optional[datetime] = field(default=None, repr=False)
    # First time the stream went "unauthorized" without recovering since.
    # Cleared on the next successful connect.
    unauthorized_since: Optional[datetime] = None

    def note(self, status: str, reason: Optional[str], now: datetime) -> Optional[float]:
        """Record one connection event; the outage's length when it just ended.

        An outage is measured from the first event that was not "connected"
        after a connect, so the very first connect after setup -- with no
        outage behind it -- returns ``None``; the startup catch-up covers that.
        """

        outage_s: Optional[float] = None
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
        elif status == "connected":
            self.unauthorized_since = None
        return outage_s

    def repairs_due(
        self,
        now: datetime,
        *,
        last_message_at: Optional[datetime],
        started_at: Optional[datetime],
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
