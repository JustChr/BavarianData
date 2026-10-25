# Technische Referenz

> 🇬🇧 [English version](Reference)

Technisches Nachschlagematerial, das im Repository liegt und (beim
Feldkatalog) aus BMWs eigenen Exporten erzeugt wird, damit es nicht veraltet.
Die verlinkten Dokumente sind englisch.

## Katalog der Deskriptoren / Felder

- **[telematics-fields.md](https://github.com/JustChr/BavarianData/blob/main/docs/reference/telematics-fields.md)**
  — jeder Deskriptor, den BMW streamen kann, nach Datengruppe geordnet, mit der
  HA-Entität, auf die er abgebildet wird. Das ist die maßgebliche Aufteilung der
  Felder auf die Datengruppen, auf die
  [Schritt 4](DE-Getting-Started-4-Choose-Data) verweist.

## Notizen zur BMW-API

- **[bmw-cardata-api-guide.md](https://github.com/JustChr/BavarianData/blob/main/docs/reference/bmw-cardata-api-guide.md)**
  — wie die REST-API genutzt wird.
- **[bmw-cardata-streaming-guide.md](https://github.com/JustChr/BavarianData/blob/main/docs/reference/bmw-cardata-streaming-guide.md)**
  — der MQTT-Stream.
- **[bmw-cardata-api-reference.md](https://github.com/JustChr/BavarianData/blob/main/docs/reference/bmw-cardata-api-reference.md)**
  — Referenz der Endpunkte.
- Swagger: [customer-api](https://github.com/JustChr/BavarianData/blob/main/docs/reference/customer-api.swagger.json)
  · [device-flow](https://github.com/JustChr/BavarianData/blob/main/docs/reference/device-flow.swagger.json).

## Untersuchungen zum Design

- **[stream-scope-investigation.md](https://github.com/JustChr/BavarianData/blob/main/docs/reference/stream-scope-investigation.md)**
  — warum Streaming-Berechtigungen pro Deskriptor nicht funktionieren und die
  Datenauswahl nur im Portal möglich ist.

## Codequalität

Home Assistant bewertet seine eigenen Integrationen mit der
[Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale)
(Bronze, Silber, Gold, Platin). Bewertet werden nur Integrationen, die mit Home
Assistant ausgeliefert werden; eine benutzerdefinierte Integration kann keine
Stufe verliehen bekommen. BavarianData hält die Regeln trotzdem ein und **stuft
sich selbst als Platin ein**: Alle Regeln bis Gold sind erfüllt, dazu zwei der
drei Platin-Regeln.

Die Einstufung steht Regel für Regel in
[`quality_scale.yaml`](https://github.com/JustChr/BavarianData/blob/main/custom_components/bavariandata/quality_scale.yaml),
mit einer Begründung neben jeder Ausnahme. Drei Regeln werden bei jeder Änderung
geprüft, im CI-Job *Home Assistant tests*:

- **Testabdeckung:** Beide Testsuiten zusammen decken mehr als 95 % jedes
  einzelnen Moduls ab, nicht nur der Summe. Fällt eines darunter, schlägt der Job
  fehl.
- **Strikte Typisierung:** Die ganze Integration besteht `mypy --strict` gegen
  ein echtes Home Assistant.
- **Tests der Einrichtung:** Einrichtung, erneute Anmeldung, Neukonfiguration und
  jeder Konfigurieren-Bildschirm laufen gegen ein echtes Home Assistant.

Drei Regeln sind ausgenommen, und die Datei sagt, warum:

- **Abfragen im Takt** entfällt: Die Daten des Autos kommen über BMWs Stream.
- **Erkennung im Netzwerk** entfällt: Ein BMW-Konto liegt in der Cloud, in deinem
  Netzwerk gibt es nichts zu finden.
- **Platins Regel zur asynchronen Bibliothek:** Der Stream nutzt paho-mqtt,
  denselben MQTT-Client wie Home Assistants eigene MQTT-Integration.

## Für Mitwirkende

Deskriptor-Katalog, Metadaten, Übersetzungen und Feldreferenz werden alle von der
Pipeline in [`tools/`](https://github.com/JustChr/BavarianData/tree/main/tools)
aus BMWs Exporten erzeugt — lies
[tools/README.md](https://github.com/JustChr/BavarianData/blob/main/tools/README.md),
bevor du eine erzeugte Datei von Hand bearbeitest. Um den Namen einer Entität zu
ändern, bearbeite `title_en` in `tools/curated_titles.json` und lass die Pipeline
erneut laufen.
