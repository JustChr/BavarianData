#!/usr/bin/env python3
"""Generate Home Assistant entity translations from the catalogue.

Writes the ``entity`` block of ``translations/en.json`` (English, using the
curated ``title_en`` names from the catalogue so existing installs are not
renamed) and of every other language in ``LANGUAGES``: ``de.json`` from BMW's
German element names, the rest from BMW's other localized catalogue exports
(``names_i18n``). Enum sensors also get per-state labels; a curated table covers
the common control values and the long tail is humanised from the raw token
(every other language falls back to English).

Entities without a BMW catalogue descriptor — the integration's own derived and
diagnostic sensors, plus the fixed ``device_tracker`` and ``image`` entities —
are named from ``tools/derived_entities.json`` and merged into the same block, so
a regeneration never drops them and they stay translated like everything else.
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
enum_options = _load("catalogue_enums", "catalogue_enums.py").enum_options

# Every language written besides English, in the column order of _STATE_ROWS.
# German names come from the German catalogue export (``name_de``), the others
# from BMW's localized exports in tools/catalogue_i18n/ (``names_i18n``).
LANGUAGES: tuple[str, ...] = ("de", "fr", "it", "es", "nl", "pl", "pt", "cs", "sv")

# Curated labels for frequently occurring enum values, one column per language:
# en, then LANGUAGES in order. Anything not listed is humanised from the raw
# token (English) and every other language falls back to it.
# fmt: off
_STATE_ROWS: dict[str, tuple[str, ...]] = {
    "ON": ("On", "Ein", "Activé", "Attivo", "Activado", "Aan", "Włączone", "Ligado", "Zapnuto", "På"),
    "OFF": ("Off", "Aus", "Désactivé", "Disattivo", "Desactivado", "Uit", "Wyłączone", "Desligado", "Vypnuto", "Av"),
    "AUTOMATIC": ("Automatic", "Automatisch", "Automatique", "Automatico", "Automático", "Automatisch", "Automatycznie", "Automático", "Automaticky", "Automatiskt"),
    "NO_CHANGE": ("No change", "Keine Änderung", "Aucun changement", "Nessuna modifica", "Sin cambios", "Geen wijziging", "Bez zmian", "Sem alteração", "Beze změny", "Ingen ändring"),
    "NO_ACTION": ("No action", "Keine Aktion", "Aucune action", "Nessuna azione", "Ninguna acción", "Geen actie", "Brak działania", "Nenhuma ação", "Žádná akce", "Ingen åtgärd"),
    "ACTIVATE": ("Activate", "Aktivieren", "Activer", "Attiva", "Activar", "Activeren", "Aktywuj", "Ativar", "Aktivovat", "Aktivera"),
    "DEACTIVATE": ("Deactivate", "Deaktivieren", "Désactiver", "Disattiva", "Desactivar", "Deactiveren", "Dezaktywuj", "Desativar", "Deaktivovat", "Inaktivera"),
    "OPEN": ("Open", "Offen", "Ouvert", "Aperto", "Abierto", "Open", "Otwarte", "Aberto", "Otevřeno", "Öppen"),
    "CLOSED": ("Closed", "Geschlossen", "Fermé", "Chiuso", "Cerrado", "Gesloten", "Zamknięte", "Fechado", "Zavřeno", "Stängd"),
    "INTERMEDIATE": ("Partially open", "Teilweise offen", "Partiellement ouvert", "Parzialmente aperto", "Parcialmente abierto", "Gedeeltelijk open", "Częściowo otwarte", "Parcialmente aberto", "Částečně otevřeno", "Delvis öppen"),
    "INVALID": ("Invalid", "Ungültig", "Non valide", "Non valido", "No válido", "Ongeldig", "Nieprawidłowy", "Inválido", "Neplatné", "Ogiltig"),
    "UNKNOWN": ("Unknown", "Unbekannt", "Inconnu", "Sconosciuto", "Desconocido", "Onbekend", "Nieznany", "Desconhecido", "Neznámé", "Okänd"),
    "CONNECTED": ("Connected", "Verbunden", "Connecté", "Collegato", "Conectado", "Verbonden", "Podłączony", "Conectado", "Připojeno", "Ansluten"),
    "DISCONNECTED": ("Disconnected", "Getrennt", "Déconnecté", "Scollegato", "Desconectado", "Losgekoppeld", "Odłączony", "Desconectado", "Odpojeno", "Frånkopplad"),
    "LOCKED": ("Locked", "Verriegelt", "Verrouillé", "Bloccato", "Bloqueado", "Vergrendeld", "Zablokowany", "Trancado", "Zamčeno", "Låst"),
    "UNLOCKED": ("Unlocked", "Entriegelt", "Déverrouillé", "Sbloccato", "Desbloqueado", "Ontgrendeld", "Odblokowany", "Destrancado", "Odemčeno", "Olåst"),
    "SECURED": ("Secured", "Gesichert", "Sécurisé", "Protetto", "Asegurado", "Beveiligd", "Zabezpieczony", "Protegido", "Zajištěno", "Säkrad"),
    # Locked except the driver's door. BMW spells it with a hyphen on
    # vehicle.cabin.door.lock.status and without one on the stream's
    # vehicle.cabin.door.status, so curate both; the wording matches the
    # dashboard card's central-lock tile.
    "SELECTIVE-LOCKED": ("Partially locked", "Teilweise verriegelt", "Partiellement verrouillé", "Parzialmente bloccato", "Parcialmente bloqueado", "Gedeeltelijk vergrendeld", "Częściowo zablokowany", "Parcialmente trancado", "Částečně zamčeno", "Delvis låst"),
    "SELECTIVELOCKED": ("Partially locked", "Teilweise verriegelt", "Partiellement verrouillé", "Parzialmente bloccato", "Parcialmente bloqueado", "Gedeeltelijk vergrendeld", "Częściowo zablokowany", "Parcialmente trancado", "Částečně zamčeno", "Delvis låst"),
    "KILOMETERS": ("Kilometers", "Kilometer", "Kilomètres", "Chilometri", "Kilómetros", "Kilometer", "Kilometry", "Quilómetros", "Kilometry", "Kilometer"),
    "MILES": ("Miles", "Meilen", "Miles", "Miglia", "Millas", "Mijl", "Mile", "Milhas", "Míle", "Engelska mil"),
    "CHARGINGACTIVE": ("Charging", "Lädt", "En charge", "In carica", "Cargando", "Laden", "Ładowanie", "A carregar", "Nabíjí se", "Laddar"),
    "CHARGINGPAUSED": ("Charging paused", "Ladevorgang pausiert", "Charge en pause", "Ricarica in pausa", "Carga en pausa", "Laden gepauzeerd", "Ładowanie wstrzymane", "Carregamento em pausa", "Nabíjení pozastaveno", "Laddning pausad"),
    "CHARGINGENDED": ("Charging ended", "Ladevorgang beendet", "Charge terminée", "Ricarica terminata", "Carga finalizada", "Laden beëindigd", "Ładowanie zakończone", "Carregamento concluído", "Nabíjení ukončeno", "Laddning avslutad"),
    "CHARGINGERROR": ("Charging error", "Ladefehler", "Erreur de charge", "Errore di ricarica", "Error de carga", "Laadfout", "Błąd ładowania", "Erro de carregamento", "Chyba nabíjení", "Laddningsfel"),
    "CHARGINGINTERRUPTED": ("Charging interrupted", "Ladevorgang unterbrochen", "Charge interrompue", "Ricarica interrotta", "Carga interrumpida", "Laden onderbroken", "Ładowanie przerwane", "Carregamento interrompido", "Nabíjení přerušeno", "Laddning avbruten"),
    "CHARGINGDISRUPTED": ("Charging disrupted", "Ladevorgang gestört", "Charge perturbée", "Ricarica disturbata", "Carga perturbada", "Laden verstoord", "Ładowanie zakłócone", "Carregamento perturbado", "Nabíjení narušeno", "Laddning störd"),
    "NOCHARGING": ("Not charging", "Lädt nicht", "Pas en charge", "Non in carica", "Sin cargar", "Laadt niet", "Brak ładowania", "Sem carregar", "Nenabíjí se", "Laddar inte"),
    "INITIALIZATION": ("Initializing", "Initialisierung", "Initialisation", "Inizializzazione", "Inicialización", "Initialiseren", "Inicjalizacja", "Inicialização", "Inicializace", "Initierar"),
    "OK": ("OK",) * 10,
    "utc": ("UTC",) * 10,
    # Anti-theft alarm arming state (vehicle.…antiTheftAlarmSystem.alarm.armStatus).
    "unarmed": ("Unarmed", "Unscharf", "Désarmé", "Disinserito", "Desactivada", "Uitgeschakeld", "Rozbrojony", "Desativado", "Nestřeženo", "Avlarmat"),
    "doorsOnly": ("Doors only", "Nur Türen", "Portes uniquement", "Solo porte", "Solo puertas", "Alleen deuren", "Tylko drzwi", "Apenas portas", "Pouze dveře", "Endast dörrar"),
    "doorsTiltCabin": ("Doors, tilt & interior", "Türen, Neigung & Innenraum", "Portes, inclinaison et habitacle", "Porte, inclinazione e abitacolo", "Puertas, inclinación e interior", "Deuren, kanteling en interieur", "Drzwi, przechył i wnętrze", "Portas, inclinação e habitáculo", "Dveře, náklon a interiér", "Dörrar, lutning och kupé"),
    # Preconditioning activity (vehicle.vehicle.preConditioning.activity).
    "standby": ("Standby", "Bereitschaft", "Veille", "Standby", "En espera", "Stand-by", "Czuwanie", "Em espera", "Pohotovost", "Standby"),
    "heating": ("Heating", "Heizen", "Chauffage", "Riscaldamento", "Calefacción", "Verwarmen", "Ogrzewanie", "Aquecimento", "Topení", "Värmer"),
    "cooling": ("Cooling", "Kühlen", "Refroidissement", "Raffreddamento", "Refrigeración", "Koelen", "Chłodzenie", "Arrefecimento", "Chlazení", "Kyler"),
    "ventilation": ("Ventilation", "Lüften", "Ventilation", "Ventilazione", "Ventilación", "Ventileren", "Wentylacja", "Ventilação", "Větrání", "Ventilerar"),
    "inactive": ("Inactive", "Inaktiv", "Inactif", "Inattivo", "Inactivo", "Inactief", "Nieaktywny", "Inativo", "Neaktivní", "Inaktiv"),
    # Preconditioning error (vehicle.vehicle.preConditioning.error).
    "LowFuel": ("Low fuel", "Niedriger Kraftstoffstand", "Niveau de carburant bas", "Carburante basso", "Combustible bajo", "Weinig brandstof", "Niski poziom paliwa", "Combustível baixo", "Nízká hladina paliva", "Låg bränslenivå"),
    "LowBattery": ("Low battery", "Niedriger Batteriestand", "Batterie faible", "Batteria scarica", "Batería baja", "Accu bijna leeg", "Niski poziom baterii", "Bateria fraca", "Slabá baterie", "Lågt batteri"),
    "QuotaExceeded": ("Quota exceeded", "Kontingent überschritten", "Quota dépassé", "Quota superata", "Cuota superada", "Quotum overschreden", "Przekroczono limit", "Quota excedida", "Kvóta překročena", "Kvot överskriden"),
    "HeaterFailure": ("Heater failure", "Heizungsfehler", "Panne du chauffage", "Guasto del riscaldamento", "Fallo de la calefacción", "Storing verwarming", "Awaria ogrzewania", "Falha do aquecimento", "Porucha topení", "Värmarfel"),
    "ComponentFailure": ("Component failure", "Komponentenfehler", "Panne d'un composant", "Guasto di un componente", "Fallo de un componente", "Storing onderdeel", "Awaria podzespołu", "Falha de componente", "Porucha součásti", "Komponentfel"),
    "OpenOrUnlocked": ("Open or unlocked", "Offen oder entriegelt", "Ouvert ou déverrouillé", "Aperto o sbloccato", "Abierto o desbloqueado", "Open of ontgrendeld", "Otwarte lub odblokowane", "Aberto ou destrancado", "Otevřeno nebo odemčeno", "Öppen eller olåst"),
    # Time setting (vehicle.vehicle.timeSetting).
    "wintertime": ("Winter time", "Winterzeit", "Heure d'hiver", "Ora solare", "Horario de invierno", "Wintertijd", "Czas zimowy", "Hora de inverno", "Zimní čas", "Vintertid"),
    "summertime": ("Summer time", "Sommerzeit", "Heure d'été", "Ora legale", "Horario de verano", "Zomertijd", "Czas letni", "Hora de verão", "Letní čas", "Sommartid"),
    "manual": ("Manual", "Manuell", "Manuel", "Manuale", "Manual", "Handmatig", "Ręcznie", "Manual", "Ručně", "Manuell"),
    # Display distance unit (vehicle.cabin.infotainment.displayUnit.distance).
    "km": ("Kilometers", "Kilometer", "Kilomètres", "Chilometri", "Kilómetros", "Kilometer", "Kilometry", "Quilómetros", "Kilometry", "Kilometer"),
    "miles": ("Miles", "Meilen", "Miles", "Miglia", "Millas", "Mijl", "Mile", "Milhas", "Míle", "Engelska mil"),
}
# fmt: on

COMMON_STATES: dict[str, dict[str, str]] = {
    raw: dict(zip(("en", *LANGUAGES), row, strict=True)) for raw, row in _STATE_ROWS.items()
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


def state_labels(options: list[str]) -> dict[str, dict[str, str]]:
    # Home Assistant requires translation state keys to be lowercase slugs, so
    # key by the lower-cased token; the human label keeps its casing. The
    # runtime lower-cases BMW's value to match (see sensor.py).
    labels: dict[str, dict[str, str]] = {lang: {} for lang in ("en", *LANGUAGES)}
    for raw in options:
        key = raw.lower()
        if raw in COMMON_STATES:
            for lang, label in COMMON_STATES[raw].items():
                labels[lang][key] = label
        else:
            labels["en"][key] = humanise(raw)
            # Other languages intentionally omitted -> HA falls back to English.
    return labels


def en_name(descriptor: str, entry: dict) -> str:
    # Prefer our curated display name (catalogue title_en); fall back to BMW's
    # raw English element/name, then the descriptor's last segment.
    for candidate in (entry.get("title_en"), entry.get("element_en"), entry.get("name_en")):
        if candidate:
            return candidate
    return descriptor.rsplit(".", 1)[-1]


def local_name(entry: dict, lang: str, fallback: str) -> str:
    # BMW's own element name in that language, from the German export or one of
    # the other localized ones; English when BMW has none.
    if lang == "de":
        return entry.get("name_de") or fallback
    return (entry.get("names_i18n") or {}).get(lang) or fallback


def build() -> dict[str, dict]:
    """``{lang: entity_block}`` for English and every language in LANGUAGES."""

    data = json.loads(CATALOGUE_FILE.read_text(encoding="utf-8"))
    langs = ("en", *LANGUAGES)
    sensor: dict[str, dict[str, dict]] = {lang: {} for lang in langs}
    binary: dict[str, dict[str, dict]] = {lang: {} for lang in langs}

    for entry in data["descriptors"]:
        descriptor = entry["descriptor"]
        key = translation_key(descriptor)
        name_en = en_name(descriptor, entry)

        # Same enum detection as tools/generate_metadata.py (shared helper), so
        # every metadata option gets a matching state label.
        value_range = entry.get("value_range_en") or entry.get("value_range_de") or ""
        opts = list(enum_options(descriptor, value_range, entry.get("data_type", "")))
        states = state_labels(opts) if opts else {}

        for lang in langs:
            name = name_en if lang == "en" else local_name(entry, lang, name_en)
            # A descriptor surfaces as either a sensor or a binary_sensor at
            # runtime depending on value type; emit the name under both so it
            # always resolves.
            sensor[lang][key] = {"name": name}
            binary[lang][key] = {"name": name}
            if states.get(lang):
                sensor[lang][key]["state"] = states[lang]

    blocks = {lang: {"sensor": sensor[lang], "binary_sensor": binary[lang]} for lang in langs}
    merge_derived(blocks)
    return blocks


def load_derived() -> dict[str, dict[str, dict[str, str]]]:
    data = json.loads(DERIVED_FILE.read_text(encoding="utf-8"))
    return {platform: entries for platform, entries in data.items() if not platform.startswith("_")}


def merge_derived(blocks: dict[str, dict]) -> None:
    """Fold the integration's own (non-catalogue) entity names into the blocks.

    These have no BMW descriptor, so nothing in the catalogue can name them; a
    hand-authored multilingual source is the only way they can appear in the
    generated ``entity`` block instead of being hardcoded in Python (where
    non-English users would only ever see English).
    """

    for platform, entries in load_derived().items():
        targets = {lang: block.setdefault(platform, {}) for lang, block in blocks.items()}
        for key, names in entries.items():
            # A collision would mean a derived entity silently overwrites (or is
            # overwritten by) a real descriptor's name -- fail loudly instead.
            if key in targets["en"]:
                raise SystemExit(
                    f"derived_entities.json: {platform}.{key} collides with a "
                    "catalogue-derived translation key"
                )
            for lang, target in targets.items():
                target[key] = {"name": names.get(lang) or names["en"]}


def write_language(filename: str, entity_block: dict) -> None:
    # Only the ``entity`` block is generated; the flow sections around it are
    # hand-maintained and read back so a regeneration preserves them.
    path = TRANS_DIR / filename
    if path.exists():
        doc = json.loads(path.read_text(encoding="utf-8"))
    else:
        doc = {}
    doc["entity"] = entity_block
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote entity translations to {path.relative_to(REPO_ROOT)}")


def main() -> None:
    for lang, block in build().items():
        write_language(f"{lang}.json", block)


if __name__ == "__main__":
    main()
