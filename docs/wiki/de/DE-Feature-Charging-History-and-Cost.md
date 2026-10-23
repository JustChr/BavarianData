# Ladeverlauf & Kosten

> 🇬🇧 [English version](Feature-Charging-History-and-Cost)

Weil BavarianData Daten in Echtzeit streamt, zeichnet es jeden abgeschlossenen
Ladevorgang auf und kann ihm einen Preis geben — alles aus dem Stream, ohne
**REST-Kontingent** zu verbrauchen.

## Sensoren für geladene Energie

BMW sendet nie einen kWh-Zähler, daher integriert die Integration die
Live-Ladeleistung über die Zeit zu zwei Sensoren pro Fahrzeug:

- **Geladene Energie (gesamt)** — ein nur steigender kWh-Zähler
  (`device_class: energy` / `state_class: total_increasing`). Er zählt jede
  Ladung, ob zu Hause oder nicht. Für das **Energie-Dashboard** nimm stattdessen
  die Reihe
  [geladene Energie zu Hause](DE-Feature-Energy-and-Statistics#adding-to-the-energy-dashboard).
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

Das gilt auch für ein Auto, das während der Ladung schläft und den neuen
Ladezustand erst mit dem Ende der Ladung schickt: Beim Abschluss wird die Ladung
noch einmal gegen diesen Wert verrechnet. Die zurückgehaltene Energie behält die
Aufteilung nach PV, Hausbatterie und Netz und den Preis des Moments, in dem sie
geladen wurde, sodass eine Nachtladung, die erst bei Sonnenaufgang freigegeben
wird, trotzdem als Netzstrom zählt.

**Mit einer verknüpften Wallbox ist stattdessen ihr Zähler die Grenze** (zu
Hause, wo er die Ladung misst). Der Wert ist dann das, was das Auto gemeldet
hat, aber nie mehr, als der Zähler gezählt hat. Das hebt eine Ladung an, für die
das Auto keinen neuen Ladezustand geschickt hat und die die Ladezustands-Grenze
beim Spielraum festgehalten hätte, und es kürzt Leistung, die das Auto zuletzt
gemeldet hat, die Wallbox aber nicht mehr geliefert hat, etwa bei einem
Stromausfall. Die Ladezustands-Grenze gilt wieder, solange der Zähler noch nichts
gezählt hat und wenn er weniger gezählt hat, als der Anstieg des Ladezustands
beweist: Dann hängt er oder misst etwas anderes. Seit 0.9.22; bereits
gespeicherte Ladungen behalten ihren Wert.

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

Hört eine Ladung auf und lädt dann weiter, bleibt sie **ein Ladevorgang**,
solange die Pause kurz ist: bis zu **fünf Minuten**, wenn das Kabel steckt
(manche Autos pausieren mitten im Laden von selbst), oder bis zu **zwei
Minuten**, wenn dein Auto nicht meldet, ob es angesteckt ist. Eine längere Pause
wird als **zwei Ladevorgänge** aufgezeichnet, auch wenn das Kabel die ganze Zeit
steckte. Solarladen, das eine lange Wolke abwartet, kann deshalb mehrere
Ladevorgänge an einem Tag hinterlassen. Ziehst du das Kabel, endet der
Ladevorgang sofort. Er endet zu dem Zeitpunkt, an dem das Laden aufgehört hat,
aber die Ereignisse zum Ladeende (`bavariandata_charging_stopped` und die
Auslöser, die darauf aufbauen) kommen erst, wenn die Wartezeit vorbei ist: mit
steckendem Kabel bis zu fünf Minuten später.

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
Integration deshalb bei BMW nach** — BMWs Server kennen den letzten Status, den
das Auto geschickt hat, auch ein Ladeende, während Home Assistant aus war. Das
ist die [automatische Aktualisierung](DE-Settings-Reference#automatic-data-refresh)
nach jedem Start, und ein laufender Ladevorgang wird auch dann geprüft, wenn der
Rest als aktuell übersprungen wird. Ist sie abgeschaltet, wartet der Ladevorgang
auf das Auto und wird als durch den Neustart beendet abgelegt, wenn das Auto 15
Minuten lang schweigt. Der geschätzte
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

Das gilt nur für **Ladungen zu Hause**. An einer öffentlichen Ladesäule, bei der
Arbeit oder an jedem anderen Ort, den das Auto außerhalb deiner Zone Home meldet,
sagen deine Hauszähler nichts darüber, was ins Auto geflossen ist; eine solche
Ladung speichert daher keinen Mix. Dasselbe gilt für die Kosten: Dein Tarif ist
dein Preis zu Hause, also bekommt eine Ladung woanders daraus keine Kosten. Ihre
Energie zählt trotzdem überall. Hat BMW die Ladung selbst abgerechnet, ergänzt
[der Import des BMW-Verlaufs](#importing-past-charges-from-bmw) diese Kosten. Ein
Auto, das gar keine Position meldet, gilt wie bisher als zu Hause. Ladungen von
vor v0.9.18, die unterwegs deinen Mix oder Tarif bekommen haben, werden beim
nächsten Start von Home Assistant bereinigt.

Die Karte zeigt am Ladevorgang ein Etikett **☀ 62 % Solar** und beim Aufklappen
die ganze Aufteilung; der Anteil des Monats steht als Attribute (`solar_percent`,
`energy_mix`) am Sensor **Geladene Energie (dieser Monat)**, und Export wie
gedruckter Monatsbericht enthalten die Zahlen. Eine Ladung ganz ohne Solarstrom
bekommt kein Etikett. Der Anteil bezieht sich auf die Energie, die gemessen werden
konnte: Ladungen von vor der Einrichtung der Sensoren zählen zur Energie des Monats,
aber nicht zu seinem Mix, deshalb sagt der Monatsbericht es ausdrücklich, etwa
*76 % — 75 von 99 kWh gemessen*.

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
  Eine Ausnahme: Meldet der PV-Sensor bei Sonne unter dem Horizont „unbekannt“
  (viele Wechselrichter gehen nachts offline), zählt er als 0 W, sodass auch
  eine Ladung in der Nacht auf Netz und Hausspeicher aufgeteilt wird.

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

<a id="setting-up-cost"></a>

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
  werden kann. Jede Ladung des Monats braucht Kosten: Die Strecke umfasst jeden
  Kilometer, also lässt eine Ladung unterwegs, die keine Kosten hat, den Monat
  ohne diesen Wert. Nicht bei einem Plug-in-Hybrid, dessen Kilometerstand auch die
  mit Kraftstoff gefahrenen Kilometer zählt
  ([warum](DE-Feature-Efficiency-and-Range#plug-in-hybride)).

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
markiert, statt stillschweigend zu niedrig auszufallen. Auch die Kosten des Monats
werden als `partial` markiert, wenn sie eine Ladung ganz ohne Kosten auslassen,
etwa eine unterwegs. Auch gemessene Ladungen zu Hause, die vor v0.9.22
gespeichert wurden, können so markiert sein: Eine ältere Version hat einen Teil
ihrer Energie nicht in Aufteilung und Kosten übernommen. Weil der Preis dafür nie
erfasst wurde, stehen die fehlenden kWh als nicht bepreist (`unpriced_kwh`) da,
statt geschätzt zu werden.

<a id="importing-past-charges-from-bmw"></a>

## Frühere Ladungen von BMW importieren

Der Dienst **`bavariandata.fetch_charging_history`** holt BMWs eigene
aufgezeichnete Ladevorgänge und importiert sie in den lokalen Verlauf, sodass
Ladungen von vor der Installation auf der Karte und in den Monatsübersichten
erscheinen. Überlappende live aufgezeichnete Ladevorgänge werden an Ort und Stelle
um BMWs **gemessene Netzenergie** ergänzt, außer dein Wallbox-Zähler hat die
Ladung schon gemessen: Dann bleiben sein Wert und die daraus berechneten Kosten
erhalten. BMWs eigene abgerechnete Kosten ersetzen sie trotzdem. Schließt BMWs
Eintrag auch kurze Teile derselben Ladung ein, die der Zähler nicht gemessen hat,
gilt BMWs Wert für die ganze Ladung. Dieser Dienst **verbraucht**
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

<a id="charging-places"></a>

## Ladeorte

Der Reiter **Orte** der Karte listet jeden Ort, an dem das Auto geladen hat, die
meistbesuchten zuerst, gebildet aus den aufgezeichneten Ladevorgängen. Zusätzlich
gespeichert wird nichts: Löschst du den Verlauf, verschwinden auch die Orte. Je Ort:

- **Besuche**, die **typische Standzeit** (der Median der Ladedauer dort) und die
  dort insgesamt geladene Energie;
- **AC oder DC**. Das Auto sagt es, wenn es seinen Steckertyp streamt; sonst zählt
  eine Spitze über 22 kW als DC, denn kein BMW lädt an AC schneller. AC- und
  DC-Besuche werden nie miteinander gemittelt;
- die **durchschnittliche Leistung**, die du tatsächlich bekommen hast, und ab zwei
  Besuchen der **beste** Besuch;
- **Minuten je 10 %**, gezählt nur aus Besuchen, die mindestens 20 % nachgeladen
  und bei höchstens 80 % geendet haben. Über 80 % wird ein Schnelllader stark
  langsamer, das ließe einen guten Lader langsam aussehen;
- **vorkonditioniert oder nicht**, wenn es Besuche beider Art gibt. Nur BMWs
  Ladeverlauf sagt, ob die Batterie vorkonditioniert war, dafür braucht es also
  [einen Import](#importing-past-charges-from-bmw);
- die Leistungskurven der letzten fünf Besuche, übereinander gezeichnet.

Ein Ort mit einem Besuch zeigt die Werte dieses Besuchs, nie einen „Durchschnitt“
aus einem. Spät erkannte Ladungen und Ladungen, in die ein Neustart fiel, zählen
als Besuch, aber nicht für die Geschwindigkeit: Ihre Energie ist nur eine
Untergrenze.

**Wie aus Ladungen Orte werden.** Eine Ladung in einer Home-Assistant-Zone gehört
zu dieser Zone. Außerhalb deiner Zonen benennen BMWs Adresse und Ladepunktbetreiber
den Ort; der Betreiber wird nur gezeigt, wenn alle Ladepunkte, die BMW zugeordnet
hat, zum selben gehören. Eine Ladung ganz ohne Position zählt als Zuhause, nach
derselben Regel wie der Tarif, und ist als *angenommen* markiert. Ladungen
unterwegs ohne Position und ohne Adresse teilen sich eine Zeile **Andere Orte**,
ohne Geschwindigkeiten, weil sie verschiedene Lader mischt.

<a id="remember-charging-locations"></a>

### Ladeorte merken

**Konfigurieren → Ladekosten & Verlauf → Ladeorte merken** (standardmäßig aus)
behält die Koordinaten jeder Ladung. Ist es an, sind Ladungen innerhalb von 75 m
ein Ort, und der Reiter Orte zeigt sie auf einer **Landkarte**. Es schreibt rohe
Koordinaten auf die Festplatte, deshalb ist es aus, bis du es einschaltest.
Schaltest du es wieder aus, wird **jede gespeicherte Koordinate gelöscht**; Zonen,
Adressen und Betreiber bleiben, ebenso die Liste.

Es wirkt ab dem nächsten Laden. Für ältere Ladungen einmal
[den Ladeverlauf abrufen](#importing-past-charges-from-bmw): BMWs Einträge tragen
die Position, und der Import ergänzt sie bei den Ladungen, die du schon hast.

Die Orte kommen von `get_charging_places` (unten), das **kein Kontingent** kostet.
DC-Laden und BMWs Betreibernamen wurden noch in keinen echten Fahrzeugdaten
gesehen. Zeigt deines etwas Merkwürdiges, helfen
[Diagnosedaten](DE-Troubleshooting-and-FAQ#download-diagnostics).

## Auslesen

- Die [Kartenansicht `view: charging`](DE-The-Dashboard-Card#charging-history-view-charging)
  mit ihren Reitern **Ladevorgänge** und **Orte**.
- **`bavariandata.get_charging_places`** liefert die Orte als Antwortdaten des
  Dienstes (optional `vin`, `from`, `to`) — ohne Kontingent.
- **`bavariandata.get_charging_sessions`** liefert den ganzen Verlauf als
  Antwortdaten des Dienstes (optional `vin`, `from`, `to`, `limit`) — ohne
  Kontingent.
- [Export](DE-Feature-Export) als CSV oder druckbarer Bericht.
- [Langzeitstatistiken](DE-Feature-Energy-and-Statistics) im Energie-Dashboard.
