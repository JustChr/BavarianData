"""Make a trip-capture file safe to commit as a regression replay.

Trip-capture mode writes every raw MQTT batch to
``<config>/bavariandata_trip_capture.ndjson`` -- BMW's data exactly as it
arrived, which is what makes it a faithful regression test and also what makes
it private: a VIN, where someone lives and works, and when they come and go.
This repository is public, so a capture goes through here first:

* every VIN becomes ``WBATEST000000000<n>``;
* every latitude/longitude is shifted so the first fix lands on a fixed point
  (48.1, 16.3) -- a shift, not a scale, so distances and turns are kept;
* every timestamp is shifted so the capture starts on 2026-01-01 at 08:00 UTC
  -- the gaps between messages, which the detectors depend on, are exact;
* descriptors that can carry free text (addresses, destinations, names) are
  dropped.

Usage::

    python tools/anonymize_capture.py capture.ndjson tests/replays/<name>.ndjson

Read the output before committing it. The scrub is by rule, and a descriptor
BMW adds tomorrow will not be on the list.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

ORIGIN = (48.1, 16.3)
EPOCH = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
DROP_WORDS = ("address", "destination", "street", "city", "name", "poi", "contact")


def _parse(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _fmt(when: datetime, like: str) -> str:
    text = when.astimezone(timezone.utc).isoformat()
    return text.replace("+00:00", "Z") if like.endswith("Z") else text


class Anonymizer:
    def __init__(self) -> None:
        self.vins: dict[str, str] = {}
        self.shift_time: Optional[timedelta] = None
        # One shift per axis, each learned from its own first value: BMW sends
        # latitude and longitude as separate batches.
        self.shift_axis: dict[str, float] = {}

    def vin(self, vin: str) -> str:
        if vin not in self.vins:
            self.vins[vin] = f"WBATEST{len(self.vins) + 1:010d}"
        return self.vins[vin]

    def time(self, value: str) -> str:
        parsed = _parse(value)
        if parsed is None:
            return value
        if self.shift_time is None:
            self.shift_time = EPOCH - parsed
        return _fmt(parsed + self.shift_time, value)

    def coordinate(self, axis: str, value: float) -> float:
        target = ORIGIN[0] if axis == "latitude" else ORIGIN[1]
        shift = self.shift_axis.setdefault(axis, target - value)
        return round(value + shift, 6)

    def data(self, data: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, entry in data.items():
            if any(word in key.lower() for word in DROP_WORDS):
                continue
            if not isinstance(entry, dict):
                out[key] = entry
                continue
            entry = dict(entry)
            value = entry.get("value")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                axis = key.rsplit(".", 1)[-1]
                if axis in ("latitude", "longitude"):
                    entry["value"] = self.coordinate(axis, float(value))
            if "timestamp" in entry:
                entry["timestamp"] = self.time(entry["timestamp"])
            out[key] = entry
        return out

    def record(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "at": self.time(record["at"]),
            "vin": self.vin(record.get("vin", "")),
            "data": self.data(record.get("data") or {}),
        }


def anonymize(lines: list[str]) -> list[str]:
    anonymizer = Anonymizer()
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        out.append(json.dumps(anonymizer.record(json.loads(line)), sort_keys=True))
    return out


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("source", type=pathlib.Path)
    parser.add_argument("target", type=pathlib.Path)
    args = parser.parse_args(argv)
    lines = args.source.read_text(encoding="utf-8").splitlines()
    result = anonymize(lines)
    args.target.write_text("\n".join(result) + "\n", encoding="utf-8")
    print(f"{len(result)} batches -> {args.target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
