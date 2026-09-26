# Entitäten & Geräte

> 🇬🇧 [English version](Feature-Entities-and-Devices)

## Ein Gerät pro FIN

Jede Fahrzeug-Identifizierungsnummer (FIN/VIN) in deinem Konto wird zu einem
eigenen **Gerät** in Home Assistant. Ein separates **„CarData Debug Device“** auf
Ebene der Integration trägt die Diagnose-Entitäten (Stream-Verbindung,
Kontingent, Zeitstempel der letzten Nachricht).

## Deskriptor-Entitäten

Jeder Deskriptor, den BMW streamt, wird zu einer eigenen Entität:

- Gestreamte Deskriptoren werden zu **Sensoren** und **Binärsensoren**. Ihre Namen
  stammen aus einem gepflegten Satz von Titeln (im Katalog und in den
  HA-Übersetzungen hinterlegt); deutsche Namen werden mitgeliefert — öffne ein
  Issue oder einen PR, wenn ein Name nicht passt.
- Zahlenfelder bekommen passende **Geräteklassen**; Strecken nutzen
  `device_class: distance`, der Kilometerstand nutzt
  `state_class: total_increasing`, damit Langzeitstatistiken funktionieren.
- Prozentwerte sind ebenfalls Messwerte mit Langzeitstatistiken, auch ohne
  Geräteklasse: der Tankfüllstand in %, Sitz- und Lenkradheizung, Schiebedach-
  und Türstellungen, der Fortschritt der Vorklimatisierung.
- Jede Entität hat ihren **Quell-Zeitstempel** sowie `cluster` und `category` aus
  dem Katalog als Attribute — die [Dashboard-Karte](DE-The-Dashboard-Card)
  gruppiert die Werte darüber, unabhängig von der Sprache in Home Assistant.
- Einige Deskriptoren sind **Listen** statt einzelner Werte — **Condition Based
  Service** (jede Serviceposition mit Fälligkeitsdatum) und **Check Control
  Meldungen** (die Warnungen des Autos). Ihr Zustand ist die **Anzahl der
  Einträge**, die ganze Liste steht im Attribut **`items`**, z. B.
  `{{ state_attr('sensor.<auto>_check_control_meldungen', 'items') }}` in einem
  Template. Ein Zustand `0` bedeutet, dass das Auto eine leere Liste gemeldet hat.

