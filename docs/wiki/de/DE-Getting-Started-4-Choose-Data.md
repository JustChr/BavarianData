# 4. Gestreamte Daten auswählen

> 🇬🇧 [English version](Getting-Started-4-Choose-Data)

BMW streamt nur die Deskriptoren, die du im Portal unter **Datenauswahl**
anhakst, und bietet **keine API**, um diese Auswahl zu setzen — das geht nur im
Portal. Statt Hunderte technischer Felder von Hand anzuhaken, schaltet die
Integration sie mit einem Ein-Klick-Lesezeichen **Activate BMW data** für dich
ein — demselben Aktivator wie auf dem
[geführten Weg](DE-Getting-Started-3-Add-and-Authorize#guided-path). Die geführte
Einrichtung führt ihn vor der Autorisierung mit einem Standard-Satz aus; die
**manuelle Einrichtung** und **Konfigurieren → Gestreamte Daten auswählen**
lassen dich zuerst die Datengruppen wählen.

## Datengruppen wählen und einschalten

1. **Wähle die Datengruppen**, die du willst (Elektrofahrzeug, Fahrzeugstatus,
   Reifendaten, …). Die Vorauswahl ist ein sinnvoller Start; deine Wahl wird
   gespeichert und die Auswahl öffnet sich beim nächsten Mal vorausgefüllt.

   <p align="center">
     <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-cluster-picker.png" alt="Dialog 'Choose streamed data' mit einer Mehrfachauswahl von Datengruppen (Electric vehicle, Vehicle status, Vehicle events, Tire data, …)" width="540" />
   </p>

2. Der nächste Bildschirm verlinkt die Aktivierungsseite. Ziehe die Schaltfläche
   **Activate BMW data** in deine Lesezeichenleiste, falls sie dort noch nicht
   liegt.
3. Öffne im BMW-Portal die Seite **BMW CarData** des Fahrzeugs und klicke unter
   **CarData Stream** auf **Datenauswahl ändern**:

   <p align="center">
     <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bmw-portal-change-data-selection.png" alt="Bereich 'BMW CarData Stream' mit Konfigurationsstatus 'ready' und den Schaltflächen 'Delete stream' und 'Change data selection'" width="760" />
   </p>

   Klicke auf dieser Stream-Setup-Seite auf das Lesezeichen **Activate BMW data**.
   Es schaltet die Felder deiner gewählten Datengruppen ein, alles in deinem
   eigenen Browser.
4. Zurück in Home Assistant:
   - Bei einem Home Assistant mit **https** **läuft der Dialog von allein
     weiter**;
   - bei einem Home Assistant mit **http** klickst du im Feld des Aktivators auf
     **Copy** und fügst das kurze Ergebnis ein.

   Eine Bestätigung zeigt, wie viele Felder jetzt gestreamt werden. Klicke auf
   **Absenden**. (Nur bei der Ersteinrichtung: Hast du die Felder im Portal schon
   selbst angehakt, lass das Ergebnisfeld leer und klicke auf **Absenden**, um
   abzuschließen.)
5. Löse in der MyBMW App etwas aus (Ver- oder Entriegeln), damit das Auto seine
   erste Aktualisierung sendet.

## Später erweitern oder einschränken

Das kannst du jederzeit unter **Konfigurieren → Gestreamte Daten auswählen**
wiederholen — dieselbe Auswahl, dasselbe Lesezeichen.

> **Nur hinzufügend:** Der Aktivator *fügt* die gewählten Datengruppen deinem
> laufenden Stream hinzu; ein bereits gestreamtes Feld entfernt er nie. Um ein
> Feld **nicht mehr** zu streamen, hake es im Portal unter **Datenauswahl** ab.

Einzelne Streaming-*Berechtigungen* pro Deskriptor statt einer Auswahl im Portal
lehnt BMW ab — siehe
[stream-scope-investigation.md](https://github.com/JustChr/BavarianData/blob/main/docs/reference/stream-scope-investigation.md)
(englisch). Die vollständige Aufteilung der Felder auf die Datengruppen steht in
[telematics-fields.md](https://github.com/JustChr/BavarianData/blob/main/docs/reference/telematics-fields.md).

## Welche Datengruppen welche Funktionen speisen

Einige abgeleitete Funktionen brauchen bestimmte Datengruppen im Stream:

- **Die hochgerechneten Ladezustands-Helfer** brauchen die Datengruppe
  **Elektrofahrzeug** — genauer `vehicle.drivetrain.batteryManagement.header`,
  `vehicle.drivetrain.batteryManagement.maxEnergy`,
  `vehicle.powertrain.electric.battery.charging.power` und
  `vehicle.drivetrain.electricEngine.charging.status`.
- **Ladeverlauf, Kosten und Batteriezustand** brauchen die Ladeleistung aus der
  Datengruppe **Elektrofahrzeug**.
- **Ladekosten pro 100 km** brauchen zusätzlich den **Kilometerstand**
  (Datengruppe Fahrzeugstatus).
- **Fahrten** werden aus dem Live-**GPS** im Stream rekonstruiert.

## Hat es geklappt?

<a id="did-it-work"></a>

Hast du eine Datengruppe aktiviert, aber es sind keine Entitäten erschienen,
führe den Dienst **`bavariandata.get_coverage_report`** aus — er vergleicht die
Deskriptoren, die deine gewählten Datengruppen liefern *sollten*, mit denen, die
tatsächlich angekommen sind, und listet die fehlenden auf. Er beantwortet „liegt
es an meiner Auswahl, an meinem Auto oder an einem Fehler?“ und verbraucht kein
API-Kontingent. Siehe [Dienste](DE-Services-Reference#get_coverage_report).

Lass dich von einer langen Liste nicht beunruhigen: Jede Datengruppe deckt BMWs
gesamte Flotte ab, daher liefert jedes Auto nur einen Teil davon. Sendet eine
gewählte Datengruppe 7 Tage lang **überhaupt nichts**, erscheint unter
**Einstellungen → Reparaturen** eine Warnung — das ist der Fall, in dem es sich
lohnt, die Datenauswahl zu prüfen.

Ist diese eine Datengruppe nach **30 Tagen** immer noch leer, während alles andere
weiter ankommt, behandelt die Integration sie nicht mehr als Lücke: Deine Auswahl
wurde offenkundig gespeichert, die Felder fehlen also im Auto. Die Warnung
verschwindet, und die Datengruppe wird stattdessen als nicht zutreffend geführt.

### Manche Felder kommen nie über den Stream

<a id="some-fields-never-arrive-on-the-stream"></a>

BMWs Katalog vermerkt für jedes Feld, ob der MQTT-Stream es transportieren kann,
und **49 von 295 können es nicht** — darunter die Reifendiagnose, das
Fahrzeugbild, der Batterie-Gesundheitszustand (State of Health), Condition Based
Service, der Verriegelungsstatus der Türen und die Verbrauchszähler über die
Lebensdauer. Diese Felder gibt es **nur per REST**: Sie werden nie über den
Stream angefragt, ein leeres Feld dieser Art ist also keine Lücke in deiner
Datenauswahl und der Abdeckungsbericht meldet es nicht. Hole sie mit dem
passenden Dienst `bavariandata.fetch_*` ([Dienste](DE-Services-Reference)) und
denke an das Kontingent von 50 Aufrufen pro Tag.

Die Spalte **Stream** in
[telematics-fields.md](https://github.com/JustChr/BavarianData/blob/main/docs/reference/telematics-fields.md)
sagt dir Feld für Feld, was was ist.

## Ein letzter Schritt: die Karte auf ein Dashboard setzen

Deine Entitäten gibt es jetzt, aber BavarianData legt **kein** eigenes Dashboard
an — in der Seitenleiste erscheint nichts Neues. Öffne ein beliebiges Dashboard,
wähle **✏️ Bearbeiten → ➕ Karte hinzufügen** und nimm **BavarianData Card**; sie
findet dein Fahrzeug von allein.

**Fertig!** [Die Dashboard-Karte](DE-The-Dashboard-Card) beschreibt die weiteren
Ansichten — Ladeverlauf, Batteriezustand, Fahrten, Karte, Reifen und Öffnungen.

---

> **Welches Auto fährst du?** BavarianData wird an genau einem Fahrzeug gebaut
> und getestet — einem i5 eDrive40 —, welche Deskriptoren *dein* Modell streamt,
> können wir allein also nicht herausfinden. Wenn du bis hierhin gekommen bist,
> hilft es sehr, dein Modell zu erfahren, ungefähr wie viele Entitäten erschienen
> sind und ob der Ein-Klick-Aktivator in deinem Browser funktioniert hat.
> [Erzähl es uns in den Discussions →](https://github.com/JustChr/BavarianData/discussions)
