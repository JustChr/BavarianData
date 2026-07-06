#!/usr/bin/env python3
"""Generate Home Assistant entity translations from the catalogue.

Writes the ``entity`` block of ``translations/en.json`` (English, using the
curated ``title_en`` names from the catalogue so existing installs are not
renamed) and ``translations/de.json`` (BMW's own German element names). Enum
sensors also get per-state labels; a curated bilingual map covers the common
control values and the long tail is humanised from the raw token (German falls
back to English).

Besides the catalogue-driven sensors/binary_sensors, the fixed ``device_tracker``
and ``image`` entities (which have no catalogue descriptor) get their names here
so a regeneration never drops them.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PKG = REPO_ROOT / "custom_components" / "bavariandata"
CATALOGUE_FILE = PKG / "catalogue.json"
TRANS_DIR = PKG / "translations"


def _load(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(module_name, PKG / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

translation_key = _load("keys", "keys.py").translation_key

# Curated bilingual labels for frequently occurring enum values. Anything not
# listed is humanised from the raw token (English) and German falls back to it.
COMMON_STATES: dict[str, tuple[str, str]] = {
    "ON": ("On", "Ein"),
    "OFF": ("Off", "Aus"),
    "AUTOMATIC": ("Automatic", "Automatisch"),
    "NO_CHANGE": ("No change", "Keine Änderung"),
    "NO_ACTION": ("No action", "Keine Aktion"),
    "ACTIVATE": ("Activate", "Aktivieren"),
    "DEACTIVATE": ("Deactivate", "Deaktivieren"),
    "OPEN": ("Open", "Offen"),
    "CLOSED": ("Closed", "Geschlossen"),
    "INTERMEDIATE": ("Partially open", "Teilweise offen"),
    "INVALID": ("Invalid", "Ungültig"),
    "UNKNOWN": ("Unknown", "Unbekannt"),
    "CONNECTED": ("Connected", "Verbunden"),
    "DISCONNECTED": ("Disconnected", "Getrennt"),
    "LOCKED": ("Locked", "Verriegelt"),
    "UNLOCKED": ("Unlocked", "Entriegelt"),
    "SECURED": ("Secured", "Gesichert"),
    "KILOMETERS": ("Kilometres", "Kilometer"),
    "MILES": ("Miles", "Meilen"),
    "CHARGINGACTIVE": ("Charging", "Lädt"),
    "CHARGINGPAUSED": ("Charging paused", "Ladevorgang pausiert"),
    "CHARGINGENDED": ("Charging ended", "Ladevorgang beendet"),
    "CHARGINGERROR": ("Charging error", "Ladefehler"),
    "NOCHARGING": ("Not charging", "Lädt nicht"),
    "INITIALIZATION": ("Initialising", "Initialisierung"),
}


def humanise(token: str) -> str:
    text = token.replace("_", " ").replace("-", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return token
    return text[:1].upper() + text[1:].lower()


def state_labels(options: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    # Home Assistant requires translation state keys to be lowercase slugs, so
    # key by the lower-cased token; the human label keeps its casing. The
    # runtime lower-cases BMW's value to match (see sensor.py).
    en: dict[str, str] = {}
    de: dict[str, str] = {}
    for raw in options:
        key = raw.lower()
        if raw in COMMON_STATES:
            en[key], de[key] = COMMON_STATES[raw]
        else:
            en[key] = humanise(raw)
            # German intentionally omitted -> HA falls back to the English label.
    return en, de


def en_name(descriptor: str, entry: dict) -> str:
    # Prefer our curated display name (catalogue title_en); fall back to BMW's
    # raw English element/name, then the descriptor's last segment.
    for candidate in (entry.get("title_en"), entry.get("element_en"), entry.get("name_en")):
        if candidate:
            return candidate
    return descriptor.rsplit(".", 1)[-1]


def de_name(descriptor: str, entry: dict, fallback: str) -> str:
    return entry.get("name_de") or fallback


def build() -> tuple[dict, dict]:
    data = json.loads(CATALOGUE_FILE.read_text(encoding="utf-8"))
    sensor_en: dict[str, dict] = {}
    sensor_de: dict[str, dict] = {}
    binary_en: dict[str, dict] = {}
    binary_de: dict[str, dict] = {}

    for entry in data["descriptors"]:
        descriptor = entry["descriptor"]
        key = translation_key(descriptor)
        name_en = en_name(descriptor, entry)
        name_de = de_name(descriptor, entry, name_en)

        # A descriptor surfaces as either a sensor or a binary_sensor at runtime
        # depending on value type; emit the name under both so it always resolves.
        sensor_en[key] = {"name": name_en}
        sensor_de[key] = {"name": name_de}
        binary_en[key] = {"name": name_en}
        binary_de[key] = {"name": name_de}

        # Same enum detection as tools/generate_metadata.py: purely value-range
        # based, because BMW's "boolean" data type is not reliable (some carry
        # string enum values). Real booleans use lowercase true/false and are
        # excluded by the ALL_CAPS token rule.
        value_range = entry.get("value_range_en") or entry.get("value_range_de") or ""
        opts = [t.strip() for t in value_range.split(",") if re.fullmatch(r"[A-Z][A-Z0-9_\-]+", t.strip())]
        opts = list(dict.fromkeys(opts))
        if len(opts) >= 2:
            en_states, de_states = state_labels(opts)
            sensor_en[key]["state"] = en_states
            if de_states:
                sensor_de[key]["state"] = de_states

    # The device tracker and vehicle image have no catalogue descriptor; their
    # names are fixed here so a regeneration never drops them.
    en_entity = {
        "sensor": sensor_en,
        "binary_sensor": binary_en,
        "device_tracker": {"car": {"name": "Location"}},
        "image": {"vehicle_image": {"name": "Vehicle Image"}},
    }
    de_entity = {
        "sensor": sensor_de,
        "binary_sensor": binary_de,
        "device_tracker": {"car": {"name": "Standort"}},
        "image": {"vehicle_image": {"name": "Fahrzeugbild"}},
    }
    return en_entity, de_entity


def write_language(filename: str, entity_block: dict, *, entity_only: bool) -> None:
    path = TRANS_DIR / filename
    if path.exists():
        doc = json.loads(path.read_text(encoding="utf-8"))
    else:
        doc = {}
    doc["entity"] = entity_block
    path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Wrote entity translations to {path.relative_to(REPO_ROOT)}")


def main() -> None:
    en_entity, de_entity = build()
    write_language("en.json", en_entity, entity_only=False)
    write_language("de.json", de_entity, entity_only=True)


if __name__ == "__main__":
    main()
