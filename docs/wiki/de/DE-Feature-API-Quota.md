# API-Kontingent

> 🇬🇧 [English version](Feature-API-Quota)

BMW begrenzt die CarData-REST-API auf **50 Anfragen pro 24 Stunden je Konto**.
Die Integration zählt und erzwingt das selbst.

## Was Kontingent verbraucht

- Jeder manuelle Aufruf eines **`fetch_*`**-Dienstes
  ([Dienste](DE-Services-Reference)).
- Die **tägliche Aktualisierung**: **3 Anfragen alle 24 h je Fahrzeug** für
  alles, was BMW nicht streamen kann (siehe unten).
- Einmalige Abrufe bei der Einrichtung (Fahrzeug-Basisdaten, das Fahrzeugbild),
  die nicht wiederholt werden, weil sich diese Daten nicht ändern.
- Das Einrichten des Telematik-Containers und sein Ersetzen (**Telemetrie-Container
  zurücksetzen** oder nach einem Update, das seinen Inhalt ändert): je eine
  Anfrage zum Auflisten, Anlegen und Löschen, ein Zurücksetzen kostet also etwa
  drei. BMW zählt auch diese.

## Die tägliche Aktualisierung

<a id="the-daily-refresh"></a>

49 seiner rund 295 Telematikfelder kann BMW nicht streamen — Servicebedarfe,
Condition Based Service, Check-Control-Meldungen, Reifenverschleiß,
Verbrauchszähler über die Lebensdauer, Verriegelungsstatus der Türen. Nur über
die REST-API kommen sie nach Home Assistant, daher holt die Integration sie
einmal am Tag:

| Anfrage | Umfasst |
| --- | --- |
| Der Telematik-**Container** | 40 der 41 nicht streambaren Felder, die der Container-Endpunkt liefern kann — **eine Anfrage für alles** |
| Der Container für die **Serviceumfänge** | Nur Condition Based Service. Seit dem 16. Okt. 2026 lehnt BMW jeden Container ab, der es enthält, und damit den ganzen Abruf, daher hat es einen eigenen Container — eine Anfrage |
| **Reifendiagnose** | Profiltiefe und Restlaufleistung pro Rad — ein eigener Endpunkt, also eine eigene Anfrage |

Alle drei gelten je Fahrzeug, die Aktualisierung kostet also **3 Anfragen
pro Auto**: 3 pro Tag bei einem Auto, 6 bei zweien, 9 bei dreien — von 50.
Täglich ist Absicht:
Diese Werte ändern sich über Tage, nicht über Minuten.

## Was kein Kontingent verbraucht

- Der **MQTT-Stream** — der Hauptweg der Daten. Alle gestreamten Sensoren, der
  Lade-, Fahrten- und Batterieverlauf, die Statistiken und der Export sind
  **kostenlos**.
- Die Dienste **`get_*`** sowie `export_history` / `import_statistics` /
  `set_trip_class` — sie lesen oder schreiben den lokalen Speicher der
  Integration.

Bevorzuge den Stream. Frage nur ab, was der Stream wirklich nicht
transportieren kann, und speichere, was geholt wurde — das Fahrzeugbild und die
Reifendiagnose überstehen aus genau diesem Grund Neustarts, damit ein Neustart
nie eine Anfrage kostet, um etwas zurückzuholen, das schon da war.

## Im Blick behalten

Ein Diagnosesensor **Verbleibendes API-Kontingent** zeigt, wie viele der 50
Anfragen noch übrig sind, mit den Attributen `used`, `limit` und `next_reset`.

Ist das Kontingent einmal aufgebraucht, erscheint unter **Einstellungen →
Reparaturen** ein Hinweis, wann es zurückgesetzt wird. **Die gestreamten Daten
fließen die ganze Zeit weiter** — nur REST-Abrufe pausieren, bis das Zeitfenster
weiterrückt.
