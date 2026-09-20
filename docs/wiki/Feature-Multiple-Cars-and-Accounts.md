# Multiple cars & accounts

> 🇩🇪 [Deutsch](DE-Feature-Multiple-Cars-and-Accounts)

BavarianData was built around one car in one BMW account, and everything since
has been about making the other shapes work just as well. This page is the whole
picture: what one config entry covers, what changes when a second car or a
second account appears, and the few places you still have to say which car you
mean.

## One entry per account, not per car

A config entry is **one BMW account**: one Client ID, one OAuth login, one MQTT
stream, one 50-request daily quota — and **every car on that account**. Each VIN
becomes its own [device](Feature-Entities-and-Devices) with its own entities,
history, charging ledger and tyre record.

So:

- **Two cars on one account** → add the integration **once**. Both cars appear.
- **Two accounts** (yours and a partner's, or a BMW and a MINI account) → add
  the integration **twice**, once per Client ID.
- **One account, added twice** is refused. Since **v0.9.11-beta.4** setup stops
  with *"This BMW account is already set up"* if another entry already has the
  same account, even when you generated a second Client ID for it in the portal.
  It is not a limitation we invented: BMW allows **one stream connection per
  account**, so two entries would disconnect each other in a loop, and the
  50-request quota is counted per account, not per Client ID.

A car can only be in one account, so no VIN is ever served by two entries.

## Adding a car later

Buy a second BMW, or enrol an existing one in CarData after setup, and it simply
turns up: it rides the same stream, so its entities appear on their own within
minutes of the car sending anything.

Two things used to be missed and are handled from **v0.9.11-beta.4**:

- **Its name and model.** Everything that turns a VIN into "i5 eDrive40" comes
  from a one-off REST call that previously ran only at setup, so a later car
  stayed a bare VIN on the device page. It is now fetched the first time the car
  is seen on the stream (one request against that day's quota, once).
- **Its coverage grace period.** The
  [stream self-test](Getting-Started-4-Choose-Data#did-it-work) gives a fresh
  install a week before reporting a cluster as silent. That clock is now per
  car, so a car added two years in is not declared incomplete on day one.

You still have to tick the new car's fields in **BMW's portal**: Data Selection
is per vehicle and has no API. Run **Configure → Choose streamed data** again and
select the new car on the portal page the activator opens
([step 4](Getting-Started-4-Choose-Data)).

## Quota with several cars

The 50 requests per 24 h belong to the **account**, and the daily refresh costs
**2 requests per car** (the telematics container, plus the tyre diagnosis). Three
cars on one account is 6 of 50 a day — comfortable. Two separate accounts get 50
each, because BMW counts per account. See [API quota](Feature-API-Quota).

## Telling the cars apart

<a id="telling-the-cars-apart"></a>

| Where | What to do |
| --- | --- |
| **The card** | One card per car: set `device:` (the picker offers each car by name) or `vin:`. With nothing set it picks the first car it finds. |
| **Services** | Pass `vin:` to target one car. `bavariandata.fetch_telematic_data` without a VIN refreshes **every** car on the entry; the other `fetch_*` services pick one car, so name the VIN. With two accounts set up, also pass `entry_id:` — or a `vin:`, which identifies the account on its own. See [Services](Services-Reference). |
| **Automations** | The `bavariandata_charging_*` events carry `vin` and `entry_id`; filter on `trigger.event.data.vin`. The shipped [blueprints](Feature-Automations) fire for every car unless you add that condition. |
| **The evcc bridge** | Topics are per VIN (`<prefix>/<vin>/soc`), so several cars share one prefix without colliding. Generate each car's evcc block with `bavariandata.get_evcc_config` and its `vin`. One bound wallbox meter is per entry, though: if two cars can charge at home at the same time, the measured kWh cannot be attributed to one of them. |
| **Debug logging** | The log level is one switch for the whole integration — it cannot be per account. Verbose logging stays on while **any** entry has it enabled, and logs every account's data while it is on. |
| **Repairs & notifications** | Coverage and stream repairs name the car. The quota repair names neither account — with two set up, check the **API Quota Remaining** diagnostic entity on each debug device. |

## Removing an entry

Removing one entry leaves the other running: its stream, services and card are
untouched. The entry being removed takes its own stored history, coverage record
and tyre data with it, and clears any retained evcc topics for **every** car it
ever had — including one that only ever streamed and never answered a basic-data
request.
