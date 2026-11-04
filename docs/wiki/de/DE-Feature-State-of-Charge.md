# Ladezustand beim Laden: warum BMWs Wert stehen bleibt, und die Schätzung, die weiterzählt

> 🇬🇧 [English version](Feature-State-of-Charge)

Wer in Home Assistant einem BMW beim Laden zusieht, erlebt oft, dass der
Ladestand eine Stunde oder länger stehen bleibt und dann springt. Das liegt nicht
an Home Assistant: **BMW CarData streamt den Ladezustand (SoC) während des Ladens
oft nicht**. Ein BMW i5 kann stundenlang laden, ohne einen einzigen neuen
Prozentwert zu senden, und meldet das Ergebnis erst, wenn er aufhört. Der Wert,
den die MyBMW-App beim Laden zeigt, ist BMWs serverseitige *Vorhersage*. CarData
liefert ihn nur über REST, das auf [50 Anfragen pro Tag](DE-Feature-API-Quota)
begrenzt ist und sich nie über den Stream aktualisiert.

BavarianData füllt die Lücke mit einer eigenen Schätzung und nutzt sie überall,
wo der Ladestand zählt.

## So funktioniert die Schätzung

- **Geparkt:** Sie ist einfach BMWs letzter gemessener Wert.
- **Beim Laden:** Zwischen BMWs Meldungen steigt sie mit der Rate, die die
  Ladeleistung ergibt, und hält am Ladeziel des Autos an.
- **Mit einem Wallbox-Zähler zu Hause:** Sobald der Zähler drei Ladungen zu
  Hause gesehen hat, bewegt er die Schätzung, um genau die gezählte Energie. Wie
  viele Prozentpunkte eine Kilowattstunde wert ist und was das Auto beim Laden
  selbst verbraucht, wird aus deinen letzten zehn Ladungen zu Hause gelernt. Beim
  i5 des Maintainers sagte das eine 11-kW-Ladung von 8 auf 80 % auf 0,8 Punkte
  genau voraus.
  [Die Details](DE-Feature-evcc-and-Wallbox-Bridge#it-also-drives-the-state-of-charge-estimate).
- **Jede echte Meldung gewinnt.** Sendet BMW einen neuen Prozentwert, übernimmt
  die Schätzung ihn, sie kann also nie lange abdriften.

## Wo du sie siehst

| Entität | Was sie ist |
| --- | --- |
| **Ladezustand (integrationsseitig vorhergesagt)** | Die Schätzung. Der Ring der Karte, die [evcc- und MQTT-Brücke](DE-Feature-evcc-and-Wallbox-Bridge), [ABRP](DE-Feature-ABRP), [intelligentes Laden](DE-Feature-Smart-Charging) und [Assist](DE-Feature-Assist-and-AI) nutzen alle diese. |
| **Vorhergesagte Ladegeschwindigkeit** | Wie schnell die Schätzung steigt, in % pro Stunde. |
| **Vorhergesagte Zeit bis zum Ladeziel** | Minuten, bis eine Ladung **zu Hause** das Ziel erreicht, aus dem Wallbox-Zähler. Unterwegs zeigt die Karte stattdessen BMWs eigenen Wert. |
| **Battery HV State Of Charge** | BMWs gemessener Wert, wie gestreamt. Er bleibt stehen, solange BMW schweigt. |

Nimm die Schätzung für Automationen wie „Benachrichtige mich bei 80 %“ oder
„Stoppe die Wallbox am Ziel“: Sie bewegt sich, wenn sich der echte Akku bewegt.
Den gemessenen Wert gibt es, wenn du BMWs eigene Zahl brauchst. Alle
Akkustand-Entitäten, und welche du nimmst, stehen unter
[welche Ladezustands-Entität verwenden](DE-Feature-Entities-and-Devices#which-state-of-charge-entity-to-use).

## Fragen

### Der Akkustand aktualisiert sich beim Laden nicht

Prüfe, welche Entität du ansiehst. *Battery HV State Of Charge* ist BMWs Messung
und bleibt stehen, solange BMW schweigt. *Ladezustand (integrationsseitig
vorhergesagt)* zählt weiter. Hat keine von beiden überhaupt einen Wert, ist das
Ladezustands-Feld in deiner Datenauswahl nicht eingeschaltet. Siehe
[kein Start-/End-SoC bei einer Ladung](DE-Feature-Charging-History-and-Cost#no-startend-soc-on-a-session).

### Die Schätzung sprang, als BMW meldete

Dann wich BMWs Wert von der Schätzung ab, und der Wert gewinnt. Ohne
Wallbox-Zähler folgt die Schätzung BMWs Ladeleistung, die in Schüben kommt und bei
einer PV-geführten oder unterbrochenen Ladung alt sein kann. Ein gebundener
[Wallbox-Zähler](DE-Feature-evcc-and-Wallbox-Bridge#inbound-your-wallboxs-meter-for-the-charging-history)
macht diese Sprünge klein.

### Funktioniert es an einer öffentlichen Ladesäule?

Ja, über BMWs Ladeleistung. Der Wallbox-Zähler hilft nur zu Hause, und nur dort
ist die Zeit bis zum Ladeziel BavarianDatas eigene. An einem DC-Schnelllader
zeigt die Karte BMWs Zeit bis voll.

### Kostet es API-Kontingent?

Nein. Die Schätzung entsteht in Home Assistant aus dem Stream.
