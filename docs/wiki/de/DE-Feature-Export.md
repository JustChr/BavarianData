# Export (CSV / HTML-Bericht)

> 🇬🇧 [English version](Feature-Export)

Die Kartenansichten [Ladeverlauf](DE-The-Dashboard-Card#charging-history-view-charging)
und [Fahrten](DE-The-Dashboard-Card#trips--driving-journal-view-trips) haben je
eine Schaltfläche **CSV** und **Bericht**, die den angezeigten Monat direkt im
Browser herunterladen. Dahinter steckt der Dienst
**`bavariandata.export_history`**, den auch Automationen nutzen können. Auf die
Festplatte wird nichts geschrieben — der Dateiinhalt kommt als Antwortdaten des
Dienstes zurück — und es wird **kein API-Kontingent** verbraucht.

## CSV

Eine Zeile pro Ladevorgang oder Fahrt:

- Die **batterieseitige** und die **gemessene Netz**-Energie in getrennten
  Spalten.
- Die Kosten und woher sie stammen.
- Bei Fahrten die Endpunkte als **Ortsnamen**.

Die Spaltenüberschriften sind immer maschinenlesbares Englisch. Öffnet sich
sauber in Excel und Numbers.

## HTML-Bericht

Eine eigenständige HTML-Seite: Monatssummen, die Ladetabelle und das
Fahrtenbuch. Drucke sie (**Strg/⌘+P → Als PDF speichern**) für ein ordentliches
A4-Dokument. Die Sprache des Berichts folgt der Sprache von Home Assistant, oder
du legst sie ausdrücklich fest.

> Der Bericht ist ein Fahrtenbuch und eine Hilfe für Spesen, **kein
> Finanzamt-konformes Fahrtenbuch**, und sagt das auch auf der Seite.

## Direkt aufrufen

```yaml
service: bavariandata.export_history
data:
  month: "2026-07"     # Standard: der aktuelle Monat
  type: both           # charging · trips · both
  format: csv          # csv · html
  language: de          # en · de (nur HTML-Bericht; Standard: Sprache von HA)
```

Die vollständige Liste der Felder steht unter
[Dienste](DE-Services-Reference#export_history).
