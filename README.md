<p align="center">
  <img src="logo.png" alt="BavarianData logo" width="240" />
</p>

<h1 align="center">BavarianData: Connect Home Assistant to BMW CarData</h1>

<p align="center">
  Bring your BMW's live data into Home Assistant — straight from BMW CarData,
  no third-party cloud in between.
</p>

---

BMW CarData is BMW's own telematics service: an MQTT stream that pushes vehicle
data in real time and a REST API for on-demand snapshots. This integration talks
to both directly, using your personal BMW client ID. There is no intermediate
server and no MyBMW screen-scraping — Home Assistant is the only client.

Every descriptor BMW sends becomes a native entity. A parked charging session,
a door left open, tyre pressures, the 12 V battery — each surfaces as a sensor or
binary sensor with a proper device class, unit, and (where BMW provides one) a
set of translated states. The integration also ships a Lovelace card and a cached
vehicle image so a usable dashboard exists out of the box.

> **Status — experimental.** This is a spare-time project, verified against a
> limited number of vehicles and Home Assistant versions. Expect rough edges and
> avoid wiring it into safety-critical automations. Track `main`; other branches
> may be broken at any time.

## How it works

- **Streaming** — the integration holds one MQTT connection to BMW's CarData
  broker and refreshes your OAuth tokens on its own. Whatever descriptors you
  enable in the BMW portal arrive as they change; each maps to an entity.
- **REST snapshots** — some data (basic vehicle info, charging history, tyre
  diagnosis, the vehicle image, …) isn't streamed, so the integration fetches it
  on demand or on a schedule.
- **Quota-aware** — BMW caps the REST API at **50 requests per 24 h**. The
  integration tracks and enforces this itself, and every manual service call
  counts against it.

## Requirements

- A BMW account with a vehicle that supports CarData.
- **CarData API** and **CarData Streaming** subscribed in the BMW portal, and a
  **client ID** generated for this integration.
- Home Assistant **2024.6** or newer.

