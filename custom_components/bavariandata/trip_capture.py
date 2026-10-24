"""Trip-capture mode: the trip detector's own log and the raw-batch file.

Trip detection is the one thing no unit test here can settle -- which fields a
car streams, in what order, how far apart -- so it can be watched on a real
drive. With *Trip capture* on (``OPTION_TRIP_DEBUG``) every batch is logged
compactly and appended whole to an NDJSON file in the config directory, which
``tests/replays`` turns into regression tests (anonymized first).

:class:`TripLog` routes the detector's lines: to their own logger at INFO in
capture mode, else the decision lines (never the heavy "substrate" ones) under
the generic debug log. Nothing here imports Home Assistant; the coordinator
hands it the batch and writes the file through HA's executor.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

from .debug import debug_enabled, mask_vin

_LOGGER = logging.getLogger("custom_components.bavariandata.coordinator")

# Curated candidate trip-lifecycle / motion signals, surfaced on a ``[trip.watch]``
# line in trip-capture mode. NONE is used for detection today -- the capture
# exists to learn which (if any) the car actually streams and which cleanly
# bracket a drive, so a future detector can lean on something sturdier than GPS
# jitter + a 5-minute debounce. Chosen from the catalogue as the fields most
# likely to mark "driving": a speed, the HV system / plug state, the ignition
# trio (to confirm on a real drive they truly stay silent on this car), the
# driver door + central lock (entry/exit brackets), and active-navigation hints.
TRIP_WATCH_DESCRIPTORS = (
    "vehicle.isMoving",
    "vehicle.drivetrain.engine.isIgnitionOn",
    "vehicle.drivetrain.engine.isActive",
    "vehicle.vehicle.avgSpeed",
    "vehicle.vehicle.speedRange.lowerBound",
    "vehicle.vehicle.speedRange.upperBound",
    "vehicle.drivetrain.electricEngine.charging.hvStatus",
    "vehicle.drivetrain.electricEngine.charging.connectorStatus",
    "vehicle.cabin.door.row1.driver.isOpen",
    # Central lock, both spellings of it: door.lock.status is the documented one
    # but is not streamable (it only refreshes on a REST fetch, so it is silent
    # for days at a time), while door.status carries the same lock vocabulary and
    # arrives within seconds of every lock/unlock -- the sturdier entry/exit
    # bracket of the two (issue #8).
    "vehicle.cabin.door.status",
    "vehicle.cabin.door.lock.status",
    "vehicle.cabin.infotainment.navigation.currentLocation.altitude",
    "vehicle.cabin.infotainment.navigation.destinationSet.distance",
    "vehicle.cabin.infotainment.navigation.remainingRange",
)

# --- Trip-capture diagnostics (OPTION_TRIP_DEBUG) --------------------------
# A dedicated logger so the trip capture is visible on its own, independent of
# the generic ``debug_log`` toggle (which only raises the parent logger). Lines
# are emitted at INFO so they appear whatever the integration's log level, and
# only ever when the user has explicitly switched trip-capture mode on.
# The name predates this module and stays: a user's logger config names it.
TRIPLOG = logging.getLogger("custom_components.bavariandata.coordinator.tripcapture")
# Basename of the raw-batch NDJSON capture written to the HA config dir while
# trip-capture mode is on. One record per MQTT batch, replayable offline.
TRIP_CAPTURE_FILE = "bavariandata_trip_capture.ndjson"
# Stop appending once the capture file reaches this size, so a toggle left on by
# accident can't fill the disk. ~25 MB is many hours of driving.
TRIP_CAPTURE_MAX_BYTES = 25 * 1024 * 1024
# Every field of a segment/accumulated batch, captured whole (not just the
# ``end.*`` subset the detector reduces to a boolean) so we can tell whether any
# variant ever carries a real trip end.
DESC_SEG_CAPTURE_PREFIX = "vehicle.trip.segment."


def _parse(value: Any) -> Optional[datetime]:
    """A BMW timestamp, or None when it isn't one with a timezone."""

    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


