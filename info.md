# BavarianData: Connect Home Assistant to BMW CarData

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/logo.png" alt="BavarianData logo" width="240" />
</p>

Bring your BMW's live data into Home Assistant straight from BMW CarData — no
third-party cloud in between. BavarianData holds one MQTT connection to BMW's
CarData stream, keeps the OAuth tokens fresh on its own, and turns every
streamed descriptor into a native sensor or binary sensor. It also polls the
CarData REST API for telematics, basic vehicle data, charging history, tyre
diagnosis, location-based charging settings and the vehicle image — all within
BMW's 50 requests / 24 h quota. A Lovelace card and a cached vehicle image ship
with it, so a usable dashboard exists out of the box.

## Requirements

- A BMW CarData account with **CarData API** and **CarData Streaming** subscribed
  in the BMW portal, plus a generated **client ID**.
- Home Assistant **2026.3** or newer.

## Setup

1. Add the integration via **Settings → Devices & Services → Add Integration →
   BavarianData: Connect Home Assistant to BMW CarData**.
2. Enter your client ID and complete the BMW device-code authorization — the
   dialog continues on its own once you approve on BMW's site.
3. Wait for the car to send data (locking/unlocking via the MyBMW app usually
   triggers an update).

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-car.png" alt="Bundled Lovelace card showing a BMW i5 eDrive40 with charge level, range, charging status and odometer" width="360" />
</p>
<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-security.png" alt="Security &amp; closures card with a top-down car diagram, anti-theft alarm armed and all closures closed" width="300" />
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-tires.png" alt="Tyre pressure card showing all four tyres at 290 kPa on a top-down car diagram" width="300" />
</p>

See the [README](https://github.com/JustChr/BavarianData) for the full BMW
portal setup, the descriptor-selection helper, and troubleshooting.

---

An independent, community-built integration, not affiliated with or endorsed by
BMW Group. Distributed under the MIT License; it began as a continuation of the
public-domain [`bmw-cardata-ha`](https://github.com/JjyKsi/bmw-cardata-ha) by
**JjyKsi**, credited in [`NOTICE`](NOTICE).
