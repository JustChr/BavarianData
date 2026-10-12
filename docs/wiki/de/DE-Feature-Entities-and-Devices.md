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
  HA-Übersetzungen hinterlegt); Namen in neun weiteren Sprachen werden
  mitgeliefert, jeweils mit BMWs eigener Bezeichnung des Felds — öffne ein
  Issue oder einen PR, wenn ein Name nicht passt.
- Zahlenfelder bekommen passende **Geräteklassen**; Strecken nutzen
  `device_class: distance`, der Kilometerstand nutzt
  `state_class: total_increasing`, damit Langzeitstatistiken funktionieren.
- Prozentwerte sind ebenfalls Messwerte mit Langzeitstatistiken, auch ohne
  Geräteklasse: der Tankfüllstand in %, Sitz- und Lenkradheizung, Schiebedach-
  und Türstellungen, der Fortschritt der Vorklimatisierung.
- Jede Entität hat ihren **Quell-Zeitstempel** (`timestamp`: wann das Auto den
  Wert gemessen hat, nicht wann Home Assistant ihn empfing) sowie `cluster` und
  `category` aus dem Katalog als Attribute — die [Dashboard-Karte](DE-The-Dashboard-Card)
  gruppiert die Werte darüber, unabhängig von der Sprache in Home Assistant.
- Einige Deskriptoren sind **Listen** statt einzelner Werte — **Condition Based
  Service** (jede Serviceposition mit Fälligkeitsdatum) und **Check Control
  Meldungen** (die Warnungen des Autos). Ihr Zustand ist die **Anzahl der
  Einträge**, die ganze Liste steht im Attribut **`items`**, z. B.
  `{{ state_attr('sensor.<auto>_check_control_meldungen', 'items') }}` in einem
  Template. Ein Zustand `0` bedeutet, dass das Auto eine leere Liste gemeldet hat —
  oder, bei Check Control, dass BMW gar keine Meldungen geliefert hat, so kommt
  eine erledigte Warnung an. Erledigte Check-Control-Meldungen bleiben im Attribut
  **`resolved`** erhalten (die letzten zehn, neueste zuerst), jeweils mit
  `last_reported` und `resolved_at`.

