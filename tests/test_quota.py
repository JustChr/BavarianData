"""BMW's 50 requests a day, counted over a rolling 24 hours and kept across restarts.

``quota.QuotaManager`` runs here against an in-memory store and a clock the
test moves; the repair issue is two callbacks that record their calls.
"""

from __future__ import annotations

import asyncio

import pytest

from .conftest import load_module

quota = load_module("quota")
const = load_module("const")
rest = load_module("rest")

DAY = const.REQUEST_WINDOW_SECONDS
LIMIT = const.REQUEST_LIMIT
T0 = 1_800_000_000.0


class _Store:
    def __init__(self, data=None) -> None:
        self.data = data
        self.saved: list[dict] = []

    async def async_load(self):
        return self.data

    async def async_save(self, data) -> None:
        self.saved.append(data)


class _Clock:
    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _manager(stored=None, clock=None):
    store = _Store(stored)
    events: list = []
    manager = asyncio.run(
        quota.QuotaManager.async_create(
            store,
            on_exhausted=lambda reset: events.append(("exhausted", reset)),
            on_available=lambda: events.append("available"),
            clock=clock or _Clock(),
        )
    )
    return manager, store, events


def _claim(manager, times: int = 1) -> None:
    async def run():
        for _ in range(times):
            await manager.async_claim()

    asyncio.run(run())


def test_the_stored_claims_survive_a_restart_and_the_expired_ones_go() -> None:
    manager, store, _events = _manager(
        {"timestamps": [T0 - DAY - 1, T0 - 60, str(T0 - 30), "2027-01-15T08:00:00Z"]}
    )

    # The ISO one is T0 itself: 1.8e9 seconds is 2027-01-15 08:00 UTC.
    assert manager.used == 3
    assert store.saved[0]["timestamps"][0] == T0 - 60


def test_the_older_iso_format_is_still_read() -> None:
    iso = "2027-01-15T08:00:00+00:00"
    parsed = quota.parse_timestamps([iso, iso.replace("+00:00", "Z")])
    assert len(parsed) == 2 and parsed[0] == parsed[1]


@pytest.mark.parametrize("raw", [None, "junk", {"a": 1}, [None, "nope", True, {}]])
def test_damaged_claims_are_dropped_not_fatal(raw) -> None:
    assert list(quota.parse_timestamps(raw)) == []


def test_each_claim_counts_and_is_saved() -> None:
    manager, store, events = _manager()
    _claim(manager, 3)

    assert (manager.used, manager.remaining) == (3, LIMIT - 3)
    assert len(store.saved[-1]["timestamps"]) == 3
    assert events == ["available"] * 3
    assert manager.next_reset_iso is None, "only a spent quota has a reset time"


def test_the_fifty_first_request_is_refused_until_the_first_ages_out() -> None:
    clock = _Clock()
    manager, _store, events = _manager(clock=clock)
    for _ in range(LIMIT):
        _claim(manager)
        clock.now += 60

    with pytest.raises(rest.CardataQuotaError):
        _claim(manager)
    assert manager.remaining == 0
    assert events[-1] == ("exhausted", manager.next_reset_iso)
    assert manager.next_reset_epoch == T0 + DAY

    clock.now = T0 + DAY
    _claim(manager)
    assert events[-1] == "available"
    assert manager.used == LIMIT


def test_closing_saves_without_the_expired_claims() -> None:
    clock = _Clock()
    manager, store, _events = _manager(clock=clock)
    _claim(manager, 2)
    clock.now += DAY + 1

    asyncio.run(manager.async_close())
    assert store.saved[-1] == {"timestamps": []}
