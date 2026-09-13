# Fehlerbehebung & FAQ

> 🇬🇧 [English version](Troubleshooting-and-FAQ)

## Die Einrichtung scheitert mit „access denied“

<a id="onboarding-fails-with-access-denied"></a>

BMWs Backend für die Geräteautorisierung kann `access_denied` („The user has declined
authorization“) melden, **obwohl du nie eine Zustimmungsseite gesehen hast und die
Anmeldung offensichtlich geklappt hat**. Das ist Unzuverlässigkeit auf BMW-Seite,
nicht die Integration — der BMW-Support hat (auf ein Ticket hin) bestätigt, dass der
Device-Code-Ablauf von einem internen Partnersystem mit „Synchronisationsproblemen“
abgewickelt wird. Die Integration kann daran nichts ändern.

> **Update, 28.07.2026 — laut BMW behoben.** BMW hat mehreren Projekten mitgeteilt,
> dass das Synchronisationsproblem des Partnersystems gelöst ist, und das Nutzern mit
> Tickets per E-Mail bestätigt. Die meisten Betroffenen kamen danach sofort durch.
> **Betroffene Clients werden nicht an Ort und Stelle repariert**: BMWs Anweisung ist,
> **den CarData Client im Portal zu löschen, einen neuen anzulegen** (beide Abos
> anhaken) und den Device-Ablauf mit der neuen Client-ID erneut zu durchlaufen. Hängst
> du seit Wochen fest, mach das zuerst — vor dem Ritual unten.

Klappt es trotzdem nicht, lag es wiederholt an zwei Dingen außerhalb des Ablaufs:

- **Datenschutzeinstellungen der MyBMW App.** Mehrere kamen erst durch, nachdem sie
  die Datenschutz- bzw. Datenfreigabe-Einstellungen des Profils in der MyBMW App auf
  **alle** gesetzt und das Auto **einmal gestartet** hatten, damit die Änderung
  übernommen wird. Danach kannst du sie wieder zurücknehmen.
- **Werbeblocker.** AdGuard, Pi-hole & Co. können die Bestätigungsseite des Portals
  kaputt machen. Schalte sie für den Versuch ab.

Darüber hinaus kann es ein paar Anläufe brauchen. Diese Abfolge hat bei anderen
(mehrfach) verlässlich funktioniert:

1. Öffne ein **neues Inkognito- bzw. privates Browserfenster**.
2. Gehe **von Hand** ins My-BMW-/CarData-Portal — **nicht** über den vorausgefüllten
   Link, und entferne ein etwaiges `?user_code=…` aus der Adresse.
3. Melde dich an, öffne **Gerät authentifizieren**, **tippe den Code von Hand ein**
   (achte darauf, dass kein Banner „falscher Code“ erscheint) und bestätige.
   Meist landest du auf einer Seite „Login erfolgreich“ bzw. „im Auto fortfahren“ —
   **im Auto musst du nichts tun**; die Seite ist nur eine Bestätigung. Geh direkt
   zurück zu Home Assistant und klicke auf **Absenden**.
4. Ist der Code inzwischen abgelaufen, klicke in Home Assistant auf **Absenden** für
   einen neuen Code und wiederhole.
5. Nach mehreren Versuchen immer noch nichts? **Lösche den Client** im BMW-Portal,
   lege einen neuen an (beide Abos anhaken) und autorisiere mit der neuen Client-ID
   erneut.

Hilft nichts davon, **hör auf, es erneut zu versuchen, und warte**. Das ist auf
BMW-Seite wirklich eine Lotterie. Viele kamen erst durch, nachdem sie es mehrere
Stunden bis ein paar Tage ruhen ließen (manchmal geht es über Nacht ohne weiteres
Zutun); schnelle Wiederholungen scheinen nicht zu helfen. Einen Trick, der bei allen
funktioniert, gibt es nicht. Für die hartnäckigsten Fälle bleibt nur der BMW-Support:
**bmwcardata-b2c-support@bmwgroup.com** (mit der Referenz-ID aus der Fehlermeldung,
falls BMW eine gezeigt hat).

## Es kommen keine Daten an

<a id="no-data-arriving"></a>

1. Stelle sicher, dass du [Schritt 4](DE-Getting-Started-4-Choose-Data)
   abgeschlossen hast — die Deskriptoren müssen im Portal eingeschaltet **und
   gespeichert** sein.
