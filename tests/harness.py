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

Two switches make the harness a refactoring gate (``scripts/golden.py``):

* ``BAVARIANDATA_TRACE_DIR`` writes a full trace of every step (``trace.py``):
  every event, signal, repair, store write, timer and public getter.
* ``BAVARIANDATA_TWIN=<minutes>`` (or ``twin=``) puts a second car in the
  account that receives a copy of everything the first car sends, that many
  minutes later. The first car's trace must not change: nothing one car does
  may leak into another's state.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import shutil
import sys
import tempfile
from itertools import pairwise
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pytest

from .fake_ha import Clock, FakeHass, clocked_datetime, install_fake_ha, load_with_fake_ha
from .trace import tracer_for

UTC = timezone.utc
VIN = "WBATEST0000000001"
# The second car of twin mode.
TWIN_VIN = "WBATWIN0000000099"
TWIN_ENV = "BAVARIANDATA_TWIN"
START = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)
WATCHDOG_S = 30

coordinator_mod = load_with_fake_ha("coordinator")
store_mod = load_with_fake_ha("history.store")
soc_mod = load_with_fake_ha("soc_tracking")
coverage_store_mod = load_with_fake_ha("coverage_store")
tyre_store_mod = load_with_fake_ha("tyre_store")

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
    "ac_volt": ("vehicle.drivetrain.electricEngine.charging.acVoltage", "V"),
    "ac_amp": ("vehicle.drivetrain.electricEngine.charging.acAmpere", "A"),
    "phases": ("vehicle.drivetrain.electricEngine.charging.phaseNumber", None),
    "basic": ("vehicle.vehicleIdentification.basicVehicleData", None),
}

# Modules whose ``datetime`` must follow the virtual clock.


def _clocked_modules() -> list:
    """Every loaded integration module that reads ``datetime.now``.

    Found rather than listed: a module the coordinator's code moves into must
    follow the virtual clock as well, or its readings depend on the day the
    suite runs -- what a fixed list let happen to ``dt_util.utcnow()``.
    """

    prefix = "custom_components.bavariandata."
    return [
        module
        for name, module in sorted(sys.modules.items())
        if name.startswith(prefix) and getattr(module, "datetime", None) is datetime
    ]

# Every harness a test made, so the ``conftest`` fixture can close the ones a
# test leaves open (their trace is written on close, their patches undone).
OPEN_HARNESSES: list["CoordinatorHarness"] = []


def _twin_offset(twin: Optional[float]) -> Optional[float]:
    if twin is not None:
        return twin
    value = os.environ.get(TWIN_ENV)
    return float(value) if value else None


