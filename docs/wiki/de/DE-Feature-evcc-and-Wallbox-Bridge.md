# evcc- & Wallbox-Brücke

> 🇬🇧 [English version](Feature-evcc-and-Wallbox-Bridge)

Gib deiner Ladesteuerung den **Live-Ladezustand** des Autos, direkt aus BMWs
Stream — und lass den Zähler deiner Wallbox dem
[Ladeverlauf](DE-Feature-Charging-History-and-Cost) genau sagen, was jede Ladung
aus dem Netz gezogen hat.

Zwei Richtungen, an zwei verschiedenen Stellen eingerichtet und unabhängig
voneinander nützlich.

---

## Ausgehend: der Ladezustand des Autos für evcc

### Warum sich das lohnt

evcc, openWB und ähnliche Steuerungen können selbst mit BMW sprechen. Sie fragen
dazu BMWs Kunden-API zyklisch ab, und die ist begrenzt und für viele unzuverlässig
— es ist die häufigste Beschwerde in deren Issue-Trackern.

BavarianData fragt nicht ab. Der Ladezustand kommt über BMWs MQTT-Stream, sobald
das Auto ihn meldet, und kostet **keine** deiner
[50 täglichen API-Anfragen](DE-Feature-API-Quota). Gibst du ihn an evcc weiter,
bekommt evcc einen besseren Ladezustand, als es allein bekäme, und das kostenlos.

Genau das macht diese Brücke: Sie veröffentlicht, was wir ohnehin wissen, erneut
auf deinem eigenen MQTT-Broker, in der Form, die evccs Fahrzeugtyp `custom`
erwartet.

### Einrichten

**Voraussetzung:** Die **MQTT-Integration** muss in Home Assistant eingerichtet sein
(*Einstellungen → Geräte & Dienste*). Die Brücke veröffentlicht über sie, deshalb
sind hier weder Host noch Passwort des Brokers einzutragen — die Werte landen genau
auf dem Broker, mit dem Home Assistant schon verbunden ist, und das ist fast immer
derselbe, den evcc liest.

1. **Konfigurieren** → **evcc-/Wallbox-Brücke**
2. Hake **Dieses Auto per MQTT veröffentlichen** an. Lass **Topic-Prefix** und
   **Mit Retain veröffentlichen** unverändert, wenn du keinen Grund hast.
3. Der nächste Bildschirm liefert dir die **evcc-Konfiguration**, bereits mit deiner
   FIN, deinem Prefix und der Akkugröße deines Autos ausgefüllt. Kopiere sie.
4. Füge sie in deine `evcc.yaml` unter `vehicles:` ein — zusammen mit bereits
   vorhandenen Fahrzeugen, denn der Schlüssel darf nur einmal vorkommen.
5. Sorge dafür, dass evccs eigener Abschnitt `mqtt:` auf denselben Broker zeigt,
   weise das Fahrzeug einem Ladepunkt zu und starte evcc neu.

