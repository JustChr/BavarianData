# Ladeverlauf & Kosten

> 🇬🇧 [English version](Feature-Charging-History-and-Cost)

Weil BavarianData Daten in Echtzeit streamt, zeichnet es jeden abgeschlossenen
Ladevorgang auf und kann ihm einen Preis geben — alles aus dem Stream, ohne
**REST-Kontingent** zu verbrauchen.

## Sensoren für geladene Energie

BMW sendet nie einen kWh-Zähler, daher integriert die Integration die
Live-Ladeleistung über die Zeit zu zwei Sensoren pro Fahrzeug:

- **Geladene Energie (gesamt)** — ein nur steigender kWh-Zähler
  (`device_class: energy` / `state_class: total_increasing`). Füge ihn im
  **Energie-Dashboard** als „einzelnes Gerät“ hinzu, um das Laden des Autos neben
  dem restlichen Haushalt zu sehen und zu bepreisen.
- **Geladene Energie (Ladevorgang)** — setzt sich zu Beginn jedes Ladevorgangs
  zurück.

Beide funktionieren mit jedem Fahrzeug, das die Ladeleistung streamt.

### Wie genau ist das?

<a id="how-accurate-is-it"></a>

Der Wert ist **batterieseitig** — es ist das, was im Akku ankam, nicht was aus der
Wand floss, er liegt also um die Ladeverluste unter deinem Zähler. (Einen echten
Netzwert gibt es nur als `grid_kwh`, aus BMWs Ladehistorien-Import oder aus einer
Wallbox-Entität, die du verknüpfst.)

BMW tastet die Ladeleistung **nicht** gleichmäßig ab. Sie kommt in Schüben, manchmal
mit mehr als einer Stunde dazwischen, und die Integration muss annehmen, dass die
zuletzt gemeldete Leistung bis zur nächsten galt. Bei dichten Werten ist das auf
ein Prozent genau; verstummt der Stream mitten im Laden, kann ein
unrepräsentativer Wert eine lange Strecke bestimmen.

Deshalb ist die laufende Summe **auf das begrenzt, was der Akku aufgenommen haben
kann** — der Anstieg des Ladezustands mal die Akkukapazität, plus ein kleiner
Spielraum dafür, dass der Ladezustand in ganzen Prozent kommt. Das ist eine
Obergrenze, nie eine Korrektur: Ein Ladevorgang, der zu niedrig gemessen hat,
bleibt unangetastet, weil nichts einen zu niedrigen Messwert von einer wirklich
langsamen Ladung unterscheiden kann. Die Grenze gilt für die Summe, nicht für jeden
einzelnen Schritt, daher holt eine zurückgehaltene Ladung, die auf einen
Ladezustandswert wartet, alles wieder auf, sobald er eintrifft. Sind Ladezustand
oder Kapazität unbekannt, gilt keine Grenze.

