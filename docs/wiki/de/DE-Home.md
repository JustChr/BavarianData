<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/logo.png" alt="BavarianData-Logo" width="200" />
</p>

# BavarianData — Handbuch

> 🇬🇧 [English version](Home)

Verbindet Home Assistant direkt mit **BMW CarData**: ein Live-MQTT-Stream plus
eine REST-API, mit deiner eigenen, persönlichen BMW-Client-ID. Keine fremde Cloud
dazwischen, kein Auslesen der MyBMW-Oberfläche — Home Assistant ist der einzige
Client. Nur lesend: CarData zeigt dir alles, kann das Auto aber nicht steuern.

Dieses Wiki ist das vollständige Handbuch. Einen Überblick auf einer Seite und
die Installation auf einen Blick findest du in der
[README](https://github.com/JustChr/BavarianData#readme) (englisch).

## Hier starten

Neu installiert? Folge den fünf Schritten der Reihe nach:

1. [Einrichtung im BMW-Portal](DE-Getting-Started-1-BMW-Portal-Setup) — Client-ID und Berechtigungen
2. [Installation über HACS](DE-Getting-Started-2-Install)
3. [Integration hinzufügen und autorisieren](DE-Getting-Started-3-Add-and-Authorize)
4. [Gestreamte Daten auswählen](DE-Getting-Started-4-Choose-Data) — die Datengruppen-Auswahl
5. [Karte zu einem Dashboard hinzufügen](DE-The-Dashboard-Card) — von allein
   erscheint nichts in der Seitenleiste

## Die Dashboard-Karte

BavarianData bringt eine Lovelace-Karte mit, deren *Ressource* automatisch
registriert wird; die Karte selbst fügst du einem Dashboard hinzu. Sie hat
mehrere Ansichten:

- [Übersicht, alle Kartenansichten und die vollständige YAML-Referenz](DE-The-Dashboard-Card)

## Funktionen

- [Entitäten & Geräte](DE-Feature-Entities-and-Devices) — was angelegt wird und wie es heißt
- [Ladeverlauf & Kosten](DE-Feature-Charging-History-and-Cost)
- [Batteriezustand](DE-Feature-Battery-Health) — wie die nutzbare Kapazität gelernt wird
- [Effizienz & reale Reichweite](DE-Feature-Efficiency-and-Range) — gemessener Verbrauch und wie weit er reicht
- [Fahrten / Fahrtenbuch](DE-Feature-Trips) — und warum es kein Finanzamt-Fahrtenbuch ist
- [evcc- & Wallbox-Brücke](DE-Feature-evcc-and-Wallbox-Bridge) — den Ladezustand ohne API-Kosten an eine Ladesteuerung geben
- [Energie-Dashboard & Langzeitstatistiken](DE-Feature-Energy-and-Statistics)
- [Export (CSV / HTML-Bericht)](DE-Feature-Export)
- [Ereignisse & Automations-Blueprints](DE-Feature-Automations)
- [API-Kontingent](DE-Feature-API-Quota) — die Grenze von 50 Anfragen / 24 h

## Nachschlagen

- [Einstellungen](DE-Settings-Reference) — jeder Konfigurieren-Bildschirm
- [Dienste](DE-Services-Reference) — jeder Dienstaufruf
- [Fehlerbehebung & FAQ](DE-Troubleshooting-and-FAQ)
- [Technische Referenz](DE-Reference) — Katalog der Deskriptoren und Felder

> **Status — in aktiver Entwicklung.** Ein Freizeitprojekt, geprüft an einer
> begrenzten Zahl von Fahrzeugen und Home-Assistant-Versionen — rechne also mit
> der einen oder anderen Unebenheit bei einem Modell, das es noch nicht kennt. Es
> ist bewusst nur lesend (CarData kann das Auto nicht steuern), behandle seine
> Werte daher als Information und nicht als Auslöser für sicherheitskritische
> Automationen.
