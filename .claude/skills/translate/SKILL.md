---
name: translate
description: Carry changed or new English user-facing text into every shipped language (de fr it es nl pl pt cs sv) — HA flow/services/issues strings, the dashboard card, the month report. Use after editing any English string, when tools/i18n_gaps.py lists anything, when a parity test fails, or when adding a language.
---

# Translate a change into every language

Ten full languages ship: **en de fr it es nl pl pt cs sv** (plus `en-GB`, which is
generated). English is the source; every other table must carry the same keys
and the same `{placeholders}`, or Home Assistant silently drops the string.

## 1. Get the worklist

```bash
python tools/i18n_gaps.py            # grouped by language
python tools/i18n_gaps.py --since <ref>   # stale check against another base
```

`missing`, `extra`, `placeholders`, `code spans` are hard failures (the parity
tests fail on them too). `stale` means English changed and the translation did
not — retranslate unless the English change was a typo fix with no meaning change.

## 2. Where each string lives

| `[source]` | File | Notes |
| --- | --- | --- |
| `translations` | `custom_components/bavariandata/translations/<lang>.json` | Hand-edit every section **except `entity`** (generated). |
| `card` | `www/bavariandata-card.js`, `TRANSLATIONS.<lang>` | Same key order as `en`. |
| `report` | `history/export.py`, `STRINGS["<lang>"]` | HTML month report. |
| `export_history.language` | `services.yaml` | Must list exactly the report's languages. |

**Entity names are not translated here.** Descriptor names come from BMW's own
catalogue per language (`tools/catalogue_i18n/`); derived entities from
`tools/derived_entities.json`; enum states from `_STATE_ROWS` in
`tools/generate_translations.py`. Edit those, then run the `regen` skill.

After any `en.json` edit: `python tools/generate_en_gb.py`.

Edit JSON with a small Python script (`json.load` → change → `json.dump(...,
indent=2, ensure_ascii=False)` + newline), or the Edit tool — never a heredoc
with `\n` in it (the Bash tool un-escapes it).

## 3. Rules every translation keeps

- `{placeholders}`, `` `code spans` ``, ```` ``` ```` fences, `**bold**` and
  markdown links `[text]({url})` exactly as in English. **No URLs, nothing
  tag-shaped** (`<VIN>`) — hassfest rejects both. Write `PREFIX/VIN/soc`.
  (The card may contain `<code>`/`<b>` where English does; HA files never.)
- **Stay English on purpose:** the activator's UI labels **Activate BMW data**
  and **Copy**, the portal's *stream setup* page, BMW product names (CarData
  Client, CarData API, CarData Streaming, Client ID, MyBMW, BMW Motorrad,
  Teleservice, ConnectedDrive), README headings quoted in *Troubleshooting → …*,
  the support e-mail, evcc topic names, and **values a user types or a service
  takes**: `business`/`private`/`commute`, cluster slugs (`electric`, `status`,
  `tire`), `from`/`to`, service ids, `YYYY-MM`, `en`/`de`/`fr` codes.
- A reference to another screen uses **that language's own label** for it —
  look the menu label up in the same file (`options.step.init.menu_options`),
  and use HA's word for *Configure* and *Submit* from the table below.
- Portuguese is **European** (`pt-pt`: *ecrã*, *registar*, *a carregar*,
  *ficheiro*). German is established — match its existing wording.

| | Address | HA "Configure" | HA "Submit" | charging / trip / data group | "stream" |
| --- | --- | --- | --- | --- | --- |
| de | du | Konfigurieren | Absenden | Laden / Fahrt / Datengruppe | Stream |
| fr | vous | Configurer | Valider | recharge / trajet / groupe de données | flux |
| it | tu | Configura | Invia | ricarica / viaggio / gruppo di dati | stream |
| es | tú | Configurar | Enviar | carga / trayecto / grupo de datos | stream |
| nl | je | Configureren | Verzenden | laden / rit / gegevensgroep | stream |
| pl | Ty (impersonal where natural) | Konfiguruj | Zatwierdź | ładowanie / przejazd / grupa danych | strumień |
| pt | formal (você implied) | Configurar | Submeter | carregamento / viagem / grupo de dados | stream |
| cs | vy | Konfigurovat | Odeslat | nabíjení / jízda / skupina dat | stream |
| sv | du | Konfigurera | Skicka | laddning / resa / datagrupp | ström |

Trip classes: business/private/commute → de Geschäftlich/Privat/Pendeln · fr
Professionnel/Privé/Domicile-travail · it Lavoro/Privato/Pendolarismo · es
Trabajo/Privado/Desplazamiento · nl Zakelijk/Privé/Woon-werk · pl
Służbowy/Prywatny/Dojazd · pt Profissional/Particular/Casa-trabalho · cs
Služební/Soukromá/Dojíždění · sv Tjänst/Privat/Pendling.

## 4. Verify

```bash
python tools/i18n_gaps.py          # must print "No translation gaps."
python -m pytest tests/test_translations_parity.py tests/test_translations_dialect.py \
  tests/test_card_snapshots.py tests/test_statistics.py -q
```

`test_card_snapshots.py` renders every card view in every language; the
snapshots themselves stay English.

Say in the hand-off that the new strings are machine-written and unreviewed by a
native speaker.

## Adding a language

Follow the *Languages* section of `tools/README.md` — BMW must localize its
catalogue for it (some locales come back English; don't ship those). The tests
name each place still missing it.
