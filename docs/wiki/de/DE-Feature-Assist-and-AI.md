# Assist & KI nach dem Auto fragen

> 🇬🇧 [English version](Feature-Assist-and-AI)

Frag den Sprachassistenten von Home Assistant in ganz normalen Worten nach
deinem BMW oder MINI. Mit einem KI-Gesprächsagenten hinter Assist (OpenAI,
Google, Anthropic oder Ollama) beantwortet Assist Fragen wie:

- *„Muss ich laden, bevor ich nach München und zurück fahre?“*
- *„Ist das Auto abgeschlossen?“* / *„Habe ich ein Fenster offen gelassen?“*
- *„Was hat das Laden diesen Monat gekostet?“*
- *„Wie weit bin ich letzten Monat gefahren, und wie viel davon dienstlich?“*
- *„Liege ich beim Leasing im Plan?“*
- *„Wie weit komme ich gerade wirklich?“*

BavarianData bringt dafür eigene Werkzeuge mit, in Home Assistants Sprache eine
*LLM-API*. Sie lesen dasselbe wie die Karte und kosten **kein
API-Kontingent**. Sie funktionieren per Sprache, im Assist-Chat und aus
KI-Apps am Rechner.

## Einschalten

Schalte die Werkzeuge einmal ein: deine KI-Integration unter **Einstellungen →
Geräte & Dienste** öffnen, ihren Gesprächsagenten **konfigurieren** und unter
*Home Assistant steuern* neben *Assist* auch **BavarianData** ankreuzen.
Freigeben musst du nichts, Skripte schreiben auch nicht.

### KI-Apps am Rechner (MCP)

In der Integration *Model Context Protocol Server* von Home Assistant triffst
du dieselbe Wahl. Kreuze auch dort **BavarianData** an, dann kann eine KI-App,
die MCP spricht (etwa Claude Desktop), über dein Home Assistant nach dem Auto
fragen.

## Die Werkzeuge

| Werkzeug | Beantwortet |
|---|---|
| Fahrzeugstatus | Akku, Laden (Leistung, Minuten bis zum Ziel), Reichweite (beim Plug-in-Hybrid elektrisch, mit Kraftstoff und gesamt), Tankfüllstand, die Zone, in der es steht, ob es gerade fährt, alles, was offen oder unverriegelt ist |
| Reichweitencheck | Ob eine Fahrt in die Restreichweite passt, einfach oder hin und zurück, mit Sicherheitsabstand (ein Zehntel der Reichweite eines vollen Akkus, mindestens 20 km): Der Agent nennt dir die Reichweite, die nach der Fahrt bleibt, und wie viel mehr Ladung sie braucht, wenn sie nicht passt. Ein Plug-in-Hybrid oder Verbrenner zählt seinen Tank mit (Abstand 20 km), ein Hybrid sagt außerdem, ob der Akku allein reicht. Die Strecke schätzt der Agent aus den Orten, die du nennst. |
| Ladeverlauf | Die Ladevorgänge der letzten Tage (30, wenn du nichts anderes sagst): wann, wo, Akku vorher und nachher, Energie und Kosten, mit Summen |
| Fahrmonat | Strecke, Fahrten, die Aufteilung dienstlich/privat/Arbeitsweg, Verbrauch, Kosten und die häufigsten Ziele, im Vergleich zum Vormonat |
| Kilometerstand | Tachostand, dieser Monat und dieses Jahr, die Hochrechnung aufs Jahr und dein Leasing- oder Jahreskontingent, falls eingestellt |
| Effizienz | Gemessener Verbrauch, die echte Reichweite daraus, der Vergleich mit dem Wert des Autos und der Verlauf nach Monaten |

Gibt es eine [gemessene Reichweite](DE-Feature-Efficiency-and-Range), nehmen
die Werkzeuge sie, sonst die des Autos. Die Kraftstoffreichweite eines
Plug-in-Hybrids ist seine Gesamtreichweite minus die elektrische, denn BMW
schickt keine eigene. Antworten kommen in der Entfernungseinheit deines Autos
(die [Einstellung Einheiten](DE-Settings-Reference#einheiten)). Mit mehreren
Autos fragst du eines beim Namen ab oder alle zusammen.

## Was die KI sieht

Die KI sieht die Werte oben und Ortsnamen (deine Zonen, oder die Adresse, an
der eine Fahrt außerhalb davon endete). Nie sieht sie die FIN, die Position des
Autos, Fahrt-IDs oder Ladekurven; die werden aus jeder Antwort entfernt. Am
Auto können die Werkzeuge nichts tun, denn CarData liest nur: Die KI kann es
weder verriegeln noch die Klimatisierung oder das Laden starten. Mit einem
Cloud-Agenten verlassen diese Werte dein Zuhause. Ein lokaler Agent wie Ollama
behält sie dort.

*Du hast die drei `car_…`-Skripte aus einer früheren Fassung der Seite
[Ereignisse & Blueprints](DE-Feature-Automations) eingerichtet?* Du kannst sie
löschen. Die Werkzeuge beantworten alles, was sie konnten.
