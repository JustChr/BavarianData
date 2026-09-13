# Changelog

All notable changes to BavarianData are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
A curated changelog is kept from v0.9.0-beta.6 onward and backfilled to the last
stable release (v0.8.1); releases before that used auto-generated notes.

## [Unreleased]

## [0.9.10-beta.1] - 2026-09-13

### Added
- **The card's charging, battery-health and efficiency views explain themselves
  on a petrol or diesel car.** They used to sit empty, which looks like a fault.
  They now say there is nothing to show for a car that runs on fuel alone, and
  point to the card editor's **Drivetrain** setting in case the car does plug in.
  Card **1.13.0**.
- **The fuel in the tank now has long-term statistics.** It had no device class,
  so Home Assistant kept no history for it. It is now a volume sensor in whatever
  unit the car sends — litres, or gallons on a car that reports them — shown as a
  whole number, since BMW calls the reading accurate to about 6 litres.
- **A BMW motorcycle gets a notice under Settings → Repairs.** BMW lists BMW
  Motorrad bikes in CarData but streams no data for them, so setup went through
  and the device then stayed empty with no explanation.

### Changed
- **Manual setup switches the stream on the same way guided setup does.** After
  you pick your clusters, it now runs the one-click **Activate BMW data**
  bookmarklet instead of handing you a console snippet to paste into the portal's
  Data Selection page. Guided setup, manual setup and **Configure → Choose
  streamed data** now all use the same screens. If you already ticked the fields
  in the portal yourself, leave the result box empty and press **Submit**.
- **Plug-in hybrid trips no longer show a kWh/100 km figure.** A trip's energy is
  the drop in battery charge, but a hybrid may have driven part of the distance on
  fuel, so dividing the two read far too low. The trip keeps its energy; the rate
  is left blank and stays out of the monthly average.

### Fixed
- **Petrol and diesel cars no longer get electric-car sensors.** The
  state-of-charge estimate, charged energy and charging energy and cost sensors
  were created for every car, so a petrol or diesel car had up to ten of them
  stuck at *unknown*. They now appear only once the car sends high-voltage battery
  data. On a car that has sent fuel data and never any battery data, the existing
  ones are removed on the first start after updating; their old history may then
  show under **Developer tools → Statistics**, where it can be deleted.
- **Condition Based Service and Check Control messages now really show their
  data.** Beta.9 said it fixed this, but it didn't: it expected BMW to send a
  list, and BMW sends the list as text. Home Assistant kept logging the same
  "longer than 255" error on every start and kept showing *unknown*. The text is
  now read as the list it contains, so the state is the **number of entries** and
  the full list is in the **`items`** attribute, as beta.9 described. Installs
  that already stored the rejected text fix themselves on the first start after
  updating; nothing to wait for.
- **The card wrote its own figures with a decimal point on a German
  dashboard.** Consumption, charged energy, capacity, trip distances and costs
  that the card works out itself read "19.8" and "0.2 kWh" right next to Home
  Assistant's "110,10 kWh". They now follow your profile's language and *Number
  format* setting, like every entity state does.
- **Tyre pressures showed two decimals** ("260,00 kPa"). BMW reports whole kPa,
  so they now show as "260 kPa", like distances already do. A display precision
  you set yourself on an entity is kept.

## [0.9.9-beta.9] - 2026-09-13

### Fixed
- **The card's charge ring could show "—" instead of the state of charge.**
  Beta.8 switched on the measured state-of-charge entity on installs where an old
  default had left it off, and taught the ring to prefer it. But a freshly
  enabled entity has no value until the car next reports one, and the ring
  preferred it anyway — so it went blank while the integration's own estimate
  had the right figure. The ring now uses whatever has a value, measured first,
  and only waits on the measured one when nothing else has a value either. Card
  **1.12.1**.
- **Condition Based Service and Check Control messages never showed anything.**
  BMW sends both as a list — the service items with their due dates, and the
  warnings the car raised. Home Assistant can't store a state that long, so it
  logged an error on every start and showed *unknown* instead. The state is now
  the **number of entries** (so "a Check Control message appeared" works in an
  automation), and the full list is in the entity's **`items`** attribute. It
  fills in the next time the car sends it.

## [0.9.9-beta.8] - 2026-09-13

### Added
- **The card's Overview fits the car's drivetrain.** It was built for electric
  cars only, so a petrol or diesel car got a "Charge" ring, "Not charging" and a
  charge *Target* tile — BMW streams an EV charge target even to a petrol F87 M2.
  The card now works out the drivetrain from what the car streams. A **petrol or
  diesel** car gets its tank in the ring (the fill level where the car sends a
  percentage, otherwise the volume), its remaining range, and no charging tiles. A
  **plug-in hybrid** keeps the charge ring and adds its tank and total range. An
  **electric** car looks exactly as before. If the guess is wrong, set
  **Drivetrain** in the card editor (`drivetrain: bev | phev | ice`). Only the
  electric layout has been seen on a real car so far — a screenshot from a hybrid
  or combustion car is very welcome. Card **1.12.0**.

### Fixed
- **A charge away from home could be booked with another car's energy from your
  wallbox.** The *wallbox energy sensor* was read for every charge the car
  reported, wherever it was plugged in. Had your car charged at work while someone
  else charged on your wallbox, that meter's advance would have been taken as
  this car's measured grid energy and cost — and a charge of a similar size
  passes every plausibility check. The meter is now read only for charges in your
  **Home** zone; a charge with no known position still uses it, cross-checked as
  before.
- **The "stream data has never arrived" repair appeared on every car.** BMW
  publishes one catalogue for its whole fleet, so no car sends every field of a
  data cluster — the maintainer's i5 was warned about 154 "missing" fields, from
  a third seat row to a fuel tank, on a perfectly healthy stream, and a petrol car
  was warned about the entire electric cluster. The warning now appears only when
  a selected cluster has sent **nothing at all** for 7 days, which is what a Data
  Selection that didn't save looks like. Partly-filled clusters, the *Vehicle
  events* cluster (teleservice calls can be months apart), and the *Electric
  vehicle* and *Vehicle basic data* clusters on a car that shows fuel data and no
  high-voltage battery never raise it. An existing warning clears itself on the
  next check. `get_coverage_report` still lists every missing field, and now also
  names the clusters that don't apply to the car.
- **The car's measured state of charge was hidden on every install from before
  v0.9.6.** That release switched the *HV battery state of charge* entity
  (`batteryManagement.header`) to enabled by default, but Home Assistant decides
  an entity's enabled state only once, when it is first registered — so every
  existing install kept it disabled, and nothing ever revisited it. Found on the
  maintainer's own car, where it had sat disabled since the July install. No
  recorded data was affected (the integration reads the stream, not the entity),
  but the measured figure was invisible next to the estimate, and a disabled
  entity never restores after a restart — which is why the evcc bridge's
  `updated` topic went missing after one until the car next reported. On start-up
  the integration now re-enables any entity **it** disabled whose default has
  since been turned on. Entities you disabled yourself are never touched, and
  descriptors still off by default stay off. Home Assistant reloads the
  integration once, about 30 seconds after the first start on this version, when
  something was re-enabled — one extra stream reconnect, once.
- **The card's state-of-charge ring could show the fuel tank or the 12 V
  battery.** The *Tank level (%)* sensor was tagged as a battery (its BMW name
  ends in `.level`), and every car also reports its 12 V battery as a
  percentage. The ring took the first battery percentage it found, so which one
  won came down to the order the entities were registered: a plug-in hybrid
  could show its tank instead of its charge, and a petrol or diesel car its 12 V
  battery. The ring now asks for the high-voltage state of charge by name and
  never takes the tank or the 12 V battery. The tank level is no longer a
  battery in Home Assistant either — it keeps its history and now carries a
  fuel-pump icon. On a petrol or diesel car the ring reads "—" for now; a
  drivetrain-aware overview is planned. Card **1.11.1**.

## [0.9.9-beta.7] - 2026-09-13

### Fixed
- **The evcc bridge published no plug state for a BMW i5.** Cars report "is a
  cable in" through different descriptors, and the bridge only read two of them
  — neither of which an i5 streams. So a plugged-in i5 (the maintainer's own,
  found on a live check) gave evcc no `status` and no `plugged` topic, and the
  troubleshooting text blamed the car. It now also reads *Charging Port plug
  state* and *Charging Port state text*. The source was checked against ten days
  of the recorder before trusting a charge controller to act on it: `connected`
  ahead of every charge, and never still `connected` when a drive began (27
  trips). The chain now lives in the HA-free `evcc.py` with its own tests,
  including one that refuses any source not delivered over the stream — a
  REST-polled plug state is stale by construction.
- **A genuine wallbox reading could have been refused on a small charge.** The
  cross-check rejected a meter delta below 90 % of our battery-side figure, on
  the reasoning that the grid cannot deliver less than the pack absorbed. Tested
  against a real wallbox over twelve sessions, the meter read a median **0.989**
  of our figure: below it, which the grid cannot do, so it is *our* figure running
  a little high — and three sessions sat within four percent of refusal. That
  floor was using the less reliable number to veto the measurement, on exactly
  the charges where ours is weakest. It now allows for our own error, which is
  bounded by the state-of-charge ceiling in absolute terms (about 1.5 kWh), not
  as a percentage. A meter that barely moved is still refused.

## [0.9.9-beta.6] - 2026-09-13

