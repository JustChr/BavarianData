# Ereignisse & Automations-Blueprints

> 🇬🇧 [English version](Feature-Automations)

BavarianData streamt Daten in Echtzeit und eignet sich daher für Automationen,
die sofort auf eine Änderung reagieren statt auf einen Abfragezyklus zu warten.

## Geräte-Auslöser

Der einfachste Weg: Automation anlegen, als Auslöser **Gerät** wählen, dein Auto
auswählen und aus der Liste wählen. Kein YAML, keine Entitätsnamen.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/automation-device-triggers.png" alt="Der Geräte-Auslöser im Automations-Editor mit gewähltem Auto und offener Auslöser-Liste: Alarm went off, Arrived at a zone, Charging completed (target reached), Charging started, Charging stopped before the target" width="360" />
</p>

| Auslöser | Löst aus, wenn | Optionen |
| --- | --- | --- |
| **In einer Zone angekommen** | eine Fahrt in einer Zone endet, sobald das Auto steht (meist 1–3 Minuten nach dem Anhalten) | eine Zone, oder leer für jede Zone |
| **Eine Zone verlassen** | eine Fahrt das Auto aus der Zone bringt, in der sie begann | eine Zone, oder leer für jede Zone |
| **Fahrt beendet (überall)** | eine Fahrt ins Fahrtenbuch eingetragen wird, egal wo sie endete | — |
| **Geparkt und nicht verriegelt** | das Auto die gewählte Zeit unverriegelt steht | wie lange (Standard 10 Minuten) |
| **Geparkt mit offenem Fenster** | ein Fenster oder das Schiebedach die gewählte Zeit offen oder halb offen steht | wie lange (Standard 10 Minuten) |
| **Angesteckt, lädt aber nicht** | ein Kabel steckt, nichts lädt und das Auto unter seinem Ladeziel ist, für die gewählte Zeit | wie lange (Standard 15 Minuten) |
| **Laden gestartet** | ein Ladevorgang beginnt | — |
| **Laden abgeschlossen (Ziel erreicht)** | ein Ladevorgang bei oder über dem Ziel endet | — |
| **Laden vor dem Ziel beendet, Kabel steckt noch** | ein Ladevorgang unter dem Ziel endet, während das Kabel noch steckt | — |
| **Vorklimatisierung gestartet** | das Auto beginnt, sich zu heizen oder zu kühlen | — |
| **Vorklimatisierung beendet** | das Auto hört auf, sich zu heizen oder zu kühlen | — |
| **Alarmanlage ausgelöst** | die Alarmanlage beginnt zu schlagen | — |
| **Neue Check-Control-Meldung** | eine Warnung erscheint, die das Auto vorher nicht zeigte | — |

**„Laden abgeschlossen“ ist der Auslöser für „Ziel erreicht“.** Das Auto hört bei
seinem Ladeziel auf, und dort endet der Ladevorgang. Für eine andere Schwelle,
etwa 70 % an einer Wallbox ohne eigene Grenze, nimm einen numerischen
Zustands-Auslöser auf der Entität für den Ladezustand.

**Angeboten werden nur die Auslöser, die dein Auto auslösen kann.** Ein Auto,
das nie einen Verriegelungszustand gesendet hat, bekommt kein *Geparkt und nicht
verriegelt*; die Zonen-Auslöser brauchen die Position des Autos und das
[Fahrtenbuch](DE-Feature-Trips) (standardmäßig an).

### Was sie absichtlich nicht tun

Diese Auslöser sollen genau einmal auslösen, und nie fälschlich:

- **Ein Neustart löst nichts aus.** Nach einem Neustart von Home Assistant
  beginnt eine Situation erst wieder, wenn das Auto sie neu meldet. Ein Auto, das
  verriegelt wurde, während Home Assistant aus war, meldet beim Start nie „nicht
  verriegelt“. Der Preis: Ein Auto, das schon vor dem Neustart offen stand, fällt
  erst bei seiner nächsten Meldung auf.
- **Aufsperren zum Einsteigen ist nicht „nicht verriegelt“.** Das Auto entriegelt
  bei jeder Ankunft und verriegelt sich etwa zwei Minuten später selbst, wenn
  keine Tür geöffnet wird. Dafür gibt es die Wartezeit; unter etwa drei Minuten
  löst der Auslöser jeden Tag aus.