Die Grenze braucht einen Ladezustandswert, **der während des Ladevorgangs
gemessen** wurde. Nicht jedes Auto streamt einen: Stammt der einzige verfügbare
Ladezustand aus dem REST-Snapshot, ändert er sich zwischen den Ladungen nie, und
ein Anstieg von null würde jeden Ladevorgang allein auf den Spielraum begrenzen
statt auf das, was das Auto aufgenommen hat. Ein solcher Ladevorgang bleibt
unbegrenzt und wird ohne Ladezustand gespeichert — siehe
[kein Start-/End-Ladezustand](#no-startend-soc-on-a-session) unten.

## Aufgezeichneter Verlauf

Jeder abgeschlossene Ladevorgang wird aufgezeichnet und im **eigenen Speicher**
der Integration gehalten statt im Recorder (der nach zehn Tagen löscht). Erfasst
werden Start- und End-Ladezustand, Energie, Dauer, Spitzenleistung, die Ladekurve
und die Kosten.

Die Aufbewahrung stellst du mit **Verlauf aufbewahren (Monate)** unter
**Konfigurieren → Ladekosten & Verlauf** ein (0 = alles behalten).

### Neustart während das Auto lädt

<a id="restarting-while-the-car-is-charging"></a>

Eine laufende Ladung wird schon während des Ladens in den Speicher geschrieben,
daher geht sie nicht mehr verloren, wenn du Home Assistant mittendrin neu
startest, aktualisierst oder die Integration neu lädst. Wenn Home Assistant
zurückkommt:

- **lädt noch** — derselbe Ladevorgang läuft einfach weiter, und die Energie, die
  der Akku gewonnen hat, während niemand zusah, wird aus dem gemessenen
  Ladezustand gutgeschrieben;
- **Laden schon vorbei** — der Ladevorgang endet beim letzten tatsächlich gesehenen
  Wert, nicht in dem Moment, in dem die Integration es bemerkte. Was danach noch
  floss, ist nicht im Wert enthalten, der Datensatz ist also eine *Untergrenze*.

In beiden Fällen wird der Ladevorgang als **`interrupted`** markiert, sichtbar in
`get_charging_sessions` und im Export. Der Batteriezustand überspringt einen
solchen Ladevorgang (siehe [Batteriezustand](DE-Feature-Battery-Health)); Energie
und Kosten zählen überall sonst weiter.

Welcher der beiden Fälle vorliegt, weiß man erst, wenn das Auto es meldet — und
ein Auto, das mit gleichbleibender Leistung lädt, kann stundenlang schweigen.
Hat sich das Auto zwei Minuten nach dem Neustart noch nicht gemeldet, **fragt die
Integration deshalb einmal bei BMW nach** — BMWs Server kennen den letzten Status,
den das Auto geschickt hat, auch ein Ladeende, während Home Assistant aus war.
Das kostet eine der 50 täglichen Abfragen, nur wenn tatsächlich geladen wurde,
und höchstens einmal pro halbe Stunde, egal wie oft du neu startest. Abschalten
lässt sich das mit **`refresh_on_start`** unter **Konfigurieren → Ladekosten &
Verlauf**; der Ladevorgang wartet dann auf das Auto und wird als durch den
Neustart beendet abgelegt, wenn das Auto 15 Minuten lang schweigt. Der geschätzte
Ladezustand steigt währenddessen mit der zuletzt gemessenen Rate weiter.

Vor v0.9.9 ging eine laufende Ladung bei einem Neustart ganz verloren, weshalb
hin und wieder eine Ladung im Verlauf fehlen und die Monatssummen zu niedrig sein
konnten.

### Kein Start-/End-Ladezustand bei einem Ladevorgang

<a id="no-startend-soc-on-a-session"></a>

Ein Ladevorgang zeigt keinen Ladezustand, wenn während der Ladung kein
Ladezustandswert ankam. Das ist die ehrliche Antwort, keine fehlende Funktion:
Der zuletzt bekannte Wert kann Tage alt sein und zu einer anderen Ladung gehören,
und ihn als Start und Ende dieser Ladung auszugeben, zeigte bei jeder Ladung des
Autos ein flaches „38 → 38 %“.

Es bedeutet, dass dein Auto
`vehicle.drivetrain.batteryManagement.header` nicht streamt — BMWs (irreführend
benannten) Ladezustand der Hochvoltbatterie.

**Hast du vor v0.9.6 eingerichtet, führe Konfigurieren → Gestreamte Daten
auswählen erneut aus.** Dieser Deskriptor fehlte im alten Einrichtungs-Snippet,
wurde im Portal also nie eingeschaltet und kam nie über den Stream. Die
Abdeckungs-Reparaturmeldung nennt ihn, sobald er lange genug fehlt.

Ist er eingeschaltet und der Wert ändert sich trotzdem nie, sendet dein Auto ihn
nicht, und die Integration kann daran nichts ändern. Energie, Dauer,
Spitzenleistung, Ladekurve und Kosten sind davon in keinem Fall betroffen, und der
Import von BMWs eigener Ladehistorie
([Frühere Ladungen von BMW importieren](#importing-past-charges-from-bmw)) füllt
den Ladezustand dort nach, wo BMW ihn aufgezeichnet hat.

Ladungen **von vor v0.9.6** zeigen das umgekehrte Bild — ein flaches „38 → 38 %“
und etwa 1,4 kWh — und lassen sich ebenfalls aus BMWs Historie reparieren:
[Ladungen von vor v0.9.6 reparieren](#repairing-charges-from-before-v096).

## Woher die Energie kam

<a id="where-the-energy-came-from"></a>

Ist **Konfigurieren → Solar & Energiequellen** eingerichtet, zeichnet jede Ladung
zusätzlich auf, wie viel ihrer Energie vom eigenen Dach, aus dem Hausspeicher und
aus dem Netz kam — in netzseitigen kWh, also auf der Seite, die deine Hauszähler
messen.

Die Karte zeigt am Ladevorgang ein Etikett **☀ 62 % Solar** und beim Aufklappen
die ganze Aufteilung; der Anteil des Monats steht als Attribute (`solar_percent`,
`energy_mix`) am Sensor **Geladene Energie (dieser Monat)**, und Export wie
gedruckter Monatsbericht enthalten die Zahlen.

### Wie die Aufteilung entschieden wird

Energie wird **während der Lieferung** zugeordnet, nach dem Versorgungsmix des
Hauses in genau diesem Moment — so wie auch der Preis abgetastet wird. Eine Ladung,
die in der Sonne beginnt und nach Sonnenuntergang endet, wird zwischen beiden
aufgeteilt, statt dem zuzufallen, was zuletzt kam.

Das Auto gilt als **ganz normaler Verbraucher**: Es bekommt denselben Mix wie der
restliche Haushalt in diesem Moment. Keine Konvention, die dem Auto zuerst die
Sonne gibt (das schmeichelt dem Dach), und keine, die es zum Grenzverbraucher
macht, der den Netzbezug trägt (das schmeichelt dem Netz). Konkret: In einem
Moment mit 4 kW PV bei den Verbrauchern, 2 kW aus dem Speicher und 2 kW Netzbezug
wird die Energie des Autos zu 50 % PV, 25 % Speicher und 25 % Netz verbucht.

Zwei Folgen, die man kennen sollte:

- **Eingespeister PV-Strom zählt nicht**, und PV-Strom, der *in* den Hausspeicher
  fließt, zählt noch nicht — er wird später als Speicher zugeordnet, wenn er wieder
  herauskommt. So wird dieselbe Kilowattstunde nie doppelt gezählt.
- **Ein fehlender oder nicht verfügbarer Sensor bedeutet „nicht zuordenbar“**,
  nicht „Netz“. Solche Energie landet in einem eigenen Topf, und ein Ladevorgang,
  bei dem nichts zugeordnet werden konnte, speichert gar keinen Mix — denn „wir
  konnten es nicht sagen“ und „nichts davon war Solar“ sind verschiedene Aussagen.
  Der Solaranteil ist immer ein Anteil dessen, was zugeordnet werden *konnte*.

### Was es kostet

Setze **Wert des eigenen Solarstroms pro kWh** — meist deine Einspeisevergütung,
also das Geld, auf das du verzichtest, wenn du nicht einspeist —, und die Kosten
einer Ladung zeigen, was sie dich wirklich gekostet hat. Bleibt das Feld leer,
wird Solarstrom mit deinem Bezugspreis bewertet, bisherige Summen behalten also
ihre Bedeutung und nur der Mix ist neu.

Energie aus dem Hausspeicher wird **immer** mit dem Bezugspreis bewertet, der zu
diesem Zeitpunkt galt. Ihr Inhalt kann mittags vom Dach oder nachts um drei aus
einer günstigen Netzstunde gekommen sein, und die Integration kann das nicht
unterscheiden — sie nimmt daher den vorsichtigen Wert, statt eine Ersparnis zu
behaupten, die es vielleicht nie gab. Die rohen kWh je Quelle werden gespeichert,
du kannst diese Rechnung also jederzeit selbst anders aufmachen.

## Kosten einrichten

Das richtest du unter **Konfigurieren → Ladekosten & Verlauf** ein
([Einstellungen](DE-Settings-Reference#charging-costs--history)). Wähle eine
**Preisquelle**:

- **Fester Preis pro kWh** — ein fester Satz, den du kennst.
- **Preis-Entität** — zeigt auf Tibber, Nordpool, aWATTar, … Diese Entität wird
  *während das Auto lädt* abgetastet, daher wird ein Ladevorgang über einen
  Preiswechsel hinweg richtig abgerechnet und nicht mit dem Preis, der zufällig am
  Ende galt.

> Solange du keine Preisquelle wählst, **werden gar keine Kosten-Entitäten
> angelegt** — ein falscher Wert wäre schlimmer als keiner.

Danach bekommst du pro Fahrzeug:

- **Geladene Energie (dieser Monat)** — immer verfügbar.
- **Ladekosten (dieser Monat)** und **Ladekosten (letzter Ladevorgang)** — sobald
  eine Preisquelle gesetzt ist.
- **Ladekosten pro 100 km** — braucht zusätzlich den Kilometerstand (Datengruppe
  Fahrzeugstatus) und zwei Ladevorgänge, zwischen denen eine Strecke gemessen
  werden kann.

## Netzenergie oder Batterieenergie

<a id="grid-energy-vs-battery-energy"></a>

Die Energie wird **an der Batterie** gemessen, liegt also etwas unter dem, was das
Netz geliefert hat. Zwei Wege, das auszugleichen:

- **Wallbox-Sensor** — wähle den **kumulativen** Energiesensor deiner Wallbox
  (**Wallbox-Energiesensor**), und genau dieser Netzwert wird verwendet: Er landet
  als gemessenes `grid_kwh` am Ladevorgang, fließt in die Monatssummen, die
  Statistiken und den Export ein und wird **während der Ladung** abgerechnet, sodass
  ein dynamischer Tarif jede Kilowattstunde zu dem Satz bepreist, der beim
  Eintreffen galt. Ein Wert, der nicht stimmen kann — ein zurückgesetzter Zähler
  oder einer, der weit weniger als der Akku aufnahm oder fast das Doppelte meldet —,
  wird abgelehnt statt geglaubt, und der Ladevorgang behält seinen batterieseitigen
  Wert. Details und Fehlerfälle:
  [evcc- & Wallbox-Brücke](DE-Feature-evcc-and-Wallbox-Bridge#inbound-your-wallboxs-meter-for-the-charging-history).
- **Ladeverluste in Prozent** — sonst den Batteriewert um deine Ladeverluste
  hochrechnen (**Ladeverluste (%)**). Standard ist **0 %**, weil eine erfundene
  Korrektur wie eine Messung aussähe. Mit verknüpftem Wallbox-Zähler brauchst du
  das nicht: Der Verlust wird dann gemessen.

Ein Ladevorgang, während dem der Preis kurz unbekannt war, wird als `partial`
markiert, statt stillschweigend zu niedrig auszufallen.

<a id="importing-past-charges-from-bmw"></a>

## Frühere Ladungen von BMW importieren

Der Dienst **`bavariandata.fetch_charging_history`** holt BMWs eigene
aufgezeichnete Ladevorgänge und importiert sie in den lokalen Verlauf, sodass
Ladungen von vor der Installation auf der Karte und in den Monatsübersichten
erscheinen. Überlappende live aufgezeichnete Ladevorgänge werden an Ort und Stelle
um BMWs **gemessene Netzenergie** ergänzt. Dieser Dienst **verbraucht**
API-Kontingent (eine oder mehrere deiner 50 pro 24 h). Ohne **Von** und **Bis**
holt er die letzten 30 Tage.

<a id="repairing-charges-from-before-v096"></a>

### Ladungen von vor v0.9.6 reparieren

Bis v0.9.6 kam der Ladezustand nie über den Stream, daher wurde jede Ladung aus
dieser Zeit mit demselben veralteten Wert an beiden Enden gespeichert („38 → 38 %“).
Die Energie wurde auf das begrenzt, was ein flacher Wert zulässt: etwa 2 % des
Akkus, rund 1,4 kWh.

Führe **Ladehistorie abrufen** einmal aus und setze **Von** vor deine erste
betroffene Ladung. Für jede Ladung, bei der BMW einen Anstieg von **mindestens 2
Prozentpunkten** aufgezeichnet hat, die aber flach gespeichert ist, macht der
Import Folgendes:

- er übernimmt BMWs Start- und Endwert,
- er ergänzt BMWs gemessene Netzenergie, die Karte, Monatssummen, Statistiken und
  Export dann zählen,
- er entfernt die batterieseitigen kWh und den Solaranteil, weil beide aus dem
  begrenzten Wert stammen,
- er berechnet die Kosten bei einem festen Preis neu; mit einer Live-Preis-Entität
  bleiben sie erhalten und als **partial** markiert, weil sich vergangene Preise
  nicht rekonstruieren lassen.

Eine Ladung, die auch BMW flach aufgezeichnet hat — etwa ein volles Auto, das
vorklimatisiert —, bleibt genau so, wie sie ist, ebenso eine Ladung, die BMW nicht
aufführt. Ein erneuter Import ändert nichts weiter.

Zwei Dinge bleiben anders als bei einer Ladung, die mit einer korrigierten Version
aufgezeichnet wurde. Diese Ladungen zählen nie zum
[Batteriezustand](DE-Feature-Battery-Health), der batterieseitige Energie braucht.
Und die [reale Reichweite](DE-Feature-Efficiency-and-Range) braucht die
batterieseitigen kWh jeder Ladung, die sie abdeckt; sie zeigt daher nichts statt
eines falschen Werts, bis die reparierten Ladungen älter als 30 Tage sind.

## Auslesen

- Die [Kartenansicht `view: charging`](DE-The-Dashboard-Card#charging-history-view-charging).
- **`bavariandata.get_charging_sessions`** liefert den ganzen Verlauf als
  Antwortdaten des Dienstes (optional `vin`, `from`, `to`, `limit`) — ohne
  Kontingent.
- [Export](DE-Feature-Export) als CSV oder druckbarer Bericht.
- [Langzeitstatistiken](DE-Feature-Energy-and-Statistics) im Energie-Dashboard.
