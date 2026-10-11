---
name: flow-ux
description: Design or review a setup or Configure screen (config flow, options flow, reconfigure) so it is plain and obvious to a non-technical user — mock it up first, check it against the rules, get Chris's go, then build and look at it for real. Use before adding or changing any flow step, form field, menu entry or flow string, and when asked whether a screen is "easy to understand".
---

# Setup-screen UX

Most users meet this integration only through these screens. The bar: someone
who has never heard of MQTT, descriptors or a GCID gets through every screen
without the wiki, and nothing on a screen surprises them later.

## 1. Mock it up before writing code

A design hands over three things:

1. **Mockups:** every screen the change touches as an ASCII box, with title,
   description, each field's label, default and help line, and the button.
2. **The path:** for a flow that branches, a Mermaid `flowchart` from the
   menu entry to `async_create_entry` / `async_abort`, with one route per kind
   of user it affects (single-car vs. multi-car account, first setup vs.
   Configure) and every abort and error state.
3. **A field table:** key, selector, default, where it is stored (`data`,
   `options`, or `options` per car), validation, and the error it can raise.

If there is a design choice, put the mockups side by side in an
`AskUserQuestion` with `preview` (see [[questions-as-selectors]]). **Build
only after Chris has picked one.**

## 2. The rules

**What a screen contains**
- One screen does one job. Its title is the same as the menu entry that
  opens it.
- The **description** says what the screen is for and the one thing to know
  before touching it: 1 to 3 short sentences, about **400 characters at
  most**. Field details go in `data_description` (shown under the field) and
  are **never repeated** in the description.
- A field's help line is **one sentence, about 200 characters at most**: what
  it does, plus what it costs if anything (privacy, BMW quota, a restart).
- **Leave out what doesn't apply to this user.** A single-car account never
  sees a vehicle picker. A setting that only matters for another choice is
  better as a follow-up screen than as a field whose help starts "only if…".
- Settings people need sit at the top. Diagnostic and advanced settings go
  at the bottom or in a collapsed section.

**Words**
- Use the user's words, not ours: never *descriptor, cluster, container,
  GCID, substrate, coordinator, entry, VIN* in a label. (The *stream* is
  fine, because the portal uses that word too.)
- One thing has one name everywhere: the screen, the card, the wiki and the
  other nine languages. Before inventing a term, look for the existing one in
  `en.json`.
- US English (`en.json` is the source). Write labels short: German runs
  about 30 % longer and still has to fit.

**Behaviour**
- **What you see is what applies.** No hidden inheritance and no "blank means
  use the other setting". A form shows the values that are actually in
  effect, pre-filled.
- Defaults are right for most people, so pressing Submit without reading is
  safe. Anything that sends data out of the house or writes coordinates to
  disk is **off** by default and says so.
- Say **when** a change takes effect if it isn't immediately: "from the next
  trip on", "after a restart".
- Anything that spends BMW's 50-a-day request budget says so on the screen
  that spends it.
- An error names what went wrong and what to do next, on the field it
  belongs to (`errors={field: key}`), not only in the log.
- It has to work at phone width (about 360 px). Many people open Configure in
  the companion app, so keep labels short and dropdown entries short.

**Home Assistant's rules (core and Quality Scale)**
- `entry.data` holds only what the connection needs (client ID, tokens, GCID,
  the chosen data groups). Everything a user can tune lives in
  `entry.options`. A per-car setting lives in `options` under that car's VIN,
  never in a store of its own.
- **Option keys are stored data.** Renaming one silently resets every
  user's setting, so add new keys and never rename or reuse old ones. The
  same applies to translation keys, which nine other languages depend on.
- Every field has a `data_description`. Every `errors` key and every
  `async_abort` reason has text in all ten languages.
- No URLs in any string (hassfest rejects them). Pass a link in through
  `description_placeholders`.
- The unique ID is the client ID, and a second entry for the same account
  aborts with `account_already_configured`. Don't break either.
- Reauth exists (`async_step_reauth`). There is no `reconfigure` step,
  because Configure → *Choose streamed data* does that job, so don't add a
  second way to do the same thing. There's no discovery, because BMW is
  cloud-only.
- This is a custom integration, so there is no `strings.json`. The source is
  `translations/en.json`, carried into the other languages with the
  `translate` skill.

## 3. What HA forms can and cannot do

- Forms are **not reactive**: fields can't appear or disappear based on
  another field on the same screen. Use a follow-up step instead.
- Field keys, section keys and their labels are **static translation keys**,
  so a label can't name a car. Dynamic choices such as cars go in a
  `SelectSelector` with `SelectOptionDict(value=…, label=…)` options, whose
  labels are taken literally. `description_placeholders` can put a car's name
  into the **description** (verify on the live instance before relying on it
  anywhere else).
- Menu entries (`async_show_menu`) are fixed step ids, each needing its own
  `async_step_*` method. They are not usable for a list of cars.
- `data_entry_flow.section(schema, {"collapsed": True})` groups fields under a
  heading. Its key is static, and its text sits in translations under
  `sections`.
- Use `suggested_value` (not `default`) for an optional field the user must be
  able to clear, such as an entity selector. A `default` comes back as soon as
  the field is emptied.
- Finishing an options step **replaces** `entry.options`, so carry the
  existing options forward (see `_finish`).

## 4. Build, then look at it

1. New strings go in every full language in the same change (`translate`
   skill). `tests/test_config_flow_steps.py` fails on a step without text.
2. Shoot the real screens on the live instance (`shoot` skill), in **English
   and German**, because German is where labels wrap or overflow. Read each
   one as a newcomer would: is anything unclear without the wiki?
   **A branch your setup can't reach** (a second car, a hybrid, an error
   state): stage it with `mock_flow.js` next to this file
   (`node mock_flow.js <trips|charging|bridge> <real|staged> <lang> <width> <outdir>`;
   `real` shoots the screen as the instance shows it). It answers the
   options-flow REST calls in the browser the way the integration would, so
   HA's real dialog renders the step with the installed build's translations
   (so new strings need a beta first). Entity pickers holding a value are
   blurred: they show the user's own names.
   Nothing reaches HA (other calls are aborted, the flow is abandoned). Build
   the fake step from the real one the server returns, so its schema is HA's
   own. A section serializes as `{"type": "expandable", "name", "expanded",
   "schema"}`. It proves how a screen looks and reads, never what the
   backend does: that is the tests' job.
   - A step that more steps follow sets `last_step=False`, so the button says
     *Next* and not *Submit*. Found this way on the car picker.
3. Measure the text against the budgets above:
   ```bash
   python -c "import json;d=json.load(open('custom_components/bavariandata/translations/en.json',encoding='utf-8'));[print(s,k,len(v.get('description',''))) for s in ('config','options') for k,v in d[s]['step'].items()]"
   ```
4. Docs: the wiki row and screenshot in EN and DE, plus the coverage matrix
   (CLAUDE.md, *User documentation*).

## 5. Known debt, so it doesn't spread

Measured 2026-10-11:
- The settings screens restate each field's help in the description. Trips
  is at 1,625 characters, evcc bridge 1,264, energy sources 1,215, charging
  costs 881. Shrink a screen to the budget whenever a change touches it.
- The Configure menu is a flat list of 17 entries, with one-off actions
  (fetch, refresh, reset) mixed in with settings. Don't add to it without
  asking whether the entry belongs inside an existing screen.
