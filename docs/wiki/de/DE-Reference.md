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

## Für Mitwirkende

Deskriptor-Katalog, Metadaten, Übersetzungen und Feldreferenz werden alle von der
Pipeline in [`tools/`](https://github.com/JustChr/BavarianData/tree/main/tools)
aus BMWs Exporten erzeugt — lies
[tools/README.md](https://github.com/JustChr/BavarianData/blob/main/tools/README.md),
bevor du eine erzeugte Datei von Hand bearbeitest. Um den Namen einer Entität zu
ändern, bearbeite `title_en` in `tools/curated_titles.json` und lass die Pipeline
erneut laufen.
