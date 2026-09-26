"""Drive the real coordinator through a scripted day, restarts included.

The coordinator is where the integration's behaviour lives -- charging
sessions, the SoC estimate, charge events, trips -- and until this harness it
had no tests at all, because it imports Home Assistant (see ``fake_ha.py``).
Bugs there were found on the maintainer's car instead: the SoC estimate that
froze after every restart mid-charge (fixed in 0.9.12) lived in the seam
between the entity restore and the coordinator, which is exactly what
:meth:`CoordinatorHarness.restart` replays.

A test reads like the timeline it checks::

    h = CoordinatorHarness()
    h.send(0, soc=38, status="CHARGINGACTIVE", power=3480)
    h.advance_to(17)          # minutes; timers and the watchdog tick on the way
    h.restart()               # flush on stop, reload, restore -- like HA does
    h.advance_to(135)
    assert h.estimate() > 45

Every step appends a row to ``h.timeline`` (the estimate, the rate, whether a
charge is on record, the events fired), which is what the scenario snapshots
pin and what :func:`check_invariants` inspects after each step.
"""

from __future__ import annotations

import asyncio
import logging
from itertools import pairwise
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest

from .fake_ha import Clock, FakeHass, clocked_datetime, install_fake_ha, load_with_fake_ha

UTC = timezone.utc
VIN = "WBATEST0000000001"
START = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)
WATCHDOG_S = 30

coordinator_mod = load_with_fake_ha("coordinator")
store_mod = load_with_fake_ha("history.store")
soc_mod = load_with_fake_ha("soc_tracking")

# Short names for the descriptors a scenario touches, so a timeline stays
# readable. Anything else can be passed by its full path via ``extra``.
DESCRIPTORS = {
    "soc": ("vehicle.drivetrain.batteryManagement.header", "%"),
    "capacity": ("vehicle.drivetrain.batteryManagement.maxEnergy", "kWh"),
    "status": ("vehicle.drivetrain.electricEngine.charging.status", None),
    "power": ("vehicle.powertrain.electric.battery.charging.power", "W"),
    "target": ("vehicle.powertrain.electric.battery.stateOfCharge.target", "%"),
    "plug": ("vehicle.body.chargingPort.status", None),
    "moving": ("vehicle.isMoving", None),
    "ignition": ("vehicle.drivetrain.engine.isIgnitionOn", None),
    "door": ("vehicle.cabin.door.row1.driver.isOpen", None),
    "odometer": ("vehicle.vehicle.travelledDistance", "km"),
    "lat": ("vehicle.cabin.infotainment.navigation.currentLocation.latitude", "degrees"),
    "lon": ("vehicle.cabin.infotainment.navigation.currentLocation.longitude", "degrees"),
}

# Modules whose ``datetime`` must follow the virtual clock.
_CLOCKED = (coordinator_mod, store_mod, soc_mod)


@dataclass
class Row:
    """One observation, taken after every step."""

    minute: float
    step: str
    estimate: Optional[float]
    rate: Optional[float]
    charging: Optional[bool]
    open_session: bool
    sessions: int
    events: list[str] = field(default_factory=list)

    def as_line(self) -> str:
        est = "-" if self.estimate is None else f"{self.estimate:.2f}"
        rate = "-" if self.rate is None else f"{self.rate:.2f}"
        charging = {None: "?", True: "on", False: "off"}[self.charging]
        events = (" " + ",".join(self.events)) if self.events else ""
        return (
            f"{self.minute:7.1f}  {self.step:<24} est={est:>6} rate={rate:>5} "
            f"charging={charging:<3} open={'y' if self.open_session else 'n'} "
            f"sessions={self.sessions}{events}"
        )


