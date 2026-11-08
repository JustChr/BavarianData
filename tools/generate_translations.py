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
    # Issue #300: every other enum value BMW documents. German is @stepas72's,
    # checked by a native speaker; the other languages are ours.
    "HOSPITALITY_INACTIVE": ("Comfort function inactive", "Komfortfunktion inaktiv", "Fonction confort inactive", "Funzione comfort inattiva", "Función de confort inactiva", "Comfortfunctie inactief", "Funkcja komfortu nieaktywna", "Função de conforto inativa", "Komfortní funkce neaktivní", "Komfortfunktion inaktiv"),
    "HOSPITALITY_ACTIVE": ("Comfort function active", "Komfortfunktion aktiv", "Fonction confort active", "Funzione comfort attiva", "Función de confort activa", "Comfortfunctie actief", "Funkcja komfortu aktywna", "Função de conforto ativa", "Komfortní funkce aktivní", "Komfortfunktion aktiv"),
    "CHARGING_CABLE_NOT_LOCKED": ("Charging cable not locked", "Ladekabel nicht verriegelt", "Câble de charge non verrouillé", "Cavo di ricarica non bloccato", "Cable de carga no bloqueado", "Laadkabel niet vergrendeld", "Kabel ładowania niezablokowany", "Cabo de carregamento não trancado", "Nabíjecí kabel nezamčen", "Laddkabeln inte låst"),
    "CHARGING_CABLE_LOCKED": ("Charging cable locked", "Ladekabel verriegelt", "Câble de charge verrouillé", "Cavo di ricarica bloccato", "Cable de carga bloqueado", "Laadkabel vergrendeld", "Kabel ładowania zablokowany", "Cabo de carregamento trancado", "Nabíjecí kabel zamčen", "Laddkabeln låst"),
    "CHARGING_CABLE_LOCKING_ERROR_DETECTED": ("Cable locking error", "Fehler bei der Kabelverriegelung", "Erreur de verrouillage du câble", "Errore di bloccaggio del cavo", "Error de bloqueo del cable", "Fout bij kabelvergrendeling", "Błąd blokady kabla", "Erro de trancamento do cabo", "Chyba zamčení kabelu", "Fel vid kabellåsning"),
    "FLAP_UNLOCKED": ("Flap unlocked", "Klappe entriegelt", "Trappe déverrouillée", "Sportello sbloccato", "Tapa desbloqueada", "Klep ontgrendeld", "Klapka odblokowana", "Tampa destrancada", "Klapka odemčena", "Lucka olåst"),
    "FLAP_LOCKED": ("Flap locked", "Klappe verriegelt", "Trappe verrouillée", "Sportello bloccato", "Tapa bloqueada", "Klep vergrendeld", "Klapka zablokowana", "Tampa trancada", "Klapka zamčena", "Lucka låst"),
    "CLOSEDSECURED": ("Closed and secured", "Geschlossen und gesichert", "Fermé et sécurisé", "Chiuso e protetto", "Cerrado y asegurado", "Gesloten en beveiligd", "Zamknięty i zabezpieczony", "Fechado e protegido", "Zavřeno a zajištěno", "Stängd och säkrad"),
    "OPENSECURED": ("Open and secured", "Offen und gesichert", "Ouvert et sécurisé", "Aperto e protetto", "Abierto y asegurado", "Open en beveiligd", "Otwarty i zabezpieczony", "Aberto e protegido", "Otevřeno a zajištěno", "Öppen och säkrad"),
    "NOT_ACTIVE": ("Not active", "Nicht aktiv", "Non actif", "Non attivo", "No activo", "Niet actief", "Nieaktywny", "Não ativo", "Neaktivní", "Inte aktiv"),
    "COMFORT_HEATING": ("Comfort heating", "Komfort Heizen", "Confort chauffage", "Comfort riscaldamento", "Confort calefacción", "Comfort verwarmen", "Komfort ogrzewanie", "Conforto aquecimento", "Komfort topení", "Komfort värme"),
    "COMFORT_COOLING": ("Comfort cooling", "Komfort Kühlen", "Confort refroidissement", "Comfort raffreddamento", "Confort refrigeración", "Comfort koelen", "Komfort chłodzenie", "Conforto arrefecimento", "Komfort chlazení", "Komfort kyla"),
    "COMFORT_VENTILATION": ("Comfort ventilation", "Komfort Lüften", "Confort ventilation", "Comfort ventilazione", "Confort ventilación", "Comfort ventileren", "Komfort wentylacja", "Conforto ventilação", "Komfort větrání", "Komfort ventilation"),
    "DEFROST": ("Defrost", "Enteisen", "Dégivrage", "Sbrinamento", "Desescarchar", "Ontdooien", "Odmrażanie", "Descongelar", "Odmrazování", "Avfrostning"),
    "COMFORT_UNDEFINED": ("Not defined", "Nicht definiert", "Non défini", "Non definito", "No definido", "Niet gedefinieerd", "Nieokreślony", "Não definido", "Nedefinováno", "Inte definierad"),
    "UNFILTERED": ("Unfiltered", "Ungefiltert", "Non filtré", "Non filtrata", "Sin filtrar", "Ongefilterd", "Niefiltrowane", "Não filtrado", "Nefiltrováno", "Ofiltrerad"),
    "PURIFYING_ONGOING": ("Purifying", "Reinigung läuft", "Purification en cours", "Purificazione in corso", "Purificación en curso", "Reiniging bezig", "Oczyszczanie w toku", "Purificação em curso", "Čištění probíhá", "Rening pågår"),
    "PURIFIED": ("Purified", "Gereinigt", "Purifié", "Purificata", "Purificado", "Gereinigd", "Oczyszczone", "Purificado", "Vyčištěno", "Renad"),
    "NO_FIX": ("No GPS fix", "Kein GPS-Empfang", "Pas de signal GPS", "Nessun segnale GPS", "Sin señal GPS", "Geen GPS-ontvangst", "Brak sygnału GPS", "Sem sinal GPS", "Bez signálu GPS", "Ingen GPS-mottagning"),
    "GPS_FIX_2D": ("GPS fix 2D", "GPS-Position 2D", "Position GPS 2D", "Posizione GPS 2D", "Posición GPS 2D", "GPS-positie 2D", "Pozycja GPS 2D", "Posição GPS 2D", "Poloha GPS 2D", "GPS-position 2D"),
    "GPS_FIX_3D": ("GPS fix 3D", "GPS-Position 3D", "Position GPS 3D", "Posizione GPS 3D", "Posición GPS 3D", "GPS-positie 3D", "Pozycja GPS 3D", "Posição GPS 3D", "Poloha GPS 3D", "GPS-position 3D"),
    "OPEN_TILT": ("Tilted", "Ausgestellt", "Entrebâillé", "Sollevato", "Inclinado", "Gekanteld", "Uchylony", "Inclinado", "Vyklopeno", "Upplyft"),
    "INTERMEDIATE_TILT": ("Partially tilted", "Teilweise ausgestellt", "Partiellement entrebâillé", "Parzialmente sollevato", "Parcialmente inclinado", "Gedeeltelijk gekanteld", "Częściowo uchylony", "Parcialmente inclinado", "Částečně vyklopeno", "Delvis upplyft"),
    "ECU_COMMUNICATION_ERROR": ("Control unit communication error", "Kommunikationsfehler Steuergerät", "Erreur de communication du calculateur", "Errore di comunicazione della centralina", "Error de comunicación de la unidad de control", "Communicatiefout regeleenheid", "Błąd komunikacji sterownika", "Erro de comunicação da unidade de controlo", "Chyba komunikace řídicí jednotky", "Kommunikationsfel styrenhet"),
    "VEHICLE_MANIPULATION_DETECTED": ("Vehicle tampering detected", "Manipulation am Fahrzeug erkannt", "Manipulation du véhicule détectée", "Manomissione del veicolo rilevata", "Manipulación del vehículo detectada", "Manipulatie van het voertuig gedetecteerd", "Wykryto manipulację w pojeździe", "Manipulação do veículo detetada", "Zjištěna manipulace s vozidlem", "Manipulation av fordonet upptäckt"),
    "MANUFACTURER_EXCLUDED": ("Excluded by the manufacturer", "Vom Hersteller ausgeschlossen", "Exclu par le constructeur", "Escluso dal costruttore", "Excluido por el fabricante", "Uitgesloten door de fabrikant", "Wykluczone przez producenta", "Excluído pelo fabricante", "Vyloučeno výrobcem", "Undantagen av tillverkaren"),
    "PENDING": ("Pending", "Ausstehend", "En attente", "In sospeso", "Pendiente", "In behandeling", "Oczekujące", "Pendente", "Čeká", "Väntar"),
    "IDLE": ("Ready", "Bereit", "Prêt", "Pronto", "Listo", "Gereed", "Gotowy", "Pronto", "Připraveno", "Redo"),
    "SUCCESSFUL": ("Successful", "Erfolgreich", "Réussi", "Riuscito", "Correcto", "Geslaagd", "Zakończone powodzeniem", "Bem-sucedido", "Úspěšné", "Lyckades"),
    "ERROR": ("Error", "Fehler", "Erreur", "Errore", "Error", "Fout", "Błąd", "Erro", "Chyba", "Fel"),
    "MAXCHARGING": ("Maximum charging current", "Maximaler Ladestrom", "Courant de charge maximal", "Corrente di ricarica massima", "Corriente de carga máxima", "Maximale laadstroom", "Maksymalny prąd ładowania", "Corrente de carregamento máxima", "Maximální nabíjecí proud", "Maximal laddström"),
    "REDUCEDCHARGING": ("Reduced charging current", "Reduzierter Ladestrom", "Courant de charge réduit", "Corrente di ricarica ridotta", "Corriente de carga reducida", "Verlaagde laadstroom", "Zmniejszony prąd ładowania", "Corrente de carregamento reduzida", "Snížený nabíjecí proud", "Reducerad laddström"),
    "MINCHARGING": ("Minimum charging current", "Minimaler Ladestrom", "Courant de charge minimal", "Corrente di ricarica minima", "Corriente de carga mínima", "Minimale laadstroom", "Minimalny prąd ładowania", "Corrente de carregamento mínima", "Minimální nabíjecí proud", "Minimal laddström"),
    "NOTCHOSEN": ("Not selected", "Nicht gewählt", "Non sélectionné", "Non selezionato", "No seleccionado", "Niet gekozen", "Nie wybrano", "Não selecionado", "Nevybráno", "Inte vald"),
    "CHOSEN": ("Selected", "Gewählt", "Sélectionné", "Selezionato", "Seleccionado", "Gekozen", "Wybrano", "Selecionado", "Vybráno", "Vald"),
    "AUTHORIZATION_SUCCESSFUL": ("Authorization successful", "Autorisierung erfolgreich", "Autorisation réussie", "Autorizzazione riuscita", "Autorización correcta", "Autorisatie geslaagd", "Autoryzacja zakończona powodzeniem", "Autorização bem-sucedida", "Autorizace úspěšná", "Auktorisering lyckades"),
    "TRANSPORT_LAYER_ERROR": ("Transport layer error", "Fehler in der Transportschicht", "Erreur de la couche transport", "Errore del livello di trasporto", "Error de la capa de transporte", "Fout in de transportlaag", "Błąd warstwy transportowej", "Erro da camada de transporte", "Chyba transportní vrstvy", "Fel i transportlagret"),
    "HIGH_LEVEL_COMMUNICATION_ERROR": ("Communication error (high level)", "Kommunikationsfehler (High Level)", "Erreur de communication (haut niveau)", "Errore di comunicazione (alto livello)", "Error de comunicación (alto nivel)", "Communicatiefout (high level)", "Błąd komunikacji (wysoki poziom)", "Erro de comunicação (alto nível)", "Chyba komunikace (vysoká úroveň)", "Kommunikationsfel (hög nivå)"),
    "CONTRACT_SERVICE_NOT_SUPPORTED_ERROR": ("Contract service not supported", "Vertragsdienst nicht unterstützt", "Service contractuel non pris en charge", "Servizio contrattuale non supportato", "Servicio contractual no compatible", "Contractdienst niet ondersteund", "Usługa umowy nieobsługiwana", "Serviço contratual não suportado", "Smluvní služba není podporována", "Avtalstjänst stöds inte"),
    "AUTHORIZATION_ERROR": ("Authorization error", "Autorisierungsfehler", "Erreur d'autorisation", "Errore di autorizzazione", "Error de autorización", "Autorisatiefout", "Błąd autoryzacji", "Erro de autorização", "Chyba autorizace", "Auktoriseringsfel"),
    "AUTHORIZATION_TIMEOUT": ("Authorization timed out", "Zeitüberschreitung bei der Autorisierung", "Délai d'autorisation dépassé", "Timeout dell'autorizzazione", "Tiempo de autorización agotado", "Time-out bij autorisatie", "Przekroczono czas autoryzacji", "Tempo de autorização esgotado", "Vypršel časový limit autorizace", "Tidsgräns för auktorisering överskreds"),
    "NVM_READ_CERTIFICATE_ERROR": ("Error reading the certificate", "Fehler beim Lesen des Zertifikats", "Erreur de lecture du certificat", "Errore di lettura del certificato", "Error al leer el certificado", "Fout bij lezen van het certificaat", "Błąd odczytu certyfikatu", "Erro ao ler o certificado", "Chyba při čtení certifikátu", "Fel vid läsning av certifikatet"),
    "CERTIFICATE_UPDATE_ERROR": ("Error updating the certificate", "Fehler beim Aktualisieren des Zertifikats", "Erreur de mise à jour du certificat", "Errore di aggiornamento del certificato", "Error al actualizar el certificado", "Fout bij bijwerken van het certificaat", "Błąd aktualizacji certyfikatu", "Erro ao atualizar o certificado", "Chyba při aktualizaci certifikátu", "Fel vid uppdatering av certifikatet"),
    "XML_SECURITY_ERROR": ("XML security error", "XML-Sicherheitsfehler", "Erreur de sécurité XML", "Errore di sicurezza XML", "Error de seguridad XML", "XML-beveiligingsfout", "Błąd zabezpieczeń XML", "Erro de segurança XML", "Chyba zabezpečení XML", "XML-säkerhetsfel"),
    "NORMAL_PROGNOSE_BASED": ("Normal (forecast-based)", "Normal (prognosebasiert)", "Normal (basé sur une prévision)", "Normale (basato su previsione)", "Normal (basado en previsión)", "Normaal (op basis van prognose)", "Normalny (na podstawie prognozy)", "Normal (com base em previsão)", "Normální (podle prognózy)", "Normal (prognosbaserad)"),
    "STEP_BASED": ("Step-based", "Stufenbasiert", "Par paliers", "A gradini", "Por etapas", "Stapsgewijs", "Stopniowy", "Por etapas", "Stupňovitý", "Stegbaserad"),
    "PLC_MODE": ("PLC mode", "PLC-Modus", "Mode PLC", "Modalità PLC", "Modo PLC", "PLC-modus", "Tryb PLC", "Modo PLC", "Režim PLC", "PLC-läge"),
    "CONDUCTIVE": ("Wired", "Kabelgebunden", "Filaire", "Via cavo", "Por cable", "Via kabel", "Przewodowe", "Por cabo", "Kabelem", "Med kabel"),
    "INDUCTIVE": ("Inductive", "Induktiv", "Inductif", "Induttiva", "Inductiva", "Inductief", "Indukcyjne", "Indutivo", "Indukční", "Induktiv"),
    "SIGNAL_INVALID": ("Signal invalid", "Signal ungültig", "Signal non valide", "Segnale non valido", "Señal no válida", "Signaal ongeldig", "Nieprawidłowy sygnał", "Sinal inválido", "Neplatný signál", "Ogiltig signal"),
    "CHARGING": ("Charging", "Lädt", "En charge", "In carica", "Cargando", "Laden", "Ładowanie", "A carregar", "Nabíjí se", "Laddar"),
    "NOT_CHARGING": ("Not charging", "Lädt nicht", "Pas en charge", "Non in carica", "Sin cargar", "Laadt niet", "Brak ładowania", "Sem carregar", "Nenabíjí se", "Laddar inte"),
    "WAITING_FOR_CHARGING": ("Waiting to charge", "Wartet auf Ladung", "En attente de charge", "In attesa di ricarica", "Esperando para cargar", "Wacht op laden", "Oczekiwanie na ładowanie", "A aguardar carregamento", "Čeká na nabíjení", "Väntar på laddning"),
    "FINISHED_FULLY_CHARGED": ("Finished, fully charged", "Beendet, voll geladen", "Terminé, charge complète", "Terminata, carica completa", "Finalizada, carga completa", "Voltooid, volledig geladen", "Zakończono, w pełni naładowany", "Concluído, totalmente carregado", "Dokončeno, plně nabito", "Klar, fulladdad"),
    "FINISHED_NOT_FULL": ("Finished, not full", "Beendet, nicht voll", "Terminé, charge incomplète", "Terminata, non completa", "Finalizada, no completa", "Voltooid, niet vol", "Zakończono, nie w pełni", "Concluído, não totalmente", "Dokončeno, ne plně", "Klar, inte full"),
    "CHARGING_GOAL_REACHED": ("Charging target reached", "Ladeziel erreicht", "Objectif de charge atteint", "Obiettivo di ricarica raggiunto", "Objetivo de carga alcanzado", "Laaddoel bereikt", "Osiągnięto cel ładowania", "Objetivo de carregamento atingido", "Cíl nabíjení dosažen", "Laddningsmål nått"),
    "END_REQUESTED_BY_DRIVER": ("Ended by the driver", "Vom Fahrer beendet", "Arrêtée par le conducteur", "Terminata dal conducente", "Finalizada por el conductor", "Beëindigd door de bestuurder", "Zakończone przez kierowcę", "Terminado pelo condutor", "Ukončeno řidičem", "Avslutad av föraren"),
    "CONNECTOR_REMOVED": ("Connector unplugged", "Stecker gezogen", "Connecteur débranché", "Connettore scollegato", "Conector desenchufado", "Stekker verwijderd", "Wtyczka odłączona", "Conector desligado", "Konektor odpojen", "Kontakten urdragen"),
    "POWERGRID_FAILED": ("Power grid failed", "Stromnetz ausgefallen", "Panne du réseau électrique", "Guasto della rete elettrica", "Fallo de la red eléctrica", "Stroomnet uitgevallen", "Awaria sieci energetycznej", "Falha da rede elétrica", "Výpadek elektrické sítě", "Elnätet föll bort"),
    "HV_SYSTEM_FAILURE": ("High-voltage system fault", "Fehler im Hochvoltsystem", "Défaut du système haute tension", "Guasto del sistema ad alta tensione", "Fallo del sistema de alto voltaje", "Storing hoogspanningssysteem", "Usterka układu wysokiego napięcia", "Falha do sistema de alta tensão", "Porucha vysokonapěťového systému", "Fel i högspänningssystemet"),
    "CHARGING_STATION_FAILURE": ("Charging station fault", "Fehler der Ladestation", "Défaut de la borne de recharge", "Guasto della stazione di ricarica", "Fallo de la estación de carga", "Storing laadstation", "Usterka stacji ładowania", "Falha do posto de carregamento", "Porucha nabíjecí stanice", "Fel på laddstationen"),
    "PARKING_LOCK_FAILED": ("Parking lock failed", "Parksperre fehlgeschlagen", "Échec du verrouillage de stationnement", "Blocco di parcheggio non riuscito", "Fallo del bloqueo de aparcamiento", "Parkeerblokkering mislukt", "Blokada parkingowa nie powiodła się", "Falha do bloqueio de estacionamento", "Parkovací zámek selhal", "Parkeringslåset misslyckades"),
    "NO_PARKING_LOCK": ("No parking lock", "Keine Parksperre", "Pas de verrouillage de stationnement", "Nessun blocco di parcheggio", "Sin bloqueo de aparcamiento", "Geen parkeerblokkering", "Brak blokady parkingowej", "Sem bloqueio de estacionamento", "Bez parkovacího zámku", "Inget parkeringslås"),
    "DIRECT_CHG_ONCE_NOT_ACTIVE": ("One-time immediate charging inactive", "Einmalig sofort laden nicht aktiv", "Charge immédiate unique inactive", "Ricarica immediata singola non attiva", "Carga inmediata única inactiva", "Eenmalig direct laden niet actief", "Jednorazowe natychmiastowe ładowanie nieaktywne", "Carregamento imediato único inativo", "Jednorázové okamžité nabíjení neaktivní", "Engångs direktladdning inte aktiv"),
    "DIRECT_CHG_ONCE_ACTIVE": ("One-time immediate charging active", "Einmalig sofort laden aktiv", "Charge immédiate unique active", "Ricarica immediata singola attiva", "Carga inmediata única activa", "Eenmalig direct laden actief", "Jednorazowe natychmiastowe ładowanie aktywne", "Carregamento imediato único ativo", "Jednorázové okamžité nabíjení aktivní", "Engångs direktladdning aktiv"),
    "SUCCESS": ("Successful", "Erfolgreich", "Réussi", "Riuscito", "Correcto", "Geslaagd", "Zakończone powodzeniem", "Bem-sucedido", "Úspěšné", "Lyckades"),
    "FAILED": ("Failed", "Fehlgeschlagen", "Échoué", "Non riuscito", "Fallido", "Mislukt", "Niepowodzenie", "Falhou", "Selhalo", "Misslyckades"),
    "AC_TYPE1PLUG": ("AC Type 1 plug", "AC Typ-1-Stecker", "Prise AC type 1", "Spina AC tipo 1", "Conector AC tipo 1", "AC type 1-stekker", "Wtyczka AC typu 1", "Ficha AC tipo 1", "Konektor AC typ 1", "AC typ 1-kontakt"),
    "AC_TYPE2PLUG": ("AC Type 2 plug", "AC Typ-2-Stecker", "Prise AC type 2", "Spina AC tipo 2", "Conector AC tipo 2", "AC type 2-stekker", "Wtyczka AC typu 2", "Ficha AC tipo 2", "Konektor AC typ 2", "AC typ 2-kontakt"),
    "NO_DEVIATION": ("No deviation", "Keine Abweichung", "Aucun écart", "Nessuna deviazione", "Sin desviación", "Geen afwijking", "Brak odchylenia", "Sem desvio", "Bez odchylky", "Ingen avvikelse"),
    "DC_DYNAMIC_MALIBU": ("DC dynamic (Malibu)", "DC dynamisch (Malibu)", "DC dynamique (Malibu)", "DC dinamico (Malibu)", "CC dinámica (Malibu)", "DC dynamisch (Malibu)", "DC dynamiczne (Malibu)", "DC dinâmico (Malibu)", "DC dynamické (Malibu)", "DC dynamisk (Malibu)"),
    "DC_DYNAMIC_MIN_SOE_PROTECTION": ("DC dynamic (minimum charge protection)", "DC dynamisch (Schutz Mindestladung)", "DC dynamique (protection de charge minimale)", "DC dinamico (protezione carica minima)", "CC dinámica (protección de carga mínima)", "DC dynamisch (bescherming minimale lading)", "DC dynamiczne (ochrona minimalnego naładowania)", "DC dinâmico (proteção de carga mínima)", "DC dynamické (ochrana minimálního nabití)", "DC dynamisk (skydd för minsta laddning)"),
    "IMMEDIATE_CHARGING_NOT_SUPPORTED_BY_EVSE": ("Immediate charging not supported by the charging station", "Sofortladen von der Ladestation nicht unterstützt", "Charge immédiate non prise en charge par la borne", "Ricarica immediata non supportata dalla stazione", "Carga inmediata no compatible con la estación", "Direct laden niet ondersteund door het laadstation", "Natychmiastowe ładowanie nieobsługiwane przez stację", "Carregamento imediato não suportado pelo posto", "Okamžité nabíjení stanice nepodporuje", "Direktladdning stöds inte av laddstationen"),
    "IMMEDIATE_CHARGING_WAKE_UP_LIMIT_REACHED": ("Immediate charging: wake-up limit reached", "Sofortladen: Weckgrenze erreicht", "Charge immédiate : limite de réveil atteinte", "Ricarica immediata: limite di risveglio raggiunto", "Carga inmediata: límite de activación alcanzado", "Direct laden: wekgrens bereikt", "Natychmiastowe ładowanie: osiągnięto limit wybudzeń", "Carregamento imediato: limite de ativação atingido", "Okamžité nabíjení: dosažen limit probuzení", "Direktladdning: väckningsgräns nådd"),
    "IMMEDIATE_CHARGING": ("Immediate charging", "Sofortladen", "Charge immédiate", "Ricarica immediata", "Carga inmediata", "Direct laden", "Natychmiastowe ładowanie", "Carregamento imediato", "Okamžité nabíjení", "Direktladdning"),
    "CHARGING_TIMESLOT": ("Charging window", "Ladezeitfenster", "Plage de charge", "Finestra di ricarica", "Franja de carga", "Laadvenster", "Okno ładowania", "Janela de carregamento", "Nabíjecí okno", "Laddfönster"),
    "CHARGING_IN_TIMESLOT": ("Charging in the time window", "Laden im Zeitfenster", "Charge dans la plage horaire", "Ricarica nella finestra oraria", "Carga en la franja horaria", "Laden binnen het tijdvenster", "Ładowanie w oknie czasowym", "Carregamento na janela horária", "Nabíjení v časovém okně", "Laddning inom tidsfönstret"),
    "SMART_CHARGING": ("Smart charging", "Smart Charging", "Recharge intelligente", "Ricarica intelligente", "Carga inteligente", "Slim laden", "Inteligentne ładowanie", "Carregamento inteligente", "Chytré nabíjení", "Smart laddning"),
    "DC_DYNAMIC": ("DC dynamic", "DC dynamisch", "DC dynamique", "DC dinamico", "CC dinámica", "DC dynamisch", "DC dynamiczne", "DC dinâmico", "DC dynamické", "DC dynamisk"),
    "BIDIRECTIONAL_CHARGING": ("Bidirectional charging", "Bidirektionales Laden", "Charge bidirectionnelle", "Ricarica bidirezionale", "Carga bidireccional", "Bidirectioneel laden", "Ładowanie dwukierunkowe", "Carregamento bidirecional", "Obousměrné nabíjení", "Dubbelriktad laddning"),
    "NO_CHARGING": ("Not charging", "Lädt nicht", "Pas en charge", "Non in carica", "Sin cargar", "Laadt niet", "Brak ładowania", "Sem carregar", "Nenabíjí se", "Laddar inte"),
    "CHARGING_WINDOW": ("Charging window", "Ladezeitfenster", "Plage de charge", "Finestra di ricarica", "Franja de carga", "Laadvenster", "Okno ładowania", "Janela de carregamento", "Nabíjecí okno", "Laddfönster"),
    "NO_PRESELECTION": ("No preselection", "Keine Vorauswahl", "Aucune présélection", "Nessuna preselezione", "Sin preselección", "Geen voorkeuze", "Brak wstępnego wyboru", "Sem pré-seleção", "Bez předvolby", "Inget förval"),
    "NO_CHARGING_END_SINCE_IPF_WAKEUP": ("No charging end since wake-up", "Kein Ladeende seit dem Aufwecken", "Aucune fin de charge depuis le réveil", "Nessuna fine ricarica dal risveglio", "Sin fin de carga desde la activación", "Geen laadeinde sinds het ontwaken", "Brak zakończenia ładowania od wybudzenia", "Sem fim de carregamento desde a ativação", "Žádné ukončení nabíjení od probuzení", "Inget laddningsslut sedan väckning"),
    "CUSTOMER_INTERACTION_HMI_UNPLUG_CABLE_ONCE": ("Driver: cable released once (car)", "Fahrer: Kabel einmalig entriegelt (Fahrzeug)", "Conducteur : câble libéré une fois (véhicule)", "Conducente: cavo sbloccato una volta (veicolo)", "Conductor: cable liberado una vez (vehículo)", "Bestuurder: kabel eenmalig ontgrendeld (auto)", "Kierowca: kabel jednorazowo odblokowany (pojazd)", "Condutor: cabo libertado uma vez (veículo)", "Řidič: kabel jednorázově odemčen (vozidlo)", "Förare: kabeln frigjord en gång (bil)"),
    "CUSTOMER_INTERACTION_HMI_CHARGING_TARGET_REACHED": ("Driver: charging target reached (car)", "Fahrer: Ladeziel erreicht (Fahrzeug)", "Conducteur : objectif de charge atteint (véhicule)", "Conducente: obiettivo di ricarica raggiunto (veicolo)", "Conductor: objetivo de carga alcanzado (vehículo)", "Bestuurder: laaddoel bereikt (auto)", "Kierowca: osiągnięto cel ładowania (pojazd)", "Condutor: objetivo de carregamento atingido (veículo)", "Řidič: cíl nabíjení dosažen (vozidlo)", "Förare: laddningsmål nått (bil)"),
    "CUSTOMER_INTERACTION_APP_UNPLUG_CABLE_ONCE": ("Driver: cable released once (app)", "Fahrer: Kabel einmalig entriegelt (App)", "Conducteur : câble libéré une fois (app)", "Conducente: cavo sbloccato una volta (app)", "Conductor: cable liberado una vez (app)", "Bestuurder: kabel eenmalig ontgrendeld (app)", "Kierowca: kabel jednorazowo odblokowany (aplikacja)", "Condutor: cabo libertado uma vez (app)", "Řidič: kabel jednorázově odemčen (aplikace)", "Förare: kabeln frigjord en gång (app)"),
    "CUSTOMER_INTERACTION_APP_CHARGING_TARGET_REACHED": ("Driver: charging target reached (app)", "Fahrer: Ladeziel erreicht (App)", "Conducteur : objectif de charge atteint (app)", "Conducente: obiettivo di ricarica raggiunto (app)", "Conductor: objetivo de carga alcanzado (app)", "Bestuurder: laaddoel bereikt (app)", "Kierowca: osiągnięto cel ładowania (aplikacja)", "Condutor: objetivo de carregamento atingido (app)", "Řidič: cíl nabíjení dosažen (aplikace)", "Förare: laddningsmål nått (app)"),
    "CUSTOMER_INTERACTION_CHARGING_STOP_VIA_CHARGING_STOP_BUTTON_SOCKET": ("Driver: stop button on the charging socket", "Fahrer: Stopptaste an der Ladebuchse", "Conducteur : bouton d'arrêt sur la prise de charge", "Conducente: pulsante di arresto sulla presa di ricarica", "Conductor: botón de parada en la toma de carga", "Bestuurder: stopknop op de laadaansluiting", "Kierowca: przycisk stop przy gnieździe ładowania", "Condutor: botão de paragem na tomada de carregamento", "Řidič: tlačítko stop na nabíjecí zásuvce", "Förare: stoppknapp vid laddningsuttaget"),
    "CUSTOMER_INTERACTION_AC_CHARGING_STOP_VIA_KEY": ("Driver: AC charging stopped by key", "Fahrer: AC-Laden per Schlüssel beendet", "Conducteur : charge AC arrêtée par la clé", "Conducente: ricarica AC arrestata con la chiave", "Conductor: carga CA detenida con la llave", "Bestuurder: AC-laden gestopt met de sleutel", "Kierowca: ładowanie AC zatrzymane kluczykiem", "Condutor: carregamento AC parado pela chave", "Řidič: nabíjení AC ukončeno klíčem", "Förare: AC-laddning stoppad med nyckeln"),
    "CUSTOMER_INTERACTION_AC_CHARGING_STOP_WALLBOX": ("Driver: AC charging stopped at the wallbox", "Fahrer: AC-Laden an der Wallbox beendet", "Conducteur : charge AC arrêtée à la wallbox", "Conducente: ricarica AC arrestata alla wallbox", "Conductor: carga CA detenida en el wallbox", "Bestuurder: AC-laden gestopt bij de wallbox", "Kierowca: ładowanie AC zatrzymane na wallboksie", "Condutor: carregamento AC parado na wallbox", "Řidič: nabíjení AC ukončeno na wallboxu", "Förare: AC-laddning stoppad vid laddboxen"),
    "CUSTOMER_INTERACTION_AC_CHARGING_STOP_LATCH": ("Driver: AC charging stopped by the latch", "Fahrer: AC-Laden per Verriegelung beendet", "Conducteur : charge AC arrêtée par le verrou", "Conducente: ricarica AC arrestata dal blocco", "Conductor: carga CA detenida por el bloqueo", "Bestuurder: AC-laden gestopt via de vergrendeling", "Kierowca: ładowanie AC zatrzymane blokadą", "Condutor: carregamento AC parado pelo trinco", "Řidič: nabíjení AC ukončeno západkou", "Förare: AC-laddning stoppad via låsningen"),
    "HV_BATTERY_FULLY_CHARGED": ("High-voltage battery fully charged", "Hochvoltbatterie voll geladen", "Batterie haute tension entièrement chargée", "Batteria ad alta tensione completamente carica", "Batería de alto voltaje totalmente cargada", "Hoogspanningsaccu volledig geladen", "Akumulator wysokonapięciowy w pełni naładowany", "Bateria de alta tensão totalmente carregada", "Vysokonapěťová baterie plně nabita", "Högspänningsbatteriet fulladdat"),
    "INFRASTRUCTURE_PHYSICAL_LIMITS_REACHED_TARGET_SOE_NOT_REACHABLE": ("Charging station: limit reached, target not reachable", "Ladestation: Grenze erreicht, Ladeziel nicht erreichbar", "Borne : limite atteinte, objectif inatteignable", "Stazione: limite raggiunto, obiettivo non raggiungibile", "Estación: límite alcanzado, objetivo inalcanzable", "Laadstation: grens bereikt, laaddoel niet haalbaar", "Stacja: osiągnięto limit, cel nieosiągalny", "Posto: limite atingido, objetivo inalcançável", "Stanice: dosažen limit, cíl nedosažitelný", "Laddstation: gräns nådd, målet kan inte nås"),
    "INFRASTRUCTURE_DC_CCS_EVSE_SHUTDOWN": ("Charging station: DC CCS shut down", "Ladestation: DC-CCS abgeschaltet", "Borne : arrêt DC CCS", "Stazione: spegnimento DC CCS", "Estación: apagado CC CCS", "Laadstation: DC-CCS uitgeschakeld", "Stacja: wyłączenie DC CCS", "Posto: desligamento DC CCS", "Stanice: vypnutí DC CCS", "Laddstation: DC CCS avstängd"),
    "INFRASTRUCTURE_DC_GBT_CST_MESSAGE": ("Charging station: DC GB/T stop message", "Ladestation: DC-GB/T-Stoppmeldung", "Borne : message d'arrêt DC GB/T", "Stazione: messaggio di arresto DC GB/T", "Estación: mensaje de parada CC GB/T", "Laadstation: DC-GB/T-stopmelding", "Stacja: komunikat zatrzymania DC GB/T", "Posto: mensagem de paragem DC GB/T", "Stanice: zpráva o zastavení DC GB/T", "Laddstation: DC GB/T-stoppmeddelande"),
    "INFRASTRUCTURE_DC_CCS_STATIC_PILOT": ("Charging station: DC CCS static pilot signal", "Ladestation: DC-CCS statisches Pilotsignal", "Borne : signal pilote statique DC CCS", "Stazione: segnale pilota statico DC CCS", "Estación: señal piloto estática CC CCS", "Laadstation: DC-CCS statisch pilotsignaal", "Stacja: statyczny sygnał pilota DC CCS", "Posto: sinal piloto estático DC CCS", "Stanice: statický pilotní signál DC CCS", "Laddstation: DC CCS statisk pilotsignal"),
    "INFRASTRUCTURE_DC_CCS_COMMUNICATION_ERROR_FAILED_RESPONSE_CODE": ("Charging station: DC CCS communication error (response code)", "Ladestation: DC-CCS Kommunikationsfehler (Antwortcode)", "Borne : erreur de communication DC CCS (code de réponse)", "Stazione: errore di comunicazione DC CCS (codice di risposta)", "Estación: error de comunicación CC CCS (código de respuesta)", "Laadstation: DC-CCS-communicatiefout (antwoordcode)", "Stacja: błąd komunikacji DC CCS (kod odpowiedzi)", "Posto: erro de comunicação DC CCS (código de resposta)", "Stanice: chyba komunikace DC CCS (kód odpovědi)", "Laddstation: DC CCS-kommunikationsfel (svarskod)"),
    "INFRASTRUCTURE_DC_CCS_COMMUNICATION_ERROR": ("Charging station: DC CCS communication error", "Ladestation: DC-CCS Kommunikationsfehler", "Borne : erreur de communication DC CCS", "Stazione: errore di comunicazione DC CCS", "Estación: error de comunicación CC CCS", "Laadstation: DC-CCS-communicatiefout", "Stacja: błąd komunikacji DC CCS", "Posto: erro de comunicação DC CCS", "Stanice: chyba komunikace DC CCS", "Laddstation: DC CCS-kommunikationsfel"),
    "INFRASTRUCTURE_NO_ENERGY_TRANSFER": ("Charging station: no energy transfer", "Ladestation: keine Energieübertragung", "Borne : aucun transfert d'énergie", "Stazione: nessun trasferimento di energia", "Estación: sin transferencia de energía", "Laadstation: geen energieoverdracht", "Stacja: brak przepływu energii", "Posto: sem transferência de energia", "Stanice: žádný přenos energie", "Laddstation: ingen energiöverföring"),
    "INFRASTRUCTURE_DC_CCS_PRECHARGE_ERROR": ("Charging station: DC CCS precharge error", "Ladestation: DC-CCS Fehler beim Vorladen", "Borne : erreur de précharge DC CCS", "Stazione: errore di precarica DC CCS", "Estación: error de precarga CC CCS", "Laadstation: DC-CCS-voorlaadfout", "Stacja: błąd ładowania wstępnego DC CCS", "Posto: erro de pré-carga DC CCS", "Stanice: chyba předběžného nabití DC CCS", "Laddstation: DC CCS-förladdningsfel"),
    "INFRASTRUCTURE_DC_CCS_EVSE_MALFUNCTION_EMERGENCY_SHUTDOWN": ("Charging station: DC CCS fault, emergency shutdown", "Ladestation: DC-CCS Störung, Notabschaltung", "Borne : défaut DC CCS, arrêt d'urgence", "Stazione: guasto DC CCS, spegnimento di emergenza", "Estación: fallo CC CCS, apagado de emergencia", "Laadstation: DC-CCS-storing, noodstop", "Stacja: usterka DC CCS, wyłączenie awaryjne", "Posto: avaria DC CCS, paragem de emergência", "Stanice: porucha DC CCS, nouzové vypnutí", "Laddstation: DC CCS-fel, nödavstängning"),
    "INFRASTRUCTURE_STATION_NOT_COMPATIBLE": ("Charging station not compatible", "Ladestation nicht kompatibel", "Borne non compatible", "Stazione di ricarica non compatibile", "Estación de carga no compatible", "Laadstation niet compatibel", "Stacja ładowania niekompatybilna", "Posto de carregamento não compatível", "Nabíjecí stanice není kompatibilní", "Laddstationen är inte kompatibel"),
    "INFRASTRUCTURE_CHARGE_READINESS_TIMEOUT": ("Charging station: charge readiness timed out", "Ladestation: Zeitüberschreitung Ladebereitschaft", "Borne : délai de disponibilité de charge dépassé", "Stazione: timeout di disponibilità alla ricarica", "Estación: tiempo de disponibilidad de carga agotado", "Laadstation: time-out laadgereedheid", "Stacja: przekroczono czas gotowości do ładowania", "Posto: tempo de disponibilidade de carregamento esgotado", "Stanice: vypršela připravenost k nabíjení", "Laddstation: tidsgräns för laddberedskap överskreds"),
    "INFRASTRUCTURE_AC_MAXIMUM_GRID_ERROR_REACHED": ("Charging station: maximum AC grid errors reached", "Ladestation: maximale AC-Netzfehler erreicht", "Borne : nombre maximal d'erreurs réseau AC atteint", "Stazione: numero massimo di errori di rete AC raggiunto", "Estación: máximo de errores de red CA alcanzado", "Laadstation: maximaal aantal AC-netfouten bereikt", "Stacja: osiągnięto maksymalną liczbę błędów sieci AC", "Posto: máximo de erros de rede AC atingido", "Stanice: dosažen maximální počet chyb sítě AC", "Laddstation: max antal AC-nätfel nått"),
    "INFRASTRUCTURE_DC_GBT_ISOLATION_ERROR_DETECTED": ("Charging station: DC GB/T insulation fault", "Ladestation: DC-GB/T Isolationsfehler", "Borne : défaut d'isolement DC GB/T", "Stazione: errore di isolamento DC GB/T", "Estación: fallo de aislamiento CC GB/T", "Laadstation: DC-GB/T-isolatiefout", "Stacja: błąd izolacji DC GB/T", "Posto: falha de isolamento DC GB/T", "Stanice: chyba izolace DC GB/T", "Laddstation: DC GB/T-isolationsfel"),
    "INFRASTRUCTURE_AUTHENTIFICATION_ERROR": ("Charging station: authentication error", "Ladestation: Authentifizierungsfehler", "Borne : erreur d'authentification", "Stazione: errore di autenticazione", "Estación: error de autenticación", "Laadstation: authenticatiefout", "Stacja: błąd uwierzytelniania", "Posto: erro de autenticação", "Stanice: chyba ověření", "Laddstation: autentiseringsfel"),
    "VEHICLE_HV_BATTERY_ERROR": ("Vehicle: high-voltage battery fault", "Fahrzeug: Fehler Hochvoltbatterie", "Véhicule : défaut de la batterie haute tension", "Veicolo: guasto della batteria ad alta tensione", "Vehículo: fallo de la batería de alto voltaje", "Voertuig: storing hoogspanningsaccu", "Pojazd: usterka akumulatora wysokonapięciowego", "Veículo: falha da bateria de alta tensão", "Vozidlo: porucha vysokonapěťové baterie", "Fordon: fel på högspänningsbatteriet"),
    "VEHICLE_HV_BATTERY_COMMUNICATION_ERROR": ("Vehicle: high-voltage battery communication error", "Fahrzeug: Kommunikationsfehler Hochvoltbatterie", "Véhicule : erreur de communication de la batterie haute tension", "Veicolo: errore di comunicazione della batteria ad alta tensione", "Vehículo: error de comunicación de la batería de alto voltaje", "Voertuig: communicatiefout hoogspanningsaccu", "Pojazd: błąd komunikacji akumulatora wysokonapięciowego", "Veículo: erro de comunicação da bateria de alta tensão", "Vozidlo: chyba komunikace vysokonapěťové baterie", "Fordon: kommunikationsfel högspänningsbatteri"),
    "VEHICLE_DCDC_ERROR": ("Vehicle: DC/DC converter fault", "Fahrzeug: Fehler DC/DC-Wandler", "Véhicule : défaut du convertisseur DC/DC", "Veicolo: guasto del convertitore DC/DC", "Vehículo: fallo del convertidor CC/CC", "Voertuig: storing DC/DC-omvormer", "Pojazd: usterka przetwornicy DC/DC", "Veículo: falha do conversor DC/DC", "Vozidlo: porucha měniče DC/DC", "Fordon: fel på DC/DC-omvandlaren"),
    "VEHICLE_PLC_BOOTUP_ERROR": ("Vehicle: PLC startup error", "Fahrzeug: PLC-Startfehler", "Véhicule : erreur de démarrage PLC", "Veicolo: errore di avvio PLC", "Vehículo: error de arranque PLC", "Voertuig: PLC-opstartfout", "Pojazd: błąd uruchamiania PLC", "Veículo: erro de arranque PLC", "Vozidlo: chyba spuštění PLC", "Fordon: PLC-startfel"),
    "VEHICLE_HV_BATTERY_MAXIMAL_CURRENT_OVERSHOOT": ("Vehicle: high-voltage battery maximum current exceeded", "Fahrzeug: Maximalstrom Hochvoltbatterie überschritten", "Véhicule : courant maximal de la batterie haute tension dépassé", "Veicolo: corrente massima della batteria ad alta tensione superata", "Vehículo: corriente máxima de la batería de alto voltaje superada", "Voertuig: maximale stroom hoogspanningsaccu overschreden", "Pojazd: przekroczono maksymalny prąd akumulatora wysokonapięciowego", "Veículo: corrente máxima da bateria de alta tensão excedida", "Vozidlo: překročen maximální proud vysokonapěťové baterie", "Fordon: högspänningsbatteriets maxström överskreds"),
    "VEHICLE_ISOLATION_ERROR": ("Vehicle: insulation fault", "Fahrzeug: Isolationsfehler", "Véhicule : défaut d'isolement", "Veicolo: errore di isolamento", "Vehículo: fallo de aislamiento", "Voertuig: isolatiefout", "Pojazd: błąd izolacji", "Veículo: falha de isolamento", "Vozidlo: chyba izolace", "Fordon: isolationsfel"),
    "VEHICLE_MAXIMUM_TEMPERATURE_CHARGING_SOCKET_OVERSHOOT": ("Vehicle: charging socket overheated", "Fahrzeug: Ladebuchse überhitzt", "Véhicule : prise de charge en surchauffe", "Veicolo: presa di ricarica surriscaldata", "Vehículo: toma de carga sobrecalentada", "Voertuig: laadaansluiting oververhit", "Pojazd: przegrzane gniazdo ładowania", "Veículo: tomada de carregamento sobreaquecida", "Vozidlo: přehřátá nabíjecí zásuvka", "Fordon: laddningsuttaget överhettat"),
    "VEHICLE_LT3_OVERSHOOT": ("Vehicle: LT3 limit exceeded", "Fahrzeug: LT3-Grenzwert überschritten", "Véhicule : limite LT3 dépassée", "Veicolo: limite LT3 superato", "Vehículo: límite LT3 superado", "Voertuig: LT3-grens overschreden", "Pojazd: przekroczono limit LT3", "Veículo: limite LT3 excedido", "Vozidlo: překročen limit LT3", "Fordon: LT3-gräns överskreds"),
    "VEHICLE_FUSI_CABLE_LOCK_ERROR": ("Vehicle: cable lock fault (safety)", "Fahrzeug: Fehler Kabelverriegelung (Sicherheit)", "Véhicule : défaut de verrouillage du câble (sécurité)", "Veicolo: errore di bloccaggio del cavo (sicurezza)", "Vehículo: fallo de bloqueo del cable (seguridad)", "Voertuig: fout kabelvergrendeling (veiligheid)", "Pojazd: błąd blokady kabla (bezpieczeństwo)", "Veículo: falha de trancamento do cabo (segurança)", "Vozidlo: chyba zámku kabelu (bezpečnost)", "Fordon: fel på kabellåset (säkerhet)"),
    "VEHICLE_PLC_COMMUNICATION_NOT_POSSIBLE": ("Vehicle: PLC communication not possible", "Fahrzeug: PLC-Kommunikation nicht möglich", "Véhicule : communication PLC impossible", "Veicolo: comunicazione PLC non possibile", "Vehículo: comunicación PLC imposible", "Voertuig: PLC-communicatie niet mogelijk", "Pojazd: komunikacja PLC niemożliwa", "Veículo: comunicação PLC impossível", "Vozidlo: komunikace PLC není možná", "Fordon: PLC-kommunikation inte möjlig"),
    "FLEXCHARGER_GRID_ERROR": ("Flexible charger: grid error", "Flexible Ladeeinheit: Netzfehler", "Chargeur flexible : erreur réseau", "Caricatore flessibile: errore di rete", "Cargador flexible: error de red", "Flexibele lader: netfout", "Ładowarka mobilna: błąd sieci", "Carregador flexível: erro de rede", "Flexibilní nabíječka: chyba sítě", "Flexibel laddare: nätfel"),
    "FLEXCHARGER_GRID_TEMPERATURE_ERROR": ("Flexible charger: grid temperature error", "Flexible Ladeeinheit: Temperaturfehler Netz", "Chargeur flexible : erreur de température réseau", "Caricatore flessibile: errore di temperatura di rete", "Cargador flexible: error de temperatura de red", "Flexibele lader: temperatuurfout net", "Ładowarka mobilna: błąd temperatury sieci", "Carregador flexível: erro de temperatura da rede", "Flexibilní nabíječka: chyba teploty sítě", "Flexibel laddare: temperaturfel nät"),
    "FLEXCHARGER_EVSE_TEMPERATURE_ERROR": ("Flexible charger: temperature error", "Flexible Ladeeinheit: Temperaturfehler", "Chargeur flexible : erreur de température", "Caricatore flessibile: errore di temperatura", "Cargador flexible: error de temperatura", "Flexibele lader: temperatuurfout", "Ładowarka mobilna: błąd temperatury", "Carregador flexível: erro de temperatura", "Flexibilní nabíječka: chyba teploty", "Flexibel laddare: temperaturfel"),
    "FLEXCHARGER_EVSE_INTERNAL_ERROR": ("Flexible charger: internal error", "Flexible Ladeeinheit: interner Fehler", "Chargeur flexible : erreur interne", "Caricatore flessibile: errore interno", "Cargador flexible: error interno", "Flexibele lader: interne fout", "Ładowarka mobilna: błąd wewnętrzny", "Carregador flexível: erro interno", "Flexibilní nabíječka: interní chyba", "Flexibel laddare: internt fel"),
    "FLEXCHARGER_AC_DC_FAULT_CURRENT": ("Flexible charger: AC/DC fault current", "Flexible Ladeeinheit: AC/DC-Fehlerstrom", "Chargeur flexible : courant de défaut AC/DC", "Caricatore flessibile: corrente di guasto AC/DC", "Cargador flexible: corriente de fuga CA/CC", "Flexibele lader: AC/DC-lekstroom", "Ładowarka mobilna: prąd upływu AC/DC", "Carregador flexível: corrente de fuga AC/DC", "Flexibilní nabíječka: chybový proud AC/DC", "Flexibel laddare: AC/DC-felström"),
    "FLEXCHARGER_INCORRECT_REACTION_TIME_PWM": ("Flexible charger: incorrect PWM reaction time", "Flexible Ladeeinheit: falsche Reaktionszeit PWM", "Chargeur flexible : temps de réaction PWM incorrect", "Caricatore flessibile: tempo di reazione PWM errato", "Cargador flexible: tiempo de reacción PWM incorrecto", "Flexibele lader: onjuiste PWM-reactietijd", "Ładowarka mobilna: nieprawidłowy czas reakcji PWM", "Carregador flexível: tempo de reação PWM incorreto", "Flexibilní nabíječka: nesprávná reakční doba PWM", "Flexibel laddare: felaktig PWM-reaktionstid"),
    "FLEXCHARGER_CHARGING_CURRENT_TOO_HIGH": ("Flexible charger: charging current too high", "Flexible Ladeeinheit: Ladestrom zu hoch", "Chargeur flexible : courant de charge trop élevé", "Caricatore flessibile: corrente di ricarica troppo alta", "Cargador flexible: corriente de carga demasiado alta", "Flexibele lader: laadstroom te hoog", "Ładowarka mobilna: zbyt wysoki prąd ładowania", "Carregador flexível: corrente de carregamento demasiado alta", "Flexibilní nabíječka: příliš vysoký nabíjecí proud", "Flexibel laddare: för hög laddström"),
    "FLEXCHARGER_PE_FAULT": ("Flexible charger: protective earth fault", "Flexible Ladeeinheit: Schutzleiterfehler", "Chargeur flexible : défaut de terre", "Caricatore flessibile: guasto del conduttore di terra", "Cargador flexible: fallo del conductor de tierra", "Flexibele lader: aardingsfout", "Ładowarka mobilna: błąd przewodu ochronnego", "Carregador flexível: falha do condutor de terra", "Flexibilní nabíječka: chyba ochranného vodiče", "Flexibel laddare: jordfel"),
    "INFRASTRUCTURE_INEFFICIENT_CHARGING_LOW_POWER": ("Charging station: inefficient charging, too little power", "Ladestation: ineffizientes Laden, zu wenig Leistung", "Borne : charge inefficace, puissance trop faible", "Stazione: ricarica inefficiente, potenza troppo bassa", "Estación: carga ineficiente, potencia insuficiente", "Laadstation: inefficiënt laden, te weinig vermogen", "Stacja: nieefektywne ładowanie, za mała moc", "Posto: carregamento ineficiente, potência insuficiente", "Stanice: neefektivní nabíjení, příliš nízký výkon", "Laddstation: ineffektiv laddning, för låg effekt"),
    "VEHICLE_CABLE_LOCKING_ERROR": ("Vehicle: cable locking error", "Fahrzeug: Fehler Kabelverriegelung", "Véhicule : erreur de verrouillage du câble", "Veicolo: errore di bloccaggio del cavo", "Vehículo: error de bloqueo del cable", "Voertuig: fout kabelvergrendeling", "Pojazd: błąd blokady kabla", "Veículo: erro de trancamento do cabo", "Vozidlo: chyba zamčení kabelu", "Fordon: fel vid kabellåsning"),
    "VEHICLE_CONFIGURATION_ERROR_400V_800V": ("Vehicle: 400 V / 800 V configuration error", "Fahrzeug: Konfigurationsfehler 400 V / 800 V", "Véhicule : erreur de configuration 400 V / 800 V", "Veicolo: errore di configurazione 400 V / 800 V", "Vehículo: error de configuración 400 V / 800 V", "Voertuig: configuratiefout 400 V / 800 V", "Pojazd: błąd konfiguracji 400 V / 800 V", "Veículo: erro de configuração 400 V / 800 V", "Vozidlo: chyba konfigurace 400 V / 800 V", "Fordon: konfigurationsfel 400 V / 800 V"),
    "INFRASTRUCTURE_NO_AC_VOLTAGE": ("Charging station: no AC voltage", "Ladestation: keine AC-Spannung", "Borne : pas de tension AC", "Stazione: nessuna tensione AC", "Estación: sin tensión CA", "Laadstation: geen AC-spanning", "Stacja: brak napięcia AC", "Posto: sem tensão AC", "Stanice: žádné napětí AC", "Laddstation: ingen AC-spänning"),
    "PLUG_AND_CHARGE_TLS_ERROR": ("Plug & Charge: TLS error", "Plug & Charge: TLS-Fehler", "Plug & Charge : erreur TLS", "Plug & Charge: errore TLS", "Plug & Charge: error TLS", "Plug & Charge: TLS-fout", "Plug & Charge: błąd TLS", "Plug & Charge: erro TLS", "Plug & Charge: chyba TLS", "Plug & Charge: TLS-fel"),
    "PLUG_AND_CHARGE_CONTRACT_ERROR": ("Plug & Charge: contract error", "Plug & Charge: Vertragsfehler", "Plug & Charge : erreur de contrat", "Plug & Charge: errore del contratto", "Plug & Charge: error de contrato", "Plug & Charge: contractfout", "Plug & Charge: błąd umowy", "Plug & Charge: erro de contrato", "Plug & Charge: chyba smlouvy", "Plug & Charge: avtalsfel"),
    "SMART_CHARGING_TARIFF_SELECTION_INVALID": ("Smart charging: tariff selection invalid", "Smart Charging: Tarifauswahl ungültig", "Recharge intelligente : choix de tarif non valide", "Ricarica intelligente: selezione tariffa non valida", "Carga inteligente: selección de tarifa no válida", "Slim laden: tariefkeuze ongeldig", "Inteligentne ładowanie: nieprawidłowy wybór taryfy", "Carregamento inteligente: seleção de tarifa inválida", "Chytré nabíjení: neplatný výběr tarifu", "Smart laddning: ogiltigt tariffval"),
    "SMART_CHARGING_CHARGING_PROFILE_INVALID": ("Smart charging: charging profile invalid", "Smart Charging: Ladeprofil ungültig", "Recharge intelligente : profil de charge non valide", "Ricarica intelligente: profilo di ricarica non valido", "Carga inteligente: perfil de carga no válido", "Slim laden: laadprofiel ongeldig", "Inteligentne ładowanie: nieprawidłowy profil ładowania", "Carregamento inteligente: perfil de carregamento inválido", "Chytré nabíjení: neplatný nabíjecí profil", "Smart laddning: ogiltig laddprofil"),
    "CUSTOMER_INTERACTION_AC_CHARGING_STOP_VIA_APP": ("Driver: AC charging stopped via the app", "Fahrer: AC-Laden per App beendet", "Conducteur : charge AC arrêtée via l'app", "Conducente: ricarica AC arrestata tramite app", "Conductor: carga CA detenida desde la app", "Bestuurder: AC-laden gestopt via de app", "Kierowca: ładowanie AC zatrzymane w aplikacji", "Condutor: carregamento AC parado pela app", "Řidič: nabíjení AC ukončeno v aplikaci", "Förare: AC-laddning stoppad via appen"),
    "CHARGING_PAUSED": ("Charging paused", "Laden pausiert", "Charge en pause", "Ricarica in pausa", "Carga en pausa", "Laden gepauzeerd", "Ładowanie wstrzymane", "Carregamento em pausa", "Nabíjení pozastaveno", "Laddning pausad"),
    "DISCHARGE_NOT_POSSIBLE_VOLTAGE_TOO_HIGH": ("Discharging not possible: voltage too high", "Entladen nicht möglich: Spannung zu hoch", "Décharge impossible : tension trop élevée", "Scarica non possibile: tensione troppo alta", "Descarga imposible: tensión demasiado alta", "Ontladen niet mogelijk: spanning te hoog", "Rozładowanie niemożliwe: zbyt wysokie napięcie", "Descarga impossível: tensão demasiado alta", "Vybíjení není možné: příliš vysoké napětí", "Urladdning inte möjlig: för hög spänning"),
    "DISCHARGE_NOT_POSSIBLE_SOE_TOO_LOW": ("Discharging not possible: charge too low", "Entladen nicht möglich: Ladestand zu tief", "Décharge impossible : charge trop faible", "Scarica non possibile: carica troppo bassa", "Descarga imposible: carga demasiado baja", "Ontladen niet mogelijk: laadniveau te laag", "Rozładowanie niemożliwe: zbyt niski poziom naładowania", "Descarga impossível: carga demasiado baixa", "Vybíjení není možné: příliš nízké nabití", "Urladdning inte möjlig: för låg laddnivå"),
    "DISCHARGE_NOT_POSSIBLE_INTERNAL_ERROR": ("Discharging not possible: internal error", "Entladen nicht möglich: interner Fehler", "Décharge impossible : erreur interne", "Scarica non possibile: errore interno", "Descarga imposible: error interno", "Ontladen niet mogelijk: interne fout", "Rozładowanie niemożliwe: błąd wewnętrzny", "Descarga impossível: erro interno", "Vybíjení není možné: interní chyba", "Urladdning inte möjlig: internt fel"),
    "DISCHARGE_NOT_POSSIBLE_INCOMPATIBLE_ADAPTER": ("Discharging not possible: adapter not compatible", "Entladen nicht möglich: Adapter nicht kompatibel", "Décharge impossible : adaptateur non compatible", "Scarica non possibile: adattatore non compatibile", "Descarga imposible: adaptador no compatible", "Ontladen niet mogelijk: adapter niet compatibel", "Rozładowanie niemożliwe: niekompatybilny adapter", "Descarga impossível: adaptador não compatível", "Vybíjení není možné: nekompatibilní adaptér", "Urladdning inte möjlig: adaptern är inte kompatibel"),
    "DISCHARGE_NOT_POSSIBLE_INCOMPATIBLE_CONSUMER": ("Discharging not possible: consumer not compatible", "Entladen nicht möglich: Verbraucher nicht kompatibel", "Décharge impossible : consommateur non compatible", "Scarica non possibile: utenza non compatibile", "Descarga imposible: consumidor no compatible", "Ontladen niet mogelijk: verbruiker niet compatibel", "Rozładowanie niemożliwe: niekompatybilny odbiornik", "Descarga impossível: consumidor não compatível", "Vybíjení není možné: nekompatibilní spotřebič", "Urladdning inte möjlig: förbrukaren är inte kompatibel"),
    "ROUTE_OPTIMIZED_CHARGING_IN_PROGRESS": ("Route-optimized charging in progress", "Routenoptimiertes Laden läuft", "Charge optimisée pour l'itinéraire en cours", "Ricarica ottimizzata per il percorso in corso", "Carga optimizada para la ruta en curso", "Routegeoptimaliseerd laden bezig", "Ładowanie zoptymalizowane pod trasę w toku", "Carregamento otimizado para a rota em curso", "Nabíjení optimalizované pro trasu probíhá", "Ruttoptimerad laddning pågår"),
    "ROUTE_OPTIMIZED_SOC_TARGET_REACHED": ("Route-optimized charging target reached", "Routenoptimiertes Ladeziel erreicht", "Objectif de charge optimisé pour l'itinéraire atteint", "Obiettivo di ricarica ottimizzato per il percorso raggiunto", "Objetivo de carga optimizado para la ruta alcanzado", "Routegeoptimaliseerd laaddoel bereikt", "Osiągnięto cel ładowania zoptymalizowany pod trasę", "Objetivo de carregamento otimizado para a rota atingido", "Cíl nabíjení optimalizovaný pro trasu dosažen", "Ruttoptimerat laddningsmål nått"),
    "AVAILABLE": ("Available", "Verfügbar", "Disponible", "Disponibile", "Disponible", "Beschikbaar", "Dostępne", "Disponível", "K dispozici", "Tillgänglig"),
    "NOT_AVAILABLE": ("Not available", "Nicht verfügbar", "Non disponible", "Non disponibile", "No disponible", "Niet beschikbaar", "Niedostępne", "Não disponível", "Není k dispozici", "Inte tillgänglig"),
    "DISABLED_MALIBU": ("Disabled (Malibu)", "Deaktiviert (Malibu)", "Désactivé (Malibu)", "Disattivato (Malibu)", "Desactivado (Malibu)", "Uitgeschakeld (Malibu)", "Wyłączone (Malibu)", "Desativado (Malibu)", "Vypnuto (Malibu)", "Inaktiverad (Malibu)"),
    "DISABLED_HVS": ("Disabled (high-voltage battery)", "Deaktiviert (Hochvoltspeicher)", "Désactivé (batterie haute tension)", "Disattivato (batteria ad alta tensione)", "Desactivado (batería de alto voltaje)", "Uitgeschakeld (hoogspanningsaccu)", "Wyłączone (akumulator wysokonapięciowy)", "Desativado (bateria de alta tensão)", "Vypnuto (vysokonapěťová baterie)", "Inaktiverad (högspänningsbatteri)"),
    "BPT_AND_IMO_DISABLED": ("Power export and immobilizer disabled", "Rückspeisung und Wegfahrsperre deaktiviert", "Réinjection et antidémarrage désactivés", "Cessione di energia e immobilizer disattivati", "Exportación de energía e inmovilizador desactivados", "Terugleveren en startonderbreker uitgeschakeld", "Oddawanie energii i immobilizer wyłączone", "Exportação de energia e imobilizador desativados", "Zpětné dodávání a imobilizér vypnuty", "Återmatning och startspärr inaktiverade"),
    "BPT_ONLY_FOR_EMERGENCY_POWER_SUPPLY": ("Power export for emergency power only", "Rückspeisung nur für Notstrom", "Réinjection uniquement pour l'alimentation de secours", "Cessione di energia solo per alimentazione di emergenza", "Exportación de energía solo para suministro de emergencia", "Terugleveren alleen voor noodstroom", "Oddawanie energii tylko do zasilania awaryjnego", "Exportação de energia apenas para alimentação de emergência", "Zpětné dodávání jen pro nouzové napájení", "Återmatning endast för nödström"),
    "DISABLED_IMO_POSSIBLE": ("Disabled, immobilizer possible", "Deaktiviert, Wegfahrsperre möglich", "Désactivé, antidémarrage possible", "Disattivato, immobilizer possibile", "Desactivado, inmovilizador posible", "Uitgeschakeld, startonderbreker mogelijk", "Wyłączone, immobilizer możliwy", "Desativado, imobilizador possível", "Vypnuto, imobilizér možný", "Inaktiverad, startspärr möjlig"),
    "DISABLED_LONG_TERM": ("Permanently disabled", "Dauerhaft deaktiviert", "Désactivé durablement", "Disattivato in modo permanente", "Desactivado de forma permanente", "Permanent uitgeschakeld", "Trwale wyłączone", "Desativado permanentemente", "Trvale vypnuto", "Permanent inaktiverad"),
    "AC_LIMIT_INACTIVE": ("AC limit inactive", "AC-Begrenzung inaktiv", "Limite AC inactive", "Limite AC non attivo", "Límite de CA inactivo", "AC-begrenzing inactief", "Ograniczenie AC nieaktywne", "Limite AC inativo", "Omezení AC neaktivní", "AC-begränsning inaktiv"),
    "AC_LIMIT_ACTIVE": ("AC limit active", "AC-Begrenzung aktiv", "Limite AC active", "Limite AC attivo", "Límite de CA activo", "AC-begrenzing actief", "Ograniczenie AC aktywne", "Limite AC ativo", "Omezení AC aktivní", "AC-begränsning aktiv"),
    "UNLIMITED": ("Unlimited", "Unbegrenzt", "Illimité", "Illimitato", "Ilimitado", "Onbeperkt", "Bez ograniczeń", "Ilimitado", "Neomezeno", "Obegränsad"),
    "LIMITED": ("Limited", "Begrenzt", "Limité", "Limitato", "Limitado", "Beperkt", "Ograniczony", "Limitado", "Omezeno", "Begränsad"),
    "NOT_STARTED": ("Not started", "Nicht gestartet", "Non démarré", "Non avviato", "No iniciado", "Niet gestart", "Nie rozpoczęto", "Não iniciado", "Nespuštěno", "Inte startad"),
    "STARTED_EIM": ("Started (EIM)", "Gestartet (EIM)", "Démarré (EIM)", "Avviato (EIM)", "Iniciado (EIM)", "Gestart (EIM)", "Rozpoczęto (EIM)", "Iniciado (EIM)", "Spuštěno (EIM)", "Startad (EIM)"),
    "STARTED_PNC": ("Started (Plug & Charge)", "Gestartet (Plug & Charge)", "Démarré (Plug & Charge)", "Avviato (Plug & Charge)", "Iniciado (Plug & Charge)", "Gestart (Plug & Charge)", "Rozpoczęto (Plug & Charge)", "Iniciado (Plug & Charge)", "Spuštěno (Plug & Charge)", "Startad (Plug & Charge)"),
    "STARTED_PLC_EIM": ("Started (PLC EIM)", "Gestartet (PLC-EIM)", "Démarré (PLC EIM)", "Avviato (PLC EIM)", "Iniciado (PLC EIM)", "Gestart (PLC-EIM)", "Rozpoczęto (PLC EIM)", "Iniciado (PLC EIM)", "Spuštěno (PLC EIM)", "Startad (PLC EIM)"),
    "DONE_EIM": ("Completed (EIM)", "Abgeschlossen (EIM)", "Terminé (EIM)", "Completato (EIM)", "Completado (EIM)", "Voltooid (EIM)", "Zakończono (EIM)", "Concluído (EIM)", "Dokončeno (EIM)", "Slutförd (EIM)"),
    "DONE_PLC_EIM": ("Completed (PLC EIM)", "Abgeschlossen (PLC-EIM)", "Terminé (PLC EIM)", "Completato (PLC EIM)", "Completado (PLC EIM)", "Voltooid (PLC-EIM)", "Zakończono (PLC EIM)", "Concluído (PLC EIM)", "Dokončeno (PLC EIM)", "Slutförd (PLC EIM)"),
    "DONE_PNC": ("Completed (Plug & Charge)", "Abgeschlossen (Plug & Charge)", "Terminé (Plug & Charge)", "Completato (Plug & Charge)", "Completado (Plug & Charge)", "Voltooid (Plug & Charge)", "Zakończono (Plug & Charge)", "Concluído (Plug & Charge)", "Dokončeno (Plug & Charge)", "Slutförd (Plug & Charge)"),
    "FAILED_PLC_EIM": ("Failed (PLC EIM)", "Fehlgeschlagen (PLC-EIM)", "Échoué (PLC EIM)", "Non riuscito (PLC EIM)", "Fallido (PLC EIM)", "Mislukt (PLC-EIM)", "Niepowodzenie (PLC EIM)", "Falhou (PLC EIM)", "Selhalo (PLC EIM)", "Misslyckades (PLC EIM)"),
    "FAILED_PNC": ("Failed (Plug & Charge)", "Fehlgeschlagen (Plug & Charge)", "Échoué (Plug & Charge)", "Non riuscito (Plug & Charge)", "Fallido (Plug & Charge)", "Mislukt (Plug & Charge)", "Niepowodzenie (Plug & Charge)", "Falhou (Plug & Charge)", "Selhalo (Plug & Charge)", "Misslyckades (Plug & Charge)"),
    "EFFICIENT": ("Efficient", "Effizient", "Efficace", "Efficiente", "Eficiente", "Efficiënt", "Wydajny", "Eficiente", "Úsporný", "Effektiv"),
    "STANDARD": ("Standard", "Standard", "Standard", "Standard", "Estándar", "Standaard", "Standardowy", "Padrão", "Standardní", "Standard"),
    "PERFORMANCE": ("Performance", "Leistung", "Performance", "Prestazioni", "Rendimiento", "Prestatie", "Wydajność", "Desempenho", "Výkon", "Prestanda"),
    "PRICE_OPTIMIZED": ("Price-optimized", "Preisoptimiert", "Optimisé pour le prix", "Ottimizzato per il prezzo", "Optimizado por precio", "Prijsgeoptimaliseerd", "Zoptymalizowany pod cenę", "Otimizado pelo preço", "Optimalizováno podle ceny", "Prisoptimerad"),
    "RENEWABLE_ENERGEY": ("Renewable energy", "Erneuerbare Energie", "Énergie renouvelable", "Energia rinnovabile", "Energía renovable", "Hernieuwbare energie", "Energia odnawialna", "Energia renovável", "Obnovitelná energie", "Förnybar energi"),
    "CO2_OPTIMIZED": ("CO₂-optimized", "CO₂-optimiert", "Optimisé pour le CO₂", "Ottimizzato per la CO₂", "Optimizado por CO₂", "CO₂-geoptimaliseerd", "Zoptymalizowany pod CO₂", "Otimizado para CO₂", "Optimalizováno podle CO₂", "CO₂-optimerad"),
    "TEMP_OFF": ("Temporarily off", "Vorübergehend aus", "Temporairement désactivé", "Temporaneamente spento", "Desactivado temporalmente", "Tijdelijk uit", "Tymczasowo wyłączone", "Temporariamente desligado", "Dočasně vypnuto", "Tillfälligt av"),
    "ON_CHARGE": ("While charging", "Beim Laden", "Pendant la charge", "Durante la ricarica", "Durante la carga", "Tijdens het laden", "Podczas ładowania", "Durante o carregamento", "Při nabíjení", "Vid laddning"),
    "ON_DRIVE": ("While driving", "Beim Fahren", "Pendant la conduite", "Durante la guida", "Durante la conducción", "Tijdens het rijden", "Podczas jazdy", "Durante a condução", "Při jízdě", "Vid körning"),
    "ON_LEGACY": ("On (legacy method)", "Ein (älteres Verfahren)", "Activé (ancienne méthode)", "Attivo (metodo precedente)", "Activado (método anterior)", "Aan (oudere methode)", "Włączone (starsza metoda)", "Ligado (método antigo)", "Zapnuto (starší metoda)", "På (äldre metod)"),
    "MANUAL_ON_CHARGE": ("Manual while charging", "Manuell beim Laden", "Manuel pendant la charge", "Manuale durante la ricarica", "Manual durante la carga", "Handmatig tijdens het laden", "Ręcznie podczas ładowania", "Manual durante o carregamento", "Ručně při nabíjení", "Manuellt vid laddning"),
    "AUTOMATIC_ON": ("Automatic on", "Automatisch ein", "Automatique activé", "Automatico attivo", "Automático activado", "Automatisch aan", "Automatycznie włączone", "Automático ligado", "Automaticky zapnuto", "Automatiskt på"),
    "REMOTE_ON_CHARGE": ("Remote start while charging", "Fernstart beim Laden", "Démarrage à distance pendant la charge", "Avvio remoto durante la ricarica", "Inicio remoto durante la carga", "Op afstand gestart tijdens het laden", "Zdalne uruchomienie podczas ładowania", "Arranque remoto durante o carregamento", "Vzdálené spuštění při nabíjení", "Fjärrstart vid laddning"),
    "REMOTE_ON_DRIVE": ("Remote start for the drive", "Fernstart für die Fahrt", "Démarrage à distance pour le trajet", "Avvio remoto per la guida", "Inicio remoto para el trayecto", "Op afstand gestart voor de rit", "Zdalne uruchomienie przed jazdą", "Arranque remoto para a viagem", "Vzdálené spuštění pro jízdu", "Fjärrstart för körningen"),
    "REMOTE_OFF": ("Remote off", "Fernsteuerung aus", "Arrêt à distance", "Spento da remoto", "Apagado remoto", "Op afstand uit", "Zdalnie wyłączone", "Desligado remotamente", "Vzdáleně vypnuto", "Fjärravstängd"),
    "NO_DISPLAY_TIME_FOR_CHARGING": ("Don't show a charging time", "Keine Ladezeit anzeigen", "Ne pas afficher de temps de charge", "Non mostrare il tempo di ricarica", "No mostrar el tiempo de carga", "Geen laadtijd tonen", "Nie pokazuj czasu ładowania", "Não mostrar o tempo de carregamento", "Nezobrazovat čas nabíjení", "Visa ingen laddtid"),
    "DISPLAY_CHARGING_DURATION": ("Show charging duration", "Ladedauer anzeigen", "Afficher la durée de charge", "Mostra la durata della ricarica", "Mostrar la duración de la carga", "Laadduur tonen", "Pokazuj czas trwania ładowania", "Mostrar a duração do carregamento", "Zobrazit dobu nabíjení", "Visa laddtid"),
    "NO_DISPLAY_CHARGING_DURATION": ("Don't show charging duration", "Ladedauer nicht anzeigen", "Ne pas afficher la durée de charge", "Non mostrare la durata della ricarica", "No mostrar la duración de la carga", "Laadduur niet tonen", "Nie pokazuj czasu trwania ładowania", "Não mostrar a duração do carregamento", "Nezobrazovat dobu nabíjení", "Visa inte laddtid"),
    "DONT_DISPLAY": ("Don't show", "Nicht anzeigen", "Ne pas afficher", "Non mostrare", "No mostrar", "Niet tonen", "Nie pokazuj", "Não mostrar", "Nezobrazovat", "Visa inte"),
    "DISPLAY_DEPARTURE_TIME": ("Show departure time", "Abfahrtszeit anzeigen", "Afficher l'heure de départ", "Mostra l'orario di partenza", "Mostrar la hora de salida", "Vertrektijd tonen", "Pokazuj godzinę odjazdu", "Mostrar a hora de partida", "Zobrazit čas odjezdu", "Visa avgångstid"),
    "DISPLAY_DEPARTURE_TIME_WITH_V2XSOE": ("Show departure time with V2X charge level", "Abfahrtszeit mit V2X-Ladestand anzeigen", "Afficher l'heure de départ avec le niveau de charge V2X", "Mostra l'orario di partenza con il livello di carica V2X", "Mostrar la hora de salida con el nivel de carga V2X", "Vertrektijd met V2X-laadniveau tonen", "Pokazuj godzinę odjazdu z poziomem naładowania V2X", "Mostrar a hora de partida com o nível de carga V2X", "Zobrazit čas odjezdu s úrovní nabití V2X", "Visa avgångstid med V2X-laddnivå"),
    "NO_DISPLAY_DEPARTURE_TIME": ("Don't show departure time", "Abfahrtszeit nicht anzeigen", "Ne pas afficher l'heure de départ", "Non mostrare l'orario di partenza", "No mostrar la hora de salida", "Vertrektijd niet tonen", "Nie pokazuj godziny odjazdu", "Não mostrar a hora de partida", "Nezobrazovat čas odjezdu", "Visa inte avgångstid"),
    "1-PHASES": ("1 phase", "1 Phase", "1 phase", "1 fase", "1 fase", "1 fase", "1 faza", "1 fase", "1 fáze", "1 fas"),
    "2-PHASES": ("2 phases", "2 Phasen", "2 phases", "2 fasi", "2 fases", "2 fasen", "2 fazy", "2 fases", "2 fáze", "2 faser"),
    "3-PHASES": ("3 phases", "3 Phasen", "3 phases", "3 fasi", "3 fases", "3 fasen", "3 fazy", "3 fases", "3 fáze", "3 faser"),
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
    # Our curated name in that language where BMW's is wrong for some cars
    # (``titles_i18n`` in curated_titles.json); otherwise BMW's own element name,
    # from the German export or one of the other localized ones; English when
    # BMW has none.
    if curated := (entry.get("titles_i18n") or {}).get(lang):
        return curated
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
    return {
        platform: entries
        for platform, entries in data.items()
        if not platform.startswith("_") and platform != "states"
    }


def load_derived_states() -> dict[str, dict[str, dict[str, dict[str, str]]]]:
    """``{platform: {key: {state: {lang: label}}}}`` for the derived entities."""

    data = json.loads(DERIVED_FILE.read_text(encoding="utf-8"))
    return data.get("states") or {}


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

    for platform, entries in load_derived_states().items():
        for key, states in entries.items():
            if key not in blocks["en"].get(platform, {}):
                raise SystemExit(f"derived_entities.json: states for unknown {platform}.{key}")
            for lang, block in blocks.items():
                block[platform][key]["state"] = {
                    state: labels.get(lang) or labels["en"] for state, labels in states.items()
                }


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
