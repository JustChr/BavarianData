<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/logo.png" alt="BavarianData logo" width="200" />
</p>

# BavarianData — User Manual

Connect Home Assistant directly to **BMW CarData**: a live MQTT stream plus a
REST API, using your own personal BMW client ID. No third-party cloud in
between, no MyBMW screen-scraping — Home Assistant is the only client. Read-only:
CarData can show you everything, but cannot command the car.

This wiki is the full manual. For a one-page overview and install-at-a-glance,
see the [README](https://github.com/JustChr/BavarianData#readme).

## Start here

New install? Follow the four steps in order:

1. [BMW portal setup](Getting-Started-1-BMW-Portal-Setup) — client ID + scopes
2. [Install via HACS](Getting-Started-2-Install)
3. [Add & authorize the integration](Getting-Started-3-Add-and-Authorize)
4. [Choose which data to stream](Getting-Started-4-Choose-Data) — the cluster picker

## The dashboard card

BavarianData ships a Lovelace card, registered automatically. It has several
views:

- [Overview, and all card views + full YAML reference](The-Dashboard-Card)

## Features

- [Entities & devices](Feature-Entities-and-Devices) — what appears, and how it's named
- [Charging history & cost](Feature-Charging-History-and-Cost)
- [Battery health](Feature-Battery-Health) — how usable capacity is learned
- [Trips / driving journal](Feature-Trips) — and the Fahrtenbuch caveat
- [Energy dashboard & long-term statistics](Feature-Energy-and-Statistics)
- [Export (CSV / HTML report)](Feature-Export)
- [Events & automation blueprints](Feature-Automations)
- [API quota](Feature-API-Quota) — the 50 requests / 24 h cap

## Reference

- [Settings reference](Settings-Reference) — every Configure screen
- [Services reference](Services-Reference) — every service call
- [Troubleshooting & FAQ](Troubleshooting-and-FAQ)
- [Deep reference](Reference) — descriptor/field catalogue

> **Status — experimental.** A spare-time project, verified against a limited
> number of vehicles and Home Assistant versions. Expect rough edges and avoid
> wiring it into safety-critical automations.
