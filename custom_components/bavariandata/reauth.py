"""When an account renews its tokens, and when it asks the user to authorize again.

Two paths lead here. The token-refresh loop renews the ID token the stream
authenticates with, on a cadence well inside its one-hour life, backing off
while the network fails. And the stream reports a login BMW refused
("unauthorized") or one that works again ("recovered"). Either may end in a
reauth flow -- but only when BMW rejected the login itself: a network error or a
BMW hiccup is retried, because asking the user to log in again would be for
nothing.

Home Assistant-free: the flow, its notice and the entry's data come through the
small :class:`ReauthHost` that ``__init__.py`` implements.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import Any, Awaitable, Callable, Mapping, Optional, Protocol

from .const import DEFAULT_REFRESH_INTERVAL, TOKEN_REFRESH_RETRY_DELAY, TOKEN_REFRESH_RETRY_MAX
from .device_flow import refresh_rejected

_LOGGER = logging.getLogger(__name__)

# A refresh after "unauthorized", and a reauth flow, at most this often.
RETRY_SPACING_S = 30


class ReauthHost(Protocol):
    """What the gate needs of the entry it guards."""

    entry_id: str
    tokens: Any
    refresh_wake: asyncio.Event

    async def start_reauth(self) -> Optional[str]:
        """Start Home Assistant's reauth flow for the entry; its flow id."""

    async def abort_flow(self, flow_id: str) -> None:
        """Abort a flow this gate started."""

    def dismiss_notice(self) -> None:
        """Dismiss the reauth notice, if one is up."""


