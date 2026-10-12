# Energie-Dashboard & Langzeitstatistiken

> 🇬🇧 [English version](Feature-Energy-and-Statistics)

Der aufgezeichnete Verlauf wird in die **Langzeitstatistiken** von Home Assistant
übertragen, unter dem Namensraum `bavariandata:` dieser Integration: geladene
Energie (kWh), zu Hause geladene Energie (kWh), Ladekosten und Fahrstrecke — je
eine Reihe pro Fahrzeug.

## Warum externe Statistiken

Sie werden als **externe** Statistiken geschrieben und können daher auch Stunden
abdecken, in denen keine Entität existierte. So landen Ladevorgänge von **vor der
Installation** oder aus der Zeit, **in der Home Assistant aus war**, trotzdem im
Energie-Dashboard.

## Zum Energie-Dashboard hinzufügen

<a id="adding-to-the-energy-dashboard"></a>

**Einstellungen → Dashboards → Energie → Einzelne Geräte**, dann die Reihe
**charging energy at home** (`bavariandata:charging_energy_home_<fin>`) hinzufügen.

Nimm diese, nicht die einfache Reihe **charging energy** oder die Entität
**Geladene Energie (gesamt)**. Home Assistant zählt ein einzelnes Gerät zu dem,
was dein Haus verbraucht hat. Die beiden anderen zählen jede Ladung, auch eine
Schnellladung an der Autobahn; die erschiene dann als Haushaltsverbrauch und
kann deine Geräte über den Gesamtverbrauch des Hauses heben. Die Reihe für zu
Hause folgt derselben Regel wie der
[Energiemix](DE-Feature-Charging-History-and-Cost#where-the-energy-came-from): Ein
Auto, das keine Position meldet, gilt als zu Hause.

## Ein Spiegel, kein zweites Archiv

Die Statistiken **spiegeln den Speicher**, statt ein eigenes Archiv zu sein: Jeder
Neuaufbau erzeugt die ganze Reihe aus den noch vorhandenen Datensätzen, deine
[Aufbewahrungs-Einstellung](DE-Settings-Reference#charging-costs--history) gilt
also wirklich für alles, was behalten wird.

Das passiert **automatisch**, sobald Ladevorgänge und Fahrten aufgezeichnet
werden. Einen Neuaufbau erzwingen:

- **`bavariandata.import_statistics`** — baut aus dem Speicher neu auf und meldet
  die Zeilenzahlen. Nützlich nach dem **Wiederherstellen eines Backups** oder zum
  Prüfen der Zahlen.

## Abschalten

Schalte **In Langzeitstatistiken übernehmen** unter **Konfigurieren → Ladekosten
& Verlauf** aus. Das **löscht auch die veröffentlichten Reihen** — es bleibt kein
veralteter Spiegel im Energie-Dashboard zurück.
