#!/usr/bin/env python3
"""Generate Home Assistant entity translations from the catalogue.

Writes the ``entity`` block of ``translations/en.json`` (English, using the
curated ``title_en`` names from the catalogue so existing installs are not
renamed) and ``translations/de.json`` (BMW's own German element names). Enum
sensors also get per-state labels; a curated bilingual map covers the common
control values and the long tail is humanised from the raw token (German falls
back to English).

Entities without a BMW catalogue descriptor — the integration's own derived and
diagnostic sensors, plus the fixed ``device_tracker`` and ``image`` entities —
are named from ``tools/derived_entities.json`` and merged into the same block, so
a regeneration never drops them and they stay bilingual like everything else.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PKG = REPO_ROOT / "custom_components" / "bavariandata"
CATALOGUE_FILE = PKG / "catalogue.json"
DERIVED_FILE = Path(__file__).resolve().parent / "derived_entities.json"
TRANS_DIR = PKG / "translations"


def _load(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(module_name, PKG / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

translation_key = _load("keys", "keys.py").translation_key
# Shared enum detection (see catalogue_enums.py) so translation state labels and
# the metadata ``options`` are always derived from the same tokens.
enum_tokens = _load("catalogue_enums", "catalogue_enums.py").enum_tokens

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
    "OK": ("OK", "OK"),
    "utc": ("UTC", "UTC"),
    # Anti-theft alarm arming state (vehicle.…antiTheftAlarmSystem.alarm.armStatus).
    "unarmed": ("Unarmed", "Unscharf"),
    "doorsOnly": ("Doors only", "Nur Türen"),
    "doorsTiltCabin": ("Doors, tilt & interior", "Türen, Neigung & Innenraum"),
    # Preconditioning activity (vehicle.vehicle.preConditioning.activity).
    "standby": ("Standby", "Bereitschaft"),
    "heating": ("Heating", "Heizen"),
    "cooling": ("Cooling", "Kühlen"),
    "ventilation": ("Ventilation", "Lüften"),
    "inactive": ("Inactive", "Inaktiv"),
    # Preconditioning error (vehicle.vehicle.preConditioning.error).
    "LowFuel": ("Low fuel", "Niedriger Kraftstoffstand"),
    "LowBattery": ("Low battery", "Niedriger Batteriestand"),
    "QuotaExceeded": ("Quota exceeded", "Kontingent überschritten"),
    "HeaterFailure": ("Heater failure", "Heizungsfehler"),
    "ComponentFailure": ("Component failure", "Komponentenfehler"),
    "OpenOrUnlocked": ("Open or unlocked", "Offen oder entriegelt"),
    # Time setting (vehicle.vehicle.timeSetting).
    "wintertime": ("Winter time", "Winterzeit"),
    "summertime": ("Summer time", "Sommerzeit"),
    "manual": ("Manual", "Manuell"),
    # Display distance unit (vehicle.cabin.infotainment.displayUnit.distance).
    "km": ("Kilometres", "Kilometer"),
    "miles": ("Miles", "Meilen"),
}


def humanise(token: str) -> str:
    # Split camelCase (doorsTiltCabin -> "doors Tilt Cabin") so uncurated
    # mixed-case enum tokens still read as words, then normalise separators.
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", token)
    text = text.replace("_", " ").replace("-", " ").strip()
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

        # Same enum detection as tools/generate_metadata.py (shared helper), so
        # every metadata option gets a matching state label.
        value_range = entry.get("value_range_en") or entry.get("value_range_de") or ""
        opts = list(enum_tokens(value_range, entry.get("data_type", "")))
        if opts:
            en_states, de_states = state_labels(opts)
            sensor_en[key]["state"] = en_states
            if de_states:
                sensor_de[key]["state"] = de_states

    en_entity = {"sensor": sensor_en, "binary_sensor": binary_en}
    de_entity = {"sensor": sensor_de, "binary_sensor": binary_de}
    merge_derived(en_entity, de_entity)
    return en_entity, de_entity


def load_derived() -> dict[str, dict[str, dict[str, str]]]:
    data = json.loads(DERIVED_FILE.read_text(encoding="utf-8"))
    return {
        platform: entries
        for platform, entries in data.items()
        if not platform.startswith("_")
    }


def merge_derived(en_entity: dict, de_entity: dict) -> None:
    """Fold the integration's own (non-catalogue) entity names into the block.

    These have no BMW descriptor, so nothing in the catalogue can name them; a
    hand-authored bilingual source is the only way they can appear in the
    generated ``entity`` block instead of being hardcoded in Python (where German
    users would only ever see English).
    """

    for platform, entries in load_derived().items():
        target_en = en_entity.setdefault(platform, {})
        target_de = de_entity.setdefault(platform, {})
        for key, names in entries.items():
            # A collision would mean a derived entity silently overwrites (or is
            # overwritten by) a real descriptor's name -- fail loudly instead.
            if key in target_en:
                raise SystemExit(
                    f"derived_entities.json: {platform}.{key} collides with a "
                    "catalogue-derived translation key"
                )
            target_en[key] = {"name": names["en"]}
            target_de[key] = {"name": names.get("de") or names["en"]}


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
