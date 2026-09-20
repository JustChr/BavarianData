# Die Dashboard-Karte

> 🇬🇧 [English version](The-Dashboard-Card)

Eine eigene **BavarianData Card** liegt der Integration bei und wird automatisch als
Dashboard-*Ressource* registriert — es gibt keine Ressource von Hand hinzuzufügen,
und sie wird bei jedem Update erneuert, damit Browser die neue Version laden.

**Die Integration legt kein eigenes Dashboard an.** Die Registrierung macht die
Karte *verfügbar*; wohin sie kommt, bestimmst du:

1. Öffne ein beliebiges Dashboard und klicke auf **✏️ Bearbeiten → ➕ Karte
   hinzufügen**.
2. Suche nach **BavarianData Card** und wähle sie aus — ein visueller Editor öffnet
   sich.
3. Speichern. Die minimale Konfiguration findet das Auto selbst, es gibt also nichts
   auszufüllen:

   ```yaml
   type: custom:bavariandata-card
   ```

> Die automatische Registrierung braucht Ressourcen im Speichermodus, das ist der
> Standard. Werden deine Dashboard-Ressourcen per YAML verwaltet, musst du die
> Ressource selbst hinzufügen — siehe
> [Fehlerbehebung](DE-Troubleshooting-and-FAQ#config-error-after-reload).

Die Karte hat mehrere **Ansichten**. Standard ist die Übersicht; mit `view:` oder
`cluster:` wechselst du. Nutze **eine Karte pro Ansicht** — mehrere Karten auf einem
Dashboard zeigen sie nebeneinander.

> Erscheint die Karte nach einem Update nicht, **lade den Browser hart neu**.
> Funktioniert sie im Browser, aber die **Companion App** meldet *Custom element
> doesn't exist*, leere den Frontend-Cache der App — siehe
> [Fehlerbehebung](DE-Troubleshooting-and-FAQ#custom-element-doesnt-exist-app).

- [Übersicht](#overview)
- [Ladeverlauf](#charging-history-view-charging)
- [Batteriezustand](#battery-health-view-health)
- [Effizienz & Reichweite](#efficiency--range-view-efficiency)
- [Fahrten / Fahrtenbuch](#trips--driving-journal-view-trips)
- [Fahrtenkarte](#trip-map-view-map)
- [Reifen](#tires-cluster-tire)
- [Sicherheit & Öffnungen](#security--closures-cluster-closures)
- [Liste einer Datengruppe](#single-cluster-list)
- [Vollständige YAML-Referenz](#full-yaml-reference)

---

## Übersicht

<a id="overview"></a>

Das Fahrzeugbild, ein Ring, die Reichweite und ein Raster wichtiger Werte. Was sie
zeigen, folgt dem **Antrieb** des Autos, den die Karte aus den gestreamten Daten
ableitet:

| Antrieb | Ring | Daneben | Raster |
| --- | --- | --- | --- |
| Elektrisch | Ladezustand (blau beim Laden) | Reichweite, Ladestatus | Ziel, Stecker, Bis voll, Kilometerstand |
| Plug-in-Hybrid | Ladezustand | elektrische Reichweite, Ladestatus | Tank, Gesamtreichweite, Ziel, Stecker, Bis voll, Kilometerstand |
| Benzin / Diesel | Tankfüllstand — oder der Tankinhalt, bei einem Auto, das keinen Prozentwert streamt | Reichweite, dazu Tankinhalt oder Kilometerstand | Kilometerstand |
| Keines, erwiesen | Reichweite | Kilometerstand | — |

Ein Auto, das Daten der Hochvoltbatterie *und* Kraftstoffdaten sendet, ist ein
Plug-in-Hybrid; Kraftstoffdaten allein machen es zum Benziner oder Diesel. Manche
Benziner streamen trotzdem ein E-Ladeziel, das lässt ein Auto aber nie elektrisch
erscheinen.

Ein frisch eingerichtetes Auto hat noch nichts bewiesen und behält so lange das
elektrische Layout. Ein Auto, das schon eine Weile sendet und **keine** der
beiden Datenarten geliefert hat, hat dagegen etwas bewiesen: Es ist nicht
elektrisch, was immer es sonst ist. Das kommt vor — ein MINI mit Benzinmotor
streamt unter Umständen weder Kraftstoffsystem noch Motor — und ihm einen
Ladering zu zeigen, den er nie füllen kann, war schlicht falsch. Er bekommt
deshalb die letzte Zeile oben: Reichweite im Ring, Kilometerstand daneben, nichts
zum Laden. Wenn dein Auto dort landet und du es besser weißt, setze `drivetrain:`
in der YAML-Konfiguration der Karte.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-car.png" alt="Übersichtskarte eines BMW i5 mit Ladestand, Reichweite, Ladestatus und Kilometerstand" width="360" />
</p>

```yaml
type: custom:bavariandata-card
```

Wählt sie das falsche Layout, setze im visuellen Editor **Antrieb**, oder:

```yaml
type: custom:bavariandata-card
drivetrain: ice   # bev · phev · ice — weglassen für automatische Erkennung
```

Die Ansichten Ladeverlauf, Batteriezustand und Effizienz beruhen auf Ladedaten. Bei
einem Benziner oder Diesel sagen sie das — *Für dieses Fahrzeug gibt es hier nichts*
—, statt leer zu bleiben, als wäre etwas kaputt. Wird das Auto doch geladen, setze
**Antrieb**.

Während einer Fahrt erscheint unten am Fahrzeugbild ein Abzeichen **Fahrt aktiv**
mit Strecke und Minuten bisher; antippen für die vollständigen Attribute. Es folgt
der Entität *Fahrt aktiv* und bleibt daher nach der Ankunft noch ein paar Minuten
stehen — [warum](DE-Feature-Trips#seeing-the-drive-thats-happening-now).

Ein bestimmtes Fahrzeug legst du mit `device:` (Geräte-ID) oder `vin:` fest.

---

## Ladeverlauf (`view: charging`)

<a id="charging-history-view-charging"></a>

Listet aufgezeichnete Ladevorgänge, neueste zuerst, jeweils mit Datum, Energie,
Kosten und einem Abzeichen Zuhause/Unterwegs. Tippe einen Ladevorgang an, um seine
**Ladekurve**, Spitzen- und Durchschnittsleistung, Dauer und Netzenergie zu sehen.
Oben stehen die Summen „Dieser Monat“. Die Schaltflächen **CSV** und **Bericht**
exportieren den angezeigten Monat ([siehe Export](DE-Feature-Export)).

**Ein Monat nach dem anderen.** Wie in der Fahrten-Ansicht begrenzt ein Element
`‹ August 2026 ›` unter der Überschrift die Liste auf einen Kalendermonat;
blättere zurück für ältere Ladevorgänge. Das Band mit den Monatssummen erscheint nur
im aktuellen Monat — es wird von den Monatssensoren gespeist, die keinen älteren
Wert haben.

```yaml
type: custom:bavariandata-card
view: charging
```

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-charging.png" alt="Karte Ladeverlauf: aufgezeichnete Ladevorgänge mit Datum, Ladezustandsänderung, Energie und Abzeichen Zuhause, oben die Monatssumme" width="360" />
</p>

Sie liest den gespeicherten Verlauf über den Dienst `get_charging_sessions` und
verbraucht daher **kein API-Kontingent**. Kosten erscheinen erst, wenn unter
**Konfigurieren → Ladekosten & Verlauf** eine Preisquelle gesetzt ist
([siehe Ladeverlauf & Kosten](DE-Feature-Charging-History-and-Cost)); bis dahin
stehen die Ladevorgänge mit ihrer Energie da. Ein ohne GPS geladener Ladevorgang
trägt *Zuhause · angenommen*, einer, der bepreist wurde, während der Tarif kurz
unbekannt war, *Teilpreis*.

Ist **Konfigurieren → Solar & Energiequellen** eingerichtet, trägt ein zuordenbarer
Ladevorgang zusätzlich ein Etikett **☀ 62 % Solar**, und aufgeklappt kommt eine Zeile
*Energiequelle* mit den kWh je Quelle hinzu
([siehe Woher die Energie kam](DE-Feature-Charging-History-and-Cost#where-the-energy-came-from)).
Ladevorgänge von vor der Einrichtung dieser Sensoren haben einfach kein Etikett — die
Karte zeigt nie 0 %, wenn es eigentlich „nicht gemessen“ heißt.

---

## Batteriezustand (`view: health`)

<a id="battery-health-view-health"></a>

Zeigt die gelernte nutzbare Kapazität als Anzeige (Prozent des Akkus im
Neuzustand) mit dem Verlauf der Kapazität über die Laufleistung darunter.

```yaml
type: custom:bavariandata-card
view: health
```

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-health.png" alt="Karte Batteriezustand im Zustand Lernt (0/10), während sie Ladungen über einen weiten Bereich sammelt" width="420" />
</p>

Sie liest den Sensor **Batteriezustand** und verbraucht daher **kein
API-Kontingent**. Bis genug Ladungen über einen weiten Bereich vorliegen, um sich des
Werts sicher zu sein, zeigt sie *Lernt (n/10)* statt einer springenden Zahl
([wie er gelernt wird](DE-Feature-Battery-Health)).

---

## Effizienz & Reichweite (`view: efficiency`)

<a id="efficiency--range-view-efficiency"></a>

Wie weit das Auto mit der aktuellen Ladung wirklich kommt, über dem Verbrauch, auf
dem dieser Wert beruht.

```yaml
type: custom:bavariandata-card
view: efficiency
```

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-efficiency.png" alt="Karte Effizienz und Reichweite: reale Reichweite beim aktuellen Ladezustand, Abstand zur Prognose des Autos, gemessener Verbrauch mit Seite des Ladegeräts und Fenster, nutzbare Kapazität und Herkunft sowie ein Balkendiagramm des Verbrauchs nach Monat" width="360" />
</p>

Sie zeigt die reale Reichweite von hier (und mit vollem Akku), den Vergleich mit der
Restreichweiten-Prognose des Autos, den gemessenen Verbrauch mit Seite des Ladegeräts
und Fenster, den gemessenen Ladeverlust, wo ein Netzwert existiert, die nutzbare
Kapazität, durch die geteilt wurde, deine Kosten pro 100 km mit dem Solaranteil des
Monats und ein Balkendiagramm des Verbrauchs nach Kalendermonat — die Jahreszeiten,
denn der Winterverbrauch liegt regelmäßig ein Drittel über dem Sommer.

Alles wird aus dem Ladeverlauf gemessen — zwei Ladungen schließen eine Strecke und
die hineingeflossene Energie ein —, daher verbraucht sie **kein API-Kontingent** und
übernimmt nie die Prognose des Autos
([wie gemessen wird](DE-Feature-Efficiency-and-Range)).

Bis der Ladeverlauf rund 50 km Fahrt einschließt, sagt die Karte das, statt eine Zahl
zu zeigen.

---

## Fahrten / Fahrtenbuch (`view: trips`)

<a id="trips--driving-journal-view-trips"></a>

Listet deine aufgezeichneten Fahrten, neueste zuerst, jeweils mit *von → nach*,
Strecke, Dauer und einem Abzeichen geschäftlich/privat/Pendeln; tippe eine an für
Verbrauch, Rekuperation und verbrauchten Ladezustand, zum Umordnen und — bei Fahrten,
die mit **Route aufzeichnen** gespeichert wurden — für eine kleine Karte der Route
(als saubere Linie mit Start- und Zielmarkierung; zum Zeichnen verlässt nichts deinen
Browser). Über der Liste fasst eine **Monatsübersicht** die Strecke (mit Differenz zum
Vormonat), die Aufteilung geschäftlich/privat/Pendeln, den Durchschnittsverbrauch,
die rekuperierte Energie, eine Fahrstil-Bewertung und die häufigsten Ziele zusammen —
und, sobald ein Tarif gesetzt ist, geschätzte Fahrtkosten.

**Ein Monat nach dem anderen.** Ein Element `‹ August 2026 ›` unter der Überschrift
begrenzt Liste, Monatsübersicht und die Schaltflächen **CSV** / **Bericht** auf einen
Kalendermonat. Blättere zurück für ältere Datensätze — der Verlauf wird standardmäßig
zwei Jahre aufbewahrt
([Aufbewahrung](DE-Settings-Reference#charging-costs--history)), weit mehr, als ein
Bildschirm auf einmal zeigen sollte. Vorwärts endet beim aktuellen Monat.

> Der **Durchschnittsverbrauch** wird aus dem Ladeverlauf und dem Kilometerstand
> gemessen, nicht aus den Fahrten, und ist mit **ab Akku** oder **ab Steckdose**
> beschriftet, je nachdem, wo die Energie tatsächlich gemessen wurde — ab Steckdose
> nur, wenn jede Ladung einen gemessenen Netzwert hat; dann erscheint darunter der
> batterieseitige Wert mit dem Ladeverlust dazwischen. Siehe
> [wie der Verbrauch gemessen wird](DE-Feature-Trips#how-consumption-is-measured).

Eine laufende Fahrt **steht an der Spitze der Liste**, mit einem Live-Abzeichen und
einem farbigen Rand: woher du losgefahren bist, Strecke und Zeit bisher und — mit
**Route aufzeichnen** — die wachsende Route. Sie hat keine Schaltflächen zum
Zuordnen, weil es bis zu ihrem Ende keine gespeicherte Fahrt gibt
([Details](DE-Feature-Trips#seeing-the-drive-thats-happening-now)).

```yaml
type: custom:bavariandata-card
view: trips
```

Sie liest die Dienste `get_trips` und `get_driving_summary` und verbraucht daher
**kein API-Kontingent**. Fahrten werden aus dem Stream rekonstruiert — keine
Konfiguration nötig. Endpunkte werden als **Ortsnamen** gespeichert, nie als
Koordinaten. Lege unter **Konfigurieren → Fahrten** eine **Arbeitszone** fest, damit
Fahrten Zuhause↔Arbeit als Pendelfahrten erkannt werden
([siehe Fahrten](DE-Feature-Trips)).

> Das ist ein Fahrtenbuch und eine Hilfe für Spesen — **kein Finanzamt-konformes
> Fahrtenbuch**: Es ist rechtlich nicht manipulationssicher.

---

## Fahrtenkarte (`view: map`)

<a id="trip-map-view-map"></a>

Eine **Zielkarte**: Sie zeigt, wo deine Fahrten **enden**, und diese Markierungen
**fassen sich beim Herauszoomen zu gezählten Blasen zusammen und teilen sich beim
Hineinzoomen auf** — ein schneller Blick darauf, wohin du am häufigsten fährst. Gezeigt
wird nur das Ende jeder Fahrt (der Start einer Fahrt ist das Ende der vorigen, beides zu
zeigen zählte doppelt), was eine ehrliche Zählung „so oft hier angekommen“ ergibt.
Klicke auf eine Blase, um hineinzuzoomen. Eine Leiste wechselt den Zeitraum — **Dieser
Monat** (Standard), **3 Monate** oder **Alle**.

Die *Route* einer bestimmten Fahrt siehst du in der Ansicht
[Fahrten](#trips--driving-journal-view-trips), wenn du diese Fahrt aufklappst — dort
wird sie auf einer kleinen Karte gezeichnet.

```yaml
type: custom:bavariandata-card
view: map
```

Die Karte hat erst etwas zu zeigen, wenn du unter **Konfigurieren → Fahrten** **Route
aufzeichnen** einschaltest — diese optionale Einstellung speichert die GPS-Koordinaten
jeder Fahrt (die einzige Stelle, an der die Integration rohe Koordinaten auf der
Festplatte behält, und **standardmäßig aus**). Ist sie aus oder gab es noch keine Fahrt
damit, erklärt die Ansicht, dass noch keine Orte aufgezeichnet sind. Fahrten von
*vor* dem Einschalten haben keine Koordinaten, die sich eintragen ließen.

Sie liest die Endpunkte über den Dienst `get_trips`, verbraucht also **kein
API-Kontingent**, und nutzt Home Assistants eigene Kartenkomponente samt
Markierungs-Gruppierung (die Kartenkacheln kommen von OpenStreetMap, wie bei der
eingebauten Kartenkarte).

> **Privatsphäre:** Anders als der Rest des Verlaufs — der Orts*namen* speichert, nie
> Koordinaten — nutzt diese Ansicht die aufgezeichneten Koordinaten deiner
> Fahrt-Endpunkte, dein Zuhause eingeschlossen. Schalte die Routenaufzeichnung nur ein,
> wenn dir das recht ist, und denk daran, dass die Karte jeder sieht, der das Dashboard
> sehen kann.

---

## Reifen (`cluster: tire`)

<a id="tires-cluster-tire"></a>

Zeichnet ein Auto von oben mit jedem Reifen nach Zustand eingefärbt, oben eine
Zusammenfassung des ganzen Satzes und daneben die Werte und die Bereifung jedes Rads.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-tires.png" alt="Reifen-Karte mit Zusammenfassung von Druck und Verschleiß über einem Auto von oben mit Größe, Profil und Restlaufleistung pro Rad" width="300" />
</p>

```yaml
type: custom:bavariandata-card
cluster: tire
```

**Die Zusammenfassung** oben hält die zwei Dinge, die an einem Reifen falsch sein
können, nebeneinander: **Druck** (die gemessene Spanne im Satz, mit dem gemeinsamen
Sollwert darunter) und **Verschleiß** (BMWs Urteil, mit der Laufleistung, bis das
erste Rad fällig ist). Das Abzeichen in der Überschrift ist das Gesamturteil für das
ganze Auto.

**Der Druck** kommt aus dem Stream und wird am eigenen Sollwert jedes Rads gemessen.
Das Band ist bewusst schief — niedrig ab 8 % unter Soll, hoch erst über 15 % über Soll
—, weil der Sollwert ein *Kalt*druck ist und ein gerade gefahrener Reifen 8–10 % höher
liegt, ohne dass etwas falsch ist.

**Der Verschleiß** kommt aus BMWs Reifendiagnose (Smart Maintenance), erneuert durch
die [tägliche Aktualisierung](DE-Feature-API-Quota#the-daily-refresh). Ist sie
verfügbar, zeigt jedes Rad außerdem Größe, Profil, Saison, Montagedatum und die
Laufleistung bis zum Wechsel. Das ist absichtlich pro Rad: Mischbereifung
(unterschiedliche Größen vorne und hinten) ist normal, und eine einzige Zeile unter dem
Diagramm müsste sich für eine entscheiden.

Verschleiß geht in Farbe und Überschrift vor Druck: Ein Reifen, den BMW als abgefahren
meldet, zeigt *„Reifen prüfen“* auch bei perfektem Druck, denn Druck ist leicht zu
beheben, Profil nicht. Hat dein Auto keinen Reifen-Servicedatensatz, liefert BMW hier
nichts, die Verschleiß-Teile fehlen einfach und die Karte zeigt nur den Druck.

In einer schmalen Dashboard-Spalte fällt das Autodiagramm weg und die vier Räder
ordnen sich als 2×2-Raster, vordere Reihe über der hinteren.

---

## Sicherheit & Öffnungen (`cluster: closures`)

<a id="security--closures-cluster-closures"></a>

Zeigt Türen, Fenster, Motorhaube, Kofferraum, Schiebedach, Zentralverriegelung und
Diebstahlwarnanlage auf demselben Autodiagramm. Offene Türen leuchten rot, offene
Fenster und Schiebedach gelb, und ein Schloss in der Mitte zeigt den
Verriegelungszustand — gelesen aus dem gestreamten **Zustand der Türen**, sodass es
dem echten Schloss folgt statt dem veralteten REST-Wert *Status der Türen*
([warum](DE-Feature-Entities-and-Devices#which-lock-entity-to-use)); ein Abzeichen fasst
den ungünstigsten Zustand zusammen, und jedes Teil führt beim Antippen zur zugehörigen
Entität. Teile, die das Fahrzeug nicht meldet, werden einfach weggelassen.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-security.png" alt="Karte Sicherheit & Öffnungen mit Autodiagramm von oben, scharf geschalteter Diebstahlwarnanlage und allen Öffnungen geschlossen" width="300" />
</p>

```yaml
type: custom:bavariandata-card
cluster: closures
```

---

## Liste einer Datengruppe

<a id="single-cluster-list"></a>

Setze `cluster:`, um jeden Wert einer Datengruppe des Katalogs aufzulisten. Nutze eine
Karte pro Datengruppe:

```yaml
type: custom:bavariandata-card
cluster: electric   # electric · status · tire · usage · events · basic · contract · metadata · other
```

Die Karte gruppiert Entitäten über ihre **Attribute** `cluster`/`category`, nicht über
ihre Namen — sie funktioniert also unabhängig von der Sprache in Home Assistant.

`cluster: events` zeigt zuerst die **Check-Control-Meldungen** des Fahrzeugs — die
Warnungen, die das Auto selbst im Display anzeigt, etwa zu wenig Waschwasser — und
sagt es, wenn keine vorliegen. Tippe auf eine Meldung, um sie aufzuklappen: der
Kilometerstand, bei dem das Fahrzeug sie zuletzt angezeigt hat, wie lange es her
ist, dass das Fahrzeug sie gesendet hat, und BMWs Meldungscode. Der Text erscheint so, wie BMW ihn sendet, also auf
Englisch, egal welche Sprache eingestellt ist. Die Meldungen stammen vom Sensor
*Check Control Meldungen*, den BMW unter den nutzungsbasierten Daten führt; BMWs
eigene Datengruppe *Fahrzeugereignisse* enthält nur zwei Teleservice-Zeitstempel,
die unter den Meldungen stehen, wenn das Fahrzeug sie sendet.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-events.png" alt="Karte Fahrzeugereignisse mit einer Check-Control-Meldung zu wenig Waschwasser, aufgeklappt mit dem Kilometerstand der letzten Anzeige, wie lange es her ist, dass das Fahrzeug sie gesendet hat, und dem Code" width="420" />
</p>

---

## Vollständige YAML-Referenz

<a id="full-yaml-reference"></a>

| Schlüssel | Zweck |
| --- | --- |
| `type` | Immer `custom:bavariandata-card`. |
| `view` | `charging`, `trips`, `map`, `health` oder `efficiency`. Weglassen für die Übersicht. |
| `cluster` | `electric`, `status`, `tire`, `usage`, `events`, `basic`, `contract`, `metadata`, `other`, `closures`. Zeigt die Liste einer Datengruppe (oder die besonderen Diagramme für Reifen und Öffnungen). |
| `device` | Geräte-ID, um ein bestimmtes Fahrzeug festzulegen. Der visuelle Editor listet nur deine Fahrzeuge, nie das *CarData Debug Device*; eine Karte, die darauf eingestellt ist, zeigt stattdessen das erste Fahrzeug. |
| `vin` | FIN, als Alternative zu `device`. |
| `title` | Überschreibt den Titel der Karte. |
| `image` | Überschreibt die Bild-Entität des Fahrzeugs. |
| `soc` | Überschreibt die Ladezustands-Entität. |
| `range` | Überschreibt die Reichweiten-Entität. |
| `charging` | Überschreibt die Ladestatus-Entität. |
| `target_soc` | Überschreibt die Ladeziel-Entität. |
| `time_to_full` | Überschreibt die Entität „Bis voll“. |
| `odometer` | Überschreibt die Kilometerstand-Entität. |
| `plug` | Überschreibt die Steckerstatus-Entität. |
| `drivetrain` | `bev`, `phev` oder `ice` — das Layout der Übersicht. Weglassen, um es aus den gestreamten Daten zu erkennen. |
| `fuel` | Überschreibt die Tank-Entität (Prozent oder Volumen). |

Mit installierter Integration braucht man Entitäts-Überschreibungen selten — die Karte
findet sie über das Gerät des Fahrzeugs. Nutze sie nur, wenn du Entitäten umbenannt
hast oder die Karte auf einen Helfer zeigen soll.

---

> **Sieht das an deinem Auto richtig aus?** Die Karte wird hier nur an einem i5
> gesehen. Ein Screenshot an einem anderen Modell ist das Nützlichste, was du schicken
> kannst — besonders, wenn eine Ansicht leer bleibt, ein Wert falsch aussieht oder eine
> erwartete Datengruppe fehlt.
> [Poste ihn in den Discussions →](https://github.com/JustChr/BavarianData/discussions)