class CoordinatorHarness:
    """The real ``CardataCoordinator`` on a virtual clock, with restarts."""

    def __init__(
        self,
        *,
        vin: str = VIN,
        capacity_kwh: Optional[float] = 75.0,
        history: bool = True,
        start: datetime = START,
        home: Optional[tuple[float, float]] = None,
    ) -> None:
        self.vin = vin
        self.clock = Clock(start)
        self.start = start
        self.hass = FakeHass(self.clock)
        # One event loop for the harness's life, as HA has. A loop per call
        # (``asyncio.run``) opens a socket pair each time on Windows; the
        # fuzzer's thousands exhausted the ephemeral ports and hung the suite.
        self._loop = asyncio.new_event_loop()
        self._patch = pytest.MonkeyPatch()
        install_fake_ha(self._patch)
        clocked = clocked_datetime(self.clock)
        for module in _CLOCKED:
            self._patch.setattr(module, "datetime", clocked)
        self.history_enabled = history
        # The integration swallows exceptions wherever one must not break the
        # stream (``except Exception: _LOGGER.exception(...)``). Right in
        # production, a blind spot in a test: collect them and fail the step.
        self.logged_errors: list[logging.LogRecord] = []
        self._log_handler = _ErrorCollector(self.logged_errors)
        logging.getLogger("custom_components.bavariandata").addHandler(self._log_handler)
        self.timeline: list[Row] = []
        self._event_mark = 0
        self._last_tick = self.clock.now
        # When each derived value last changed -- what HA's ``last_changed``
        # would say, and what the entity restore hands back after a restart.
        self._derived_changed: dict[str, tuple[Any, datetime]] = {}
        self.coordinator = self._boot()
        if home is not None:
            # What the device tracker hands back at startup on a real install:
            # the last known position, so the first live fix has a mate.
            stamp = start.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            for short, value in zip(("lat", "lon"), home, strict=True):
                self.coordinator.restore_descriptor_state(
                    vin, DESCRIPTORS[short][0], value, None, stamp
                )
        if capacity_kwh is not None:
            self.send(0, capacity=capacity_kwh, step="capacity")

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        logging.getLogger("custom_components.bavariandata").removeHandler(self._log_handler)
        self._patch.undo()
        if not self._loop.is_closed():
            self._loop.close()

    def __enter__(self) -> "CoordinatorHarness":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _boot(self):
        coordinator = coordinator_mod.CardataCoordinator(hass=self.hass, entry_id="entry")
        if self.history_enabled:
            store = store_mod.HistoryStore(self.hass, "entry")
            self._run(store.async_load())
            coordinator.history = store
            # Setup order in ``async_setup_entry``: history, then open sessions,
            # and only then the platforms restore their entities.
            coordinator.async_restore_open_sessions()
        return coordinator

    def _run(self, coro):
        result = self._loop.run_until_complete(coro)
        self._drain_tasks()
        return result

    def _drain_tasks(self) -> None:
        while self.hass.tasks:
            self._loop.run_until_complete(self.hass.tasks.pop(0))

    # -- time --------------------------------------------------------------
    @property
    def minute(self) -> float:
        return (self.clock.now - self.start).total_seconds() / 60

    def at(self, minute: float) -> datetime:
        return self.start + timedelta(minutes=minute)

    def advance_to(self, minute: float, *, step: Optional[str] = None) -> None:
        """Move the clock forward, firing timers and watchdog ticks in order."""

        target = self.at(minute)
        if target < self.clock.now:
            raise ValueError(f"time runs forward: {minute} < {self.minute}")
        while True:
            next_tick = self._last_tick + timedelta(seconds=WATCHDOG_S)
            timers = self.hass.due_timers(min(next_tick, target))
            if timers:
                timer = timers[0]
                # Put back the ones after the first; they fire in turn.
                for later in timers[1:]:
                    later.cancelled = False
                self.clock.now = timer.due
                timer.callback(timer.due)
                self._drain_tasks()
                continue
            if next_tick > target:
                break
            self.clock.now = next_tick
            self._last_tick = next_tick
            self.coordinator._log_diagnostics()
            self._note_derived()
        self.clock.now = target
        self._observe(step or f"advance {minute:g}")

    # -- input -------------------------------------------------------------
    def send(
        self,
        minute: Optional[float] = None,
        *,
        stamped: Optional[float] = None,
        step: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
        **values: Any,
    ) -> None:
        """Deliver one batch, as the stream (or a REST fetch) would.

        ``stamped`` is the minute BMW's timestamp says, when it differs from the
        arrival -- a REST catch-up hands back the car's last message, hours old.
        """

        if minute is not None and self.at(minute) > self.clock.now:
            self.advance_to(minute, step=f"wait {minute:g}")
        stamp = self.at(stamped if stamped is not None else self.minute)
        stamp_text = stamp.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        data: dict[str, Any] = {}
        for short, value in values.items():
            path, unit = DESCRIPTORS[short]
            data[path] = {"value": value, "unit": unit, "timestamp": stamp_text}
        for path, value in (extra or {}).items():
            data[path] = {"value": value, "timestamp": stamp_text}
        self._run(self.coordinator.async_handle_message({"vin": self.vin, "data": data}))
        self._note_derived()
        label = step or "send " + ",".join(f"{k}={v}" for k, v in values.items())
        self._observe(label)

    def fix(self, minute: float, lat: float, lon: float, **values: Any) -> None:
        """One GPS fix, as BMW sends it: latitude and longitude ~1 s apart."""

        self.send(minute, stamped=minute, lat=lat, step=f"fix {lat:.4f},{lon:.4f}", **values)
        # Both halves carry the fix's own timestamp; only the arrival differs.
        self.send(minute + 1 / 60, stamped=minute, lon=lon, step="  (longitude)")

    def trips(self) -> list:
        if self.coordinator.history is None:
            return []
        return list(self.coordinator.history.trips(self.vin))

    def replay(self, records: list[dict[str, Any]]) -> None:
        """Feed captured batches (``bavariandata_trip_capture.ndjson``) at their times.

        Each record is ``{"at": iso, "vin": ..., "data": {descriptor: {...}}}`` --
        BMW's batch exactly as it arrived -- so a real drive or charge becomes a
        regression test. Anonymize it first (``tools/anonymize_capture.py``):
        this repository is public.
        """

        for record in records:
            at = datetime.fromisoformat(record["at"])
            minute = (at - self.start).total_seconds() / 60
            if minute > self.minute:
                self.advance_to(minute, step="wait")
            self._run(
                self.coordinator.async_handle_message(
                    {"vin": record.get("vin", self.vin), "data": record["data"]}
                )
            )
            self._note_derived()
            self._observe(
                "replay " + ",".join(sorted(k.rsplit(".", 1)[-1] for k in record["data"]))
            )

    def connection(self, status: str) -> None:
        self._run(self.coordinator.async_handle_connection_event(status))
        self._observe(f"stream {status}")

    # -- restart -----------------------------------------------------------
    def restart(
        self,
        *,
        downtime_s: float = 60,
        order: str = "descriptors-first",
    ) -> None:
        """Stop and start Home Assistant the way it really happens.

        On stop HA does not unload the entry: it fires the stop event, whose
        listener flushes open trips and charges (``_flush_on_stop``). On start
        the entry is set up afresh -- history loaded, open sessions restored --
        and then every entity hands its last state back to the coordinator. The
        entity restore is mirrored from ``sensor.py``: a string enum comes back
        as its lowercase slug, which is how the 0.9.12 bug got in, and the
        entities restore in no fixed order, hence ``order``.
        """

        old = self.coordinator
        self._run(old.async_flush_trips())
        old.async_flush_charging()
        restored_descriptors = {
            descriptor: (state.value, state.unit, state.timestamp)
            for descriptor, state in (old.data.get(self.vin) or {}).items()
        }
        derived = dict(self._derived_changed)
        # Nothing of the old instance survives but storage -- its timers die too.
        for timer in self.hass.timers:
            timer.cancelled = True
        self.clock.advance(downtime_s)
        self._last_tick = self.clock.now
        self.coordinator = self._boot()

        def restore_descriptors() -> None:
            for descriptor, (value, unit, timestamp) in restored_descriptors.items():
                if isinstance(value, str) and value.isupper():
                    value = value.lower()  # the sensor's enum slug
                self.coordinator.restore_descriptor_state(
                    self.vin, descriptor, value, unit, timestamp
                )

        def restore_derived() -> None:
            if "estimate" in derived:
                value, changed = derived["estimate"]
                if value is not None:
                    self.coordinator.restore_soc_cache(self.vin, estimate=value, timestamp=changed)
            if "rate" in derived:
                value, changed = derived["rate"]
                if value is not None and self.coordinator.get_soc_rate(self.vin) is None:
                    self.coordinator.restore_soc_cache(self.vin, rate=value, timestamp=changed)

        if order == "descriptors-first":
            restore_descriptors()
            restore_derived()
        elif order == "derived-first":
            restore_derived()
            restore_descriptors()
        else:
            raise ValueError(order)
        self._observe(f"restart ({order})")

    # -- reading -----------------------------------------------------------
    def estimate(self) -> Optional[float]:
        return self.coordinator.get_soc_estimate(self.vin)

    def rate(self) -> Optional[float]:
        return self.coordinator.get_soc_rate(self.vin)

    def sessions(self) -> list:
        if self.coordinator.history is None:
            return []
        return list(self.coordinator.history.sessions(self.vin))

    def has_open_session(self) -> bool:
        return self.vin in self.coordinator._session_builders

    def events(self, prefix: str = "bavariandata_charging") -> list[tuple[str, dict]]:
        return [(name, data) for name, data in self.hass.events if name.startswith(prefix)]

    def _note_derived(self) -> None:
        for key, value in (("estimate", self.estimate()), ("rate", self.rate())):
            previous = self._derived_changed.get(key)
            if previous is None or previous[0] != value:
                self._derived_changed[key] = (value, self.clock.now)

    def _observe(self, step: str) -> None:
        tracking = self.coordinator._soc_tracking.get(self.vin)
        new_events = self.hass.event_types()[self._event_mark :]
        self._event_mark = len(self.hass.events)
        row = Row(
            minute=round(self.minute, 2),
            step=step,
            estimate=self.estimate(),
            rate=self.rate(),
            charging=None if tracking is None else tracking.charging_active,
            open_session=self.has_open_session(),
            sessions=len(self.sessions()),
            events=[name.removeprefix("bavariandata_") for name in new_events],
        )
        self.timeline.append(row)
        check_invariants(self, row)

    def render(self) -> str:
        """The timeline as text, for snapshots and failure messages."""

        return "\n".join(row.as_line() for row in self.timeline)


