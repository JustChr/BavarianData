"""Everything the coordinator lets the outside world see, written down step by step.

The scenario snapshots pin a handful of columns (estimate, rate, sessions,
event names). That is enough to read a story, but not enough to refactor the
coordinator under: a moved line can change an event's payload, a dispatcher
signal, a stored trip's field or a getter the card reads, and no column would
notice. A trace pins all of it.

After every harness step the tracer writes what changed since the step before:

=========  ================================================================
``ev``     an event fired, with its full payload
``sig``    a dispatcher signal sent, with its arguments
``issue``  a repair raised (``+``) or cleared (``-``)
``store``  a stored section that changed (history per car and section,
           every other store key whole), as a digest
``timer``  the set of armed timers, as seconds from now
``get``    a public getter whose answer changed (every getter, every car)
``acct``   an account-wide value that changed (connection, open trips)
=========  ================================================================

Comparing one car with and without a twin skips ``store`` and ``final``: all
cars share one debounced store, so when a car's section is written depends on
the other car's saves by design. What a car's history *holds* is compared all
the same, through the ``history.*`` getters.

and when the harness closes, every store in full, so a digest that differs can
be read as a diff. Each line is tagged with the car it belongs to (``-`` for the
account), which is what lets ``CoordinatorHarness(twin=...)`` compare one car's
view with and without a second car in the account.

Tracing is off unless ``BAVARIANDATA_TRACE_DIR`` names a directory; it is
written by ``scripts/golden.py``, which is what compares two traces. Nothing in
here may change what the coordinator does: every probe is a read.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from .harness import CoordinatorHarness

TRACE_DIR_ENV = "BAVARIANDATA_TRACE_DIR"

# The coordinator's public per-car surface: every getter another module of the
# integration calls (entities, services, Assist, diagnostics, the bridge). Kept
# in step with ``tests/test_trace.py``, which fails when a public method is
# neither probed here nor named in ``NOT_PROBED``.
PER_CAR: dict[str, Callable[[Any, str], Any]] = {
    "get_soc_estimate": lambda c, v: c.get_soc_estimate(v),
    "get_soc_rate": lambda c, v: c.get_soc_rate(v),
    "get_charge_eta": lambda c, v: c.get_charge_eta(v),
    "get_lifetime_energy_kwh": lambda c, v: c.get_lifetime_energy_kwh(v),
    "get_session_energy_kwh": lambda c, v: c.get_session_energy_kwh(v),
    "get_session_start": lambda c, v: c.get_session_start(v),
    "soc_estimate_attributes": lambda c, v: c.soc_estimate_attributes(v),
    "learned_meter_rate": lambda c, v: c.learned_meter_rate(v),
    "has_open_session": lambda c, v: c.has_open_session(v),
    "charging_active": lambda c, v: c.charging_active(v),
    "gps_halves_pending": lambda c, v: sorted(c.gps_halves_pending(v)),
    "has_open_trip": lambda c, v: c.has_open_trip(v),
    "open_trip_progress": lambda c, v: c.open_trip_progress(v, include_track=True),
    "odometer_km": lambda c, v: c.odometer_km(v),
    "fuel_report": lambda c, v: c.fuel_report(v),
    "obfcm_lifetime": lambda c, v: c.obfcm_lifetime(v),
    "mileage_report": lambda c, v: c.mileage_report(v),
    "km_per_day": lambda c, v: c.km_per_day(v),
    "last_vehicle_report": lambda c, v: c.last_vehicle_report(v),
    "pricing_for": lambda c, v: c.pricing_for(v),
    "trip_settings_for": lambda c, v: c.trip_settings_for(v),
    "report_trips": lambda c, v: c.report_trips(v),
    "battery_nominal_kwh": lambda c, v: c.battery_nominal_kwh(v),
    "battery_capacity_kwh": lambda c, v: c.battery_capacity_kwh(v),
    "current_soc": lambda c, v: c.current_soc(v),
    "bmw_range_km": lambda c, v: c.bmw_range_km(v),
    "bridge_snapshot": lambda c, v: c.bridge_snapshot(v),
    "efficiency": lambda c, v: c.efficiency(v),
    "seen_descriptors": lambda c, v: sorted(c.seen_descriptors(v)),
    "drive_train": lambda c, v: c.drive_train(v),
    "is_combustion_only": lambda c, v: c.is_combustion_only(v),
    "is_plug_in_hybrid": lambda c, v: c.is_plug_in_hybrid(v),
    "descriptor_diagnostics": lambda c, v: c.descriptor_diagnostics(v),
    "data": lambda c, v: c.data.get(v),
    "names": lambda c, v: c.names.get(v),
    "device_metadata": lambda c, v: c.device_metadata.get(v),
    "tyre_diagnosis": lambda c, v: c.tyre_diagnosis.get(v),
    "last_message_by_vin": lambda c, v: c.last_message_by_vin.get(v),
    "descriptor_counts": lambda c, v: c.descriptor_counts.get(v),
    "car_profiles": lambda c, v: c.car_profiles.get(v),
    "mileage_allowances": lambda c, v: c.mileage_allowances.get(v),
    "in_unconfirmed_restored_charges": lambda c, v: v in c.unconfirmed_restored_charges(),
    # The history as it stands in memory, per car. The ``store`` lines say when
    # it reached disk, which one debounced store for every car makes depend on
    # the others' saves; these say what it holds.
    "history.sessions": lambda c, v: c.history and list(c.history.sessions(v)),
    "history.trips": lambda c, v: c.history and list(c.history.trips(v)),
    "history.open_session": lambda c, v: c.history and c.history.open_sessions().get(v),
    "history.last_report": lambda c, v: c.history and c.history.last_report(v),
    "history.odometer_log": lambda c, v: c.history and c.history.odometer_log(v),
    "in_open_trip_vins": lambda c, v: v in c.open_trip_vins(),
}

ACCOUNT: dict[str, Callable[[Any], Any]] = {
    "connection_status": lambda c: c.connection_status,
    "last_disconnect_reason": lambda c: c.last_disconnect_reason,
    "last_message_at": lambda c: c.last_message_at,
    "last_telematic_api_at": lambda c: c.last_telematic_api_at,
    "stream_started_at": lambda c: c.stream_started_at,
    "connection_history": lambda c: list(c.connection_history),
    "home_zone_name": lambda c: c.home_zone_name(),
    "open_trip_vins": lambda c: c.open_trip_vins(),
    "unconfirmed_restored_charges": lambda c: c.unconfirmed_restored_charges(),
    "display_units": lambda c: c.display_units,
}

# Public names that are not a per-step read: actions, signal names, wiring.
NOT_PROBED = frozenset(
    {
        "apply_basic_data",
        "apply_tyre_diagnosis",
        "async_clear_stream_repairs",
        "async_flush_charging",
        "async_flush_trips",
        "async_handle_connection_event",
        "async_handle_message",
        "async_restore_open_sessions",
        "async_start_watchdog",
        "async_stop_watchdog",
        "cars_charging_on",  # asked per meter, read through bridge_snapshot
        "iter_descriptors",
        "get_state",  # the ``data`` probe holds every state
        "mark_trip_start",
        "numeric",  # the ``data`` probe holds every value
        "restore_descriptor_state",
        "restore_lifetime_energy",
        "restore_session_energy",
        "restore_soc_cache",
        "restore_tyre_diagnosis",
        "stop_watching_phone_routes",
        "unconfirmed_restored_charges",  # per car above, whole on the account
        "watch_phone_routes",
        "watchdog_tick",
        "zone_at",  # a pure lookup of HA's zones
    }
)

_ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+")


def _plain(value: Any) -> Any:
    """``value`` as JSON-ready data, with a stable order everywhere."""

    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if value == value else "nan"
    if isinstance(value, enum.Enum):
        return f"{type(value).__name__}.{value.name}"
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, timedelta):
        return f"{value.total_seconds()}s"
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "__type__": type(value).__name__,
            **{f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)},
        }
    if isinstance(value, dict):
        return {str(_plain(k)): _plain(v) for k, v in value.items()}
    if isinstance(value, set | frozenset):
        return sorted((_plain(v) for v in value), key=repr)
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if hasattr(value, "__dict__") and not callable(value):
        return {
            "__type__": type(value).__name__,
            **{k: _plain(v) for k, v in vars(value).items()},
        }
    return _ADDRESS.sub("", repr(value))


def canonical(value: Any) -> str:
    return json.dumps(_plain(value), sort_keys=True, ensure_ascii=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()[:16]


def _read(probe: Callable[[], Any]) -> str:
    try:
        return canonical(probe())
    except Exception as err:  # noqa: BLE001 - a raising getter is part of the record
        return f"raises {type(err).__name__}: {err}"


def _store_sections(key: str, data: Any) -> dict[tuple[str, str], Any]:
    """A store split into (section, car) pieces, so each piece has an owner.

    The history store keeps every section per VIN; any other store (or a
    history section that isn't a per-VIN mapping) is one account-wide piece.
    """

    if key.endswith("_history") and isinstance(data, dict):
        pieces: dict[tuple[str, str], Any] = {}
        for section, content in data.items():
            if isinstance(content, dict) and section != "schema":
                for vin, part in content.items():
                    pieces[(f"{key}.{section}", vin)] = part
            else:
                pieces[(f"{key}.{section}", "-")] = content
        return pieces
    return {(key, "-"): data}


class Tracer:
    """Writes one harness's trace; owned by :class:`CoordinatorHarness`."""

    def __init__(self, harness: CoordinatorHarness, path: Path) -> None:
        self._h = harness
        self._path = path
        self._lines: list[str] = []
        self._events = 0
        self._signals = 0
        self._issues: dict[str, str] = {}
        self._store: dict[tuple[str, str], str] = {}
        self._timers: Optional[str] = None
        self._gets: dict[tuple[str, str], str] = {}
        self._steps = 0

    def _tag(self, vin: Optional[str]) -> str:
        return self._h.alias(vin) if vin else "-"

    def _emit(self, owner: str, kind: str, text: str) -> None:
        self._lines.append(f"  [{owner}] {kind} {text}")

    def step(self, label: str) -> None:
        h = self._h
        hass = h.hass
        self._steps += 1
        self._lines.append(f"#{self._steps} {h.minute:.4f} {label}")

        for name, data in hass.events[self._events :]:
            self._emit(self._tag(data.get("vin")), "ev", f"{name} {canonical(data)}")
        self._events = len(hass.events)

        prefix = f"bavariandata_{h.coordinator.entry_id}_"
        for signal, args in hass.signals[self._signals :]:
            owner = args[0] if args and isinstance(args[0], str) and args[0] in h.vins else None
            self._emit(self._tag(owner), "sig", f"{signal.removeprefix(prefix)} {canonical(args)}")
        self._signals = len(hass.signals)

        issues = {issue_id: canonical(data) for issue_id, data in hass.issues.items()}
        for issue_id in sorted(set(self._issues) | set(issues)):
            if issue_id not in issues:
                self._emit("-", "issue", f"-{issue_id}")
            elif self._issues.get(issue_id) != issues[issue_id]:
                self._emit("-", "issue", f"+{issue_id} {issues[issue_id]}")
        self._issues = issues

        store: dict[tuple[str, str], str] = {}
        for key, data in hass.storage.items():
            for piece, content in _store_sections(key, data).items():
                store[piece] = digest(content)
        for piece in sorted(set(self._store) | set(store)):
            section, vin = piece
            if store.get(piece) != self._store.get(piece):
                self._emit(
                    self._tag(None if vin == "-" else vin),
                    "store",
                    f"{section} {store.get(piece, 'gone')}",
                )
        self._store = store

        now = h.clock.now
        timers = canonical(
            sorted(round((t.due - now).total_seconds(), 3) for t in hass.timers if not t.cancelled)
        )
        if timers != self._timers:
            self._emit("-", "timer", timers)
            self._timers = timers

        coordinator = h.coordinator
        for vin in h.vins:
            for name, probe in PER_CAR.items():
                key = (vin, name)
                value = _read(lambda probe=probe, vin=vin: probe(coordinator, vin))
                if self._gets.get(key) != value:
                    self._emit(self._tag(vin), "get", f"{name}={value}")
                    self._gets[key] = value
        for name, probe in ACCOUNT.items():
            key = ("-", name)
            value = _read(lambda probe=probe: probe(coordinator))
            if self._gets.get(key) != value:
                self._emit("-", "acct", f"{name}={value}")
                self._gets[key] = value

    def close(self) -> None:
        self._lines.append("# final stores")
        for key, data in sorted(self._h.hass.storage.items()):
            for (section, vin), content in sorted(_store_sections(key, data).items()):
                self._emit(
                    self._tag(None if vin == "-" else vin),
                    "final",
                    f"{section} {canonical(content)}",
                )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as out:
            out.write("\n".join(self._lines) + "\n")


_counts: dict[str, int] = {}


def tracer_for(harness: CoordinatorHarness, path: Optional[Path] = None) -> Optional[Tracer]:
    """A tracer writing to ``path``, or under the running test's name when tracing is on."""

    if path is not None:
        tracer = Tracer(harness, path)
        tracer._lines.append("=== harness")
        return tracer
    root = os.environ.get(TRACE_DIR_ENV)
    if not root:
        return None
    test = os.environ.get("PYTEST_CURRENT_TEST", "unknown").rsplit(" (", 1)[0]
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", test)
    index = _counts.get(safe, 0)
    _counts[safe] = index + 1
    path = Path(root) / f"{safe}.trace"
    if index == 0 and path.exists():
        path.unlink()
    tracer = Tracer(harness, path)
    tracer._lines.append(f"=== harness {index}")
    return tracer
