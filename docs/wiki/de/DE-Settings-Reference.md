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
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-configure-menu.png?v=2026-10-17" alt="Optionsmenü von BavarianData (Hauptmenü seit v0.9.20)" width="460" />
</p>

Die Menüeinträge unten sind so geschrieben, wie sie in der deutschen Oberfläche
erscheinen (der Screenshot zeigt die englische).

| Menüeintrag | Art | Was er tut |
| --- | --- | --- |
| **Gestreamte Daten auswählen** | Einrichtung | Öffnet die [Auswahl der Datengruppen](DE-Getting-Started-4-Choose-Data) erneut und schaltet die Felder dann mit dem Ein-Klick-Lesezeichen **Activate BMW data** ein (derselbe Aktivator wie bei der geführten Einrichtung). Nur hinzufügend — fügt die gewählten Datengruppen dem laufenden Stream hinzu, entfernt aber nie bereits gestreamte Felder. |
| **Fahrzeugbild** | Einstellungen ⚡ | Woher das Bild des Autos kommt: BMW CarData oder BMWs Online-Konfigurator — unten. |
| **Ladekosten & Verlauf** | Einstellungen | Preisquelle, Aufbewahrung, Statistiken — unten. |
| **Solar & Energiequellen** | Einstellungen | Woher die Energie jeder Ladung kam: PV, Hausspeicher, Netz — unten. |
| **evcc-/Wallbox-Brücke** | Einstellungen | Den Live-Zustand des Autos für eine Ladesteuerung per MQTT veröffentlichen — unten. |
| **Fahrten** | Einstellungen | Arbeitszone, Standard-Typ, Toleranz für Zwischenstopps, Adressauflösung und Routenaufzeichnung — unten. |
| **Kilometerkontingent** | Einstellungen | Ein Leasing oder eine jährliche Kilometergrenze pro Auto — unten. |
| **Automatische Aktualisierung** | Einstellungen ⚡ | Nach dem Start von Home Assistant bei BMW aufholen — unten. |
| **Jetzt von BMW abrufen** | Menü ⚡ | Daten sofort bei BMW abfragen, statt auf die tägliche Aktualisierung zu warten — nächste Tabelle. |
| **Verbindung** | Menü | Die Verbindung zu BMW am Laufen halten, dazu die Debug-Protokollierung — die Tabelle danach. |

**Konfigurieren → Jetzt von BMW abrufen**

| Menüeintrag | Art | Was er tut |
| --- | --- | --- |
| **Fahrzeuge suchen** | Aktion ⚡ | Ruft die Fahrzeugzuordnungen ab und legt noch unbekannte Fahrzeuge als Gerät an. Siehe [Dienste](DE-Services-Reference). |
| **Telematikdaten abrufen** | Aktion ⚡ | Siehe [Dienste](DE-Services-Reference). |
| **Fahrzeug-Basisdaten abrufen** | Aktion ⚡ | " |
| **Ladehistorie abrufen** | Aktion ⚡ | " |
| **Reifendiagnose abrufen** | Aktion ⚡ | " |
| **Ladeeinstellungen abrufen** | Aktion ⚡ | Standortbasierte Ladeeinstellungen. " |

**Konfigurieren → Verbindung**

| Menüeintrag | Art | Was er tut |
| --- | --- | --- |
| **Neu bei BMW autorisieren** | Aktion | Führt die Geräteautorisierung erneut aus (nachdem BMW das Token ungültig gemacht hat). |
| **Token jetzt erneuern** | Aktion | Erzwingt eine Erneuerung des OAuth-Tokens. |
| **Telemetrie-Container zurücksetzen** | Aktion ⚡ | Ersetzt die beiden REST-Container der Integration durch frische (je etwa drei Anfragen). Nutze das, wenn Telematik-Abrufe mit CU-105 oder CU-121 fehlschlagen. |
| **Debug-Protokollierung** | Einstellungen | Schalter für ausführliche Protokollierung — unten. |

