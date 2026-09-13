# 1. BMW CarData im Portal einrichten

> 🇬🇧 [English version](Getting-Started-1-BMW-Portal-Setup)

Erledige das, **bevor** du die Integration in Home Assistant hinzufügst.

BMW CarData ist BMWs eigener Telematikdienst. Dafür brauchst du eine im
BMW-Portal erzeugte **Client-ID** mit zwei freigegebenen Berechtigungen (Scopes).
Das CarData-Portal wird nicht in jedem Land angeboten, die Client-ID gilt aber
**kontoweit**: Du kannst die Einrichtung in jeder unterstützten Region erledigen
und die ID überall verwenden, für jedes Fahrzeug im Konto.

## Voraussetzungen

<a id="requirements"></a>

- Ein BMW-Konto mit einem Fahrzeug, das CarData unterstützt.
- **CarData API** und **CarData Streaming** im BMW-Portal abonniert.
- Ein **Auto**. BMW-Motorräder (BMW Motorrad) werden in CarData zwar geführt,
  BMW streamt für sie aber keine Daten
  ([mehr](DE-Troubleshooting-and-FAQ#does-bavariandata-work-with-a-bmw-motorcycle)).

Es hilft, vorher einmal
[BMWs CarData-Dokumentation](https://bmw-cardata.bmwgroup.com/customer/public/api-documentation/Id-Introduction)
zu überfliegen — die Schritte unten folgen ihr.

## Das CarData-Portal öffnen

Melde dich in deinem BMW-Konto an und öffne **Meine Fahrzeuge →
Fahrzeugübersicht**:

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bmw-portal-account.png" alt="Startseite des MyBMW-Kontos; unter 'My Vehicles' ist der Link 'Vehicle overview' hervorgehoben" width="760" />
</p>

Direktlinks zur Fahrzeugübersicht je Markt:

|       | Englisch | Deutschland | Österreich |
| ----- | -------- | ----------- | ---------- |
| BMW   | [vehicle overview](https://www.bmw.co.uk/en-gb/mybmw/vehicle-overview) | [Fahrzeugübersicht](https://www.bmw.de/de-de/mybmw/vehicle-overview) | [Fahrzeugübersicht](https://www.bmw.at/de-at/mybmw/vehicle-overview) |
| Mini  | [vehicle overview](https://www.mini.co.uk/en-gb/mymini/vehicle-overview) | [Fahrzeugübersicht](https://www.mini.de/de-de/mymini/vehicle-overview) | [Fahrzeugübersicht](https://www.mini.at/de-at/mymini/vehicle-overview) |

## Schritte

1. Wähle dein Fahrzeug und öffne die Kachel **BMW CarData** / **Mini CarData**.

   <p align="center">
     <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bmw-portal-vehicle-overview.png" alt="Fahrzeugübersicht eines BMW i5 eDrive40 mit der Kachel 'BMW CarData' zwischen den anderen Fahrzeugkacheln" width="760" />
   </p>

2. Klicke auf **CarData Client erstellen** (*Create CarData Client*), um eine
   Client-ID zu erzeugen:

   <p align="center">
     <img src="https://cd-prd-euc1-cardata-public-s3.s3.eu-central-1.amazonaws.com/customer-portal/b2c/2_1_Create-Client.png" alt="Bildschirm 'Technical access to BMW CarData' mit hervorgehobener Schaltfläche 'Create CarData Client'" width="760" />
   </p>

3. Gib dem Client **beide** Berechtigungen — `cardata:api:read` und
   `cardata:streaming:read` — und bestätige. Auf dem Bildschirm **Technischer
   Zugriff auf BMW CarData** sollte am Ende eine **Client-ID** stehen und sowohl
   **Zugriff auf CarData API anfragen** als auch **CarData Stream** eingeschaltet
   sein.

   <p align="center">
     <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bmw-portal-cardata-client.png" alt="Bildschirm 'Technical access to BMW CarData' mit der erzeugten Client-ID (geschwärzt), beiden eingeschalteten Schaltern für CarData API und CarData Stream sowie den Schaltflächen Delete Client und Authenticate device" width="760" />
   </p>

   > Meldet das Portal einen Scope-Fehler, lade die Seite neu, füge eine
   > Berechtigung hinzu, warte etwa 30 Sekunden und füge dann die zweite hinzu.

Mehr brauchst du im Portal vorerst nicht.

> **Hake unter Datenauswahl noch nichts an.** Welche Deskriptoren gestreamt
> werden, wählst du nach der Installation in Home Assistant aus
> ([Schritt 4](DE-Getting-Started-4-Choose-Data)). Die **geführte** Einrichtung
> schaltet sie mit einem Ein-Klick-Lesezeichen **Activate BMW data** für dich ein,
> und die **manuelle** Einrichtung nutzt dasselbe Lesezeichen für die
> Datengruppen, die du auswählst. Von Hand hieße das, sich durch Hunderte
> technischer Felder zu klicken — also lass es.

Das ist die ganze Portal-Einrichtung. In
[Schritt 3](DE-Getting-Started-3-Add-and-Authorize) lässt du diese Client-ID
entweder vom **geführten** Weg finden oder fügst sie auf dem **manuellen** Weg
selbst ein — halte die Client-ID also bereit, wenn du manuell vorgehen willst.

**Weiter:** [2. Installation über HACS →](DE-Getting-Started-2-Install)