class ReauthGate:
    """One entry's reauth state: whether a flow is open, and when we last tried."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self.in_progress = False
        self.flow_id: Optional[str] = None
        self.last_reauth_attempt = 0.0
        self.last_refresh_attempt = 0.0
        # A refresh after "unauthorized" already failed: the next one starts
        # the flow without trying again.
        self.pending = False

    # -- what the config flow tells us -----------------------------------------

    def authorized(self) -> None:
        """The user authorized again: start from nothing."""

        self.in_progress = False
        self.flow_id = None
        self.last_reauth_attempt = 0.0
        self.last_refresh_attempt = 0.0
        self.pending = False

    def abandoned(self) -> None:
        """The reauth flow ended without new tokens."""

        self.in_progress = False
        self.flow_id = None

    def started_by_user(self) -> None:
        """The user started a reauth themselves (Configure)."""

        self.in_progress = True
        self.pending = True

    def renewed(self) -> None:
        """A token refresh worked."""

        self.pending = False

    # -- what the stream tells us ------------------------------------------------

    async def stream_error(self, host: ReauthHost, reason: str) -> None:
        """The stream reports ``unauthorized`` or ``recovered``."""

        if reason == "unauthorized":
            await self._unauthorized(host)
        elif reason == "recovered":
            if self.in_progress:
                self.in_progress = False
                _LOGGER.info("BMW stream connection restored; dismissing reauth notification")
                host.dismiss_notice()
                if self.flow_id:
                    with suppress(Exception):
                        await host.abort_flow(self.flow_id)
                    self.flow_id = None
            self.pending = False
            self.last_reauth_attempt = 0.0

    async def _unauthorized(self, host: ReauthHost) -> None:
        entry_id = host.entry_id
        if self.in_progress:
            _LOGGER.debug("Ignoring duplicate unauthorized notification for entry %s", entry_id)
            return

        now = self._clock()
        if self.pending:
            _LOGGER.debug(
                "Reauth pending for entry %s after failed refresh; starting flow", entry_id
            )
        elif now - self.last_refresh_attempt >= RETRY_SPACING_S:
            self.last_refresh_attempt = now
            try:
                _LOGGER.info(
                    "Attempting token refresh after unauthorized response for entry %s", entry_id
                )
                await host.tokens.renew()
                self.in_progress = False
                self.last_reauth_attempt = 0.0
                self.pending = False
                return
            except Exception as err:  # noqa: BLE001 - classified right below
                if not refresh_rejected(err):
                    # The network or BMW failed, not the credentials: no reauth.
                    # Have the refresh loop retry soon, or the stream waiting for
                    # a new token would sit idle until the next regular refresh.
                    _LOGGER.warning(
                        "Token refresh after unauthorized failed for entry %s, retrying: %s",
                        entry_id,
                        err,
                    )
                    host.refresh_wake.set()
                    return
                _LOGGER.warning(
                    "Token refresh after unauthorized failed for entry %s: %s", entry_id, err
                )
        else:
            self.pending = True
            _LOGGER.debug(
                "Token refresh attempted recently for entry %s; will trigger reauth", entry_id
            )

        await self.request(host, "BMW stream unauthorized")

    async def request(self, host: ReauthHost, why: str) -> None:
        """Ask the user to authorize again, unless that is already under way."""

        now = self._clock()
        if self.in_progress or now - self.last_reauth_attempt < RETRY_SPACING_S:
            _LOGGER.debug("Reauth already requested for entry %s; skipping new flow", host.entry_id)
            return

        self.in_progress = True
        self.last_reauth_attempt = now
        self.pending = False
        _LOGGER.error("%s; starting reauth flow", why)
        if self.flow_id:
            with suppress(Exception):
                await host.abort_flow(self.flow_id)
            self.flow_id = None
        # No notice of our own: Home Assistant posts one for every reauth flow,
        # and its card names the entry, whose title is its cars. Ours was a
        # second, English-only one that said neither which account nor which car.
        self.flow_id = await host.start_reauth()


async def wait_for_refresh(wake: asyncio.Event, delay: float) -> None:
    """Wait ``delay`` seconds, or less when a refresh is requested sooner."""

    with suppress(TimeoutError):
        async with asyncio.timeout(delay):
            await wake.wait()
    wake.clear()


async def refresh_loop(
    tokens: Any,
    wake: asyncio.Event,
    request_reauth: Callable[[str], Awaitable[None]],
    *,
    wait: Callable[[asyncio.Event, float], Awaitable[None]] = wait_for_refresh,
) -> None:
    """Renew the tokens for as long as the entry is loaded.

    This loop renews the ID token the stream authenticates with, and restarts a
    stopped stream through ``async_update_credentials``. Nothing reports an
    exception that escapes a background task, so no failed refresh may end it:
    retry well within the token's one-hour lifetime, backing off while it fails.
    ``wake`` lets a stream whose login was refused ask for a refresh right away.
    It is also what keeps the access token fresh, so a REST request normally
    needs no renewal of its own (rest.py).
    """

    delay = DEFAULT_REFRESH_INTERVAL
    retry_delay = TOKEN_REFRESH_RETRY_DELAY
    try:
        while True:
            await wait(wake, delay)
            try:
                await tokens.renew()
            except Exception as err:  # noqa: BLE001 - no failure may end the loop
                if refresh_rejected(err):
                    # Retrying cannot fix a login BMW no longer accepts, so ask
                    # the user once and go back to the regular cadence instead
                    # of logging an error every few minutes until they do.
                    await request_reauth(f"Token refresh rejected ({err})")
                    delay = DEFAULT_REFRESH_INTERVAL
                    retry_delay = TOKEN_REFRESH_RETRY_DELAY
                    continue
                _LOGGER.warning("Token refresh failed, retrying in %ss: %s", retry_delay, err)
                delay = retry_delay
                retry_delay = min(retry_delay * 2, TOKEN_REFRESH_RETRY_MAX)
            else:
                delay = DEFAULT_REFRESH_INTERVAL
                retry_delay = TOKEN_REFRESH_RETRY_DELAY
    except asyncio.CancelledError:
        return


def renewed_entry_data(
    data: Mapping[str, Any], token_data: Mapping[str, Any], now: float
) -> dict[str, Any]:
    """The entry's data after a token refresh answered ``token_data``.

    BMW may leave out a new refresh token, scope or token type; the stored ones
    stay then. Raises ``KeyError`` naming ``id_token`` when the answer has none,
    which the stream cannot do without.
    """

    new_id_token = token_data.get("id_token")
    if not new_id_token:
        raise KeyError("id_token")
    return {
        **data,
        "access_token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token", data.get("refresh_token")),
        "id_token": new_id_token,
        "expires_in": token_data.get("expires_in"),
        "scope": token_data.get("scope", data.get("scope")),
        "token_type": token_data.get("token_type", data.get("token_type")),
        "received_at": now,
    }