Die Konfiguration kannst du jederzeit erneut mit der Aktion
[`get_evcc_config`](DE-Services-Reference#get_evcc_config) abrufen, die dir auch
sagt, welche Topics gerade live sind.

### Was veröffentlicht wird

Alles unter `<prefix>/<FIN>/`, mit `bavariandata` als Standard-Prefix:

| Topic | Was es ist | Von evcc genutzt als |
| --- | --- | --- |
| `soc` | Ladezustand, % | `soc` |
| `status` | `A` nicht angesteckt, `B` angesteckt, `C` lädt | `status` |
| `range` | BMWs eigene elektrische Restreichweite, km | `range` |
| `odometer` | Kilometerstand, km | `odometer` |
| `limitSoc` | Das Ladeziel des Autos, % | `limitSoc` |
| `chargePower` | Ladeleistung, kW | — |
| `plugged` / `charging` | `true` / `false` | — |
| `updated` | Wann BMW den Ladezustand zuletzt *gemessen* hat (ISO 8601) | — |
| `state` | Alles oben als ein JSON-Dokument | — |

`chargePower`, `plugged`, `charging`, `updated` und `state` nutzt evcc nicht. Sie
sind für das MQTT-SoC-Modul von openWB, für Node-RED-Flows und zum Nachsehen im MQTT
Explorer da, wenn etwas nicht funktioniert.

### Drei Dinge, die sie bewusst tut

**Was das Auto nicht meldet, wird nicht veröffentlicht.** Nicht als Null — es fehlt
einfach. Eine Ladesteuerung *handelt* nach diesen Zahlen: Mit `soc: 0` lädt sie
einen vollen Akku, und mit `status: A` („kein Fahrzeug verbunden“) kann eine
erkennende Wallbox das Laden des angesteckten Autos beenden. Ein Auto, das keinen
Steckerstatus meldet, bekommt daher kein Topic `status`, und evcc greift auf seine
eigene Behandlung zurück, statt in die Irre geführt zu werden. Wird ein Wert später
wieder unbekannt, wird sein Topic entfernt, statt den Wert von letzter Woche zu
zeigen.

**Alles wird alle fünf Minuten unverändert erneut gesendet.** Ein geparktes Auto
streamt tagelang nichts, und sein Ladezustand stimmt deshalb nicht weniger. evccs
Plugins behandeln einen Wert, der nicht mehr kommt, als veraltet, daher setzt die
erzeugte Konfiguration bewusst **kein `timeout`**, und die Brücke hält die Werte auf
dem Broker frisch.

**Das Abschalten der Brücke entfernt, was sie veröffentlicht hat.** Retain-Nachrichten
überleben Home Assistant absichtlich — so findet evcc den Ladezustand sofort beim
Start. Deshalb reicht einfaches Aufhören nicht: Ein Broker würde weiter einen
Ladezustand ausliefern, der sich nie wieder ändert, ohne dass evcc das erkennen
kann. Das Abhaken löscht also die Topics. (Ein Neustart, Neuladen oder Update tut
das *nicht* — der letzte bekannte Wert stimmt für diese paar Sekunden noch.)

### Der veröffentlichte Ladezustand

Derselbe Wert, den die Karte und der Sensor **Ladezustand (integrationsseitig
vorhergesagt)** zeigen: BMWs letzte Messung, beim Laden anhand der Ladeleistung
hochgerechnet — oder anhand deines
[Wallbox-Zählers](#it-also-drives-the-state-of-charge-estimate), sobald einer
verknüpft ist und gelernt hat. evcc und dein Dashboard können sich beim Prozentwert also nie
widersprechen. Das Topic `updated` trägt, wann BMW ihn zuletzt tatsächlich
*gemessen* hat, damit du siehst, wie alt die zugrunde liegende Messung ist.

### Mehrere Autos, mehrere Konten

Jedes Fahrzeug bekommt einen eigenen Topic-Zweig, benannt nach seiner FIN, und die
erzeugte Konfiguration führt jedes Auto, das der Eintrag kennt, unter einem einzigen
Schlüssel `vehicles:` auf.

---

## Eingehend: der Zähler deiner Wallbox für den Ladeverlauf

<a id="inbound-your-wallboxs-meter-for-the-charging-history"></a>

Energie aus dem Stream wird **an der Batterie** gemessen und liegt damit unter dem,
was das Netz tatsächlich geliefert hat — die Differenz sind die Ladeverluste. Stellt
deine Wallbox in Home Assistant einen kumulativen Energiesensor bereit, kann
BavarianData diesen genauen Wert verwenden, statt zu schätzen.

**Konfigurieren** → **Ladekosten & Verlauf** → **Wallbox-Energiesensor**.

Wähle den **Gesamt**-Energiesensor der Wallbox — den Zähler über die Lebensdauer,
der nur steigt (`state_class: total_increasing`), keinen, der pro Ladevorgang
zurücksetzt. Wh, kWh und MWh werden alle verarbeitet.

Was sich damit ändert:

- Jeder Ladevorgang speichert neben dem batterieseitigen Wert ein gemessenes
  **`grid_kwh`**, und die Monatssummen, die Statistiken und der CSV-Export nutzen den
  gemessenen Wert, wo es ihn gibt.
- **Die Kosten werden aus dem Fortschritt des Zählers selbst abgerechnet**, während
  der Ladung abgetastet — ein dynamischer Tarif bepreist also jede Kilowattstunde zu
  dem Satz, der bei ihrer Lieferung galt.
- Der **Ladeverlust** in der [Effizienz-Ansicht](DE-Feature-Efficiency-and-Range)
  wird gemessen statt angenommen — wenn der Zähler über unserem batterieseitigen
  Wert liegt. Unser Wert kann etwas zu hoch liegen (an einem echten Auto lag der
  Zähler rund 1 % *darunter*), dann wird gar kein Verlust gezeigt statt eines
  negativen. **Ladeverluste (%)** kannst du auf 0 % lassen: Die Einstellung gibt es
  nur für alle ohne Zähler.

### Wenn der Wert abgelehnt wird

<a id="when-the-reading-is-refused"></a>

Ein gemessener Wert ist nur etwas wert, wenn er wirklich dieses Auto misst. Die
Differenz jedes Ladevorgangs wird daher geprüft und **verworfen statt geglaubt**,
wenn sie nicht stimmen kann:

- Die Ladung fand **außerhalb deiner Zone „Zuhause“** statt. Der Zähler weiß nicht,
  welches Auto er geladen hat; während dein Auto bei der Arbeit oder an einer
  öffentlichen Säule lädt, würde sonst ein anderes Auto an deiner Wallbox ihm
  zugebucht — und eine Ladung ähnlicher Größe besteht jede Prüfung unten. Eine
  Ladung ohne bekannte Position behält den Zähler, weiter geprüft wie unten.
- Der Zähler ging **zurück oder blieb stehen** — ein Zurücksetzen, ein Stromausfall
  oder ein Zähler, der diese Ladung nicht mitgezählt hat.
- Er meldet **weit weniger, als der Akku aufnahm**. Das Netz kann nicht weniger
  liefern, als der Akku bekam; unser eigener Batteriewert liegt nachweislich etwas
  hoch, dafür ist Spielraum da, aber ein Zähler, der sich kaum bewegt hat, zählt
  etwas anderes.
- Er meldet **fast das Doppelte** des Batteriewerts. Das ist die häufige
  Fehlkonfiguration: den *Haus*-Bezugszähler statt des Wallbox-Zählers zu
  verknüpfen, was sonst jede Kostenangabe um das aufbliese, was der Haushalt in
  diesen Stunden sonst noch tat.

Wird ein Wert abgelehnt, behält der Ladevorgang seinen batterieseitigen Wert, und
nichts wird als gemessen dargestellt, was es nicht ist. Die Gegenprüfung entfällt
bei Ladevorgängen, deren eigene Energie bekanntermaßen zu kurz ist — einer, der
durch einen Neustart von Home Assistant unterbrochen wurde, oder einer, der begann,
bevor wir es bemerkten —, denn dort ist der Zähler das Einzige, was den fehlenden
Teil gesehen hat, und genau dort ist er am wertvollsten.

### Er treibt auch den geschätzten Ladezustand

<a id="it-also-drives-the-state-of-charge-estimate"></a>

Zwischen BMWs Messungen steigt der geschätzte Ladezustand normalerweise mit der
Rate, die BMWs letzte Ladeleistung ergibt. Diese Leistung kommt schubweise und kann
Stunden alt sein, bei einer PV-geführten oder unterbrochenen Ladung läuft die
Schätzung daher davon. Hat der Zähler **drei Ladungen zu Hause** gesehen, bewegt
stattdessen er die Schätzung, um genau das, was er gezählt hat.

Wie weit die Kilowattstunden des Zählers den Akku bewegen, wird aus deinem eigenen
Ladeverlauf berechnet, aus den **letzten zehn Ladungen zu Hause**, die der Zähler
gemessen hat:

> gewonnene Prozentpunkte = Wirkungsgrad × (kWh vom Zähler − Grundlast × Ladestunden)

Die **Grundlast** ist, was das Auto beim Laden zieht, ohne dass es im Akku ankommt:
das Bordladegerät, das 12-V-System, das Batteriemanagement. Beim i5 des Maintainers
sind das rund 0,3 kW. Deshalb ist eine PV-Überschussladung mit 2 kW sichtbar
weniger effizient als eine mit 11 kW, und ein fester Prozentsatz pro kWh liegt bei
einer der beiden daneben. Nur aus PV-Ladungen gelernt, sagte das Modell eine
11-kW-Ladung von 8 auf 80 % auf 0,8 % genau voraus. Die Zahl der Phasen brauchte
keinen eigenen Term.

Eine Ladung zählt, wenn sie mindestens 5 Punkte gebracht hat und nicht vor unserem
Bemerken begann. Eine durch einen Neustart von Home Assistant unterbrochene Ladung
zählt weiter: Der Zählerstand läuft über einen Neustart hinweg. Ebenso eine
Ladung, in die später BMWs Ladehistorie eingeflossen ist: Dabei bleibt der Wert
deines Zählers erhalten. Eine vor v0.9.17 zusammengeführte Ladung zählt nicht,
weil ihr Wert durch BMWs ersetzt worden sein kann, der von einem anderen
Messgerät stammt. Eine einzelne Ladung, die weit neben den anderen
liegt (BMWs Ladezustand nahe 100 % kann das), bleibt außen vor. Laufen alle deine
Ladungen mit derselben Leistung, lassen sich Wirkungsgrad und Grundlast nicht
trennen, und es gilt ein einfacher Prozentsatz pro kWh, der bei dieser Leistung
genau ist.

Nichts wird separat gelernt, es gibt also nichts zurückzusetzen. Wechselst du die
Wallbox, übernehmen die neueren Ladungen innerhalb von ein, zwei Wochen. Die
Akkugröße kürzt sich aus der Rechnung heraus, eine falsche Kapazität von BMW kann
sie also nicht verfälschen.

Der Zähler treibt nur, solange er sich nicht irren kann, welches Auto er zählt:

- **dieses Auto lädt, und zwar zu Hause** (dieselbe Zonenregel wie oben);
- **sein Kabel steckt** — vergisst BMW, eine Ladung zu beenden, und meldet das
  Auto sich als ausgesteckt, lädt die Wallbox jetzt ein anderes Auto;
- **kein anderes Auto dieses Eintrags lädt** gleichzeitig — bei zweien gehört der
  Fortschritt des Zählers beiden, und keines bekommt ihn;
- **der Wert ist plausibel** — ein Zähler, der zurückgeht, beginnt eine neue
  Zählung, und ein Sprung, schneller als jede 22-kW-Wallbox liefern könnte, wird
  ignoriert.

Sonst fällt die Schätzung auf BMWs Ladeleistung zurück und macht dort weiter, wo
der Zähler sie gelassen hat, ohne Sprung. Eine echte Messung von BMW ersetzt die
Schätzung weiterhin immer, und sie hält weiter am Ladeziel des Autos an. Startet
Home Assistant mitten in der Ladung neu, deckt der Fortschritt des Zählers die
Ausfallzeit ab, und die Schätzung kommt dort zurück, wo der Akku wirklich steht.

Auch die **Laderate** folgt dem Zähler: die %/h, mit denen die Schätzung tatsächlich
steigt, gemessen über mindestens fünf Minuten. Hat sich der Zähler 15 Minuten lang
nicht bewegt, eine pausierte PV-Ladung, zeigt sie keine Rate. Eine Wallbox, die
seltener meldet, zeigt zwischen ihren Meldungen keine Rate.

<a id="time-to-the-charge-target"></a>
Aus beidem berechnet der Sensor **Vorhergesagte Zeit bis zum Ladeziel**, wie lange
es noch dauert: (Ladeziel − Schätzung) ÷ Laderate, in Minuten. Beim i5 des
Maintainers lag eine 11-kW-Ladung auf 80 % jedes Mal 1–3 Minuten daneben, BMWs
eigene *Restladedauer* dagegen 7–26 Minuten zu lang, und sie änderte sich nur etwa
alle 40 Minuten. Einen Wert gibt es nur, solange der Zähler treibt, also nur zu
Hause; `0`, wenn das Auto nicht lädt, und unbekannt in den ersten fünf Minuten
einer Ladung oder während eine PV-Ladung pausiert (wann sie endet, hängt an der
Sonne). Unterwegs ist BMWs Wert der bessere: Eine DC-Schnellladung wird zum Ende
hin stark langsamer, was BMW weiß und eine gerade Linie aus der aktuellen Rate
nicht. Die Dashboard-Karte zeigt diesen Wert, wenn es einen gibt, sonst BMWs.
Auch AC-Laden wird kurz vor 100 % langsamer, dort kommt er also etwas zu früh.

BMW hört am Ende einer Ladung auf, *Restladedauer*, *verbleibende Ladezeit* und
*Ladeleistung* zu senden, statt null zu senden; deshalb blieben sie bisher für
immer auf ihrem letzten Wert. Jetzt werden sie auf 0 gesetzt, sobald die Ladung
zwei Minuten lang beendet ist.

Der Sensor **Ladezustand (integrationsseitig vorhergesagt)** zeigt, was ihn treibt:
`estimate_source` (`meter` oder `stream`), `meter_sessions` (die Ladungen, aus
denen gelernt wurde, 0, bis drei zählen), `meter_percent_per_kwh` und
`meter_overhead_kw`.

DC-Laden wird zu Hause nie gezählt und nutzt daher wie bisher BMWs Leistung. Gibt
deine Wallbox nur eine **Leistung** her, keinen Energiezähler: Lege in Home
Assistant einen **Integral**-Helfer an (*Einstellungen → Geräte & Dienste →
Helfer*, „Integralsensor“, Riemann links, Präfix k) auf ihren Leistungssensor und
verknüpfe diesen.

---

## Fehlerbehebung

**Auf dem Broker erscheint nichts.** Prüfe, ob die MQTT-Integration eingerichtet und
geladen ist; fehlt sie, schreibt die Brücke genau das als Warnung ins Log. Prüfe
dann mit `get_evcc_config`, dass `mqtt_available` `true` ist, und sieh nach, was
`published_topics` auflistet.

**evcc zeigt keinen Ladezustand.** Prüfe, ob evccs Abschnitt `mqtt:` auf denselben
Broker zeigt und das Topic in deinem Block `vehicles:` Zeichen für Zeichen zum
Prefix im Einstellungsbildschirm passt. Abonniere `bavariandata/#` im MQTT Explorer,
um zu sehen, was wirklich ankommt.

**`status` fehlt.** Die Brücke hat keinen Steckerstatus gefunden. Autos melden ihn
an unterschiedlichen Stellen, daher liest sie der Reihe nach: *Batterie-Ladeanschluss
an beliebiger Position angeschlossen*, *Status der Ladesteckverbindung*,
*Status-Text des Ladeanschlusses* und *Status des Ladesteckers* — ein i5 etwa meldet
nur *Status der Ladesteckverbindung*. Führe
[Gestreamte Daten auswählen](DE-Getting-Started-4-Choose-Data) mit den
entsprechenden Datengruppen erneut aus. Meldet das Auto dann immer noch keinen davon,
erfindet die Brücke keinen.

**Der Ladezustand wirkt veraltet.** Vergleiche das Topic `updated` mit jetzt. Ist
BMWs eigene Messung Stunden alt, liegt es am Stream, nicht an der Brücke — das Auto
meldet sich, wann es will, und die meisten Autos schweigen im geparkten Zustand.

**Eine Ladung hat kein `grid_kwh`.** Siehe *Wenn der Wert abgelehnt wird* oben.
Schalte die Debug-Protokollierung ein (**Konfigurieren → Debug-Protokollierung**) und
suche die Zeile `[charge]` zu diesem Ladevorgang.
