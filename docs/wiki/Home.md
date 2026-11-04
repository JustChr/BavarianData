<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/logo.png" alt="BavarianData logo" width="200" />
</p>

# BavarianData — User Manual

> 🇩🇪 [Deutsch](DE-Home)

> 🌐 This manual is also on the website, with search and a live demo of the card: **[justchr.github.io/BavarianData](https://justchr.github.io/BavarianData/docs/)**

Connect Home Assistant directly to **BMW CarData**: a live MQTT stream plus a
REST API, using your own personal BMW client ID. No third-party cloud in
between, no MyBMW screen-scraping — Home Assistant is the only client. Read-only:
CarData can show you everything, but cannot command the car.

This wiki is the full manual. For a one-page overview and install-at-a-glance,
see the [README](https://github.com/JustChr/BavarianData#readme).

## Start here

Used the old *BMW Connected Drive* integration? Read
[Coming from bimmer_connected](Coming-from-bimmer_connected) first. Not sure
your car is supported? See [which cars work](Supported-Cars).

New install? Follow the five steps in order:

1. [BMW portal setup](Getting-Started-1-BMW-Portal-Setup) — create your client ID
2. [Install from the Marketplace](Getting-Started-2-Install)
3. [Add & authorize the integration](Getting-Started-3-Add-and-Authorize)
4. [Choose which data to stream](Getting-Started-4-Choose-Data) — the cluster picker
5. [Add the card to a dashboard](The-Dashboard-Card) — nothing appears in the
   sidebar on its own

## The dashboard card

BavarianData ships a Lovelace card whose *resource* is registered automatically;
you add the card to a dashboard yourself. It has several views:

- [Overview, and all card views + full YAML reference](The-Dashboard-Card)

## Features

### Charging

- [Charging history & cost](Feature-Charging-History-and-Cost) — every charge with its energy, and what it cost at your tariff
- [State of charge while charging](Feature-State-of-Charge) — why BMW's figure stands still mid-charge, and the estimate that keeps counting
- [Battery health](Feature-Battery-Health) — how usable capacity is learned
- [Smart charging](Feature-Smart-Charging) — charge level and target for EV Smart Charging and Predbat

### Driving

- [Trips / driving journal](Feature-Trips) — and the Fahrtenbuch caveat
- [Efficiency & real range](Feature-Efficiency-and-Range) — measured consumption, and how far it reaches
- [Mileage & allowance](Feature-Mileage) — distance driven month by month, and whether a lease is on plan

### Automate & connect

- [Events & automation blueprints](Feature-Automations) — device triggers, charging events and ready-made automations
- [Ask Assist & AI about your car](Feature-Assist-and-AI) — voice and chat questions, and MCP for desktop AI apps
- [Energy dashboard & long-term statistics](Feature-Energy-and-Statistics) — the car's charging in Home Assistant's Energy dashboard
- [evcc & wallbox bridge](Feature-evcc-and-Wallbox-Bridge) — hand your state of charge to a charge controller, at no API cost
- [ABRP live data](Feature-ABRP) — your real battery level in A Better Routeplanner, with a free ABRP account
- [Export (CSV / HTML report)](Feature-Export)

### Your account

- [Entities & devices](Feature-Entities-and-Devices) — what appears, and how it's named
- [Multiple cars & accounts](Feature-Multiple-Cars-and-Accounts) — one entry per account, and how to say which car you mean
- [API quota](Feature-API-Quota) — the 50-requests-a-day cap

## Reference

- [Settings reference](Settings-Reference) — every Configure screen
- [Services reference](Services-Reference) — every service call
- [Troubleshooting & FAQ](Troubleshooting-and-FAQ)
- [Deep reference](Reference) — descriptor/field catalogue

> **Status — actively developed.** Runs on electric, plug-in hybrid, petrol and
> diesel BMWs and MINIs ([which cars](Supported-Cars)). It is read-only by
> design — CarData cannot command the car — so treat what it reports as
> informational rather than as the trigger for a safety-critical automation.