Der vollständige Katalog der Felder nach Datengruppe steht in
[telematics-fields.md](https://github.com/JustChr/BavarianData/blob/main/docs/reference/telematics-fields.md).

## Abgeleitete & Diagnose-Entitäten

<a id="derived--diagnostic-entities"></a>

Diese sendet BMW nicht — die Integration leitet sie ab. Ihre Namen stammen aus
den Übersetzungen der Integration (deutsche Installationen bekommen also deutsche
Namen).

| Entität | Was sie ist |
| --- | --- |
| **Geladene Energie (gesamt)** | Nur steigender kWh-Zähler (`device_class: energy`, `state_class: total_increasing`). Im Energie-Dashboard hinzufügen. |
| **Geladene Energie (Ladevorgang)** | Setzt sich zu Beginn jedes Ladevorgangs zurück. |
| **Geladene Energie (dieser Monat)** | Summe der geladenen Energie im Monat. |
| **Ladekosten (dieser Monat)** | Kosten im Monat — erst, wenn eine Preisquelle gesetzt ist. |
| **Ladekosten (letzter Ladevorgang)** | Kosten des letzten Ladevorgangs — nur mit Preisquelle. |
| **Ladekosten pro 100 km** | Braucht den Kilometerstand und zwei Ladevorgänge, um eine Strecke zu messen. |
| **Batteriezustand** | Gelernte nutzbare Kapazität (kWh), mit Prozent vom Neuwert, Zahl der Proben und einem Verlauf der Kapazität über die Laufleistung. |
| **Reale Reichweite** | Wie weit das Auto mit der aktuellen Ladung wirklich kommt (km), aus gemessenem Verbrauch und nutzbarer Kapazität — mit der Prognose des Autos und der Abweichung als Attribute. |
| **Fahrstrecke (dieser Monat)** | Strecke im Monat plus Aufteilung geschäftlich/privat/Pendeln. |
| **Fahrt aktiv** | Binärsensor: `an`, solange eine Fahrt läuft, mit der bisherigen Fahrt als Attributen. Bewusst *kein* „bewegt sich“-Sensor — er bleibt nach der Ankunft noch eine Weile an; siehe [Fahrten](DE-Feature-Trips#seeing-the-drive-thats-happening-now). |
| **Reifenzustand** | BMWs Gesamturteil zum montierten Satz, plus etwaige Fehler der Gegenseite. |
| **Reifen vorne links / vorne rechts / hinten links / hinten rechts** | Verschleiß-Ampel pro Rad (`green`/`yellow`/`red`/`grey`), mit Restlaufleistung bis zum Wechsel, Defektstatus, Saison, Dimension, Profil und Montagedatum als Attributen. |
| **Verbleibendes API-Kontingent** | Diagnose: übrige Anfragen im Fenster von 50 pro 24 h. |
| **Ladezustand (integrationsseitig vorhergesagt) / Vorhergesagte Ladegeschwindigkeit** | Hochgerechnete Ladezustands-Helfer (brauchen die Datengruppe Elektrofahrzeug). |
| **Stream-Verbindungsstatus** | Diagnose: Zustand der MQTT-Verbindung. |
| **Letzte empfangene Nachricht** | Diagnose: Zeitstempel der letzten Stream-Nachricht. |
| **Letzter Telematik-API-Aufruf** | Diagnose: Zeitstempel des letzten REST-Aufrufs. |

Die Entitäten für Laden, Ladezustand, Batteriezustand und reale Reichweite gibt es
nur für ein Auto mit **Hochvoltbatterie**; sie erscheinen, sobald das Auto zum
ersten Mal Batteriedaten sendet. Ein Benziner oder Diesel bekommt keine davon. Hat
eine frühere Version sie ihm doch angelegt, werden sie beim ersten Start nach dem
Update entfernt, weil sie nie einen Wert haben konnten — ihr alter Verlauf kann
danach unter **Entwicklerwerkzeuge → Statistiken** aufgeführt sein und lässt sich
dort löschen.

Der **Kraftstoff im Tank** (*Tankinhalt*) ist ein Volumen-Sensor mit
Langzeitstatistiken. Er übernimmt die Einheit, die das Auto sendet — Liter, oder
Gallonen bei einem Auto, das sie meldet —, und Home Assistant rechnet ihn wie jeden
anderen in deine Anzeigeeinheit um.

Die Reifen-Entitäten gibt es nur für Räder, die BMW tatsächlich meldet. Viele Autos
haben keinen Reifen-Servicedatensatz hinterlegt, dann werden keine angelegt — BMW
hat dann keine Daten, das ist kein Fehler. Befüllt werden sie von der
[täglichen Aktualisierung](DE-Feature-API-Quota#the-daily-refresh) und von
`bavariandata.fetch_tyre_diagnosis`.

Weil diese Daten eine Anfrage kosten und höchstens einmal täglich aktualisiert
werden, **werden sie gespeichert und über Neustarts hinweg wiederhergestellt** —
die Sensoren zeigen danach den letzten Wert statt `unknown`. Jeder hat ein
Attribut `fetched_at` mit dem Zeitpunkt dieses Abrufs, ein einen Tag alter Wert
ist also als solcher erkennbar.

## Fahrzeugbild

Jede FIN bekommt außerdem eine **Bild**-Entität mit BMWs gerendertem Bild des
Autos. Es wird **zwischengespeichert und übersteht Neustarts**, verbraucht also
nicht bei jedem Start Kontingent; manuell aktualisieren kannst du es mit
`bavariandata.fetch_vehicle_image`.

## Geräte-Tracker

Jede FIN bekommt einen **device_tracker** („Standort“) mit der Position des
Fahrzeugs aus dem GPS-Stream, nutzbar auf der HA-Karte und in zonenbasierten
Automationen.

## Welche Verriegelungs-Entität verwenden

<a id="which-lock-entity-to-use"></a>

Das Auto meldet seine Zentralverriegelung über **zwei** Deskriptoren, und die
verhalten sich sehr unterschiedlich:

| Entität | Deskriptor | Aktualisierung |
| --- | --- | --- |
| **Zustand der Türen** | `vehicle.cabin.door.status` | **Über den Stream** — folgt jedem Ver- und Entriegeln innerhalb von Sekunden |
| **Status der Türen** | `vehicle.cabin.door.lock.status` | **Nur REST** — BMW streamt ihn nicht, er wird also höchstens bei der [täglichen Aktualisierung](DE-Feature-API-Quota#the-daily-refresh) erneuert und kann tagelang veraltet sein |

Trotz des Namens ist **Zustand der Türen die richtige Wahl für Automationen.**
Beide haben dieselben Werte — `Gesichert`, `Verriegelt`, `Teilweise verriegelt`,
`Entriegelt` — und beide erscheinen in der Zustandsauswahl des
Automations-Editors, eine Bedingung lässt sich also aus der Liste wählen statt von
Hand tippen. Auch die [Dashboard-Karte](DE-The-Dashboard-Card) bevorzugt für ihre
Zentralverriegelung den gestreamten Wert und greift nur bei Autos, die ihn nie
streamen, auf `Status der Türen` zurück.

„Teilweise verriegelt“ (BMWs `SELECTIVE-LOCKED`) bedeutet, dass jede Tür außer der
Fahrertür verriegelt ist — der Zustand nach einem Entriegeln aus der Ferne.

Der Zustand **offen/geschlossen** jeder Tür ist wiederum getrennt und gestreamt:
die vier Binärsensoren `Status der Tür …`.

## Entitätsnamen und deine Sprache

<a id="entity-names-and-your-language"></a>

Entitätsnamen folgen der Sprache von Home Assistant. Drei werden mitgeliefert:

| HA-Sprache | Was du bekommst |
| --- | --- |
| **Deutsch** | BMWs eigene deutsche Namen — *Reifendruck (vorne links)*, *Reifenzustand* |
| **English** | US-Schreibweisen — *Tire pressure (front left)*, *Tire Condition* |
| **English (UK)** | Britische Schreibweisen — *Tyre pressure (front left)*, *Tyre Condition* |

Umschalten unter *Profil → Sprache*; die Namen wechseln beim nächsten Neuladen.
Die mitgelieferte Karte folgt derselben Einstellung.

**Entitäts-IDs ändern sich dabei nie.** Sie entstehen einmalig aus dem
Deskriptor, den BMW sendet — `sensor.<auto>_tire_pressure_front_left` —, also
funktionieren Automationen, Dashboards und Templates in jeder Sprache weiter, und
auch eine deutsche Installation schreibt `tire` im YAML. Übersetzt wird nur der
Anzeigename.

Englisch ist US-Englisch, weil BMWs eigene Feldnamen US-Englisch sind
(`vehicle.chassis.axle.row1.wheel.left.tire.pressure`) — der gelesene Name passt
damit zur getippten ID. Britisches Englisch überschreibt nur die Wörter, die sich
tatsächlich unterscheiden, und erbt alles andere; deshalb erscheint eine neue
Bezeichnung dort sofort mit, ohne auf eine Übersetzung zu warten.

## Warum manche Entitäten „nicht verfügbar“ sind

Entitäten behalten ihre Attribute `cluster`/`category` auch wiederhergestellt oder
nicht verfügbar, weil die Datengruppen-Ansichten der Karte davon abhängen. Eine
Entität kann nicht verfügbar sein, bis das Auto diesen Deskriptor das nächste Mal
streamt — löse in der MyBMW App ein Ver- oder Entriegeln aus, um eine
Aktualisierung anzustoßen.