class TripLog:
    """The trip detector's log, and capture mode's raw-batch record."""

    def __init__(self) -> None:
        # Capture mode (``trip_debug``), refreshed from the options.
        self.enabled = False
        # The file-size warning is logged only once.
        self._warned = False

    def cap(self, msg: str, *args: Any, substrate: bool = False) -> None:
        """Emit a trip log line, routed by which debug mode is on.

        Trip-capture mode is the loudest: it sends the line to the dedicated
        capture logger at INFO so it is visible on its own, whether or not the
        generic ``debug_log`` is on. Without capture mode, the decision lines
        (``substrate=False``) still appear under ``debug_log``; the heavy
        substrate lines (``substrate=True``) never do -- they are only worth
        their volume during a deliberate capture.
        """

        if self.enabled:
            TRIPLOG.info(msg, *args)
        elif not substrate and debug_enabled():
            _LOGGER.debug(msg, *args)

    def log_batch(
        self,
        vin: str,
        data: Dict[str, Any],
        now: datetime,
        *,
        open_trip: bool,
        fresh_s: float,
    ) -> None:
        """Log one raw batch: ``[trip.raw]``, ``[trip.seg]``, ``[trip.watch]``.

        ``fresh_s`` is how recent a segment timestamp must be to count as a
        completed trip (the detector's ``SEG_FRESH_S``). Never raises.
        """

        try:
            scalars = []
            for key, payload in data.items():
                short = key[len("vehicle.") :] if key.startswith("vehicle.") else key
                value = payload.get("value") if isinstance(payload, dict) else payload
                if isinstance(value, (dict, list)):
                    scalars.append(f"{short}=<{type(value).__name__}>")
                else:
                    scalars.append(f"{short}={value}")
            self.cap(
                "[trip.raw] %s open=%s n=%d %s",
                vin,
                open_trip,
                len(data),
                " ".join(scalars),
                substrate=True,
            )

            # Segment/accumulated batch, captured whole with each field's own
            # timestamp so we can see whether any ever carries a real trip end.
            seg = {k: v for k, v in data.items() if k.startswith(DESC_SEG_CAPTURE_PREFIX)}
            if seg:
                freshest = None
                parts = []
                for key, payload in seg.items():
                    short = key[len(DESC_SEG_CAPTURE_PREFIX) :]
                    if isinstance(payload, dict):
                        ts = payload.get("timestamp")
                        parts.append(f"{short}={payload.get('value')}@{ts}")
                        parsed = _parse(ts)
                        if parsed is not None and (freshest is None or parsed > freshest):
                            freshest = parsed
                    else:
                        parts.append(f"{short}={payload}")
                age = (now - freshest).total_seconds() if freshest is not None else None
                self.cap(
                    "[trip.seg] %s n=%d freshest_age=%ss fresh=%s %s",
                    vin,
                    len(seg),
                    None if age is None else int(age),
                    age is not None and age <= fresh_s,
                    " ".join(parts),
                    substrate=True,
                )

            # Curated candidate lifecycle/motion signals present in this batch,
            # pulled out of the firehose so the fields we hope might drive a
            # better detector are trivially greppable. Each with its own
            # timestamp; absent (unstreamed) fields simply never appear.
            watch = []
            for key in TRIP_WATCH_DESCRIPTORS:
                payload = data.get(key)
                if not isinstance(payload, dict):
                    continue
                short = key[len("vehicle.") :] if key.startswith("vehicle.") else key
                watch.append(f"{short}={payload.get('value')}@{payload.get('timestamp')}")
            if watch:
                self.cap("[trip.watch] %s %s", vin, " ".join(watch), substrate=True)
        except Exception:  # noqa: BLE001 - capture must never break the stream
            _LOGGER.exception("Trip capture (log) failed for %s", mask_vin(vin))

    @staticmethod
    def record_line(
        vin: str, data: Dict[str, Any], now: datetime, *, open_trip: bool
    ) -> Optional[str]:
        """One batch as a line of the NDJSON capture file, or None if it won't encode."""

        record = {"at": now.isoformat(), "vin": vin, "open": open_trip, "data": data}
        try:
            return json.dumps(record, default=str)
        except TypeError, ValueError:
            return None

    def append_line(self, path: str, line: str) -> None:
        """Append to the capture file, up to ``TRIP_CAPTURE_MAX_BYTES`` (blocking IO)."""

        try:
            if os.path.exists(path) and os.path.getsize(path) > TRIP_CAPTURE_MAX_BYTES:
                if not self._warned:
                    self._warned = True
                    TRIPLOG.warning(
                        "Trip capture file %s exceeded %d bytes; pausing capture "
                        "writes. Turn trip-capture mode off, or move/delete the file.",
                        path,
                        TRIP_CAPTURE_MAX_BYTES,
                    )
                return
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as err:
            TRIPLOG.warning("Trip capture file write failed: %s", err)
