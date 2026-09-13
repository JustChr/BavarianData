# Ereignisse & Automations-Blueprints

> 🇬🇧 [English version](Feature-Automations)

BavarianData streamt Daten in Echtzeit und eignet sich daher für Automationen,
die sofort auf eine Änderung reagieren statt auf einen Abfragezyklus zu warten.

## Lade-Ereignisse

Wichtige Übergänge beim Laden werden auf dem **Event-Bus** von Home Assistant
ausgelöst und lassen sich direkt als Auslöser von Automationen nutzen (beobachten
kannst du sie unter **Entwicklerwerkzeuge → Ereignisse**):

| Ereignis | Wird ausgelöst, wenn | Daten |
| --- | --- | --- |
| `bavariandata_charging_started` | ein Ladevorgang beginnt | `vin`, `soc`, `target_soc`, `status` |
| `bavariandata_charging_stopped` | ein Ladevorgang endet (aus jedem Grund) | `vin`, `soc`, `target_soc`, `status` |
| `bavariandata_charging_complete` | ein Ladevorgang **bei/über** dem Ziel-Ladezustand endet | `vin`, `soc`, `target_soc`, `status` |

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
