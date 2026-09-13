# Batteriezustand

> 🇬🇧 [English version](Feature-Battery-Health)

Die Integration lernt die **nutzbare Kapazität** deines Akkus aus denselben
aufgezeichneten Ladevorgängen, die auch den
[Ladeverlauf](DE-Feature-Charging-History-and-Cost) speisen — ohne
REST-Kontingent, alles aus dem Stream abgeleitet.

## Wie er gelernt wird

Eine **Ladung über einen weiten Bereich** (etwa 20 → 80 %), die eine bekannte
Menge kWh hinzufügt, verrät die Kapazität des ganzen Akkus: Nahmen 30 % der
Batterie *X* kWh auf, hat der Akku rund *X / 0,30*. Der Durchschnitt über viele
solche Ladungen gleicht das Rauschen durch Temperatur, Ladegeschwindigkeit und
Messfehler aus.

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
- **Er hat batterieseitige Energie.** Aus BMWs Ladehistorie importierte
  Ladevorgänge haben nur einen *Netz*-Wert, der die Ladeverluste enthält und den
  Akku zu groß erscheinen ließe. Sie zählen nicht, egal wie weit ihr Bereich ist —
  die ersten Ladungen nach einer Installation, meist importiert, zählen also nicht.
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
> bleibt der Prozentwert leer, egal wie viele Proben zusammenkommen.

## Ansehen

Nutze die [Kartenansicht `view: health`](DE-The-Dashboard-Card#battery-health-view-health):
eine Anzeige (Prozent des Akkus im Neuzustand) mit dem Verlauf der Kapazität über
die Laufleistung darunter.
