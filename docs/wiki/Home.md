<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/logo.png" alt="BavarianData logo" width="200" />
</p>

# BavarianData — User Manual

> 🇩🇪 [Deutsch](DE-Home)

Connect Home Assistant directly to **BMW CarData**: a live MQTT stream plus a
REST API, using your own personal BMW client ID. No third-party cloud in
between, no MyBMW screen-scraping — Home Assistant is the only client. Read-only:
CarData can show you everything, but cannot command the car.

This wiki is the full manual. For a one-page overview and install-at-a-glance,
see the [README](https://github.com/JustChr/BavarianData#readme).

## Start here

New install? Follow the five steps in order:

1. [BMW portal setup](Getting-Started-1-BMW-Portal-Setup) — client ID + scopes
2. [Install via HACS](Getting-Started-2-Install)
3. [Add & authorize the integration](Getting-Started-3-Add-and-Authorize)
4. [Choose which data to stream](Getting-Started-4-Choose-Data) — the cluster picker
5. [Add the card to a dashboard](The-Dashboard-Card) — nothing appears in the
   sidebar on its own

## The dashboard card

BavarianData ships a Lovelace card whose *resource* is registered automatically;
you add the card to a dashboard yourself. It has several views:

- [Overview, and all card views + full YAML reference](The-Dashboard-Card)

## Features

- [Entities & devices](Feature-Entities-and-Devices) — what appears, and how it's named
- [Charging history & cost](Feature-Charging-History-and-Cost)
- [Battery health](Feature-Battery-Health) — how usable capacity is learned
- [Efficiency & real range](Feature-Efficiency-and-Range) — measured consumption, and how far it reaches
- [Trips / driving journal](Feature-Trips) — and the Fahrtenbuch caveat
- [evcc & wallbox bridge](Feature-evcc-and-Wallbox-Bridge) — hand your state of charge to a charge controller, at no API cost
- [Energy dashboard & long-term statistics](Feature-Energy-and-Statistics)
- [Export (CSV / HTML report)](Feature-Export)
- [Events & automation blueprints](Feature-Automations)
- [API quota](Feature-API-Quota) — the 50 requests / 24 h cap
- [Multiple cars & accounts](Feature-Multiple-Cars-and-Accounts) — one entry per account, and how to say which car you mean

## Reference

- [Settings reference](Settings-Reference) — every Configure screen
- [Services reference](Services-Reference) — every service call
- [Troubleshooting & FAQ](Troubleshooting-and-FAQ)
- [Deep reference](Reference) — descriptor/field catalogue

> **Status — actively developed.** A spare-time project, verified against a
> limited number of vehicles and Home Assistant versions, so expect the odd
> rough edge on a model it hasn't met yet. It is read-only by design — CarData
> cannot command the car — so treat what it reports as informational rather than
> as the trigger for a safety-critical automation.