### Fixed
- **The bridge's settings screen failed Home Assistant's own validation.** Its
  help text spelled the topic layout the obvious way — `<prefix>/<VIN>/soc` —
  and hassfest rejects *any* translation string containing something tag-shaped,
  which fails validation for the whole integration rather than for that one
  string. Written as `PREFIX/VIN/soc` now. This is the same class of trap as the
  no-URLs-in-translations rule, so it has the same answer: a test
  (`test_translations_contain_no_html`) that catches it in CI before a release
  does, proven to fail on the exact string that got through.


## [0.9.9-beta.5] - 2026-09-13

### Added
- **evcc / wallbox bridge — hand your charge controller the state of charge, at
  no API cost.** BavarianData has the one thing evcc, openWB and friends cannot
  get cheaply: a charge level that arrives over BMW's stream as the car reports
  it. Their own BMW integrations poll a rate-limited vendor API on a timer, which
  is the most common complaint in their issue trackers. **Configure → evcc /
  wallbox bridge** now republishes what we already know onto your own MQTT
  broker, and the screen that follows hands you the evcc `custom` vehicle
  configuration with your VIN, your topic prefix and your pack size already
  filled in — paste-ready, the way the portal Data Selection snippet is.
  Published under `<prefix>/<VIN>/`: `soc`, `status` (evcc's A/B/C), `range`,
  `odometer`, `limitSoc`, `chargePower`, `plugged`, `charging`, `updated`, and
  `state` as one JSON document for openWB's MQTT SoC module or a Node-RED flow.
  It publishes through **Home Assistant's own MQTT integration**, so it lands on
  exactly the broker evcc already reads and there is no host, port or password to
  enter — and if you have no MQTT integration the settings screen says so
  instead of failing quietly. Off by default.

  Three decisions worth knowing, because a charge controller *acts* on what we
  publish — which makes this the one place where a confident wrong number does
  more than mislead a dashboard:
  - **Anything the car doesn't report is not published**, rather than sent as a
    zero. Told `soc: 0` a controller charges a full battery; told `status: A`
    ("no vehicle connected") an identifying charger can stop charging the car it
    is plugged into. A car that streams no charging-port descriptor therefore
    gets no `status` topic at all. Charging power is published only while the car
    is *known* to be charging, because BMW's reading lingers at its final value
    for days after the plug comes out.
  - **Everything is re-sent every five minutes, unchanged.** A parked car streams
    nothing for days and its charge level is no less true for it, so the
    generated config deliberately sets no `timeout` and the bridge keeps the
    values current on the broker instead.
  - **Switching the bridge off removes what it published**, and so does removing
    the integration. Retained messages are held by the broker, not by us — which
    is what lets evcc find the charge level the instant it starts, and equally
    what would leave it charging forever against a value that stopped updating.
    A restart or reload deliberately leaves them: the last thing we knew is still
    true for those few seconds.
- `bavariandata.get_evcc_config` returns that configuration again at any time,
  plus the topics the bridge owns, which of them are currently live, and whether
  Home Assistant has an MQTT integration to publish through — the three things
  worth knowing when nothing shows up on the broker. Local only, no quota.

### Fixed
- **The wallbox energy sensor now actually does something.** The setting has been
  offered since 0.9.0 and the manual promised "that exact grid figure is used
  instead" — but nothing ever read it: the entity id was stored, carried into
  the pricing config, and dropped. Bind your wallbox's cumulative energy total
  and each session now records a measured `grid_kwh` alongside the battery-side
  figure, which the monthly totals, the long-term statistics and the CSV export
  all prefer where it exists. The **cost is billed from the meter's own advance
  as the charge proceeds**, on the same sample as the source mix, so a dynamic
  tariff still prices each kilowatt-hour at the rate in force when it arrived —
  applying a session total at close would instead price the whole charge at
  whatever the tariff happened to be when the plug came out. The measured
  charging loss on the efficiency view stops being an assumption, and the
  *charging loss %* setting becomes what it was always meant to be: the fallback
  for people with no meter to bind.

  A measured number is only worth having if it is measuring *this car*, so each
  session's delta is **refused rather than believed** when it cannot be right:
  a meter that went down or nowhere (a reset or power cycle), one reporting less
  than the pack absorbed (physically impossible), or one reporting nearly twice
  it — which is the common misconfiguration of binding the *house* import meter,
  and would otherwise inflate every cost by whatever else the house was doing for
  those hours. A refused reading leaves the session on its battery-side figure;
  nothing is ever presented as measured that isn't. The cross-check is skipped
  for a session whose own energy is already known to be short — one interrupted
  by a restart, or one that started before we noticed — because there the meter
  is the only thing that saw the missing part, and that is exactly where it earns
  its keep. The baseline rides the open-session snapshot, so a restart mid-charge
  keeps it.

### Documentation
- New wiki page **evcc & wallbox bridge**, covering both directions, the full
  topic table, the three deliberate behaviours above, when a wallbox reading is
  refused, and the troubleshooting path for "nothing appears on the broker".
  Settings and Services reference, `Home`, the sidebar and the coverage matrix
  updated; `docs/clean-install.md` gains a section on retained MQTT messages,
  which are the one thing this integration leaves behind that lives on someone
  else's machine.
- The charging-history page no longer claims behaviour that did not exist, and
  `get_efficiency` — which had a reference section but no row — is now listed in
  the Services table.


## [0.9.9-beta.4] - 2026-09-12

### Fixed
- **Distances are whole kilometres again.** Home Assistant stamps two decimals on
  any sensor whose unit it can convert, so the card's headline read
  **"379.00 km"** — and the only cure was for every user to set the precision by
  hand, entity by entity. Ranges, the odometer and the lifetime reference
  distances now declare whole kilometres, which is the resolution BMW streams
  them at.
- **The car's own remaining range is now a properly classified sensor.** BMW
  ships `kombiRemainingElectricRange` with an empty unit ("Remaining electric
  range in km or mi"), so it arrived with no unit, no device class and no
  statistics — and the card's range tile, which looks for a distance, could not
  see the one range worth showing. It was only classified at all on installs
  where a restored state happened to carry "km". Now pinned to km / distance /
  measurement, next to the odometer override that exists for the same reason.

### Changed
- The month summary's `energy_balance` is documented as what it is: grid-side
  when a measured grid figure exists, battery-side otherwise, with `source`
  saying which. It was described in the code as always grid-side.

## [0.9.9-beta.3] - 2026-09-12

### Fixed
- **The main card showed the wrong range.** Three entities on an electric car
  answer to "electric range", they scored identically in the card's entity
  matching, and which one won came down to the order Home Assistant happened to
  list them in. The one that kept winning is BMW's *estimate during charging* —
  on a parked car that is whatever was predicted mid-charge, and it read **128 km
  on a car sitting at 86 % with a real 379**. The card now names the figure it
  wants, `kombiRemainingElectricRange`, the number on the car's own display, and
  explicitly rejects both impostors: the during-charging estimate and the range
  at the *target* state of charge. They stay available to the fallback for a car
  that streams nothing better, where they are chosen knowingly.
- **The Real Range sensor could still miss a restart.** v0.9.9-beta.2 taught it to
  watch its inputs arriving on the stream, but a restart delivers them without a
  stream message at all: every descriptor entity restores its own state and hands
  it back to the coordinator silently, in whatever order the entities are added.
  A car parked over a restart therefore kept the empty profile it wrote before
  that restore landed — no comparison against BMW, and a capacity figure left
  over from the previous run. It now also watches the stream heartbeat, which is
  the one tick that always comes, and re-reads the ledger only on the rare tick
  where one of those two figures has actually moved.

## [0.9.9-beta.2] - 2026-09-12

### Fixed
- **The Real Range sensor no longer freezes at what it knew when Home Assistant
  started.** It recomputed itself when a charge landed or the state of charge
  moved — but the two figures it is measured *against*, the car's own
  remaining-range prediction and the pack capacity, arrive as ordinary stream
  messages that do neither. On a car parked after a restart, that left the
  comparison against BMW missing from the sensor's attributes and from the card's
  *Efficiency & range* view, and it stayed missing until the next drive or
  charge. Found on a live instance: the sensor reported no BMW range at all while
  the `get_efficiency` action, reading the same data a moment later, answered
  379 km.

## [0.9.9-beta.1] - 2026-09-12

### Added
- **How far the car really goes — measured, not predicted.** A new **Real
  Range** sensor divides the usable battery capacity by the consumption your own
  charging history actually recorded, and scales it to the charge in the car
  right now. Its attributes carry the whole story: the consumption it used, which
  side of the charger that figure describes, how many days it was measured over,
  the capacity it divided into and where that came from, and what the car's own
  remaining-range prediction says for comparison.

  Consumption is measured the way the trips card's headline figure already is —
  two charging sessions bracket a window, and the odometer and state of charge at
  each end give the distance and the energy without a single drive having to have
  been detected. What is new is that it picks **the shortest window that can
  answer**: 30 days, then 90, then a year, then everything on file. A fixed month
  goes blank for anyone who charges rarely; “all time” is still quoting last
  winter in June. Whichever it used is published beside the number, because
  17.5 kWh/100 km means different things over a month and over a year.

  Battery-side and grid-side figures are never mixed. Range is computed from the
  battery side only — feeding it a grid figure would have the car driving on the
  charging losses — and a window that contains a charge which can only be read on
  one side reports nothing on the other rather than summing a hole.
- **A new `view: efficiency` card view.** The real range from here, how it
  compares with the car's own estimate, the measured consumption with its window
  and its side of the charger, the measured charging loss, the usable capacity,
  your cost per 100 km with the month's solar share, and a bar chart of
  consumption by calendar month — which is where the seasonal story shows up,
  since winter consumption on an EV runs routinely a third above summer.
- **A `get_efficiency` service** returning the same profile plus the month trend,
  for templates and automations. Local store only, no BMW API quota.
- **The charging loss is now measured, where it can be.** With a wallbox energy
  entity bound (or BMW's own imported charging records), grid-side and
  battery-side consumption are computed over **the same window** and their gap
  reported as `measured_loss_percent`. It is deliberately named apart from the
  *charging loss %* setting, which is an assumption you supply so cost can be
  grossed up when nothing measured the wall — the measured figure is a good
  sanity check on the number you typed there.
- **Where each charge's energy came from: PV, house battery, or the grid.**
  Point **Configure → Solar & energy sources** at your PV power and grid power
  sensors (house battery optional) and every session records the split in
  grid-side kWh, plus the share that came off your own roof. The card tags the
  session row with **☀ 62 % solar** and breaks it down when expanded; the
  month's share rides as `solar_percent` / `energy_mix` attributes on the
  existing *Charging energy this month* sensor — no new entities; the CSV export
  gains four columns and the printed month report a tile.

  Energy is attributed **as it is delivered** from the site's supply mix at that
  instant — the same live sampling the tariff already uses — so a charge that
  starts in sunshine and ends after dark is split rather than filed under
  whichever came last. The car is treated as just another load and gets the mix
  the rest of the house got at that moment: no convention that hands the car the
  sunshine first, none that makes it the marginal load carrying the import.
  Exported PV is excluded, and PV going into the house battery is attributed
  later, as battery, so nothing is counted twice. A missing sensor makes energy
  *unattributed* rather than *grid*, and a session that could attribute nothing
  records no mix at all — "we couldn't tell" must not look like "no sun".
- **Optionally, what that solar is worth.** Set **Value of own solar per kWh**
  (usually your feed-in tariff) and cost becomes what a charge really cost you.
  Left empty, solar is billed at the import price and existing totals keep their
  old meaning. House-battery energy is always billed at the import price: it
  could have come from the roof at noon or from a cheap-hour grid charge at
  three in the morning, and that is not knowable here.

### Fixed
- **The monthly driving-distance sensor now appears on cars that report
  `travelledDistance`.** It was created only for a car streaming
  `vehicle.vehicle.mileage` or `vehicle.isMoving` — and the i5 streams neither,
  only the other spelling of the odometer. On such a car the sensor was never
  created at all, however many trips were recorded underneath it; the
  cost-per-100 km sensor was gated the same way and had the same gap. Both now
  accept either odometer descriptor, which is what every other part of the
  integration already did.
- **A restart no longer loses the charge that was running.** An in-progress
  charging session lived only in memory, so any Home Assistant restart, update
  or options reload mid-charge deleted it — and Home Assistant does not unload
  config entries on shutdown, so the existing flush never ran on a restart at
  all. The session is now snapshotted to the history store as it charges, and
  picked up again on the way back in: still charging, and it carries on as the
  same record; already over, and it is filed ending at the last sample actually
  watched, not at the moment we noticed. The energy the pack gained during the
  gap is credited back from the SoC that measured it, bounded by the same
  ceiling as everything else, and such a record is flagged `interrupted` so
  nothing that divides energy by SoC treats it as a clean measurement.

  This was not a cosmetic loss. Measured on a live instance, two restarts that
  happened to land mid-charge cost about **22 kWh in a single week** — every
  total built on the ledger (session energy, cost, the Energy dashboard
  statistics, and the driving summary's energy balance) read low by exactly
  that much. It is why the trips dashboard could show a consumption figure well
  below what the trips underneath it added up to.
- **A restored charging rate no longer masquerades as a charge in progress.**
  The state was set from the restored rate sensor alone, so the next genuine
  `CHARGINGACTIVE` looked like no transition at all and opened no session.
- **A drive in progress at a restart is now captured too.** The same root
  cause: Home Assistant does not unload config entries when it shuts down, so
  the existing "close the open trip" step ran on a reload but never on a
  restart. Both flushes are now wired to the shutdown event.

## [0.9.8] - 2026-09-12

The first stable release since 0.9.7. No code has changed since
`v0.9.8-beta.2`: this promotes the two 0.9.8 betas unchanged, so everything
below has already been running in the betas.

### Breaking
- **"Doors overall state" now reports a lowercase slug** (`SECURED` →
  `secured`, displayed "Secured"). Automations that match the old ALL-CAPS text
  silently stop matching — open the condition and re-pick the state from the
  dropdown, which now offers the real values. This is the one disruptive part of
  this release; it is what made the entity usable from the automation editor in
  the first place.

### Also in this release
- **The streamed lock state is usable in automations, and the card follows it**
  (0.9.8-beta.1). `Doors overall state` is the lock BMW actually streams —
  `Doors lock` is REST-only and can sit stale for days on a 50-request budget —
  but its values parsed as no enum at all, so the state picker offered only
  *Unknown* and *Unavailable*. It is now a proper enum in both languages, the
  card's central-lock tile prefers it, and trip capture watches it. Reported by
  @nevryn ([#8](https://github.com/JustChr/BavarianData/issues/8)).
- **Two BMW accounts start up together again** (0.9.8-beta.2). Home Assistant
  sets all config entries up at once and both registered the bundled card's
  static route, so the second entry failed on every restart and stayed
  unavailable until reloaded by hand. Found and fixed by @netbasebe — the first
  external code contribution ([#9](https://github.com/JustChr/BavarianData/pull/9)).
- **BMW's unit spellings are canonicalised in one shared table** (0.9.8-beta.1),
  and the catalogue pipeline now refuses to build on a unit it cannot name —
  an unrecognised spelling used to strip a sensor's device class, unit
  conversion and long-term statistics with nothing in the build saying so.

See the beta sections below for the full detail of each.

## [0.9.8-beta.2] - 2026-09-11

### Fixed
- **With two BMW accounts, one of them no longer fails to set up after a restart.**
  Home Assistant starts all config entries at the same time, and both registered the
  bundled card's static route; the second hit `RuntimeError: Added route will never be
  executed, method GET is already registered` and stayed unavailable until it was
  reloaded by hand. The card is now registered exactly once, however many accounts
  start together. Thanks to @netbasebe for tracking it down and fixing it (#9).

## [0.9.8-beta.1] - 2026-09-09

### Changed
- **"Doors overall state" is now a proper enum, so it can be picked from the
  automation editor instead of typed.** It is the *streamed* central lock —
  `vehicle.cabin.door.lock.status` is not streamable and only refreshes on a
  quota-limited REST call, so it can sit on a stale value for days while this one
  follows every lock/unlock within seconds. BMW documents its allowed values as a
  compound of four raw sub-fields (`oldDoorStatus: ASN_secured …`), which parsed
  as no enum at all, leaving it a free-text sensor whose state picker offered
  only *Unknown* and *Unavailable*. It now carries the lock vocabulary the stream
  actually sends — `Secured`, `Locked`, `Partially locked`, `Unlocked` — in both
  languages. Thanks to @nevryn for the report (#8).
  **Breaking for existing automations:** the state is now a lowercase slug
  (`SECURED` → `secured`, shown as "Secured"), so conditions that match the old
  ALL-CAPS text need updating — re-pick the state from the dropdown.
- **The card's central-lock tile follows the streamed lock.** It read
  `Doors lock`, which meant the padlock could show "Secured" for days after the
  car was unlocked. It now prefers `Doors overall state` and falls back to
  `Doors lock` on cars that never stream it.
- **Trip capture watches the streamed lock too.** The `[trip.watch]` line listed
  only the REST-only `door.lock.status` as an entry/exit bracket, which never
  arrives on the stream; it now also carries `door.status`, which does.
- **`SELECTIVE-LOCKED` now reads "Partially locked" ("Teilweise verriegelt")**
  on `Doors lock`, matching the card's wording; it previously read "Selective
  locked" and had no German label at all.
- **BMW's unit spellings are now canonicalised in one place instead of two.**
  The catalogue export and the live stream do not use the same vocabulary — the
  export says `percent`, `Celsius`, `degrees`, `l`; the stream says `kpa` for
  the very descriptors the catalogue calls `kPa`. There was a translation table
  for each side, and only the build-time one had been filled in (24 entries
  against the runtime's one), so stream spellings reached entities and
  diagnostics unnormalised. Both sides now share
  `custom_components/bavariandata/units.py`. Visible effect is small — GPS
  latitude/longitude/heading show `°` rather than `degrees` — and no sensor that
  records long-term statistics changes unit, so no history is affected.

### Fixed
- **A future BMW catalogue update can no longer silently strip a sensor's device
  class.** A descriptor's device class and state class are derived from its unit
  string by exact match, and an unrecognised unit was passed through unchanged —
  so a catalogue that spelled `kPa` as `kpa` would have turned all eight
  tyre-pressure sensors into plain unclassified ones: no pressure device class,
  no unit conversion, and no long-term statistics, with nothing in the build
  saying so. The generator now refuses to run on a unit it cannot name, tests
  pin the device classes that carry a feature, and the whole unit vocabulary is
  covered by tests for the first time.

## [0.9.7] - 2026-09-09

### Fixed
- **Sensor values no longer shrink a little more with every restart.** Home
  Assistant saves the value it *displayed*, not the one we reported, and the
  integration read that back as if it were the raw reading — so any sensor shown
  in a unit other than the one BMW sends had its conversion applied again on
  every restart. Tyre pressure held in kPa but displayed in bar was divided by
  100 each time: 250 kPa became 2.5, then 0.025, and after five restarts the
  2.5e-10 bar reported in
  [#7](https://github.com/JustChr/BavarianData/issues/7). It could not
  self-correct, because the unit never disagreed with itself — only a fresh
  message from the car reset it, which is why slow-moving readings like tyre
  pressure were where it showed. The same descent is visible in the maintainer's
  own history on a charge-time sensor displayed in hours, dividing by 60 a step
  at a time.

  Sensors now save their native value *and* its unit (Home Assistant's
  `RestoreSensor`), so there is nothing left to infer. This affected any entity
  whose unit was changed in its settings, and — with no user action at all —
  distance, speed, temperature, pressure and volume on installs using the US
  customary unit system, where Home Assistant picks the display unit itself.

  **After updating, an affected sensor reads as unknown until the car next
  reports it** (for tyre pressure, that means the next drive). A value already
  decayed cannot be recovered, and restoring it would only preserve a wrong
  number; recorded history for those entities keeps the bad readings.

## [0.9.6] - 2026-09-08

### Documentation
- **The wiki now asks for feedback on the pages users actually reach.** A short
  ask closes *4. Choose which data to stream* (which model, how many entities
  appeared, whether setup needed the portal snippet), *The dashboard card* (a
  screenshot on anything that isn't an i5) and *Troubleshooting & FAQ* (say so
  when nothing on the page matched). Placed only where a reader has just
  succeeded or just failed — the earlier Getting Started steps are mid-funnel and
  are left uninterrupted. The links point at the Discussions index rather than a
  numbered thread, so they survive announcement-thread rotation.
- **Install instructions now describe the HACS default store.** BavarianData was
  accepted into the HACS default list
  ([hacs/default#9018](https://github.com/hacs/default/pull/9018), merged
  2026-08-25), so it installs by searching HACS — the README quick start and the
  *Install via HACS* wiki page no longer walk users through adding a custom
  repository. That route is kept on the wiki page as a fallback for instances
  whose HACS data hasn't refreshed yet, alongside a note that the missing logo
  in the HACS list is a HACS-side limitation and not a broken install.

### Security
**If you have attached a diagnostics download to a public issue or forum post,
it may contain your VIN and the entity id of your price sensor. Both are fixed
below; a file downloaded before this release is not retroactively cleaned, so
consider replacing it.**

- **The VIN escaped the diagnostics redaction through a repair id.**
  `diagnostics.py` builds a safe payload and runs `async_redact_data` over it,
  but that covers only *our* payload: Home Assistant's own diagnostics wrapper
  appends the registered repair issues to the download, and the coverage repair's
  id was `stream_coverage_gaps_{entry_id}_{vin}`. The VIN therefore rode straight
  into the one file users are asked to attach to an issue. The v0.9.5 log masking
  was never at fault and is intact — this escaped as an *identifier*, not a
  message. The id is now a stable 12-character digest of the VIN, and the
  pre-0.9.6 id is deleted on every refresh so an existing repair is cleared
  rather than orphaned, which also removes the leaked value from the issue
  registry. The repair body's vehicle name also fell back to the raw VIN for a
  car with no name, and now falls back to the masked form. Guarded in
  `tests/test_services_and_privacy.py`.
- **Diagnostics dumped the entity id of your price and wallbox sensors.** The
  config-entry options are included verbatim, and `price_entity` /
  `grid_energy_entity` hold an entity id the user picked — free text named by
  whichever integration created it, which routinely embeds identifiers of its
  own. Energy-supplier integrations in particular name theirs after the meter
  serial and supply-point number, so a home's electricity connection could travel
  with the file. Both are now reduced to their domain (`sensor.**REDACTED**`);
  whether one is configured is all the triage ever used.

### Fixed
- **The state of charge was never activated on the stream.** `enabled_default`
  in `descriptor_metadata.py` is generated by substring-matching the descriptor
  in `tools/generate_metadata.py`, and it decides far more than whether an entity
  starts enabled: `descriptors_for_sections()` streams only what it marks, so the
  flag governs what the portal snippet ticks, what the stream activator turns on,
  what onboarding selects, and what the coverage self-test expects. The pattern
  `.header` matched exactly one descriptor in BMW's 295-field catalogue —
  `vehicle.drivetrain.batteryManagement.header`, which despite the name is the
  **high-voltage state of charge** the whole charging history is built on. It was
  therefore never ticked in Data Selection, never arrived, and was not reported
  missing either, because it had been excluded from the expected set as well.
  Anyone who set up Data Selection with our snippet has had a state of charge
  frozen at whatever the REST bootstrap last wrote
  ([#6](https://github.com/JustChr/BavarianData/issues/6)). Fixed in the
  generator and regenerated; a new guard in `tests/test_review_guards.py` pins
  every stream-critical descriptor into the activation set so a future pattern
  can't quietly drop one. **Existing installs must re-run the Data Selection
  snippet** under Configure — the coverage repair now names the missing
  descriptor and says so.
- **The card's gauge showed the trip-end state of charge.** Two battery-class
  percentages impersonate the live SoC in the overview's entity pick: BMW's
  *predicted* SoC (`charging.level`, REST-only, so it never updates on the
  stream) and the SoC at the end of the last trip, which only moves when a drive
  finishes. With the live SoC missing (above) one of them silently won the pick,
  so the reported iX showed a confident 58 % that was hours old and disagreed
  with its own charging history. Both are now excluded from the primary pick and
  kept only as an explicit fallback. The predicted one is matched on its
  descriptor rather than its name: the old `"predicted"` keyword only rejected it
  in English, so a German install picked it in preference to the real thing.
  Card **1.11.0**.
- **The state-of-charge sensor had no `device_class`.** The generator infers it
  from `stateofcharge`/`soc`/`.level` in the descriptor and
  `batteryManagement.header` contains none of them, so HA had no idea the most
  important sensor in the integration was a battery percentage — and the card's
  gauge could not find it even once it was streaming. Now set explicitly.
- **A frozen state of charge no longer wrecks the charging figures.** Downstream
  of the above, and the reason it was so destructive rather than merely missing:
  with the same stale reading at both ends of every session, the SoC arc read
  "38 → 38%" and the energy ceiling saw a rise of zero, pinning every charge to
  its bare margin — 1.4 kWh on a 71 kWh pack, whatever the car actually took.
  Four DC charges peaking above 100 kW were each filed as 1.4 kWh, with the
  average power and the cost that follow from it. A SoC reading now only
  describes a session if it was taken during it; without one there is no ceiling
  (the energy stands on the power integration alone) and no SoC arc, which leaves
  both ends free for BMW's own charging history to fill in on the next import.
  Duration and peak power were always right and are unchanged. This holds for any
  car that genuinely doesn't produce the descriptor, not just for the
  misconfiguration above.
- **README overstated the export formats.** The feature summary advertised
  "CSV/PDF export", but `export_history` emits CSV and a self-contained HTML
  report — there is deliberately no PDF renderer (see `history/export.py`); the
  HTML report prints to PDF from any browser. Wording only, no code change.
  Spotted by @frenck during the HACS default review
  ([hacs/default#9018](https://github.com/hacs/default/pull/9018)).

## [0.9.5] - 2026-08-20

### Security
- **The last unescaped value in the bundled card.** v0.9.4 escaped every value
  the card renders, but one site survived: the cluster view's empty state passes
  the card's `title:` option through a translation placeholder, and `t()`
  substitutes placeholders raw so that a caller can deliberately pass markup.
  A cluster card whose title contained markup therefore had it parsed rather
  than shown, on any vehicle where that cluster has no entities. Card **1.10.1**.
- **The VIN is masked in ordinary log lines.** Verbose debug logging is opt-in
  because it carries the VIN and GPS position — but 27 lines at INFO and above
  wrote the full VIN to `home-assistant.log` on every install, and that file is
  what users paste into public issues. Those now show the last four characters
  (`***1234`), which still tells two cars apart while reading a log. Debug lines
  are unchanged: triage needs the full value, and the user opted in to it.

### Fixed
- **`webhook` is now declared in `manifest.json`.** Guided setup registers a
  webhook for the in-browser stream activator to report its result to, but never
  declared the dependency. On an install without `default_config` the route
  would not have existed, and the guided flow would have silently fallen back to
  the manual paste step.

### Changed
- The charging-price options are read through the shared `OPTION_*` constants
  instead of repeated string literals, so renaming one cannot silently reset
  everybody's pricing to the defaults.

### Documentation
- **The diagnostics download is documented at last.** It was shipped but
  described nowhere — despite being the one artifact that answers most "it
  doesn't work" reports. Troubleshooting now has a *Download diagnostics*
  section covering what it contains, what it redacts, and that it costs no API
  quota, and *Where to get help* points at it.
- **Settings reference now matches the screen.** Seven rows on the
  *Charging costs & history* and *Debug logging* screens were documented under
  paraphrased names (“Price mode”, “Charging loss %”, “History retain months”)
  that never appeared in the UI. They now read exactly as the dialog does.
- Fixed a broken link on the card page pointing at a retention page that does
  not exist; it now goes to the retention setting itself.

### Added
- **The HACS review audit now runs in CI.** Two Home Assistant-free guard
  modules replace a checklist that was being re-derived by hand before each
  release: `tests/test_card_escaping.py` scans the card for user-controlled text
  reaching `innerHTML` unescaped (it catches the bug above), for Leaflet tooltips
  and for external resource loads; `tests/test_review_guards.py` pins the classes
  HACS review actually rejects for — disabled TLS verification, new
  unauthenticated HTTP views, credentials reaching logs or diagnostics,
  undeclared integration dependencies and payload size.

## [0.9.4] - 2026-08-18

### Security
- **The bundled card now escapes every value it renders.** The card composes its
  views as HTML strings, and several sources of user-controllable text reached
  `innerHTML` unescaped: the card's `title:` option (in both the heading and the
  subtitle), the device name as renamed in Home Assistant, entity
  `friendly_name`s, formatted entity states and their units, the free-text
  **currency** option, and the cluster heading. The trip map was affected too —
  Leaflet treats a tooltip string as HTML, and an endpoint tooltip carries a zone
  name or a reverse-geocoded address. Markup placed in any of those was parsed
  rather than shown.

  Escaping now happens at the source wherever there is one — `_fmt()`,
  `_shortName()` and `_fmtCost()` escape what they return — plus at each
  individual site the vehicle name, card title or currency is interpolated, and
  on the map tooltip. A value containing `&`, `<`, `>` or `"` now displays as
  typed. The one deliberate exception is the trip-map subtitle, which is written
  through `textContent` and must stay unescaped.

  Setting any of those strings requires an authenticated Home Assistant user, so
  this was not remotely reachable — but a card should not depend on that.

### Changed
- The onboarding helper view now carries a full rationale for why it is served
  unauthenticated (one-time capability token, GET-only, no identifiers in the
  page, torn down with the flow), and `stream.py` records why `paho-mqtt` stays
  declared in the manifest even though Home Assistant's container image already
  ships it — a Home Assistant Core install does not.

### Also in this release
This is the first stable release since 0.9.3. It carries the two fixes published
in the 0.9.4 betas: **average consumption was badly overstated** (0.9.4-beta.1)
and **charged energy could overshoot when the stream went quiet mid-charge**
(0.9.4-beta.2). See those sections below for the detail.

## [0.9.4-beta.2] - 2026-08-14

### Fixed
- **Charged energy could overshoot badly when the stream went quiet mid-charge.**
  BMW sends charging power in *bursts*, sometimes with over an hour between them,
  while the integration assumes the last reported power held until the next
  reading — and it re-integrates on a watchdog tick as well as on each message.
  Held across a long gap, one unrepresentative sample dominates: a real session
  integrated a 3.54 kW reading for 162 minutes and recorded **11.10 kWh where the
  battery had taken 5.46**. Another claimed 11.04 against 7.02.

  The running total is now bounded by what the pack can actually have absorbed —
  the SoC rise times the capacity, plus a margin for SoC arriving a whole percent
  at a time. It is a **ceiling, not a correction**: a session that under-read is
  left exactly as it is, because nothing can distinguish an under-read from a
  genuinely slow charge. The bound applies to the running total rather than to
  each step, so a charge held back while a SoC reading is pending recovers in
  full once it lands instead of being written off. Where SoC or capacity is
  unknown, nothing is bounded.

  This was not a uniform inflation — five of seven measurable sessions were
  already within a few percent, and one *under*-read by 2.5 kWh. It was
  occasional and large. It affected the charged-energy sensors, charging **cost**
  (billed on this energy), long-term statistics, battery-health capacity samples,
  and the plug-side consumption figure added in 0.9.4-beta.1.

## [0.9.4-beta.1] - 2026-08-14

### Fixed
- **Average consumption was badly overstated.** The monthly figure averaged the
  *per-trip* consumption ratios, so a 1 km hop counted exactly as much as a
  200 km run. Because BMW streams SoC as a whole percent — about 0.8 kWh a step
  on a 78 kWh pack — short drives can only record 0 kWh or a full step, and the
  0 kWh ones drop out while the full-step ones stay, so the survivors all
  over-read. On one real month the card showed **35.3 kWh/100 km** against a true
  figure near 21. The month is now measured two ways, both shown:
  - **An energy balance** (the headline): from the charging ledger alone — two
    charging sessions bracket a window, each carrying an odometer reading and an
    SoC, so distance and energy are both known without any drive having to have
    been detected. It deliberately reads nothing from the trip record: a month
    where a drive was missed used to divide a full month of charging by a partial
    month of driving (86.8 kWh/100 km on real data, against 20.4 from the
    odometer). It is labelled **at the battery** or **at the plug** according to
    its new `source` field — plug-side only when every contributing charge
    carried a *measured* `grid_kwh`, since BMW streams battery-side charging
    power and an estimated session never saw the wall.
  - **The battery-side trip figure**: total trip energy over total trip distance —
    a distance-weighted total, not an average of ratios. Shown beside the balance
    only when the balance is grid-side, where the gap between them is the
    charging loss; otherwise both measure the same quantity and showing two would
    dress the difference between their windows up as a loss.
- **Per-trip consumption is withheld below a 3 % SoC drop**, where the
  quantisation *is* the measurement. Such trips keep their distance, duration and
  energy but show no rate, and can no longer be nominated "best" or "worst" trip
  of the month — a 1 km errand was being reported as the month's worst drive at
  79 kWh/100 km. The figure now ships with the record instead of being
  recomputed by the card, which had been re-deriving the numbers the integration
  refuses to publish.
- **Recuperation was summed in the wrong unit.** BMW documents
  `recuperationTotal` as an average per 100 km, not a kWh total, so adding a
  month of them together produced a meaningless number. It is now a
  distance-weighted mean, labelled kWh/100 km. Records written under the old key
  are still read.
- **`to:` on `get_trips` / `get_charging_sessions` dropped the last day.** A bare
  date resolved to midnight, so `to: 2026-08-31` excluded everything that
  happened on the 31st. A bare date now means the whole day at either end.

- **Battery health could never leave "Learning (0/10)" on some cars.** A charge
  had to span **40 % of SoC** to count as a capacity sample — a threshold set
  before there was data to calibrate it, and unreachable for anyone who tops up
  little and often rather than running the pack down: one real car went 39
  sessions without a single qualifying charge, so the sensor was stuck
  permanently rather than learning slowly. The gate is now **25 %**, calibrated
  against measured error (on that car a 21 % charge implied 77 kWh against BMW's
  own 78, a 12 % charge 82 kWh, a 9 % charge 123 kWh). Two further rules are now
  explicit and documented: sessions imported from BMW's charging history carry
  only a grid-side figure and can never be capacity samples, and a charge that
  was **already running when the integration noticed it** is now detected
  (`late_start`) and skipped — its energy and SoC span both begin late by
  different amounts, and one such session implied a 123 kWh pack on a 78 kWh
  car. Its energy and cost still count everywhere else, as a floor.

### Added
- **The trips and charging card views show one month at a time**, with a
  `‹ August 2026 ›` control under the header. History is kept for two years, so
  both lists had grown into a single unbounded scroll that nothing on screen
  described — and on the trips view it disagreed with the month-in-review band
  right above it. The month scopes the list, the summary and the CSV / Report
  export together. A drive still under way stays pinned to the top of the current
  month: `get_trips` now returns `open_trips` for any window containing the
  present moment, not only for an unbounded query. Card **1.10.0**.

## [0.9.3] - 2026-07-31

### Added
- **Actions are now translated.** All 15 actions — their names, descriptions and
  every field — now live in `translations/en.json` and `translations/de.json`
  instead of being hardcoded English in `services.yaml`. German installs showed
  English action names in the UI; they no longer do.

### Fixed
- **Removed real vehicle identifiers from the shipped integration.** A real VIN
  was used as the `example:` for every VIN field in `services.yaml` (rendered to
  every user in the actions UI), a real config entry id as the `entry_id`
  example, and `const.py` carried three pasted debug-log dumps containing two
  VINs plus colour, build date, country and the full option list of the cars
  they came from. All replaced with the synthetic placeholder
  `WBAEXAMPLE0000000` or deleted; the response shapes those dumps documented are
  in `docs/reference/customer-api.swagger.json` anyway. A test now fails if a
  VIN- or entry-id-shaped literal reappears in any shipped file.

## [0.9.3-beta.1] - 2026-07-30

### Added
- **You can see the drive that's happening now.** Until it ended, a trip was
  invisible: nothing on the card, no entity, nothing in `get_trips`. Now there's a
  **Trip in Progress** binary sensor per vehicle carrying the trip so far
  (`started`, `start_location`, `distance_km`, `duration_s`, `soc_start`,
  `soc_now`, `energy_kwh`, `last_movement`, `held`), a **badge on the overview
  card** with the distance and minutes so far, and a **live row at the top of the
  trips view** — where you set off from, the figures so far, and the route as it
  grows when *Record route* is on. `get_trips` returns it as `open_trips`
  alongside the recorded ones. Card **1.9.0**.

  It is deliberately **not** a "car is moving" sensor and isn't named one: it says
  a *trip is open*, which starts at the first position report showing movement and
  ends five minutes after the last one — longer while the stream is quiet, which is
  what parking underground looks like. So it stays on for a few minutes after you
  arrive. The recorded trip's end is backdated correctly regardless; only the live
  flag lingers. Use `last_movement` for the finer question.
- **A configurable default type for trips.** Anything that isn't a recognised
  home ↔ work commute is now filed as your **Default type** under
  **Configure → Trips** — **Private** out of the box, **Business** if that is the
  honest default for your driving, or **Leave unclassified** to keep sorting every
  trip by hand (the previous behaviour). Trips whose endpoints fall outside any
  zone are covered too; they used to be left blank however obvious they were. An
  automatic class is never more than a starting point: a trip you classified
  yourself is never overwritten.
- **A commute survives a stop on the way.** Buying groceries between home and work
  parks the car long enough that the detector records two drives, neither of which
  is home → work on its own. With the new **Commute stop tolerance** (default
  **30 minutes**) they are recognised as one commute and *both* legs are badged as
  such — retroactively, once the chain arrives. The stops stay visible as separate
  trips in the journal. A chain ends when it reaches home or work, so a lunch run
  out of the office and back is not dragged into the morning commute, and a round
  trip that starts and finishes at home stays private. Set the tolerance to 0 to
  switch chaining off.

### Fixed
- **A quiet position stream no longer splits one drive into two trips.** "No
  movement for five minutes" was treated as the car having stopped, when it can
  just as easily be the stream going silent — a tunnel, a coverage hole, or the
  i5's own sparse cadence. A stop is now only acted on once something confirms it
  (a position report showing the car standing still, or an explicit "not moving");
  otherwise the drive is held open until the reports come back and settle it. A
  car that reappears where it vanished still ends its trip back at the last
  movement, so a park in a signal-dead garage is not merged into the next drive.

## [0.9.2] - 2026-07-27

Trips grow up: every recorded drive can now be drawn on a map — a route line per
trip and a clustering map of where you actually go — and a trip's start and end
times finally describe the drive rather than the moment the detector noticed it.
Tires gain BMW's wear diagnosis next to pressure on a rebuilt tire card. And the
REST poller stops eating your quota: the daily container now carries the 41
fields the stream cannot, taking normal running from about 36 requests a day
down to 2 — while filling entities that until now could only ever sit empty.
Everything below accumulated across the 0.9.2 pre-releases; upgrading from 0.9.1
gets it all at once.

### Added
- **Each trip now shows its route on a map.** Expanding a trip in the Trips card
  view draws that drive on a small map — a single clean line with a start and an
  end marker — for trips recorded with **Record route** on. No map data leaves
  your browser to do it.
- **A destinations map (`view: map`) on the dashboard card.** A new card view
  plots where your trips **end** as markers that **cluster into counted bubbles
  when zoomed out and split apart as you zoom in** — a quick read on where you go
  most, with an honest "times arrived here" count (a trip's start is the previous
  trip's end, so plotting both would double-count). A time-window chip row (This
  month / 3 months / All) filters it. It reuses Home Assistant's own map and
  clustering, so there are no new dependencies, and it reads routes via
  `get_trips`, so it spends no API quota. Destinations appear once **Record
  route** (`trip_track`) is enabled; the map shows a hint until then.
- **Tire wear, on a rebuilt tire card** (card 1.8.1). BMW's smart-maintenance
  tyre diagnosis was being fetched and thrown away — `fetch_tyre_diagnosis`
  logged it and nothing else. It now becomes five entities per car (**Tyre
  Condition** plus one per wheel BMW reports), and the card is a tire card rather
  than a tire-*pressure* card: titled **Tires**, it opens with the two things
  that can actually be wrong with one, side by side — **Pressure** (the measured
  spread across the set, with the shared target under it) and **Wear** (BMW's
  verdict, with the mileage until the soonest wheel is due). Each wheel carries
  its own size, tread pattern, season and fitting date beside it, because
  staggered setups are normal: the i5 runs 245s at the front and 275s at the
  rear, and a single shared line had to show the wrong size for half the car.
  Wear outranks pressure in the wheel colour and the header badge — a tyre BMW
  flags as worn reads "Check tires" even at perfect pressure. Note that BMW
  reports **remaining mileage, not tread depth**; `tread` is the tread *pattern*
  ("EcoContact 6 Q"). Cars with no tyre service record on file get no tyre
  entities and an unchanged card.
- **Sharper trip start and end on cars that stream the driver door** (e.g. the
  i5). The driver door brackets the drive: closing it (you got in) anchors where
  a trip starts, and opening it again after the car has stopped (you got out)
  ends the trip promptly instead of waiting out the 5-minute stationary timer.
  Used only as an accelerator — cars that don't stream the door fall back to GPS
  exactly as before.
- **Recorded routes now carry timing.** With **Record route** (`trip_track`) on,
  each GPS fix in a trip's track is stored with the number of seconds since the
  trip started (`track` points become `[lat, lon, t]`), so a map can replay a
  drive in real time, colour it by pace and show where the car stopped. Only
  affects opted-in recording; routes captured before this update keep their
  two-element `[lat, lon]` points and read back without timing (their times
  can't be backfilled).
- **Trip-capture diagnostics (`Configure → Trips`).** An opt-in troubleshooting
  toggle for improving trip detection. When on, the integration logs the raw
  detector substrate under greppable tags — every GPS fix with its cadence,
  latency, odometer and the close-timer countdown (`[trip.gps]`), the timer
  lifecycle (`[trip.timer]`), full BMW segment batches (`[trip.seg]`), a
  per-message descriptor firehose (`[trip.raw]`) and a per-trip post-mortem
  (`[trip.post]`) — and writes a replayable `bavariandata_trip_capture.ndjson`
  capture to the config folder. Each `[trip.gps]` line also carries the GPS fix
  state, satellite count and heading (to tell a stopped car from a lost fix), and
  a `[trip.watch]` line surfaces catalogue signals that might drive a better
  detector (speed, HV-system and connector state, the ignition trio, driver
  door/lock, active navigation) whenever the car streams them. Independent of
  Debug logging, off by default, and contains GPS/VIN, so it's meant to be
  switched on for a test drive and back off.
- **Documented how to remove BavarianData completely.** Troubleshooting & FAQ has
  a new "Removing BavarianData completely" section: deleting the integration
  already wipes your tokens, your charging/trip history and the statistics it
  published, but the quota log, the cached vehicle image and any trip-capture
  file are left behind — and a trip capture contains GPS coordinates, so it's
  worth deleting. Also covers the one trap when testing a fresh install: long-term
  statistics live in the recorder database, so a partial removal can leave
  `bavariandata:…` series showing up in your Energy dashboard with nothing
  installed.

### Changed
- **The REST container is polled once a day instead of every 40 minutes, and now
  carries everything the stream cannot.** The container held 30 descriptors of
  which 26 duplicated the stream, and was refetched every 40 minutes — about 36
  of the 50 daily requests. It now holds the 41 non-streamable descriptors the
  container endpoint can serve (service demands, Condition Based Servicing,
  check-control messages, door-lock status, lifetime-consumption counters, state
  of health) plus the battery keys for a fast first paint, and refreshes daily.

  **Net effect: from ~36 requests a day to 2** (the container, plus the tyre
  diagnosis on its own endpoint), leaving 48 free for manual fetches — while
  filling entities that until now could only ever sit empty. Existing installs
  migrate on the next token refresh: the old container is deleted and replaced,
  rather than left to idle against BMW's 10-container limit.
- **A trip's start and end times now describe the drive, not the detection.** A
  trip was stamped from the first position fix that registered movement to the
  moment the five-minute stationary timer expired — so a drive from 10:51 to
  10:57 was recorded as 10:54 → 11:00, and its duration overstated by minutes at
  both ends. The start now falls back to when the car was last seen parked (on
  cars that stream the driver door, bounded to five minutes before detection, so
  sitting in the car before pulling away doesn't count as driving), and a
  stationary or segment close ends the trip at the last fix that showed movement
  instead of when the timer fired. A driver-door arrival still ends the trip
  where it always did — the door opening *is* the arrival. Trips already
  recorded keep their old timestamps.
- **The tire-pressure band is no longer symmetric: low from 8% under target, high
  only past 15% over.** The old ±4% flagged every wheel of a perfectly healthy
  car — BMW's target is the *cold* pressure and a tire you have just driven on
  reads 8–10% high. Under-inflation is the condition worth an early hint;
  over-inflation is only worth one past what warm-up explains.
- **The dashboard card is now the "BavarianData Card".** It was previously shown
  as the "BMW CarData Card" (element `custom:bmw-cardata-card`); the card, its
  element (`custom:bavariandata-card`) and its bundled file have been renamed to
  match the integration's name. Existing dashboards keep working: the old
  `custom:bmw-cardata-card` element is still registered as a hidden alias, so no
  card needs to be re-added. The old name simply no longer appears in the card
  picker.
- **Refreshed BMW's API reference material from source.** `docs/reference/` now
  carries Integration Guide **v1.5 (09/01/2026)** and the current Swagger files,
  re-fetched from BMW. Notable spec changes: the charging-history session gained
  `energyDecreaseHvbKwh` and `energyDischargedKwh` and lost
  `chargingCostInformation`; `basicData` gained `reessNominalCapacityGross`; the
  device-code response no longer documents `verification_uri_complete`; and the
  streaming chapter now states MQTT **QoS 0 only** plus a per-IP connection-rate
  policy over a one-minute window. No integration behaviour changes. The README
  in that folder records the exact URLs to re-fetch from, and `tools/README.md`
  now records the direct Telematics Data Catalogue download URL and spells out
  that the catalogue — not the Swagger — is the stream's contract.

### Fixed
- **Cards no longer break on a browser reload.** Every BavarianData card turned
  into a "Configuration error" box after pressing F5 — a hard refresh didn't
  help, only closing and reopening the browser did. The card was published as a
  frontend module, which Home Assistant renders into the page as a
  fire-and-forget `import()`; the frontend's service worker can serve that page
  from cache without it, so the `bavariandata-card` element was never defined
  and every placed card fell back to the error box
  ([home-assistant/frontend#18728](https://github.com/home-assistant/frontend/issues/18728)).
  The card is now registered as a proper Lovelace dashboard resource, which is
  loaded before any dashboard renders and is version-stamped on every update.
  Installations whose dashboard resources are YAML-managed keep the old
  behaviour and should add the resource by hand — see
  [Troubleshooting](https://github.com/JustChr/BavarianData/wiki/Troubleshooting-and-FAQ#config-error-after-reload).
- **Trip routes were drawn from mismatched latitude/longitude pairs**, putting
  every recorded point somewhere the car never was: a right-angle staircase where
  each vertex took its latitude from the *previous* fix and its longitude from the
  current one. BMW sends the two coordinates as separate messages, and the pairing
  guard compared each coordinate only against its own previous value — so once a
  single unpaired message slipped through (the first one after connect always
  does, with nothing to compare against), the stale half counted as "advanced"
  too and every later fix paired one message behind, permanently. Pairing now
  waits for both halves of a fix to actually *arrive*, and drops BMW's duplicate
  redelivery by the fix's own timestamp. The phantom right-angle points also
  inflated the measured trip distance, which is now correct too. Routes recorded
  before this fix keep their bad points; new drives are correct.
- **Routes now start where the car was parked.** A trip's track (and its start
  place) began at the first point that registered as movement — often a block or
  more past the actual start. It is now seeded from the last known parked
  position, so the line connects from where the drive really began.
- **The tire diagnosis now survives a restart.** Wear, tread, size, season and
  fitting date were held in memory only, so every Home Assistant restart blanked
  the tire sensors and the wear half of the tire card until the next daily
  refresh came due — up to 24 hours later — or `fetch_tyre_diagnosis` was called
  by hand. The last fetched diagnosis is now stored and restored at startup, at
  no cost to the API quota. The sensors also carry a `fetched_at` attribute now,
  so a day-old reading is recognisable as one.
- **The coverage report no longer reports gaps that can never close.** BMW's
  telematic catalogue marks each field with whether the MQTT stream can carry it
  (246 of 295 can), but that column is drawn as a tick glyph rather than text, so
  the catalogue generator read it as empty and the flag never existed. As a
  result **35 fields BMW cannot stream** — tyre diagnosis, vehicle image, state of
  health, Condition Based Servicing, door-lock status, the lifetime-consumption
  counters — were being requested on the stream and counted as expected by
  `get_coverage_report`, which then listed them as missing on every car, forever.
  That is exactly the false alarm the self-test exists to prevent.

  The flag is now parsed and carried through the pipeline, and non-streamable
  fields are excluded from the cluster picker, the portal snippet,
  `activate_stream_fields` and the coverage comparison. **Entities are unaffected**
  — several of these fields arrive over REST via a container, so they keep their
  entity and simply are not expected on the stream. `telematics-fields.md` gained
  a **Stream** column so you can see which is which per field.

## [0.9.1] - 2026-07-26

### Added
- **Optional route recording for trips.** A new **Record route** toggle under
  **Configure → Trips** (off by default) makes each new trip additionally store
  its GPS track — the polyline of coordinates along the drive — so a map can show
  where the car went. It is the only setting that persists raw coordinates, is
  independent of address resolution, and takes effect on the next trip that
  starts. The track is served through `bavariandata.get_trips` (a `track` list of
  `[lat, lon]` points) and is never included in the CSV / printable export, which
  stays place-names-only. Trips keep storing named places only when the toggle is
  off, exactly as before.

## [0.9.1-beta.7] - 2026-07-26

### Changed
- **Changing your streamed data is now one click, like guided setup.**
  **Configure → Choose streamed data** no longer hands you a console snippet to
  paste on the portal's Data Selection page. Instead it reuses the guided-setup
  activator: pick your clusters, then run the **Activate BMW data** bookmarklet on
  the portal's stream-setup page and Home Assistant turns the fields on for you
  and continues automatically (on an http instance it falls back to the same
  Copy-and-paste screen guided setup uses). The activator is **additive** — it
  adds the chosen clusters to your live stream but never removes a field you
  already stream; to stop streaming a field, untick it under Data Selection in the
  portal.

## [0.9.1-beta.6] - 2026-07-26

### Fixed
- **MINI vehicles are now handled in guided setup.** The activator recognised only
  a `bmw` portal address, so it would refuse to run on the MINI portal
  (`mini.at`); it now accepts both, and the setup wording is brand-neutral
  ("BMW or MINI"). BMW and MINI share the same CarData backend — the API path is
  `/utilities/bmw/api/cd/…` on both — so a MINI's own portal and MINI-branded
  sign-in page are correct and work identically.

## [0.9.1-beta.5] - 2026-07-25

### Fixed
- **Guided setup no longer hangs on an http Home Assistant.** The browser
  activator can only auto-report to an **https** Home Assistant (an http instance
  blocks the https→http POST as mixed content). The guided flow now detects this
  and, on an http instance, goes **straight to a self-contained paste screen**
  (open the setup page → run the bookmarklet → Copy → paste) instead of a webhook
  wait that could never complete. On https it still auto-continues; the wait's
  fallback timeout dropped from 10 to 5 minutes.

## [0.9.1-beta.4] - 2026-07-25

### Changed
- **Guided setup is now one click, not a copy-paste.** The guided path no longer
  hands you a console snippet to paste. Instead Home Assistant serves a small
  setup page with a one-click **"Activate BMW data"** bookmarklet; you run it on
  the BMW portal and it activates the stream **in your own browser** (no password
  or session ever leaves it), then **reports the result straight back to Home
  Assistant via a webhook** — so setup continues automatically, with nothing to
  copy. A paste fallback remains for Home Assistant instances reached over plain
  HTTP (where the browser blocks the automatic report).
- **The activator is now bulletproof.** It reads BMW's per-vehicle *streamable
  catalogue* and only turns on fields BMW will accept (fixes an HTTP 500 when a
  descriptor wasn't streamable for that car), is **additive** (never removes a
  field you already had) and **idempotent** (re-running a fully-set car does
  nothing), has **per-request timeouts** so a stalled request can't hang it, shows
  live progress, and detects when you're on the wrong page.

## [0.9.1-beta.3] - 2026-07-25

### Added
- **Guided setup (one-snippet onboarding).** Setup now opens with a Guided vs.
  Manual choice. Guided asks which data clusters you want, then hands you a single
  snippet to run on the BMW portal: it discovers your **Client ID** (no more
  copying it by hand), checks your vehicle mapping, and **activates the stream
  fields** for those clusters — all in your browser, so no password or session
  ever leaves it. You paste back a short, non-secret result and the flow fills in
  the Client ID and continues to device authorization. The classic Manual path
  (paste a Client ID) is unchanged. New module `onboarding.py`; portal route
  documented in
  [`docs/reference/stream-attribute-activation.md`](docs/reference/stream-attribute-activation.md).
- **Activate stream fields in one call** — a new `bavariandata.activate_stream_fields`
  service replays the exact request the BMW portal's stream-setup page sends when
  you save *Datenauswahl ändern*, replacing your whole streamed-attribute
  selection at once instead of ticking checkboxes. Stream selection has no CarData
  API and is gated by your browser session (behind BMW's bot-defense), so the
  service takes a **captured portal session** (`base_url`/`locale`/`mapped_vehicle_id`/`cookie`)
  and is a manual, occasional tool — it can't run unattended and spends no API
  quota. Attributes default to your chosen streamed-data clusters. The captured
  cookie is never logged; the service reports requested/accepted counts. See
  [`docs/reference/stream-attribute-activation.md`](docs/reference/stream-attribute-activation.md).

### Fixed
- **Long-term statistics no longer log a deprecation warning.** The statistics
  backfill now sets `unit_class` on each external-statistics series (energy for
  kWh, distance for km, none for currency), which Home Assistant requires from
  2026.11 — silences the "doesn't specify unit_class" warning.
- **Trip/charge close timers now run on the event loop.** The debounced trip- and
  charge-close timer callbacks were plain functions, so Home Assistant dispatched
  them to a worker thread, where `async_create_task` / `async_dispatcher_send` are
  not thread-safe (HA logged a thread-safety warning and warned of possible
  corruption). Both are now `@callback`, so they run inline on the loop.

## [0.9.1-beta.2] - 2026-07-25

### Fixed
- **Trip distance now uses BMW's odometer, not just the GPS track.** The i5
  streams its cumulative odometer as `vehicle.vehicle.travelledDistance` (km),
  but the code only ever read `vehicle.vehicle.mileage` (which the i5 doesn't
  stream), so every trip fell back to the GPS haversine track — which undercounts
  winding roads. The odometer is now read from either descriptor, giving BMW's
  own exact distance on cars that stream it; sub-kilometre trips that don't tick
  the (1 km-resolution) odometer still fall back to GPS.
- **Trips no longer fragment mid-drive.** On the i5 (and other cars that stream
  live GPS), BMW emits `trip.segment` batches repeatedly *during* a drive, not
  just at its end. Each one was treated as a completed-trip marker and closed the
  open trip, chopping one continuous drive into sub-minute pieces that then
  dropped out as noise — so a long drive recorded as a single 0.7 km trip. A
  segment batch now closes a trip only when the GPS track shows the car has
  actually stopped; otherwise the drive stays open and the GPS stationary-close
  debounce owns the end.

## [0.9.1-beta.1] - 2026-07-25

### Added
- **Diagnostics download.** Settings → Devices & Services → BavarianData → ⋮ →
  Download diagnostics now produces a redacted snapshot for triage: integration
  and Home Assistant versions, REST quota state, selected clusters, per-VIN
  descriptor arrival counts and last-message timestamps, and the MQTT
  connect/disconnect history with rc codes. VIN, GCID, client ID, tokens and GPS
  are redacted.
- **Two new repair issues** (Settings → Repairs), each linking to the matching
  Troubleshooting anchor: **no stream data received in 48 h**, and **stream
  unauthorized (MQTT rc=5) persisting** past reauth. The existing quota-exhausted
  repair now links to its Troubleshooting section too.
- **GitHub issue templates.** A bug report form that requires the diagnostics
  attachment and the "did you save Data Selection?" answer, and routes setup /
  BMW-registration questions to Discussions.

## [0.9.0] - 2026-07-24

The big one: a full **history layer**. BavarianData now keeps its own local
store — charging sessions with real cost, a Fahrtenbuch/trips log with a
month-in-review, a learned battery-health estimate, and long-term statistics
that feed the Energy dashboard — none of which touches your 50-request BMW API
quota. Past charges and drives can be imported from BMW's history, and the
Lovelace card gained views for all of it. Everything below accumulated across
the 0.9.0 pre-releases; upgrading from 0.8.1 gets it all at once.

### Added
- **History layer — the foundation (Phases 0–2).** A local history store that
  records **charging sessions with real cost** and surfaces them in a new
  **charging-history card view**, plus a **battery-health estimate** learned from
  wide-SoC charges with its own view. All local — no BMW API quota.
- **Trips / Fahrtenbuch with a month-in-review (Phase 3).** Trips are recorded
  locally with their endpoints stored as place names, never coordinates. The
  `get_trips` service lists them, and `get_driving_summary` aggregates a month —
  distance, the business/private/commute split, consumption, recuperation, a
  driving-style score, top destinations, and (with a tariff configured) an
  estimated driving cost.
- **Statistics backfill & month export (Phase 4).** The new `import_statistics`
  service rebuilds long-term statistics from recorded history, so charging and
  driving from before the install — or from while Home Assistant was down —
  appear on the Energy dashboard. The new `export_history` service returns a
  month of charging sessions and/or trips as CSV files or a self-contained,
  printable HTML report. Both read the local store and cost no BMW API quota.
- **`fetch_charging_history` now imports into the local history**, instead of
  only logging BMW's response. Past charges — including ones from before the
  integration was installed, or from while Home Assistant was down — appear on
  the card's charging view and in the monthly summaries. BMW's measured grid
  energy, SoC, odometer, location and power curve are mapped into session
  records; a charge that also streamed live is enriched in place (no duplicate),
  and cost is backfilled for a fixed tariff. Re-running the import is idempotent.
- **Descriptor coverage self-test.** A new `get_coverage_report` service (costs
  no BMW API quota) lists, per vehicle, which descriptors from the stream
  clusters you enabled have never actually arrived — so you can tell whether a
  missing entity is down to your BMW Data Selection, your car, or a bug. A
  dismissible repair issue is raised once a gap is genuinely overdue (7-day
  grace period), never a notification.
- **Selectors for every service action.** Action fields (VIN, config entry, date
  ranges, limits, month) now render in Home Assistant's visual action editor with
  proper pickers, instead of being reachable only in YAML mode.
- German translations for the derived entities and the setup/config flow.
- Tagged debug tracing across the history layer
  (`[trip]`/`[charge]`/`[health]`/`[stats]`/`[history]`) to make the opt-in debug
  log readable.

### Changed
- **Imported charging sessions now resolve their location to a Home Assistant
  zone** (Home, Work, …) from BMW's coordinates, the same way live-recorded
  sessions do — so the card shows "Home" for a home charge instead of "Away".
  Only the resolved zone is stored; the raw latitude/longitude BMW returns are
  used for the lookup and then dropped, matching the live path's privacy stance.
- **Card charging view:** the collapsed session row now leads with the energy
  (kWh) instead of the price; the per-session cost moves into the expanded
  detail. The power-curve chart is now stepped — each sampled power holds until
  the next reading rather than being drawn as a diagonal ramp between samples.

### Fixed
- **A charge that streamed live *and* was imported from BMW no longer becomes two
  records.** A brief `charging.status` blip (a momentary "not charging" that came
  straight back) split one plug-in into two live sessions; BMW's import then
  enriched only one of them and orphaned the other, so the monthly total counted
  the same charge's energy twice. Two fixes: the session close is now debounced,
  so a status flap no longer splits a live session in the first place; and on
  import, every live-only fragment that falls inside BMW's charge window is folded
  into the single measured record instead of being left behind. Re-running "Fetch
  charging history" once reconciles any already-split charge into one record.
- **The charging-state sensor ("Ladestatus") now shows readable states.** BMW's
  catalogue lists the wrong allowed values for this field (a charging-*mode*
  list), so the actual stream states — `nocharging`, `chargingactive`,
  `initialization`, `chargingpaused`, `chargingended`, `chargingerror` — were
  shown raw and untranslated. The real enum (which BMW documents in the field's
  own description) is now pinned, so the sensor reads "Not charging" / "Charging"
  / "Charging paused" … in English and "Lädt nicht" / "Lädt" / … in German.
- **Already-imported home charges now show "Home" instead of "Away".** Sessions
  imported by an earlier build kept their unresolved location, and a re-import
  wouldn't overwrite it — so a charge at home stayed labelled "Away" forever. On
  startup those legacy records are now re-resolved locally (no BMW request, no
  quota) and a re-import upgrades an unresolved location in place.
- **Public charges now show BMW's address.** When a charge's location matches no
  Home Assistant zone, BMW's own `formattedAddress` (which the charging-history
  API already returns) is kept as the label and shown on the card, in the CSV/
  HTML export, and as a sensor attribute — no reverse geocoding, and still no raw
  coordinates stored.
- **Trips are now detected from the live GPS position stream.** On vehicles that
  don't stream a live `isMoving` / ignition signal or a fresh completed-trip
  batch (e.g. the i5, even with those descriptors selected in Data Selection), no
  trip was ever recorded. Trip detection now opens on GPS movement, sums the
  drive's distance from the position track when no odometer or BMW
  `travelledDistance` is available, and closes after the car has been stationary
  — so drives finally appear in the trip list and the monthly distance sensor.
- **A stale "last trip end" field can no longer close a live trip.** BMW repeats
  the previous drive's `trip.segment.end.*` fields (with their old timestamp) in
  every telematic snapshot; these are now ignored unless their timestamp is
  recent, so a parked, charging car no longer looks like a just-finished trip.
- **Imported BMW charging now counts toward the monthly energy total.** An
  imported session carries only BMW's measured grid energy (`grid_kwh`), never
  the stream-integrated battery-side figure, so it was silently excluded from the
  "charging energy this month" sensor. The monthly summary now counts the
  measured grid figure — the same rule the Energy-dashboard statistics and the
  CSV/HTML export already use.
- **The Lovelace card showed "not charging" while the car was charging.** The
  overview picked the wrong status entity (`charging.hvStatus`) over the
  authoritative `charging.status`, and did not recognise BMW's uncatalogued
  `chargingactive` value. The card now prefers `charging.status`, treats
  `chargingactive`/`charging_in_progress` as active, and renders them as a clean
  localized "Charging" label.
- **Card entity auto-selection ignored multi-word keywords on non-English
  installs.** The picker matched keys like "charging status", "electric range" or
  "connection status" only against the localized friendly name, never the English
  descriptor path — so on a German (or other) install it fell back to arbitrary
  tie-breaks. The descriptor is now also matched word-normalized, fixing
  selection of the charging-status, range, plug and time-to-full tiles regardless
  of Home Assistant's language.
- **`fetch_charging_history` failed with HTTP 400 when run without a date
  range.** BMW requires `from`/`to`; the service now defaults to the last 30 days
  when they are omitted and sends the ISO 8601 UTC timestamps BMW expects. The
  `from`/`to` fields also get date/time pickers.
- **Duplicate unique-ID warning after a restart** for the monthly charging
  summary sensors (e.g. "energy this month"), which also spawned a phantom
  duplicate sensor. The restore path now recreates only the correct entity.
- **Vehicle location went unavailable after every restart or upgrade** until the
  car next streamed a GPS position (often hours away). The device tracker is now
  recreated immediately and its last known position restored, so the map shows
  where the car was parked straight away.
- **hassfest validation:** declared the `recorder` dependency (now required
  because the history layer writes long-term statistics) and sorted the manifest
  keys into the required order.

## [0.8.1] - 2026-07-21

### Added
- Options-flow toggle to enable debug logging (opt-in, since debug output can
  contain VIN/GPS).

### Fixed
- GPS and length sensors could fail to be added because of an `AttributeError`
  reading `_attr_device_class`; the read is now guarded.
- Invalid energy `state_class` (`measurement` → `None`) corrected through the
  descriptor metadata pipeline, restoring Energy-dashboard compatibility.
- Device tracker crash (`_update_name`) and a blocking manifest read during
  setup.

### Changed
- Minimum supported Home Assistant raised to **2026.3** (needed for self-served
  brand icons). Untracked `.venv` and dropped the obsolete `brands/` staging dir.
- A clean MQTT `rc=0` disconnect is now logged at debug instead of warning.
- README images use absolute `raw.githubusercontent.com` URLs so they render on
  the HACS info screen.
