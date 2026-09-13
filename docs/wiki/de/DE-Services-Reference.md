# Dienste

> 🇬🇧 [English version](Services-Reference)

Jeder Dienst ist unter **Entwicklerwerkzeuge → Aktionen** verfügbar und (die
meisten) auch als Schaltfläche im Menü **Konfigurieren** der Integration.

Alle Dienste akzeptieren eine optionale **`entry_id`** (nur nötig, wenn du mehrere
Konfigurationseinträge hast) und, wo sinnvoll, eine optionale **`vin`**
(Standard: das erste bekannte Fahrzeug).

## Abruf-Dienste — verbrauchen API-Kontingent ⚡

Jeder davon verbraucht **eine oder mehrere** deiner
[50 Anfragen / 24 h](DE-Feature-API-Quota). Die ersten beiden laufen einmal am Tag
automatisch als [tägliche Aktualisierung](DE-Feature-API-Quota#the-daily-refresh);
von Hand aufgerufen holen sie nur früher ab und verbrauchen eine Anfrage mehr.

| Dienst | Was er abruft |
| --- | --- |
| `bavariandata.fetch_telematic_data` | Aktueller Inhalt des Telematik-Containers einer FIN — jedes Feld, das BMW nicht streamen kann, in einer Anfrage. |
| `bavariandata.fetch_vehicle_mappings` | Mit dem Konto verknüpfte Fahrzeuge und ihr Status PRIMARY/SECONDARY. |
| `bavariandata.fetch_basic_data` | Statische Fahrzeugdaten (Modell, Baureihe, …). |
| `bavariandata.fetch_charging_history` | BMWs Ladevorgänge (seitenweise; optional `from`/`to`), in den lokalen Verlauf importiert und um gemessene Netzenergie ergänzt. |
| `bavariandata.fetch_tyre_diagnosis` | Reifendiagnose aus Smart Maintenance — Profiltiefe, Restlaufleistung, Defektstatus pro Rad. Befüllt die Reifen-Sensoren und das Raddiagramm der Karte. |
| `bavariandata.fetch_location_charging_settings` | Standortbasierte Ladeeinstellungen (seitenweise). |
| `bavariandata.fetch_vehicle_image` | Fahrzeugbild (aktualisiert die zwischengespeicherte Bild-Entität). |

## Lokale Dienste — ohne Kontingent

Diese lesen (oder schreiben) den eigenen Speicher der Integration und kosten
**kein** Kontingent.

| Dienst | Was er tut |
| --- | --- |
| `bavariandata.get_charging_sessions` | Gespeicherte Ladevorgänge als Antwortdaten. |
| `bavariandata.get_trips` | Gespeicherte Fahrten als Antwortdaten (Endpunkte als Ortsnamen), dazu eine gerade laufende Fahrt. |
| `bavariandata.get_driving_summary` | Die Monatsübersicht der Fahrten. |
| `bavariandata.get_efficiency` | Gemessener Verbrauch, die daraus folgende reale Reichweite, der Ladeverlust und der Monatsverlauf — siehe unten. |
| `bavariandata.get_evcc_config` | Die evcc-Fahrzeugkonfiguration `custom` für ein Auto, dazu die MQTT-Topics der Brücke — siehe unten. |
| `bavariandata.set_trip_class` | Korrigiert die Zuordnung einer Fahrt (geschäftlich/privat/Pendeln). |
| `bavariandata.export_history` | Liefert einen Monat als CSV oder druckbaren HTML-Bericht. |
| `bavariandata.get_coverage_report` | Selbsttest der Deskriptor-Abdeckung — siehe unten. |
| `bavariandata.import_statistics` | Baut die Langzeitstatistiken aus dem Speicher neu auf. |
| `bavariandata.activate_stream_fields` | Ersetzt, welche Attribute BMW streamt, indem es die Stream-Setup-Anfrage des Portals nachspielt — siehe unten. |

## Details zu den Feldern

### `get_charging_sessions` / `get_trips`

`vin`, `from`, `to`, `limit`. Liefert Datensätze, neueste zuerst, als
**Antwortdaten** (in den Entwicklerwerkzeugen *Antwort zurückgeben* anhaken).

Ein reines Datum steht für den **ganzen Tag**, egal an welchem Ende: `from:
2026-08-01` beginnt um Mitternacht und `to: 2026-08-31` reicht bis zum Ende des
31., eine Anfrage für einen Monat verliert also nicht stillschweigend den letzten
Tag. Gibst du stattdessen ein Datum mit Uhrzeit an, wird es genau so verwendet.

`get_trips` liefert außerdem **`open_trips`** — eine gerade laufende Fahrt, die noch
nicht im Fahrtenbuch steht: ohne Ende, ohne Zuordnung, mit vorläufiger Strecke und,
bei eingeschalteter Routenaufzeichnung, der bisherigen Route. Sie wird immer dann
mitgeliefert, wenn das angefragte Fenster **den jetzigen Moment enthält** (auch ganz
ohne Fenster): Eine Anfrage für letzten März meint offensichtlich nicht die Fahrt
von gerade eben, eine für diesen Monat offensichtlich schon. Siehe
[Fahrten](DE-Feature-Trips#seeing-the-drive-thats-happening-now).

### `get_driving_summary`

`vin`, `month` (`YYYY-MM`, Standard: aktueller Monat). Liefert Strecke, die
Aufteilung geschäftlich/privat/Pendeln, Verbrauch, Rekuperation, eine
Fahrstil-Bewertung, die häufigsten Ziele und (mit Tarif) geschätzte Fahrtkosten.

Der Verbrauch kommt als zwei unabhängige Werte, von denen jeder fehlen kann, wenn
seine Eingangswerte keinen tragen:

- **`energy_balance`** — ein Objekt mit `kwh_per_100km` sowie dem Fenster und den
  Eingangswerten. Seine **`source`** sagt, welche Seite des Ladegeräts es
  beschreibt: `"grid"` nur, wenn jeder beteiligte Ladevorgang ein gemessenes
  `grid_kwh` hatte, sonst `"battery"` (BMW streamt die *batterieseitige*
  Ladeleistung, ein geschätzter Ladevorgang hat die Wand also nie gemessen). Lies
  `source`, bevor du die Zahl beschriftest — die beiden unterscheiden sich um die
  Ladeverluste.
- **`avg_consumption_kwh_per_100km`** — batterieseitig, aus den Fahrten.

Die Rekuperation ist `recuperation_kwh_per_100km`, ein nach Strecke gewichtetes
Mittel statt einer Summe. Siehe
[wie der Verbrauch gemessen wird](DE-Feature-Trips#how-consumption-is-measured).

### `get_efficiency`

`vin`. Liefert das gemessene Effizienzprofil: Verbrauch, die daraus folgende reale
Reichweite, den Ladeverlust und einen Verlauf Monat für Monat. Liest den lokalen
Speicher und kostet daher kein BMW-API-Kontingent.

- **`consumption`** — batterieseitiges `kwh_per_100km` plus `window_days` (30, 90,
  365 oder `null`, wenn der ganze Verlauf nötig war) und das Kilometerstand-Fenster,
  über das gemessen wurde. **`grid_consumption`** ist derselbe Wert ab Steckdose, nur
  vorhanden, wenn jede Ladung im selben Fenster ein gemessenes `grid_kwh` hatte.
- **`measured_loss_percent`** — die Differenz der beiden, wenn es beide gibt. Sie ist
  *gemessen* und etwas anderes als die
  [Einstellung Ladeverluste (%)](DE-Settings-Reference#charging-costs--history), die
  eine Annahme für die Kostenrechnung ist.
- **`range`** — `full_km`, `now_km` (mit der aktuellen Ladung skaliert), `bmw_km`
  (die Prognose des Autos) und `vs_bmw_percent`. Fehlt, wenn Kapazität oder
  Verbrauch unbekannt sind.
- **`capacity_kwh` / `capacity_source`** — `measured`, sobald der Batteriezustand
  sicher ist, sonst `bmw`.
- **`trend`** — ein Eintrag pro messbarem Kalendermonat, älteste zuerst; Monate, deren
  Ladungen nicht genug Strecke einschließen konnten, fehlen, statt als null zu
  erscheinen.
- **`cost_per_100km` / `currency` / `energy_mix`** — die laufenden Kosten dieses
  Monats und woher seine Energie kam.

`status` sagt, warum ein Wert fehlt: `ok`, `not_enough_history` oder `no_capacity`.
Siehe [Effizienz & reale Reichweite](DE-Feature-Efficiency-and-Range).

### `get_evcc_config`

`vin`. Liefert die evcc-Konfiguration für ein Auto, fertig zum Einfügen in
`evcc.yaml`, plus alles, was zur Fehlersuche an der Brücke nötig ist. Liest nur
lokale Zustände und kostet daher kein BMW-API-Kontingent.

- **`yaml`** — der Block `vehicles:`, mit deiner FIN, deinem Topic-Prefix und der
  Akkugröße des Autos schon ausgefüllt. Verwendet werden nur die Felder, die das Auto
  wirklich meldet, und es ist **kein `timeout`** gesetzt (die Brücke sendet
  stattdessen regelmäßig erneut).
- **`enabled`** — ob die Brücke eingeschaltet ist. Das YAML wird in jedem Fall
  erzeugt, veröffentlicht wird aber nichts, solange dies `false` ist.
- **`mqtt_available`** — ob Home Assistant eine geladene MQTT-Integration hat, über
  die veröffentlicht werden kann. `false` ist das Erste, was du prüfst, wenn auf dem
  Broker nichts erscheint.
- **`topic_prefix`**, **`topics`** — das geltende Prefix und jedes Topic, das der
  Brücke gehört.
- **`published_topics`** — die davon gerade veröffentlichten, also das, was dieses
  Auto wirklich meldet. Fehlt `status` in dieser Liste, meldet das Auto keinen
  Steckerstatus — siehe die Fehlerbehebung auf der Seite zur Brücke.

Siehe [evcc- & Wallbox-Brücke](DE-Feature-evcc-and-Wallbox-Bridge).

### `set_trip_class`

`vin`, `trip_id` (wie von `get_trips` geliefert), `classification`
(`business` · `private` · `commute`). Schreibt nur den lokalen Speicher.

### `export_history`

`vin`, `month` (`YYYY-MM`), `type` (`charging` · `trips` · `both`), `format`
(`csv` · `html`), `language` (`en` · `de`, nur HTML-Bericht). Liefert den
Dateiinhalt als Antwortdaten; auf die Festplatte wird nichts geschrieben. Siehe
[Export](DE-Feature-Export).

### `get_coverage_report`

`vin`. Vergleicht für jedes Fahrzeug die Deskriptoren, die deine gewählten
Datengruppen liefern *sollten*, mit denen, die tatsächlich angekommen sind, und
listet die fehlenden auf. Beantwortet *„Ich habe eine Datengruppe aktiviert, aber
keine Entitäten erscheinen — liegt es an meiner Auswahl, meinem Auto oder einem
Fehler?“* Liest nur den lokalen Speicher und den Live-Stream.

Felder, die BMW als nicht streambar kennzeichnet, sind vom Vergleich ausgenommen —
sie können nur per REST kommen, sie mitzuzählen ergäbe also eine dauerhafte Lücke,
die keine Einstellung schließen kann. Siehe
[Gestreamte Daten auswählen](DE-Getting-Started-4-Choose-Data#some-fields-never-arrive-on-the-stream).

**Erwarte bei einem gesunden Auto eine lange Fehlliste.** BMW veröffentlicht einen
Katalog für die ganze Flotte, daher enthält jede Datengruppe Felder, für die dein
Auto keine Hardware hat — eine dritte Sitzreihe, ein Cabrio-Verdeck, ein Tank bei
einem E-Auto. Der Bericht listet sie alle; der Wert `seen` pro Datengruppe ist das,
was du lesen solltest. Jeder Bericht nennt außerdem `not_applicable`-Datengruppen:
*Elektrofahrzeug* und *Fahrzeug-Basisdaten* bei einem Auto, das Kraftstoffdaten und
keine Hochvoltbatterie-Daten gesendet hat.

**Die Reparaturwarnung ist strenger als der Bericht.** Sie erscheint nur, wenn eine
gewählte Datengruppe 7 Tage lang **überhaupt nichts** gesendet hat — das Kennzeichen
einer Datenauswahl, die nicht gespeichert wurde —, und nie für eine teilweise
gefüllte Datengruppe, für *Fahrzeugereignisse* (Teleservice-Anrufe können Monate
auseinanderliegen) oder für eine `not_applicable`-Datengruppe.

### `import_statistics`

`vin`. Baut die Langzeitstatistiken dieser Integration aus dem aufgezeichneten
Verlauf neu auf, sodass Laden und Fahren von vor der Installation (oder aus der Zeit,
in der HA aus war) im Energie-Dashboard erscheinen. Läuft automatisch, sobald
Datensätze entstehen; nutze es nach dem Wiederherstellen eines Backups oder zum
Prüfen der Zeilenzahlen. Siehe
[Energie & Statistiken](DE-Feature-Energy-and-Statistics).

### `activate_stream_fields`

Ersetzt die Auswahl der gestreamten Attribute eines Fahrzeugs, indem es **dieselbe
Anfrage sendet wie das BMW-Portal**, wenn du *Datenauswahl ändern* speicherst — so
kannst du alle Felder mit einem Aufruf aktivieren, statt Häkchen zu setzen. Es
**ersetzt**: Die übergebene Attributliste wird die ganze Auswahl.

Die Stream-Auswahl hat **keine CarData-API**; sie liegt hinter dem Portal deines
Marktes, das über deine **Browser-Sitzung** authentifiziert. Dieser Dienst braucht
daher eine **mitgeschnittene Portal-Sitzung**, und weil diese Sitzung (einschließlich
BMWs Bot-Abwehr-Cookies) kurzlebig ist und sich nicht automatisch erneuern lässt, ist
er ein Werkzeug für **gelegentliche, manuelle** Einsätze, nichts, was unbeaufsichtigt
läuft. Er verbraucht kein API-Kontingent.

**Die vier nötigen Werte** — öffne die **Stream-Setup**-Seite deines Fahrzeugs im
Browser, öffne die Entwicklertools → **Netzwerk**, speichere eine beliebige Änderung
und sieh dir die Anfrage `POST …/utilities/bmw/api/cd/streams/…` an:

| Feld | Woher es kommt | Beispiel |
| --- | --- | --- |
| `base_url` | der Ursprung der Anfrage | `https://www.bmw.at` |
| `locale` | erster Pfadabschnitt | `de-at` |
| `mapped_vehicle_id` | die ID in der URL (ein Hash, **nicht** die FIN) | `90d3dd3e0ba0ea99…` |
| `cookie` | der Header **Cookie** der Anfrage (geheim — wird nie protokolliert) | `gcdmToken=…; ak_bmsc=…` |

**Die Attribute wählen** — übergib eine ausdrückliche Liste `attributes` oder
`sections` (Kürzel der Datengruppen wie `electric`, `status`, `tire`). Übergibst du
keins von beiden, werden deine gespeicherten Datengruppen aus **Gestreamte Daten
auswählen** oder der Standard-Satz verwendet. Die Liste wird entdoppelt und sortiert,
und eine unveränderte Auswahl wird erkannt und übersprungen. Liefert
`{requested, accepted, unchanged}` als Antwortdaten.

Meldet der Aufruf, die Sitzung sei **abgelehnt** worden (Authentifizierung), ist das
mitgeschnittene Cookie abgelaufen — hol dir ein frisches und versuche es erneut.
**Läuft er in eine Zeitüberschreitung**, bremst BMWs Bot-Abwehr automatische Aufrufe;
warte etwas und versuche es mit einer **frisch mitgeschnittenen** Sitzung (deshalb ist
es ein Werkzeug für einzelne Aufrufe und nichts für Schleifen). Die Alternative mit
dem Ein-Klick-Lesezeichen steht unter
[Gestreamte Daten auswählen](DE-Getting-Started-4-Choose-Data).

### `fetch_charging_history`

`vin`, `from` (Standard: vor 30 Tagen), `to` (Standard: jetzt). Importiert BMWs
Ladevorgänge in den lokalen Verlauf; überlappende live aufgezeichnete Ladevorgänge
werden an Ort und Stelle um BMWs gemessene Netzenergie ergänzt.
