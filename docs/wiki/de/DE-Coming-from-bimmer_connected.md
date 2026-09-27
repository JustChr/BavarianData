# Umstieg von bimmer_connected (MyBMW / ConnectedDrive)

> 🇬🇧 [English version](Coming-from-bimmer_connected)

Wenn dein BMW oder MINI früher über die Integration **BMW Connected Drive** in
Home Assistant erschien und seit Herbst 2025 nur noch `nicht verfügbar` zeigt,
ist diese Seite für dich. Sie erklärt, was sich geändert hat, was an die Stelle
tritt und wie du Dashboards und Automationen umziehst.

## Was passiert ist

<a id="what-happened"></a>

Die Connected-Drive-Integration beruhte auf der Bibliothek `bimmer_connected`,
die mit den Servern hinter der MyBMW-App sprach. Am **29. September 2025** hat
BMW Drittanbieter für genau diese Anfragen gesperrt. Das README der Bibliothek
bezeichnet sie inzwischen selbst als veraltet und funktionslos, und keine
Einstellung und kein Update auf deiner Seite bringt sie zurück.

## Was stattdessen kommt: BMW CarData

<a id="what-replaces-it-bmw-cardata"></a>

**BMW CarData** ist BMWs eigener, offizieller Weg, mit dem du als Kundin oder
Kunde die Daten deines Autos abrufst. Du legst im BMW- (oder MINI-)Portal eine
persönliche **Client-ID** an, autorisierst sie einmal, und ab dann kommen die
Daten auf zwei Wegen zu dir:

- ein **Live-MQTT-Stream**, der jeden Wert wenige Sekunden nach dem Senden durch
  das Auto liefert — Ladezustand, Türen, Fenster, Reifendrücke, Position,
  Fahrtdaten;
- eine **REST-API** für Abfragen bei Bedarf, begrenzt auf 50 Anfragen pro
  24 Stunden.

BavarianData ist eine Home-Assistant-Integration, die mit beiden direkt spricht.
Kein Server dazwischen: Home Assistant hält die Tokens und ist der einzige
Client. Installiert wird sie aus dem **HACS-Standard-Store**.

## Was sich für dich ändert

<a id="what-changes-for-you"></a>

| | Connected Drive (bimmer_connected) | BavarianData (BMW CarData) |
| --- | --- | --- |
| Stand | Seit 29.09.2025 von BMW gesperrt | Nutzt BMWs offiziellen Kundendaten-Dienst |
| Anmeldung | MyBMW-Benutzername und Passwort | Eine persönliche Client-ID, einmal im Browser autorisiert — Home Assistant speichert kein Passwort |
| Datenfluss | Alle paar Minuten abgefragt | Live über MQTT geliefert |
| Fernbefehle (Verriegeln, Klima, Lichthupe, Laden starten) | Ja | **Nein** — CarData ist nur lesend |
| Wo es funktioniert | Überall, wo die MyBMW-App lief | Wo BMW das CarData-Portal anbietet ([mehr](DE-Getting-Started-1-BMW-Portal-Setup)) |
| Entitätsnamen | Eigene | Neue — Automationen müssen umgestellt werden |

Der einzige echte Verlust sind **Fernbefehle**. CarData hat überhaupt keine
Befehlsschnittstelle, also kann keine CarData-basierte Integration das Auto
verriegeln oder die Klimatisierung starten. Dafür bleibt die MyBMW-App.

Was du zusätzlich zu den Rohdaten bekommst: einen [Ladeverlauf mit
Kosten](DE-Feature-Charging-History-and-Cost), ein
[Fahrtenbuch](DE-Feature-Trips), den gelernten
[Batteriezustand](DE-Feature-Battery-Health), eine aus deinem eigenen Fahren
gemessene [reale Reichweite](DE-Feature-Efficiency-and-Range),
[Statistiken fürs Energie-Dashboard](DE-Feature-Energy-and-Statistics),
[CSV-/HTML-Export](DE-Feature-Export) und eine [evcc-/Wallbox-Brücke](DE-Feature-evcc-and-Wallbox-Bridge).
Alles wird aus dem Stream berechnet und verbraucht kein API-Kontingent.

## Umziehen

<a id="moving-across"></a>

1. **Alte Integration entfernen.** Unter **Einstellungen → Geräte & Dienste**
   den Eintrag *BMW Connected Drive* löschen. Er bekommt keine Daten mehr, und
   das Entfernen macht die Geräte- und Entitätsnamen frei.
2. **BavarianData einrichten** — den vier Einrichtungsschritten folgen,
   beginnend mit der [Einrichtung im BMW-Portal](DE-Getting-Started-1-BMW-Portal-Setup).
   Der Großteil der Arbeit passiert im BMW-Portal.
3. **Die Dashboard-Karte hinzufügen.** Siehe [Die Dashboard-Karte](DE-The-Dashboard-Card).
4. **Automationen umstellen.** Durchsuche Automationen, Skripte und Dashboards
   nach den alten Entitäts-IDs und setze die neuen ein. Die Tabelle unten nennt
   die üblichen Entsprechungen; [Entitäten & Geräte](DE-Feature-Entities-and-Devices)
   listet alles auf.

### Übliche Entsprechungen

<a id="common-equivalents"></a>

| Bisher (Connected Drive) | Jetzt (BavarianData) |
| --- | --- |
| Verbleibender Akkustand | **Ladestatus der Hochvoltbatterie** |
| Elektrische Reichweite | **Verbleibende elektrische Reichweite in km** — oder **Reale Reichweite**, aus deinem eigenen Fahren gemessen |
| Kilometerstand | **Kilometerstand** |
| Ladestatus | **Ladestatus** |
| Ladeziel | **Zielwert des Ladezustands der Hochvoltbatterie** |
| Stecker-/Verbindungsstatus | **Status der Ladesteckverbindung** |
| Verriegelungsstatus | **Zustand der Türen** — folgt jedem Ver- und Entriegeln; siehe [welche Sperr-Entität](DE-Feature-Entities-and-Devices#which-lock-entity-to-use) |
| Türen, Fenster, Klappen | Je ein Binärsensor pro Tür, Fenster, Motorhaube und Heckklappe |
| Check-Control-Meldungen | **Check Control Meldungen** |
| Condition Based Services | **Condition Based Service** |
| Device Tracker | **Standort** (`device_tracker`) |

## Häufige Fragen

<a id="questions-people-ask"></a>

**Ist das die alte Integration unter neuem Namen?** Nein. Es ist eine eigene
Integration, von Grund auf auf einem anderen BMW-Dienst gebaut, deshalb werden
deine alten Entitäts-IDs nicht übernommen.

**Funktioniert es mit MINI?** Ja. MINI nutzt denselben CarData-Dienst über das
MINI-Portal.

**Funktioniert es außerhalb Europas?** Überall dort, wo BMW das CarData-Portal
für dein Konto anbietet. Noch nicht jeder Markt hat es, die Client-ID gilt aber
für das ganze Konto — Details in [Schritt 1](DE-Getting-Started-1-BMW-Portal-Setup).

**Kann ich es neben einer anderen CarData-Integration betreiben?** BMW erlaubt
**einen Stream pro Konto** gleichzeitig; zwei Stream-Clients am selben Konto
werfen sich also abwechselnd hinaus. Entscheide dich für einen.
