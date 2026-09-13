# Effizienz & reale Reichweite

> 🇬🇧 [English version](Feature-Efficiency-and-Range)

Wie viel das Auto wirklich verbraucht und wie weit das tatsächlich reicht —
gemessen aus deinem eigenen [Ladeverlauf](DE-Feature-Charging-History-and-Cost)
statt aus der Prognose des Autos übernommen. Ohne REST-Kontingent: Jeder Wert
stammt aus Datensätzen, die der Stream ohnehin erzeugt hat.

## Wie gemessen wird

Jeder Ladevorgang speichert den **Kilometerstand** und den **Ladezustand** in dem
Moment, in dem er endete. Zwei Ladevorgänge schließen daher ein Fenster ein, in
dem sowohl die gefahrene Strecke als auch die hineingeflossene Energie bekannt
sind:

```
verbraucht = zwischen den beiden Messungen geladene Energie
             − die Ladung, die am Ende noch im Akku steckt
Strecke    = Kilometerstand am Ende − Kilometerstand am Anfang
```

Dabei wird bewusst **nichts aus deinen Fahrten** gelesen. Die Fahrterkennung kann
eine Fahrt verpassen — ein ausgefallener Stream, ein Update, eine Garage ohne
Empfang —, und einen ganzen Monat Laden durch einen halben Monat Fahren zu teilen,
ergibt einen maßlos überhöhten Wert. An einem echten Auto ergab ein solcher Monat
86,8 kWh/100 km aus der Fahrtenstrecke und 20,4 aus dem Kilometerstand.

### Das verwendete Fenster

Ein Fenster braucht zwei Ladungen und mindestens ~50 km dazwischen. Feste „letzte
30 Tage“ blieben daher bei allen leer, die selten laden, und „seit jeher“ zitierte
im Juni noch den letzten Winter. Deshalb gilt: **Das kürzeste Fenster, das eine
Antwort liefert, gewinnt** — 30 Tage, dann 90, dann ein Jahr, dann der ganze
Verlauf. Karte und Sensor sagen immer, aus welchem Fenster der Wert stammt —
*letzte 30 Tage* bedeutet etwas anderes als *gesamte Historie*.

### Welche Seite des Ladegeräts

- **Batterieseitig** (`ab Batterie`) — was das Auto dem Akku entnommen hat. Das ist
  der Wert, der mit der Anzeige des Autos vergleichbar ist, und der einzige, aus dem
  eine Reichweite berechnet werden darf.
- **Netzseitig** (`ab Steckdose`) — was aus der Wand kam, einschließlich der
  Ladeverluste. Nur verfügbar, wenn jede Ladung im Fenster tatsächlich am Netz
  *gemessen* wurde (BMWs eigene Ladehistorie oder eine verknüpfte
  Wallbox-Energie-Entität).

Ein Wert ist nie eine Mischung aus beiden. Lässt sich eine Ladung im Fenster nur
auf einer Seite lesen, meldet die andere Seite einfach nichts, statt über eine
Lücke hinweg zu summieren.

## Reale Reichweite

```
Reichweite mit vollem Akku = nutzbare Kapazität ÷ Verbrauch × 100
Reichweite von hier        = das, skaliert mit dem aktuellen Ladezustand
```

Als Kapazität dient BMWs eigene Angabe zur nutzbaren Energie (`maxEnergy`), ersetzt
durch den vom [Batteriezustand](DE-Feature-Battery-Health) gelernten Wert, sobald
dieser sicher ist — der Sensor sagt, welchen er verwendet hat.

Beide Hälften müssen vorhanden sein. Hat das Auto nie eine Kapazität gemeldet oder
reicht der Verlauf noch nicht für einen Verbrauch, bleibt der Sensor **unbekannt**,
statt eine Reichweite auf einer Annahme zu zeigen.

### Verglichen mit der Prognose des Autos

Die Karte zeigt die Abweichung zu BMWs Restreichweiten-Prognose
(`kombiRemainingElectricRange` — die Zahl im Kombiinstrument). Positiv heißt: Dein
gemessener Verbrauch reicht *weiter*, als das Auto verspricht.

BMWs Wert wird nur über **20 % Ladung** auf einen vollen Akku hochgerechnet:
Darunter vervielfacht die Division einer kleinen Restreichweite durch einen kleinen
Prozentwert auch ihren eigenen Fehler.

## Der Sensor Reale Reichweite

Pro E-Auto ein Sensor **Reale Reichweite**:

- **Zustand** — wie weit es mit der aktuellen Ladung kommt, in km.
- **Attribute** — Reichweite mit vollem Akku, der gemessene Verbrauch mit Seite und
  Fenster, der netzseitige Wert und der gemessene Ladeverlust, die Kapazität und
  woher sie stammt, BMWs eigene Prognose und die prozentuale Abweichung.

Er wird für ein E-Auto angelegt, das einen Kilometerstand streamt. Bis der Verlauf
einen Wert trägt, zeigt er `unknown`, und das Attribut `status` sagt, welche Hälfte
fehlt: `not_enough_history` oder `no_capacity`.

> **Zum hier gezeigten Ladeverlust.** Er ist *gemessen* — netzseitiger gegen
> batterieseitigen Verbrauch über dasselbe Fenster — und etwas anderes als die
> Einstellung **Ladeverluste (%)** unter *Ladekosten & Verlauf*, die eine von dir
> angegebene Annahme ist, damit Kosten hochgerechnet werden können, wenn nichts das
> Netz gemessen hat. Hast du beides, ist der gemessene Wert eine gute Probe für die
> Zahl, die du eingetragen hast.

## Ansehen

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-efficiency.png" alt="Karte Effizienz und Reichweite: reale Reichweite beim aktuellen Ladezustand, wie weit das unter der Prognose des Autos liegt, der gemessene Verbrauch mit Seite des Ladegeräts und Fenster, die nutzbare Kapazität und ihre Herkunft sowie ein Balkendiagramm des Verbrauchs nach Monat" width="420" />
</p>

Nutze die [Kartenansicht `view: efficiency`](DE-The-Dashboard-Card#efficiency--range-view-efficiency):
die reale Reichweite von hier, der gemessene Verbrauch, der Ladeverlust, die
Kapazität, deine Kosten pro 100 km mit dem Solaranteil des Monats und ein
Balkendiagramm des Verbrauchs nach Monat — dort zeigt sich die Jahreszeit, denn
der Winterverbrauch eines E-Autos liegt regelmäßig ein Drittel über dem Sommer.

Oder rufe [`bavariandata.get_efficiency`](DE-Services-Reference#get_efficiency)
auf, um das ganze Profil einschließlich des Monatsverlaufs zu bekommen.

## Warum die Werte vom Auto abweichen können

- **Die Anzeige des Autos mittelt anders.** BMWs eigene Verbrauchsanzeige setzt
  sich nach eigenen Regeln zurück und gewichtet anders; diese hier ist eine
  schlichte Summe Energie über Strecke über ganze Ladezyklen.
- **Netzseitig liegt höher als batterieseitig**, um die Ladeverluste — eine
  langsame AC-Ladung in der Kälte kann 10–15 % verlieren.
- **Kurze, kalte Fahrten kosten mehr als der Durchschnitt.** Der Wert beschreibt
  das ganze Fenster, ein Januar voller 5-km-Strecken liegt daher weit über einem
  Sommerdurchschnitt. Dann stimmt die Zahl, sie ist nicht falsch.
