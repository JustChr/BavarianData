# Events & automation blueprints

BavarianData streams data in real time, so it's built for automations that react
the instant something changes rather than on a polling cycle.

## Charging events

Meaningful charging transitions fire on the Home Assistant **event bus**, ready
to use as automation triggers (watch them under **Developer Tools → Events**):

| Event | Fires when | Data |
| --- | --- | --- |
| `bavariandata_charging_started` | a session begins | `vin`, `soc`, `target_soc`, `status` |
| `bavariandata_charging_stopped` | a session ends (any reason) | `vin`, `soc`, `target_soc`, `status` |
| `bavariandata_charging_complete` | a session ends **at/above** the target SoC | `vin`, `soc`, `target_soc`, `status` |

## Starter blueprints

Two starter automations ship with the integration. Import them via **Settings →
Automations → Blueprints → Import Blueprint** with the raw GitHub URL:

- **Stop charging at target %** —
  [`stop_charge_at_target.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/stop_charge_at_target.yaml).
  Switches off a wallbox / smart-plug the moment a SoC sensor reaches your
  target. CarData is **read-only**, so it drives an external switch you already
  have — it can't command the car directly.
- **Notify when charging completes** —
  [`notify_charging_complete.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/notify_charging_complete.yaml).
  Pings a notify service on the charging-complete / -stopped events above.

## Remember: read-only

CarData cannot send commands, so no automation built on this integration can
lock, precondition, or otherwise control the car. Automations act on **external**
devices (wallboxes, plugs, notifiers) in response to the car's data.
