# Mehrere Fahrzeuge & Konten

> 🇬🇧 [English version](Feature-Multiple-Cars-and-Accounts)

BavarianData entstand rund um ein Fahrzeug in einem BMW-Konto, und seitdem ging
es darum, die anderen Konstellationen genauso gut zu bedienen. Diese Seite zeigt
das Gesamtbild: was ein Konfigurationseintrag abdeckt, was sich mit einem
zweiten Fahrzeug oder einem zweiten Konto ändert, und an welchen wenigen Stellen
du weiterhin sagen musst, welches Auto gemeint ist.

## Ein Eintrag pro Konto, nicht pro Fahrzeug

<a id="one-entry-per-account-not-per-car"></a>

Ein Konfigurationseintrag ist **ein BMW-Konto**: eine Client-ID, eine
OAuth-Anmeldung, ein MQTT-Stream, ein Tageskontingent von 50 Anfragen — und
**alle Fahrzeuge dieses Kontos**. Jede FIN wird zu einem eigenen
[Gerät](DE-Feature-Entities-and-Devices) mit eigenen Entitäten, eigener
Historie, eigenem Ladekonto und eigener Reifenakte.

Also:

- **Zwei Fahrzeuge in einem Konto** → die Integration **einmal** hinzufügen.
  Beide Autos erscheinen.
- **Zwei Konten** (deins und das des Partners, oder ein BMW- und ein
  MINI-Konto) → die Integration **zweimal** hinzufügen, je Client-ID einmal.
- **Ein Konto zweimal** wird abgelehnt. Seit **v0.9.11-beta.4** bricht die
  Einrichtung mit *„Dieses BMW-Konto ist bereits eingerichtet"* ab, wenn ein
  anderer Eintrag dasselbe Konto nutzt — auch dann, wenn du dafür im Portal eine
  zweite Client-ID erzeugt hast. Das ist keine selbst erfundene Einschränkung:
  BMW erlaubt **eine Stream-Verbindung pro Konto**, zwei Einträge würden sich
  also gegenseitig endlos trennen, und das Kontingent von 50 Anfragen zählt pro
  Konto, nicht pro Client-ID.

Ein Fahrzeug kann nur in einem Konto sein, daher wird keine FIN je von zwei
Einträgen bedient.

## Ein Fahrzeug später hinzufügen

<a id="adding-a-car-later"></a>

Kaufst du einen zweiten BMW oder meldest ein vorhandenes Auto erst nach der
Einrichtung bei CarData an, taucht es einfach auf: Es läuft über denselben
Stream, seine Entitäten erscheinen also von selbst, sobald das Auto etwas sendet.

Zwei Dinge fehlten dabei früher und sind ab **v0.9.11-beta.4** abgedeckt:

- **Name und Modell.** Alles, was aus einer FIN einen „i5 eDrive40" macht, stammt
  aus einem einmaligen REST-Aufruf, der bisher nur bei der Einrichtung lief — ein
  später hinzugekommenes Auto blieb auf der Geräteseite eine nackte FIN. Er
  erfolgt jetzt, sobald das Fahrzeug zum ersten Mal im Stream auftaucht (eine
  Anfrage aus dem Tageskontingent, einmalig).
- **Die Schonfrist der Abdeckungsprüfung.** Der
  [Stream-Selbsttest](DE-Getting-Started-4-Choose-Data#did-it-work) gibt einer
  frischen Installation eine Woche, bevor er einen Cluster als stumm meldet.
  Diese Uhr läuft jetzt pro Fahrzeug — ein nach zwei Jahren hinzugefügtes Auto
  gilt also nicht ab dem ersten Tag als unvollständig.

Die Felder des neuen Fahrzeugs musst du weiterhin **im BMW-Portal** ankreuzen:
Die Datenauswahl gilt je Fahrzeug und hat keine API. Rufe dafür erneut
**Konfigurieren → Gestreamte Daten auswählen** auf und wähle auf der
Portalseite, die der Aktivator öffnet, das neue Auto
([Schritt 4](DE-Getting-Started-4-Choose-Data)).

## Kontingent mit mehreren Fahrzeugen

<a id="quota-with-several-cars"></a>

Die 50 Anfragen pro 24 h gehören dem **Konto**, und die tägliche Aktualisierung
kostet **2 Anfragen je Fahrzeug** (Telematik-Container plus Reifendiagnose). Drei
Autos in einem Konto sind 6 von 50 pro Tag — reichlich Luft. Zwei getrennte
Konten haben je 50, weil BMW pro Konto zählt. Siehe
[API-Kontingent](DE-Feature-API-Quota).

## Die Fahrzeuge auseinanderhalten

<a id="telling-the-cars-apart"></a>

| Wo | Was zu tun ist |
| --- | --- |
| **Die Karte** | Eine Karte je Fahrzeug: `device:` setzen (die Auswahl bietet jedes Auto mit Namen an) oder `vin:`. Ohne Angabe wird das erste gefundene Fahrzeug genommen. |
| **Dienste** | Mit `vin:` ein bestimmtes Auto ansprechen. `bavariandata.fetch_telematic_data` ohne FIN aktualisiert **alle** Fahrzeuge des Eintrags; die übrigen `fetch_*`-Dienste wählen eines aus, nenne dort also die FIN. Bei zwei eingerichteten Konten zusätzlich `entry_id:` angeben — oder eine `vin:`, die das Konto bereits eindeutig bestimmt. Siehe [Dienste](DE-Services-Reference). |
| **Automatisierungen** | Die `bavariandata_charging_*`-Ereignisse enthalten `vin` und `entry_id`; filtere auf `trigger.event.data.vin`. Die mitgelieferten [Blueprints](DE-Feature-Automations) lösen sonst für jedes Fahrzeug aus. |
| **Die evcc-Brücke** | Topics laufen je FIN (`<prefix>/<vin>/soc`), mehrere Autos teilen sich also kollisionsfrei ein Präfix. Den evcc-Block je Fahrzeug erzeugt `bavariandata.get_evcc_config` mit dessen `vin`. Ein gebundener Wallbox-Zähler gilt allerdings pro Eintrag: Können zwei Autos gleichzeitig zu Hause laden, lassen sich die gemessenen kWh keinem von beiden zuordnen. |
| **Debug-Protokollierung** | Der Log-Level ist ein Schalter für die ganze Integration — er kann nicht je Konto gelten. Die ausführliche Protokollierung bleibt aktiv, solange **irgendein** Eintrag sie anhat, und protokolliert währenddessen die Daten aller Konten. |
| **Reparaturen & Hinweise** | Abdeckungs- und Stream-Reparaturen nennen das Fahrzeug. Die Kontingent-Reparatur nennt kein Konto — prüfe bei zwei Einträgen die Diagnose-Entität **Verbleibendes API-Kontingent** am jeweiligen Debug-Gerät. |

## Einen Eintrag entfernen

<a id="removing-an-entry"></a>

Das Entfernen eines Eintrags lässt den anderen unberührt weiterlaufen: Stream,
Dienste und Karte bleiben, wie sie sind. Der entfernte Eintrag nimmt seine
gespeicherte Historie, seinen Abdeckungsstand und seine Reifendaten mit und
räumt zurückgehaltene evcc-Topics für **jedes** Fahrzeug ab, das er je hatte —
auch für eines, das nur gestreamt und nie Basisdaten geliefert hat.
