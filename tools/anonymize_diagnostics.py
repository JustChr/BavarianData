"""Reduce a user's diagnostics JSON to a car fixture for ``tests/cars/``.

A fixture keeps only what the dashboard card's automatic picks depend on: the
model name and, per streamed descriptor, its descriptor path and unit. The VIN
(already redacted by the diagnostics), arrival counts, timestamps, the account
and every other field are dropped -- this repository is public.

    python tools/anonymize_diagnostics.py <diagnostics.json> tests/cars/<slug>.json [--vehicle N]

Read the output before committing it.
"""

from __future__ import annotations

import argparse
import json
import pathlib


def reduce_vehicle(vehicle: dict) -> dict:
    """Return the fixture for one ``vehicles`` row of a diagnostics dump."""

    rows = sorted(
        (
            {"descriptor": r["descriptor"], "unit": r.get("unit") or None}
            for r in vehicle["descriptors"]
        ),
        key=lambda r: r["descriptor"],
    )
    return {"name": vehicle["name"], "descriptors": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("diagnostics", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    parser.add_argument(
        "--vehicle", type=int, default=0, help="index into the dump's vehicles list"
    )
    args = parser.parse_args()

    dump = json.loads(args.diagnostics.read_text(encoding="utf-8"))
    vehicles = (dump.get("data") or dump).get("vehicles") or []
    vehicle = vehicles[args.vehicle]
    if not vehicle.get("name"):
        raise SystemExit("this vehicle has no model name; it has not streamed yet")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(reduce_vehicle(vehicle), indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.output} ({len(vehicle['descriptors'])} descriptors)")


if __name__ == "__main__":
    main()
