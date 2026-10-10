# Ereignisse & Automations-Blueprints

> 🇬🇧 [English version](Feature-Automations)

BavarianData streamt Daten in Echtzeit und eignet sich daher für Automationen,
die sofort auf eine Änderung reagieren statt auf einen Abfragezyklus zu warten.

## Geräte-Auslöser

Der einfachste Weg: Automation anlegen, als Auslöser **Gerät** wählen, dein Auto
auswählen und aus der Liste wählen. Kein YAML, keine Entitätsnamen.

| Auslöser | Löst aus, wenn | Optionen |
| --- | --- | --- |
| **In einer Zone angekommen** | eine Fahrt in einer Zone endet, sobald das Auto steht (meist 1–3 Minuten nach dem Anhalten) | eine Zone, oder leer für jede Zone |
| **Eine Zone verlassen** | eine Fahrt das Auto aus der Zone bringt, in der sie begann | eine Zone, oder leer für jede Zone |
| **Geparkt und nicht verriegelt** | das Auto die gewählte Zeit unverriegelt steht | wie lange (Standard 10 Minuten) |
| **Angesteckt, lädt aber nicht** | ein Kabel steckt, nichts lädt und das Auto unter seinem Ladeziel ist, für die gewählte Zeit | wie lange (Standard 15 Minuten) |
| **Laden gestartet** | ein Ladevorgang beginnt | — |
| **Laden abgeschlossen (Ziel erreicht)** | ein Ladevorgang bei oder über dem Ziel endet | — |
| **Laden vor dem Ziel beendet, Kabel steckt noch** | ein Ladevorgang unter dem Ziel endet, während das Kabel noch steckt | — |

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
  braucht das Kabel noch zwei Minuten nach dem Stopp, also löst eine kurze Pause,
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

### Was die Automation bekommt

Jeder Auslöser übergibt seine Details als `trigger.data`:

| Auslöser | `trigger.data` |
| --- | --- |
| In einer Zone angekommen | `zone`, `zone_entity_id`, `from`, `distance_km`, `duration_s`, `energy_kwh`, `soc`, `trip_id`, `trip_start` |
| Eine Zone verlassen | `zone`, `zone_entity_id`, `soc`, `trip_start` |
| Geparkt und nicht verriegelt | `since`, `zone`, `zone_entity_id` |
| Angesteckt, lädt aber nicht | `since`, `soc`, `target_soc`, `zone`, `zone_entity_id` |
| Laden gestartet / abgeschlossen | `soc`, `target_soc`, `status` (abgeschlossen: auch `energy_kwh`, `cost`, `session_id`) |
| Laden vor dem Ziel beendet | wie abgeschlossen, dazu `reason` — BMWs eigene Begründung, wenn das Auto eine sendet |

Jede Nutzlast enthält außerdem `vin` und `entry_id`. Ein Beispiel, das vor einem
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
**Entwicklerwerkzeuge → Ereignisse**). Jedes enthält `vin` und `entry_id`.

| Ereignis | Wird ausgelöst, wenn | Daten |
| --- | --- | --- |
| `bavariandata_charging_started` | ein Ladevorgang beginnt | `soc`, `target_soc`, `status` |
| `bavariandata_charging_stopped` | ein Ladevorgang endet (aus jedem Grund) | `soc`, `target_soc`, `status`, `energy_kwh`, `cost`, `session_id` |
| `bavariandata_charging_complete` | ein Ladevorgang **bei/über** dem Ziel-Ladezustand endet | wie `charging_stopped` |
| `bavariandata_charging_interrupted` | ein Ladevorgang unter dem Ziel endet, während das Kabel noch steckt | wie `charging_stopped`, dazu `reason` |
| `bavariandata_zone_arrived` | eine Fahrt in einer Zone endet | wie beim Auslöser *In einer Zone angekommen* |
| `bavariandata_zone_left` | eine Fahrt die Zone verlässt, in der sie begann | wie beim Auslöser *Eine Zone verlassen* |
| `bavariandata_situation` | eine Situation beginnt (`active: true`) oder endet (`active: false`) | `situation` (`parked_unlocked` oder `plugged_not_charging`), `active`, `since`, … |
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

Zwei Automationen für den Einstieg liegen der Integration bei. Importiere sie
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
  Sendet bei den obigen Ereignissen „complete“ bzw. „stopped“ eine Nachricht über
  einen Benachrichtigungsdienst.

## Nicht vergessen: nur lesend

CarData kann keine Befehle senden, daher kann keine Automation auf Basis dieser
Integration das Auto verriegeln, vorklimatisieren oder anderweitig steuern.
Automationen steuern **externe** Geräte (Wallboxen, Steckdosen,
Benachrichtigungen) als Reaktion auf die Daten des Autos.
