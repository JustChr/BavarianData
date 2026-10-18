# 2. Installation über HACS

> 🇬🇧 [English version](Getting-Started-2-Install)

BavarianData ist im **HACS-Standard-Store**, wird also wie jede andere
Integration installiert — ohne benutzerdefiniertes Repository.

## Voraussetzungen

- Home Assistant **2026.3** oder neuer.
- HACS ist installiert.

## Schritte

Am schnellsten geht es über diesen Button. Er öffnet BavarianData direkt in
HACS auf deinem eigenen Home Assistant:

[![Öffne deine Home-Assistant-Instanz und BavarianData in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=JustChr&repository=BavarianData&category=integration)

Oder von Hand:

1. Öffne **HACS** und suche nach **BavarianData**.
2. Öffne **BavarianData: Connect Home Assistant to BMW CarData** und installiere
   es.
3. **Starte Home Assistant neu.**

> HACS-Nutzer bekommen Updates nur über **GitHub-Releases**, nicht bei jedem
> Push auf `main`. Neue Versionen findest du auf der Releases-Seite.

## Wenn es in der Suche nicht auftaucht

Neu aufgenommene Integrationen brauchen eine Weile, bis sie jede HACS-Instanz
erreichen. Aktualisiere die HACS-Daten (**⋮ → Daten neu laden** oder Home
Assistant neu starten) und suche erneut. Siehst du es dann immer noch nicht,
installiere es als benutzerdefiniertes Repository — es ist dieselbe Integration
mit denselben Updates:

1. Öffne in HACS **⋮ → Benutzerdefinierte Repositories**.
2. Füge `https://github.com/JustChr/BavarianData` mit der Kategorie
   **Integration** hinzu.
3. Installiere es und starte Home Assistant neu.

## Das fehlende Symbol in HACS

In der Liste von HACS selbst steht neben BavarianData kein Logo. HACS zeigt
keine Logos an, die eine Integration selbst mitbringt; die Installation ist
nicht kaputt. Überall sonst in Home Assistant erscheint das Symbol richtig, und
bei dir gibt es nichts zu beheben.

**Weiter:** [3. Integration hinzufügen und autorisieren →](DE-Getting-Started-3-Add-and-Authorize)