- **Ein fertig geladenes Auto „lädt nicht“ nicht.** Am Ziel (oder bis 1 %
  darunter) ist ein angestecktes Auto, das aufgehört hat, fertig und nicht
  hängengeblieben.
- **Früh abstecken ist keine Unterbrechung.** *Laden vor dem Ziel beendet*
  braucht das Kabel noch fünf Minuten nach dem Stopp, also löst eine kurze Pause,
  die von selbst weiterläuft, oder ein Abstecken durch den Fahrer nicht aus.
- **Solar- und preisgesteuertes Laden pausiert absichtlich.** Wenn ein
  Laderegler (evcc, der Solarmodus einer Wallbox) das Laden stoppt und wieder
  startet, lösen *Angesteckt, lädt aber nicht* und *Laden vor dem Ziel beendet*
  auch bei diesen Pausen aus — genau das ist passiert. Füge eine Bedingung hinzu
  (zum Beispiel „erst nach 22:00“) oder eine längere Wartezeit.
- **Ankommen ist nicht die Grenze überqueren.** *In einer Zone angekommen* löst
  aus, wenn die Fahrt endet: Vorbeifahren am Zuhause löst es nicht aus, und
  GPS-Zittern am Zonenrand kann es nicht flackern lassen. Die eigenen
  Zonen-Auslöser von Home Assistant auf der Standort-Entität des Autos gibt es
  weiterhin, wenn du den Moment des Überquerens willst.
- **Ein offenes Fenster beim Fahren ist nicht „geparkt mit offenem Fenster“.** Die
  Situation beginnt, wenn die Fahrt vorbei ist. Ein halb offenes Fenster zählt:
  durch einen Spalt regnet es auch herein.
- **Ein alter Wert ist keine Neuigkeit.** Die Auslöser für Alarm und
  Vorklimatisierung lösen nur bei einer Änderung aus, die das Auto streamt, und
  nur solange der Zeitstempel des Autos darauf weniger als 10 Minuten alt ist.
  Weder der Abruf nach einem Neustart (BMW kann ihn aus seinem Zwischenspeicher
  beantworten) noch ein Auto, das einen alten Wert wiederholt, startet sie.
- **Die erste Check-Control-Liste setzt nur den Ausgangspunkt.** Warnungen, die
  das Auto schon zeigte, als du die Integration installiert hast, werden nicht
  gemeldet. Das Auto schickt diese Warnungen nur mit den Daten, die bei BMW
  abgerufen werden, nie über den Live-Stream: Eine neue erscheint beim nächsten
  Abruf (beim Start, oder wenn ein Dienst abruft), nicht in dem Moment, in dem
  sie im Auto erscheint.
- **Der Alarm ist an keinem echten Alarm geprüft.** Er beruht auf BMWs Alarm-Feld
  und ist an aufgezeichneten Daten getestet, aber noch kein Auto mit dieser
  Integration hat seinen Alarm ausgelöst. Wenn das bei dir passiert und nichts
  auslöst, eröffne bitte ein Issue.

### Was die Automation bekommt

Jeder Auslöser übergibt seine Details als `trigger.data`:

| Auslöser | `trigger.data` |
| --- | --- |
| In einer Zone angekommen | `zone`, `zone_entity_id`, `from`, `distance_km`, `duration_s`, `energy_kwh`, `soc`, `trip_id`, `trip_start` |
| Eine Zone verlassen | `zone`, `zone_entity_id`, `soc`, `trip_start` |
| Fahrt beendet | wie *In einer Zone angekommen* (`zone` und `zone_entity_id` sind außerhalb einer Zone `null`), dazu `trip_end`, `to`, `soc_start`, `classification` |
| Geparkt und nicht verriegelt | `since`, `zone`, `zone_entity_id` |
| Geparkt mit offenem Fenster | `since`, `windows` (welche: `front_driver`, `front_passenger`, `rear_driver`, `rear_passenger`, `sunroof`, `sunroof_tilt`, `rear_window`), `zone`, `zone_entity_id` |
| Angesteckt, lädt aber nicht | `since`, `soc`, `target_soc`, `zone`, `zone_entity_id` |
| Laden gestartet / abgeschlossen | `soc`, `target_soc`, `status` (abgeschlossen: auch `energy_kwh`, `cost`, `session_id`) |
| Laden vor dem Ziel beendet | wie abgeschlossen, dazu `reason` — BMWs eigene Begründung, wenn das Auto eine sendet |
| Vorklimatisierung gestartet / beendet | `activity` (`heating`, `cooling`, … oder `inactive`), `error` (BMWs Grund, wenn sie nicht laufen konnte, etwa `lowbattery`; sonst `ok`), `soc`, `zone`, `zone_entity_id` |
| Alarmanlage ausgelöst | `zone`, `zone_entity_id` |
| Neue Check-Control-Meldung | `id`, `title`, `text`, `message_type`, `message` (der ganze Eintrag, wie BMW ihn schickt), `zone`, `zone_entity_id` |

