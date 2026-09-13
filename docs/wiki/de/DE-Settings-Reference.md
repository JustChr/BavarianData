# Einstellungen

> 🇬🇧 [English version](Settings-Reference)

Alle Einstellungen liegen im Menü **Konfigurieren** der Integration: **Einstellungen
→ Geräte & Dienste → BavarianData → Konfigurieren**. Jeder Aktionsschritt und jede
Option steht hier.

Das Menü hat drei Arten von Einträgen: **Stream-Einrichtung**,
**Einstellungsbildschirme** und **einmalige Abruf-Aktionen** (die API-Kontingent
verbrauchen). Änderungen in Einstellungsbildschirmen gelten **sofort** — die
Integration lädt den Eintrag nicht neu, weil BMW nur einen gleichzeitigen Stream je
Konto erlaubt und ein Neuladen mit dem Wiederverbinden kollidieren könnte.

## Das Menü Konfigurieren

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-configure-menu.png" alt="Optionsmenü von BavarianData mit allen Aktionen, von Choose streamed data bis Debug logging" width="460" />
</p>

Die Menüeinträge unten sind so geschrieben, wie sie in der deutschen Oberfläche
erscheinen (der Screenshot zeigt die englische).

| Menüeintrag | Art | Was er tut |
| --- | --- | --- |
| **Gestreamte Daten auswählen** | Einrichtung | Öffnet die [Auswahl der Datengruppen](DE-Getting-Started-4-Choose-Data) erneut und schaltet die Felder dann mit dem Ein-Klick-Lesezeichen **Activate BMW data** ein (derselbe Aktivator wie bei der geführten Einrichtung). Nur hinzufügend — fügt die gewählten Datengruppen dem laufenden Stream hinzu, entfernt aber nie bereits gestreamte Felder. |
| **Token jetzt erneuern** | Aktion | Erzwingt eine Erneuerung des OAuth-Tokens. |
| **Neu bei BMW autorisieren** | Aktion | Führt die Geräteautorisierung erneut aus (nachdem BMW das Token ungültig gemacht hat). |
| **Telemetrie-Container zurücksetzen** | Aktion | Löscht ID und Signatur des gespeicherten Containers, damit er beim nächsten Abruf neu angelegt wird. Nutze das, wenn Telematik-Abrufe nach einer Änderung der Deskriptoren fehlschlagen. |
| **Fahrzeuge suchen** | Aktion ⚡ | Ruft die Fahrzeugzuordnungen ab. Siehe [Dienste](DE-Services-Reference). |
| **Fahrzeug-Basisdaten abrufen** | Aktion ⚡ | Siehe [Dienste](DE-Services-Reference). |
| **Telematikdaten abrufen** | Aktion ⚡ | " |
| **Ladehistorie abrufen** | Aktion ⚡ | " |
| **Reifendiagnose abrufen** | Aktion ⚡ | " |
| **Ladeeinstellungen abrufen** | Aktion ⚡ | Standortbasierte Ladeeinstellungen. " |
| **Fahrzeugbild abrufen** | Aktion ⚡ | " |
| **Ladekosten & Verlauf** | Einstellungen | Preisquelle, Aufbewahrung, Statistiken — unten. |
| **Solar & Energiequellen** | Einstellungen | Woher die Energie jeder Ladung kam: PV, Hausspeicher, Netz — unten. |
| **evcc-/Wallbox-Brücke** | Einstellungen | Den Live-Zustand des Autos für eine Ladesteuerung per MQTT veröffentlichen — unten. |
| **Fahrten** | Einstellungen | Arbeitszone, Standard-Typ, Toleranz für Zwischenstopps, Adressauflösung und Routenaufzeichnung — unten. |
| **Debug-Protokollierung** | Einstellungen | Schalter für ausführliche Protokollierung — unten. |

⚡ = verbraucht eine (oder mehrere) deiner [50 Anfragen / 24 h](DE-Feature-API-Quota).

## Ladekosten & Verlauf

<a id="charging-costs--history"></a>

Bildschirm: **Konfigurieren → Ladekosten & Verlauf**. Die Konzepte stehen unter
[Ladeverlauf & Kosten](DE-Feature-Charging-History-and-Cost).

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-charging-costs.png" alt="Einstellungen Ladekosten & Verlauf: Preisquelle, fester Preis, Preis-Entität, Währung, Wallbox-Energiesensor, Ladeverluste und Aufbewahrung" width="460" />
</p>

