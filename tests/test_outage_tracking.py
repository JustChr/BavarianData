"""The coordinator measures stream outages for the catch-up that follows them.

A long outage loses whatever the cars sent meanwhile, just as a restart does, so
``async_handle_connection_event`` reports how long the stream was away when it
connects again. The first connect after setup has no outage behind it and must
report nothing (the startup catch-up covers it); a reconnect storm is one outage,
measured from its first drop.

``coordinator.py`` imports Home Assistant, so the method is compiled out of the
shipped source and run on a stand-in with a controllable clock.
"""

from __future__ import annotations

import __future__
import ast
import asyncio
import pathlib
import types
from datetime import datetime, timedelta, timezone
from typing import Optional

_COORDINATOR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "bavariandata"
    / "coordinator.py"
)
T0 = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


class _Clock:
    def __init__(self) -> None:
        self.now_value = T0

    def now(self, tz=None) -> datetime:
        return self.now_value


def _handler(clock: _Clock):
    tree = ast.parse(_COORDINATOR.read_text(encoding="utf-8"))
    cls = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CardataCoordinator"
    )
    method = next(
        n
        for n in cls.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "async_handle_connection_event"
    )
    code = compile(
        ast.Module(body=[method], type_ignores=[]),
        str(_COORDINATOR),
        "exec",
        flags=__future__.annotations.compiler_flag,
        dont_inherit=True,
    )
    namespace = {
        "datetime": types.SimpleNamespace(now=clock.now),
        "timezone": timezone,
        "Optional": Optional,
    }
    exec(code, namespace)  # noqa: S102 - trusted, shipped source
    return namespace["async_handle_connection_event"]


def _run(events: list[tuple[int, str]]) -> list[float]:
    """Feed (minute, status) events; return the outage lengths reported."""

    clock = _Clock()
    handle = _handler(clock)
    reported: list[float] = []
    coordinator = types.SimpleNamespace(
        connection_status="connecting",
        last_disconnect_reason=None,
        connection_history=[],
        _unauthorized_since=None,
        _stream_down_since=None,
        on_reconnect_after_outage=reported.append,
        _log_diagnostics=lambda: None,
        _evaluate_stream_repairs=lambda now=None: None,
    )

    async def scenario() -> None:
        for minute, status in events:
            clock.now_value = T0 + timedelta(minutes=minute)
            await handle(coordinator, status)

    asyncio.run(scenario())
    return reported


def test_the_first_connect_after_setup_is_not_an_outage():
    assert _run([(0, "connected")]) == []


def test_a_reconnect_reports_how_long_the_stream_was_away():
    assert _run([(0, "connected"), (10, "disconnected"), (40, "connected")]) == [30 * 60]


def test_a_reconnect_storm_is_one_outage_from_its_first_drop():
    events = [
        (0, "connected"),
        (10, "disconnected"),
        (20, "unauthorized"),
        (30, "disconnected"),
        (70, "connected"),
    ]
    assert _run(events) == [60 * 60]


def test_a_failed_first_connect_is_not_an_outage_either():
    """Setup that took a while to connect is the startup catch-up's business."""

    assert _run([(0, "disconnected"), (30, "connected")]) == []
