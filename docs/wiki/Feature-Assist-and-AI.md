# Ask Assist & AI about your car

> 🇩🇪 [Deutsch](DE-Feature-Assist-and-AI)

Ask Home Assistant's voice assistant about your BMW or MINI in plain words.
With an AI conversation agent behind Assist (OpenAI, Google, Anthropic or
Ollama), Assist can answer questions such as:

- *"Do I need to charge before driving to Munich and back?"*
- *"Is the car locked?"* / *"Did I leave a window open?"*
- *"What did charging cost this month?"*
- *"How far did I drive last month, and how much of it was business?"*
- *"Am I on track with my lease?"*
- *"What's my real range right now?"*

BavarianData brings its own set of tools for this, an *LLM API* in Home
Assistant's terms. They read what the card reads, so they cost **no API
quota**, and they work by voice, in the Assist chat and from desktop AI apps.

## Turn it on

Turn the tools on once: open your AI integration under **Settings → Devices &
services**, then **Configure** its conversation agent, and tick
**BavarianData** under *Control Home Assistant*, next to *Assist*. You don't
have to expose anything or write scripts.

### Desktop AI apps (MCP)

Home Assistant's *Model Context Protocol Server* integration lets you make the
same choice. Tick **BavarianData** there too, and an AI app that speaks MCP
(such as Claude Desktop) can ask about the car through your Home Assistant.

## The tools

| Tool | Answers |
|---|---|
| Car status | Battery, charging (power, minutes to the target), range (on a plug-in hybrid: electric, fuel and total), fuel level, the zone it is in, whether it is being driven, anything open or unlocked |
| Range check | Whether a drive fits the range left, one way or there and back, with a safety margin (a tenth of a full battery's range, at least 20 km): the agent tells you the range left after the drive, and how much more charge it needs if it doesn't fit. A plug-in hybrid or fuel car counts its tank too (margin 20 km), and a hybrid also says whether the battery alone does it. The agent estimates the distance from the places you name. |
| Charging history | The sessions of the last days (30 unless you say otherwise): when, where, battery before and after, energy and cost, with totals |
| Driving month | Distance, trips, the business/private/commute split, consumption, cost and the most visited places, against the month before |
| Mileage | Odometer, this month and year, the yearly projection and your lease or yearly allowance, if one is set |
| Efficiency | Measured consumption, the real range it gives, how it compares with the car's own figure, and the monthly trend |

Where there is a [measured range](Feature-Efficiency-and-Range), the tools use
it; otherwise they use the car's own. A plug-in hybrid's fuel range is its total
range minus its electric range, because BMW sends no fuel range of its own.
Answers come in your car's distance unit (the
[Units setting](Settings-Reference#units)). With several cars, you can ask
about one by name or about all of them.

## What the AI sees

The AI sees the figures above and place names (your zones, or the address where
a trip ended outside them). It never sees the VIN, the car's position, trip ids
or charging curves; every answer has them removed. The tools can't do anything
to the car, because CarData is read-only: the AI can't lock it, start the
climate or start charging. With a cloud agent, these figures leave your home. A
local agent such as Ollama keeps them there.

*Did you set up the three `car_…` scripts from an earlier version of the
[Events & blueprints](Feature-Automations) page?* You can delete them. The tools
answer everything they did.