class _ErrorCollector(logging.Handler):
    def __init__(self, sink: list[logging.LogRecord]) -> None:
        super().__init__(level=logging.ERROR)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self._sink.append(record)


def check_invariants(h: CoordinatorHarness, row: Row) -> None:
    """What must hold after every single step, in every scenario."""

    context = f"\n{h.render()}"
    if h.logged_errors:
        record = h.logged_errors[0]
        detail = record.getMessage()
        if record.exc_info:
            detail += "\n" + logging.Formatter().formatException(record.exc_info)
        raise AssertionError(f"the integration logged an error at '{row.step}': {detail}{context}")
    if row.estimate is not None:
        assert 0.0 <= row.estimate <= 100.0, f"estimate out of range{context}"
    if row.rate is not None:
        assert row.rate > 0, f"a rate is only shown while charging{context}"
    sessions = h.sessions()
    for session in sessions:
        assert session.end is None or session.end >= session.start, (
            f"session ends before start{context}"
        )
        if session.energy_kwh is not None:
            assert session.energy_kwh >= 0, f"negative session energy{context}"
    trips = sorted(h.trips(), key=lambda t: t.start)
    for trip in trips:
        assert trip.end is None or trip.end >= trip.start, f"trip ends before start{context}"
        if trip.distance_km is not None:
            assert trip.distance_km >= 0, f"negative trip distance{context}"
    for earlier, later in pairwise(trips):
        if earlier.end is not None:
            assert earlier.end <= later.start, f"overlapping trips{context}"
    ordered = sorted(sessions, key=lambda s: s.start)
    for earlier, later in pairwise(ordered):
        if earlier.end is not None:
            assert earlier.end <= later.start, f"overlapping sessions{context}"
