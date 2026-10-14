# Kilometer & Kontingent

> 🇬🇧 [English version](Feature-Mileage)

Wie viel jedes Auto gefahren wird: diesen Monat, dieses Jahr, Monat für Monat,
und in welchem Tempo. Hat das Auto eine **Kilometergrenze** aus Leasing oder
Versicherung, zeigt BavarianData außerdem, ob du unter oder über Plan liegst und
was noch übrig ist. Alles kommt vom Kilometerzähler des Autos selbst und kostet
daher **kein API-Kontingent**.

## Woher die Zahlen kommen

<a id="where-the-figures-come-from"></a>

BavarianData speichert für jedes Auto **einen Kilometerstand pro Tag**, den
höchsten des Tages. Anders als eine Fahrt kann der Kilometerzähler keinen
Kilometer verpassen: Was auch immer zwischen zwei Ständen passiert ist, ihre
Differenz ist genau das, was gefahren wurde. Am Tag des Updates beginnt das
Protokoll mit den Ständen aus deinem Ladeverlauf, daher haben die meisten
Installationen schon Monate an Daten.

- **Dieser Monat / dieses Jahr:** vom Stand zu Beginn des Zeitraums bis zum
  letzten Stand. Begann der Zeitraum vor dem Protokoll, steht *seit
  Aufzeichnungsbeginn* dabei.
- **Letzte 12 Monate:** ein Balken pro Kalendermonat, an den Monatsgrenzen
  zwischen den Ständen davor und danach berechnet.
- **Pro Tag und Jahrestempo:** der Durchschnitt der letzten 90 Tage. Er erscheint,
  sobald die Stände **vier Wochen** abdecken; bis dahin gibt es kein Tempo statt
  eines geratenen. Dasselbe Tempo legt Einträge, für die nur eine Strecke übrig
  ist, in den [Servicekalender](DE-Feature-Entities-and-Devices#service-calendar).

## Ein Kilometerkontingent

<a id="a-mileage-allowance"></a>

Pro Auto unter **Konfigurieren → Kilometerkontingent** einstellen
([die Masken](DE-Settings-Reference#mileage-allowance)). Es gibt zwei Arten:

- **Leasing oder Finanzierung:** Beginn, Ende, Kilometer pro Jahr und der
  Kilometerstand bei Übergabe. Das Gesamtkontingent ist der Jahreswert mal die
  Vertragslaufzeit (drei Jahre mit 15.000 km sind 45.000 km). Der Rückgabetag
  steht zusätzlich im Servicekalender.
- **Jahresgrenze:** eine Grenze, die jedes Jahr am selben Tag neu beginnt, etwa
  die Jahresfahrleistung deiner Versicherung oder eine Dienstwagenregel. Du
  trägst den Kilometerstand vom letzten Jahresbeginn ein; jedes spätere Jahr
  beginnt mit dem Stand, den BavarianData an dem Tag protokolliert hat.

Was du siehst:

| Wert | Bedeutung |
| --- | --- |
| **Gefahren** | Stand jetzt minus Stand zu Beginn. |
| **Plan bis heute** | Das Kontingent gleichmäßig über den Zeitraum verteilt, bis heute. |
| **Unter / über Plan** | Plan bis heute minus gefahren. Positiv heißt: Kilometer übrig. |
| **Pro Tag bis zum Ende** | Der Rest des Kontingents geteilt durch die verbleibenden Tage. |
| **Bei diesem Tempo** | Gefahren plus dein 90-Tage-Tempo bis zum Ende, und wie weit das vom Kontingent entfernt landet. |

Der Kilometerstand bei Übergabe ist die Grundlage des Vergleichs. Hast du ihn
nicht, füllt die Maske den heutigen Stand ein. Das stimmt nur, wenn der Vertrag
heute beginnt; bei einem älteren Vertrag nimm die Zahl aus dem
Übergabeprotokoll.

**Mehrere Autos?** Jedes Auto hat sein eigenes Kontingent oder keins. Ein später
hinzugefügtes Auto übernimmt nie das Leasing eines anderen.

## Wo du es siehst

<a id="where-to-see-it"></a>

- Die Ansicht **Kilometer** der Karte ([`view: mileage`](DE-The-Dashboard-Card#mileage-view-mileage)).
- Der Sensor **Kilometerkontingent-Saldo**, **standardmäßig deaktiviert**: unter
  (+) oder über (−) Plan in km, mit gefahren, verbleibend, Kilometer pro Tag und
  dem erwarteten Ende als Attributen. Unbekannt, solange das Auto kein
  Kontingent hat. Am Gerät des Autos aktivieren, wenn du ihn für Automationen
  brauchst, etwa für eine Warnung ab 500 km über Plan.
- Der Dienst `bavariandata.get_mileage_report`
  ([Felder](DE-Services-Reference#get_mileage_report)) liefert alles als
  Antwortdaten.
