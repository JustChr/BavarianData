# Smart charging with other integrations

> 🇩🇪 [Deutsch](DE-Feature-Smart-Charging)

Charge when power is cheap or the sun is out, and stop at the level you need.
BavarianData doesn't plan charging itself: dedicated planners do that well. It
gives them what they can't get from BMW reliably, the car's **state of charge,
its target and whether the cable is in**, live off the stream and at no
[API cost](Feature-API-Quota).

CarData is read-only, and that doesn't get in the way here. A planner never
commands the car. It switches your **wallbox** (or a smart plug), and the car
charges whenever the wallbox gives it power.

---

## Which entities to hand over

| A planner asks for | Use | Why |
| --- | --- | --- |
| State of charge (%) | **State Of Charge (Predicted on Integration side)** | While charging, BMW can go quiet for an hour or more, so its own figure stands still. This one keeps counting, from the wallbox meter once you have [bound one](Feature-evcc-and-Wallbox-Bridge#inbound-your-wallboxs-meter-for-the-charging-history). A planner that stops at a target stops on time. When parked it equals the car's own reading. |
| Target state of charge (%) | **Battery EV Target state of charge** | The limit set in the car or the My BMW app. |
| Car connected | **Charging Port plug state** (`connected` / `disconnected`) | Streamed as the cable goes in or out, no polling. |
| Battery size (kWh), if asked | **Range EV Battery energy max**, or the usable capacity from your car's data sheet | Only needed to turn percentages into kWh. |

On a few iX3 cars the target briefly jumps to 100 % next to the real value
([#52](https://github.com/JustChr/BavarianData/issues/52)). If your planner
then charges to full, give it a fixed target instead: most planners have their
own target setting, or point it at an `input_number`.

With more than one car, every car has its own set of these entities; set up
one planner per car where the planner supports it.

---

## EV Smart Charging

[EV Smart Charging](https://github.com/jonasbkarlsson/ev_smart_charging) (HACS)
picks the cheapest hours from Nord Pool, Entso-e, Energi Data Service and
others, and switches your charger in them.

1. Install it from HACS and add it under *Settings → Devices & services*.
2. **EV SOC entity:** the car's *State Of Charge (Predicted on Integration
   side)*.
3. **EV Target SOC entity:** the car's *Battery EV Target state of charge*.
4. **Charger control entity:** your wallbox's charge switch (or smart plug).
5. Leave **An EV integration will control start/stop of charging** off:
   BavarianData can't, the wallbox does.

EV Smart Charging plans only while its **EV connected** switch is on. Turn it
with the cable using the blueprint
[`plug_to_switch.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/plug_to_switch.yaml)
(see [Events & blueprints](Feature-Automations#starter-blueprints)): pick the
car's *Charging Port plug state*, its location and the *EV connected* switch.
Plugging in away from home does nothing, so a public charger never starts your
home schedule.

---

## Predbat

[Predbat](https://springfall2008.github.io/batpred/car-charging/) plans a home
battery and the car together. Its car settings live in `apps.yaml`:

```yaml
car_charging_soc:
  - sensor.<car>_state_of_charge_predicted_on_integration_side
car_charging_limit:
  - sensor.<car>_battery_ev_target_state_of_charge
car_charging_planned:
  - sensor.<car>_charging_port_plug_state
car_charging_battery_size:
  - 81.2
```

Use your own entity IDs (*Settings → Entities*, search for the names above);
they depend on your car's name and Home Assistant's language. Predbat's default
answers for *planned* already include `connected`, so the cable sensor works
without a blueprint. Predbat then switches the charger through its own
settings, or through an automation on `binary_sensor.predbat_car_charging_slot`.

---

## evcc

evcc can read the car's state of charge straight from BavarianData over MQTT.
That has its own page: [evcc & wallbox bridge](Feature-evcc-and-Wallbox-Bridge).