Der vollständige Katalog der Felder nach Datengruppe steht in
[telematics-fields.md](https://github.com/JustChr/BavarianData/blob/main/docs/reference/telematics-fields.md).

## Abgeleitete & Diagnose-Entitäten

<a id="derived--diagnostic-entities"></a>

Diese sendet BMW nicht — die Integration leitet sie ab. Ihre Namen stammen aus
den Übersetzungen der Integration (sie heißen also auch in deiner Sprache
passend).

| Entität | Was sie ist |
| --- | --- |
| **Geladene Energie (gesamt)** | Nur steigender kWh-Zähler (`device_class: energy`, `state_class: total_increasing`). Zählt jede Ladung; für das Energie-Dashboard die [Reihe für zu Hause](DE-Feature-Energy-and-Statistics#adding-to-the-energy-dashboard) nehmen. |
| **Geladene Energie (Ladevorgang)** | Setzt sich zu Beginn jedes Ladevorgangs zurück. |
| **Geladene Energie (dieser Monat)** | Summe der geladenen Energie im Monat. |
| **Ladekosten (dieser Monat)** | Kosten im Monat — erst, wenn eine Preisquelle gesetzt ist. |
| **Ladekosten (letzter Ladevorgang)** | Kosten des letzten Ladevorgangs — nur mit Preisquelle. |
| **Ladekosten pro 100 km** | Braucht den Kilometerstand und zwei Ladevorgänge, um eine Strecke zu messen. Nicht bei einem [Plug-in-Hybrid](DE-Feature-Efficiency-and-Range#plug-in-hybride). |
| **Batteriezustand** | Gelernte nutzbare Kapazität (kWh), mit Prozent vom Neuwert, Zahl der Proben und einem Verlauf der Kapazität über die Laufleistung. |
| **Reale Reichweite** | Wie weit das Auto mit der aktuellen Ladung wirklich kommt (km), aus gemessenem Verbrauch und nutzbarer Kapazität — mit der Prognose des Autos und der Abweichung als Attribute. Zeigt `unknown`, bis dein Ladeverlauf einen Wert trägt; das Attribut `status` sagt, warum ([Details](DE-Feature-Efficiency-and-Range#the-real-range-sensor)). Nicht bei einem [Plug-in-Hybrid](DE-Feature-Efficiency-and-Range#plug-in-hybride). |
| **Fahrstrecke (dieser Monat)** | Strecke im Monat plus Aufteilung geschäftlich/privat/Pendeln. |
| **Fahrt aktiv** | Binärsensor: `an`, solange eine Fahrt läuft, mit der bisherigen Fahrt als Attributen. Bewusst *kein* „bewegt sich“-Sensor — er bleibt nach der Ankunft noch eine Weile an; siehe [Fahrten](DE-Feature-Trips#seeing-the-drive-thats-happening-now). |
| **Reifenzustand** | BMWs Gesamturteil zum montierten Satz, plus etwaige Fehler der Gegenseite. |
| **Reifen vorne links / vorne rechts / hinten links / hinten rechts** | Verschleiß-Ampel pro Rad (`green`/`yellow`/`red`/`grey`), mit Restlaufleistung bis zum Wechsel, Defektstatus, Saison, Dimension, Profil und Montagedatum als Attributen. |
| **Verbleibendes API-Kontingent** | Diagnose: übrige Anfragen im Fenster von 50 pro 24 h. |
| **Ladezustand (integrationsseitig vorhergesagt) / Vorhergesagte Ladegeschwindigkeit** | Hochgerechnete Ladezustands-Helfer (brauchen die Datengruppe Elektrofahrzeug) — siehe [welche Ladezustands-Entität verwenden](#which-state-of-charge-entity-to-use). |
| **Vorhergesagte Zeit bis zum Ladeziel** | Minuten, bis eine Ladung **zu Hause** das Ladeziel des Autos erreicht, berechnet aus dem [Wallbox-Zähler](DE-Feature-evcc-and-Wallbox-Bridge#time-to-the-charge-target). `0`, wenn nicht geladen wird; unbekannt unterwegs, in den ersten Minuten einer Ladung und während eine PV-Ladung pausiert. Nur mit verknüpftem Wallbox-Zähler. |
| **Stream-Verbindungsstatus** | Diagnose: Zustand der MQTT-Verbindung. |
| **Letzte empfangene Nachricht** | Diagnose: Zeitstempel der letzten Stream-Nachricht. |
| **Letzte Fahrzeugmeldung** | Diagnose, **standardmäßig deaktiviert**: wann das Auto zuletzt seinen Kilometerstand gemeldet hat, nach der Uhr des Autos. Weicht von *Letzte empfangene Nachricht* ab, wenn ein Auto ohne Empfang geparkt war und verspätet meldet. |
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

<a id="device-tracker"></a>

Jede FIN bekommt einen **device_tracker** („Standort“) mit der Position des
Fahrzeugs aus dem GPS-Stream, nutzbar auf der HA-Karte und in zonenbasierten
Automationen.

**Die Position als normale Sensoren?** Manche Erweiterungen lassen nur
`sensor.*`-Entitäten zu, nicht die Attribute eines Trackers (etwa die
Fahrzeug-Einrichtung von DriveLoom). Jedes Auto hat deshalb auch die Sensoren
**Position des Fahrzeugs – geographische Breite** und **– geographische Länge**,
sie sind aber **standardmäßig deaktiviert**: Jede Position landet als Zustand im
Recorder, und Karte und Zonen deckt der Tracker bereits ab. Zum Verwenden unter
*Einstellungen → Geräte & Dienste → BavarianData → dein Auto → deaktivierte
Entitäten* beide aktivieren und warten, bis Home Assistant die Integration neu
lädt. Sie folgen dem Stream, Breite und Länge als getrennte Nachrichten, wie der
Tracker. Brauchst du sie nur für eine andere Integration, nimm sie vom Recorder
aus.

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

## Welche Reichweiten-Entität verwenden

<a id="which-range-entity-to-use"></a>

Ein Elektroauto kann bis zu fünf Reichweiten-Sensoren von BMW haben und einen von
uns — und BMW hat dem am wenigsten nützlichen den naheliegendsten Namen gegeben:

| Entität | Deskriptor | Was sie ist |
| --- | --- | --- |
| **Verbleibende elektrische Reichweite in km** | `electricEngine.kombiRemainingElectricRange` | Die elektrische Reichweite im **Kombiinstrument**. Die richtige. |
| **Zuletzt gesendete Restreichweite** | `lastRemainingRange` | Gesamtreichweite. Beim reinen E-Auto derselbe Wert; beim Plug-in-Hybrid **mit Tankinhalt**. |
| **Prognose der elektrischen Reichweite während des Ladevorgangs** | `electricEngine.remainingElectricRange` | Eine Prognose **während des Ladens**. Sie kommt ein paar Mal am Tag und bleibt dann stehen; bei einem geparkten Auto kann sie weit daneben liegen — 128 km bei einem Auto, das 379 anzeigt. |
| **Verbleibende elektrische Reichweite in Abhängigkeit vom Zielwert des Ladezustands** | `range.target` | Die Reichweite, wenn das Auto sein **Ladeziel** erreicht hat, nicht die jetzige. |
| **Restreichweite** | `navigation.remainingRange` | Der Wert des Navigationssystems. |
| **Reale Reichweite** | — (berechnet) | Unsere: wie weit dein **gemessener** Verbrauch mit der aktuellen Ladung reicht. Siehe [Effizienz & reale Reichweite](DE-Feature-Efficiency-and-Range). |

Welche davon ankommen, hängt vom Auto ab — nicht jedes Modell streamt alle. Die
[Dashboard-Karte](DE-The-Dashboard-Card) zeigt *Verbleibende elektrische
Reichweite in km* und greift nur bei einem Auto, das sie nicht sendet, auf die
anderen zurück.

## Welche Ladezustands-Entität verwenden

<a id="which-state-of-charge-entity-to-use"></a>

Dieselbe Falle beim Akkustand:

| Entität | Deskriptor | Was sie ist |
| --- | --- | --- |
| **Ladestatus der Hochvoltbatterie** | `batteryManagement.header` | Der **gemessene** Ladezustand der Hochvoltbatterie, gestreamt. Trotz des Deskriptornamens die richtige. |
| **Ladezustand (integrationsseitig vorhergesagt)** | — (berechnet) | Unsere. Im Stand gleich dem gemessenen Wert; beim Laden **zählt sie weiter**, wenn BMW mitten in der Ladung verstummt, was stundenlang dauern kann. Die Karte zeigt diese. |
| **Anzeige des Ladezustands** | `stateOfCharge.displayed` | BMWs Anzeigewert, bei Autos, die ihn streamen. |
| **Aktueller vorhergesagter Ladezustand** | `electricEngine.charging.level` | BMWs serverseitige Prognose (das, was die MyBMW App zeigt). **Nur REST**, aktualisiert sich also nie über den Stream. |
| **Ladezustand der Batterie** (Fahrt) | `trip.segment.end…hvSoc` | Ändert sich nur am Ende einer Fahrt. |
| **Niedervoltbatterie** | `electricalSystem.battery.stateOfCharge` | Die kleine 12-V-Batterie, nicht der Antriebsakku. |

Füllt sich *Ladestatus der Hochvoltbatterie* nie, ist der Deskriptor in deiner
Datenauswahl nicht eingeschaltet — siehe
[Ladeverlauf](DE-Feature-Charging-History-and-Cost#no-startend-soc-on-a-session).

## Entitätsnamen und deine Sprache

<a id="entity-names-and-your-language"></a>

Entitätsnamen folgen der Sprache von Home Assistant. Elf werden mitgeliefert:

| HA-Sprache | Was du bekommst |
| --- | --- |
| **Deutsch** | *Gemessener Reifendruck vorne links*, *Reifenzustand* |
| **English** | US-Schreibweisen — *Tire pressure (front left)*, *Tire Condition* |
| **English (UK)** | Britische Schreibweisen — *Tyre pressure (front left)*, *Tyre Condition* |
| **Français** | *Pression mesurée du pneu avant gauche*, *État des pneus* |
| **Italiano** | *Pressione misurata pneumatico anteriore sinistro*, *Stato degli pneumatici* |
| **Español** | *Presión medida del neumático delantero izquierdo*, *Estado de los neumáticos* |
| **Nederlands** | *Gemeten bandspanning linksvoor*, *Bandenconditie* |
| **Polski** | *Zmierzone ciśnienie w oponach przód lewo*, *Stan opon* |
| **Português** | *Pressão do pneu medida no pneu dianteiro esquerdo*, *Estado dos pneus* |
| **Čeština** | *Změřený tlak v pneumatice vpředu vlevo*, *Stav pneumatik* |
| **Svenska** | *Uppmätt däcktryck vänster fram*, *Däckstatus* |

Außer im Englischen sind die Namen von BMWs Feldern **BMWs eigene** — aus dem
Katalog, den BMW in jeder Sprache veröffentlicht. Sie entsprechen also dem
Wortlaut im BMW-Portal und sind keine Übersetzung unserer englischen Titel.
Die einzige Ausnahme sind die seitlichen Türen und Fenster. BMW meldet sie nach
Sitzplatz, benennt sie in mehreren Sprachen aber nach Seite (*Status der Tür vorn
links*), und das ist bei einem Rechtslenker die falsche Tür. Sie heißen deshalb
nach Fahrer- oder Beifahrerseite (*Status der Tür Fahrerseite vorn*), wie die
englischen Namen auch. Übersetzt sind außerdem die Einrichtungsdialoge, Optionen, Dienste,
Reparaturhinweise, der Monatsbericht und die Dashboard-Karte. Portugiesisch ist
europäisches Portugiesisch; eine Installation auf brasilianischem Portugiesisch
zeigt in Home Assistant Englisch, die Karte aber Portugiesisch.

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

## Unbekannt oder nicht verfügbar?

<a id="unknown-or-unavailable"></a>
<a id="why-some-entities-are-unavailable"></a>

Die beiden bedeuten hier Verschiedenes.

**`unbekannt`: Es gibt noch keinen Wert.** Entweder hat BMW dieses Feld seit der
Installation nicht gesendet, oder eine berechnete Entität wartet noch auf genug
Daten:

| Entität | Worauf sie wartet |
| --- | --- |
| Ein BMW-Feld | Die nächste Meldung des Autos. Gestreamte Felder füllen sich von selbst; ein Ver- oder Entriegeln in der MyBMW App stößt meist eine an. **Nur-REST**-Felder ([welche](DE-Getting-Started-4-Choose-Data#some-fields-never-arrive-on-the-stream)) brauchen die tägliche Aktualisierung oder einen `fetch_*`-Dienst. Ein Feld, das dein Auto nicht hat, bleibt dauerhaft `unbekannt`. |
| **Reale Reichweite** | Mindestens ~50 km zwischen zwei aufgezeichneten Ladungen und eine Akkukapazität vom Auto. Das Attribut `status` nennt, was fehlt: `not_enough_history` oder `no_capacity`. |
| **Ladekosten pro 100 km** | Eine [Preisquelle](DE-Feature-Charging-History-and-Cost#setting-up-cost) und zwei Ladungen mit einer Fahrt dazwischen. |
| **Batteriezustand** | Nicht `unbekannt`: Er zeigt `Learning (n/10)`, bis er zehn gute Ladungen hat ([warum](DE-Feature-Battery-Health)). |
| **Vorhergesagte Zeit bis zum Ladeziel** | Eine Ladung zu Hause mit verknüpftem Wallbox-Zähler ([Details](DE-Feature-evcc-and-Wallbox-Bridge#time-to-the-charge-target)). |

**`nicht verfügbar`: Die Integration läuft für diese Entität nicht.** BavarianData
setzt die eigenen Entitäten nie auf nicht verfügbar. Es heißt also: Die
Integration ist nicht gestartet oder muss neu autorisiert werden (siehe
*Einstellungen → Geräte & Dienste* und *Reparaturen*), oder die Entität ist ein
Überbleibsel von etwas, das die Integration nicht mehr anlegt — dann kannst du sie
löschen.

In beiden Fällen behalten Entitäten ihre Attribute `cluster`/`category`, weil die
Datengruppen-Ansichten der Karte davon abhängen.