def _shift_stamps(data: dict[str, Any], minutes: float) -> dict[str, Any]:
    """A batch's descriptors with every BMW timestamp moved ``minutes`` later."""

    shifted = copy.deepcopy(data)
    for item in shifted.values():
        if not isinstance(item, dict) or not isinstance(item.get("timestamp"), str):
            continue
        try:
            stamp = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
        except ValueError:
            continue
        if stamp.tzinfo is None:
            continue
        moved = (stamp + timedelta(minutes=minutes)).astimezone(UTC)
        item["timestamp"] = moved.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return shifted


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
        wallbox_meter: Optional[str] = None,
        pricing: Optional[dict[str, Any]] = None,
        trip_debug: bool = False,
        twin: Optional[float] = None,
        trace: Optional[Path] = None,
    ) -> None:
        self.vin = vin
        # Twin mode: a second car, sent a copy of every batch ``twin`` minutes
        # later (``_queue_mirror``). ``None`` keeps the account to one car.
        self.twin_offset = _twin_offset(twin)
        self.twin_vin = TWIN_VIN if self.twin_offset is not None and vin != TWIN_VIN else None
        if self.twin_vin is None:
            self.twin_offset = None
        self._mirror: list[tuple[datetime, dict[str, Any]]] = []
        # A bound wallbox energy meter (the ``grid_energy_entity`` option);
        # move it with :meth:`meter`.
        self.wallbox_meter = wallbox_meter
        # Further ``PricingConfig`` options (a price entity, a tariff), kept
        # across restarts as the entry's options are.
        self.pricing_options = dict(pricing or {})
        # Trip-capture mode, which appends every batch to a file in HA's config
        # directory -- a scratch directory here, removed on close.
        self.trip_debug = trip_debug
        self._config_dir: Optional[str] = (
            tempfile.mkdtemp(prefix="harness-") if trip_debug else None
        )
        # Each car's estimate entity's last written ``meter_reading_kwh``
        # attribute -- what HA hands back with the restored state.
        self._meter_attribute: dict[str, Optional[float]] = {}
        self.clock = Clock(start)
        self.start = start
        self.hass = FakeHass(self.clock)
        if self._config_dir is not None:
            self.hass.config.config_dir = self._config_dir
        # One event loop for the harness's life, as HA has. A loop per call
        # (``asyncio.run``) opens a socket pair each time on Windows; the
        # fuzzer's thousands exhausted the ephemeral ports and hung the suite.
        self._loop = asyncio.new_event_loop()
        self._patch = pytest.MonkeyPatch()
        install_fake_ha(self._patch)
        clocked = clocked_datetime(self.clock)
        for module in _clocked_modules():
            self._patch.setattr(module, "datetime", clocked)
            # ``dt_util.utcnow()`` too: the stub's own reads the wall clock,
            # which made a live trip's duration depend on the day the suite ran.
            dt_util = getattr(module, "dt_util", None)
            if dt_util is not None:
                self._patch.setattr(dt_util, "utcnow", lambda: self.clock.now)
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
        # When each car's derived value last changed -- what HA's
        # ``last_changed`` would say, and what the entity restore hands back
        # after a restart. Keyed ``(vin, "estimate" | "rate")``.
        self._derived_changed: dict[tuple[str, str], tuple[Any, datetime]] = {}
        self._closed = False
        # What the coordinator's two REST callbacks were called with.
        self.new_vehicles: list[str] = []
        self.outages: list[float] = []
        self.coordinator = self._boot()
        # ``trace`` writes this harness's trace there, whatever the environment says.
        self._tracer = tracer_for(self, trace)
        OPEN_HARNESSES.append(self)
        if home is not None:
            # What the device tracker hands back at startup on a real install:
            # the last known position, so the first live fix has a mate.
            stamp = start.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            for car in filter(None, (vin, self.twin_vin)):
                for short, value in zip(("lat", "lon"), home, strict=True):
                    self.coordinator.restore_descriptor_state(
                        car, DESCRIPTORS[short][0], value, None, stamp
                    )
        if capacity_kwh is not None:
            self.send(0, capacity=capacity_kwh, step="capacity")

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self in OPEN_HARNESSES:
            OPEN_HARNESSES.remove(self)
        if self._tracer is not None:
            self._tracer.close()
        logging.getLogger("custom_components.bavariandata").removeHandler(self._log_handler)
        self._patch.undo()
        if not self._loop.is_closed():
            self._loop.close()
        if self._config_dir is not None:
            shutil.rmtree(self._config_dir, ignore_errors=True)

    def __enter__(self) -> "CoordinatorHarness":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _boot(self, *, remember_locations: bool = False):
        """Set the entry up the way ``async_setup_entry`` does, in its order.

        Stores first (history, descriptor coverage, tyres), then the
        coordinator, the tyre seed, the one-time history repairs, the callbacks
        and the pricing, and only then the open charges -- after the tariff,
        before any entity restores. ``remember_locations`` is the option of the
        same name, which outlives a restart.
        """

        history = None
        if self.history_enabled:
            history = store_mod.HistoryStore(
                self.hass, "entry", remember_locations=remember_locations
            )
            self._run(history.async_load())
        coverage = coverage_store_mod.CoverageStore(self.hass, "entry", sections=[])
        self._run(coverage.async_load())
        tyre_store = tyre_store_mod.TyreStore(self.hass, "entry")
        stored_tyre = self._run(tyre_store.async_load())

        coordinator = coordinator_mod.CardataCoordinator(hass=self.hass, entry_id="entry")
        coordinator.history = history
        coordinator.tyre_store = tyre_store
        coordinator.restore_tyre_diagnosis(stored_tyre)
        if history is not None:
            history.reresolve_zones(coordinator.zone_at)
            history.drop_house_figures_elsewhere()
            history.flag_unbilled_meter_energy()
            if not history.remember_locations:
                history.forget_locations()
        coordinator.coverage = coverage
        coordinator.trip_debug = self.trip_debug
        # What ``__init__.py`` hangs here fetches over REST; the harness notes
        # the calls, so a scenario can say a new car or an outage was noticed.
        coordinator.on_new_vehicle = self.new_vehicles.append
        coordinator.on_reconnect_after_outage = self.outages.append
        if self.wallbox_meter is not None or self.pricing_options:
            coordinator.pricing = coordinator_mod.PricingConfig(
                grid_energy_entity=self.wallbox_meter, **self.pricing_options
            )
        if self.twin_vin is not None:
            # The twin charges somewhere else: two cars on one meter is a real
            # interaction (the meter's advance goes to the charging one), not
            # a leak, and would show up in the first car's figures.
            coordinator.vehicle_pricing[self.twin_vin] = coordinator_mod.PricingConfig()
        if history is not None:
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
            horizon = min(next_tick, target)
            if self._mirror and self._mirror[0][0] <= horizon:
                due = self._mirror[0][0]
                if not any(not t.cancelled and t.due < due for t in self.hass.timers):
                    _, message = self._mirror.pop(0)
                    self.clock.now = max(self.clock.now, due)
                    self._run(self.coordinator.async_handle_message(message))
                    self._note_derived()
                    continue
            timers = self.hass.due_timers(horizon)
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
            self.coordinator.watchdog_tick()
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
        rest: bool = False,
        **values: Any,
    ) -> None:
        """Deliver one batch, as the stream (or a REST fetch) would.

        ``stamped`` is the minute BMW's timestamp says, when it differs from the
        arrival -- a REST catch-up hands back the car's last message, hours old.
        ``rest`` tags the batch the way ``__init__.py`` tags a REST fetch's.
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
        message: dict[str, Any] = {"vin": self.vin, "data": data}
        if rest:
            message["source"] = "rest"
        self._run(self.coordinator.async_handle_message(message))
        self._queue_mirror(message)
        self._note_derived()
        label = step or "send " + ",".join(f"{k}={v}" for k, v in values.items())
        self._observe(label)

    def fix(self, minute: float, lat: float, lon: float, **values: Any) -> None:
        """One GPS fix, as BMW sends it: latitude and longitude ~1 s apart."""

        self.send(minute, stamped=minute, lat=lat, step=f"fix {lat:.4f},{lon:.4f}", **values)
        # Both halves carry the fix's own timestamp; only the arrival differs.
        self.send(minute + 1 / 60, stamped=minute, lon=lon, step="  (longitude)")

    def meter(self, minute: Optional[float], kwh: Optional[float], *, unit: str = "kWh") -> None:
        """Set the wallbox meter's running total (``None`` = unavailable)."""

        if self.wallbox_meter is None:
            raise ValueError("no wallbox meter bound")
        if minute is not None and self.at(minute) > self.clock.now:
            self.advance_to(minute, step=f"wait {minute:g}")
        state = "unavailable" if kwh is None else kwh
        self.hass.set_state(self.wallbox_meter, state, unit_of_measurement=unit)
        self._observe(f"meter {state}")

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
            message = {"vin": record.get("vin", self.vin), "data": record["data"]}
            self._run(self.coordinator.async_handle_message(message))
            self._queue_mirror(message)
            self._note_derived()
            self._observe(
                "replay " + ",".join(sorted(k.rsplit(".", 1)[-1] for k in record["data"]))
            )

    def _queue_mirror(self, message: dict[str, Any]) -> None:
        """Hand the twin the same batch, ``twin_offset`` minutes from now."""

        if self.twin_vin is None or message.get("vin") != self.vin:
            return
        twin = {
            **message,
            "vin": self.twin_vin,
            "data": _shift_stamps(message["data"], self.twin_offset),
        }
        due = self.clock.now + timedelta(minutes=self.twin_offset)
        self._mirror.append((due, twin))
        self._mirror.sort(key=lambda item: item[0])

    def tyres(self, payload: Any, *, minute: Optional[float] = None) -> None:
        """A tyre diagnosis fetched over REST (the daily poll), for every car."""

        if minute is not None and self.at(minute) > self.clock.now:
            self.advance_to(minute, step=f"wait {minute:g}")
        for vin in filter(None, (self.vin, self.twin_vin)):
            self.coordinator.apply_tyre_diagnosis(vin, payload)
        self._observe("tyres")

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
            vin: {
                descriptor: (state.value, state.unit, state.timestamp)
                for descriptor, state in (old.data.get(vin) or {}).items()
            }
            for vin in self.vins
        }
        derived = dict(self._derived_changed)
        meter_attribute = dict(self._meter_attribute)
        # What the two energy sensors show, and the session's ``last_reset``.
        energy = {
            vin: (
                old.get_lifetime_energy_kwh(vin),
                old.get_session_energy_kwh(vin),
                old.get_session_start(vin),
            )
            for vin in self.vins
        }
        # Nothing of the old instance survives but storage -- its timers die too.
        for timer in self.hass.timers:
            timer.cancelled = True
        self.clock.advance(downtime_s)
        self._last_tick = self.clock.now
        self.coordinator = self._boot(
            remember_locations=old.history is not None and old.history.remember_locations
        )

        def restore_descriptors() -> None:
            for vin, states in restored_descriptors.items():
                for descriptor, (value, unit, timestamp) in states.items():
                    if isinstance(value, str) and value.isupper():
                        value = value.lower()  # the sensor's enum slug
                    self.coordinator.restore_descriptor_state(
                        vin, descriptor, value, unit, timestamp
                    )

        def restore_derived() -> None:
            for vin in restored_descriptors:
                if (vin, "estimate") in derived:
                    value, changed = derived[(vin, "estimate")]
                    if value is not None:
                        self.coordinator.restore_soc_cache(
                            vin,
                            estimate=value,
                            timestamp=changed,
                            meter_kwh=meter_attribute.get(vin),
                        )
                if (vin, "rate") in derived:
                    value, changed = derived[(vin, "rate")]
                    if value is not None and self.coordinator.get_soc_rate(vin) is None:
                        self.coordinator.restore_soc_cache(vin, rate=value, timestamp=changed)
                # The energy sensors, as ``sensor.py`` restores them: only into
                # a counter the open-session restore left empty.
                lifetime, session, started = energy.get(vin, (None, None, None))
                if lifetime is not None and self.coordinator.get_lifetime_energy_kwh(vin) is None:
                    self.coordinator.restore_lifetime_energy(vin, lifetime)
                if session is not None and self.coordinator.get_session_energy_kwh(vin) is None:
                    self.coordinator.restore_session_energy(vin, session, started)

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
    @property
    def vins(self) -> list[str]:
        """Every car in the account: the first, its twin, then any a test sent."""

        cars = [self.vin] + ([self.twin_vin] if self.twin_vin else [])
        return cars + sorted(set(self.coordinator.data) - set(cars))

    def alias(self, vin: str) -> str:
        """How a trace names a car: ``A`` the first, ``B`` the twin."""

        return {self.vin: "A", self.twin_vin: "B"}.get(vin, vin)

    def _mine(self, data: dict) -> bool:
        """True for an event of the first car's (or of the account)."""

        return data.get("vin") in (None, self.vin)

    def estimate(self) -> Optional[float]:
        return self.coordinator.get_soc_estimate(self.vin)

    def rate(self) -> Optional[float]:
        return self.coordinator.get_soc_rate(self.vin)

    def sessions(self) -> list:
        if self.coordinator.history is None:
            return []
        return list(self.coordinator.history.sessions(self.vin))

    def has_open_session(self) -> bool:
        return self.coordinator.has_open_session(self.vin)

    def events(self, prefix: str = "bavariandata_charging") -> list[tuple[str, dict]]:
        return [
            (name, data)
            for name, data in self.hass.events
            if name.startswith(prefix) and self._mine(data)
        ]

    def _note_derived(self) -> None:
        coordinator = self.coordinator
        for vin in self.vins:
            for key, value in (
                ("estimate", coordinator.get_soc_estimate(vin)),
                ("rate", coordinator.get_soc_rate(vin)),
            ):
                previous = self._derived_changed.get((vin, key))
                if previous is None or previous[0] != value:
                    self._derived_changed[(vin, key)] = (value, self.clock.now)
            self._meter_attribute[vin] = coordinator.soc_estimate_attributes(vin).get(
                "meter_reading_kwh"
            )

    def _observe(self, step: str) -> None:
        new_events = [
            name for name, data in self.hass.events[self._event_mark :] if self._mine(data)
        ]
        self._event_mark = len(self.hass.events)
        row = Row(
            minute=round(self.minute, 2),
            step=step,
            estimate=self.estimate(),
            rate=self.rate(),
            charging=self.coordinator.charging_active(self.vin),
            open_session=self.has_open_session(),
            sessions=len(self.sessions()),
            events=[name.removeprefix("bavariandata_") for name in new_events],
        )
        self.timeline.append(row)
        check_invariants(self, row)
        if self._tracer is not None:
            self._tracer.step(step)

    def render(self) -> str:
        """The timeline as text, for snapshots and failure messages."""

        return "\n".join(row.as_line() for row in self.timeline)


class _ErrorCollector(logging.Handler):
    def __init__(self, sink: list[logging.LogRecord]) -> None:
        super().__init__(level=logging.ERROR)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self._sink.append(record)


class _Timeline:
    """The timeline, rendered only when a failure message is actually built.

    Rendering it on every step to have it ready made a long scenario quadratic.
    """

    def __init__(self, h: CoordinatorHarness) -> None:
        self._h = h

    def __format__(self, spec: str) -> str:
        return "\n" + self._h.render()

    def __str__(self) -> str:
        return format(self)


def check_invariants(h: CoordinatorHarness, row: Row) -> None:
    """What must hold after every single step, in every scenario."""

    context = _Timeline(h)
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
    history = h.coordinator.history
    for vin in h.vins if history is not None else ():
        _check_car(h, list(history.sessions(vin)), list(history.trips(vin)), context)


def _check_car(h: CoordinatorHarness, sessions: list, trips: list, context: Any) -> None:
    for session in sessions:
        assert session.end is None or session.end >= session.start, (
            f"session ends before start{context}"
        )
        if session.energy_kwh is not None:
            assert session.energy_kwh >= 0, f"negative session energy{context}"
    trips = sorted(trips, key=lambda t: t.start)
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