It helps to skim
[BMW's CarData documentation](https://bmw-cardata.bmwgroup.com/customer/public/api-documentation/Id-Introduction)
once before starting — the portal steps below mirror it.

## Step 1 — Set up BMW CarData in the portal

Do this before adding the integration. The CarData portal isn't offered in every
market, but the client ID it produces is account-wide, so you can complete the
setup from any supported region and use it everywhere.

Open the vehicle overview and pick **CarData**:

|        | English | German |
| ------ | ------- | ------ |
| BMW    | [vehicle overview](https://www.bmw.co.uk/en-gb/mybmw/vehicle-overview) | [Fahrzeugübersicht](https://www.bmw.de/de-de/mybmw/vehicle-overview) |
| Mini   | [vehicle overview](https://www.mini.co.uk/en-gb/mymini/vehicle-overview) | [Fahrzeugübersicht](https://www.mini.de/de-de/mymini/vehicle-overview) |

1. Select your vehicle and open **BMW CarData** / **Mini CarData**.
2. [Generate a client ID](https://bmw-cardata.bmwgroup.com/customer/public/api-documentation/Id-Technical-registration_Step-1).
3. Give the client both scopes — `cardata:api:read` and `cardata:streaming:read`
   — and authorize it.
   *If the portal throws a scope error, reload, add one scope, wait ~30 s, then
   add the second.*
4. Open **Data Selection** (`Datenauswahl ändern`) and choose which descriptors
   to stream. Click **Load more** until the full list is shown, then tick what
   you want. To tick everything currently listed, paste this into the browser
   console:

   ```js
   (() => {
     const labels = document.querySelectorAll('.css-k008qs label.chakra-checkbox');
     let changed = 0;
     labels.forEach(label => {
       const input = label.querySelector('input.chakra-checkbox__input[type="checkbox"]');
       if (!input || input.disabled || input.checked) return;
       label.click();
       if (!input.checked) {
         const ctrl = label.querySelector('.chakra-checkbox__control');
         if (ctrl) ctrl.click();
       }
       if (!input.checked) {
         input.checked = true;
         ['click', 'input', 'change'].forEach(type =>
           input.dispatchEvent(new Event(type, { bubbles: true }))
         );
       }
       if (input.checked) changed++;
     });
     console.log(`Checked ${changed} of ${labels.length} checkboxes.`);
   })();
   ```

5. Save, and repeat for each vehicle you want in Home Assistant.

> **Prefer to pick by cluster?** After the integration is installed,
> **Configure → Choose streamed data** lets you select data by cluster
> (Electric vehicle, Vehicle status, Tire data, …) and generates a console
> snippet that ticks only those clusters. BMW has no API to set the stream
> selection — it is portal-only, and requesting per-descriptor streaming scopes
> is rejected (details in
> [docs/reference/stream-scope-investigation.md](docs/reference/stream-scope-investigation.md)).
> The full field-per-cluster breakdown lives in
> [docs/reference/telematics-fields.md](docs/reference/telematics-fields.md).

> **Extrapolated state-of-charge helpers** need these descriptors in the stream:
> `vehicle.drivetrain.batteryManagement.header`,
> `vehicle.drivetrain.batteryManagement.maxEnergy`,
> `vehicle.powertrain.electric.battery.charging.power`, and
> `vehicle.drivetrain.electricEngine.charging.status`.

## Step 2 — Install

Via [HACS](https://hacs.xyz/) as a custom repository:

1. HACS → **Custom repositories** → add this repo, category **Integration**.
2. Install **BavarianData: Connect Home Assistant to BMW CarData**.
3. Restart Home Assistant.

## Step 3 — Add the integration

1. **Settings → Devices & Services → Add Integration → BavarianData: Connect Home Assistant to BMW CarData**.
2. The first screen recaps the portal setup and asks for your **client ID**.
3. Home Assistant shows a **link and a code**. Open the link, sign in, and
   approve the device on BMW's site. The dialog waits and **continues on its own**
   the instant you approve — nothing to click in HA, no timing to get right. If
   it times out or is declined, press **Submit** for a fresh code and try again.
4. Wait for the first data. Triggering something in the MyBMW app (lock/unlock)
   usually nudges the car into sending an update right away.

If BMW later invalidates the token, run **Configure → Re-authorize with BMW**.
Removing and re-adding the integration with the same client ID also works — the
previous entry is cleaned up automatically.

## Entities

- Each VIN becomes its own device.
- Streamed descriptors become sensors and binary sensors, named from a curated
  English title set ([`tools/curated_titles.json`](tools/curated_titles.json),
  baked into the catalogue and the HA translations). German names ship too;
  open an issue or PR if a name looks off.
- Numeric fields get sensible device classes; distances use
  `device_class: distance`, and odometer/mileage uses
  `state_class: total_increasing` so long-term statistics work.
- Every entity exposes its source timestamp plus its catalogue `cluster` and
  `category` as attributes — the Lovelace card uses these to group values
  regardless of the user's HA language.
- Each VIN also gets an **image** entity holding BMW's rendered picture of the
  car. It's cached and survives restarts, so it doesn't burn quota on every
  boot; refresh it manually with `bavariandata.fetch_vehicle_image`.

## Lovelace card

A custom **BMW CarData Card** is bundled and registered automatically — no
dashboard resource to add by hand. Pick it from the card gallery to open a visual
editor, or write YAML directly.

**Overview** — the vehicle render, a state-of-charge ring (blue while charging),
remaining range, charging status, and a grid of key metrics. With the integration
installed, the minimal config auto-discovers the car:

```yaml
type: custom:bmw-cardata-card
```

Pin a specific vehicle with `device:` (device id) or `vin:`. Optional entity
overrides: `title`, `image`, `soc`, `range`, `charging`, `target_soc`,
`time_to_full`, `odometer`, `plug`.

**Single cluster** — set `cluster:` to list every value in one catalogue cluster.
Use one card per cluster:

```yaml
type: custom:bmw-cardata-card
cluster: electric   # electric · status · tire · usage · events · basic · contract · metadata · other
```

If the card doesn't show up after an update, hard-refresh the browser.

## Services

Each service is available in Developer Tools and as a button in the integration's
**Configure** menu. **Every call spends one of your 50 requests / 24 h.**

| Service | What it fetches |
| --- | --- |
| `bavariandata.fetch_telematic_data` | Current contents of a VIN's telematics container. |
| `bavariandata.fetch_vehicle_mappings` | Vehicles linked to the account and their PRIMARY/SECONDARY status. |
| `bavariandata.fetch_basic_data` | Static vehicle metadata (model, series, …). |
| `bavariandata.fetch_charging_history` | Charging sessions (paginated; optional `from` / `to`). |
| `bavariandata.fetch_tyre_diagnosis` | Smart-maintenance tyre diagnosis. |
| `bavariandata.fetch_location_charging_settings` | Location-based charging settings (paginated). |
| `bavariandata.fetch_vehicle_image` | Vehicle render (updates the image entity). |

## Troubleshooting

- **Debug logging** is off by default. Turn it on in **Configure → options**
  (`debug_log`) and reload. It's verbose and can include vehicle data such as GPS
  and VIN, so leave it off unless you're chasing a problem.
- **No data arriving?** Confirm the descriptors are ticked in the portal's Data
  Selection, and trigger a lock/unlock in the MyBMW app to prompt an update.
- **Only one stream per account (GCID)** — BMW allows a single concurrent
  streaming client, so no other tool can be connected at the same time.
- **Read-only** — CarData cannot send commands, so this integration can't lock,
  precondition, or otherwise control the car.

## Contributing & support

- Bugs in the integration → [Issues](https://github.com/JustChr/BavarianData/issues).
- BMW-side registration trouble, setup help, or general questions →
  [Discussions](https://github.com/JustChr/BavarianData/discussions).

The descriptor catalogue, metadata, translations, and reference docs are all
generated from BMW's exports by the pipeline in [`tools/`](tools/) — see
[tools/README.md](tools/README.md) before hand-editing any generated file.

## Credits & license

Released under the [MIT License](LICENSE). This integration began as a
continuation of the public-domain
[`bmw-cardata-ha`](https://github.com/JjyKsi/bmw-cardata-ha) by **JjyKsi**;
that project carried no licensing restrictions, and the original author is
credited in [`NOTICE`](NOTICE) out of respect for their work.

"BMW", "Mini", "Rolls-Royce", and "CarData" are trademarks of their respective
owners. This is an independent, community-built integration and is **not**
affiliated with, endorsed by, or sponsored by BMW Group. Use at your own risk;
see the warranty disclaimer in the [LICENSE](LICENSE).
