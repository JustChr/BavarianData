"""One way into BMW's REST API: a valid token, a quota slot, then the request.

Every CarData request needs an access token and spends one of the account's 50
daily requests. Both steps used to be repeated by each caller, each a little
differently: most renewed the tokens before every request, and a renewal hands
the stream a new ID token, which costs it a reconnect (about ten seconds of
nothing heard, since BMW refuses a reconnect sooner). So a manual fetch, or a
restart's catch-up of two cars, dropped the stream once per request.

:class:`TokenKeeper` renews only when the access token is about to lapse, one
renewal at a time; the token-refresh loop (every 45 minutes) keeps it fresh,
so a request normally renews nothing. :class:`RestGateway` claims the quota
after the token is in hand, so a failed renewal spends nothing.

No Home Assistant imports: the rules are unit-tested directly.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Mapping, Optional, TypeVar

from .device_flow import CardataAuthError

T = TypeVar("T")

# Renew this long before the access token lapses, so a request that waits on a
# slow network still carries a token BMW accepts.
TOKEN_MARGIN_S = 5 * 60


class CardataQuotaError(Exception):
    """Raised when a request would exceed the daily quota."""


class TokenKeeper:
    """Hands out the access token, renewing it only when it is about to lapse.

    ``renew`` renews and stores the tokens (and gives the stream its new ID
    token); ``stored`` returns the stored token fields: ``access_token``,
    ``received_at`` (epoch seconds) and ``expires_in`` (seconds).
    """

    def __init__(
        self,
        renew: Callable[[], Awaitable[None]],
        stored: Callable[[], Mapping[str, Any]],
        *,
        clock: Callable[[], float] = time.time,
        margin_s: float = TOKEN_MARGIN_S,
    ) -> None:
        self._renew = renew
        self._stored = stored
        self._clock = clock
        self._margin_s = margin_s
        self._lock = asyncio.Lock()

    def _usable(self) -> Optional[str]:
        data = self._stored()
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            return None
        try:
            expires_at = float(data["received_at"]) + float(data["expires_in"])
        except KeyError, TypeError, ValueError:
            # Of unknown age: renew rather than send one BMW may refuse.
            return None
        return token if self._clock() < expires_at - self._margin_s else None

    async def access_token(self) -> str:
        """A token valid for at least the margin, renewing it if need be."""

        token = self._usable()
        if token is not None:
            return token
        async with self._lock:
            # A request waiting here behind another's renewal uses its result.
            token = self._usable()
            if token is not None:
                return token
            await self._renew()
            token = self._stored().get("access_token")
        if not isinstance(token, str) or not token:
            raise CardataAuthError("No access token after renewing the tokens")
        return token

    async def renew(self) -> None:
        """Renew now, whatever the token's age (the refresh loop, a refused stream)."""

        async with self._lock:
            await self._renew()


class RestGateway:
    """Runs a CarData request with a valid token and a claimed quota slot.

    ``claim`` is the quota manager's claim (it raises :class:`CardataQuotaError`
    when the day's requests are spent); ``None`` claims nothing.
    """

    def __init__(
        self,
        session: Any,
        tokens: TokenKeeper,
        claim: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        self.session = session
        self.tokens = tokens
        self._claim = claim

    async def claim(self) -> None:
        """Spend one request of the day's quota."""

        if self._claim is not None:
            await self._claim()

    async def call(self, request: Callable[..., Awaitable[T]], *args: Any) -> T:
        """``request(session, access_token, *args)``, once the token and a slot are in hand.

        Raises ``CardataAuthError`` when no token can be had (nothing is
        claimed then), :class:`CardataQuotaError` when the quota is spent, and
        whatever the request raises (``CardataApiError``).
        """

        token = await self.tokens.access_token()
        await self.claim()
        return await request(self.session, token, *args)
