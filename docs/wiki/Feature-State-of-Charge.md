# State of charge while charging: why BMW's goes quiet, and the estimate that doesn't

> 🇩🇪 [Deutsch](DE-Feature-State-of-Charge)

If you watch a BMW charge in Home Assistant, the battery level can stand still
for an hour or more and then jump. That is not Home Assistant: **BMW CarData
often stops streaming the state of charge (SoC) while the car charges**. A BMW
i5 can charge for hours without sending a single new percentage, then report the
result when it stops. The figure the MyBMW app shows during a charge is BMW's
server-side *prediction*, and CarData offers it only over REST, which is capped at
[50 requests a day](Feature-API-Quota) and never updates on the stream.

BavarianData fills the gap with its own estimate, and uses it everywhere the
charge level matters.

## How the estimate works

- **Parked:** it is simply BMW's last measured reading.
- **Charging:** it keeps climbing between BMW's readings at the rate the
  charging power implies, and stops at the car's charge target.
- **With a wallbox meter at home:** once the meter has seen three home charges,
  the meter moves the estimate instead, by exactly the energy it counted. How
  many percentage points a kilowatt-hour is worth, and what the car draws for
  itself while charging, are learned from your own last ten home charges. On the
  maintainer's i5, that predicted an 11 kW charge from 8 to 80 % to within 0.8
  points. [The details](Feature-evcc-and-Wallbox-Bridge#it-also-drives-the-state-of-charge-estimate).
- **Every real reading wins.** When BMW sends a new percentage, the estimate
  takes it over, so it can never drift for long.

## Where you see it

| Entity | What it is |
| --- | --- |
| **State Of Charge (Predicted on Integration side)** | The estimate. The card's ring, the [evcc and MQTT bridge](Feature-evcc-and-Wallbox-Bridge), [ABRP](Feature-ABRP), [smart charging](Feature-Smart-Charging) and [Assist](Feature-Assist-and-AI) all use this one. |
| **Predicted charge speed** | How fast the estimate is climbing, in % per hour. |
| **Predicted time to charge target** | Minutes until a **home** charge reaches the target, from the wallbox meter. Away from home, the card shows BMW's own figure instead. |
| **Battery HV State Of Charge** | BMW's measured reading, as streamed. It stands still while BMW is quiet. |

Use the estimate for automations, such as "notify me at 80 %" or "stop the
wallbox at the target": it moves when the real battery does. The measured
reading is there when you need BMW's own number. Every battery-level entity, and
which one to pick, is listed under
[which state-of-charge entity to use](Feature-Entities-and-Devices#which-state-of-charge-entity-to-use).

## Questions

### The battery level doesn't update while charging

Check which entity you're looking at. *Battery HV State Of Charge* is BMW's
measurement and stands still while BMW is quiet. *State Of Charge (Predicted on
Integration side)* keeps counting. If neither has a value at all, the
state-of-charge field isn't switched on in your Data Selection. See
[no start/end SoC on a session](Feature-Charging-History-and-Cost#no-startend-soc-on-a-session).

### The estimate jumped when BMW reported

Then BMW's reading differed from the estimate, and the reading wins. Without a
wallbox meter, the estimate follows BMW's charging power, which arrives in bursts
and can be old on a solar-following or interrupted charge. Binding a
[wallbox meter](Feature-evcc-and-Wallbox-Bridge#inbound-your-wallboxs-meter-for-the-charging-history)
makes those jumps small.

### Does it work at a public charger?

Yes, from BMW's charging power. The wallbox meter only helps at home, and the
time to the target is BavarianData's own only there. At a DC fast charger, the
card shows BMW's time to full.

### Does it cost API quota?

No. The estimate is worked out in Home Assistant from the stream.
