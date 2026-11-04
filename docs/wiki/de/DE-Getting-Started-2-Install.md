# 2. Installation aus dem Marketplace

> 🇬🇧 [English version](Getting-Started-2-Install)

BavarianData ist im **Marketplace** von Home Assistant, dem eingebauten Store
für Community-Integrationen. Es wird wie jede andere Integration installiert,
ohne benutzerdefiniertes Repository.

> **Home Assistant älter als 2026.11?** Den Marketplace gibt es seit 2026.11;
> davor war derselbe Store das separate **HACS**. Siehe
> [Vor Home Assistant 2026.11: HACS](#vor-home-assistant-202611-hacs).

## Voraussetzungen

- Home Assistant **2026.3** oder neuer.

## Schritte

Am schnellsten geht es über diesen Button. Er öffnet BavarianData direkt im
Marketplace auf deinem eigenen Home Assistant (auf älteren Versionen in HACS):

[![Öffne deine Home-Assistant-Instanz und BavarianData im Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=JustChr&repository=BavarianData&category=integration)

Oder von Hand:

1. Öffne **Einstellungen → Marketplace** und suche nach **BavarianData**.
2. Öffne **BavarianData: Connect Home Assistant to BMW CarData** und wähle
   **Installieren**. Beim ersten Mal lässt dich der Marketplace bestätigen, dass
   Community-Inhalte vom Home-Assistant-Projekt nicht geprüft werden.
3. **Starte Home Assistant neu.**

Updates kommen über **GitHub-Releases**, nicht bei jeder Änderung am Code. Home
Assistant bietet jedes wie seine eigenen als Update an; die Release Notes stehen
im Update-Dialog.

Für Betaversionen öffne BavarianData im Marketplace und wähle
**⋮ → Vorabversionen einschließen**.

## Wenn es in der Suche nicht auftaucht

Der Marketplace aktualisiert seinen Katalog alle paar Stunden. Öffne die Seite
von BavarianData und wähle **⋮ → Informationen aktualisieren**, oder starte Home
Assistant neu, und suche erneut. Siehst du es dann immer noch nicht, füge es
über seinen GitHub-Link hinzu: Es ist dieselbe Integration mit denselben
Updates.

1. Öffne im Marketplace den Reiter **Über Link hinzufügen**.
2. Gib `https://github.com/JustChr/BavarianData` mit dem Typ **Integration**
   ein.
3. Installiere es und starte Home Assistant neu.

## Vor Home Assistant 2026.11: HACS

Auf Home Assistant 2026.3 bis 2026.10 installierst du stattdessen über
**HACS**. Der Button oben öffnet dort HACS, oder von Hand:

1. Öffne **HACS** und suche nach **BavarianData**.
2. Öffne es, installiere es und starte Home Assistant neu.

Taucht es nicht auf, füge `https://github.com/JustChr/BavarianData` unter
**⋮ → Benutzerdefinierte Repositories** mit der Kategorie **Integration** hinzu.
In der Liste von HACS steht neben BavarianData kein Logo; das liegt an HACS,
die Installation ist nicht kaputt.

Aktualisierst du Home Assistant auf 2026.11 oder neuer, übernimmt der
Marketplace von selbst: BavarianData bleibt installiert, mit seiner
Update-Entität und seinen Einstellungen, und es muss nichts neu installiert
werden.

**Weiter:** [3. Integration hinzufügen und autorisieren →](DE-Getting-Started-3-Add-and-Authorize)
