"""Hand each reading to the entities that show it, and to no one else.

Every reading the coordinator stores goes out on one dispatcher signal,
``signal_update``, as ``(vin, descriptor)``. Each descriptor sensor used to
listen to that signal itself and drop the readings that were not its own, so
one batch from one car woke every descriptor entity of every car -- several
hundred calls per batch, and Home Assistant ran each of them on a worker
thread, since none was a callback.

The router listens once (``__init__._async_wire_router``, as a callback) and
looks the reading up by car and descriptor. A subscriber names the car, or
``None`` for every car (the platforms that create a car's entity on its first
reading), and the descriptors, or ``None`` for all of them. Subscribers run on
the event loop, in the order they subscribed within each of the four lookups.

No Home Assistant imports: the dispatcher connection is the caller's.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import logging

_LOGGER = logging.getLogger(__name__)

type Target = Callable[[str, str], None]
type Key = tuple[str | None, str | None]


class DescriptorRouter:
    """One account's readings, routed by car and descriptor."""

    def __init__(self) -> None:
        self._targets: dict[Key, list[Target]] = {}

    def subscribe(
        self, vin: str | None, descriptors: Iterable[str] | None, target: Target
    ) -> Callable[[], None]:
        """Call ``target(vin, descriptor)`` for these readings; returns the unsubscribe.

        ``vin=None`` is every car, ``descriptors=None`` every descriptor.
        """

        keys: list[Key] = (
            [(vin, None)]
            if descriptors is None
            else [(vin, descriptor) for descriptor in dict.fromkeys(descriptors)]
        )
        for key in keys:
            self._targets.setdefault(key, []).append(target)

        def unsubscribe() -> None:
            for key in keys:
                targets = self._targets.get(key)
                if targets is None:
                    continue
                try:
                    targets.remove(target)
                except ValueError:
                    continue
                if not targets:
                    del self._targets[key]

        return unsubscribe

    def dispatch(self, vin: str, descriptor: str) -> None:
        """Hand one stored reading to everyone who asked for it.

        A subscriber that raises is logged and skipped, as Home Assistant's
        dispatcher does, so one broken entity cannot silence the others.
        """

        for key in ((vin, descriptor), (vin, None), (None, descriptor), (None, None)):
            targets = self._targets.get(key)
            if not targets:
                continue
            # A copy: a target may unsubscribe (an entity removed) mid-dispatch.
            for target in list(targets):
                try:
                    target(vin, descriptor)
                except Exception:
                    _LOGGER.exception("Error handing %s to %s", descriptor, target)

    def __len__(self) -> int:
        """How many (car, descriptor) listeners stand, for tests."""

        return sum(len(targets) for targets in self._targets.values())
