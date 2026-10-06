# Welche Autos funktionieren

> 🇬🇧 [English version](Supported-Cars)

BavarianData hat keine Modellliste und keinen modellspezifischen Code. Es
funktioniert mit dem, was BMW CarData für dein Auto liefert. Die Frage ist also,
ob **dein Auto CarData hat**, und wie viel es sendet.

## Der schnelle Test

<a id="the-quick-check"></a>

Öffne dein Auto im BMW- oder MINI-Portal (**Meine Fahrzeuge → Fahrzeugübersicht**,
siehe [Schritt 1](DE-Getting-Started-1-BMW-Portal-Setup#open-the-cardata-portal)).
Gibt es dort eine Kachel **BMW CarData** oder **Mini CarData**, funktioniert
BavarianData mit diesem Auto.

Die einzige Ausnahme ist **BMW Motorrad**: Motorräder werden in CarData geführt,
BMW streamt für sie aber keine Daten
([mehr](DE-Troubleshooting-and-FAQ#does-bavariandata-work-with-a-bmw-motorcycle)).

## Was du bekommst, nach Antrieb

<a id="what-you-get-by-drivetrain"></a>

Die Integration erkennt den Antrieb an dem, was das Auto meldet, und passt die
[Dashboard-Karte](DE-The-Dashboard-Card) und die abgeleiteten Sensoren daran an.

| Antrieb | Was funktioniert |
| --- | --- |
| **Elektrisch** | Alles: Ladestand und Reichweite live, [Ladeverlauf und Kosten](DE-Feature-Charging-History-and-Cost), [Batteriezustand](DE-Feature-Battery-Health), [Effizienz und echte Reichweite](DE-Feature-Efficiency-and-Range), die [evcc-Brücke](DE-Feature-evcc-and-Wallbox-Bridge), [Fahrten](DE-Feature-Trips). |
| **Plug-in-Hybrid** | Alles oben außer Verbrauch, echter Reichweite und Ladekosten pro 100 km; das gibt der Kilometerstand bei einem Auto, das auch mit Kraftstoff fährt, nicht her ([warum](DE-Feature-Efficiency-and-Range#plug-in-hybrids)). Dazu Tankstand und kombinierte Reichweite. |
| **Benzin / Diesel** | Tankstand, Reichweite, Kilometerstand, Türen, Fenster und Schlösser, Reifendruck, Service- und Check-Control-Meldungen, Standort und Fahrten. |

## Von Besitzern bestätigte Autos

<a id="cars-owners-have-confirmed"></a>

Diese Modelle laufen laut Berichten in Issues und Diskussionen. Jedes Auto mit
CarData sollte funktionieren; die Liste zeigt nur, was schon gesehen wurde.

| Auto | Antrieb | Gut zu wissen |
| --- | --- | --- |
| BMW i5 eDrive40 | Elektrisch | Das Auto des Maintainers. Jede Funktion wird darauf entwickelt und getestet. |
| BMW i4 M60 xDrive, i4 eDrive35 | Elektrisch | Versionen vor 0.9.15 konnten einen i4 für einen Hybrid halten ([#53](https://github.com/JustChr/BavarianData/issues/53)). |
| BMW iX3 (neue Generation) | Elektrisch | BMW streamt zusätzlich zur echten Einstellung immer wieder ein Ladeziel von 100 % ([#52](https://github.com/JustChr/BavarianData/issues/52)). |
| BMW i3s 120 Ah | Elektrisch | — |
| BMW X3 30e xDrive | Plug-in-Hybrid | Streamt den Ladestand nicht; er kommt mit der [täglichen REST-Aktualisierung](DE-Feature-API-Quota#the-daily-refresh). |
| BMW X3 M40d (G01) | Diesel | — |
| BMW M240i xDrive (G42) | Benzin | Vor 0.9.7 verschob sich der Reifendruck nach Neustarts ([#7](https://github.com/JustChr/BavarianData/issues/7)). |
| BMW 530d Touring (G31, iDrive 6) | Diesel | Sendet Kilometerstand, Tankstand und Position nur am Ende einer Fahrt; das Fahrtenbuch bleibt deshalb leer ([mehr](DE-Feature-Trips#older-cars-that-report-only-at-the-end-of-a-trip)). |
| MINI (Benziner) | Benzin | Über das MINI-Portal eingerichtet. Sendet weniger Felder als ein BMW, zum Beispiel keine Zähler für den Kraftstoffverbrauch über die Lebensdauer. |

## Was sich von Auto zu Auto unterscheidet

<a id="what-differs-from-car-to-car"></a>

- **Welche Felder ankommen.** BMWs Katalog hat [Hunderte Deskriptoren](DE-Reference),
  und kein Auto sendet alle. Mit
  [`get_coverage_report`](DE-Services-Reference#get_coverage_report) siehst du,
  welche der ausgewählten dein Auto tatsächlich liefert.
- **Wie oft.** Manche Autos senden einen Wert nur, wenn er sich ändert, andere
  wiederholen im Wachzustand alles etwa alle halbe Minute. Ein geparktes Auto
  kann stundenlang still sein; das ist normal.
- **Während der Fahrt.** Die meisten Autos streamen beim Fahren Position und
  Kilometerstand; daraus entsteht das [Fahrtenbuch](DE-Feature-Trips). Manche
  älteren melden sich erst am Ende.

## Dein Auto fehlt?

<a id="your-car-isnt-listed"></a>

Es funktioniert mit ziemlicher Sicherheit trotzdem. Sobald es läuft, schreib es
in die [Diskussionen](https://github.com/JustChr/BavarianData/discussions), mit
Modell und Antrieb, damit der nächste Besitzer dieses Autos es hier findet. Sieht
etwas falsch aus, eröffne ein [Issue](https://github.com/JustChr/BavarianData/issues)
und hänge einen [Diagnose-Download](DE-Troubleshooting-and-FAQ#download-diagnostics)
an: Er zeigt, was dein Auto sendet.
