# 3. Integration hinzufügen und autorisieren

> 🇬🇧 [English version](Getting-Started-3-Add-and-Authorize)

Die Vorbereitung im Portal aus [Schritt 1](DE-Getting-Started-1-BMW-Portal-Setup)
muss erledigt sein: ein **CarData Client**, der für **beide** Dienste, CarData API
und CarData Streaming, freigeschaltet ist. Das gilt für **beide** Wege unten —
der geführte Weg erspart dir nur das Abtippen der Client-ID, nicht das Anlegen
des Clients.

Einrichtung starten:

1. **Einstellungen → Geräte & Dienste → Integration hinzufügen → BavarianData:
   Connect Home Assistant to BMW CarData**.
2. Wähle einen Weg — **geführt** oder **manuell**:

   <p align="center">
     <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-setup-chooser.png" alt="Erster Bildschirm: Hinweis, einen CarData Client mit beiden Diensten anzulegen, dann die Wahl zwischen geführter Einrichtung (empfohlen) und dem manuellen Einfügen der Client-ID" width="520" />
   </p>

## Geführt oder manuell — was wählen?

Beide Wege brauchen denselben Client im Portal und enden gleich
(Geräteautorisierung → ein laufender Stream). Sie unterscheiden sich nur darin,
**wie die Client-ID hineinkommt** und **wann der Stream eingeschaltet wird**:

| | **Geführt** (empfohlen) | **Manuell** |
| --- | --- | --- |
| Client-ID | wird für dich gefunden | du kopierst und fügst sie ein |
| Stream einschalten | ein Klick — ein Lesezeichen **Activate BMW data**, das du im Portal ausführst, vor der Autorisierung | dasselbe Lesezeichen, nach der Autorisierung und der Auswahl der Datengruppen |
| Welche Felder eingeschaltet werden | ein sinnvoller **Standard**-Satz (später feinjustierbar) | **genau** die Datengruppen, die du anhakst |
| Am besten, wenn | es einfach ohne Kopieren laufen soll | du die Datengruppen gleich zu Beginn wählen willst |

Die gestreamten Datengruppen kannst du danach jederzeit unter
**Konfigurieren → Gestreamte Daten auswählen** ändern, das wieder denselben
Ein-Klick-Aktivator nutzt — siehe [Schritt 4](DE-Getting-Started-4-Choose-Data).

## Geführte Einrichtung

<a id="guided-path"></a>

1. Wähle **Geführte Einrichtung (empfohlen)**. Home Assistant stellt eine kleine
   Aktivierungsseite bereit. **Ziehe die Schaltfläche *Activate BMW data* in deine
   Lesezeichenleiste** (einmalig):

   <p align="center">
     <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-setup-bookmarklet.png" alt="Die bereitgestellte Aktivierungsseite mit dem ziehbaren Lesezeichen 'Activate BMW data' und einer Konsolen-Alternative" width="600" />
   </p>

2. Öffne das **BMW- oder MINI-Portal**, melde dich an, gehe zur
   **Stream-Setup**-Seite eines Fahrzeugs (BMW CarData → **Datenauswahl ändern**)
   und klicke auf das Lesezeichen **Activate BMW data**. Auf dieser Seite läuft es:

   <p align="center">
     <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bmw-portal-stream-setup.png" alt="Stream-Setup-Seite von BMW CarData ('Selection of vehicle data') mit den streambaren technischen Deskriptoren eines i5 eDrive40" width="760" />
   </p>

   Das Lesezeichen findet deine Client-ID, prüft das Fahrzeug und schaltet die
   Standard-Datenfelder ein — **alles in deinem eigenen Browser**, kein Passwort
   und keine Sitzung verlässt ihn. Zuerst liest es die verfügbaren Felder …

   <p align="center">
     <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bmw-portal-bookmarklet-running.png" alt="Dieselbe Portalseite mit der Fortschrittsmeldung 'BavarianData — Reading available fields…' oben rechts" width="760" />
   </p>

   … und meldet dann, wie viele aktiv sind:

   <p align="center">
     <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bmw-portal-bookmarklet-done.png" alt="Portalseite mit der Meldung 'BavarianData — 228 fields active' und einem Feld 'Copy this and paste it into Home Assistant' mit Copy-Schaltfläche" width="760" />
   </p>

   Zurück in Home Assistant wartet der geführte Bildschirm auf dieses Ergebnis:

   <p align="center">
     <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-setup-guided.png" alt="Geführter Aktivierungsbildschirm: Lesezeichen im Portal ausführen, danach macht Home Assistant von allein weiter oder du fügst das kurze Ergebnis ein" width="520" />
   </p>

   - Bei einem Home Assistant mit **https** meldet der Aktivator sich zurück und
     die Einrichtung **läuft von allein weiter**.
   - Bei einem Home Assistant mit **http** klickst du im angezeigten Feld des
     Aktivators auf **Copy** (siehe oben) und **fügst** das kurze (nicht geheime)
     Ergebnis in das Feld ein.

3. Weiter mit der **Geräteautorisierung** (unten). Ist sie abgeschlossen, werden
   die Standard-Datengruppen bereits gestreamt — feinjustieren kannst du sie
   jederzeit unter **Konfigurieren → Gestreamte Daten auswählen**.

## Manuelle Einrichtung

1. Wähle **Ich füge die Client-ID selbst ein** und füge die im Portal kopierte
   **Client-ID** ein.

   <p align="center">
     <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-setup-manual.png" alt="Manueller Weg: Zusammenfassung der Portal-Einrichtung und ein Feld für die CarData Client-ID" width="520" />
   </p>

2. Weiter mit der **Geräteautorisierung** (unten).
3. Ist die Autorisierung erfolgreich, geht es direkt zur **Auswahl der
   Datengruppen**. Wähle deine Datengruppen und führe dann das Lesezeichen
   **Activate BMW data** genau wie auf dem [geführten Weg](#guided-path) aus.
   Weiter mit [Schritt 4](DE-Getting-Started-4-Choose-Data).

## Geräteautorisierung (beide Wege)

Home Assistant zeigt einen **Link und einen Code**. Öffne den Link, melde dich an
und bestätige das Gerät auf BMWs Seite. Sobald BMW die Bestätigung annimmt,
**läuft der Dialog von allein weiter** — in Home Assistant ist nichts anzuklicken.
Läuft der Code ab, klicke auf **Absenden**, um einen neuen zu bekommen, und
versuche es erneut.

## Wenn die Einrichtung mit „access denied“ scheitert

BMWs Autorisierungs-Backend ist manchmal unzuverlässig und kann *access denied*
melden, obwohl deine Anmeldung offensichtlich geklappt hat und du nie eine
Zustimmungsseite gesehen hast. **Das ist eine bekannte Eigenart auf BMW-Seite,
kein Fehler der Integration**, und es gibt einen verlässlichen Umweg — siehe
[Fehlerbehebung → „access denied“](DE-Troubleshooting-and-FAQ#onboarding-fails-with-access-denied).

## Später neu autorisieren

Macht BMW das Token später ungültig, führe **Konfigurieren → Neu bei BMW
autorisieren** aus. Die Integration mit derselben Client-ID zu entfernen und neu
hinzuzufügen funktioniert auch — der alte Eintrag wird automatisch aufgeräumt.

**Weiter:** [4. Gestreamte Daten auswählen →](DE-Getting-Started-4-Choose-Data)