2. Löse in der MyBMW App ein Ver- oder Entriegeln aus, damit das Auto eine
   Aktualisierung sendet.
3. Führe **`bavariandata.get_coverage_report`** aus
   ([Dienste](DE-Services-Reference#get_coverage_report)) — er sagt dir genau, welche
   erwarteten Deskriptoren nicht angekommen sind, sodass du ein Auswahlproblem von
   einem „das Auto sendet es nicht“-Problem unterscheiden kannst. Eine Datengruppe, von
   der *einige* Felder ankommen, ist gesund; eine, von der keines ankommt, ist das
   Auswahlproblem.

Streamt **48 Stunden** lang nichts, erscheint unter **Einstellungen → Reparaturen**
ein Hinweis, der hierher verweist.

## Ein Sensor zeigt einen absurden Wert oder nach einem Update „unbekannt“

<a id="sensor-value-looks-wrong"></a>

**Hast du die Einheit in den Einstellungen einer Entität geändert?** Versionen bis
0.9.6 hatten einen Fehler, bei dem diese Einheit bei jedem Neustart von Home Assistant
erneut angewandt wurde. Ein Reifendruck, den das Auto in kPa meldet, aber in bar
angezeigt wurde, wurde jedes Mal durch 100 geteilt und lief gegen null —
`2.5e-10 bar` nach fünf Neustarts
([Issue #7](https://github.com/JustChr/BavarianData/issues/7)). Werte, die das Auto
selten sendet, wie der Reifendruck, traf es am schlimmsten, weil erst eine frische
Nachricht des Autos den Wert zurücksetzte. Installationen mit dem Einheitensystem
**US customary** waren betroffen, ohne etwas zu ändern, weil Home Assistant dort die
Anzeigeeinheit selbst wählt — Strecke, Geschwindigkeit, Temperatur, Druck und Volumen.

Aktualisiere auf die neueste Version. Ab dann wird der Wert in der Einheit gespeichert,
in der das Auto ihn sendet, und die Einheit, in der du ihn anzeigst, spielt dafür keine
Rolle.

**Warum er dann „unbekannt“ zeigt:** Ein bereits abgedrifteter Wert lässt sich nicht
retten, und das Update verwirft ihn bewusst, statt eine falsche Zahl zu behalten. Der
Sensor füllt sich, sobald das Auto diesen Wert das nächste Mal meldet — bei den meisten
sofort, beim Reifendruck am Ende deiner **nächsten Fahrt**. Der aufgezeichnete Verlauf
behält die alten falschen Werte; nur neue Messungen stimmen.

Anzeigen kannst du weiterhin jede Einheit, die du magst. **Einstellungen → Geräte &
Dienste → Entitäten →** die Entität **→ ⚙ → Maßeinheit** ist wieder sicher.

## „Status der Türen“ hat sich seit Tagen nicht geändert

Erwartet und kein Fehler. BMW legt `vehicle.cabin.door.lock.status` **nicht** auf den
Stream — er wird nur bei einem REST-Aufruf erneuert, zeigt also das Schloss zum
Zeitpunkt der letzten [täglichen Aktualisierung](DE-Feature-API-Quota#the-daily-refresh)
und kann Tage alt sein.

Nutze stattdessen **Zustand der Türen** (`vehicle.cabin.door.status`): Er wird gestreamt
und folgt jedem Ver- und Entriegeln innerhalb von Sekunden, mit denselben Werten
`Gesichert` / `Verriegelt` / `Teilweise verriegelt` / `Entriegelt`. Siehe
[Welche Verriegelungs-Entität verwenden](DE-Feature-Entities-and-Devices#which-lock-entity-to-use).

## Stream-Autorisierung schlägt fehl (MQTT rc=5)

<a id="stream-authorization-failing"></a>

Wird der Stream als **nicht autorisiert** (`MQTT rc=5`) abgelehnt und bleibt es dabei,
erscheint unter **Einstellungen → Reparaturen** ein Hinweis. Die Integration versucht es
zuerst selbst erneut und autorisiert sich neu; der Hinweis erscheint erst, wenn das eine
Weile nichts gebracht hat. Zwei übliche Ursachen:

1. **Veraltete Token.** Führe **Konfigurieren → Token jetzt erneuern** aus. Schlägt es
   weiter fehl, **Konfigurieren → Neu bei BMW autorisieren** (das Refresh-Token läuft
   etwa alle zwei Wochen ab — siehe [access denied](#onboarding-fails-with-access-denied),
   falls die Neuautorisierung selbst abgelehnt wird).
2. **Ein anderer Client hält den Stream.** BMW erlaubt nur
   [einen Stream pro Konto](#only-one-stream-per-account) — trenne jeden anderen
   CarData-Client und lade die Integration dann neu.

## Nur ein Stream pro Konto

<a id="only-one-stream-per-account"></a>

BMW erlaubt **nur einen gleichzeitigen Streaming-Client** pro Konto (GCID), es kann also
kein anderes Werkzeug zur selben Zeit verbunden sein. Läuft ein anderer CarData-Client,
trenne ihn.

## Das Auto lässt sich nicht steuern

**Nur lesend.** CarData kann keine Befehle senden, daher kann diese Integration das Auto
nicht verriegeln, vorklimatisieren oder anderweitig steuern. Automationen steuern externe
Geräte als Reaktion auf die Daten des Autos — siehe
[Ereignisse & Blueprints](DE-Feature-Automations).

## Funktioniert BavarianData mit einem BMW-Motorrad?

<a id="does-bavariandata-work-with-a-bmw-motorcycle"></a>

Nein. Laut BMWs CarData-Leitfaden ist Streaming für **BMW Motorrad** nicht verfügbar. Ein
Motorrad kann trotzdem dem Konto zugeordnet sein und die Einrichtung durchlaufen, sein
Gerät bliebe dann einfach leer. Erkennt die Integration in den Basisdaten des Fahrzeugs
ein Motorrad, zeigt sie unter **Einstellungen → Reparaturen** eine Warnung, die genau das
sagt. Auf deiner Seite gibt es nichts zu beheben; entferne den Eintrag, wenn zum Konto
kein Auto gehört.

## Diagnosedaten herunterladen

<a id="download-diagnostics"></a>

Der schnellste Weg von „es geht nicht“ zu einer Antwort. Gehe zu **Einstellungen →
Geräte & Dienste → BavarianData → ⋮ → Diagnosedaten herunterladen** und hänge die Datei
an dein Issue an.

Das kostet **kein API-Kontingent** — alles darin sind Zustände, die die Integration ohnehin
hat — und die Datei ist so gebaut, dass sie öffentlich gepostet werden kann: **FIN, GCID,
Client-ID, Token, MQTT-Topic und GPS-Koordinaten werden automatisch geschwärzt**.

Übrig bleibt genau das, was die üblichen Fehler voneinander unterscheidet:

| In der Datei | Beantwortet |
| --- | --- |
| Kontingent (verbraucht / übrig / nächstes Zurücksetzen) | Ob du deine 50 Anfragen / 24 h aufgebraucht hast. |
| Gewählte Datengruppen + Deskriptor-Abdeckung pro Auto | Ob die Datenauswahl wirklich gespeichert wurde und ob das Auto liefert, was du gewählt hast. |
| Ankünfte und letzter Zeitpunkt pro Deskriptor | Ob der Stream liefert, und was. |
| Verbindungsverlauf mit MQTT-`rc`-Codes | Autorisierungsfehler und ein anderer Client, der den einzigen Stream belegt. |
| Bootstrap-Zustand und deine Optionen | Eine nie abgeschlossene Einrichtung; eine Einstellung, die nicht ist, was du dachtest. |

## Debug-Protokollierung

<a id="debug-logging"></a>

Standardmäßig aus. Schalte sie unter **Konfigurieren → Debug-Protokollierung** ein und lade
neu. Sie ist ausführlich und kann Fahrzeugdaten wie **GPS und die vollständige FIN**
enthalten, lass sie also aus, solange du kein Problem jagst. (Getrennt vom allgemeinen
Log-Level, den Home Assistant pro Integration setzt.)

Normale Log-Zeilen — die ohne Debug geschriebenen — zeigen die FIN bis auf die letzten
vier Zeichen maskiert (`***1234`), was immer noch reicht, um beim Lesen zwei Autos
auseinanderzuhalten.

Nimm beim Melden eines Problems lieber die
[Diagnosedaten](#download-diagnostics): Sie enthalten mehr von dem, was gebraucht wird,
und weniger von dem, was nicht.

## Eine Fahrt für die Fahrterkennung aufzeichnen

<a id="capturing-a-drive-for-trip-detection"></a>

Werden Fahrten verpasst, geteilt oder falsch gemessen, liefert die Aufzeichnung einer
Fahrt uns die Rohdaten, um die Erkennung zu verbessern. Schalte **Fahrt-Diagnoseaufzeichnung**
unter **Konfigurieren → Fahrten** ein (unabhängig von der Debug-Protokollierung), mach eine
normale Fahrt, die das Problem zeigt, und **schalte sie danach wieder aus**.

Für die reichhaltigste Aufzeichnung **wähle vorher so viele Datenfelder wie möglich** in der
Datenauswahl des BMW-Portals ([Daten auswählen](DE-Getting-Started-4-Choose-Data)) —
besonders die Felder zu Navigation/GPS, Geschwindigkeit und Antrieb. Die Aufzeichnung kann
nur zeigen, was das Auto tatsächlich streamt, und ein Teil dessen, was wir suchen, ist,
welche zusätzlichen Signale (GPS-Fixqualität und Satellitenzahl, Richtung,
Geschwindigkeit, Zustand des Hochvoltsystems und von Tür/Schloss) dein Auto während der
Fahrt sendet.

Sie erzeugt zwei Dinge:

- **Log-Zeilen** mit Kennungen zum leichten Filtern — suche im Home-Assistant-Log nach
  `[trip.`:
  - `[trip.gps]` — jeder GPS-Punkt mit Schrittweite, Abstand zum vorigen Punkt,
    Stream-Verzögerung, Kilometerstand, Ladezustand, dem Countdown des Schließ-Timers,
    **und GPS-Fixzustand / Satellitenzahl / Richtung** (damit sich eine Phase ohne Bewegung
    von einem verlorenen Fix unterscheiden lässt).
  - `[trip.timer]` — wann der Stillstands-Timer scharf wird und auslöst.
  - `[trip.door]` — Verfeinerungen von Start und Ende über die Fahrertür (wenn das Auto
    die Tür streamt).
  - `[trip.seg]` — jeder Fahrtsegment-Batch von BMW vollständig, mit Zeitstempeln.
  - `[trip.watch]` — ein ausgewählter Satz möglicher „fährt es?“-Signale
    (Geschwindigkeit, Zustand des Hochvoltsystems, Zündung, Fahrertür, beide
    Deskriptoren der Zentralverriegelung, aktive Navigation), sobald das Auto sie streamt.
  - `[trip.raw]` — jeder andere Deskriptor, den eine Nachricht enthielt.
  - `[trip.post]` — eine Zusammenfassung pro geschlossener Fahrt (Strecken nebeneinander,
    Zahl der Punkte, größte GPS-Lücke).
- **Eine Aufzeichnungsdatei** `bavariandata_trip_capture.ndjson` in deinem
  **Konfigurationsordner** von Home Assistant — ein JSON-Datensatz pro Nachricht, sodass
  sich eine Fahrt beim Testen von Änderungen am Erkenner offline abspielen lässt.

Beides enthält **GPS-Koordinaten und deine FIN**. Teile sie nur mit den Maintainern (etwa
angehängt an ein GitHub-Issue, mit dem du dich wohlfühlst) und lösche die
Aufzeichnungsdatei, wenn du fertig bist. Sie wächst nicht über ~25 MB.

## Nach der Einrichtung erscheint kein Dashboard

<a id="no-dashboard-appears"></a>

Erwartet: In der Seitenleiste taucht nichts Neues auf. BavarianData legt **Entitäten und
Geräte** an, kein Dashboard. Die mitgelieferte Karte registriert sich als
Dashboard-*Ressource* und ist damit verfügbar — platzieren musst du sie selbst: ein
beliebiges Dashboard öffnen, **✏️ Bearbeiten → ➕ Karte hinzufügen**, **BavarianData Card**
wählen. Siehe [Die Dashboard-Karte](DE-The-Dashboard-Card).

Fehlt **BavarianData Card** in der Kartenauswahl ganz, *ist* das ein Fehler — lies in den
nächsten beiden Abschnitten weiter.

## Die Karte erscheint nach einem Update nicht

**Lade den Browser hart neu** — die mitgelieferte Karte wird hartnäckig zwischengespeichert.

## „Custom element doesn't exist: bavariandata-card“ in der App

<a id="custom-element-doesnt-exist-app"></a>

Symptom: Die Karte funktioniert in einem Desktop-Browser, aber die **Companion App** von
Home Assistant (iOS oder Android) zeigt einen roten Kasten *Konfigurationsfehler* mit
`Custom element doesn't exist: bavariandata-card`.

Die Karte wird einmal für die ganze Instanz registriert — sieht ein Browser sie, ist die
Seite der Integration in Ordnung, und etwas auf dem Telefon lädt die Kartendatei nicht.
Fang nicht mit Cache-Leeren an; zwei schnelle Tests grenzen es vorher ein.

**Test 1 — öffne dasselbe Dashboard im Browser des Telefons** (Safari unter iOS, Chrome
unter Android), nicht in der App. Gleiche Rendering-Engine, anderer Cache und andere
Verbindungseinstellungen — das halbiert das Problem:

- **Geht im Browser, kaputt in der App** → es ist der eigene Frontend-Cache der App. Ein
  Neustart von Home Assistant oder des Telefons leert ihn *nicht*; das musst du in der App
  tun:
  - iOS: **Einstellungen → Companion App → Debugging → Frontend-Cache zurücksetzen**
    (*Reset frontend cache*), dann die App beenden (wegwischen) und neu öffnen.
  - Android: **Einstellungen → Companion App → Fehlerbehebung → Cache leeren**
    (*Troubleshooting → Clear cache*), dann die App beenden und neu öffnen.
- **Auch im Browser kaputt** → weiter mit Test 2.

**Test 2 — kann das Telefon die Kartendatei überhaupt laden?** Öffne im Browser des
Telefons deine Home-Assistant-Adresse mit angehängtem Pfad:

```
/bavariandata/bavariandata-card.js
```

- **Eine Wand aus JavaScript** → die Datei ist erreichbar, die Karte scheitert also beim
  Laden. Bitte öffne ein Issue mit deiner iOS-/Android-Version und, wenn du drankommst,
  den Fehlern aus der Browser-Konsole.
- **404 / „nicht gefunden“** → deine Verbindung erreicht den Pfad nicht. Das ist ein
  **Reverse Proxy** (Nginx, Traefik, Caddy, Cloudflare Tunnel …), der nur die bekannten
  Pfade weiterleitet — `/api/`, `/static/`, `/frontend_latest/` und Co. Eigene Karten
  liegen außerhalb davon: die von BavarianData unter `/bavariandata/`, HACS-Karten unter
  `/hacsfiles/`. Er muss alles weiterleiten. Home Assistant Cloud (Nabu Casa) tut das und
  ist nicht betroffen.

Für beide Tests gut zu wissen: Die Companion App nutzt deine **interne** URL nur, wenn du
ihr gesagt hast, welches WLAN dein Zuhause ist (**Einstellungen → Companion App →
Verbindung → Interne URL**, mit gesetzter SSID). Ohne das nutzt sie die externe URL *auch
in deinem Heim-WLAN* — so versteckt sich ein Proxy-Problem, bis man es auf dem Telefon
ansieht. Teste die URL, die die App tatsächlich nutzt, am besten beide.

Gegenprobe, falls du eine HACS-Karte installiert hast: Ist *die* auf dem Telefon auch
kaputt, liegt es an der Verbindung oder der App, nicht an dieser Integration.

## Nach einem Neuladen zeigt jede Karte „Konfigurationsfehler“

<a id="config-error-after-reload"></a>

Symptom: Die Karten werden beim ersten Öffnen von Home Assistant richtig angezeigt, und
nach F5 wird jede zu einem Kasten *Konfigurationsfehler*. Ein hartes Neuladen hilft nicht;
nur eine neue Browsersitzung.

Das ist ab **v0.9.2-beta.8** behoben — aktualisiere die Integration und starte Home
Assistant neu. Bleibt es:

1. Gehe zu **Einstellungen → Dashboards → ⋮ → Ressourcen** und prüfe, ob
   `/bavariandata/bavariandata-card.js?v=…` aufgeführt ist, mit einem `?v=`, das zu
   deiner installierten Version passt. Sie wird automatisch angelegt und aktuell gehalten.
2. Fehlt die Liste oder ist sie ausgegraut, werden deine Dashboard-Ressourcen per YAML
   verwaltet. Füge die Ressource selbst in `configuration.yaml` hinzu:

   ```yaml
   lovelace:
     mode: yaml
     resources:
       - url: /bavariandata/bavariandata-card.js
         type: module
   ```

   Seit Home Assistant 2026.2 kannst du stattdessen bei einem YAML-Dashboard
   `resource_mode: storage` setzen und BavarianData die Ressource verwalten lassen.

Ursache war, dass die Karte früher als Frontend-Modul eingebunden wurde, das Home
Assistant als nicht abgewartetes `import()` in die Seite schreibt. Der Service Worker des
Frontends kann diese Seite ohne es aus seinem Cache ausliefern, sodass das Element
`bavariandata-card` nie definiert wurde und jede platzierte Karte auf den Fehlerkasten
zurückfiel — siehe [home-assistant/frontend#18728][fe18728]. Dashboard-Ressourcen werden
dagegen mit der Lovelace-Konfiguration ausgeliefert und geladen, bevor ein Dashboard
angezeigt wird.

[fe18728]: https://github.com/home-assistant/frontend/issues/18728

## Kontingent aufgebraucht

<a id="quota-exhausted"></a>

Hast du alle [50 REST-Anfragen](DE-Feature-API-Quota) in 24 h verbraucht, erscheint unter
**Einstellungen → Reparaturen** ein Hinweis, wann es zurückgesetzt wird. **Der Stream
fließt die ganze Zeit weiter** — nur `fetch_*`-Aufrufe pausieren.

## BavarianData vollständig entfernen

<a id="clean-uninstall"></a>

Das Löschen der Integration unter **Einstellungen → Geräte & Dienste** erledigt das
Wichtige: Es entfernt deine Token *und* löscht den aufgezeichneten Verlauf —
Ladevorgänge, Fahrten, aufgezeichnete Routen — sowie die veröffentlichten
Langzeitstatistiken. Das ist Absicht: Deine Fahr- und Ladehistorie soll die Integration
nicht überleben.

Einiges wird **nicht** automatisch entfernt:

- Das **API-Kontingent-Protokoll** und das **zwischengespeicherte Fahrzeugbild** in
  `.storage`.
- `bavariandata_trip_capture.ndjson` in deinem Konfigurationsordner, falls du die
  Fahrt-Diagnose je eingeschaltet hattest. **Sie enthält GPS-Koordinaten — lösche sie.**
- Geräte und Entitäten bleiben gelegentlich in den Registern hängen; lösche Reste unter
  **Einstellungen → Geräte & Dienste → Geräte / Entitäten**.

**Testest du eine frische Installation**, zählt eine zusätzliche Prüfung:
Langzeitstatistiken liegen in der Recorder-Datenbank, nicht im Entitätenregister, daher
kann ein *unvollständiges* Entfernen (gelöschter Ordner, Absturz beim Entfernen) Reihen
`bavariandata:…` zurücklassen, die ohne installierte Integration im Energie-Dashboard
wieder auftauchen. Suche unter **Entwicklerwerkzeuge → Statistiken** nach verwaisten IDs
und lösche sie.

Die vollständige Liste der Artefakte und eine Schritt-für-Schritt-Checkliste:
[clean-install.md](https://github.com/JustChr/BavarianData/blob/main/docs/clean-install.md)
(englisch).

> **Umstieg von einer älteren Installation `cardata` / `bmw_cardata`?** Es gibt keine
> Migration zwischen den Domänen — die Umbenennung ist ein Bruch. Entferne die alte
> Integration mit der Checkliste oben und richte BavarianData dann neu ein.

## Wo es Hilfe gibt

- Fehler in der Integration → [Issues](https://github.com/JustChr/BavarianData/issues).
  Hänge [Diagnosedaten](#download-diagnostics) an — sie sind geschwärzt, kosten kein
  Kontingent und beantworten meist schon die ersten drei Fragen, die wir stellen würden.
- Probleme mit der Registrierung bei BMW, Hilfe bei der Einrichtung oder allgemeine Fragen
  → [Discussions](https://github.com/JustChr/BavarianData/discussions).
- **Nichts hier hat gepasst?** Sag es trotzdem — in den
  [Discussions](https://github.com/JustChr/BavarianData/discussions). Ein Problem, das diese
  Seite nicht beantwortet hat, ist es wert, bekannt zu sein, auch wenn du es umgangen hast,
  und so wird die Seite fürs nächste Auto besser. Gern auch auf Deutsch.
