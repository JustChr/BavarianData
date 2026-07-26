<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/logo.png" alt="BavarianData logo" width="240" />
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
server and no MyBMW screen-scraping — Home Assistant is the only client, and it's
**read-only** (CarData cannot command the car).

Every descriptor BMW sends becomes a native entity — charge level, doors, tyre
pressures, the 12 V battery — each with a proper device class, unit and
translated states. On top of that the integration derives a **charging history &
cost** ledger, **battery-health** learning, a **trip journal**, **long-term
statistics** for the Energy dashboard, and **CSV/PDF export** — all from the
stream, spending no API quota. A bundled **Lovelace card** and a cached vehicle
image give you a usable dashboard out of the box.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-car.png" alt="Bundled Lovelace card showing a BMW i5 eDrive40 with charge level, range, charging status and odometer" width="360" />
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-security.png" alt="Security &amp; closures card with a top-down car diagram, anti-theft alarm armed and all closures closed" width="300" />
  &nbsp;&nbsp;
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-tires.png" alt="Tyre pressure card flagging slightly high pressures on all four tyres on a top-down car diagram" width="300" />
</p>

> **Status — experimental.** This is a spare-time project, verified against a
> limited number of vehicles and Home Assistant versions. Expect rough edges and
> avoid wiring it into safety-critical automations. Track `main`; other branches
> may be broken at any time.

## 📖 Full documentation → the [Wiki](https://github.com/JustChr/BavarianData/wiki)

The complete manual — every screen, card view, setting, service and feature —
lives in the **[Wiki](https://github.com/JustChr/BavarianData/wiki)**. This
README is the quick overview.

- [Getting started](https://github.com/JustChr/BavarianData/wiki/Home#start-here) · [The dashboard card](https://github.com/JustChr/BavarianData/wiki/The-Dashboard-Card) · [Settings](https://github.com/JustChr/BavarianData/wiki/Settings-Reference) · [Services](https://github.com/JustChr/BavarianData/wiki/Services-Reference) · [Troubleshooting](https://github.com/JustChr/BavarianData/wiki/Troubleshooting-and-FAQ)

## Requirements

- A BMW account with a vehicle that supports CarData.
- **CarData API** and **CarData Streaming** subscribed in the BMW portal, and a
  **client ID** generated for this integration.
- Home Assistant **2026.3** or newer, with [HACS](https://hacs.xyz/).

## Quick start

Four steps — the Wiki has the detail and screenshots for each.

1. **[Set up BMW CarData in the portal](https://github.com/JustChr/BavarianData/wiki/Getting-Started-1-BMW-Portal-Setup)** —
   generate a **client ID** and give it both scopes (`cardata:api:read` and
   `cardata:streaming:read`). Don't touch Data Selection yet.
2. **[Install via HACS](https://github.com/JustChr/BavarianData/wiki/Getting-Started-2-Install)** —
   add this repo as a custom repository (category *Integration*), install, and
   restart Home Assistant.
3. **[Add & authorize](https://github.com/JustChr/BavarianData/wiki/Getting-Started-3-Add-and-Authorize)** —
   **Settings → Devices & Services → Add Integration → BavarianData**, paste the
   client ID, and approve the device on BMW's site. If BMW says *access denied*
   even though your login worked, that's a
   [known BMW-side quirk with a workaround](https://github.com/JustChr/BavarianData/wiki/Troubleshooting-and-FAQ#onboarding-fails-with-access-denied).
4. **[Choose which data to stream](https://github.com/JustChr/BavarianData/wiki/Getting-Started-4-Choose-Data)** —
   pick your clusters. Guided setup then turns the fields on with a one-click
   **Activate BMW data** bookmarklet; manual setup hands you a browser-console
   snippet to paste into the portal's Data Selection instead. Either way, save
   and trigger a lock/unlock to prompt the first update.

### Activating stream fields in one call (advanced)

Stream selection ("Datenauswahl") has no CarData API. Guided setup and
**Configure → Choose streamed data** hide this behind the one-click **Activate
BMW data** bookmarklet, which flips the fields on for you in the browser. If you
instead want to drive it yourself, the `bavariandata.activate_stream_fields`
service **replays the exact request the portal sends when you save**, replacing
the whole selection in one call. Because that endpoint authenticates with your
**browser session** (not the integration's token, and behind BMW's bot-defense),
you supply a **captured portal session** — a manual, occasional tool that spends
no API quota. See
[Services → activate_stream_fields](https://github.com/JustChr/BavarianData/wiki/Services-Reference#activate_stream_fields).

## Contributing & support

- Bugs in the integration → [Issues](https://github.com/JustChr/BavarianData/issues).
- BMW-side registration trouble, setup help, or general questions →
  [Discussions](https://github.com/JustChr/BavarianData/discussions).

The descriptor catalogue, metadata, translations and reference docs are all
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
