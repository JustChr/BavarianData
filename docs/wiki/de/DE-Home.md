<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/logo.png" alt="BavarianData-Logo" width="200" />
</p>

# BavarianData — Handbuch

> 🇬🇧 [English version](Home)

Verbindet Home Assistant direkt mit **BMW CarData**: über einen Live-MQTT-Stream
und die REST-API, mit deiner eigenen BMW-Client-ID. Keine fremde Cloud
dazwischen, kein Auslesen der MyBMW-App — Home Assistant ist der einzige Client.
Nur lesend: CarData zeigt dir alles, kann das Auto aber nicht steuern.

Dieses Wiki ist das vollständige Handbuch. Einen Überblick auf einer Seite und
die Installation auf einen Blick findest du in der
[README](https://github.com/JustChr/BavarianData#readme) (englisch).

## Hier starten

Du kommst von der alten Integration *BMW Connected Drive*? Dann lies zuerst
[Umstieg von bimmer_connected](DE-Coming-from-bimmer_connected). Ob dein Auto
unterstützt wird, steht unter [Welche Autos funktionieren](DE-Supported-Cars).

Zum ersten Mal dabei? Geh die fünf Schritte der Reihe nach durch:

1. [Einrichtung im BMW-Portal](DE-Getting-Started-1-BMW-Portal-Setup) — deine Client-ID erstellen
2. [Installation über HACS](DE-Getting-Started-2-Install)
3. [Integration hinzufügen und autorisieren](DE-Getting-Started-3-Add-and-Authorize)
4. [Gestreamte Daten auswählen](DE-Getting-Started-4-Choose-Data) — die Datengruppen-Auswahl
5. [Karte zu einem Dashboard hinzufügen](DE-The-Dashboard-Card) — von selbst
   taucht nichts in der Seitenleiste auf

## Die Dashboard-Karte

BavarianData bringt eine eigene Lovelace-Karte mit. Ihre *Ressource* wird
automatisch eingetragen, die Karte selbst legst du auf einem Dashboard an. Sie
hat mehrere Ansichten:

- [Übersicht, alle Kartenansichten und die vollständige YAML-Referenz](DE-The-Dashboard-Card)

## Funktionen

- [Entitäten & Geräte](DE-Feature-Entities-and-Devices) — was angelegt wird und wie es heißt
- [Ladeverlauf & Kosten](DE-Feature-Charging-History-and-Cost)
- [Batteriezustand](DE-Feature-Battery-Health) — wie die nutzbare Kapazität gelernt wird
- [Effizienz & reale Reichweite](DE-Feature-Efficiency-and-Range) — gemessener Verbrauch und wie weit er reicht
- [Fahrten / Fahrtenbuch](DE-Feature-Trips) — und warum es kein Finanzamt-Fahrtenbuch ist
- [evcc- & Wallbox-Brücke](DE-Feature-evcc-and-Wallbox-Bridge) — den Ladezustand ohne API-Kosten an eine Ladesteuerung weitergeben
- [Energie-Dashboard & Langzeitstatistiken](DE-Feature-Energy-and-Statistics)
- [Export (CSV / HTML-Bericht)](DE-Feature-Export)
- [Ereignisse & Automations-Blueprints](DE-Feature-Automations)
- [API-Kontingent](DE-Feature-API-Quota) — die Grenze von 50 Anfragen / 24 h
- [Mehrere Fahrzeuge & Konten](DE-Feature-Multiple-Cars-and-Accounts) — ein Eintrag je Konto, und wie du sagst, welches Auto gemeint ist

## Nachschlagen

- [Einstellungen](DE-Settings-Reference) — jeder Konfigurieren-Bildschirm
- [Dienste](DE-Services-Reference) — jeder Dienstaufruf
- [Fehlerbehebung & FAQ](DE-Troubleshooting-and-FAQ)
- [Technische Referenz](DE-Reference) — Katalog der Deskriptoren und Felder

> **Status — in aktiver Entwicklung.** Ein Freizeitprojekt, getestet an wenigen
> Fahrzeugen und Home-Assistant-Versionen. Bei einem Modell, das es noch nicht
> kennt, kann also mal etwas haken. Es ist bewusst nur lesend (CarData kann das
> Auto nicht steuern): Nimm seine Werte als Information, nicht als Auslöser für
> sicherheitskritische Automationen.
