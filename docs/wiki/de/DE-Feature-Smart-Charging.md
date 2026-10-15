# Intelligent laden mit anderen Integrationen

> 🇬🇧 [English version](Feature-Smart-Charging)

Laden, wenn Strom billig ist oder die Sonne scheint, und aufhören, sobald es
reicht. BavarianData plant das Laden nicht selbst: Das können eigene Ladeplaner
gut. Es liefert ihnen, was sie von BMW nicht zuverlässig bekommen: den
**Ladezustand des Autos, sein Ladeziel und ob das Kabel steckt**, live aus dem
Stream und ohne [API-Kosten](DE-Feature-API-Quota).

Dass CarData nur lesend ist, stört hier nicht. Ein Ladeplaner steuert nie das
Auto. Er schaltet deine **Wallbox** (oder eine smarte Steckdose), und das Auto
lädt, sobald die Wallbox Strom gibt.

---

## Welche Entitäten du übergibst

| Der Planer fragt nach | Nimm | Warum |
| --- | --- | --- |
| Ladezustand (%) | **Ladezustand (integrationsseitig vorhergesagt)** | Beim Laden schweigt BMW manchmal eine Stunde und länger, dann steht sein eigener Wert still. Dieser zählt weiter, mit dem Zähler der Wallbox, sobald du einen [verbunden hast](DE-Feature-evcc-and-Wallbox-Bridge#eingehend-der-zähler-deiner-wallbox-für-den-ladeverlauf). Ein Planer, der beim Ziel aufhört, hört damit rechtzeitig auf. Im Stand ist er gleich dem Wert des Autos. |
| Ziel-Ladezustand (%) | **Zielwert des Ladezustands der Hochvoltbatterie** | Das Ladeziel aus dem Auto oder der My-BMW-App. |
| Auto angesteckt | **Status der Ladesteckverbindung** (`connected` / `disconnected`) | Kommt mit dem Stream, sobald das Kabel ein- oder ausgesteckt wird, ohne Abfragen. |
| Akkugröße (kWh), falls gefragt | **Maximaler Energieinhalt der Hochvoltbatterie** oder die nutzbare Kapazität aus dem Datenblatt | Nur nötig, um Prozent in kWh umzurechnen. |

Bei einigen iX3 springt das Ladeziel kurz auf 100 %, neben dem echten Wert
([#52](https://github.com/JustChr/BavarianData/issues/52)). Lädt dein Planer
dann voll, gib ihm ein festes Ziel: Die meisten Planer haben eine eigene
Zieleinstellung, oder du nimmst ein `input_number`.

Bei mehreren Autos hat jedes Auto seine eigenen Entitäten; richte, wo der
Planer das kann, einen Planer pro Auto ein.

---

## EV Smart Charging

[EV Smart Charging](https://github.com/jonasbkarlsson/ev_smart_charging) (HACS)
sucht die billigsten Stunden aus Nord Pool, Entso-e, Energi Data Service und
anderen und schaltet dein Ladegerät in diesen Stunden ein.

1. Aus HACS installieren und unter *Einstellungen → Geräte & Dienste*
   hinzufügen.
2. **EV SOC entity:** der *Ladezustand (integrationsseitig vorhergesagt)* des
   Autos.
3. **EV Target SOC entity:** der *Zielwert des Ladezustands der
   Hochvoltbatterie* des Autos.
4. **Charger control entity:** der Ladeschalter deiner Wallbox (oder eine
   smarte Steckdose).
5. **An EV integration will control start/stop of charging** ausgeschaltet
   lassen: BavarianData kann das nicht, die Wallbox schon.

EV Smart Charging plant nur, solange sein Schalter **EV connected** an ist.
Schalte ihn mit dem Kabel über den Blueprint
[`plug_to_switch.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/plug_to_switch.yaml)
(siehe [Ereignisse & Blueprints](DE-Feature-Automations#blueprints-für-den-einstieg)):
Wähle den *Status der Ladesteckverbindung* des Autos, seinen Standort und den
Schalter *EV connected*. Einstecken außerhalb von zu Hause bewirkt nichts, eine
öffentliche Ladesäule startet also nie deinen Ladeplan für daheim.

---

## Predbat

[Predbat](https://springfall2008.github.io/batpred/car-charging/) plant einen
Hausspeicher und das Auto gemeinsam. Die Einstellungen fürs Auto stehen in
`apps.yaml`:

```yaml
car_charging_soc:
  - sensor.<auto>_ladezustand_integrationsseitig_vorhergesagt
car_charging_limit:
  - sensor.<auto>_zielwert_des_ladezustands_der_hochvoltbatterie
car_charging_planned:
  - sensor.<auto>_status_der_ladesteckverbindung
car_charging_battery_size:
  - 81.2
```

Nimm deine eigenen Entitäts-IDs (*Einstellungen → Entitäten*, nach den Namen
oben suchen); sie hängen vom Namen des Autos und der Sprache von Home Assistant
ab. Predbats Standardantworten für *planned* enthalten `connected` bereits, der
Kabelsensor funktioniert also ohne Blueprint. Das Ladegerät schaltet Predbat
dann über seine eigenen Einstellungen oder über eine Automation auf
`binary_sensor.predbat_car_charging_slot`.

---

## evcc

evcc kann den Ladezustand per MQTT direkt von BavarianData lesen. Dafür gibt es
eine eigene Seite: [evcc- & Wallbox-Brücke](DE-Feature-evcc-and-Wallbox-Bridge).
