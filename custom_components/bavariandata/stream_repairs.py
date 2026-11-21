"""The stream's two repairs, raised and cleared in Home Assistant's registry.

``stream_health.py`` decides *when* a stream is in trouble (BMW refusing the
login for minutes, or no data for hours); this is the Home Assistant half that
shows it: the repair a user reads, worded for what is wrong and linked to the
wiki's fix. Latched, so the registry is called on a change only, never on
every watchdog tick.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .stream_health import (
    NO_DATA_REPAIR_AFTER_S,
    UNAUTHORIZED_REPAIR_AFTER_S,
    WIKI_TROUBLESHOOTING,
    StreamHealth,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class StreamRepairs:
    """The rc=5 and the no-data repair of one account."""

    def __init__(self, hass: HomeAssistant, entry_id: str, health: StreamHealth) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._health = health
        # The rc=5 repair standing now, by its translation key; None when none does.
        self._unauthorized: str | None = None
        self._no_data = False

    def evaluate(
        self,
        now: datetime,
        *,
        last_message_at: datetime | None,
        started_at: datetime | None,
    ) -> None:
        """Raise or clear both repairs as the stream's health says they stand.

        Idempotent and latched: the underlying registry calls are only made on a
        state transition, so this is cheap to run from both the watchdog tick and
        every connection event. Both issues carry a ``learn_more_url`` to the
        matching Wiki anchor -- the fix, not just the symptom.
        """

        unauthorized, no_data = self._health.repairs_due(
            now, last_message_at=last_message_at, started_at=started_at
        )
        self._set_unauthorized(unauthorized)
        self._set_no_data(no_data)

    def clear(self) -> None:
        """Drop both repairs (on unload)."""

        ir.async_delete_issue(self._hass, DOMAIN, f"stream_unauthorized_{self._entry_id}")
        ir.async_delete_issue(self._hass, DOMAIN, f"stream_no_data_{self._entry_id}")

    def _account_name(self) -> str:
        """The account as its repairs name it: the entry's title, its cars."""

        entries = getattr(self._hass, "config_entries", None)
        entry = entries.async_get_entry(self._entry_id) if entries is not None else None
        return getattr(entry, "title", None) or "BavarianData"

    def _set_unauthorized(self, active: bool) -> None:
        """Raise, reword or clear the rc=5 repair.

        A renewed token refused as well means the login works and another
        client holds the account's one stream (``StreamHealth.held_elsewhere``):
        the repair then says so, instead of asking for new tokens that would not
        help. The same issue id either way, so one replaces the other.
        """

        issue_id = f"stream_unauthorized_{self._entry_id}"
        key: str | None = None
        if active:
            key = "stream_held_elsewhere" if self._health.held_elsewhere else "stream_unauthorized"
        if key == self._unauthorized:
            return
        self._unauthorized = key
        if key is None:
            ir.async_delete_issue(self._hass, DOMAIN, issue_id)
            return
        anchor = (
            "only-one-stream-per-account"
            if key == "stream_held_elsewhere"
            else "stream-authorization-failing"
        )
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=key,
            translation_placeholders={
                "minutes": str(UNAUTHORIZED_REPAIR_AFTER_S // 60),
                "account": self._account_name(),
            },
            learn_more_url=f"{WIKI_TROUBLESHOOTING}#{anchor}",
        )

    def _set_no_data(self, active: bool) -> None:
        issue_id = f"stream_no_data_{self._entry_id}"
        if active == self._no_data:
            return
        self._no_data = active
        if not active:
            ir.async_delete_issue(self._hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="stream_no_data",
            translation_placeholders={
                "hours": str(NO_DATA_REPAIR_AFTER_S // 3600),
                "account": self._account_name(),
            },
            learn_more_url=f"{WIKI_TROUBLESHOOTING}#no-data-arriving",
        )