| Option | Werte | Bedeutung |
| --- | --- | --- |
| **Preisquelle** | Keine · Fester Preis · Preis-Entität | Wie aus Energie Geld wird. „Keine“ legt **gar keine Kosten-Entitäten** an. |
| **Fester Preis pro kWh** | Zahl | Preis pro kWh bei „Fester Preis“. In diesem Modus Pflicht. |
| **Preis-Entität** | Sensor / input_number | Live-Preisquelle (Tibber/Nordpool/aWATTar) bei „Preis-Entität“. Wird während des Ladens abgetastet. In diesem Modus Pflicht. |
| **Währung** | Text | Währungscode für die Kosten-Entitäten. |
| **Wallbox-Energiesensor** | Sensor | Optional. Der **kumulative** Energiezähler der Wallbox (`total_increasing`, kein Zähler pro Ladevorgang). Sein gemessener Netzwert ersetzt die batterieseitige Schätzung im Datensatz, in den Monatssummen und bei den Kosten. Wird nur für Ladungen in deiner Zone „Zuhause“ gelesen und abgelehnt, wenn er nicht stimmen kann — siehe [die Seite zur Brücke](DE-Feature-evcc-and-Wallbox-Bridge#when-the-reading-is-refused). |
| **Ladeverluste (%)** | 0–30 | Rechnet den Batteriewert um deine Verluste hoch. Standard **0** (keine erfundene Korrektur). |
| **Verlauf aufbewahren (Monate)** | 0–120 | Wie lange gespeicherte Ladevorgänge/Fahrten behalten werden. **0 = alles behalten.** |
| **In Langzeitstatistiken übernehmen** | an/aus | Spiegelt den Verlauf ins Energie-Dashboard. **Ausschalten löscht** die veröffentlichten Reihen. Siehe [Energie & Statistiken](DE-Feature-Energy-and-Statistics). |

## Solar & Energiequellen

<a id="solar--energy-sources"></a>

Bildschirm: **Konfigurieren → Solar & Energiequellen**. Was damit geschieht, steht
unter [Ladeverlauf & Kosten → Woher die Energie kam](DE-Feature-Charging-History-and-Cost#where-the-energy-came-from).

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-solar-sources.png" alt="Einstellungen Solar & Energiequellen: PV-Leistung, Netzleistung, optionale Hausspeicher-Leistung, ein Schalter für Speicher, die beim Laden positiv melden, und der Wert des eigenen Solarstroms pro kWh" width="460" />
</p>

| Option | Werte | Bedeutung |
| --- | --- | --- |
| **PV-Leistung** | Sensor (Leistung) | Gesamterzeugung deiner Wechselrichter. Für jede Zuordnung nötig. |
| **Netzleistung (+ Bezug / − Einspeisung)** | Sensor (Leistung) | Vorzeichenbehaftete Leistung am Zähler: **positiv beim Bezug**. Pflicht. |
| **Hausspeicher-Leistung (optional)** | Sensor (Leistung) | Optional. Ohne ihn gibt es eine zweiteilige Aufteilung PV/Netz. Erwartet wird **positiv beim Entladen**. |
| **Mein Speichersensor ist beim Laden positiv** | an/aus | Dreht die Vorzeichen-Konvention um. Am Wert selbst ist das nicht erkennbar, deshalb muss es angegeben werden. |
| **Wert des eigenen Solarstroms pro kWh** | Zahl | Was dir eine kWh vom eigenen Dach wert ist — meist deine Einspeisevergütung. **Leer = Solarstrom wird mit deinem Bezugspreis bewertet**, genau wie bisher, und nur der Mix ist neu. |

Beide Leistungssensoren werden gebraucht: Mit nur einem müsste die Aufteilung den
anderen annehmen. Sensoren in **kW** werden automatisch umgerechnet, einer ganz ohne
Einheit wird als **Watt** gelesen.

Die Auswahllisten zeigen Sensoren mit der **Geräteklasse `power`**. Wird dein eigener
Template-Sensor nicht angeboten, gib ihm `device_class: power` (und eine
`unit_of_measurement` von `W` oder `kW`), dann erscheint er.

## evcc-/Wallbox-Brücke

<a id="evcc--wallbox-bridge"></a>

Bildschirm: **Konfigurieren → evcc-/Wallbox-Brücke**. Konzepte, Topic-Tabelle und
Fehlerbehebung stehen unter [evcc- & Wallbox-Brücke](DE-Feature-evcc-and-Wallbox-Bridge).

Veröffentlicht den Live-Zustand des Autos auf deinem MQTT-Broker, sodass evcc, openWB
oder ein Node-RED-Flow einen Ladezustand lesen kann, der kein
[API-Kontingent](DE-Feature-API-Quota) kostet. **Setzt die MQTT-Integration voraus**
— die Brücke veröffentlicht über sie, daher sind hier weder Host noch Passwort eines
Brokers einzutragen. Beim Einschalten erscheint ein zweiter Bildschirm mit der
evcc-Konfiguration zum Einfügen.

| Option | Werte | Bedeutung |
| --- | --- | --- |
| **Dieses Auto per MQTT veröffentlichen** | an/aus | Standard **aus**. Es legt FIN und Fahrzeugzustand auf einen Broker, den auch anderes lesen kann. Wieder ausschalten **entfernt** die veröffentlichten Topics. |
| **Topic-Prefix** | Text | Wurzel der Topics; alles landet unter `<prefix>/<FIN>/`. Standard `bavariandata`. Schrägstriche werden entfernt, und ein MQTT-Platzhalter fällt auf den Standard zurück. |
| **Mit Retain veröffentlichen** | an/aus | Standard **an**, und am besten so gelassen: Nur so findet evcc den Ladezustand sofort beim Start, statt zu warten, bis das Auto sich wieder meldet. Nur abschalten, wenn dein Broker Retain-Nachrichten ablehnt. |

## Fahrten

<a id="trips"></a>

Bildschirm: **Konfigurieren → Fahrten**. Die Konzepte stehen unter
[Fahrten](DE-Feature-Trips).

| Option | Werte | Bedeutung |
| --- | --- | --- |
| **Arbeitszone** | Zonen-Entität | Steuert die Erkennung von Pendelfahrten (Zuhause↔Arbeit). |
| **Standard-Typ** | Privat / Geschäftlich / nicht zuordnen | Standard **Privat**. Womit jede Fahrt eingeordnet wird, die keine erkannte Pendelfahrt Zuhause↔Arbeit ist. Immer nur ein Ausgangspunkt: Eine Korrektur auf der Karte wird nie überschrieben. Wähle **nicht zuordnen**, um jede Fahrt von Hand einzuordnen. |
| **Toleranz für Zwischenstopps** | 0–180 min | Standard **30**. Wie lange das Auto zwischen zwei Fahrten stehen darf, damit beide noch als eine Pendelfahrt zählen — der Supermarkt auf dem Weg zur Arbeit. **0** schaltet die Verkettung ab. Stopps unter ~5 min teilen eine Fahrt ohnehin nie. |
| **Adressen auflösen** | an/aus | Standardmäßig aus. Wenn an, werden Endpunkte **außerhalb** jeder Zone über OpenStreetMap in eine Adresse umgewandelt; gespeichert wird die Adresse, nie die Koordinaten. |
| **Route aufzeichnen** | an/aus | Standardmäßig aus. Wenn an, speichert jede neue Fahrt ihre GPS-Spur — Koordinaten entlang der Fahrt, jeweils mit Zeitpunkt (`[lat, lon, t]`, `t` = Sekunden seit Start) —, damit eine Karte die Route zeichnen und abspielen kann. Die einzige Einstellung, die rohe Koordinaten dauerhaft speichert, deine genauen Start- und Zielpunkte eingeschlossen. Über `get_trips` abrufbar; nie im Export. |
| **Fahrt-Diagnoseaufzeichnung** | an/aus | Standardmäßig aus. Eine Fehlersuche-Hilfe zur Verbesserung der Fahrterkennung: protokolliert die rohe Grundlage (jeden GPS-Punkt mit Takt und Verzögerung, den Ablauf des Schließ-Timers, vollständige Segment-Batches, einen Strom aller Deskriptoren pro Nachricht und eine Nachbetrachtung pro Fahrt) unter den Kennungen `[trip.*]` und schreibt `bavariandata_trip_capture.ndjson` in deinen Konfigurationsordner. Unabhängig von der **Debug-Protokollierung**. Sehr ausführlich und enthält GPS/FIN — für eine Testfahrt ein- und danach wieder ausschalten. Siehe [Fehlerbehebung](DE-Troubleshooting-and-FAQ#capturing-a-drive-for-trip-detection). |

## Debug-Protokollierung

<a id="debug-logging"></a>

Bildschirm: **Konfigurieren → Debug-Protokollierung**.

| Option | Werte | Bedeutung |
| --- | --- | --- |
| **Debug-Protokollierung aktivieren** | an/aus | Standardmäßig aus. Schaltet die ausführliche Protokollierung der Integration frei (getrennt vom Log-Level, den HA pro Integration setzt). **Ausführlich und kann FIN/GPS enthalten** — nur zur Fehlersuche einschalten. Wirkt sofort. |

## Versteckte Optionen

Einige fortgeschrittene Optionen stehen nicht im Menü und lassen sich nur über den
Mechanismus für versteckte Überschreibungen bzw. importierte Optionen setzen. Die
meisten brauchen sie nie.

| Options-Schlüssel | Bedeutung |
| --- | --- |
| `mqtt_keepalive` | MQTT-Keepalive-Intervall des Stream-Clients. |
| `diagnostic_log_interval` | Wie oft der Diagnose-Herzschlag protokolliert wird. |
