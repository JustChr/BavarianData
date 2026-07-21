#!/usr/bin/env python3
"""Generate the descriptor metadata registry from the canonical catalogue.

Reads ``catalogue.json`` and emits ``descriptor_metadata.py`` — a plain-data
registry (no Home Assistant imports) describing, per descriptor:

* ``section`` / ``category`` — BMW's grouping used for clustering,
* ``device_class`` / ``state_class`` / ``unit`` — Home Assistant hints so
  numeric sensors get statistics, unit conversion and proper history,
* ``options`` — raw enum values (for ENUM sensors / state translations),
* ``entity_category`` — ``"diagnostic"`` for the technical long tail,
* ``enabled_default`` — whether the entity is created enabled.

``sensor.py`` maps the string values here onto the Home Assistant enums, so
this file stays framework-agnostic and unit-testable.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PKG = REPO_ROOT / "custom_components" / "bavariandata"
CATALOGUE_FILE = PKG / "catalogue.json"
OUTPUT_FILE = PKG / "descriptor_metadata.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Shared with tools/generate_translations.py and the tests so metadata options
# and translation state labels are derived identically (see catalogue_enums.py).
enum_tokens = _load("catalogue_enums", PKG / "catalogue_enums.py").enum_tokens

# Raw catalogue unit -> Home Assistant canonical unit string.
UNIT_CANONICAL = {
    "": None,
    "-": None,
    "null": None,
    "percent": "%",
    "%": "%",
    "celsius": "°C",
    "Celsius": "°C",
    "km": "km",
    "km/h": "km/h",
    "kW": "kW",
    "kWh": "kWh",
    "W": "W",
    "V": "V",
    "A": "A",
    "kPa": "kPa",
    "l": "L",
    "min": "min",
    "s": "s",
    "h": "h",
    "degrees": "°",
    "weeks": "weeks",
    "months": "months",
    "stars": "stars",
}

# Descriptors whose min/h unit denotes a clock component (hour/minute of day),
# not a duration.
_CLOCK_SUFFIX = re.compile(r"\.(hour|minute)$")

# Fields that belong in the collapsed "Diagnostic" section and are created
# disabled by default. Matched as substrings against the descriptor.
_DIAGNOSTIC_PATTERNS = (
    ".raw",
    "sessionid",
    "isosessionid",
    "plugeventid",
    "plausibility",
    "obfcm",
    "channel.ista",
    "channel.ngtp",
    "diagnostictroublecodes",
    "cablecheckvoltage",
    "isrcpconfigcomplete",
    "referencedistance",
    "hvpmfinishreason",
    "smeenergydelta",
    ".header",
    "deepsleepmodeactive",
    "timesetting",
    "timevehicle",
    "sim.status",
    "learningnavigation",
)

# Sections whose fields are informational/administrative -> diagnostic+disabled.
_DIAGNOSTIC_SECTIONS = {"metadata", "contract"}

# Explicit overrides for descriptors whose catalogue unit is missing/unreliable
# but whose semantics are well known. (device_class, state_class, unit)
_OVERRIDES: dict[str, tuple[str | None, str | None, str | None]] = {
    # Odometer: catalogue lists unit "null" though the value range is km/mi.
    "vehicle.vehicle.travelledDistance": ("distance", "total_increasing", "km"),
}


def canonical_unit(raw: str) -> str | None:
    return UNIT_CANONICAL.get(raw, raw or None)


def parse_options(value_range: str, data_type: str = "") -> tuple[str, ...]:
    """Return enum option slugs from a value-range string, else empty.

    Enum detection lives in :func:`catalogue_enums.enum_tokens` (shared with the
    translation generator). Home Assistant requires ``options`` / translation
    state keys to be lowercase slugs, so the tokens are lower-cased here; the
    runtime lower-cases the incoming value to match (see sensor.py).
    """

    return tuple(t.lower() for t in enum_tokens(value_range, data_type))


def device_and_state_class(
    descriptor: str, unit: str | None, data_type: str
) -> tuple[str | None, str | None]:
    d = descriptor.lower()
    lifetime = "overlifetime" in d or "consumption" in d

    if unit == "°C":
        return "temperature", "measurement"
    if unit == "V":
        return "voltage", "measurement"
    if unit == "A":
        return "current", "measurement"
    if unit == "W":
        return "power", "measurement"
    if unit == "kW":
        return "power", "measurement"
    if unit == "kWh":
        if "size" in d or "maxenergy" in d or "capacity" in d:
            return "energy_storage", "measurement"
        # HA forbids state_class "measurement" on the energy device class; a
        # non-cumulative energy reading (e.g. delta-to-full) must be None.
        return "energy", ("total_increasing" if lifetime else None)
    if unit == "kPa":
        return "pressure", "measurement"
    if unit == "km/h":
        return "speed", "measurement"
    if unit == "km":
        if "travelleddistance" in d or lifetime:
            return "distance", "total_increasing"
        return "distance", "measurement"
    if unit == "L":
        return "volume_storage", ("total_increasing" if lifetime else "measurement")
    if unit in ("min", "s", "h") and not _CLOCK_SUFFIX.search(descriptor):
        return "duration", None
    if unit == "%":
        if "stateofcharge" in d or "soc" in d or ".level" in d:
            return "battery", "measurement"
        return None, "measurement"
    return None, None


def classify(entry: dict) -> dict:
    descriptor = entry["descriptor"]
    d = descriptor.lower()
    unit = canonical_unit(entry["unit"])
    data_type = entry["data_type"]
    options = parse_options(
        entry["value_range_en"] or entry["value_range_de"], data_type
    )

    if descriptor in _OVERRIDES:
        device_class, state_class, unit = _OVERRIDES[descriptor]
    else:
        device_class, state_class = device_and_state_class(descriptor, unit, data_type)

    diagnostic = entry["section"] in _DIAGNOSTIC_SECTIONS or any(
        p in d for p in _DIAGNOSTIC_PATTERNS
    )
    entity_category = "diagnostic" if diagnostic else None
    enabled_default = not diagnostic

    return {
        "section": entry["section"],
        "category": entry["category"],
        "device_class": device_class,
        "state_class": state_class,
        "unit": unit,
        "options": options,
        "entity_category": entity_category,
        "enabled_default": enabled_default,
    }


def main() -> None:
    data = json.loads(CATALOGUE_FILE.read_text(encoding="utf-8"))
    meta = {e["descriptor"]: classify(e) for e in data["descriptors"]}

    # Ordered slug -> human label for each cluster/section, in the catalogue's
    # own order (which follows BMW's grouping). Powers the cluster picker.
    sections: dict[str, str] = {}
    for entry in data["descriptors"]:
        sections.setdefault(entry["section"], entry.get("section_label") or entry["section"])

    lines = [
        '"""Descriptor metadata registry generated from catalogue.json.',
        "",
        "Generated by tools/generate_metadata.py — do not edit by hand.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "# Cluster/section slug -> human label, in BMW's catalogue order.",
        "SECTIONS: dict[str, str] = {",
    ]
    for slug, label in sections.items():
        lines.append(f"    {slug!r}: {label!r},")
    lines.append("}")
    lines.append("")
    lines.append("# descriptor -> metadata mapping. See tools/generate_metadata.py.")
    lines.append("DESCRIPTOR_META: dict[str, dict] = {")
    for descriptor in sorted(meta):
        m = meta[descriptor]
        lines.append(f"    {descriptor!r}: {{")
        lines.append(f"        \"section\": {m['section']!r},")
        lines.append(f"        \"category\": {m['category']!r},")
        lines.append(f"        \"device_class\": {m['device_class']!r},")
        lines.append(f"        \"state_class\": {m['state_class']!r},")
        lines.append(f"        \"unit\": {m['unit']!r},")
        lines.append(f"        \"options\": {list(m['options'])!r},")
        lines.append(f"        \"entity_category\": {m['entity_category']!r},")
        lines.append(f"        \"enabled_default\": {m['enabled_default']!r},")
        lines.append("    },")
    lines.append("}")
    lines.append("")
    OUTPUT_FILE.write_text("\n".join(lines), encoding="utf-8")

    from collections import Counter

    print(f"Wrote {len(meta)} entries to {OUTPUT_FILE.relative_to(REPO_ROOT)}")
    print("  device_class:", dict(Counter(m["device_class"] for m in meta.values())))
    print("  enabled_default True:", sum(m["enabled_default"] for m in meta.values()))
    print("  diagnostic:", sum(m["entity_category"] == "diagnostic" for m in meta.values()))
    print("  with options:", sum(1 for m in meta.values() if m["options"]))


if __name__ == "__main__":
    main()