Jede Nutzlast enthält außerdem `vin`, `entry_id` und `vehicle`, den Namen des Autos,
wie BMW ihn meldet (`null`, solange BMW keinen meldet). Ein Beispiel, das vor einem
zu Hause unverriegelt abgestellten Auto warnt:

```yaml
triggers:
  - trigger: device
    domain: bavariandata
    device_id: <dein Auto>
    type: parked_unlocked
    for: { minutes: 10 }
conditions:
  - condition: template
    value_template: "{{ trigger.data.zone_entity_id == 'zone.home' }}"
actions:
  - action: notify.mobile_app_telefon
    data:
      message: "Das Auto steht seit 10 Minuten unverriegelt in der Einfahrt."
```

## Ereignisse

Alles hinter den Auslösern wird auch auf dem **Event-Bus** von Home Assistant
ausgelöst, für YAML-Automationen und Node-RED (beobachten kannst du sie unter
**Entwicklerwerkzeuge → Ereignisse**). Jedes enthält `vin`, `entry_id` und `vehicle` (den Namen des Autos).

| Ereignis | Wird ausgelöst, wenn | Daten |
| --- | --- | --- |
| `bavariandata_charging_started` | ein Ladevorgang beginnt | `soc`, `target_soc`, `status` |
| `bavariandata_charging_stopped` | ein Ladevorgang endet (aus jedem Grund) | `soc`, `target_soc`, `status`, `energy_kwh`, `cost`, `session_id`, `complete` (`true`, wenn er sein Ziel erreicht hat) |
| `bavariandata_charging_complete` | ein Ladevorgang **bei/über** dem Ziel-Ladezustand endet, auch einer, der während eines Neustarts von Home Assistant fertig wurde | wie `charging_stopped` |
| `bavariandata_charging_interrupted` | ein Ladevorgang unter dem Ziel endet, während das Kabel noch steckt | wie `charging_stopped`, dazu `reason` |
| `bavariandata_zone_arrived` | eine Fahrt in einer Zone endet | wie beim Auslöser *In einer Zone angekommen* |
| `bavariandata_zone_left` | eine Fahrt die Zone verlässt, in der sie begann | wie beim Auslöser *Eine Zone verlassen* |
| `bavariandata_trip_ended` | eine Fahrt eingetragen wird, in einer Zone oder nicht | wie beim Auslöser *Fahrt beendet* |
| `bavariandata_situation` | eine Situation beginnt (`active: true`) oder endet (`active: false`) | `situation` (`parked_unlocked`, `parked_window_open` oder `plugged_not_charging`), `active`, `since`, … |
| `bavariandata_preconditioning` | die Vorklimatisierung beginnt (`active: true`) oder endet (`active: false`) | `active`, dann wie bei den Auslösern *Vorklimatisierung* |
| `bavariandata_alarm` | der Alarm beginnt (`active: true`) oder hört auf (`active: false`) | `active`, `zone`, `zone_entity_id` |
| `bavariandata_check_control` | eine Check-Control-Meldung erscheint (`active: true`) oder ist weg (`active: false`) | `active`, dann wie beim Auslöser *Neue Check-Control-Meldung* |
| `bavariandata_vehicle_report` | der Kilometerstand hat einen neuen Wert und hat sich 5 Minuten nicht verändert | `timestamp` (die Zeit des Autos), `odometer_km`, `previous_odometer_km`, `previous_timestamp`, `distance_km`, `fuel_l`, `fuel_percent`, `range_km`, `soc_percent`, `latitude`, `longitude`, `altitude_m`, `heading` |

