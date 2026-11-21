# Batteriezustand

> 🇬🇧 [English version](Feature-Battery-Health)

Die Integration lernt die **nutzbare Kapazität** deines Akkus aus denselben
aufgezeichneten Ladevorgängen, die auch den
[Ladeverlauf](DE-Feature-Charging-History-and-Cost) speisen — ohne
REST-Kontingent, alles aus dem Stream abgeleitet.

## Wie er gelernt wird

Eine **Ladung über einen weiten Bereich** (etwa 20 → 80 %), die eine bekannte
Menge kWh hinzufügt, verrät die Kapazität des ganzen Akkus: Nahmen 30 % der
Batterie *X* kWh auf, hat der Akku rund *X / 0,30*. Der Wert rechnet alle
solchen Ladungen zusammen, die Energie aller geteilt durch den Ladezuwachs
aller, sodass eine weite Ladung mehr zählt als eine schmale. Eine Ladung, deren
eigener Wert mehr als 15 % von den übrigen abweicht, bleibt außen vor: ein
Abstecken mitten in der Ladung oder ein falscher Ladestand.

### Was im Wert steckt

<a id="what-the-figure-includes"></a>

Die kWh sind die Summe der Ladeleistung, die das Auto streamt, aufaddiert von
der Integration. BMW nennt dieses Feld die Ladeleistung der *Batterie*, an dem
einen Auto, das gegen einen Wallbox-Zähler geprüft wurde (ein i5 mit 11 kW AC),
folgte es aber dem Zähler, nicht dem Akku: 57,0 kWh laut Auto gegen 56,9 kWh am
Zähler bei einer sauberen Ladung von 8 auf 80 %. Gemessen wird also am Stecker,
und ein Teil davon kommt nie im Akku an: Während es lädt, braucht das Auto
selbst etwa **0,3 kW** (Kühlung, Elektronik). Das ist eine feste Leistung, kein
Anteil der Ladung, also steckt in einer langsamen Ladung viel mehr davon. Am
i5 zählte eine 13-stündige Solarladung mit 2,6 kW rund 4 kWh, die der Akku nie
gesehen hat, und ergab allein einen Akku von 88 kWh. Vor dem Teilen zieht die
Integration deshalb 0,3 kW für jede Stunde ab, in der Strom floss, gelesen aus
der Leistungskurve der Ladung (eingesteckt, aber wartend, zählt nicht). An
diesem Auto liegt die gelernte Kapazität dann bei 76 kWh, genau BMWs eigenes
`maxEnergy`; ohne die Korrektur waren es 80. Was das Ladegerät beim Umwandeln
verliert, bleibt drin; lies den Wert also als Verlauf über das Autoleben, nicht
als genaue Akkugröße.

## Der Sensor Batteriezustand

Pro E-Auto erscheint ein Sensor **Batteriezustand**:

- **Zustand** — nutzbare Kapazität in kWh.
- **Attribute** — Prozent vom Neuwert, Anzahl der Proben und ein Verlauf der
  Kapazität über die Laufleistung.

## Was als Probe zählt

<a id="what-counts-as-a-sample"></a>

Nicht jede Ladung sagt etwas über die Kapazität. Ein Ladevorgang zählt nur, wenn
**alles** davon zutrifft:

- **Er umfasst mindestens 25 % Ladezustand.** BMW streamt den Ladezustand in
  ganzen Prozent, daher teilt eine schmale Ladung einen kleinen Energiewert durch
  eine kleine, grob gerundete Differenz. Gemessen an einem echten Auto gegen BMWs
  eigenen `maxEnergy` von 78 kWh: Eine Ladung über 21 % ergab 77 kWh (0,8 %
  daneben), eine über 12 % 82 kWh (5 %) und eine über 9 % **123 kWh** (58 %). Über
  ~20 % ist der Fehler mäßig, unter ~15 % explodiert er.
- **Er hat den Energiewert des Autos.** Aus BMWs Ladehistorie importierte
  Ladevorgänge haben nur BMWs *Netz*-Wert, anders gemessen und nicht
  vergleichbar. Sie zählen nicht, egal wie weit ihr Bereich ist — die ersten
  Ladungen nach einer Installation, meist importiert, zählen also nicht.
- **Er hat die ganze Ladung erfasst.** Lud das Auto schon, als die Integration es
  bemerkte — ein Neustart während des Ladens oder ein Statuswechsel, den der
  Stream nie gesendet hat —, beginnen Energie und Ladezustands-Bereich um
  *unterschiedlich* viel zu spät, und das eine durch das andere zu teilen ist
  sinnlos. Ein solcher Ladevorgang wird als `late_start` markiert und übersprungen.
  Seine Energie und Kosten zählen überall sonst weiter, als Untergrenze.
- **Er lief ohne Unterbrechung durch.** Eine Ladung, während der Home Assistant
  neu gestartet wurde, behält ihren vollen Ladezustands-Bereich, hat aber eine
  Lücke in der Energie, gefüllt aus einer Schätzung, die bereits eine Akkugröße
  annimmt — damit die Kapazität zu messen, hieße die Annahme zu messen. Ein
  solcher Ladevorgang wird als `interrupted` markiert und ebenfalls übersprungen.

## Lernmodus

Der Sensor zeigt **`Lernt (n/10)`**, bis er genug gute Proben hat **und** die
Schätzung zu BMWs eigener Kapazitätsangabe passt. Ein verdächtiger Wert wird
**zurückgehalten statt angezeigt** — ein springender Wert wäre schlimmer als ein
ehrliches „noch nicht sicher“.

In einer Automation oder einem Template ist der Zustand selbst `learning_0` …
`learning_10`; *Lernt (n/10)* ist das, was Home Assistant daraus in deiner
Sprache anzeigt.

Schneller zu einem sicheren Wert kommst du mit ein paar **Ladungen über einen
weiten Bereich** (ein großer Sprung im Ladezustand in einem Ladevorgang) statt
vieler kleiner Nachladungen.

> **Bleibt er bei `Lernt (0/10)`**, liegt es wahrscheinlich an deinem
> Ladeverhalten: Wer einen halbvollen Akku oft und in kleinen Portionen
> nachlädt, erzeugt nie eine Ladung, die zählt. Den Akku tiefer zu fahren und in
> einem Zug wieder aufzuladen — auch nur gelegentlich — bewegt den Zähler.
> Solange liefert BMWs eigener Sensor **State of Health (SOCE)** direkt einen
> Wert, sofern dein Auto ihn streamt. Beachte auch: Der Prozentwert vom Neuwert
> braucht BMWs `batterySizeMax`, den manche Autos als `0` (ungültig) melden; dann
> bleibt der Prozentwert leer, egal wie viele Proben zusammenkommen, und der
> Sensor *Größe der Hochvoltbatterie* zeigt *unbekannt* statt 0 kWh.

## Ansehen

Nutze die [Kartenansicht `view: health`](DE-The-Dashboard-Card#battery-health-view-health):
eine Anzeige (Prozent des Akkus im Neuzustand) mit dem Verlauf der Kapazität über
die Laufleistung darunter.
