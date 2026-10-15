# Ereignisse & Automations-Blueprints

> 🇬🇧 [English version](Feature-Automations)

BavarianData streamt Daten in Echtzeit und eignet sich daher für Automationen,
die sofort auf eine Änderung reagieren statt auf einen Abfragezyklus zu warten.

## Geräte-Auslöser

Der einfachste Weg: Automation anlegen, als Auslöser **Gerät** wählen, dein Auto
auswählen und aus der Liste wählen. Kein YAML, keine Entitätsnamen.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/automation-device-triggers.png" alt="Der Geräte-Auslöser im Automations-Editor mit gewähltem Auto und offener Auslöser-Liste: Arrived at a zone, Charging completed (target reached), Charging started, Charging stopped before the target" width="360" />
</p>

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

Sieben Automationen für den Einstieg liegen der Integration bei. Importiere sie
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
    nach Hause. Die Schwelle dann in seiner Einheit (km oder mi).

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

## Assist nach dem Auto fragen

Mit einem KI-Gesprächsagenten hinter Assist (OpenAI, Google, Anthropic oder
Ollama) beantwortet Assist Fragen wie *„Wie weit bin ich letzten Monat
gefahren?“*, *„Was hat das Laden diese Woche gekostet?“* oder *„Liege ich beim
Leasing im Plan?“* aus BavarianDatas eigenem Verlauf. Dafür braucht es drei
Skripte, die die Antworten übergeben. Sie lesen den lokalen Speicher und kosten
**kein API-Kontingent**.

1. Die Skripte unten in `scripts.yaml` einfügen (oder jedes unter
   *Einstellungen → Automationen & Szenen → Skripte* anlegen, *In YAML
   bearbeiten*), dann die Skripte neu laden.
2. Für Assist freigeben: *Einstellungen → Sprachassistenten → Freigeben*, die
   drei Skripte hinzufügen.
3. Beim Gesprächsagenten *Home Assistant steuern* einschalten, falls es aus ist.

Die Beschreibungen in den Skripten sind englisch; die KI versteht sie trotzdem
und antwortet auf Deutsch.

```yaml
car_driving_month:
  alias: Car driving month
  description: >-
    How far the car was driven in a month: distance, number of trips, the
    private/business/commute split, average consumption and the estimated
    driving cost. Use it for questions about distance, trips, consumption or
    what driving cost.
  fields:
    month:
      description: The month as YYYY-MM. Leave empty for the current month.
      example: "2026-09"
      selector:
        text:
  sequence:
    - action: bavariandata.get_driving_summary
      data:
        month: "{{ month if month | default('') | trim else now().strftime('%Y-%m') }}"
      response_variable: result
    - variables:
        # Named fields only: the trip ids in the raw answer hold the VIN.
        answer: >
          {% set s = result.summary %}
          {% set best = s.best_trip or {} %}
          {% set worst = s.worst_trip or {} %}
          {{ {'month': result.month,
              'total_km': s.total_km, 'trip_count': s.trip_count,
              'avg_trip_km': s.avg_trip_km, 'longest_trip_km': s.longest_trip_km,
              'split': s.split,
              'avg_consumption_kwh_per_100km': s.avg_consumption_kwh_per_100km,
              'energy_balance': s.energy_balance,
              'top_destinations': s.top_destinations,
              'previous_month_km': s.prev_total_km,
              'change_vs_previous_month_percent': s.mom_delta_percent,
              'estimated_cost': s.estimated_cost,
              'most_efficient_trip': {'to': best.get('label'),
                                      'kwh_per_100km': best.get('consumption'),
                                      'km': best.get('distance_km')},
              'least_efficient_trip': {'to': worst.get('label'),
                                       'kwh_per_100km': worst.get('consumption'),
                                       'km': worst.get('distance_km')}} }}
    - stop: Done
      response_variable: answer

car_charging_history:
  alias: Car charging history
  description: >-
    The car's charging sessions over the last days: when and where, state of
    charge before and after, energy and cost. Use it for questions about
    charging, charging cost or energy.
  fields:
    days:
      description: How many days back to look. 30 if not given.
      example: 30
      selector:
        number:
          min: 1
          max: 365
  sequence:
    - action: bavariandata.get_charging_sessions
      data:
        from: "{{ (now() - timedelta(days=days | default(30) | int)).date() | string }}"
      response_variable: result
    - variables:
        answer: >
          {% set ns = namespace(sessions=[]) %}
          {% for s in result.sessions %}
            {% set ns.sessions = ns.sessions + [{
              'start': s.start, 'end': s.end,
              'place': (s.location or {}).get('zone'),
              'soc_before': s.soc_start, 'soc_after': s.soc_end,
              'kwh_from_grid': s.grid_kwh, 'kwh_into_battery': s.energy_kwh,
              'cost': (s.cost or {}).get('amount'),
              'currency': (s.cost or {}).get('currency')}] %}
          {% endfor %}
          {{ {'sessions': ns.sessions} }}
    - stop: Done
      response_variable: answer

car_mileage:
  alias: Car mileage
  description: >-
    The car's odometer, the distance driven this month and this year, the
    last twelve months, its pace in km per day and, if one is set, where it
    stands against its lease or yearly mileage allowance.
  sequence:
    - action: bavariandata.get_mileage_report
      data: {}
      response_variable: result
    - variables:
        answer: >
          {{ {'odometer_km': result.odometer_km,
              'this_month_km': result.this_month.km,
              'this_year_km': result.this_year.km,
              'km_per_day': result.km_per_day,
              'projected_year_km': result.projected_year_km,
              'months': result.months,
              'allowance': result.allowance} }}
    - stop: Done
      response_variable: answer
```

**Was die KI sieht.** Genau das, was diese Skripte zurückgeben: Strecken,
Verbrauch, Kosten, Ladezeiten und die Namen von Orten (deine Zonen, oder
Adressen bei Fahrten, die woanders endeten). Sonst nichts, auch keine FIN: Die
Skripte nennen jedes Feld einzeln, weil die rohen Antworten der Dienste die FIN
in ihren IDs tragen. Mit einem Cloud-Agenten verlassen diese Werte dein
Zuhause; ein lokaler Agent wie Ollama behält sie dort.

Mit mehreren Autos (oder Konten) unter `data:` jedes Skripts `vin:` (und
`entry_id:`) ergänzen und jedem Auto eigene drei Skripte geben, nach dem Auto
benannt.

## Nicht vergessen: nur lesend

CarData kann keine Befehle senden, daher kann keine Automation auf Basis dieser
Integration das Auto verriegeln, vorklimatisieren oder anderweitig steuern.
Automationen steuern **externe** Geräte (Wallboxen, Steckdosen,
Benachrichtigungen) als Reaktion auf die Daten des Autos.