`bavariandata_situation` löst im Moment des Beginns aus, ohne Wartezeit — das
„für N Minuten“ fügen die Geräte-Auslöser hinzu. In YAML braucht derselbe Effekt
ein `wait_for_trigger` auf das passende Ereignis mit `active: false`.

`bavariandata_vehicle_report` ist für **Fahrtenbücher** gedacht, vor allem bei
älteren Autos, die ihre Daten beim Abstellen in einem Schwung senden und während
der Fahrt nichts. Es löst einmal pro neuem Kilometerstand aus, nachdem der Wert
5 Minuten gleich geblieben ist — dann ist der Schwung vollständig. Gut zu wissen:

- **Ein wiederholter Wert löst nichts aus.** Manche Autos wiederholen ihren
  unveränderten Kilometerstand alle paar Minuten; das ist keine Meldung. Ebenso
  wenig das Nachholen nach einem Neustart, das die letzten Werte des Autos
  zurückgibt.
- **Der erste Wert nach Installation oder Update legt nur den Startpunkt fest.**
  Das erste Ereignis kommt mit dem zweiten neuen Wert.
- **Bei einem Auto, das während der Fahrt streamt,** löst es am Ende der Fahrt
  aus — und ein zweites Mal, wenn das Auto zwischendurch 5 Minuten oder länger
  stand, etwa in einem langen Stau. Jedes Ereignis bringt sein eigenes
  `distance_km` mit, die Strecken gehen also trotzdem auf.
- **Ein Wert, den das Auto nicht gefahren sein kann, wird nicht gemeldet.** Einer,
  der rückwärts läuft oder weiter voraus liegt, als 250 km/h seit der letzten
  Meldung zulassen, wird beiseitegelegt. Schließt der nächste Wert daran an, war
  der frühere falsch, und die Meldungen gehen von dort aus weiter. So oder so
  fehlt womöglich eine Fahrt — eine erfundene gibt es nie.
- **Der Kilometerstand zählt ganze Kilometer**, eine Fahrt unter einem Kilometer
  ändert ihn womöglich nicht und löst dann nichts aus.
- Felder, die das Auto nicht sendet, sind `null`. Die Position kommt mit dem
  Ereignis, wie beim Device Tracker.

## Blueprints für den Einstieg

Acht Automationen für den Einstieg liegen der Integration bei. Importiere sie
über **Einstellungen → Automationen & Szenen → Blueprints → Blueprint
importieren** mit der Raw-URL von GitHub:

- **Laden beim Ziel-% stoppen** —
  [`stop_charge_at_target.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/stop_charge_at_target.yaml).
  Schaltet eine Wallbox oder smarte Steckdose ab, sobald ein
  Ladezustands-Sensor dein Ziel erreicht. CarData ist **nur lesend**, daher
  steuert es einen Schalter, den du schon hast — das Auto selbst kann es nicht
  steuern.
- **Benachrichtigen, wenn das Laden fertig ist** —
  [`notify_charging_complete.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/notify_charging_complete.yaml).
  Sendet eine Nachricht, wenn ein Ladevorgang endet: nur wenn das Auto sein Ziel
  erreicht hat, oder bei jedem Ende. Der Titel nennt das Auto, und **Cars**
  beschränkt sie auf die gewählten Autos (leer: alle). Zum Übernehmen des
  Autofilters neu importieren. Bis v0.9.20 kamen bei einem Ladevorgang bis zum
  Ziel zwei Nachrichten, wenn *Only when target reached* aus war.
