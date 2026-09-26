# Fahrten / Fahrtenbuch

> 🇬🇧 [English version](Feature-Trips)

Jede Fahrt wird aus dem Stream rekonstruiert und gespeichert: Strecke, Dauer,
Start- und Zielort, verbrauchter Ladezustand sowie BMWs eigene Werte je Fahrt zu
Verbrauch, Rekuperation und Fahrstil. Ohne REST-Kontingent.

> **Kein Finanzamt-konformes Fahrtenbuch.** Das hier ist ein Fahrtenbuch und eine
> Hilfe für Spesen; es ist rechtlich nicht manipulationssicher.

## Wie Fahrten erkannt werden

Fahrten werden aus dem **Live-GPS** im Stream rekonstruiert (die Integration
verfolgt die Strecke entlang der GPS-Spur), eine Konfiguration ist nicht nötig.
Achte darauf, dass die Datengruppe **Elektrofahrzeug** und die Standort-Deskriptoren
in [Schritt 4](DE-Getting-Started-4-Choose-Data) aktiviert sind.

### Die Zeiten einer Fahrt

Eine Fahrt wird später *bemerkt*, als sie beginnt, und später *geschlossen*, als sie
endet — BMWs Positions-Stream kann minutenlang schweigen, und eine Fahrt schließt
erst, wenn das Auto fünf Minuten stillstand. Start und Ende werden dafür korrigiert,
sie zeigen also Fahrzeiten und keine Erkennungszeiten:

- **Start** — wann das Auto zuletzt geparkt gesehen wurde, sofern dein Auto die
  Fahrertür streamt (die Tür schließt sich, weil der Fahrer eingestiegen ist) und
  sie sich in den letzten fünf Minuten geschlossen hat. Sonst der erste GPS-Punkt,
  der Bewegung zeigte.
- **Ende** — der letzte GPS-Punkt, der Bewegung zeigte, nicht der Moment, in dem der
  Fünf-Minuten-Timer ablief. Beendet stattdessen das Öffnen der Fahrertür die Fahrt,
  ist das die Ankunft.

Beides bleiben Schätzungen, begrenzt dadurch, wie oft dein Auto seine Position
meldet: Eine lange Lücke im Stream ist eine lange Lücke im Wissen.

### Wenn der Positions-Stream verstummt

<a id="when-the-position-stream-goes-quiet"></a>

„Fünf Minuten keine Bewegung“ hat zwei sehr verschiedene Ursachen: Das Auto hat
angehalten, oder der Stream hat aufgehört. Nur das Erste beendet eine Fahrt — das
Zweite zu schließen, teilte früher eine Fahrt durch einen Tunnel oder ein Funkloch
in zwei Fahrten.

Ein Halt wird daher erst umgesetzt, wenn etwas ihn **bestätigt**: eine
Positionsmeldung, die das Auto stehend zeigt, oder ein ausdrückliches „bewegt sich
nicht“ bei Autos, die so etwas senden. Fehlt beides, bleibt die Fahrt offen, bis die
Meldungen zurückkommen und es klären — ist das Auto weitergefahren, war es die ganze
Zeit eine Fahrt; taucht es dort wieder auf, wo es verschwand, stand es wirklich, und
die Fahrt endet rückwirkend bei der letzten Bewegung. Kommt der Stream nie zurück,
schließt die Fahrt nach einer halben Stunde, und weil das Ende in jedem Fall
rückdatiert wird, kostet ein spätes Schließen keine Genauigkeit.

## Die Fahrt, die gerade läuft

<a id="seeing-the-drive-thats-happening-now"></a>

Eine laufende Fahrt steht noch nicht im Fahrtenbuch — sie hat kein Ende, keine
endgültige Strecke und nichts zum Zuordnen —, daher wird sie getrennt und live
angezeigt:

- Ein Binärsensor **Fahrt aktiv** pro Fahrzeug: `an`, solange eine Fahrt läuft, mit
  der bisherigen Fahrt als Attributen — `started`, `start_location`,
  `distance_km`, `duration_s`, `soc_start`, `soc_now`, `energy_kwh`,
  `last_movement` und `held`.
- Auf der Karte: ein **Abzeichen in der Übersicht** (Strecke und Minuten bisher,
  antippen für die Attribute) und eine **aktive Zeile oben in der Fahrten-Ansicht**
  — woher du kamst, Strecke und Zeit bisher und, bei eingeschalteter
  [Routenaufzeichnung](#recording-the-route-opt-in), die bisherige Route auf einer
  Karte.
- In `bavariandata.get_trips`: eine Liste `open_trips` neben den gespeicherten
  Fahrten.

Zwei Dinge, bevor du darauf automatisierst:

- **Es ist kein Sensor „Auto bewegt sich“**, deshalb heißt er auch nicht so. Er
  sagt dir, dass *eine Fahrt offen* ist. Eine Fahrt öffnet bei der ersten
  Positionsmeldung mit Bewegung und schließt fünf Minuten nach der letzten — länger,
  solange der Stream schweigt (siehe [oben](#when-the-position-stream-goes-quiet)),
  und genau so sieht Parken in der Tiefgarage aus. Er bleibt nach der Ankunft also
  einige Minuten `an`, und bis zu einer halben Stunde, wenn das Auto ohne Empfang
  parkt. Das Ende der gespeicherten Fahrt wird trotzdem richtig rückdatiert; nur das
  Live-Signal hängt nach. Für die feinere Frage nutze das Attribut `last_movement`.
- **Die Werte sind vorläufig.** Die Strecke kommt vom Kilometerstand, der nur in
  ganzen Kilometern zählt, sie zeigt in der ersten Minute einer Fahrt also `unknown`
  und hängt danach etwas hinter der GPS-Spur her. Der endgültige Datensatz wird
  beim Schließen aus der besten verfügbaren Quelle berechnet.
- Über einen Neustart kommt nichts: Eine laufende Fahrt existiert nur im Speicher,
  ein Neustart von Home Assistant mitten in der Fahrt beendet sie also und das
  Signal geht auf `aus`.

## Privatsphäre von Haus aus

Endpunkte werden als **Ortsnamen gespeichert, nie als Koordinaten**:

- Ein Punkt innerhalb einer **Zone** von Home Assistant zeigt den Namen der Zone.
- Ein Punkt außerhalb jeder Zone wird **nur dann** als Adresse gespeichert, wenn du
  unter **Konfigurieren → Fahrten** **Adressen auflösen** einschaltest. Dabei werden
  die Koordinaten des Endpunkts an Nominatim von OpenStreetMap gesendet; gespeichert
  wird die Adresse, die Koordinaten selbst nie.

## Die Route aufzeichnen (optional)

<a id="recording-the-route-opt-in"></a>

Standardmäßig behält eine Fahrt nur ihre benannten Endpunkte — genug für das
Fahrtenbuch, aber nicht für eine Karte. Schalte **Route aufzeichnen** unter
**Konfigurieren → Fahrten** ein, und jede neue Fahrt speichert zusätzlich ihre
**GPS-Spur**: die Linie der Koordinaten entlang der Fahrt, jeweils mit Zeitpunkt,
sodass eine Karte zeigen kann, wo das Auto fuhr, **und** die Fahrt abspielen kann.

- Das ist die **einzige** Einstellung, die rohe Koordinaten auf die Festplatte
  schreibt — deine genauen Start- und Zielpunkte eingeschlossen —, deshalb ist sie
  standardmäßig aus und unabhängig von der Adressauflösung.
- Sie wirkt ab der **nächsten** Fahrt, die beginnt; schon gespeicherte Fahrten
  behalten, womit sie aufgezeichnet wurden, und eine laufende Fahrt behält die
  Einstellung, mit der sie begann.
- Die Spur hängt am Fahrt-Datensatz und wird von **`bavariandata.get_trips`**
  zurückgegeben (als Liste `track` von Punkten). Jeder Punkt ist `[lat, lon, t]`,
  wobei `t` die ganzen **Sekunden seit Fahrtbeginn** sind — so kann eine Karte die
  Route in Echtzeit animieren und nach Tempo einfärben. Routen von vor dieser
  Funktion speichern zweistellige Punkte `[lat, lon]` und kommen ohne Zeiten zurück;
  ihre Zeiten lassen sich nicht nachträglich ergänzen. Die Spur ist nie im CSV- oder
  druckbaren [Export](DE-Feature-Export) enthalten, der bei Ortsnamen bleibt.
- Die Spur ist begrenzt und leicht ausgedünnt, sodass auch eine mehrstündige Fahrt
  eine kompakte Route bleibt statt eines endlosen Stroms von Punkten. Ein Halt
  erscheint als einzelner Punkt, dessen Abstand zum nächsten Zeitstempel zeigt, wie
  lange das Auto stand.

Sind Routen aufgezeichnet, zeichnet die Ansicht **Fahrtenkarte** der Dashboard-Karte
(`view: map`) sie auf einer Karte, eingefärbt nach Zuordnung und filterbar nach
Zeitraum ([siehe Karte](DE-The-Dashboard-Card#trip-map-view-map)).

## Zuordnung

Fahrten werden automatisch als **geschäftlich**, **privat** oder **Pendeln**
eingeordnet:

- Lege unter **Konfigurieren → Fahrten** eine **Arbeitszone** fest, damit Fahrten
  Zuhause↔Arbeit als Pendelfahrten erkannt werden.
- Alles andere wird als **Standard-Typ** eingeordnet — ab Werk **Privat**. Wähle
  **Geschäftlich**, wenn das für deine Fahrten der ehrliche Standard ist, oder
  **nicht zuordnen**, um jede Fahrt von Hand einzuordnen.
- Korrigiere jede Vermutung mit **`bavariandata.set_trip_class`** oder dem
  Bearbeiten-Element auf der
  [Fahrten-Karte](DE-The-Dashboard-Card#trips--driving-journal-view-trips).

Die automatische Zuordnung ist immer nur ein **Ausgangspunkt**: Eine Fahrt, die du
selbst eingeordnet hast, wird nie überschrieben, und das Ändern dieser
Einstellungen berührt keine bereits gespeicherten Fahrten.

### Eine Pendelfahrt mit Zwischenstopp

Ein Einkauf zwischen Zuhause und Arbeit lässt das Auto lange genug stehen, dass
**zwei** Fahrten aufgezeichnet werden, von denen keine allein Zuhause→Arbeit ist.
Dafür gibt es die **Toleranz für Zwischenstopps** (Standard **30 Minuten**): Steht
das Auto zwischen dem Ende der einen und dem Beginn der nächsten Fahrt nicht länger
als das, bilden die Fahrten eine *Kette*, und eine Kette von einer Pendelzone zur
anderen zählt als Pendeln — mit allen Abschnitten, rückwirkend. Die Stopps bleiben
im Fahrtenbuch als eigene Fahrten sichtbar; sie tragen nur alle das Abzeichen
Pendeln.

```
Zuhause ──12 min──▶ Supermarkt ──[22 min Stopp]──▶ Arbeit    beide Abschnitte = Pendeln
Zuhause ──8 min───▶ Bäcker ──────[15 min Stopp]──▶ Zuhause   beide Abschnitte = privat
Arbeit ───5 min───▶ Mittagessen ─[40 min Stopp]──▶ Arbeit    beide Abschnitte = privat
```

Gut zu wissen:

- Eine Kette **endet, wenn sie Zuhause oder die Arbeit erreicht**, sodass eine
  Mittagsrunde vom Büro und zurück nicht in die Morgenfahrt gezogen wird.
- Geprüft werden beide **Enden** der Kette, nicht irgendein Endpunkt — eine
  Rundfahrt, die zu Hause beginnt und endet, bleibt privat, egal wie viele Stopps
  sie hatte.
- Bis zu **fünf** Fahrten können eine Kette bilden. Eine längere Folge kurzer
  Strecken ist ein Tag voller Besorgungen und behält daher den Standard-Typ.
- Stopps unter etwa **fünf Minuten** teilen eine Fahrt ohnehin nie, diese
  Einstellung regelt also den Bereich darüber. Mit **0** schaltest du die
  Verkettung ganz ab.

## Wie der Verbrauch gemessen wird

<a id="how-consumption-is-measured"></a>

BMW streamt den Ladezustand in **ganzen Prozent**. Bei einem 78-kWh-Akku ist eine
Stufe also etwa 0,8 kWh wert — über eine 30-km-Pendelfahrt ein Rundungsfehler, über
eine 1-km-Strecke die ganze Messung. Etwas Feineres gibt es nicht: Der einzige
andere Energiewert im Stream (`smeEnergyDeltaFullyCharged`) ist dieselbe Größe auf
ganze kWh gerundet, und BMWs eigenes Verbrauchsfeld je Fahrt
(`energyConsumptionComfort`) sendet nicht jedes Auto — der i5 zum Beispiel nie.
BavarianData zeigt daher zwei Werte und verweigert einen dritten.

**Der Hauptwert: eine Energiebilanz.** Allein aus dem Ladeverlauf gemessen — zwei
Ladevorgänge schließen ein Fenster ein, jeder mit einem Kilometerstand und einem
Ladezustand zum Ende, sodass gefahrene Strecke und gelieferte Energie dazwischen
bekannt sind, ohne dass eine Fahrt erkannt worden sein muss.

```
verbraucht = zwischen den beiden Messungen geladene Energie
             − (Ladezustand am Ende − Ladezustand am Anfang) × Kapazität
Strecke    = Kilometerstand am Ende − Kilometerstand am Anfang
```

Die geladene Energie wird aus der gestreamten Ladeleistung integriert, nicht aus
einem gerundeten Ladezustand abgelesen, dieser Wert ist also nicht an die
Ein-Prozent-Auflösung gebunden. Und weil er nie den Fahrt-Datensatz liest, stimmt
er auch in einem Monat, in dem eine Fahrt verpasst wurde (ausgefallener Stream,
Update, Tiefgarage). Er reicht von der ersten bis zur letzten Ladung, meist etwas
kürzer als der Kalendermonat.

**Welche Seite des Ladegeräts er beschreibt, hängt von deiner Einrichtung ab**, und
die Karte sagt es:

- **Ab Akku** — der Standard. BMW streamt die *batterieseitige* Ladeleistung, die
  Energie in dieser Summe ist also das, was im Akku ankam, nicht was aus der Wand
  floss. Direkt vergleichbar mit der Verbrauchsanzeige des Autos.
- **Ab Steckdose** — nur, wenn jede Ladung im Fenster einen *gemessenen* Netzwert
  hat, aus einer verknüpften Wallbox-Energie-Entität oder aus BMWs
  Ladehistorien-Import. Dieser Wert **enthält die Ladeverluste**, liegt also über der
  Anzeige des Autos und ist das, was der Strom tatsächlich gekostet hat. Nur dann
  zeigt die Karte darunter auch den batterieseitigen Wert, mit der Differenz als
  Ladeverlust — denn nur dann messen die beiden wirklich Verschiedenes.

**Der batterieseitige Fahrtenwert.** Gesamte Fahrtenergie über gesamte
Fahrtstrecke, ebenfalls mit der Anzeige des Autos vergleichbar. Er ist eine nach
Strecke gewichtete Summe, *kein* Mittel der Werte je Fahrt: Ein Mittel von
Verhältnissen lässt eine 2-km-Strecke eine 200-km-Fahrt überstimmen, was das
Ergebnis stark aufbläht. Er misst dieselbe Größe wie die batterieseitige Bilanz, nur
über Fahrterkennung und Ladezustands-Differenzen — deshalb führt die Bilanz, und
dieser Wert erscheint nur daneben, wenn die Bilanz netzseitig ist.

**Der Verbrauch je Fahrt wird unter 3 % Ladezustand zurückgehalten.** Darunter ist
die Rundung die Messung — eine 1-km-Fahrt, die zufällig ein Prozent verliert, ergäbe
~78 kWh/100 km. Solche Fahrten zeigen Strecke, Dauer und Energie wie gewohnt, aber
keinen Verbrauch, und sie können weder „beste“ noch „schlechteste“ Fahrt des Monats
sein. In einem typischen Monat aus kurzen Besorgungen und langen Pendelfahrten
tragen also nur die längeren Fahrten einen Wert; das ist das ehrliche Ergebnis,
keine Lücke.

**Plug-in-Hybride bekommen keinen Verbrauch je Fahrt.** Die Energie einer Fahrt ist
der Rückgang der Batterieladung, doch ein Hybrid kann einen Teil der Strecke mit
Kraftstoff gefahren sein, und der Stream liefert keine rein elektrische Strecke, durch
die man teilen könnte. Eine 40-km-Fahrt mit 4 kWh und einem Liter Benzin ergäbe
10 kWh/100 km — einen Wert, den kein Teil des Autos erreicht hat. Hybrid-Fahrten
behalten daher ihre Energie, der Verbrauchswert bleibt leer, und sie zählen nicht zum
batterieseitigen Durchschnitt. Auch der am Stecker gemessene Monatsverbrauch wird aus
demselben Grund zurückgehalten ([mehr](DE-Feature-Efficiency-and-Range#plug-in-hybride)).

> Auch die **Rekuperation** wird in **kWh/100 km** gezeigt, nicht in kWh: BMWs
> `recuperationTotal` ist als Durchschnitt pro 100 km dokumentiert, ein Monat ist
> also ein nach Strecke gewichtetes Mittel davon, nie eine Summe.

## Der Monatssensor und die Übersichten

- Ein Sensor **Fahrstrecke (dieser Monat)** pro Fahrzeug trägt die Monatssumme und
  die Aufteilung geschäftlich/privat/Pendeln.
- Ein Binärsensor **Fahrt aktiv** pro Fahrzeug für die gerade laufende Fahrt — siehe
  [oben](#seeing-the-drive-thats-happening-now).
- Die Details stecken in den Diensten (und der Karte), nicht in einer Flut von
  Entitäten:
  - **`bavariandata.get_trips`** — gespeicherte Fahrten als Antwortdaten, dazu
    `open_trips` für eine laufende Fahrt.
  - **`bavariandata.get_driving_summary`** — die Monatsübersicht: Strecke (gegen den
    Vormonat), die Aufteilung, Verbrauch, Rekuperation, eine Fahrstil-Bewertung, die
    häufigsten Ziele und (mit Tarif) geschätzte Fahrtkosten. Der Verbrauch kommt als
    zwei Schlüssel — `energy_balance` (steckdosenseitig, mit dem Fenster und den
    Eingangswerten) und `avg_consumption_kwh_per_100km` (batterieseitig); siehe
    [wie der Verbrauch gemessen wird](#how-consumption-is-measured). Beide können
    fehlen, wenn ihre Eingangswerte keinen Wert tragen.

## Ansehen & exportieren

- Die [Kartenansicht `view: trips`](DE-The-Dashboard-Card#trips--driving-journal-view-trips).
- [Export](DE-Feature-Export) als CSV oder druckbarer Bericht.