⚡ = verbraucht eine (oder mehrere) deiner [50 Anfragen / 24 h](DE-Feature-API-Quota).

## Fahrzeugbild

Bildschirm: **Konfigurieren → Fahrzeugbild**.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-vehicle-image.png" alt="Einstellungen zum Fahrzeugbild: ein Auswahlfeld, Picture source, auf BMW configurator (360°, unofficial) gestellt (Screenshot englisch)" width="460" />
</p>

| Option | Werte | Bedeutung |
| --- | --- | --- |
| **Bildquelle** | BMW CarData (offiziell) · BMW-Konfigurator (360°, inoffiziell) | Standard **BMW CarData**: BMWs eine Seitenansicht des Autos, eine deiner [50 Anfragen am Tag](DE-Feature-API-Quota) pro Auto. **BMW-Konfigurator** zeichnet genau dein Auto so, wie es BMWs Online-Konfigurator tut, aus Modell, Lack und Werksausstattung in den Fahrzeug-Basisdaten: eine 360°-Drehansicht in der [Karte](DE-The-Dashboard-Card#übersicht), die du mit der Maus oder dem Finger drehst, und die echte Lackfarbe am [Auto von oben](DE-The-Dashboard-Card#die-form-des-autos). Das kostet keine Anfragen, gehört aber nicht zu CarData: Es ist der Dienst hinter BMWs Konfigurator-Webseite, den BMW jederzeit ändern oder sperren kann. |

**OK** lädt das Bild sofort und behält es über Neustarts hinweg. Mit dem
Konfigurator wartet es nur auf die 360°-Ansicht (einige Sekunden pro Auto); der
Rest folgt innerhalb etwa einer Minute im Hintergrund: dieselbe Ansicht mit Licht
und bei Nacht, das Innenraum-Panorama, das Foto von oben (nur MINIs) und die
Namen und Bilder der Werksoptionen für die
[Ausstattungs-Ansicht](DE-The-Dashboard-Card#equipment-view-equipment) der Karte.
Bei mehreren Autos wird die Einstellung gespeichert, sobald sich mindestens
eins zeichnen lässt: Ein Auto, das der Konfigurator nicht zeichnen kann, behält
sein offizielles Bild, und eine Abschlussmeldung nennt es. Hat dieses Auto noch
kein offizielles Bild, wird eins geladen (eine Anfrage). Warum es sich nicht
zeichnen ließ, steht im Protokoll von Home Assistant. Lässt sich kein Auto
zeichnen, wird nichts gespeichert, und der Bildschirm nennt das Auto und den
Grund (kein BMW oder MINI, die Fahrzeug-Basisdaten sind noch nicht da, der
Konfigurator hat nicht geantwortet oder kein Bild dieses Modells). Ein später
hinzukommendes Auto wird gezeichnet, sobald es erscheint, oder bekommt sein
offizielles Bild, wenn das nicht geht.

Geladen werden diese Bilder nur dann: wenn du hier **OK** drückst, und einmal für
ein Auto, wenn es neu hinzukommt. Nichts erneuert sie nach Zeitplan oder nach
einem Neustart oder Update. Drück also erneut **OK**, um sie neu zu laden (zum
Beispiel nach einem Update von BavarianData, um Bilder zu bekommen, die eine
neuere Version hinzufügt).

Der Konfigurator zeichnet BMW- und MINI-Autos, auch Modelle, die nicht mehr
verkauft werden. Von BMW-Modellen hat er keine Ansicht von oben, und sein
Innenraum zeigt BMWs Standard-Ausstattung, weil BMWs Fahrzeugdaten die Polsterung
eines Autos nicht nennen.
Gibt es für ein Auto kein Konfigurator-Bild, zeigt die Karte das offizielle.

## Automatische Aktualisierung

<a id="automatic-data-refresh"></a>

Bildschirm: **Konfigurieren → Automatische Aktualisierung**.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-data-refresh.png" alt="Einstellungen der automatischen Aktualisierung: ein Schalter, Catch up after a restart or an outage, eingeschaltet (Screenshot englisch)" width="460" />
</p>

Der Live-Stream überträgt nur, was sich ändert, während Home Assistant zuhört. Was
ein Auto gemeldet hat, während Home Assistant aus war oder eine Netzstörung die
Verbindung zu BMW unterbrochen hat — ein beendeter Ladevorgang, das Verriegeln,
eine Fahrt —, wird beim Wiederverbinden nicht nachgeliefert, und ein Auto, das mit
gleichbleibender Leistung lädt, kann danach stundenlang schweigen. Deshalb fragt
die Integration einmal pro Auto den aktuellen Stand bei BMW ab: zwei Minuten nach
dem Start und wenn die Verbindung nach einer Unterbrechung von fünf Minuten oder
mehr zurückkommt. BMW liefert den letzten Wert, den jedes Auto
geschickt hat; so wird aufgeholt, ohne das Auto aufzuwecken — einen neueren Wert
als den zuletzt gesendeten kann das nicht liefern.

| Option | Werte | Bedeutung |
| --- | --- | --- |
| **Nach Neustart oder Unterbrechung aufholen** | an/aus | Standardmäßig **an**. Eine Abfrage pro Auto von deinen [50 pro Tag](DE-Feature-API-Quota). Entfällt, wenn BMW in der letzten Stunde schon gefragt wurde (die tägliche Aktualisierung und manuelle Abrufe zählen mit), wiederholte Neustarts oder eine immer wieder abreißende Verbindung kosten also nichts zusätzlich; ein Ladevorgang, der beim Neustart lief, wird trotzdem geprüft, wenn die letzte Abfrage älter als eine halbe Stunde ist. Lässt immer 10 Abfragen für deine eigenen Dienstaufrufe übrig. Wirkt sofort. |

Ein Ladevorgang, der beim Neustart lief, wird durch diese Antwort geklärt: lädt
das Auto noch, läuft derselbe Ladevorgang weiter, ist es fertig, wird er als
beendet abgelegt — siehe [Neustart während das Auto lädt](DE-Feature-Charging-History-and-Cost#restarting-while-the-car-is-charging).

## Ladekosten & Verlauf

<a id="charging-costs--history"></a>

Bildschirm: **Konfigurieren → Ladekosten & Verlauf**. Die Konzepte stehen unter
[Ladeverlauf & Kosten](DE-Feature-Charging-History-and-Cost).

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-charging-costs.png?v=2026-10-11b" alt="Einstellungen Ladekosten & Verlauf: Preisquelle, fester Preis, Preis-Entität, Währung, Wallbox-Energiesensor, Ladeverluste und Aufbewahrung" width="460" />
</p>

**Mehrere Fahrzeuge im Konto?** Der Bildschirm fragt zuerst nach dem Fahrzeug.
Jedes Auto behält seinen eigenen **Wallbox-Energiesensor** und seine eigenen
**Ladeverluste**: Zwei Autos können an zwei Wallboxen laden, über zwei
verschiedene Ladegeräte. Preis, Währung, Verlauf und Statistiken gelten für
alle Autos; sie stehen im Abschnitt **Alle Fahrzeuge** unter den Einstellungen
des Autos. Mit nur einem Auto gibt es keine Fahrzeugabfrage.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-charging-costs-second-car.png?v=2026-10-11b" alt="Ladekosten für ein zweites Auto: oben sein eigener Wallbox-Sensor und seine Ladeverluste, darunter die gemeinsamen Preiseinstellungen im Abschnitt Alle Fahrzeuge" width="460" />
</p>

| Option | Werte | Bedeutung |
| --- | --- | --- |
| **Preisquelle** | Keine · Fester Preis · Preis-Entität | Wie aus Energie Geld wird. „Keine“ legt **gar keine Kosten-Entitäten** an. |
| **Fester Preis pro kWh** | Zahl | Preis pro kWh bei „Fester Preis“. In diesem Modus Pflicht. |
| **Preis-Entität** | Sensor / input_number | Live-Preisquelle (Tibber/Nordpool/aWATTar) bei „Preis-Entität“. Wird während des Ladens abgetastet. In diesem Modus Pflicht. |
| **Währung** | Text | Währungscode für die Kosten-Entitäten. |
| **Wallbox-Energiesensor** | Sensor | Optional. Der **kumulative** Energiezähler der Wallbox (`total_increasing`, kein Zähler pro Ladevorgang). Sein gemessener Netzwert ersetzt die batterieseitige Schätzung im Datensatz, in den Monatssummen und bei den Kosten. Nach drei Ladungen zu Hause [treibt er auch den geschätzten Ladezustand](DE-Feature-evcc-and-Wallbox-Bridge#it-also-drives-the-state-of-charge-estimate), während das Auto zu Hause lädt. Wird nur für Ladungen in deiner Zone „Zuhause“ gelesen und abgelehnt, wenn er nicht stimmen kann — siehe [die Seite zur Brücke](DE-Feature-evcc-and-Wallbox-Bridge#when-the-reading-is-refused). |
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

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-evcc-bridge.png?v=2026-10-11b" alt="evcc-/Wallbox-Brücke mit einem Auto: dieses Auto per MQTT veröffentlichen, Topic-Prefix und mit Retain veröffentlichen" width="460" />
</p>

**Mehrere Fahrzeuge im Konto?** Der Bildschirm fragt zuerst nach dem Fahrzeug,
und **Dieses Auto per MQTT veröffentlichen** gilt je Auto, sodass ein Auto, das
woanders lädt, samt FIN vom Broker fernbleiben kann. Schaltest du ein Auto aus,
werden seine Topics entfernt. **Topic-Prefix** und **Mit Retain veröffentlichen**
gelten für alle Autos, im eingeklappten Abschnitt **Alle Fahrzeuge**.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-evcc-bridge-second-car.png?v=2026-10-11b" alt="evcc-/Wallbox-Brücke für ein zweites Auto: sein eigener Schalter, darunter Topic-Prefix und Retain gemeinsam im Abschnitt Alle Fahrzeuge" width="460" />
</p>

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

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-configure-trips.png?v=2026-10-11b" alt="Konfigurieren, Fahrten mit einem Auto: Arbeitszone, Standard-Typ, Toleranz für Zwischenstopps, Fahrterkennung, Route aufzeichnen, Adressen auflösen und Fahrt-Diagnoseaufzeichnung" width="460" />
</p>

**Mehrere Fahrzeuge im Konto?** Fahrten fragt zuerst nach dem Fahrzeug, und jedes
Auto behält seine eigene **Arbeitszone**, seinen **Standard-Typ**, seine
**Toleranz für Zwischenstopps**, **Fahrterkennung** und **Route aufzeichnen**:
ein Dienstwagen und ein privates Auto, oder ein älteres Auto neben einem, das
während der Fahrt streamt. **Adressen auflösen** und die
**Fahrt-Diagnoseaufzeichnung** gelten für alle Autos; sie stehen im
eingeklappten Abschnitt **Alle Fahrzeuge** unten im Formular jedes Autos. Ein
später hinzugekommenes Auto startet mit den Einstellungen, die vorher galten.
Mit nur einem Auto gibt es keine Fahrzeugabfrage.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-vehicle-picker.png?v=2026-10-11b" alt="Die Fahrzeugabfrage bei zwei Autos: eine Fahrzeug-Auswahl und die Schaltfläche Weiter" width="460" />
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-configure-trips-second-car.png?v=2026-10-11b" alt="Fahrteinstellungen für ein zweites Auto mit Berichten am Fahrtende, darunter der geöffnete gemeinsame Abschnitt Alle Fahrzeuge" width="460" />
</p>

| Option | Werte | Bedeutung |
| --- | --- | --- |
| **Arbeitszone** | Zonen-Entität | Steuert die Erkennung von Pendelfahrten (Zuhause↔Arbeit). |
| **Standard-Typ** | Privat / Geschäftlich / nicht zuordnen | Standard **Privat**. Womit jede Fahrt eingeordnet wird, die keine erkannte Pendelfahrt Zuhause↔Arbeit ist. Immer nur ein Ausgangspunkt: Eine Korrektur auf der Karte wird nie überschrieben. Wähle **nicht zuordnen**, um jede Fahrt von Hand einzuordnen. |
| **Toleranz für Zwischenstopps** | 0–180 min | Standard **30**. Wie lange das Auto zwischen zwei Fahrten stehen darf, damit beide noch als eine Pendelfahrt zählen — der Supermarkt auf dem Weg zur Arbeit. **0** schaltet die Verkettung ab. Stopps unter ~5 min teilen eine Fahrt ohnehin nie. |
| **Adressen auflösen** | an/aus | Standardmäßig aus. Wenn an, werden Endpunkte **außerhalb** jeder Zone über OpenStreetMap in eine Adresse umgewandelt; gespeichert wird die Adresse, nie die Koordinaten. |
| **Fahrterkennung** | Live / Berichte am Fahrtende | Standard **Live**: Fahrten folgen der Position des Autos während der Fahrt. **Berichte am Fahrtende** ist für Autos, die während der Fahrt nichts senden (iDrive 6 und älter): eine Fahrt pro Fahrzeugbericht, ab dem Ort des vorigen Berichts, mit ausgeschalteter Live-Erkennung. Startzeiten kommen von `bavariandata.mark_trip_start`. Siehe [Fahrten](DE-Feature-Trips#older-cars-that-report-only-at-the-end-of-a-trip). |
| **Route aufzeichnen** | an/aus | Standardmäßig aus. Wenn an, speichert jede neue Fahrt ihre GPS-Spur — Koordinaten entlang der Fahrt, jeweils mit Zeitpunkt (`[lat, lon, t]`, `t` = Sekunden seit Start) —, damit eine Karte die Route zeichnen und abspielen kann. Die einzige Einstellung, die rohe Koordinaten dauerhaft speichert, deine genauen Start- und Zielpunkte eingeschlossen. Über `get_trips` abrufbar; nie im Export. |
| **Handy** / **Handy ist im Auto** | `device_tracker` / Binärsensor | Nur auf dem Folgebildschirm **Route vom Handy**, der für ein Auto mit **Berichte am Fahrtende** und eingeschalteter **Route aufzeichnen** erscheint. Die Positionen des Handys zeichnen die Route der Fahrt; Strecke und Zeiten bleiben die des Autos. Schaltet der Sensor ein, markiert das den Start der Fahrt. Siehe [Fahrten](DE-Feature-Trips#die-route-vom-handy). |
| **Fahrt-Diagnoseaufzeichnung** | an/aus | Standardmäßig aus. Eine Fehlersuche-Hilfe zur Verbesserung der Fahrterkennung: protokolliert die rohe Grundlage (jeden GPS-Punkt mit Takt und Verzögerung, den Ablauf des Schließ-Timers, vollständige Segment-Batches, einen Strom aller Deskriptoren pro Nachricht und eine Nachbetrachtung pro Fahrt) unter den Kennungen `[trip.*]` und schreibt `bavariandata_trip_capture.ndjson` in deinen Konfigurationsordner. Unabhängig von der **Debug-Protokollierung**. Sehr ausführlich und enthält GPS/FIN — für eine Testfahrt ein- und danach wieder ausschalten. Siehe [Fehlerbehebung](DE-Troubleshooting-and-FAQ#capturing-a-drive-for-trip-detection). |

## Kilometerkontingent

<a id="mileage-allowance"></a>

Maske: **Konfigurieren → Kilometerkontingent**. Was die Werte bedeuten, steht
unter [Kilometer & Kontingent](DE-Feature-Mileage).

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-mileage-allowance.png" alt="Kilometerkontingent: None, Lease or financing oder Yearly limit (e.g. insurance), mit einem Next-Knopf" width="460" />
</p>

Die erste Maske fragt, welche Art Grenze das Auto hat: **Keins**, **Leasing oder
Finanzierung** oder **Jahresgrenze (z. B. Versicherung)**. **Weiter** öffnet das
Formular dieser Art; **Keins** entfernt das Kontingent des Autos. Das Kontingent
gehört immer dem Auto selbst: Bei mehreren Autos kommt zuerst die Fahrzeugfrage,
und ein später hinzugefügtes Auto beginnt ohne. Es gilt sofort, in der Karte und
im Sensor.

| Option | Werte | Bedeutung |
| --- | --- | --- |
<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-mileage-lease.png" alt="Leasing oder Finanzierung: Beginn, Ende, Kilometer pro Jahr und Kilometerstand bei Beginn, ausgefüllt für ein dreijähriges Leasing mit 15.000 km im Jahr" width="460" />
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-mileage-yearly.png" alt="Jahresgrenze: der Tag, an dem das Jahr beginnt, die Kilometer pro Jahr und der Kilometerstand an dem Tag" width="460" />
</p>

| **Beginn** / **Jahr beginnt am** | Datum | Der Tag, an dem das Leasing begann, oder der erste Tag deines Versicherungsjahres (eine Jahresgrenze beginnt jedes Jahr an diesem Tag neu). |
| **Ende** | Datum | Nur Leasing: der Tag der Rückgabe. Muss nach dem Beginn liegen. |
| **Kilometer pro Jahr** | km oder mi (siehe unten) | Aus Vertrag oder Police. Das Gesamtkontingent eines Leasings ist dieser Wert mal die Laufzeit. |
| **Kilometerstand bei Beginn** / **Kilometerstand an dem Tag** | km oder mi | Der Stand am Starttag, aus dem Übergabeprotokoll. Vorbelegt mit dem heutigen Stand, solange keiner gespeichert ist; bei einer Jahresgrenze der Stand am letzten Jahresbeginn. |

Die Felder zeigen, welche Einheit sie erwarten. Es ist die des Autos: die
Einheit, in der sein Kilometerstand-Sensor angezeigt wird (eine britische
Installation, metrisch mit dem Kilometerstand auf Meilen umgestellt, gibt also
Meilen ein), sonst die Anzeige-Einstellung des Autos, wenn BMW sie streamt,
sonst das Einheitensystem von Home Assistant. Karte und Service-Kalender
nutzen dieselbe Regel. Frühere Versionen folgten nur dem
Einheitensystem, sodass auf einer metrischen Installation ein in Meilen
eingegebenes Kontingent als km gespeichert wurde: Wirkt deins zu klein, gib es
einmal neu ein.

## Debug-Protokollierung

<a id="debug-logging"></a>

Bildschirm: **Konfigurieren → Verbindung → Debug-Protokollierung**.

| Option | Werte | Bedeutung |
| --- | --- | --- |
| **Debug-Protokollierung aktivieren** | an/aus | Standardmäßig aus. Schaltet die ausführliche Protokollierung der Integration frei (getrennt vom Log-Level, den HA pro Integration setzt). **Ausführlich und kann FIN/GPS enthalten** — nur zur Fehlersuche einschalten. Wirkt sofort. Sind mehrere BMW-Konten eingerichtet, gilt der Log-Level gemeinsam: Die Protokollierung bleibt ausführlich, solange **irgendein** Eintrag sie anhat, und umfasst alle Konten ([Mehrere Fahrzeuge & Konten](DE-Feature-Multiple-Cars-and-Accounts#telling-the-cars-apart)). |

## Versteckte Optionen

Einige fortgeschrittene Optionen stehen nicht im Menü und lassen sich nur über den
Mechanismus für versteckte Überschreibungen bzw. importierte Optionen setzen. Die
meisten brauchen sie nie.

| Options-Schlüssel | Bedeutung |
| --- | --- |
| `mqtt_keepalive` | MQTT-Keepalive-Intervall des Stream-Clients. |
| `diagnostic_log_interval` | Wie oft der Diagnose-Herzschlag protokolliert wird. |