- **Ladekabel in einen Schalter spiegeln** —
  [`plug_to_switch.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/plug_to_switch.yaml).
  Schaltet einen Schalter ein, wenn zu Hause das Kabel eingesteckt wird, und
  aus, wenn es abgezogen wird. Gedacht für Ladeplaner, die wissen müssen, dass
  das Auto angesteckt ist, etwa den Schalter *EV connected* von EV Smart
  Charging; siehe [Intelligent laden](DE-Feature-Smart-Charging). Einstecken
  außerhalb von zu Hause bewirkt nichts; ein Auto ohne Position gilt als zu
  Hause.
- **Auto kommt gleich heim** —
  [`arriving_home.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/arriving_home.yaml).
  Führt deine Aktionen einmal pro Fahrt aus, sobald das Auto näher an zu Hause
  ist als die eingestellte Schwelle: Garage öffnen, Haus aufheizen. Es braucht
  einen Wert, der fällt, während das Auto heimkommt, mit dem Standort des Autos
  als Start. Entweder:
  - **Waze Travel Time** oder **Google Maps Travel Time**: Minuten bis nach
    Hause, mit Verkehr. Der Blueprint aktualisiert den Wert bei jeder neuen
    Position.
  - **Proximity** (in Home Assistant eingebaut, ohne Konto): die Entfernung
    nach Hause. Die Schwelle dann in km (mi, wenn es Fuß anzeigt), auch
    wenn Proximity Meter anzeigt: Der Blueprint rechnet um.

  Gut zu wissen:
  - **Es weiß nicht, wohin du fährst.** Es sieht nur, dass das Auto näher
    kommt. Eine Fahrt, die nah an zu Hause vorbei und weiter führt, löst es
    ebenfalls aus. Wähle die Zonen, in denen deine Heimfahrten beginnen
    (*Only trips that start in*, z. B. Arbeit), um nur diese zu zählen.
  - **Eine kurze Fahrt löst es womöglich nicht aus.** Wer nie weiter weg war
    als die Schwelle, unterschreitet sie nie; das Losfahren von zu Hause löst
    es ebenfalls nie aus.
  - **Rechne damit, dass es etwas nach der Schwelle kommt.** BMW sendet die
    Position während der Fahrt etwa alle ein bis drei Minuten.
  - Die Navigation des Autos hat ein Feld *Zeit bis zum Navigationsziel*. Ein
    Ziel, das du am Handy über CarPlay oder Android Auto eingibst, kennt das
    Auto nicht, deshalb verlässt sich der Blueprint nicht darauf.
- **Regen kommt, ein Fenster ist offen** —
  [`rain_window_open.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/rain_window_open.yaml).
  Steht ein Fenster, das Schiebedach oder die Heckscheibe 10 Minuten offen,
  und danach jede Stunde, prüft es deine Wettervorhersage. Regen innerhalb der
  eingestellten Stunden (nach Wahrscheinlichkeit, wo die Vorhersage eine
  liefert, sonst jede Regenmenge oder ein Regenzustand) führt deine Aktionen
  aus, die offenen stehen in `open_names`. Danach bleibt es still, bis alles
  zu ist.
- **Frost heute Nacht, das Auto hängt nicht am Kabel** —
  [`frost_unplugged.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/frost_unplugged.yaml).
  Prüft zur eingestellten Zeit (standardmäßig 20:00) den Tiefstwert bis 09:00
  am nächsten Morgen. Liegt er bei oder unter deiner Frostgrenze, steht das Auto
  zu Hause und steckt das Kabel nicht, führt es deine Aktionen aus, mit dem
  Tiefstwert in `low`. Eingesteckt kann ein Elektroauto Akku und Innenraum vor
  der Abfahrt aus dem Netz wärmen.
- **Ein Reifen verliert Luft** —
  [`tire_pressure_imbalance.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/tire_pressure_imbalance.yaml).
  Vergleicht die beiden Reifen jeder Achse. Wetter und die Wärme der Fahrt
  verändern beide gleich, ein Unterschied, der eine Stunde lang bleibt
  (standardmäßig 30 kPa, 0,3 bar), heißt also: Einer verliert Luft, meist
  deutlich vor der Warnung des Autos. Den Reifen nennt `low_tire`; erneut
  meldet es sich erst, wenn der Unterschied wieder verschwunden ist. Gesunde
  Reifen liegen höchstens etwa 20 kPa auseinander.
- **Auto über Nacht offen** —
  [`car_left_open.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/car_left_open.yaml).
  Prüft zur eingestellten Zeit (standardmäßig 22:00), ob das Auto verriegelt
  ist und Türen, Fenster, Schiebedach, Heckklappe und Motorhaube zu sind. Ist
  etwas offen, führt es deine Aktionen einmal aus, mit dem Offenen oder
  Unverriegelten in `open_names`. Du wählst nur das Auto (oder mehrere): Die
  Sensoren für Verriegelung, Türen, Fenster und Klappen findet es selbst und
  prüft die, die dein Auto sendet. Ladeklappe und Kabelverriegelung bleiben
  außen vor, denn die sind in jeder Nacht offen, in der das Auto lädt. Soll es
  aus deiner eigenen Gute-Nacht-Routine heraus prüfen, starte die Automation
  von dort.
  Verriegeln lässt sich das Auto aus Home Assistant nicht (CarData liest nur);
  das geht in der My-BMW-App.

## Assist nach dem Auto fragen

Mit einem KI-Gesprächsagenten hinter Assist (OpenAI, Google, Anthropic oder
Ollama) beantwortet Assist Fragen wie *„Muss ich laden, bevor ich nach München
und zurück fahre?“*, *„Ist das Auto abgeschlossen?“*, *„Was hat das Laden diesen
Monat gekostet?“* oder *„Liege ich beim Leasing im Plan?“*. BavarianData bringt
dafür eigene Werkzeuge mit. Sie lesen dasselbe wie die Karte und kosten
**kein API-Kontingent**.

Einmal einschalten: deine KI-Integration unter **Einstellungen → Geräte &
Dienste** öffnen, ihren Gesprächsagenten **konfigurieren** und unter
*Home Assistant steuern* neben *Assist* auch **BavarianData** ankreuzen.
Freigeben musst du nichts, Skripte braucht es keine. Die Integration *Model
Context Protocol Server* von Home Assistant bietet dieselbe Auswahl, damit
können auch KI-Apps am Rechner fragen.

Die Werkzeuge:

| Werkzeug | Beantwortet |
|---|---|
| Fahrzeugstatus | Akku, Laden (Leistung, Minuten bis zum Ziel), Reichweite, die Zone, in der es steht, ob es gerade fährt, alles, was offen oder unverriegelt ist |
| Reichweitencheck | Ob eine Fahrt in die Restreichweite passt, einfach oder hin und zurück, mit Reserve (ein Zehntel der Reichweite eines vollen Akkus, mindestens 20 km), und wie viel mehr Ladung sie sonst braucht. Die Strecke schätzt der Agent aus den Orten, die du nennst. |
| Ladeverlauf | Die Ladevorgänge der letzten Tage (30, wenn du nichts anderes sagst): wann, wo, Akku vorher und nachher, Energie und Kosten, mit Summen |
| Fahrmonat | Strecke, Fahrten, die Aufteilung dienstlich/privat/Arbeitsweg, Verbrauch, Kosten und die häufigsten Ziele, im Vergleich zum Vormonat |
| Kilometerstand | Tachostand, dieser Monat und dieses Jahr, die Hochrechnung aufs Jahr und dein Leasing- oder Jahreskontingent, falls eingestellt |
| Effizienz | Gemessener Verbrauch, die echte Reichweite daraus, der Vergleich mit dem Wert des Autos und der Verlauf nach Monaten |

Die Reichweite ist die [gemessene](DE-Feature-Efficiency-and-Range), wo es
eine gibt, sonst die des Autos. Antworten kommen in der Entfernungseinheit
deines Autos. Mit mehreren Autos fragt der Agent eines beim Namen ab oder alle.

**Was die KI sieht.** Die Werte oben und die Namen von Orten (deine Zonen,
oder Adressen bei Fahrten, die woanders endeten). Nie die FIN, die Position
des Autos, Fahrt-IDs oder Ladekurven: Die werden aus jeder Antwort entfernt.
Am Auto können die Werkzeuge nichts tun; CarData liest nur. Mit einem
Cloud-Agenten verlassen diese Werte dein Zuhause; ein lokaler Agent wie Ollama
behält sie dort.

*Die drei `car_…`-Skripte aus einer früheren Fassung dieser Seite eingerichtet?*
Du kannst sie löschen; die Werkzeuge beantworten alles, was sie konnten.

## Nicht vergessen: nur lesend

CarData kann keine Befehle senden, daher kann keine Automation auf Basis dieser
Integration das Auto verriegeln, vorklimatisieren oder anderweitig steuern.
Automationen steuern **externe** Geräte (Wallboxen, Steckdosen,
Benachrichtigungen) als Reaktion auf die Daten des Autos.
