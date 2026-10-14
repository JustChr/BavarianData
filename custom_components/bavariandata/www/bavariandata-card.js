/*
 * BavarianData Card
 * A custom Lovelace card for the BavarianData integration (BMW CarData).
 *
 * Two modes:
 *   - overview (default): the vehicle render as a hero, a state-of-charge ring,
 *     range, charging status and a compact grid of key metrics.
 *   - cluster: `cluster: <slug>` renders every entity of that catalogue cluster
 *     (electric, status, tire, ...) as a clean list. The cluster of each entity
 *     is read from its `cluster` attribute, exposed by the integration.
 *
 * The card auto-discovers entities from the vehicle's device, so a minimal
 * config is just `type: custom:bavariandata-card`.
 */

const CARD_VERSION = "1.15.3";

// Classification -> colour, shared by the trips legend and the trip map so a
// route drawn on the map matches the colour of its row in the Trips view.
// Kept in sync with the `.tr__seg--*` background rules in `_styles()`.
const TRIP_CLASS_COLORS = {
  business: "#0066b1",
  commute: "#00a1e0",
  private: "#7ac142",
  unclassified: "#8a8a8a",
};

// Register a custom element idempotently: always attempt the define so a cold
// load can never silently skip it, but swallow the benign "already defined"
// error from a legitimate second evaluation. A real error (e.g. an invalid
// class) still surfaces. Hoisted (function declaration) so the define() call
// sites near the end of the module can use it.
function defineCardElement(tag, cls) {
  try {
    customElements.define(tag, cls);
  } catch (err) {
    if (!customElements.get(tag)) throw err; // not a duplicate-definition race
  }
}

// Catalogue cluster slugs, in display order. Human labels are localized via the
// translation table (keys `cl_<slug>`); icons are language-independent.
const CLUSTER_SLUGS = [
  "electric",
  "status",
  "metadata",
  "events",
  "tire",
  "basic",
  "usage",
  "other",
  "contract",
];

const CLUSTER_ICONS = {
  electric: "mdi:lightning-bolt",
  status: "mdi:car-info",
  tire: "mdi:tire",
  events: "mdi:calendar-alert",
  usage: "mdi:chart-line",
  basic: "mdi:card-account-details-outline",
  metadata: "mdi:information-outline",
  contract: "mdi:file-document-outline",
  other: "mdi:dots-horizontal",
};

const UNAVAILABLE = new Set(["unavailable", "unknown", "none", "", null, undefined]);

// Descriptor fragments of percentages that are never the high-voltage state of
// charge: the 12 V battery every car reports, and the fuel tank. Kept out of the
// state-of-charge gauge's picks (see _overviewEntities).
const NOT_HV_BATTERY = ["electricalsystem", "fuelsystem"];

// Descriptors that prove a high-voltage battery. Deliberately not "anything from
// the electric cluster": a petrol F87 M2 streams the EV charge *target* (issue #8's
// diagnostics), which must not make it look electric.
const HV_SIGNALS = [
  "vehicle.drivetrain.batteryManagement.header",
  "vehicle.drivetrain.batteryManagement.maxEnergy",
  "vehicle.drivetrain.batteryManagement.batterySizeMax",
  "vehicle.drivetrain.electricEngine.charging.status",
  "vehicle.drivetrain.electricEngine.kombiRemainingElectricRange",
];
// Descriptor prefixes only a car with a combustion engine streams.
const COMBUSTION_PREFIXES = [
  "vehicle.drivetrain.fuelSystem.",
  "vehicle.drivetrain.internalCombustionEngine.",
];
// Sensors that say whether a cable is in the car, best first. The `isPlugged`
// binary sensor outranks all of them and is picked separately.
const PLUG_SENSOR_DESCRIPTORS = [
  "vehicle.body.chargingPort.status",
  "vehicle.body.chargingPort.statusClearText",
  "vehicle.drivetrain.electricEngine.charging.connectorStatus",
];
// The overview layouts a user may pick: electric, plug-in hybrid, petrol/diesel.
// `_drivetrain` can also answer "unknown" for a car that proves none of them --
// not selectable, because it describes missing evidence rather than a car.
const DRIVETRAINS = new Set(["bev", "phev", "ice"]);
// How much a car must have said before its *silence* about a drivetrain counts
// as evidence. A car mid-bootstrap has sent a handful of fields and proves
// nothing; the MINI that prompted this had 28.
const MIN_DESCRIPTORS_TO_JUDGE = 8;
// Where the driver sits, as `_steering` answers it and `steering:` sets it.
const STEERING_SIDES = new Set(["left", "right"]);
// BMW's two odometer spellings (the coordinator's DESC_ODOMETER); whichever
// the car streams carries the distance unit its owner chose.
const DESC_ODOMETER = ["vehicle.vehicle.mileage", "vehicle.vehicle.travelledDistance"];
const KM_PER_MILE = 1.609344;

// BMW reports these charging-status values when nothing is actively charging
// (e.g. `invalid` when no cable is connected). Show a clean localized "not
// charging" for them instead of the raw state translation ("Ungültig", …).
const NOT_CHARGING_STATES = new Set([
  "invalid",
  "not_charging",
  "notcharging",
  "no_charging",
  "default",
]);

// Values BMW streams on charging.status while a charge is actually in progress.
// "chargingactive" is not in the catalogue's option list, so it arrives
// untranslated -- treat it (and its siblings) as active charging and render a
// clean localized label instead of the raw token. Mirrors the coordinator's own
// charging-active test (status in {CHARGINGACTIVE, CHARGING_IN_PROGRESS}).
const CHARGING_ACTIVE_STATES = new Set([
  "chargingactive",
  "charging_active",
  "charging_in_progress",
  "charging",
]);

// charging.status values that start with "charging" yet mean it has stopped. The
// heuristic in _isCharging reads a leading "charging" as active, so a car that
// finished at its target sat in `chargingended` under a green ring and "Time to
// full" (issue #25's X3; the maintainer's i5 never showed it, because evcc ends
// its charges and it drops straight to `nocharging`). Kept apart from
// NOT_CHARGING_STATES so the label still shows Home Assistant's own translation
// ("Charging ended", "Paused") rather than a generic "not charging". Agrees with
// the integration (soc_tracking.is_charging_status), which counts none of these.
// BMW's catalogue added `charginginterrupted` and `chargingdisrupted` in its
// 2026-09 revision: stopped too, and prefixed the same way.
const CHARGING_STOPPED_STATES = new Set([
  "chargingended",
  "chargingpaused",
  "chargingerror",
  "charginginterrupted",
  "chargingdisrupted",
]);

/* ------------------------------------------------------------------------- *
 * Localization                                                              *
 *                                                                           *
 * The card's own chrome (labels, headings, relative times, tire positions,  *
 * editor fields) is translated here. Entity names and states keep coming    *
 * from Home Assistant's own translations via hass.formatEntityState. Add a  *
 * language by adding a block below; anything missing falls back to English. *
 * ------------------------------------------------------------------------- */
const TRANSLATIONS = {
  en: {
    // cluster labels
    cl_electric: "Electric vehicle",
    cl_status: "Vehicle status",
    cl_metadata: "Metadata",
    cl_events: "Vehicle events",
    cl_tire: "Tire data",
    cl_basic: "Vehicle basic data",
    cl_usage: "Usage-based data",
    cl_other: "Other",
    cl_contract: "ConnectedDrive contract",
    // overview
    remaining_range: "remaining range",
    charging_status: "charging status",
    charge: "charge",
    charging: "charging",
    is_charging: "Charging",
    not_charging: "Not charging",
    target: "Target",
    plug: "Plug",
    fuel: "fuel",
    tank: "Tank",
    electric_range: "electric range",
    total_range: "Total range",
    time_to_full: "Time to full",
    charge_time: "Charge time",
    odometer: "Odometer",
    last_update: "Last update",
    spin_hint: "Drag to turn the car",
    hs_door_open: "Door open",
    hs_doors_open: "{n} doors open",
    hs_trunk_open: "Trunk open",
    hs_hood_open: "Hood open",
    hs_windows_open: "Windows open",
    hs_charging: "Charging",
    hs_interior: "Interior",
    hs_exterior: "Exterior",
    hs_default_trim: "BMW's default trim shown",
    eq_title: "Equipment",
    eq_count: "{n} items",
    eq_empty: "Turn on Configure → Vehicle image → BMW configurator to list the car's factory equipment.",
    eq_looks: "Paint, wheels & trim",
    eq_packages: "Packages",
    eq_features: "Features",
    eq_technical: "Technical codes ({n})",
    just_now: "just now",
    min_ago: "{n} min ago",
    h_ago: "{n} h ago",
    d_ago: "{n} d ago",
    // cluster list
    value: "value",
    values: "values",
    no_cluster_entities:
      "No {label} entities for this vehicle. Enable the cluster in the integration options.",
    cc_title: "Check Control messages",
    cc_none: "No Check Control messages reported.",
    cc_teleservice: "Teleservice",
    cc_mileage: "Last shown at",
    cc_sent: "Sent by the car",
    cc_code: "Code",
    cc_earlier: "Earlier messages ({n})",
    cc_reported: "Last reported",
    cc_gone: "No longer reported",
    // messages
    no_vehicle_title: "No BMW CarData vehicle found",
    no_vehicle_body:
      "Add the integration, or set <code>device:</code> / <code>vin:</code> in the card config.",
    // tire
    front: "FRONT",
    fl: "FL",
    fr: "FR",
    rl: "RL",
    rr: "RR",
    front_left: "Front left",
    front_right: "Front right",
    rear_left: "Rear left",
    rear_right: "Rear right",
    tires: "Tires",
    no_tire_data:
      "No tire data for this vehicle yet. Enable the Tire data cluster and drive to populate readings.",
    check_pressure: "Check pressure",
    slightly_high: "Slightly high",
    all_nominal: "All nominal",
    of_four: "{n} of 4",
    t_low: "Low",
    t_high: "High",
    t_ok: "OK",
    t_nodata: "No data",
    t_wear: "Worn",
    check_tyres: "Check tires",
    wear_due: "change in",
    fitted: "fitted",
    t_current: "Current",
    // tire summary + per-wheel fitment
    sum_pressure: "Pressure",
    sum_wear: "Wear",
    p_target: "Target",
    p_target_varies: "Targets vary",
    w_ok: "OK",
    w_soon: "Check soon",
    w_due: "Replace",
    season_summer: "Summer",
    season_winter: "Winter",
    season_all: "All-season",
    runflat: "Runflat",
    // closures / security
    cl_closures: "Security & closures",
    closures_none:
      "No door, window or security data for this vehicle yet. Enable the Vehicle status cluster in the integration options.",
    central_lock: "Central lock",
    alarm_word: "Anti-theft alarm",
    alarm_armed: "Armed",
    alarm_disarmed: "Disarmed",
    alarm_triggered: "Alarm triggered",
    secured: "Secured",
    locked: "Locked",
    unlocked: "Unlocked",
    partially_locked: "Partially locked",
    all_closed: "All closed",
    windows_open: "Windows open",
    n_open: "{n} open",
    state_open: "Open",
    state_closed: "Closed",
    state_tilted: "Tilted",
    door_word: "Door",
    window_word: "Window",
    hood_word: "Hood",
    trunk_word: "Trunk",
    rear_window_word: "Rear window",
    sunroof_word: "Sunroof",
    // charging history
    ch_title: "Charging history",
    ch_month: "This month",
    ch_loading: "Loading charging history…",
    ch_empty:
      "No charging sessions recorded yet. Once the car charges, sessions appear here automatically.",
    ch_empty_month: "No charging in this month.",
    ch_error: "Couldn't load charging history. Reload the page and try again.",
    mn_prev: "Previous month",
    mn_next: "Next month",
    ch_home: "Home",
    ch_public: "Away",
    ch_assumed: "assumed",
    ch_partial: "partial price",
    ch_session_one: "1 session",
    ch_session_many: "{n} sessions",
    ch_peak: "Peak",
    ch_avg: "Avg",
    ch_grid: "From grid",
    ch_solar: "solar",
    ch_mix: "Energy source",
    ch_mix_pv: "PV",
    ch_mix_battery: "House battery",
    ch_mix_grid: "Grid",
    ch_mix_unknown: "unattributed",
    ch_duration: "Duration",
    ch_cost: "Cost",
    ch_no_cost: "no price set",
    ch_ongoing: "charging…",
    // battery health
    bh_title: "Battery health",
    bh_empty:
      "No battery-health data yet. Once the car logs a few wide-range charges, its usable capacity appears here.",
    bh_learning: "Learning ({n}/{total})",
    bh_learning_hint:
      "Estimating usable capacity from your charges. A few wide-range charges (e.g. 20 → 80%) teach it fastest.",
    bh_suspicious:
      "Cross-checking against BMW's own capacity figure before showing a number.",
    bh_usable: "Usable capacity",
    bh_of_new: "{p}% of original",
    bh_nominal: "As new",
    bh_analysed: "Based on",
    bh_samples: "{n} charges",
    bh_trend_title: "Capacity vs mileage",
    // efficiency and real range
    ef_title: "Efficiency & range",
    ef_loading: "Working out your real range…",
    ef_empty:
      "Not enough charging history yet. Once two charges bracket 50 km or so of driving, your real consumption and the range it reaches appear here.",
    ef_no_capacity:
      "This car hasn't reported its battery capacity, so a range can't be worked out from the measured consumption.",
    ef_hybrid:
      "A plug-in hybrid covers part of its distance on fuel, and nothing the car sends says how much. A consumption or range worked out from the charging history would look far better than the battery really does, so none is shown. Battery health and the charging history are unaffected.",
    ef_error: "Couldn't load the efficiency data. Reload the page and try again.",
    ef_range_now: "Real range now",
    ef_at_soc: "at {p}% charge",
    ef_range_full: "{km} km on a full battery",
    ef_vs_bmw_over: "{p}% further than the car predicts ({km} km)",
    ef_vs_bmw_under: "{p}% less than the car predicts ({km} km)",
    ef_vs_bmw_same: "Same as the car's own estimate ({km} km)",
    ef_consumption: "Measured consumption",
    ef_side_battery: "from the battery",
    ef_side_grid: "at the plug",
    ef_window_days: "last {n} days",
    ef_window_all: "all records",
    ef_loss: "Charging loss",
    ef_capacity: "Usable capacity",
    ef_capacity_measured: "measured",
    ef_capacity_bmw: "BMW's figure",
    ef_cost: "Cost per 100 km",
    ef_solar: "{p}% solar this month",
    ef_trend_title: "Consumption by month",
    ef_footnote:
      "Measured from the charging ledger: the distance between two charges and the energy that went in, not the car's own estimate.",
    // charging, battery health and efficiency on a petrol or diesel car
    ice_view_title: "Nothing to show for this car",
    ice_view_body:
      "{name} runs on fuel alone, so there is no charging, battery health or electric range to show. If it does plug in, set Drivetrain in the card editor.",
    // trips
    tr_title: "Trips",
    tr_loading: "Loading trips…",
    tr_empty:
      "No trips recorded yet. Once the car is driven, trips appear here automatically.",
    tr_empty_month: "No trips in this month.",
    tr_error: "Couldn't load trips. Reload the page and try again.",
    tr_trip_one: "1 trip",
    tr_trip_many: "{n} trips",
    tr_review: "This month",
    tr_vs_last: "vs last month",
    tr_business: "Business",
    tr_private: "Private",
    tr_commute: "Commute",
    tr_unclassified: "Unclassified",
    tr_consumption: "Avg consumption",
    tr_at_plug: "at the plug",
    tr_at_battery: "at the battery",
    tr_charge_loss: "{n}% charging loss",
    tr_recuperation: "Recuperated",
    tr_style: "Driving style",
    tr_style_trend: "Style over time",
    tr_top_dest: "Top destinations",
    tr_est_cost: "Est. cost",
    tr_longest: "Longest trip",
    tr_duration: "Duration",
    tr_distance: "Distance",
    tr_soc_used: "Battery used",
    tr_visits: "{n}×",
    tr_auto: "auto",
    tr_classify: "Classify",
    tr_best: "Best",
    tr_worst: "Worst",
    tr_unknown_place: "Unknown",
    tr_open: "Trip in progress",
    tr_open_hint: "A drive is under way",
    tr_min: "{n} min",
    tr_open_note:
      "This drive is still under way. Distance and consumption are provisional, and the trip can be classified once it ends.",
    // map
    mp_title: "Trip map",
    mp_loading: "Loading map…",
    mp_error: "Couldn't load the map. Reload the page and try again.",
    mp_empty_none:
      "No places to map yet. Turn on “Record route” under Configure → Trips, then drive — the places you visit appear here.",
    mp_empty_window: "No places recorded in this period.",
    mp_unavailable: "The map couldn't be loaded in this browser.",
    mp_win_month: "This month",
    mp_win_3m: "3 months",
    mp_win_all: "All",
    mp_route_one: "1 route",
    mp_route_many: "{n} routes",
    // export
    ex_csv: "CSV",
    ex_report: "Report",
    ex_csv_hint: "Download this month as a spreadsheet",
    ex_report_hint: "Open a printable month report (print it to get a PDF)",
    ex_empty: "Nothing recorded for this month yet.",
    ex_error: "Export failed. Check the Home Assistant log.",
    // editor
    ed_device: "Vehicle",
    ed_cluster: "Mode",
    ed_title: "Title (optional)",
    ed_image: "Image entity",
    ed_soc: "State of charge",
    ed_range: "Range",
    ed_charging: "Charging status",
    ed_target_soc: "Charge target",
    ed_time_to_full: "Time to full",
    ed_odometer: "Odometer",
    ed_plug: "Plug / connection",
    ed_fuel: "Fuel tank",
    ed_drivetrain: "Drivetrain",
    edh_drivetrain: "Detected from what the car streams. Set it only if the card picks the wrong layout.",
    dt_auto: "Auto-detect",
    dt_bev: "Electric",
    dt_phev: "Plug-in hybrid",
    dt_ice: "Petrol / diesel",
    ed_steering: "Steering wheel",
    edh_steering: "Read from BMW's vehicle data. Set it only if doors and windows appear on the wrong side of the car.",
    st_left: "Left-hand drive",
    st_right: "Right-hand drive",
    ed_shape: "Car shape",
    edh_shape: "Picked from the model in BMW's vehicle data. Set it only if the drawing does not match your car.",
    sh_sedan: "BMW sedan",
    sh_countryman: "MINI Countryman",
    ed_overview_option: "Overview (default)",
    ed_overrides_title: "Entity overrides (optional — leave empty to auto-detect)",
    edh_cluster:
      "Overview shows the hero image and key metrics. Charging history lists recorded sessions with cost and power curve. Trips lists recorded drives with a month-in-review summary. Trip map draws recorded routes on a map (needs “Record trip routes” enabled). Battery health shows learned usable capacity and its trend. A cluster shows every value of that group as a list. A drive in progress appears as a badge on the overview and as a live row at the top of Trips.",
    edh_title: "Overrides the vehicle name shown on the card.",
  },
  // British English is a delta over `en`, not a table of its own: `t()` falls
  // back to `TRANSLATIONS.en` key by key, so only the strings that actually
  // differ belong here. `tests/test_translations_dialect.py` derives the
  // expected contents from `tools/spelling_en_gb.json` and fails if this drifts
  // from it -- add the word there, not a second full table here.
  "en-GB": {
    cl_tire: "Tyre data",
    tires: "Tyres",
    no_tire_data:
      "No tyre data for this vehicle yet. Enable the Tyre data cluster and drive to populate readings.",
    check_tyres: "Check tyres",
    sh_sedan: "BMW saloon",
  },
  de: {
    // cluster labels
    cl_electric: "Elektrofahrzeug",
    cl_status: "Fahrzeugstatus",
    cl_metadata: "Metadaten",
    cl_events: "Fahrzeugereignisse",
    cl_tire: "Reifendaten",
    cl_basic: "Fahrzeug-Basisdaten",
    cl_usage: "Nutzungsdaten",
    cl_other: "Sonstiges",
    cl_contract: "ConnectedDrive-Vertrag",
    // overview
    remaining_range: "Reichweite",
    charging_status: "Ladestatus",
    charge: "Ladung",
    charging: "lädt",
    is_charging: "Lädt",
    not_charging: "Lädt nicht",
    target: "Ziel",
    plug: "Stecker",
    fuel: "Kraftstoff",
    tank: "Tank",
    electric_range: "elektrische Reichweite",
    total_range: "Gesamtreichweite",
    time_to_full: "Bis voll",
    charge_time: "Ladezeit",
    odometer: "Kilometerstand",
    last_update: "Letzte Aktualisierung",
    spin_hint: "Ziehen, um das Auto zu drehen",
    hs_door_open: "Tür offen",
    hs_doors_open: "{n} Türen offen",
    hs_trunk_open: "Kofferraum offen",
    hs_hood_open: "Motorhaube offen",
    hs_windows_open: "Fenster offen",
    hs_charging: "Lädt",
    hs_interior: "Innenraum",
    hs_exterior: "Außen",
    hs_default_trim: "BMWs Standard-Ausstattung gezeigt",
    eq_title: "Ausstattung",
    eq_count: "{n} Posten",
    eq_empty: "Schalte Konfigurieren → Fahrzeugbild → BMW-Konfigurator ein, um die Werksausstattung des Autos aufzulisten.",
    eq_looks: "Lack, Räder & Leisten",
    eq_packages: "Pakete",
    eq_features: "Ausstattungsmerkmale",
    eq_technical: "Technische Codes ({n})",
    just_now: "gerade eben",
    min_ago: "vor {n} Min.",
    h_ago: "vor {n} Std.",
    d_ago: "vor {n} T.",
    // cluster list
    value: "Wert",
    values: "Werte",
    no_cluster_entities:
      "Keine {label}-Entitäten für dieses Fahrzeug. Aktiviere den Cluster in den Integrationsoptionen.",
    cc_title: "Check-Control-Meldungen",
    cc_none: "Keine Check-Control-Meldungen gemeldet.",
    cc_teleservice: "Teleservice",
    cc_mileage: "Zuletzt angezeigt bei",
    cc_sent: "Vom Fahrzeug gesendet",
    cc_code: "Code",
    cc_earlier: "Frühere Meldungen ({n})",
    cc_reported: "Zuletzt gemeldet",
    cc_gone: "Nicht mehr gemeldet seit",
    // messages
    no_vehicle_title: "Kein BMW-CarData-Fahrzeug gefunden",
    no_vehicle_body:
      "Füge die Integration hinzu oder setze <code>device:</code> / <code>vin:</code> in der Kartenkonfiguration.",
    // tire
    front: "VORNE",
    fl: "VL",
    fr: "VR",
    rl: "HL",
    rr: "HR",
    front_left: "Vorne links",
    front_right: "Vorne rechts",
    rear_left: "Hinten links",
    rear_right: "Hinten rechts",
    tires: "Reifen",
    no_tire_data:
      "Noch keine Reifendaten für dieses Fahrzeug. Aktiviere den Cluster „Reifendaten“ und fahre, um Werte zu erfassen.",
    check_pressure: "Druck prüfen",
    slightly_high: "Etwas hoch",
    all_nominal: "Alles normal",
    of_four: "{n} von 4",
    t_low: "Niedrig",
    t_high: "Hoch",
    t_ok: "OK",
    t_nodata: "Keine Daten",
    t_wear: "Abgefahren",
    check_tyres: "Reifen prüfen",
    wear_due: "Wechsel in",
    fitted: "montiert",
    t_current: "Aktuell",
    // tire summary + per-wheel fitment
    sum_pressure: "Druck",
    sum_wear: "Verschleiß",
    p_target: "Soll",
    p_target_varies: "Sollwerte unterschiedlich",
    w_ok: "OK",
    w_soon: "Bald prüfen",
    w_due: "Wechseln",
    season_summer: "Sommer",
    season_winter: "Winter",
    season_all: "Ganzjahres",
    runflat: "Runflat",
    // closures / security
    cl_closures: "Sicherheit & Öffnungen",
    closures_none:
      "Noch keine Tür-, Fenster- oder Sicherheitsdaten für dieses Fahrzeug. Aktiviere den Cluster „Fahrzeugstatus“ in den Integrationsoptionen.",
    central_lock: "Zentralverriegelung",
    alarm_word: "Diebstahlwarnanlage",
    alarm_armed: "Scharf",
    alarm_disarmed: "Unscharf",
    alarm_triggered: "Alarm ausgelöst",
    secured: "Gesichert",
    locked: "Verriegelt",
    unlocked: "Entriegelt",
    partially_locked: "Teilweise verriegelt",
    all_closed: "Alles geschlossen",
    windows_open: "Fenster offen",
    n_open: "{n} offen",
    state_open: "Offen",
    state_closed: "Geschlossen",
    state_tilted: "Gekippt",
    door_word: "Tür",
    window_word: "Fenster",
    hood_word: "Motorhaube",
    trunk_word: "Kofferraum",
    rear_window_word: "Heckscheibe",
    sunroof_word: "Schiebedach",
    // charging history
    ch_title: "Ladeverlauf",
    ch_month: "Dieser Monat",
    ch_loading: "Ladeverlauf wird geladen…",
    ch_empty:
      "Noch keine Ladevorgänge aufgezeichnet. Sobald das Fahrzeug lädt, erscheinen sie hier automatisch.",
    ch_empty_month: "Keine Ladevorgänge in diesem Monat.",
    ch_error: "Ladeverlauf konnte nicht geladen werden. Lade die Seite neu und versuche es erneut.",
    mn_prev: "Vorheriger Monat",
    mn_next: "Nächster Monat",
    ch_home: "Zuhause",
    ch_public: "Unterwegs",
    ch_assumed: "angenommen",
    ch_partial: "Teilpreis",
    ch_session_one: "1 Ladevorgang",
    ch_session_many: "{n} Ladevorgänge",
    ch_peak: "Spitze",
    ch_avg: "Ø",
    ch_grid: "Aus dem Netz",
    ch_solar: "Solar",
    ch_mix: "Energiequelle",
    ch_mix_pv: "PV",
    ch_mix_battery: "Hausspeicher",
    ch_mix_grid: "Netz",
    ch_mix_unknown: "nicht zuordenbar",
    ch_duration: "Dauer",
    ch_cost: "Kosten",
    ch_no_cost: "kein Preis gesetzt",
    ch_ongoing: "lädt…",
    // battery health
    bh_title: "Batteriezustand",
    bh_empty:
      "Noch keine Daten zum Batteriezustand. Sobald das Fahrzeug einige Ladevorgänge über einen weiten Bereich aufzeichnet, erscheint hier die nutzbare Kapazität.",
    bh_learning: "Lernt ({n}/{total})",
    bh_learning_hint:
      "Die nutzbare Kapazität wird aus deinen Ladevorgängen geschätzt. Ein paar Ladungen über einen weiten Bereich (z. B. 20 → 80 %) beschleunigen das.",
    bh_suspicious:
      "Wird mit BMWs eigener Kapazitätsangabe abgeglichen, bevor ein Wert angezeigt wird.",
    bh_usable: "Nutzbare Kapazität",
    bh_of_new: "{p} % vom Original",
    bh_nominal: "Neuwert",
    bh_analysed: "Basis",
    bh_samples: "{n} Ladevorgänge",
    bh_trend_title: "Kapazität nach Laufleistung",
    // efficiency and real range
    ef_title: "Effizienz & Reichweite",
    ef_loading: "Reale Reichweite wird berechnet…",
    ef_empty:
      "Noch zu wenig Ladehistorie. Sobald zwei Ladevorgänge rund 50 km Fahrt einschließen, erscheinen hier der reale Verbrauch und die Reichweite, die er ergibt.",
    ef_no_capacity:
      "Dieses Fahrzeug meldet keine Batteriekapazität, daher lässt sich aus dem gemessenen Verbrauch keine Reichweite berechnen.",
    ef_hybrid:
      "Ein Plug-in-Hybrid fährt einen Teil seiner Strecke mit Kraftstoff, und keine der Fahrzeugdaten verrät, wie viel. Ein aus der Ladehistorie berechneter Verbrauch oder eine Reichweite sähen deshalb weit besser aus, als die Batterie tatsächlich ist, und werden nicht angezeigt. Batteriezustand und Ladehistorie sind davon nicht betroffen.",
    ef_error: "Effizienzdaten konnten nicht geladen werden. Seite neu laden und erneut versuchen.",
    ef_range_now: "Reale Reichweite jetzt",
    ef_at_soc: "bei {p} % Ladung",
    ef_range_full: "{km} km bei voller Batterie",
    ef_vs_bmw_over: "{p} % weiter als das Fahrzeug vorhersagt ({km} km)",
    ef_vs_bmw_under: "{p} % weniger als das Fahrzeug vorhersagt ({km} km)",
    ef_vs_bmw_same: "Genau wie die Prognose des Fahrzeugs ({km} km)",
    ef_consumption: "Gemessener Verbrauch",
    ef_side_battery: "ab Batterie",
    ef_side_grid: "ab Steckdose",
    ef_window_days: "letzte {n} Tage",
    ef_window_all: "gesamte Historie",
    ef_loss: "Ladeverlust",
    ef_capacity: "Nutzbare Kapazität",
    ef_capacity_measured: "gemessen",
    ef_capacity_bmw: "BMW-Wert",
    ef_cost: "Kosten pro 100 km",
    ef_solar: "{p} % Solar diesen Monat",
    ef_trend_title: "Verbrauch nach Monat",
    ef_footnote:
      "Gemessen aus der Ladehistorie: die Strecke zwischen zwei Ladevorgängen und die Energie, die hineinging — nicht die Prognose des Fahrzeugs.",
    // charging, battery health and efficiency on a petrol or diesel car
    ice_view_title: "Für dieses Fahrzeug gibt es hier nichts",
    ice_view_body:
      "{name} fährt nur mit Kraftstoff, daher gibt es keine Ladevorgänge, keinen Batteriezustand und keine elektrische Reichweite. Wird es doch geladen, im Karteneditor den Antrieb einstellen.",
    // trips
    tr_title: "Fahrten",
    tr_loading: "Fahrten werden geladen…",
    tr_empty:
      "Noch keine Fahrten aufgezeichnet. Sobald das Fahrzeug bewegt wird, erscheinen Fahrten hier automatisch.",
    tr_empty_month: "Keine Fahrten in diesem Monat.",
    tr_error: "Fahrten konnten nicht geladen werden. Seite neu laden und erneut versuchen.",
    tr_trip_one: "1 Fahrt",
    tr_trip_many: "{n} Fahrten",
    tr_review: "Dieser Monat",
    tr_vs_last: "ggü. Vormonat",
    tr_business: "Geschäftlich",
    tr_private: "Privat",
    tr_commute: "Pendeln",
    tr_unclassified: "Nicht zugeordnet",
    tr_consumption: "Ø Verbrauch",
    tr_at_plug: "ab Steckdose",
    tr_at_battery: "ab Akku",
    tr_charge_loss: "{n}% Ladeverlust",
    tr_recuperation: "Rekuperiert",
    tr_style: "Fahrstil",
    tr_style_trend: "Fahrstil über Zeit",
    tr_top_dest: "Häufigste Ziele",
    tr_est_cost: "Gesch. Kosten",
    tr_longest: "Längste Fahrt",
    tr_duration: "Dauer",
    tr_distance: "Strecke",
    tr_soc_used: "Batterie verbraucht",
    tr_visits: "{n}×",
    tr_auto: "auto",
    tr_classify: "Zuordnen",
    tr_best: "Beste",
    tr_worst: "Schlechteste",
    tr_unknown_place: "Unbekannt",
    tr_open: "Fahrt aktiv",
    tr_open_hint: "Eine Fahrt ist im Gange",
    tr_min: "{n} Min.",
    tr_open_note:
      "Diese Fahrt läuft noch. Strecke und Verbrauch sind vorläufig; zuordnen lässt sich die Fahrt, sobald sie beendet ist.",
    // map
    mp_title: "Fahrtenkarte",
    mp_loading: "Karte wird geladen…",
    mp_error: "Karte konnte nicht geladen werden. Seite neu laden und erneut versuchen.",
    mp_empty_none:
      "Noch keine Orte zum Anzeigen. Unter Konfigurieren → Fahrten „Route aufzeichnen“ aktivieren, dann fahren — die besuchten Orte erscheinen hier.",
    mp_empty_window: "In diesem Zeitraum keine Orte aufgezeichnet.",
    mp_unavailable: "Die Karte konnte in diesem Browser nicht geladen werden.",
    mp_win_month: "Dieser Monat",
    mp_win_3m: "3 Monate",
    mp_win_all: "Alle",
    mp_route_one: "1 Route",
    mp_route_many: "{n} Routen",
    // export
    ex_csv: "CSV",
    ex_report: "Bericht",
    ex_csv_hint: "Diesen Monat als Tabelle herunterladen",
    ex_report_hint: "Druckbaren Monatsbericht öffnen (zum Drucken als PDF)",
    ex_empty: "Für diesen Monat ist noch nichts aufgezeichnet.",
    ex_error: "Export fehlgeschlagen. Bitte das Home-Assistant-Log prüfen.",
    // editor
    ed_device: "Fahrzeug",
    ed_cluster: "Modus",
    ed_title: "Titel (optional)",
    ed_image: "Bild-Entität",
    ed_soc: "Ladezustand",
    ed_range: "Reichweite",
    ed_charging: "Ladestatus",
    ed_target_soc: "Ladeziel",
    ed_time_to_full: "Bis voll",
    ed_odometer: "Kilometerstand",
    ed_plug: "Stecker / Verbindung",
    ed_fuel: "Tank",
    ed_drivetrain: "Antrieb",
    edh_drivetrain: "Wird aus den gestreamten Daten erkannt. Nur setzen, wenn die Karte das falsche Layout wählt.",
    dt_auto: "Automatisch",
    dt_bev: "Elektrisch",
    dt_phev: "Plug-in-Hybrid",
    dt_ice: "Benzin / Diesel",
    ed_steering: "Lenkrad",
    edh_steering: "Wird aus BMWs Fahrzeugdaten gelesen. Nur setzen, wenn Türen und Fenster auf der falschen Seite des Autos erscheinen.",
    st_left: "Linkslenker",
    st_right: "Rechtslenker",
    ed_shape: "Fahrzeugform",
    edh_shape: "Wird anhand des Modells in BMWs Fahrzeugdaten gewählt. Nur setzen, wenn die Zeichnung nicht zum Auto passt.",
    sh_sedan: "BMW-Limousine",
    sh_countryman: "MINI Countryman",
    ed_overview_option: "Übersicht (Standard)",
    ed_overrides_title: "Entitäten überschreiben (optional — leer lassen für Auto-Erkennung)",
    edh_cluster:
      "Die Übersicht zeigt das Fahrzeugbild und Kennzahlen. Der Ladeverlauf listet aufgezeichnete Ladevorgänge mit Kosten und Ladekurve. Fahrten listet aufgezeichnete Fahrten mit einer Monatsübersicht. Die Fahrtenkarte zeichnet aufgezeichnete Routen auf einer Karte (benötigt aktiviertes „Fahrtrouten aufzeichnen“). Der Batteriezustand zeigt die gelernte nutzbare Kapazität und ihren Verlauf. Ein Cluster listet alle Werte dieser Gruppe auf. Eine laufende Fahrt erscheint als Abzeichen in der Übersicht und als aktive Zeile oben in Fahrten.",
    edh_title: "Überschreibt den auf der Karte angezeigten Fahrzeugnamen.",
  },
  fr: {
    // cluster labels
    cl_electric: "Véhicule électrique",
    cl_status: "État du véhicule",
    cl_metadata: "Métadonnées",
    cl_events: "Événements du véhicule",
    cl_tire: "Pneus",
    cl_basic: "Données de base du véhicule",
    cl_usage: "Données d'utilisation",
    cl_other: "Autres",
    cl_contract: "Contrat ConnectedDrive",
    // overview
    remaining_range: "autonomie restante",
    charging_status: "état de charge",
    charge: "charge",
    charging: "en charge",
    is_charging: "En charge",
    not_charging: "Pas en charge",
    target: "Objectif",
    plug: "Prise",
    fuel: "carburant",
    tank: "Réservoir",
    electric_range: "autonomie électrique",
    total_range: "Autonomie totale",
    time_to_full: "Charge complète dans",
    charge_time: "Durée de charge",
    odometer: "Kilométrage",
    last_update: "Dernière mise à jour",
    spin_hint: "Faites glisser pour tourner la voiture",
    hs_door_open: "Porte ouverte",
    hs_doors_open: "{n} portes ouvertes",
    hs_trunk_open: "Coffre ouvert",
    hs_hood_open: "Capot ouvert",
    hs_windows_open: "Vitres ouvertes",
    hs_charging: "En charge",
    hs_interior: "Intérieur",
    hs_exterior: "Extérieur",
    hs_default_trim: "Finition BMW par défaut affichée",
    eq_title: "Équipement",
    eq_count: "{n} éléments",
    eq_empty: "Activez Configurer → Image du véhicule → Configurateur BMW pour lister l'équipement d'usine de la voiture.",
    eq_looks: "Peinture, jantes et inserts",
    eq_packages: "Packs",
    eq_features: "Équipements",
    eq_technical: "Codes techniques ({n})",
    just_now: "à l'instant",
    min_ago: "il y a {n} min",
    h_ago: "il y a {n} h",
    d_ago: "il y a {n} j",
    // cluster list
    value: "valeur",
    values: "valeurs",
    no_cluster_entities:
      "Aucune entité {label} pour ce véhicule. Activez ce groupe dans les options de l'intégration.",
    cc_title: "Messages Check Control",
    cc_none: "Aucun message Check Control signalé.",
    cc_teleservice: "Teleservice",
    cc_mileage: "Dernier affichage à",
    cc_sent: "Envoyé par la voiture",
    cc_code: "Code",
    cc_earlier: "Messages précédents ({n})",
    cc_reported: "Dernier signalement",
    cc_gone: "N'est plus signalé",
    // messages
    no_vehicle_title: "Aucun véhicule BMW CarData trouvé",
    no_vehicle_body:
      "Ajoutez l'intégration, ou définissez <code>device:</code> / <code>vin:</code> dans la configuration de la carte.",
    // tire
    front: "AVANT",
    fl: "AVG",
    fr: "AVD",
    rl: "ARG",
    rr: "ARD",
    front_left: "Avant gauche",
    front_right: "Avant droit",
    rear_left: "Arrière gauche",
    rear_right: "Arrière droit",
    tires: "Pneus",
    no_tire_data:
      "Pas encore de données de pneus pour ce véhicule. Activez le groupe Pneus et roulez pour obtenir des mesures.",
    check_pressure: "Vérifier la pression",
    slightly_high: "Légèrement élevée",
    all_nominal: "Tout est normal",
    of_four: "{n} sur 4",
    t_low: "Basse",
    t_high: "Élevée",
    t_ok: "OK",
    t_nodata: "Pas de données",
    t_wear: "Usé",
    check_tyres: "Vérifier les pneus",
    wear_due: "à changer dans",
    fitted: "monté",
    t_current: "Actuelle",
    // tire summary + per-wheel fitment
    sum_pressure: "Pression",
    sum_wear: "Usure",
    p_target: "Consigne",
    p_target_varies: "Consignes variables",
    w_ok: "OK",
    w_soon: "À vérifier bientôt",
    w_due: "À remplacer",
    season_summer: "Été",
    season_winter: "Hiver",
    season_all: "4 saisons",
    runflat: "Runflat",
    // closures / security
    cl_closures: "Sécurité et ouvrants",
    closures_none:
      "Pas encore de données de portes, vitres ou sécurité pour ce véhicule. Activez le groupe État du véhicule dans les options de l'intégration.",
    central_lock: "Verrouillage central",
    alarm_word: "Alarme antivol",
    alarm_armed: "Armée",
    alarm_disarmed: "Désarmée",
    alarm_triggered: "Alarme déclenchée",
    secured: "Sécurisé",
    locked: "Verrouillé",
    unlocked: "Déverrouillé",
    partially_locked: "Partiellement verrouillé",
    all_closed: "Tout est fermé",
    windows_open: "Vitres ouvertes",
    n_open: "{n} ouvert(s)",
    state_open: "Ouvert",
    state_closed: "Fermé",
    state_tilted: "Entrouvert",
    door_word: "Porte",
    window_word: "Vitre",
    hood_word: "Capot",
    trunk_word: "Coffre",
    rear_window_word: "Lunette arrière",
    sunroof_word: "Toit ouvrant",
    // charging history
    ch_title: "Historique de recharge",
    ch_month: "Ce mois-ci",
    ch_loading: "Chargement de l'historique de recharge…",
    ch_empty:
      "Aucune session de recharge enregistrée pour l'instant. Dès que la voiture charge, les sessions apparaissent ici automatiquement.",
    ch_empty_month: "Aucune recharge ce mois-ci.",
    ch_error: "Impossible de charger l'historique de recharge. Rechargez la page et réessayez.",
    mn_prev: "Mois précédent",
    mn_next: "Mois suivant",
    ch_home: "Domicile",
    ch_public: "Ailleurs",
    ch_assumed: "supposé",
    ch_partial: "prix partiel",
    ch_session_one: "1 session",
    ch_session_many: "{n} sessions",
    ch_peak: "Pic",
    ch_avg: "Moy.",
    ch_grid: "Du réseau",
    ch_solar: "solaire",
    ch_mix: "Source d'énergie",
    ch_mix_pv: "PV",
    ch_mix_battery: "Batterie domestique",
    ch_mix_grid: "Réseau",
    ch_mix_unknown: "non attribué",
    ch_duration: "Durée",
    ch_cost: "Coût",
    ch_no_cost: "aucun prix défini",
    ch_ongoing: "en charge…",
    // battery health
    bh_title: "Santé de la batterie",
    bh_empty:
      "Pas encore de données de santé de la batterie. Après quelques recharges sur une large plage, sa capacité utile apparaît ici.",
    bh_learning: "Apprentissage ({n}/{total})",
    bh_learning_hint:
      "Estimation de la capacité utile à partir de vos recharges. Quelques recharges sur une large plage (p. ex. 20 → 80 %) l'accélèrent le plus.",
    bh_suspicious:
      "Vérification croisée avec la capacité indiquée par BMW avant d'afficher un chiffre.",
    bh_usable: "Capacité utile",
    bh_of_new: "{p} % de l'origine",
    bh_nominal: "Neuve",
    bh_analysed: "Basé sur",
    bh_samples: "{n} recharges",
    bh_trend_title: "Capacité selon le kilométrage",
    // efficiency and real range
    ef_title: "Efficacité et autonomie",
    ef_loading: "Calcul de votre autonomie réelle…",
    ef_empty:
      "Pas encore assez d'historique de recharge. Dès que deux recharges encadrent environ 50 km de conduite, votre consommation réelle et l'autonomie qu'elle permet apparaissent ici.",
    ef_no_capacity:
      "Cette voiture n'a pas indiqué la capacité de sa batterie ; aucune autonomie ne peut donc être déduite de la consommation mesurée.",
    ef_hybrid:
      "Une hybride rechargeable parcourt une partie de sa distance au carburant, et rien de ce que la voiture envoie n'indique quelle part. Une consommation ou une autonomie calculée à partir de l'historique de recharge paraîtrait bien meilleure que la batterie ne l'est réellement ; rien n'est donc affiché. La santé de la batterie et l'historique de recharge ne sont pas concernés.",
    ef_error: "Impossible de charger les données d'efficacité. Rechargez la page et réessayez.",
    ef_range_now: "Autonomie réelle actuelle",
    ef_at_soc: "à {p} % de charge",
    ef_range_full: "{km} km batterie pleine",
    ef_vs_bmw_over: "{p} % de plus que la prévision de la voiture ({km} km)",
    ef_vs_bmw_under: "{p} % de moins que la prévision de la voiture ({km} km)",
    ef_vs_bmw_same: "Identique à l'estimation de la voiture ({km} km)",
    ef_consumption: "Consommation mesurée",
    ef_side_battery: "à la batterie",
    ef_side_grid: "à la prise",
    ef_window_days: "{n} derniers jours",
    ef_window_all: "tous les enregistrements",
    ef_loss: "Pertes de recharge",
    ef_capacity: "Capacité utile",
    ef_capacity_measured: "mesurée",
    ef_capacity_bmw: "valeur BMW",
    ef_cost: "Coût aux 100 km",
    ef_solar: "{p} % solaire ce mois-ci",
    ef_trend_title: "Consommation par mois",
    ef_footnote:
      "Mesuré à partir du registre de recharge : la distance entre deux recharges et l'énergie chargée, pas l'estimation de la voiture.",
    // charging, battery health and efficiency on a petrol or diesel car
    ice_view_title: "Rien à afficher pour cette voiture",
    ice_view_body:
      "{name} roule uniquement au carburant ; il n'y a donc ni recharge, ni santé de batterie, ni autonomie électrique à afficher. Si elle se branche, définissez la Motorisation dans l'éditeur de la carte.",
    // trips
    tr_title: "Trajets",
    tr_loading: "Chargement des trajets…",
    tr_empty:
      "Aucun trajet enregistré pour l'instant. Dès que la voiture roule, les trajets apparaissent ici automatiquement.",
    tr_empty_month: "Aucun trajet ce mois-ci.",
    tr_error: "Impossible de charger les trajets. Rechargez la page et réessayez.",
    tr_trip_one: "1 trajet",
    tr_trip_many: "{n} trajets",
    tr_review: "Ce mois-ci",
    tr_vs_last: "par rapport au mois dernier",
    tr_business: "Professionnel",
    tr_private: "Privé",
    tr_commute: "Domicile-travail",
    tr_unclassified: "Non classé",
    tr_consumption: "Conso. moyenne",
    tr_at_plug: "à la prise",
    tr_at_battery: "à la batterie",
    tr_charge_loss: "{n} % de pertes de recharge",
    tr_recuperation: "Récupéré",
    tr_style: "Style de conduite",
    tr_style_trend: "Style dans le temps",
    tr_top_dest: "Destinations principales",
    tr_est_cost: "Coût estimé",
    tr_longest: "Trajet le plus long",
    tr_duration: "Durée",
    tr_distance: "Distance",
    tr_soc_used: "Batterie utilisée",
    tr_visits: "{n}×",
    tr_auto: "auto",
    tr_classify: "Classer",
    tr_best: "Meilleur",
    tr_worst: "Pire",
    tr_unknown_place: "Inconnu",
    tr_open: "Trajet en cours",
    tr_open_hint: "Un trajet est en cours",
    tr_min: "{n} min",
    tr_open_note:
      "Ce trajet est toujours en cours. La distance et la consommation sont provisoires, et le trajet pourra être classé une fois terminé.",
    // map
    mp_title: "Carte des trajets",
    mp_loading: "Chargement de la carte…",
    mp_error: "Impossible de charger la carte. Rechargez la page et réessayez.",
    mp_empty_none:
      "Aucun lieu à afficher pour l'instant. Activez « Enregistrer l'itinéraire » sous Configurer → Trajets, puis roulez — les lieux visités apparaissent ici.",
    mp_empty_window: "Aucun lieu enregistré sur cette période.",
    mp_unavailable: "La carte n'a pas pu être chargée dans ce navigateur.",
    mp_win_month: "Ce mois-ci",
    mp_win_3m: "3 mois",
    mp_win_all: "Tout",
    mp_route_one: "1 itinéraire",
    mp_route_many: "{n} itinéraires",
    // export
    ex_csv: "CSV",
    ex_report: "Rapport",
    ex_csv_hint: "Télécharger ce mois sous forme de tableur",
    ex_report_hint: "Ouvrir un rapport mensuel imprimable (imprimez-le pour obtenir un PDF)",
    ex_empty: "Rien d'enregistré pour ce mois pour l'instant.",
    ex_error: "L'export a échoué. Consultez le journal de Home Assistant.",
    // editor
    ed_device: "Véhicule",
    ed_cluster: "Mode",
    ed_title: "Titre (facultatif)",
    ed_image: "Entité image",
    ed_soc: "État de charge",
    ed_range: "Autonomie",
    ed_charging: "État de la recharge",
    ed_target_soc: "Objectif de charge",
    ed_time_to_full: "Charge complète dans",
    ed_odometer: "Kilométrage",
    ed_plug: "Prise / connexion",
    ed_fuel: "Réservoir de carburant",
    ed_drivetrain: "Motorisation",
    edh_drivetrain: "Détectée à partir de ce que la voiture transmet. Ne la définissez que si la carte choisit la mauvaise disposition.",
    dt_auto: "Détection automatique",
    dt_bev: "Électrique",
    dt_phev: "Hybride rechargeable",
    dt_ice: "Essence / diesel",
    ed_steering: "Volant",
    edh_steering: "Lu dans les données du véhicule de BMW. Ne le définissez que si les portes et les vitres apparaissent du mauvais côté de la voiture.",
    st_left: "Conduite à gauche",
    st_right: "Conduite à droite",
    ed_shape: "Silhouette du véhicule",
    edh_shape: "Choisie d'après le modèle dans les données du véhicule de BMW. Ne la définissez que si le dessin ne correspond pas à votre voiture.",
    sh_sedan: "Berline BMW",
    sh_countryman: "MINI Countryman",
    ed_overview_option: "Aperçu (par défaut)",
    ed_overrides_title: "Remplacement d'entités (facultatif — laisser vide pour la détection automatique)",
    edh_cluster:
      "L'aperçu affiche l'image du véhicule et les chiffres clés. L'historique de recharge liste les sessions enregistrées avec coût et courbe de puissance. Les trajets listent les trajets enregistrés avec un bilan mensuel. La carte des trajets trace les itinéraires enregistrés sur une carte (nécessite « Enregistrer l'itinéraire »). La santé de la batterie affiche la capacité utile apprise et son évolution. Un groupe affiche toutes les valeurs de ce groupe sous forme de liste. Un trajet en cours apparaît comme un badge sur l'aperçu et comme une ligne en direct en haut des trajets.",
    edh_title: "Remplace le nom du véhicule affiché sur la carte.",
  },
  it: {
    // cluster labels
    cl_electric: "Veicolo elettrico",
    cl_status: "Stato del veicolo",
    cl_metadata: "Metadati",
    cl_events: "Eventi del veicolo",
    cl_tire: "Pneumatici",
    cl_basic: "Dati di base del veicolo",
    cl_usage: "Dati di utilizzo",
    cl_other: "Altro",
    cl_contract: "Contratto ConnectedDrive",
    // overview
    remaining_range: "autonomia residua",
    charging_status: "stato di ricarica",
    charge: "carica",
    charging: "in carica",
    is_charging: "In carica",
    not_charging: "Non in carica",
    target: "Obiettivo",
    plug: "Spina",
    fuel: "carburante",
    tank: "Serbatoio",
    electric_range: "autonomia elettrica",
    total_range: "Autonomia totale",
    time_to_full: "Carica completa tra",
    charge_time: "Tempo di ricarica",
    odometer: "Contachilometri",
    last_update: "Ultimo aggiornamento",
    spin_hint: "Trascina per ruotare l'auto",
    hs_door_open: "Porta aperta",
    hs_doors_open: "{n} porte aperte",
    hs_trunk_open: "Bagagliaio aperto",
    hs_hood_open: "Cofano aperto",
    hs_windows_open: "Finestrini aperti",
    hs_charging: "In carica",
    hs_interior: "Interni",
    hs_exterior: "Esterni",
    hs_default_trim: "Mostrato l'allestimento BMW standard",
    eq_title: "Equipaggiamento",
    eq_count: "{n} voci",
    eq_empty: "Attiva Configura → Immagine del veicolo → Configuratore BMW per elencare l'equipaggiamento di fabbrica dell'auto.",
    eq_looks: "Vernice, cerchi e modanature",
    eq_packages: "Pacchetti",
    eq_features: "Dotazioni",
    eq_technical: "Codici tecnici ({n})",
    just_now: "proprio ora",
    min_ago: "{n} min fa",
    h_ago: "{n} h fa",
    d_ago: "{n} g fa",
    // cluster list
    value: "valore",
    values: "valori",
    no_cluster_entities:
      "Nessuna entità {label} per questo veicolo. Attiva il gruppo nelle opzioni dell'integrazione.",
    cc_title: "Messaggi Check Control",
    cc_none: "Nessun messaggio Check Control segnalato.",
    cc_teleservice: "Teleservice",
    cc_mileage: "Mostrato l'ultima volta a",
    cc_sent: "Inviato dall'auto",
    cc_code: "Codice",
    cc_earlier: "Messaggi precedenti ({n})",
    cc_reported: "Ultima segnalazione",
    cc_gone: "Non più segnalato",
    // messages
    no_vehicle_title: "Nessun veicolo BMW CarData trovato",
    no_vehicle_body:
      "Aggiungi l'integrazione, oppure imposta <code>device:</code> / <code>vin:</code> nella configurazione della scheda.",
    // tire
    front: "ANTERIORE",
    fl: "AS",
    fr: "AD",
    rl: "PS",
    rr: "PD",
    front_left: "Anteriore sinistro",
    front_right: "Anteriore destro",
    rear_left: "Posteriore sinistro",
    rear_right: "Posteriore destro",
    tires: "Pneumatici",
    no_tire_data:
      "Ancora nessun dato sugli pneumatici per questo veicolo. Attiva il gruppo Pneumatici e guida per ottenere le letture.",
    check_pressure: "Controlla la pressione",
    slightly_high: "Leggermente alta",
    all_nominal: "Tutto regolare",
    of_four: "{n} su 4",
    t_low: "Bassa",
    t_high: "Alta",
    t_ok: "OK",
    t_nodata: "Nessun dato",
    t_wear: "Usurato",
    check_tyres: "Controlla gli pneumatici",
    wear_due: "da cambiare tra",
    fitted: "montato",
    t_current: "Attuale",
    // tire summary + per-wheel fitment
    sum_pressure: "Pressione",
    sum_wear: "Usura",
    p_target: "Nominale",
    p_target_varies: "Valori nominali diversi",
    w_ok: "OK",
    w_soon: "Controlla presto",
    w_due: "Sostituire",
    season_summer: "Estivo",
    season_winter: "Invernale",
    season_all: "4 stagioni",
    runflat: "Runflat",
    // closures / security
    cl_closures: "Sicurezza e aperture",
    closures_none:
      "Ancora nessun dato su porte, finestrini o sicurezza per questo veicolo. Attiva il gruppo Stato del veicolo nelle opzioni dell'integrazione.",
    central_lock: "Chiusura centralizzata",
    alarm_word: "Allarme antifurto",
    alarm_armed: "Inserito",
    alarm_disarmed: "Disinserito",
    alarm_triggered: "Allarme scattato",
    secured: "Protetto",
    locked: "Bloccato",
    unlocked: "Sbloccato",
    partially_locked: "Parzialmente bloccato",
    all_closed: "Tutto chiuso",
    windows_open: "Finestrini aperti",
    n_open: "{n} aperti",
    state_open: "Aperto",
    state_closed: "Chiuso",
    state_tilted: "Socchiuso",
    door_word: "Porta",
    window_word: "Finestrino",
    hood_word: "Cofano",
    trunk_word: "Bagagliaio",
    rear_window_word: "Lunotto",
    sunroof_word: "Tetto apribile",
    // charging history
    ch_title: "Storico di ricarica",
    ch_month: "Questo mese",
    ch_loading: "Caricamento dello storico di ricarica…",
    ch_empty:
      "Ancora nessuna sessione di ricarica registrata. Quando l'auto ricarica, le sessioni compaiono qui automaticamente.",
    ch_empty_month: "Nessuna ricarica in questo mese.",
    ch_error: "Impossibile caricare lo storico di ricarica. Ricarica la pagina e riprova.",
    mn_prev: "Mese precedente",
    mn_next: "Mese successivo",
    ch_home: "Casa",
    ch_public: "Fuori casa",
    ch_assumed: "presunto",
    ch_partial: "prezzo parziale",
    ch_session_one: "1 sessione",
    ch_session_many: "{n} sessioni",
    ch_peak: "Picco",
    ch_avg: "Media",
    ch_grid: "Dalla rete",
    ch_solar: "fotovoltaico",
    ch_mix: "Fonte di energia",
    ch_mix_pv: "FV",
    ch_mix_battery: "Batteria di casa",
    ch_mix_grid: "Rete",
    ch_mix_unknown: "non attribuito",
    ch_duration: "Durata",
    ch_cost: "Costo",
    ch_no_cost: "nessun prezzo impostato",
    ch_ongoing: "in carica…",
    // battery health
    bh_title: "Salute della batteria",
    bh_empty:
      "Ancora nessun dato sulla salute della batteria. Dopo alcune ricariche su un ampio intervallo, qui compare la sua capacità utile.",
    bh_learning: "Apprendimento ({n}/{total})",
    bh_learning_hint:
      "Stima della capacità utile dalle tue ricariche. Alcune ricariche su un ampio intervallo (ad es. 20 → 80%) la accelerano di più.",
    bh_suspicious:
      "Verifica incrociata con il valore di capacità di BMW prima di mostrare un numero.",
    bh_usable: "Capacità utile",
    bh_of_new: "{p}% dell'originale",
    bh_nominal: "Come nuova",
    bh_analysed: "Basato su",
    bh_samples: "{n} ricariche",
    bh_trend_title: "Capacità rispetto ai chilometri",
    // efficiency and real range
    ef_title: "Efficienza e autonomia",
    ef_loading: "Calcolo della tua autonomia reale…",
    ef_empty:
      "Storico di ricarica ancora insufficiente. Quando due ricariche racchiudono circa 50 km di guida, qui compaiono il tuo consumo reale e l'autonomia che ne deriva.",
    ef_no_capacity:
      "Quest'auto non ha comunicato la capacità della batteria, quindi non è possibile ricavare un'autonomia dal consumo misurato.",
    ef_hybrid:
      "Un'ibrida plug-in percorre parte della distanza a carburante, e nulla di ciò che l'auto invia dice quanta. Un consumo o un'autonomia calcolati dallo storico di ricarica sembrerebbero molto migliori di quanto sia davvero la batteria, quindi non viene mostrato nulla. Salute della batteria e storico di ricarica non sono interessati.",
    ef_error: "Impossibile caricare i dati di efficienza. Ricarica la pagina e riprova.",
    ef_range_now: "Autonomia reale ora",
    ef_at_soc: "al {p}% di carica",
    ef_range_full: "{km} km a batteria piena",
    ef_vs_bmw_over: "{p}% in più della previsione dell'auto ({km} km)",
    ef_vs_bmw_under: "{p}% in meno della previsione dell'auto ({km} km)",
    ef_vs_bmw_same: "Uguale alla stima dell'auto ({km} km)",
    ef_consumption: "Consumo misurato",
    ef_side_battery: "dalla batteria",
    ef_side_grid: "alla presa",
    ef_window_days: "ultimi {n} giorni",
    ef_window_all: "tutti i dati",
    ef_loss: "Perdite di ricarica",
    ef_capacity: "Capacità utile",
    ef_capacity_measured: "misurata",
    ef_capacity_bmw: "valore BMW",
    ef_cost: "Costo per 100 km",
    ef_solar: "{p}% fotovoltaico questo mese",
    ef_trend_title: "Consumo per mese",
    ef_footnote:
      "Misurato dal registro di ricarica: la distanza tra due ricariche e l'energia immessa, non la stima dell'auto.",
    // charging, battery health and efficiency on a petrol or diesel car
    ice_view_title: "Niente da mostrare per quest'auto",
    ice_view_body:
      "{name} va solo a carburante, quindi non ci sono ricarica, salute della batteria o autonomia elettrica da mostrare. Se si collega alla presa, imposta la Motorizzazione nell'editor della scheda.",
    // trips
    tr_title: "Viaggi",
    tr_loading: "Caricamento dei viaggi…",
    tr_empty:
      "Ancora nessun viaggio registrato. Quando l'auto viene guidata, i viaggi compaiono qui automaticamente.",
    tr_empty_month: "Nessun viaggio in questo mese.",
    tr_error: "Impossibile caricare i viaggi. Ricarica la pagina e riprova.",
    tr_trip_one: "1 viaggio",
    tr_trip_many: "{n} viaggi",
    tr_review: "Questo mese",
    tr_vs_last: "rispetto al mese scorso",
    tr_business: "Lavoro",
    tr_private: "Privato",
    tr_commute: "Pendolarismo",
    tr_unclassified: "Non classificato",
    tr_consumption: "Consumo medio",
    tr_at_plug: "alla presa",
    tr_at_battery: "alla batteria",
    tr_charge_loss: "{n}% di perdite di ricarica",
    tr_recuperation: "Recuperato",
    tr_style: "Stile di guida",
    tr_style_trend: "Stile nel tempo",
    tr_top_dest: "Destinazioni principali",
    tr_est_cost: "Costo stimato",
    tr_longest: "Viaggio più lungo",
    tr_duration: "Durata",
    tr_distance: "Distanza",
    tr_soc_used: "Batteria usata",
    tr_visits: "{n}×",
    tr_auto: "auto",
    tr_classify: "Classifica",
    tr_best: "Migliore",
    tr_worst: "Peggiore",
    tr_unknown_place: "Sconosciuto",
    tr_open: "Viaggio in corso",
    tr_open_hint: "Un viaggio è in corso",
    tr_min: "{n} min",
    tr_open_note:
      "Questo viaggio è ancora in corso. Distanza e consumo sono provvisori, e il viaggio potrà essere classificato al termine.",
    // map
    mp_title: "Mappa dei viaggi",
    mp_loading: "Caricamento della mappa…",
    mp_error: "Impossibile caricare la mappa. Ricarica la pagina e riprova.",
    mp_empty_none:
      "Ancora nessun luogo da mostrare. Attiva “Registra percorso” in Configura → Viaggi, poi guida — i luoghi visitati compaiono qui.",
    mp_empty_window: "Nessun luogo registrato in questo periodo.",
    mp_unavailable: "Non è stato possibile caricare la mappa in questo browser.",
    mp_win_month: "Questo mese",
    mp_win_3m: "3 mesi",
    mp_win_all: "Tutto",
    mp_route_one: "1 percorso",
    mp_route_many: "{n} percorsi",
    // export
    ex_csv: "CSV",
    ex_report: "Report",
    ex_csv_hint: "Scarica questo mese come foglio di calcolo",
    ex_report_hint: "Apri un report mensile stampabile (stampalo per ottenere un PDF)",
    ex_empty: "Ancora niente registrato per questo mese.",
    ex_error: "Esportazione non riuscita. Controlla il log di Home Assistant.",
    // editor
    ed_device: "Veicolo",
    ed_cluster: "Modalità",
    ed_title: "Titolo (facoltativo)",
    ed_image: "Entità immagine",
    ed_soc: "Stato di carica",
    ed_range: "Autonomia",
    ed_charging: "Stato di ricarica",
    ed_target_soc: "Obiettivo di carica",
    ed_time_to_full: "Carica completa tra",
    ed_odometer: "Contachilometri",
    ed_plug: "Spina / connessione",
    ed_fuel: "Serbatoio carburante",
    ed_drivetrain: "Motorizzazione",
    edh_drivetrain: "Rilevata da ciò che l'auto trasmette. Impostala solo se la scheda sceglie il layout sbagliato.",
    dt_auto: "Rilevamento automatico",
    dt_bev: "Elettrica",
    dt_phev: "Ibrida plug-in",
    dt_ice: "Benzina / diesel",
    ed_steering: "Volante",
    edh_steering: "Letto dai dati del veicolo di BMW. Impostalo solo se porte e finestrini compaiono sul lato sbagliato dell'auto.",
    st_left: "Guida a sinistra",
    st_right: "Guida a destra",
    ed_shape: "Sagoma dell'auto",
    edh_shape: "Scelta in base al modello nei dati del veicolo di BMW. Impostala solo se il disegno non corrisponde alla tua auto.",
    sh_sedan: "Berlina BMW",
    sh_countryman: "MINI Countryman",
    ed_overview_option: "Panoramica (predefinita)",
    ed_overrides_title: "Sostituzione entità (facoltativo — lascia vuoto per il rilevamento automatico)",
    edh_cluster:
      "La panoramica mostra l'immagine del veicolo e i valori principali. Lo storico di ricarica elenca le sessioni registrate con costo e curva di potenza. Viaggi elenca i viaggi registrati con un riepilogo mensile. La mappa dei viaggi disegna i percorsi registrati su una mappa (richiede “Registra percorso” attivo). La salute della batteria mostra la capacità utile appresa e il suo andamento. Un gruppo mostra tutti i valori di quel gruppo come elenco. Un viaggio in corso compare come badge nella panoramica e come riga in tempo reale in cima a Viaggi.",
    edh_title: "Sostituisce il nome del veicolo mostrato sulla scheda.",
  },
  es: {
    // cluster labels
    cl_electric: "Vehículo eléctrico",
    cl_status: "Estado del vehículo",
    cl_metadata: "Metadatos",
    cl_events: "Eventos del vehículo",
    cl_tire: "Neumáticos",
    cl_basic: "Datos básicos del vehículo",
    cl_usage: "Datos de uso",
    cl_other: "Otros",
    cl_contract: "Contrato ConnectedDrive",
    // overview
    remaining_range: "autonomía restante",
    charging_status: "estado de carga",
    charge: "carga",
    charging: "cargando",
    is_charging: "Cargando",
    not_charging: "Sin cargar",
    target: "Objetivo",
    plug: "Conector",
    fuel: "combustible",
    tank: "Depósito",
    electric_range: "autonomía eléctrica",
    total_range: "Autonomía total",
    time_to_full: "Carga completa en",
    charge_time: "Tiempo de carga",
    odometer: "Cuentakilómetros",
    last_update: "Última actualización",
    spin_hint: "Arrastra para girar el coche",
    hs_door_open: "Puerta abierta",
    hs_doors_open: "{n} puertas abiertas",
    hs_trunk_open: "Maletero abierto",
    hs_hood_open: "Capó abierto",
    hs_windows_open: "Ventanillas abiertas",
    hs_charging: "Cargando",
    hs_interior: "Interior",
    hs_exterior: "Exterior",
    hs_default_trim: "Se muestra el acabado BMW por defecto",
    eq_title: "Equipamiento",
    eq_count: "{n} elementos",
    eq_empty: "Activa Configurar → Imagen del vehículo → Configurador de BMW para listar el equipamiento de fábrica del coche.",
    eq_looks: "Pintura, llantas y molduras",
    eq_packages: "Paquetes",
    eq_features: "Equipamiento",
    eq_technical: "Códigos técnicos ({n})",
    just_now: "ahora mismo",
    min_ago: "hace {n} min",
    h_ago: "hace {n} h",
    d_ago: "hace {n} d",
    // cluster list
    value: "valor",
    values: "valores",
    no_cluster_entities:
      "No hay entidades de {label} para este vehículo. Activa el grupo en las opciones de la integración.",
    cc_title: "Mensajes Check Control",
    cc_none: "No se han notificado mensajes Check Control.",
    cc_teleservice: "Teleservice",
    cc_mileage: "Mostrado por última vez a",
    cc_sent: "Enviado por el coche",
    cc_code: "Código",
    cc_earlier: "Mensajes anteriores ({n})",
    cc_reported: "Último aviso",
    cc_gone: "Ya no se notifica",
    // messages
    no_vehicle_title: "No se encontró ningún vehículo de BMW CarData",
    no_vehicle_body:
      "Añade la integración, o define <code>device:</code> / <code>vin:</code> en la configuración de la tarjeta.",
    // tire
    front: "DELANTE",
    fl: "DI",
    fr: "DD",
    rl: "TI",
    rr: "TD",
    front_left: "Delantero izquierdo",
    front_right: "Delantero derecho",
    rear_left: "Trasero izquierdo",
    rear_right: "Trasero derecho",
    tires: "Neumáticos",
    no_tire_data:
      "Aún no hay datos de neumáticos para este vehículo. Activa el grupo Neumáticos y conduce para obtener lecturas.",
    check_pressure: "Comprobar presión",
    slightly_high: "Algo alta",
    all_nominal: "Todo correcto",
    of_four: "{n} de 4",
    t_low: "Baja",
    t_high: "Alta",
    t_ok: "OK",
    t_nodata: "Sin datos",
    t_wear: "Desgastado",
    check_tyres: "Revisar neumáticos",
    wear_due: "cambiar en",
    fitted: "montado",
    t_current: "Actual",
    // tire summary + per-wheel fitment
    sum_pressure: "Presión",
    sum_wear: "Desgaste",
    p_target: "Nominal",
    p_target_varies: "Nominales distintas",
    w_ok: "OK",
    w_soon: "Revisar pronto",
    w_due: "Cambiar",
    season_summer: "Verano",
    season_winter: "Invierno",
    season_all: "Todo el año",
    runflat: "Runflat",
    // closures / security
    cl_closures: "Seguridad y cierres",
    closures_none:
      "Aún no hay datos de puertas, ventanillas o seguridad para este vehículo. Activa el grupo Estado del vehículo en las opciones de la integración.",
    central_lock: "Cierre centralizado",
    alarm_word: "Alarma antirrobo",
    alarm_armed: "Activada",
    alarm_disarmed: "Desactivada",
    alarm_triggered: "Alarma disparada",
    secured: "Asegurado",
    locked: "Bloqueado",
    unlocked: "Desbloqueado",
    partially_locked: "Parcialmente bloqueado",
    all_closed: "Todo cerrado",
    windows_open: "Ventanillas abiertas",
    n_open: "{n} abiertos",
    state_open: "Abierto",
    state_closed: "Cerrado",
    state_tilted: "Entreabierto",
    door_word: "Puerta",
    window_word: "Ventanilla",
    hood_word: "Capó",
    trunk_word: "Maletero",
    rear_window_word: "Luneta trasera",
    sunroof_word: "Techo solar",
    // charging history
    ch_title: "Historial de carga",
    ch_month: "Este mes",
    ch_loading: "Cargando el historial de carga…",
    ch_empty:
      "Aún no hay sesiones de carga registradas. En cuanto el coche cargue, las sesiones aparecerán aquí automáticamente.",
    ch_empty_month: "Ninguna carga en este mes.",
    ch_error: "No se pudo cargar el historial de carga. Recarga la página e inténtalo de nuevo.",
    mn_prev: "Mes anterior",
    mn_next: "Mes siguiente",
    ch_home: "Casa",
    ch_public: "Fuera",
    ch_assumed: "supuesto",
    ch_partial: "precio parcial",
    ch_session_one: "1 sesión",
    ch_session_many: "{n} sesiones",
    ch_peak: "Pico",
    ch_avg: "Media",
    ch_grid: "De la red",
    ch_solar: "solar",
    ch_mix: "Fuente de energía",
    ch_mix_pv: "FV",
    ch_mix_battery: "Batería doméstica",
    ch_mix_grid: "Red",
    ch_mix_unknown: "sin atribuir",
    ch_duration: "Duración",
    ch_cost: "Coste",
    ch_no_cost: "sin precio definido",
    ch_ongoing: "cargando…",
    // battery health
    bh_title: "Salud de la batería",
    bh_empty:
      "Aún no hay datos de salud de la batería. Tras unas cuantas cargas de amplio rango, su capacidad útil aparecerá aquí.",
    bh_learning: "Aprendiendo ({n}/{total})",
    bh_learning_hint:
      "Estimando la capacidad útil a partir de tus cargas. Unas cuantas cargas de amplio rango (p. ej. 20 → 80 %) la enseñan más rápido.",
    bh_suspicious:
      "Contrastando con la cifra de capacidad de BMW antes de mostrar un número.",
    bh_usable: "Capacidad útil",
    bh_of_new: "{p} % de la original",
    bh_nominal: "Como nueva",
    bh_analysed: "Basado en",
    bh_samples: "{n} cargas",
    bh_trend_title: "Capacidad frente a kilometraje",
    // efficiency and real range
    ef_title: "Eficiencia y autonomía",
    ef_loading: "Calculando tu autonomía real…",
    ef_empty:
      "Todavía no hay suficiente historial de carga. Cuando dos cargas abarquen unos 50 km de conducción, aquí aparecerán tu consumo real y la autonomía que ofrece.",
    ef_no_capacity:
      "Este coche no ha informado de la capacidad de su batería, así que no se puede calcular una autonomía a partir del consumo medido.",
    ef_hybrid:
      "Un híbrido enchufable recorre parte de su distancia con combustible, y nada de lo que envía el coche dice cuánto. Un consumo o una autonomía calculados a partir del historial de carga parecerían mucho mejores de lo que realmente es la batería, así que no se muestra ninguno. La salud de la batería y el historial de carga no se ven afectados.",
    ef_error: "No se pudieron cargar los datos de eficiencia. Recarga la página e inténtalo de nuevo.",
    ef_range_now: "Autonomía real ahora",
    ef_at_soc: "con un {p} % de carga",
    ef_range_full: "{km} km con la batería llena",
    ef_vs_bmw_over: "{p} % más de lo que predice el coche ({km} km)",
    ef_vs_bmw_under: "{p} % menos de lo que predice el coche ({km} km)",
    ef_vs_bmw_same: "Igual que la estimación del coche ({km} km)",
    ef_consumption: "Consumo medido",
    ef_side_battery: "desde la batería",
    ef_side_grid: "en el enchufe",
    ef_window_days: "últimos {n} días",
    ef_window_all: "todos los registros",
    ef_loss: "Pérdidas de carga",
    ef_capacity: "Capacidad útil",
    ef_capacity_measured: "medida",
    ef_capacity_bmw: "cifra de BMW",
    ef_cost: "Coste por 100 km",
    ef_solar: "{p} % solar este mes",
    ef_trend_title: "Consumo por mes",
    ef_footnote:
      "Medido a partir del registro de cargas: la distancia entre dos cargas y la energía que entró, no la estimación del coche.",
    // charging, battery health and efficiency on a petrol or diesel car
    ice_view_title: "Nada que mostrar para este coche",
    ice_view_body:
      "{name} funciona solo con combustible, así que no hay carga, salud de batería ni autonomía eléctrica que mostrar. Si se enchufa, define la Propulsión en el editor de la tarjeta.",
    // trips
    tr_title: "Trayectos",
    tr_loading: "Cargando trayectos…",
    tr_empty:
      "Aún no hay trayectos registrados. En cuanto se conduzca el coche, los trayectos aparecerán aquí automáticamente.",
    tr_empty_month: "Ningún trayecto en este mes.",
    tr_error: "No se pudieron cargar los trayectos. Recarga la página e inténtalo de nuevo.",
    tr_trip_one: "1 trayecto",
    tr_trip_many: "{n} trayectos",
    tr_review: "Este mes",
    tr_vs_last: "frente al mes pasado",
    tr_business: "Trabajo",
    tr_private: "Privado",
    tr_commute: "Desplazamiento",
    tr_unclassified: "Sin clasificar",
    tr_consumption: "Consumo medio",
    tr_at_plug: "en el enchufe",
    tr_at_battery: "en la batería",
    tr_charge_loss: "{n} % de pérdidas de carga",
    tr_recuperation: "Recuperado",
    tr_style: "Estilo de conducción",
    tr_style_trend: "Estilo a lo largo del tiempo",
    tr_top_dest: "Destinos principales",
    tr_est_cost: "Coste est.",
    tr_longest: "Trayecto más largo",
    tr_duration: "Duración",
    tr_distance: "Distancia",
    tr_soc_used: "Batería usada",
    tr_visits: "{n}×",
    tr_auto: "auto",
    tr_classify: "Clasificar",
    tr_best: "Mejor",
    tr_worst: "Peor",
    tr_unknown_place: "Desconocido",
    tr_open: "Trayecto en curso",
    tr_open_hint: "Hay un trayecto en curso",
    tr_min: "{n} min",
    tr_open_note:
      "Este trayecto sigue en curso. La distancia y el consumo son provisionales, y el trayecto se podrá clasificar cuando termine.",
    // map
    mp_title: "Mapa de trayectos",
    mp_loading: "Cargando el mapa…",
    mp_error: "No se pudo cargar el mapa. Recarga la página e inténtalo de nuevo.",
    mp_empty_none:
      "Aún no hay lugares que mostrar. Activa “Registrar ruta” en Configurar → Trayectos y conduce — los lugares que visites aparecerán aquí.",
    mp_empty_window: "No hay lugares registrados en este periodo.",
    mp_unavailable: "No se pudo cargar el mapa en este navegador.",
    mp_win_month: "Este mes",
    mp_win_3m: "3 meses",
    mp_win_all: "Todo",
    mp_route_one: "1 ruta",
    mp_route_many: "{n} rutas",
    // export
    ex_csv: "CSV",
    ex_report: "Informe",
    ex_csv_hint: "Descargar este mes como hoja de cálculo",
    ex_report_hint: "Abrir un informe mensual imprimible (imprímelo para obtener un PDF)",
    ex_empty: "Aún no hay nada registrado este mes.",
    ex_error: "La exportación falló. Revisa el registro de Home Assistant.",
    // editor
    ed_device: "Vehículo",
    ed_cluster: "Modo",
    ed_title: "Título (opcional)",
    ed_image: "Entidad de imagen",
    ed_soc: "Estado de carga",
    ed_range: "Autonomía",
    ed_charging: "Estado de la carga",
    ed_target_soc: "Objetivo de carga",
    ed_time_to_full: "Carga completa en",
    ed_odometer: "Cuentakilómetros",
    ed_plug: "Conector / conexión",
    ed_fuel: "Depósito de combustible",
    ed_drivetrain: "Propulsión",
    edh_drivetrain: "Se detecta a partir de lo que transmite el coche. Defínela solo si la tarjeta elige un diseño incorrecto.",
    dt_auto: "Detección automática",
    dt_bev: "Eléctrico",
    dt_phev: "Híbrido enchufable",
    dt_ice: "Gasolina / diésel",
    ed_steering: "Volante",
    edh_steering: "Se lee de los datos del vehículo de BMW. Defínelo solo si las puertas y las ventanillas aparecen en el lado equivocado del coche.",
    st_left: "Volante a la izquierda",
    st_right: "Volante a la derecha",
    ed_shape: "Silueta del coche",
    edh_shape: "Se elige según el modelo en los datos del vehículo de BMW. Defínela solo si el dibujo no coincide con tu coche.",
    sh_sedan: "Berlina BMW",
    sh_countryman: "MINI Countryman",
    ed_overview_option: "Resumen (predeterminado)",
    ed_overrides_title: "Sustitución de entidades (opcional — déjalo vacío para la detección automática)",
    edh_cluster:
      "El resumen muestra la imagen del vehículo y las cifras clave. El historial de carga enumera las sesiones registradas con coste y curva de potencia. Trayectos enumera los trayectos registrados con un resumen mensual. El mapa de trayectos dibuja las rutas registradas en un mapa (requiere “Registrar ruta” activado). La salud de la batería muestra la capacidad útil aprendida y su evolución. Un grupo muestra todos los valores de ese grupo como una lista. Un trayecto en curso aparece como una insignia en el resumen y como una fila en directo al principio de Trayectos.",
    edh_title: "Sustituye el nombre del vehículo que se muestra en la tarjeta.",
  },
  nl: {
    // cluster labels
    cl_electric: "Elektrisch voertuig",
    cl_status: "Voertuigstatus",
    cl_metadata: "Metadata",
    cl_events: "Voertuiggebeurtenissen",
    cl_tire: "Banden",
    cl_basic: "Basisgegevens voertuig",
    cl_usage: "Gebruiksgegevens",
    cl_other: "Overig",
    cl_contract: "ConnectedDrive-contract",
    // overview
    remaining_range: "resterende actieradius",
    charging_status: "laadstatus",
    charge: "lading",
    charging: "laden",
    is_charging: "Laden",
    not_charging: "Laadt niet",
    target: "Doel",
    plug: "Stekker",
    fuel: "brandstof",
    tank: "Tank",
    electric_range: "elektrische actieradius",
    total_range: "Totale actieradius",
    time_to_full: "Vol over",
    charge_time: "Laadtijd",
    odometer: "Kilometerstand",
    last_update: "Laatste update",
    spin_hint: "Sleep om de auto te draaien",
    hs_door_open: "Deur open",
    hs_doors_open: "{n} deuren open",
    hs_trunk_open: "Kofferbak open",
    hs_hood_open: "Motorkap open",
    hs_windows_open: "Ramen open",
    hs_charging: "Laadt",
    hs_interior: "Interieur",
    hs_exterior: "Exterieur",
    hs_default_trim: "Standaardbekleding van BMW getoond",
    eq_title: "Uitrusting",
    eq_count: "{n} items",
    eq_empty: "Zet Configureren → Voertuigafbeelding → BMW-configurator aan om de fabrieksuitrusting van de auto te tonen.",
    eq_looks: "Lak, velgen en lijsten",
    eq_packages: "Pakketten",
    eq_features: "Voorzieningen",
    eq_technical: "Technische codes ({n})",
    just_now: "zojuist",
    min_ago: "{n} min geleden",
    h_ago: "{n} u geleden",
    d_ago: "{n} d geleden",
    // cluster list
    value: "waarde",
    values: "waarden",
    no_cluster_entities:
      "Geen {label}-entiteiten voor dit voertuig. Schakel de groep in via de opties van de integratie.",
    cc_title: "Check Control-meldingen",
    cc_none: "Geen Check Control-meldingen gemeld.",
    cc_teleservice: "Teleservice",
    cc_mileage: "Laatst getoond bij",
    cc_sent: "Verzonden door de auto",
    cc_code: "Code",
    cc_earlier: "Eerdere meldingen ({n})",
    cc_reported: "Laatst gemeld",
    cc_gone: "Wordt niet meer gemeld",
    // messages
    no_vehicle_title: "Geen BMW CarData-voertuig gevonden",
    no_vehicle_body:
      "Voeg de integratie toe, of stel <code>device:</code> / <code>vin:</code> in de kaartconfiguratie in.",
    // tire
    front: "VOOR",
    fl: "LV",
    fr: "RV",
    rl: "LA",
    rr: "RA",
    front_left: "Linksvoor",
    front_right: "Rechtsvoor",
    rear_left: "Linksachter",
    rear_right: "Rechtsachter",
    tires: "Banden",
    no_tire_data:
      "Nog geen bandengegevens voor dit voertuig. Schakel de groep Banden in en rij om metingen te krijgen.",
    check_pressure: "Controleer bandenspanning",
    slightly_high: "Iets te hoog",
    all_nominal: "Alles in orde",
    of_four: "{n} van 4",
    t_low: "Laag",
    t_high: "Hoog",
    t_ok: "OK",
    t_nodata: "Geen gegevens",
    t_wear: "Versleten",
    check_tyres: "Controleer banden",
    wear_due: "vervangen over",
    fitted: "gemonteerd",
    t_current: "Huidig",
    // tire summary + per-wheel fitment
    sum_pressure: "Spanning",
    sum_wear: "Slijtage",
    p_target: "Streefwaarde",
    p_target_varies: "Streefwaarden verschillen",
    w_ok: "OK",
    w_soon: "Binnenkort controleren",
    w_due: "Vervangen",
    season_summer: "Zomer",
    season_winter: "Winter",
    season_all: "All-season",
    runflat: "Runflat",
    // closures / security
    cl_closures: "Beveiliging en afsluitingen",
    closures_none:
      "Nog geen gegevens over deuren, ramen of beveiliging voor dit voertuig. Schakel de groep Voertuigstatus in via de opties van de integratie.",
    central_lock: "Centrale vergrendeling",
    alarm_word: "Alarmsysteem",
    alarm_armed: "Ingeschakeld",
    alarm_disarmed: "Uitgeschakeld",
    alarm_triggered: "Alarm afgegaan",
    secured: "Beveiligd",
    locked: "Vergrendeld",
    unlocked: "Ontgrendeld",
    partially_locked: "Gedeeltelijk vergrendeld",
    all_closed: "Alles dicht",
    windows_open: "Ramen open",
    n_open: "{n} open",
    state_open: "Open",
    state_closed: "Dicht",
    state_tilted: "Op een kier",
    door_word: "Deur",
    window_word: "Raam",
    hood_word: "Motorkap",
    trunk_word: "Kofferbak",
    rear_window_word: "Achterruit",
    sunroof_word: "Schuifdak",
    // charging history
    ch_title: "Laadgeschiedenis",
    ch_month: "Deze maand",
    ch_loading: "Laadgeschiedenis laden…",
    ch_empty:
      "Nog geen laadsessies geregistreerd. Zodra de auto laadt, verschijnen de sessies hier automatisch.",
    ch_empty_month: "Geen laadbeurten deze maand.",
    ch_error: "Laadgeschiedenis kon niet worden geladen. Herlaad de pagina en probeer het opnieuw.",
    mn_prev: "Vorige maand",
    mn_next: "Volgende maand",
    ch_home: "Thuis",
    ch_public: "Onderweg",
    ch_assumed: "aangenomen",
    ch_partial: "gedeeltelijke prijs",
    ch_session_one: "1 sessie",
    ch_session_many: "{n} sessies",
    ch_peak: "Piek",
    ch_avg: "Gem.",
    ch_grid: "Van het net",
    ch_solar: "zon",
    ch_mix: "Energiebron",
    ch_mix_pv: "PV",
    ch_mix_battery: "Thuisaccu",
    ch_mix_grid: "Net",
    ch_mix_unknown: "niet toegerekend",
    ch_duration: "Duur",
    ch_cost: "Kosten",
    ch_no_cost: "geen prijs ingesteld",
    ch_ongoing: "laden…",
    // battery health
    bh_title: "Accugezondheid",
    bh_empty:
      "Nog geen gegevens over de accugezondheid. Na een paar laadbeurten over een groot bereik verschijnt hier de bruikbare capaciteit.",
    bh_learning: "Aan het leren ({n}/{total})",
    bh_learning_hint:
      "Bruikbare capaciteit wordt geschat uit je laadbeurten. Een paar laadbeurten over een groot bereik (bijv. 20 → 80%) leren het snelst.",
    bh_suspicious:
      "Wordt vergeleken met BMW's eigen capaciteitswaarde voordat er een getal wordt getoond.",
    bh_usable: "Bruikbare capaciteit",
    bh_of_new: "{p}% van het origineel",
    bh_nominal: "Als nieuw",
    bh_analysed: "Gebaseerd op",
    bh_samples: "{n} laadbeurten",
    bh_trend_title: "Capaciteit tegen kilometerstand",
    // efficiency and real range
    ef_title: "Efficiëntie en actieradius",
    ef_loading: "Je werkelijke actieradius wordt berekend…",
    ef_empty:
      "Nog niet genoeg laadgeschiedenis. Zodra twee laadbeurten zo'n 50 km rijden omsluiten, verschijnen hier je werkelijke verbruik en de actieradius die dat oplevert.",
    ef_no_capacity:
      "Deze auto heeft zijn accucapaciteit niet gemeld, dus uit het gemeten verbruik kan geen actieradius worden berekend.",
    ef_hybrid:
      "Een plug-in hybride legt een deel van de afstand af op brandstof, en niets wat de auto stuurt zegt hoeveel. Een verbruik of actieradius uit de laadgeschiedenis zou er veel beter uitzien dan de accu werkelijk is, dus wordt er geen getoond. Accugezondheid en laadgeschiedenis blijven onveranderd.",
    ef_error: "De efficiëntiegegevens konden niet worden geladen. Herlaad de pagina en probeer het opnieuw.",
    ef_range_now: "Werkelijke actieradius nu",
    ef_at_soc: "bij {p}% lading",
    ef_range_full: "{km} km met een volle accu",
    ef_vs_bmw_over: "{p}% verder dan de auto voorspelt ({km} km)",
    ef_vs_bmw_under: "{p}% minder dan de auto voorspelt ({km} km)",
    ef_vs_bmw_same: "Gelijk aan de schatting van de auto ({km} km)",
    ef_consumption: "Gemeten verbruik",
    ef_side_battery: "uit de accu",
    ef_side_grid: "aan de stekker",
    ef_window_days: "laatste {n} dagen",
    ef_window_all: "alle gegevens",
    ef_loss: "Laadverlies",
    ef_capacity: "Bruikbare capaciteit",
    ef_capacity_measured: "gemeten",
    ef_capacity_bmw: "BMW-waarde",
    ef_cost: "Kosten per 100 km",
    ef_solar: "{p}% zon deze maand",
    ef_trend_title: "Verbruik per maand",
    ef_footnote:
      "Gemeten uit het laadlogboek: de afstand tussen twee laadbeurten en de energie die erin ging, niet de schatting van de auto.",
    // charging, battery health and efficiency on a petrol or diesel car
    ice_view_title: "Niets te tonen voor deze auto",
    ice_view_body:
      "{name} rijdt alleen op brandstof, dus er is geen laden, accugezondheid of elektrische actieradius om te tonen. Kan hij toch aan de stekker, stel dan Aandrijving in de kaarteditor in.",
    // trips
    tr_title: "Ritten",
    tr_loading: "Ritten laden…",
    tr_empty:
      "Nog geen ritten geregistreerd. Zodra er met de auto wordt gereden, verschijnen ritten hier automatisch.",
    tr_empty_month: "Geen ritten deze maand.",
    tr_error: "Ritten konden niet worden geladen. Herlaad de pagina en probeer het opnieuw.",
    tr_trip_one: "1 rit",
    tr_trip_many: "{n} ritten",
    tr_review: "Deze maand",
    tr_vs_last: "t.o.v. vorige maand",
    tr_business: "Zakelijk",
    tr_private: "Privé",
    tr_commute: "Woon-werk",
    tr_unclassified: "Niet ingedeeld",
    tr_consumption: "Gem. verbruik",
    tr_at_plug: "aan de stekker",
    tr_at_battery: "aan de accu",
    tr_charge_loss: "{n}% laadverlies",
    tr_recuperation: "Teruggewonnen",
    tr_style: "Rijstijl",
    tr_style_trend: "Rijstijl door de tijd",
    tr_top_dest: "Topbestemmingen",
    tr_est_cost: "Geschatte kosten",
    tr_longest: "Langste rit",
    tr_duration: "Duur",
    tr_distance: "Afstand",
    tr_soc_used: "Accu gebruikt",
    tr_visits: "{n}×",
    tr_auto: "auto",
    tr_classify: "Indelen",
    tr_best: "Beste",
    tr_worst: "Slechtste",
    tr_unknown_place: "Onbekend",
    tr_open: "Rit bezig",
    tr_open_hint: "Er is een rit bezig",
    tr_min: "{n} min",
    tr_open_note:
      "Deze rit is nog bezig. Afstand en verbruik zijn voorlopig, en de rit kan worden ingedeeld zodra hij eindigt.",
    // map
    mp_title: "Rittenkaart",
    mp_loading: "Kaart laden…",
    mp_error: "De kaart kon niet worden geladen. Herlaad de pagina en probeer het opnieuw.",
    mp_empty_none:
      "Nog geen plaatsen om te tonen. Zet “Route vastleggen” aan onder Configureren → Ritten en rij — de plaatsen die je bezoekt verschijnen hier.",
    mp_empty_window: "Geen plaatsen geregistreerd in deze periode.",
    mp_unavailable: "De kaart kon in deze browser niet worden geladen.",
    mp_win_month: "Deze maand",
    mp_win_3m: "3 maanden",
    mp_win_all: "Alles",
    mp_route_one: "1 route",
    mp_route_many: "{n} routes",
    // export
    ex_csv: "CSV",
    ex_report: "Rapport",
    ex_csv_hint: "Deze maand als spreadsheet downloaden",
    ex_report_hint: "Een afdrukbaar maandrapport openen (druk het af voor een PDF)",
    ex_empty: "Nog niets geregistreerd voor deze maand.",
    ex_error: "Exporteren mislukt. Bekijk het logboek van Home Assistant.",
    // editor
    ed_device: "Voertuig",
    ed_cluster: "Modus",
    ed_title: "Titel (optioneel)",
    ed_image: "Afbeeldingsentiteit",
    ed_soc: "Laadtoestand",
    ed_range: "Actieradius",
    ed_charging: "Laadstatus",
    ed_target_soc: "Laaddoel",
    ed_time_to_full: "Vol over",
    ed_odometer: "Kilometerstand",
    ed_plug: "Stekker / verbinding",
    ed_fuel: "Brandstoftank",
    ed_drivetrain: "Aandrijving",
    edh_drivetrain: "Herkend aan wat de auto streamt. Stel dit alleen in als de kaart de verkeerde indeling kiest.",
    dt_auto: "Automatisch herkennen",
    dt_bev: "Elektrisch",
    dt_phev: "Plug-in hybride",
    dt_ice: "Benzine / diesel",
    ed_steering: "Stuur",
    edh_steering: "Gelezen uit de voertuiggegevens van BMW. Stel dit alleen in als portieren en ramen aan de verkeerde kant van de auto verschijnen.",
    st_left: "Links gestuurd",
    st_right: "Rechts gestuurd",
    ed_shape: "Autovorm",
    edh_shape: "Gekozen op basis van het model in de voertuiggegevens van BMW. Stel dit alleen in als de tekening niet bij je auto past.",
    sh_sedan: "BMW-sedan",
    sh_countryman: "MINI Countryman",
    ed_overview_option: "Overzicht (standaard)",
    ed_overrides_title: "Entiteiten overschrijven (optioneel — leeg laten voor automatisch herkennen)",
    edh_cluster:
      "Het overzicht toont de voertuigafbeelding en kerncijfers. Laadgeschiedenis toont geregistreerde sessies met kosten en vermogenscurve. Ritten toont geregistreerde ritten met een maandoverzicht. De rittenkaart tekent geregistreerde routes op een kaart (vereist “Route vastleggen”). Accugezondheid toont de geleerde bruikbare capaciteit en het verloop. Een groep toont elke waarde van die groep als lijst. Een rit die bezig is verschijnt als badge op het overzicht en als live-rij boven aan Ritten.",
    edh_title: "Overschrijft de voertuignaam die op de kaart wordt getoond.",
  },
  pl: {
    // cluster labels
    cl_electric: "Pojazd elektryczny",
    cl_status: "Stan pojazdu",
    cl_metadata: "Metadane",
    cl_events: "Zdarzenia pojazdu",
    cl_tire: "Opony",
    cl_basic: "Podstawowe dane pojazdu",
    cl_usage: "Dane użytkowania",
    cl_other: "Inne",
    cl_contract: "Umowa ConnectedDrive",
    // overview
    remaining_range: "pozostały zasięg",
    charging_status: "stan ładowania",
    charge: "naładowanie",
    charging: "ładowanie",
    is_charging: "Ładowanie",
    not_charging: "Nie ładuje",
    target: "Cel",
    plug: "Wtyczka",
    fuel: "paliwo",
    tank: "Zbiornik",
    electric_range: "zasięg elektryczny",
    total_range: "Zasięg całkowity",
    time_to_full: "Pełne za",
    charge_time: "Czas ładowania",
    odometer: "Przebieg",
    last_update: "Ostatnia aktualizacja",
    spin_hint: "Przeciągnij, aby obrócić samochód",
    hs_door_open: "Drzwi otwarte",
    hs_doors_open: "Otwarte drzwi: {n}",
    hs_trunk_open: "Bagażnik otwarty",
    hs_hood_open: "Maska otwarta",
    hs_windows_open: "Okna otwarte",
    hs_charging: "Ładowanie",
    hs_interior: "Wnętrze",
    hs_exterior: "Nadwozie",
    hs_default_trim: "Pokazano standardowe wykończenie BMW",
    eq_title: "Wyposażenie",
    eq_count: "Pozycje: {n}",
    eq_empty: "Włącz Konfiguruj → Zdjęcie pojazdu → Konfigurator BMW, aby wyświetlić fabryczne wyposażenie samochodu.",
    eq_looks: "Lakier, felgi i listwy",
    eq_packages: "Pakiety",
    eq_features: "Wyposażenie",
    eq_technical: "Kody techniczne ({n})",
    just_now: "przed chwilą",
    min_ago: "{n} min temu",
    h_ago: "{n} godz. temu",
    d_ago: "{n} dn. temu",
    // cluster list
    value: "wartość",
    values: "wartości",
    no_cluster_entities:
      "Brak encji {label} dla tego pojazdu. Włącz grupę w opcjach integracji.",
    cc_title: "Komunikaty Check Control",
    cc_none: "Brak zgłoszonych komunikatów Check Control.",
    cc_teleservice: "Teleservice",
    cc_mileage: "Ostatnio pokazano przy",
    cc_sent: "Wysłane przez auto",
    cc_code: "Kod",
    cc_earlier: "Wcześniejsze komunikaty ({n})",
    cc_reported: "Ostatnio zgłoszone",
    cc_gone: "Już niezgłaszane",
    // messages
    no_vehicle_title: "Nie znaleziono pojazdu BMW CarData",
    no_vehicle_body:
      "Dodaj integrację lub ustaw <code>device:</code> / <code>vin:</code> w konfiguracji karty.",
    // tire
    front: "PRZÓD",
    fl: "LP",
    fr: "PP",
    rl: "LT",
    rr: "PT",
    front_left: "Lewa przednia",
    front_right: "Prawa przednia",
    rear_left: "Lewa tylna",
    rear_right: "Prawa tylna",
    tires: "Opony",
    no_tire_data:
      "Brak jeszcze danych o oponach dla tego pojazdu. Włącz grupę Opony i jedź, aby uzyskać odczyty.",
    check_pressure: "Sprawdź ciśnienie",
    slightly_high: "Nieco za wysokie",
    all_nominal: "Wszystko w normie",
    of_four: "{n} z 4",
    t_low: "Niskie",
    t_high: "Wysokie",
    t_ok: "OK",
    t_nodata: "Brak danych",
    t_wear: "Zużyta",
    check_tyres: "Sprawdź opony",
    wear_due: "wymiana za",
    fitted: "założona",
    t_current: "Obecne",
    // tire summary + per-wheel fitment
    sum_pressure: "Ciśnienie",
    sum_wear: "Zużycie",
    p_target: "Zadane",
    p_target_varies: "Różne wartości zadane",
    w_ok: "OK",
    w_soon: "Sprawdź wkrótce",
    w_due: "Wymień",
    season_summer: "Letnia",
    season_winter: "Zimowa",
    season_all: "Całoroczna",
    runflat: "Runflat",
    // closures / security
    cl_closures: "Bezpieczeństwo i zamknięcia",
    closures_none:
      "Brak jeszcze danych o drzwiach, szybach lub zabezpieczeniach dla tego pojazdu. Włącz grupę Stan pojazdu w opcjach integracji.",
    central_lock: "Zamek centralny",
    alarm_word: "Alarm antykradzieżowy",
    alarm_armed: "Uzbrojony",
    alarm_disarmed: "Rozbrojony",
    alarm_triggered: "Alarm włączony",
    secured: "Zabezpieczony",
    locked: "Zablokowany",
    unlocked: "Odblokowany",
    partially_locked: "Częściowo zablokowany",
    all_closed: "Wszystko zamknięte",
    windows_open: "Szyby otwarte",
    n_open: "Otwarte: {n}",
    state_open: "Otwarte",
    state_closed: "Zamknięte",
    state_tilted: "Uchylone",
    door_word: "Drzwi",
    window_word: "Szyba",
    hood_word: "Maska",
    trunk_word: "Bagażnik",
    rear_window_word: "Tylna szyba",
    sunroof_word: "Szyberdach",
    // charging history
    ch_title: "Historia ładowania",
    ch_month: "Ten miesiąc",
    ch_loading: "Wczytywanie historii ładowania…",
    ch_empty:
      "Brak jeszcze zarejestrowanych sesji ładowania. Gdy auto zacznie się ładować, sesje pojawią się tu automatycznie.",
    ch_empty_month: "Brak ładowań w tym miesiącu.",
    ch_error: "Nie udało się wczytać historii ładowania. Odśwież stronę i spróbuj ponownie.",
    mn_prev: "Poprzedni miesiąc",
    mn_next: "Następny miesiąc",
    ch_home: "Dom",
    ch_public: "Poza domem",
    ch_assumed: "założono",
    ch_partial: "cena częściowa",
    ch_session_one: "1 sesja",
    ch_session_many: "Sesje: {n}",
    ch_peak: "Szczyt",
    ch_avg: "Śr.",
    ch_grid: "Z sieci",
    ch_solar: "słońce",
    ch_mix: "Źródło energii",
    ch_mix_pv: "PV",
    ch_mix_battery: "Magazyn domowy",
    ch_mix_grid: "Sieć",
    ch_mix_unknown: "nieprzypisane",
    ch_duration: "Czas",
    ch_cost: "Koszt",
    ch_no_cost: "brak ustawionej ceny",
    ch_ongoing: "ładowanie…",
    // battery health
    bh_title: "Kondycja akumulatora",
    bh_empty:
      "Brak jeszcze danych o kondycji akumulatora. Po kilku ładowaniach w szerokim zakresie pojawi się tu jego pojemność użytkowa.",
    bh_learning: "Uczenie ({n}/{total})",
    bh_learning_hint:
      "Szacowanie pojemności użytkowej na podstawie ładowań. Kilka ładowań w szerokim zakresie (np. 20 → 80%) uczy najszybciej.",
    bh_suspicious:
      "Porównywanie z wartością pojemności podaną przez BMW przed pokazaniem liczby.",
    bh_usable: "Pojemność użytkowa",
    bh_of_new: "{p}% pierwotnej",
    bh_nominal: "Jak nowy",
    bh_analysed: "Na podstawie",
    bh_samples: "Ładowania: {n}",
    bh_trend_title: "Pojemność a przebieg",
    // efficiency and real range
    ef_title: "Wydajność i zasięg",
    ef_loading: "Obliczanie rzeczywistego zasięgu…",
    ef_empty:
      "Za mało historii ładowania. Gdy dwa ładowania obejmą około 50 km jazdy, pojawi się tu Twoje rzeczywiste zużycie i zasięg, jaki daje.",
    ef_no_capacity:
      "To auto nie podało pojemności akumulatora, więc nie da się obliczyć zasięgu na podstawie zmierzonego zużycia.",
    ef_hybrid:
      "Hybryda plug-in pokonuje część dystansu na paliwie, a nic z tego, co wysyła auto, nie mówi, jaką. Zużycie lub zasięg obliczone z historii ładowania wyglądałyby znacznie lepiej, niż akumulator faktycznie działa, dlatego nic nie jest pokazywane. Kondycja akumulatora i historia ładowania nie są tym objęte.",
    ef_error: "Nie udało się wczytać danych o wydajności. Odśwież stronę i spróbuj ponownie.",
    ef_range_now: "Rzeczywisty zasięg teraz",
    ef_at_soc: "przy {p}% naładowania",
    ef_range_full: "{km} km na pełnym akumulatorze",
    ef_vs_bmw_over: "{p}% dalej, niż przewiduje auto ({km} km)",
    ef_vs_bmw_under: "{p}% mniej, niż przewiduje auto ({km} km)",
    ef_vs_bmw_same: "Tyle samo co szacunek auta ({km} km)",
    ef_consumption: "Zmierzone zużycie",
    ef_side_battery: "z akumulatora",
    ef_side_grid: "przy gniazdku",
    ef_window_days: "ostatnie {n} dni",
    ef_window_all: "wszystkie dane",
    ef_loss: "Straty ładowania",
    ef_capacity: "Pojemność użytkowa",
    ef_capacity_measured: "zmierzona",
    ef_capacity_bmw: "wartość BMW",
    ef_cost: "Koszt na 100 km",
    ef_solar: "{p}% ze słońca w tym miesiącu",
    ef_trend_title: "Zużycie w poszczególnych miesiącach",
    ef_footnote:
      "Zmierzone z rejestru ładowań: dystans między dwoma ładowaniami i energia, która trafiła do akumulatora, a nie szacunek auta.",
    // charging, battery health and efficiency on a petrol or diesel car
    ice_view_title: "Nic do pokazania dla tego auta",
    ice_view_body:
      "{name} jeździ wyłącznie na paliwie, więc nie ma ładowania, kondycji akumulatora ani zasięgu elektrycznego do pokazania. Jeśli jednak się ładuje, ustaw Napęd w edytorze karty.",
    // trips
    tr_title: "Przejazdy",
    tr_loading: "Wczytywanie przejazdów…",
    tr_empty:
      "Brak jeszcze zarejestrowanych przejazdów. Gdy auto zacznie jeździć, przejazdy pojawią się tu automatycznie.",
    tr_empty_month: "Brak przejazdów w tym miesiącu.",
    tr_error: "Nie udało się wczytać przejazdów. Odśwież stronę i spróbuj ponownie.",
    tr_trip_one: "1 przejazd",
    tr_trip_many: "Przejazdy: {n}",
    tr_review: "Ten miesiąc",
    tr_vs_last: "w porównaniu z poprzednim miesiącem",
    tr_business: "Służbowy",
    tr_private: "Prywatny",
    tr_commute: "Dojazd",
    tr_unclassified: "Niesklasyfikowany",
    tr_consumption: "Śr. zużycie",
    tr_at_plug: "przy gniazdku",
    tr_at_battery: "przy akumulatorze",
    tr_charge_loss: "{n}% strat ładowania",
    tr_recuperation: "Odzyskano",
    tr_style: "Styl jazdy",
    tr_style_trend: "Styl jazdy w czasie",
    tr_top_dest: "Najczęstsze cele",
    tr_est_cost: "Szac. koszt",
    tr_longest: "Najdłuższy przejazd",
    tr_duration: "Czas",
    tr_distance: "Dystans",
    tr_soc_used: "Zużyty akumulator",
    tr_visits: "{n}×",
    tr_auto: "auto",
    tr_classify: "Klasyfikuj",
    tr_best: "Najlepszy",
    tr_worst: "Najgorszy",
    tr_unknown_place: "Nieznane",
    tr_open: "Przejazd w toku",
    tr_open_hint: "Trwa przejazd",
    tr_min: "{n} min",
    tr_open_note:
      "Ten przejazd wciąż trwa. Dystans i zużycie są wstępne, a przejazd można sklasyfikować po jego zakończeniu.",
    // map
    mp_title: "Mapa przejazdów",
    mp_loading: "Wczytywanie mapy…",
    mp_error: "Nie udało się wczytać mapy. Odśwież stronę i spróbuj ponownie.",
    mp_empty_none:
      "Brak jeszcze miejsc do pokazania. Włącz „Zapis trasy” w Konfiguruj → Przejazdy i jedź — odwiedzone miejsca pojawią się tutaj.",
    mp_empty_window: "Brak zarejestrowanych miejsc w tym okresie.",
    mp_unavailable: "Nie udało się wczytać mapy w tej przeglądarce.",
    mp_win_month: "Ten miesiąc",
    mp_win_3m: "3 miesiące",
    mp_win_all: "Wszystko",
    mp_route_one: "1 trasa",
    mp_route_many: "Trasy: {n}",
    // export
    ex_csv: "CSV",
    ex_report: "Raport",
    ex_csv_hint: "Pobierz ten miesiąc jako arkusz kalkulacyjny",
    ex_report_hint: "Otwórz raport miesięczny do druku (wydrukuj go, aby uzyskać PDF)",
    ex_empty: "Nic jeszcze nie zarejestrowano w tym miesiącu.",
    ex_error: "Eksport nie powiódł się. Sprawdź dziennik Home Assistant.",
    // editor
    ed_device: "Pojazd",
    ed_cluster: "Tryb",
    ed_title: "Tytuł (opcjonalnie)",
    ed_image: "Encja obrazu",
    ed_soc: "Stan naładowania",
    ed_range: "Zasięg",
    ed_charging: "Stan ładowania",
    ed_target_soc: "Cel ładowania",
    ed_time_to_full: "Pełne za",
    ed_odometer: "Przebieg",
    ed_plug: "Wtyczka / połączenie",
    ed_fuel: "Zbiornik paliwa",
    ed_drivetrain: "Napęd",
    edh_drivetrain: "Wykrywany na podstawie tego, co przesyła auto. Ustaw tylko wtedy, gdy karta wybiera zły układ.",
    dt_auto: "Wykryj automatycznie",
    dt_bev: "Elektryczny",
    dt_phev: "Hybryda plug-in",
    dt_ice: "Benzyna / diesel",
    ed_steering: "Kierownica",
    edh_steering: "Odczytywana z danych pojazdu BMW. Ustaw tylko wtedy, gdy drzwi i okna pojawiają się po złej stronie auta.",
    st_left: "Kierownica po lewej",
    st_right: "Kierownica po prawej",
    ed_shape: "Kształt auta",
    edh_shape: "Wybierany na podstawie modelu z danych pojazdu BMW. Ustaw tylko wtedy, gdy rysunek nie pasuje do auta.",
    sh_sedan: "Sedan BMW",
    sh_countryman: "MINI Countryman",
    ed_overview_option: "Przegląd (domyślnie)",
    ed_overrides_title: "Nadpisanie encji (opcjonalnie — pozostaw puste dla automatycznego wykrywania)",
    edh_cluster:
      "Przegląd pokazuje zdjęcie pojazdu i kluczowe wartości. Historia ładowania wymienia zarejestrowane sesje z kosztem i krzywą mocy. Przejazdy wymieniają zarejestrowane przejazdy z podsumowaniem miesiąca. Mapa przejazdów rysuje zarejestrowane trasy na mapie (wymaga włączonego „Zapisu trasy”). Kondycja akumulatora pokazuje wyuczoną pojemność użytkową i jej trend. Grupa pokazuje wszystkie wartości tej grupy w postaci listy. Trwający przejazd pojawia się jako plakietka w przeglądzie i jako wiersz na żywo na górze Przejazdów.",
    edh_title: "Nadpisuje nazwę pojazdu wyświetlaną na karcie.",
  },
  pt: {
    // cluster labels
    cl_electric: "Veículo elétrico",
    cl_status: "Estado do veículo",
    cl_metadata: "Metadados",
    cl_events: "Eventos do veículo",
    cl_tire: "Pneus",
    cl_basic: "Dados básicos do veículo",
    cl_usage: "Dados de utilização",
    cl_other: "Outros",
    cl_contract: "Contrato ConnectedDrive",
    // overview
    remaining_range: "autonomia restante",
    charging_status: "estado do carregamento",
    charge: "carga",
    charging: "a carregar",
    is_charging: "A carregar",
    not_charging: "Sem carregar",
    target: "Objetivo",
    plug: "Ficha",
    fuel: "combustível",
    tank: "Depósito",
    electric_range: "autonomia elétrica",
    total_range: "Autonomia total",
    time_to_full: "Carga completa em",
    charge_time: "Tempo de carregamento",
    odometer: "Conta-quilómetros",
    last_update: "Última atualização",
    spin_hint: "Arraste para rodar o carro",
    hs_door_open: "Porta aberta",
    hs_doors_open: "{n} portas abertas",
    hs_trunk_open: "Bagageira aberta",
    hs_hood_open: "Capô aberto",
    hs_windows_open: "Janelas abertas",
    hs_charging: "A carregar",
    hs_interior: "Interior",
    hs_exterior: "Exterior",
    hs_default_trim: "Mostrado o acabamento BMW de série",
    eq_title: "Equipamento",
    eq_count: "{n} itens",
    eq_empty: "Ative Configurar → Imagem do veículo → Configurador BMW para listar o equipamento de fábrica do carro.",
    eq_looks: "Pintura, jantes e frisos",
    eq_packages: "Pacotes",
    eq_features: "Equipamento",
    eq_technical: "Códigos técnicos ({n})",
    just_now: "agora mesmo",
    min_ago: "há {n} min",
    h_ago: "há {n} h",
    d_ago: "há {n} d",
    // cluster list
    value: "valor",
    values: "valores",
    no_cluster_entities:
      "Não há entidades de {label} para este veículo. Ative o grupo nas opções da integração.",
    cc_title: "Mensagens Check Control",
    cc_none: "Nenhuma mensagem Check Control comunicada.",
    cc_teleservice: "Teleservice",
    cc_mileage: "Mostrada pela última vez aos",
    cc_sent: "Enviada pelo carro",
    cc_code: "Código",
    cc_earlier: "Mensagens anteriores ({n})",
    cc_reported: "Última comunicação",
    cc_gone: "Já não é comunicada",
    // messages
    no_vehicle_title: "Nenhum veículo BMW CarData encontrado",
    no_vehicle_body:
      "Adicione a integração, ou defina <code>device:</code> / <code>vin:</code> na configuração do cartão.",
    // tire
    front: "FRENTE",
    fl: "FE",
    fr: "FD",
    rl: "TE",
    rr: "TD",
    front_left: "Dianteiro esquerdo",
    front_right: "Dianteiro direito",
    rear_left: "Traseiro esquerdo",
    rear_right: "Traseiro direito",
    tires: "Pneus",
    no_tire_data:
      "Ainda não há dados dos pneus para este veículo. Ative o grupo Pneus e conduza para obter leituras.",
    check_pressure: "Verificar pressão",
    slightly_high: "Ligeiramente alta",
    all_nominal: "Tudo normal",
    of_four: "{n} de 4",
    t_low: "Baixa",
    t_high: "Alta",
    t_ok: "OK",
    t_nodata: "Sem dados",
    t_wear: "Gasto",
    check_tyres: "Verificar pneus",
    wear_due: "trocar em",
    fitted: "montado",
    t_current: "Atual",
    // tire summary + per-wheel fitment
    sum_pressure: "Pressão",
    sum_wear: "Desgaste",
    p_target: "Nominal",
    p_target_varies: "Nominais diferentes",
    w_ok: "OK",
    w_soon: "Verificar em breve",
    w_due: "Substituir",
    season_summer: "Verão",
    season_winter: "Inverno",
    season_all: "Todas as estações",
    runflat: "Runflat",
    // closures / security
    cl_closures: "Segurança e aberturas",
    closures_none:
      "Ainda não há dados de portas, vidros ou segurança para este veículo. Ative o grupo Estado do veículo nas opções da integração.",
    central_lock: "Fecho centralizado",
    alarm_word: "Alarme antirroubo",
    alarm_armed: "Ativado",
    alarm_disarmed: "Desativado",
    alarm_triggered: "Alarme disparado",
    secured: "Protegido",
    locked: "Trancado",
    unlocked: "Destrancado",
    partially_locked: "Parcialmente trancado",
    all_closed: "Tudo fechado",
    windows_open: "Vidros abertos",
    n_open: "{n} abertos",
    state_open: "Aberto",
    state_closed: "Fechado",
    state_tilted: "Entreaberto",
    door_word: "Porta",
    window_word: "Vidro",
    hood_word: "Capô",
    trunk_word: "Bagageira",
    rear_window_word: "Óculo traseiro",
    sunroof_word: "Teto de abrir",
    // charging history
    ch_title: "Histórico de carregamento",
    ch_month: "Este mês",
    ch_loading: "A carregar o histórico de carregamento…",
    ch_empty:
      "Ainda não há sessões de carregamento registadas. Assim que o carro carregar, as sessões aparecem aqui automaticamente.",
    ch_empty_month: "Nenhum carregamento neste mês.",
    ch_error: "Não foi possível carregar o histórico de carregamento. Recarregue a página e tente novamente.",
    mn_prev: "Mês anterior",
    mn_next: "Mês seguinte",
    ch_home: "Casa",
    ch_public: "Fora",
    ch_assumed: "presumido",
    ch_partial: "preço parcial",
    ch_session_one: "1 sessão",
    ch_session_many: "{n} sessões",
    ch_peak: "Pico",
    ch_avg: "Média",
    ch_grid: "Da rede",
    ch_solar: "solar",
    ch_mix: "Fonte de energia",
    ch_mix_pv: "FV",
    ch_mix_battery: "Bateria doméstica",
    ch_mix_grid: "Rede",
    ch_mix_unknown: "não atribuído",
    ch_duration: "Duração",
    ch_cost: "Custo",
    ch_no_cost: "sem preço definido",
    ch_ongoing: "a carregar…",
    // battery health
    bh_title: "Saúde da bateria",
    bh_empty:
      "Ainda não há dados de saúde da bateria. Após alguns carregamentos de amplo intervalo, a sua capacidade útil aparece aqui.",
    bh_learning: "A aprender ({n}/{total})",
    bh_learning_hint:
      "A estimar a capacidade útil a partir dos seus carregamentos. Alguns carregamentos de amplo intervalo (p. ex. 20 → 80%) ensinam mais depressa.",
    bh_suspicious:
      "A comparar com o valor de capacidade da BMW antes de mostrar um número.",
    bh_usable: "Capacidade útil",
    bh_of_new: "{p}% da original",
    bh_nominal: "Como nova",
    bh_analysed: "Com base em",
    bh_samples: "{n} carregamentos",
    bh_trend_title: "Capacidade vs quilometragem",
    // efficiency and real range
    ef_title: "Eficiência e autonomia",
    ef_loading: "A calcular a sua autonomia real…",
    ef_empty:
      "Ainda não há histórico de carregamento suficiente. Quando dois carregamentos abrangerem cerca de 50 km de condução, o seu consumo real e a autonomia que permite aparecem aqui.",
    ef_no_capacity:
      "Este carro não comunicou a capacidade da bateria, por isso não é possível calcular uma autonomia a partir do consumo medido.",
    ef_hybrid:
      "Um híbrido plug-in percorre parte da distância a combustível, e nada do que o carro envia diz quanto. Um consumo ou uma autonomia calculados a partir do histórico de carregamento pareceriam muito melhores do que a bateria realmente é, por isso nada é mostrado. A saúde da bateria e o histórico de carregamento não são afetados.",
    ef_error: "Não foi possível carregar os dados de eficiência. Recarregue a página e tente novamente.",
    ef_range_now: "Autonomia real agora",
    ef_at_soc: "com {p}% de carga",
    ef_range_full: "{km} km com a bateria cheia",
    ef_vs_bmw_over: "{p}% mais do que o carro prevê ({km} km)",
    ef_vs_bmw_under: "{p}% menos do que o carro prevê ({km} km)",
    ef_vs_bmw_same: "Igual à estimativa do carro ({km} km)",
    ef_consumption: "Consumo medido",
    ef_side_battery: "a partir da bateria",
    ef_side_grid: "na tomada",
    ef_window_days: "últimos {n} dias",
    ef_window_all: "todos os registos",
    ef_loss: "Perdas de carregamento",
    ef_capacity: "Capacidade útil",
    ef_capacity_measured: "medida",
    ef_capacity_bmw: "valor BMW",
    ef_cost: "Custo por 100 km",
    ef_solar: "{p}% solar este mês",
    ef_trend_title: "Consumo por mês",
    ef_footnote:
      "Medido a partir do registo de carregamentos: a distância entre dois carregamentos e a energia que entrou, não a estimativa do carro.",
    // charging, battery health and efficiency on a petrol or diesel car
    ice_view_title: "Nada para mostrar neste carro",
    ice_view_body:
      "{name} funciona apenas a combustível, por isso não há carregamento, saúde da bateria nem autonomia elétrica para mostrar. Se for ligado à corrente, defina a Motorização no editor do cartão.",
    // trips
    tr_title: "Viagens",
    tr_loading: "A carregar viagens…",
    tr_empty:
      "Ainda não há viagens registadas. Assim que o carro for conduzido, as viagens aparecem aqui automaticamente.",
    tr_empty_month: "Nenhuma viagem neste mês.",
    tr_error: "Não foi possível carregar as viagens. Recarregue a página e tente novamente.",
    tr_trip_one: "1 viagem",
    tr_trip_many: "{n} viagens",
    tr_review: "Este mês",
    tr_vs_last: "vs mês anterior",
    tr_business: "Profissional",
    tr_private: "Particular",
    tr_commute: "Casa-trabalho",
    tr_unclassified: "Não classificada",
    tr_consumption: "Consumo médio",
    tr_at_plug: "na tomada",
    tr_at_battery: "na bateria",
    tr_charge_loss: "{n}% de perdas de carregamento",
    tr_recuperation: "Recuperado",
    tr_style: "Estilo de condução",
    tr_style_trend: "Estilo ao longo do tempo",
    tr_top_dest: "Destinos principais",
    tr_est_cost: "Custo est.",
    tr_longest: "Viagem mais longa",
    tr_duration: "Duração",
    tr_distance: "Distância",
    tr_soc_used: "Bateria usada",
    tr_visits: "{n}×",
    tr_auto: "auto",
    tr_classify: "Classificar",
    tr_best: "Melhor",
    tr_worst: "Pior",
    tr_unknown_place: "Desconhecido",
    tr_open: "Viagem em curso",
    tr_open_hint: "Há uma viagem em curso",
    tr_min: "{n} min",
    tr_open_note:
      "Esta viagem ainda está em curso. A distância e o consumo são provisórios, e a viagem pode ser classificada quando terminar.",
    // map
    mp_title: "Mapa de viagens",
    mp_loading: "A carregar o mapa…",
    mp_error: "Não foi possível carregar o mapa. Recarregue a página e tente novamente.",
    mp_empty_none:
      "Ainda não há locais para mostrar. Ative “Gravar percurso” em Configurar → Viagens e conduza — os locais que visitar aparecem aqui.",
    mp_empty_window: "Nenhum local registado neste período.",
    mp_unavailable: "Não foi possível carregar o mapa neste navegador.",
    mp_win_month: "Este mês",
    mp_win_3m: "3 meses",
    mp_win_all: "Tudo",
    mp_route_one: "1 percurso",
    mp_route_many: "{n} percursos",
    // export
    ex_csv: "CSV",
    ex_report: "Relatório",
    ex_csv_hint: "Transferir este mês como folha de cálculo",
    ex_report_hint: "Abrir um relatório mensal imprimível (imprima-o para obter um PDF)",
    ex_empty: "Ainda não há nada registado neste mês.",
    ex_error: "A exportação falhou. Consulte o registo do Home Assistant.",
    // editor
    ed_device: "Veículo",
    ed_cluster: "Modo",
    ed_title: "Título (opcional)",
    ed_image: "Entidade de imagem",
    ed_soc: "Estado de carga",
    ed_range: "Autonomia",
    ed_charging: "Estado do carregamento",
    ed_target_soc: "Objetivo de carga",
    ed_time_to_full: "Carga completa em",
    ed_odometer: "Conta-quilómetros",
    ed_plug: "Ficha / ligação",
    ed_fuel: "Depósito de combustível",
    ed_drivetrain: "Motorização",
    edh_drivetrain: "Detetada a partir do que o carro transmite. Defina-a apenas se o cartão escolher o esquema errado.",
    dt_auto: "Deteção automática",
    dt_bev: "Elétrico",
    dt_phev: "Híbrido plug-in",
    dt_ice: "Gasolina / gasóleo",
    ed_steering: "Volante",
    edh_steering: "Lido a partir dos dados do veículo da BMW. Defina-o apenas se as portas e as janelas aparecerem do lado errado do carro.",
    st_left: "Volante à esquerda",
    st_right: "Volante à direita",
    ed_shape: "Forma do carro",
    edh_shape: "Escolhida a partir do modelo nos dados do veículo da BMW. Defina-a apenas se o desenho não corresponder ao seu carro.",
    sh_sedan: "Berlina BMW",
    sh_countryman: "MINI Countryman",
    ed_overview_option: "Visão geral (predefinição)",
    ed_overrides_title: "Substituição de entidades (opcional — deixe vazio para deteção automática)",
    edh_cluster:
      "A visão geral mostra a imagem do veículo e os valores principais. O histórico de carregamento lista as sessões registadas com custo e curva de potência. Viagens lista as viagens registadas com um resumo mensal. O mapa de viagens desenha os percursos registados num mapa (requer “Gravar percurso” ativado). A saúde da bateria mostra a capacidade útil aprendida e a sua evolução. Um grupo mostra todos os valores desse grupo como lista. Uma viagem em curso aparece como um distintivo na visão geral e como uma linha em direto no topo de Viagens.",
    edh_title: "Substitui o nome do veículo mostrado no cartão.",
  },
  cs: {
    // cluster labels
    cl_electric: "Elektrické vozidlo",
    cl_status: "Stav vozidla",
    cl_metadata: "Metadata",
    cl_events: "Události vozidla",
    cl_tire: "Pneumatiky",
    cl_basic: "Základní údaje o vozidle",
    cl_usage: "Údaje o používání",
    cl_other: "Ostatní",
    cl_contract: "Smlouva ConnectedDrive",
    // overview
    remaining_range: "zbývající dojezd",
    charging_status: "stav nabíjení",
    charge: "nabití",
    charging: "nabíjení",
    is_charging: "Nabíjí se",
    not_charging: "Nenabíjí se",
    target: "Cíl",
    plug: "Konektor",
    fuel: "palivo",
    tank: "Nádrž",
    electric_range: "elektrický dojezd",
    total_range: "Celkový dojezd",
    time_to_full: "Plně nabito za",
    charge_time: "Doba nabíjení",
    odometer: "Počet km",
    last_update: "Poslední aktualizace",
    spin_hint: "Tažením auto otočíte",
    hs_door_open: "Dveře otevřené",
    hs_doors_open: "Otevřené dveře: {n}",
    hs_trunk_open: "Kufr otevřený",
    hs_hood_open: "Kapota otevřená",
    hs_windows_open: "Okna otevřená",
    hs_charging: "Nabíjí se",
    hs_interior: "Interiér",
    hs_exterior: "Exteriér",
    hs_default_trim: "Zobrazeno výchozí čalounění BMW",
    eq_title: "Výbava",
    eq_count: "Položek: {n}",
    eq_empty: "Zapněte Konfigurovat → Obrázek vozidla → Konfigurátor BMW a zobrazí se tovární výbava auta.",
    eq_looks: "Lak, kola a lišty",
    eq_packages: "Pakety",
    eq_features: "Výbava",
    eq_technical: "Technické kódy ({n})",
    just_now: "právě teď",
    min_ago: "před {n} min",
    h_ago: "před {n} h",
    d_ago: "před {n} d",
    // cluster list
    value: "hodnota",
    values: "hodnot",
    no_cluster_entities:
      "Pro toto vozidlo nejsou žádné entity skupiny {label}. Zapněte skupinu v možnostech integrace.",
    cc_title: "Hlášení Check Control",
    cc_none: "Nebyla nahlášena žádná hlášení Check Control.",
    cc_teleservice: "Teleservice",
    cc_mileage: "Naposledy zobrazeno při",
    cc_sent: "Odesláno vozem",
    cc_code: "Kód",
    cc_earlier: "Starší hlášení ({n})",
    cc_reported: "Naposledy hlášeno",
    cc_gone: "Již se nehlásí",
    // messages
    no_vehicle_title: "Nebylo nalezeno žádné vozidlo BMW CarData",
    no_vehicle_body:
      "Přidejte integraci, nebo v konfiguraci karty nastavte <code>device:</code> / <code>vin:</code>.",
    // tire
    front: "PŘEDEK",
    fl: "LP",
    fr: "PP",
    rl: "LZ",
    rr: "PZ",
    front_left: "Levá přední",
    front_right: "Pravá přední",
    rear_left: "Levá zadní",
    rear_right: "Pravá zadní",
    tires: "Pneumatiky",
    no_tire_data:
      "Pro toto vozidlo zatím nejsou žádná data o pneumatikách. Zapněte skupinu Pneumatiky a jeďte, abyste získali hodnoty.",
    check_pressure: "Zkontrolujte tlak",
    slightly_high: "Mírně vysoký",
    all_nominal: "Vše v pořádku",
    of_four: "{n} ze 4",
    t_low: "Nízký",
    t_high: "Vysoký",
    t_ok: "OK",
    t_nodata: "Žádná data",
    t_wear: "Opotřebená",
    check_tyres: "Zkontrolujte pneumatiky",
    wear_due: "výměna za",
    fitted: "nasazena",
    t_current: "Aktuální",
    // tire summary + per-wheel fitment
    sum_pressure: "Tlak",
    sum_wear: "Opotřebení",
    p_target: "Cílový",
    p_target_varies: "Cílové tlaky se liší",
    w_ok: "OK",
    w_soon: "Brzy zkontrolovat",
    w_due: "Vyměnit",
    season_summer: "Letní",
    season_winter: "Zimní",
    season_all: "Celoroční",
    runflat: "Runflat",
    // closures / security
    cl_closures: "Zabezpečení a otvory",
    closures_none:
      "Pro toto vozidlo zatím nejsou žádná data o dveřích, oknech ani zabezpečení. Zapněte skupinu Stav vozidla v možnostech integrace.",
    central_lock: "Centrální zamykání",
    alarm_word: "Alarm",
    alarm_armed: "Zapnutý",
    alarm_disarmed: "Vypnutý",
    alarm_triggered: "Alarm spuštěn",
    secured: "Zajištěno",
    locked: "Zamčeno",
    unlocked: "Odemčeno",
    partially_locked: "Částečně zamčeno",
    all_closed: "Vše zavřeno",
    windows_open: "Okna otevřena",
    n_open: "Otevřeno: {n}",
    state_open: "Otevřeno",
    state_closed: "Zavřeno",
    state_tilted: "Pootevřeno",
    door_word: "Dveře",
    window_word: "Okno",
    hood_word: "Kapota",
    trunk_word: "Kufr",
    rear_window_word: "Zadní okno",
    sunroof_word: "Střešní okno",
    // charging history
    ch_title: "Historie nabíjení",
    ch_month: "Tento měsíc",
    ch_loading: "Načítání historie nabíjení…",
    ch_empty:
      "Zatím nejsou zaznamenány žádné relace nabíjení. Jakmile se vůz nabije, relace se zde objeví automaticky.",
    ch_empty_month: "V tomto měsíci žádné nabíjení.",
    ch_error: "Historii nabíjení se nepodařilo načíst. Obnovte stránku a zkuste to znovu.",
    mn_prev: "Předchozí měsíc",
    mn_next: "Další měsíc",
    ch_home: "Doma",
    ch_public: "Venku",
    ch_assumed: "předpokládáno",
    ch_partial: "částečná cena",
    ch_session_one: "1 relace",
    ch_session_many: "Relace: {n}",
    ch_peak: "Špička",
    ch_avg: "Prům.",
    ch_grid: "Ze sítě",
    ch_solar: "solár",
    ch_mix: "Zdroj energie",
    ch_mix_pv: "FV",
    ch_mix_battery: "Domácí baterie",
    ch_mix_grid: "Síť",
    ch_mix_unknown: "nepřiřazeno",
    ch_duration: "Doba",
    ch_cost: "Náklady",
    ch_no_cost: "cena není nastavena",
    ch_ongoing: "nabíjí se…",
    // battery health
    bh_title: "Stav baterie",
    bh_empty:
      "Zatím nejsou žádná data o stavu baterie. Po několika nabitích v širokém rozsahu se zde objeví její využitelná kapacita.",
    bh_learning: "Učení ({n}/{total})",
    bh_learning_hint:
      "Odhad využitelné kapacity z vašich nabíjení. Nejrychleji pomůže několik nabití v širokém rozsahu (např. 20 → 80 %).",
    bh_suspicious:
      "Před zobrazením čísla probíhá porovnání s hodnotou kapacity od BMW.",
    bh_usable: "Využitelná kapacita",
    bh_of_new: "{p} % původní",
    bh_nominal: "Jako nová",
    bh_analysed: "Na základě",
    bh_samples: "Nabití: {n}",
    bh_trend_title: "Kapacita vs. nájezd",
    // efficiency and real range
    ef_title: "Účinnost a dojezd",
    ef_loading: "Výpočet skutečného dojezdu…",
    ef_empty:
      "Zatím není dost historie nabíjení. Jakmile dvě nabití ohraničí zhruba 50 km jízdy, objeví se zde vaše skutečná spotřeba a dojezd, který z ní plyne.",
    ef_no_capacity:
      "Tento vůz nehlásí kapacitu baterie, takže z naměřené spotřeby nelze dojezd vypočítat.",
    ef_hybrid:
      "Plug-in hybrid ujede část vzdálenosti na palivo a nic z toho, co vůz odesílá, neříká jakou. Spotřeba nebo dojezd vypočtené z historie nabíjení by vypadaly mnohem lépe, než baterie ve skutečnosti je, proto se nezobrazují. Stav baterie a historie nabíjení tím nejsou dotčeny.",
    ef_error: "Data o účinnosti se nepodařilo načíst. Obnovte stránku a zkuste to znovu.",
    ef_range_now: "Skutečný dojezd nyní",
    ef_at_soc: "při {p} % nabití",
    ef_range_full: "{km} km na plnou baterii",
    ef_vs_bmw_over: "O {p} % dál, než předpovídá vůz ({km} km)",
    ef_vs_bmw_under: "O {p} % méně, než předpovídá vůz ({km} km)",
    ef_vs_bmw_same: "Stejně jako odhad vozu ({km} km)",
    ef_consumption: "Naměřená spotřeba",
    ef_side_battery: "z baterie",
    ef_side_grid: "ze zásuvky",
    ef_window_days: "posledních {n} dní",
    ef_window_all: "všechny záznamy",
    ef_loss: "Ztráty nabíjení",
    ef_capacity: "Využitelná kapacita",
    ef_capacity_measured: "naměřeno",
    ef_capacity_bmw: "hodnota BMW",
    ef_cost: "Náklady na 100 km",
    ef_solar: "{p} % ze soláru tento měsíc",
    ef_trend_title: "Spotřeba po měsících",
    ef_footnote:
      "Naměřeno z knihy nabíjení: vzdálenost mezi dvěma nabitími a energie, která byla dobita, nikoli odhad vozu.",
    // charging, battery health and efficiency on a petrol or diesel car
    ice_view_title: "Pro tento vůz není co zobrazit",
    ice_view_body:
      "{name} jezdí jen na palivo, takže není co zobrazit o nabíjení, stavu baterie ani elektrickém dojezdu. Pokud se přesto nabíjí, nastavte Pohon v editoru karty.",
    // trips
    tr_title: "Jízdy",
    tr_loading: "Načítání jízd…",
    tr_empty:
      "Zatím nejsou zaznamenány žádné jízdy. Jakmile se s vozem pojede, jízdy se zde objeví automaticky.",
    tr_empty_month: "V tomto měsíci žádné jízdy.",
    tr_error: "Jízdy se nepodařilo načíst. Obnovte stránku a zkuste to znovu.",
    tr_trip_one: "1 jízda",
    tr_trip_many: "Jízdy: {n}",
    tr_review: "Tento měsíc",
    tr_vs_last: "oproti minulému měsíci",
    tr_business: "Služební",
    tr_private: "Soukromá",
    tr_commute: "Dojíždění",
    tr_unclassified: "Nezařazeno",
    tr_consumption: "Prům. spotřeba",
    tr_at_plug: "ze zásuvky",
    tr_at_battery: "z baterie",
    tr_charge_loss: "{n} % ztráty nabíjení",
    tr_recuperation: "Rekuperováno",
    tr_style: "Styl jízdy",
    tr_style_trend: "Styl jízdy v čase",
    tr_top_dest: "Nejčastější cíle",
    tr_est_cost: "Odhad nákladů",
    tr_longest: "Nejdelší jízda",
    tr_duration: "Doba",
    tr_distance: "Vzdálenost",
    tr_soc_used: "Spotřebovaná baterie",
    tr_visits: "{n}×",
    tr_auto: "auto",
    tr_classify: "Zařadit",
    tr_best: "Nejlepší",
    tr_worst: "Nejhorší",
    tr_unknown_place: "Neznámé",
    tr_open: "Probíhá jízda",
    tr_open_hint: "Právě probíhá jízda",
    tr_min: "{n} min",
    tr_open_note:
      "Tato jízda stále probíhá. Vzdálenost a spotřeba jsou předběžné a jízdu lze zařadit, až skončí.",
    // map
    mp_title: "Mapa jízd",
    mp_loading: "Načítání mapy…",
    mp_error: "Mapu se nepodařilo načíst. Obnovte stránku a zkuste to znovu.",
    mp_empty_none:
      "Zatím nejsou žádná místa k zobrazení. Zapněte „Zaznamenávat trasu“ v Konfigurovat → Jízdy a jeďte — navštívená místa se objeví zde.",
    mp_empty_window: "V tomto období nejsou zaznamenána žádná místa.",
    mp_unavailable: "Mapu se v tomto prohlížeči nepodařilo načíst.",
    mp_win_month: "Tento měsíc",
    mp_win_3m: "3 měsíce",
    mp_win_all: "Vše",
    mp_route_one: "1 trasa",
    mp_route_many: "Trasy: {n}",
    // export
    ex_csv: "CSV",
    ex_report: "Report",
    ex_csv_hint: "Stáhnout tento měsíc jako tabulku",
    ex_report_hint: "Otevřít tisknutelný měsíční report (vytiskněte jej pro PDF)",
    ex_empty: "Za tento měsíc zatím není nic zaznamenáno.",
    ex_error: "Export selhal. Zkontrolujte protokol Home Assistant.",
    // editor
    ed_device: "Vozidlo",
    ed_cluster: "Režim",
    ed_title: "Název (volitelné)",
    ed_image: "Entita obrázku",
    ed_soc: "Stav nabití",
    ed_range: "Dojezd",
    ed_charging: "Stav nabíjení",
    ed_target_soc: "Cíl nabíjení",
    ed_time_to_full: "Plně nabito za",
    ed_odometer: "Počet km",
    ed_plug: "Konektor / připojení",
    ed_fuel: "Palivová nádrž",
    ed_drivetrain: "Pohon",
    edh_drivetrain: "Zjištěno z toho, co vůz přenáší. Nastavte jen tehdy, pokud karta zvolí špatné rozvržení.",
    dt_auto: "Automaticky",
    dt_bev: "Elektrický",
    dt_phev: "Plug-in hybrid",
    dt_ice: "Benzín / nafta",
    ed_steering: "Volant",
    edh_steering: "Čte se z údajů o vozidle od BMW. Nastavte jen tehdy, pokud se dveře a okna zobrazují na špatné straně vozu.",
    st_left: "Volant vlevo",
    st_right: "Volant vpravo",
    ed_shape: "Tvar vozu",
    edh_shape: "Vybírá se podle modelu v údajích o vozidle od BMW. Nastavte jen tehdy, pokud kresba neodpovídá vašemu vozu.",
    sh_sedan: "Sedan BMW",
    sh_countryman: "MINI Countryman",
    ed_overview_option: "Přehled (výchozí)",
    ed_overrides_title: "Přepsání entit (volitelné — ponechte prázdné pro automatické zjištění)",
    edh_cluster:
      "Přehled zobrazuje obrázek vozidla a klíčové hodnoty. Historie nabíjení vypisuje zaznamenané relace s náklady a výkonovou křivkou. Jízdy vypisují zaznamenané jízdy s měsíčním přehledem. Mapa jízd kreslí zaznamenané trasy na mapu (vyžaduje zapnuté „Zaznamenávat trasu“). Stav baterie ukazuje naučenou využitelnou kapacitu a její vývoj. Skupina zobrazí všechny hodnoty dané skupiny jako seznam. Probíhající jízda se zobrazí jako odznak v přehledu a jako živý řádek nahoře v Jízdách.",
    edh_title: "Přepíše název vozidla zobrazený na kartě.",
  },
  sv: {
    // cluster labels
    cl_electric: "Elfordon",
    cl_status: "Fordonsstatus",
    cl_metadata: "Metadata",
    cl_events: "Fordonshändelser",
    cl_tire: "Däck",
    cl_basic: "Fordonets grunddata",
    cl_usage: "Användningsdata",
    cl_other: "Övrigt",
    cl_contract: "ConnectedDrive-avtal",
    // overview
    remaining_range: "återstående räckvidd",
    charging_status: "laddstatus",
    charge: "laddning",
    charging: "laddar",
    is_charging: "Laddar",
    not_charging: "Laddar inte",
    target: "Mål",
    plug: "Kontakt",
    fuel: "bränsle",
    tank: "Tank",
    electric_range: "elräckvidd",
    total_range: "Total räckvidd",
    time_to_full: "Full om",
    charge_time: "Laddtid",
    odometer: "Mätarställning",
    last_update: "Senaste uppdatering",
    spin_hint: "Dra för att vrida bilen",
    hs_door_open: "Dörr öppen",
    hs_doors_open: "{n} dörrar öppna",
    hs_trunk_open: "Bagagelucka öppen",
    hs_hood_open: "Motorhuv öppen",
    hs_windows_open: "Fönster öppna",
    hs_charging: "Laddar",
    hs_interior: "Interiör",
    hs_exterior: "Exteriör",
    hs_default_trim: "BMW:s standardklädsel visas",
    eq_title: "Utrustning",
    eq_count: "{n} poster",
    eq_empty: "Slå på Konfigurera → Fordonsbild → BMW-konfigurator för att lista bilens fabriksutrustning.",
    eq_looks: "Lack, fälgar och lister",
    eq_packages: "Paket",
    eq_features: "Utrustning",
    eq_technical: "Tekniska koder ({n})",
    just_now: "nyss",
    min_ago: "för {n} min sedan",
    h_ago: "för {n} h sedan",
    d_ago: "för {n} d sedan",
    // cluster list
    value: "värde",
    values: "värden",
    no_cluster_entities:
      "Inga {label}-entiteter för det här fordonet. Aktivera gruppen i integrationens alternativ.",
    cc_title: "Check Control-meddelanden",
    cc_none: "Inga Check Control-meddelanden rapporterade.",
    cc_teleservice: "Teleservice",
    cc_mileage: "Senast visat vid",
    cc_sent: "Skickat av bilen",
    cc_code: "Kod",
    cc_earlier: "Tidigare meddelanden ({n})",
    cc_reported: "Senast rapporterat",
    cc_gone: "Rapporteras inte längre",
    // messages
    no_vehicle_title: "Inget BMW CarData-fordon hittades",
    no_vehicle_body:
      "Lägg till integrationen, eller ange <code>device:</code> / <code>vin:</code> i kortets konfiguration.",
    // tire
    front: "FRAM",
    fl: "VF",
    fr: "HF",
    rl: "VB",
    rr: "HB",
    front_left: "Vänster fram",
    front_right: "Höger fram",
    rear_left: "Vänster bak",
    rear_right: "Höger bak",
    tires: "Däck",
    no_tire_data:
      "Inga däckdata för det här fordonet än. Aktivera gruppen Däck och kör för att få mätvärden.",
    check_pressure: "Kontrollera trycket",
    slightly_high: "Något högt",
    all_nominal: "Allt normalt",
    of_four: "{n} av 4",
    t_low: "Lågt",
    t_high: "Högt",
    t_ok: "OK",
    t_nodata: "Inga data",
    t_wear: "Slitet",
    check_tyres: "Kontrollera däcken",
    wear_due: "byts om",
    fitted: "monterat",
    t_current: "Aktuellt",
    // tire summary + per-wheel fitment
    sum_pressure: "Tryck",
    sum_wear: "Slitage",
    p_target: "Börvärde",
    p_target_varies: "Börvärden varierar",
    w_ok: "OK",
    w_soon: "Kontrollera snart",
    w_due: "Byt",
    season_summer: "Sommar",
    season_winter: "Vinter",
    season_all: "Helår",
    runflat: "Runflat",
    // closures / security
    cl_closures: "Säkerhet och öppningar",
    closures_none:
      "Inga data om dörrar, fönster eller säkerhet för det här fordonet än. Aktivera gruppen Fordonsstatus i integrationens alternativ.",
    central_lock: "Centrallås",
    alarm_word: "Stöldlarm",
    alarm_armed: "Larmat",
    alarm_disarmed: "Avlarmat",
    alarm_triggered: "Larmet har utlösts",
    secured: "Säkrad",
    locked: "Låst",
    unlocked: "Olåst",
    partially_locked: "Delvis låst",
    all_closed: "Allt stängt",
    windows_open: "Fönster öppna",
    n_open: "{n} öppna",
    state_open: "Öppen",
    state_closed: "Stängd",
    state_tilted: "På glänt",
    door_word: "Dörr",
    window_word: "Fönster",
    hood_word: "Motorhuv",
    trunk_word: "Bagagelucka",
    rear_window_word: "Bakruta",
    sunroof_word: "Taklucka",
    // charging history
    ch_title: "Laddningshistorik",
    ch_month: "Den här månaden",
    ch_loading: "Laddar laddningshistorik…",
    ch_empty:
      "Inga laddningssessioner registrerade än. Så snart bilen laddar visas sessionerna här automatiskt.",
    ch_empty_month: "Ingen laddning den här månaden.",
    ch_error: "Laddningshistoriken kunde inte läsas in. Ladda om sidan och försök igen.",
    mn_prev: "Föregående månad",
    mn_next: "Nästa månad",
    ch_home: "Hemma",
    ch_public: "Borta",
    ch_assumed: "antaget",
    ch_partial: "delvis pris",
    ch_session_one: "1 session",
    ch_session_many: "{n} sessioner",
    ch_peak: "Topp",
    ch_avg: "Snitt",
    ch_grid: "Från nätet",
    ch_solar: "sol",
    ch_mix: "Energikälla",
    ch_mix_pv: "Sol",
    ch_mix_battery: "Hembatteri",
    ch_mix_grid: "Nät",
    ch_mix_unknown: "ej fördelat",
    ch_duration: "Varaktighet",
    ch_cost: "Kostnad",
    ch_no_cost: "inget pris angivet",
    ch_ongoing: "laddar…",
    // battery health
    bh_title: "Batterihälsa",
    bh_empty:
      "Inga data om batterihälsan än. Efter några laddningar över ett brett intervall visas den användbara kapaciteten här.",
    bh_learning: "Lär sig ({n}/{total})",
    bh_learning_hint:
      "Uppskattar användbar kapacitet utifrån dina laddningar. Några laddningar över ett brett intervall (t.ex. 20 → 80 %) lär den snabbast.",
    bh_suspicious:
      "Stämmer av mot BMW:s eget kapacitetsvärde innan en siffra visas.",
    bh_usable: "Användbar kapacitet",
    bh_of_new: "{p} % av ursprunglig",
    bh_nominal: "Som ny",
    bh_analysed: "Baserat på",
    bh_samples: "{n} laddningar",
    bh_trend_title: "Kapacitet mot miltal",
    // efficiency and real range
    ef_title: "Effektivitet och räckvidd",
    ef_loading: "Räknar ut din verkliga räckvidd…",
    ef_empty:
      "Inte tillräckligt med laddningshistorik än. När två laddningar omsluter ungefär 50 km körning visas din verkliga förbrukning och räckvidden den ger här.",
    ef_no_capacity:
      "Den här bilen har inte rapporterat sin batterikapacitet, så ingen räckvidd kan räknas ut från den uppmätta förbrukningen.",
    ef_hybrid:
      "En laddhybrid kör en del av sträckan på bränsle, och inget av det bilen skickar säger hur mycket. En förbrukning eller räckvidd uträknad från laddningshistoriken skulle se mycket bättre ut än batteriet verkligen är, så ingen visas. Batterihälsan och laddningshistoriken påverkas inte.",
    ef_error: "Effektivitetsdata kunde inte läsas in. Ladda om sidan och försök igen.",
    ef_range_now: "Verklig räckvidd nu",
    ef_at_soc: "vid {p} % laddning",
    ef_range_full: "{km} km på fullt batteri",
    ef_vs_bmw_over: "{p} % längre än bilen förutspår ({km} km)",
    ef_vs_bmw_under: "{p} % kortare än bilen förutspår ({km} km)",
    ef_vs_bmw_same: "Samma som bilens egen uppskattning ({km} km)",
    ef_consumption: "Uppmätt förbrukning",
    ef_side_battery: "från batteriet",
    ef_side_grid: "vid uttaget",
    ef_window_days: "senaste {n} dagarna",
    ef_window_all: "alla poster",
    ef_loss: "Laddförlust",
    ef_capacity: "Användbar kapacitet",
    ef_capacity_measured: "uppmätt",
    ef_capacity_bmw: "BMW:s värde",
    ef_cost: "Kostnad per 100 km",
    ef_solar: "{p} % sol den här månaden",
    ef_trend_title: "Förbrukning per månad",
    ef_footnote:
      "Uppmätt från laddningsliggaren: sträckan mellan två laddningar och energin som gick in, inte bilens egen uppskattning.",
    // charging, battery health and efficiency on a petrol or diesel car
    ice_view_title: "Inget att visa för den här bilen",
    ice_view_body:
      "{name} går bara på bränsle, så det finns ingen laddning, batterihälsa eller elräckvidd att visa. Om den ändå laddas, ange Drivlina i kortredigeraren.",
    // trips
    tr_title: "Resor",
    tr_loading: "Läser in resor…",
    tr_empty:
      "Inga resor registrerade än. Så snart bilen körs visas resorna här automatiskt.",
    tr_empty_month: "Inga resor den här månaden.",
    tr_error: "Resorna kunde inte läsas in. Ladda om sidan och försök igen.",
    tr_trip_one: "1 resa",
    tr_trip_many: "{n} resor",
    tr_review: "Den här månaden",
    tr_vs_last: "jämfört med förra månaden",
    tr_business: "Tjänst",
    tr_private: "Privat",
    tr_commute: "Pendling",
    tr_unclassified: "Oklassad",
    tr_consumption: "Snittförbrukning",
    tr_at_plug: "vid uttaget",
    tr_at_battery: "vid batteriet",
    tr_charge_loss: "{n} % laddförlust",
    tr_recuperation: "Återvunnet",
    tr_style: "Körstil",
    tr_style_trend: "Körstil över tid",
    tr_top_dest: "Vanligaste mål",
    tr_est_cost: "Uppsk. kostnad",
    tr_longest: "Längsta resan",
    tr_duration: "Varaktighet",
    tr_distance: "Sträcka",
    tr_soc_used: "Batteri använt",
    tr_visits: "{n}×",
    tr_auto: "auto",
    tr_classify: "Klassa",
    tr_best: "Bäst",
    tr_worst: "Sämst",
    tr_unknown_place: "Okänd",
    tr_open: "Resa pågår",
    tr_open_hint: "En resa pågår",
    tr_min: "{n} min",
    tr_open_note:
      "Den här resan pågår fortfarande. Sträcka och förbrukning är preliminära, och resan kan klassas när den är slut.",
    // map
    mp_title: "Resekarta",
    mp_loading: "Läser in karta…",
    mp_error: "Kartan kunde inte läsas in. Ladda om sidan och försök igen.",
    mp_empty_none:
      "Inga platser att visa än. Slå på ”Registrera rutt” under Konfigurera → Resor och kör sedan — platserna du besöker visas här.",
    mp_empty_window: "Inga platser registrerade under den här perioden.",
    mp_unavailable: "Kartan kunde inte läsas in i den här webbläsaren.",
    mp_win_month: "Den här månaden",
    mp_win_3m: "3 månader",
    mp_win_all: "Alla",
    mp_route_one: "1 rutt",
    mp_route_many: "{n} rutter",
    // export
    ex_csv: "CSV",
    ex_report: "Rapport",
    ex_csv_hint: "Ladda ned den här månaden som kalkylblad",
    ex_report_hint: "Öppna en utskrivbar månadsrapport (skriv ut den för att få en PDF)",
    ex_empty: "Inget registrerat för den här månaden än.",
    ex_error: "Exporten misslyckades. Kontrollera Home Assistants logg.",
    // editor
    ed_device: "Fordon",
    ed_cluster: "Läge",
    ed_title: "Titel (valfritt)",
    ed_image: "Bildentitet",
    ed_soc: "Laddningsnivå",
    ed_range: "Räckvidd",
    ed_charging: "Laddstatus",
    ed_target_soc: "Laddmål",
    ed_time_to_full: "Full om",
    ed_odometer: "Mätarställning",
    ed_plug: "Kontakt / anslutning",
    ed_fuel: "Bränsletank",
    ed_drivetrain: "Drivlina",
    edh_drivetrain: "Identifieras utifrån vad bilen strömmar. Ange den bara om kortet väljer fel layout.",
    dt_auto: "Identifiera automatiskt",
    dt_bev: "El",
    dt_phev: "Laddhybrid",
    dt_ice: "Bensin / diesel",
    ed_steering: "Ratt",
    edh_steering: "Läses från BMW:s fordonsdata. Ange den bara om dörrar och fönster visas på fel sida av bilen.",
    st_left: "Vänsterstyrd",
    st_right: "Högerstyrd",
    ed_shape: "Bilens form",
    edh_shape: "Väljs utifrån modellen i BMW:s fordonsdata. Ange den bara om ritningen inte stämmer med din bil.",
    sh_sedan: "BMW sedan",
    sh_countryman: "MINI Countryman",
    ed_overview_option: "Översikt (standard)",
    ed_overrides_title: "Åsidosätt entiteter (valfritt — lämna tomt för automatisk identifiering)",
    edh_cluster:
      "Översikten visar fordonsbilden och nyckeltal. Laddningshistoriken listar registrerade sessioner med kostnad och effektkurva. Resor listar registrerade resor med en månadsöversikt. Resekartan ritar registrerade rutter på en karta (kräver att ”Registrera rutt” är på). Batterihälsa visar inlärd användbar kapacitet och dess trend. En grupp visar alla värden i den gruppen som en lista. En pågående resa visas som en bricka i översikten och som en liverad överst i Resor.",
    edh_title: "Åsidosätter fordonsnamnet som visas på kortet.",
  },
};

function _lang(hass) {
  const loc = hass && hass.locale;
  return (loc && loc.language) || (hass && hass.language) || "en";
}

/** The locale the card writes numbers in, following the profile's "Number
 *  format" setting the way Home Assistant's own formatter does, so the card's
 *  figures match the entity states beside them. `null` for "none": plain
 *  digits, no grouping. */
function _numberLocale(hass) {
  const loc = hass && hass.locale;
  switch (loc && loc.number_format) {
    case "none":
      return null;
    case "system":
      return undefined;
    case "comma_decimal":
      return ["en-US", "en"];
    case "decimal_comma":
      return ["de", "es", "it"];
    case "space_comma":
      return ["fr", "sv", "cs"];
    default:
      return _lang(hass);
  }
}

/** Translate `key` for the active hass language, filling `{name}` vars.
 *  Falls back to English, then to `dflt` (or the key itself).
 *
 *  `vars` are substituted **raw**, because some callers deliberately pass markup
 *  (`<b>…</b>`). The result is therefore only as safe as its vars: anything
 *  user-controlled must be `_esc()`d by the caller before it is passed in.
 *  `tests/test_card_escaping.py` enforces that. */
function t(hass, key, vars, dflt) {
  const lang = _lang(hass);
  const table =
    TRANSLATIONS[lang] ||
    TRANSLATIONS[String(lang).split("-")[0]] ||
    TRANSLATIONS.en;
  let s = table[key];
  if (s === undefined) s = TRANSLATIONS.en[key];
  if (s === undefined) s = dflt !== undefined ? dflt : key;
  if (vars) {
    Object.keys(vars).forEach((k) => {
      s = s.split("{" + k + "}").join(vars[k]);
    });
  }
  return s;
}

// Top-down car art shared by the tire and closures diagrams. viewBox 948x1659,
// centreline x=474; right-hand parts are the left ones mirrored by CAR_FLIP.
// Each door row ("f"/"r") also carries its swung-open drawing: the sill left
// behind, and a door skin/trim/edge drawn closed and rotated on its hinge.
const CAR_FLIP = "matrix(-1 0 0 1 948 0)";
const CAR = {
  body:
    "M474 12 C420 12 386 16 366 25 C300 52 228 104 202 150 C186 178 180 205 179 245 L177 1120 C176 1180 171 1225 168 1270 L168 1440 C170 1458 176 1474 181 1495 C190 1545 214 1592 258 1620 C318 1644 420 1648 474 1648 C528 1648 630 1644 690 1620 C734 1592 758 1545 767 1495 C772 1474 778 1458 780 1440 L780 1270 C777 1225 772 1180 771 1120 L769 245 C768 205 762 178 746 150 C720 104 648 52 582 25 C562 16 528 12 474 12 Z",
  hood: "M330 54 C400 46 548 46 618 54 C676 120 708 260 712 432 C640 398 560 380 474 380 C388 380 308 398 236 432 C240 260 272 120 330 54 Z",
  windshield: "M474 386 C578 386 648 402 704 446 L668 606 C600 597 348 597 280 606 L244 446 C300 402 370 386 474 386 Z",
  roof: "M284 614 C380 604 568 604 664 614 C650 720 648 1000 652 1186 C560 1177 388 1177 296 1186 C300 1000 298 720 284 614 Z",
  sunroof: "M358 712 H590 Q598 712 598 720 V1038 Q598 1046 590 1046 H358 Q348 1046 348 1038 V720 Q348 712 358 712 Z",
  rearWindow: "M298 1190 C400 1182 548 1182 650 1190 L684 1388 C600 1426 348 1426 264 1388 Z",
  trunk: "M262 1402 C348 1436 600 1436 686 1402 L693 1512 C600 1532 348 1532 255 1512 Z",
  mirror: "M222 496 C198 504 166 522 151 538 C144 547 147 561 158 563 C180 557 206 549 222 546 Z",
  headlight: "M338 50 C292 70 240 112 204 170 L213 174 C248 124 296 84 342 59 Z",
  drl: "M334 56 C292 76 246 114 210 168",
  tail: "M197 1460 C203 1503 230 1538 288 1553 L346 1559 C352 1561 352 1569 346 1570 L284 1568 C220 1556 194 1518 189 1470 C189 1462 195 1456 197 1460 Z",
  tailCore: "M195 1472 C202 1512 228 1545 292 1560 L340 1564",
  bPillar: "M211 806 L288 806 L289 834 L211 834 Z",
  shuts: "M178 820 L212 820 M179 466 L220 466 M177 1186 L232 1186 M255 1512 L262 1574 C380 1594 568 1594 686 1574 L693 1512",
  creases: "M392 56 C356 160 330 280 318 392 M556 56 C592 160 618 280 630 392",
  sheen: {
    carClipWs: ["M560 370 L650 370 L440 620 L350 620 Z", "M670 370 L700 370 L500 620 L470 620 Z"],
    carClipRw: ["M530 1170 L600 1170 L420 1440 L350 1440 Z"],
    carClipSr: ["M520 700 L572 700 L420 1060 L368 1060 Z"],
  },
  tyreFront: [158, 195, 27, 200],
  tyreRear: [150, 1255, 27, 206],
  handles: [[181, 704], [181, 1004]],
  window: {
    f: { d: "M221 474 C244 520 272 620 284 720 L288 806 L211 806 L211 620 C212 560 215 512 221 474 Z", clip: "carClipWf", drop: 540 },
    r: { d: "M211 834 L289 834 L289 1040 C286 1130 262 1210 230 1268 L220 1268 C214 1150 211 1000 211 834 Z", clip: "carClipWr", drop: 904 },
  },
  door: {
    f: {
      d: "M178 466 L222 466 C250 530 280 640 292 760 L294 818 L178 818 Z",
      hinge: "rotate(30 186 466)",
      sill: "M178 470 L220 470 C213 536 211 636 211 816 L178 816 Z",
      shadow: "M212 476 L211 812",
      skin: "M176 466 C152 554 146 695 152 802 Q155 818 170 818 L208 812 C214 695 222 554 228 474 Z",
      trim: "M178 512 C166 572 163 695 168 778 L198 774 C202 695 208 572 214 512 Z",
      edge: "M170 484 C154 572 151 695 158 798",
    },
    r: {
      d: "M178 822 L294 822 L294 1040 C291 1110 274 1160 254 1186 L177 1186 Z",
      hinge: "rotate(30 186 822)",
      sill: "M178 826 L212 826 C213 892 211 992 211 1184 L178 1184 Z",
      shadow: "M212 832 L211 1180",
      skin: "M176 822 C152 913 146 1059 152 1170 Q155 1186 170 1186 L208 1180 C214 1059 222 913 228 830 Z",
      trim: "M178 868 C166 931 163 1059 168 1146 L198 1142 C202 1059 208 931 214 868 Z",
      edge: "M170 840 C154 931 151 1059 158 1166",
    },
  },
};
// The kidney grille, one per side.
const KIDNEY = (() => {
  const bars = [16, 27, 38, 49, 60, 71, 82, 93].map((i) => `M${366 + i} 21 V43`).join(" ");
  const kidney =
    `<rect x="366" y="12" width="104" height="38" rx="16" class="carsvg__chrome"/>` +
    `<rect x="371" y="17" width="94" height="28" rx="12" class="carsvg__kidney"/>` +
    `<path d="${bars}" class="carsvg__kbar"/>`;
  return `<g>${kidney}</g><g transform="${CAR_FLIP}">${kidney}</g>`;
})();
CAR.grille = KIDNEY;

// MINI Countryman (U25): a boxier SUV with a clamshell bonnet, roof rails and
// a two-panel glass roof, after BeastleeUK's drawing in issue #100. Same
// frame, centreline and parts as CAR, so every overlay works unchanged.
const CAR_COUNTRYMAN = {
  body:
    "M474 30 C372 30 268 34 206 46 C166 54 148 78 144 124 L141 228 C136 262 134 300 134 360 L134 452 C134 476 138 492 142 506 L142 1176 C136 1196 134 1220 134 1250 L134 1388 C134 1420 138 1440 141 1458 L143 1552 C146 1596 168 1616 214 1624 C290 1634 380 1638 474 1638 C568 1638 658 1634 734 1624 C780 1616 802 1596 805 1552 L807 1458 C810 1440 814 1420 814 1388 L814 1250 C814 1220 812 1196 806 1176 L806 506 C810 492 814 476 814 452 L814 360 C814 300 812 262 807 228 L804 124 C800 78 782 54 742 46 C680 34 576 30 474 30 Z",
  hood: "M214 52 C300 40 648 40 734 52 C772 60 788 84 790 130 L792 486 C700 448 586 430 474 430 C362 430 248 448 156 486 L158 130 C160 84 176 60 214 52 Z",
  windshield: "M474 432 C568 432 662 452 744 490 L756 650 C676 628 272 628 192 650 L204 490 C286 452 380 432 474 432 Z",
  roof: "M232 652 C330 638 618 638 716 652 L718 1392 C620 1402 328 1402 230 1392 Z",
  sunroof: "M330 760 H618 Q632 760 632 774 V1206 Q632 1220 618 1220 H330 Q316 1220 316 1206 V774 Q316 760 330 760 Z",
  rearWindow: "M232 1410 C330 1400 618 1400 716 1410 L742 1552 C640 1576 308 1576 206 1552 Z",
  trunk: "M200 1398 C320 1388 628 1388 748 1398 L780 1556 C770 1590 746 1604 710 1610 C620 1622 328 1622 238 1610 C202 1604 178 1590 168 1556 Z",
  mirror: "M150 590 C128 596 98 608 84 624 C78 634 82 646 94 648 C114 644 134 638 150 634 Z",
  headlight: "M206 58 C244 50 290 52 302 64 C310 76 296 98 262 116 C226 134 186 140 170 126 C160 114 172 74 206 58 Z",
  drl: "M212 68 C250 60 284 62 292 70 C292 84 270 100 244 112",
  tail: "M150 1500 C152 1552 162 1590 196 1606 L222 1612 C228 1614 228 1622 220 1622 L196 1618 C160 1604 146 1566 144 1506 C144 1496 150 1492 150 1500 Z",
  tailCore: "M150 1512 C154 1558 166 1592 200 1608 L218 1614",
  bPillar: "M178 900 L232 900 L232 922 L178 922 Z",
  shuts: "M160 1592 C240 1606 360 1612 474 1612",
  creases: "M300 60 C290 180 288 300 292 440 M648 60 C658 180 660 300 656 440",
  grille:
    `<path d="M390 30 L558 30 L576 44 L564 60 L384 60 L372 44 Z" class="carsvg__chrome"/>` +
    `<path d="M396 35 L552 35 L566 44 L558 55 L390 55 L382 44 Z" class="carsvg__kidney"/>` +
    `<path d="M440 100 L460 96 M508 100 L488 96" class="carsvg__kbar"/>` +
    `<circle cx="474" cy="96" r="12" class="carsvg__chrome"/>`,
  // Roof rails and the bar between the two glass panels.
  roofDetail:
    `<path d="M252 690 C248 900 248 1200 252 1370" class="carsvg__rail"/>` +
    `<path d="M252 690 C248 900 248 1200 252 1370" class="carsvg__rail" transform="${CAR_FLIP}"/>` +
    `<path d="M316 990 H632" class="carsvg__rail carsvg__rail--bar"/>`,
  sheen: {
    carClipWs: ["M560 420 L650 420 L440 660 L350 660 Z", "M670 420 L700 420 L500 660 L470 660 Z"],
    carClipRw: ["M530 1395 L600 1395 L470 1580 L400 1580 Z"],
    carClipSr: ["M520 750 L572 750 L420 1230 L368 1230 Z"],
  },
  tyreFront: [116, 240, 36, 236],
  tyreRear: [116, 1204, 36, 236],
  handles: [[138, 820], [138, 1160]],
  window: {
    f: { d: "M182 600 C196 620 214 640 228 664 L230 900 L180 900 Z", clip: "carClipWf", drop: 700, dropX: [150, 260] },
    r: { d: "M180 922 L230 922 L230 1180 C226 1240 210 1300 190 1360 L180 1360 Z", clip: "carClipWr", drop: 990, dropX: [150, 260] },
  },
  door: {
    f: {
      d: "M140 497 L200 497 C216 560 230 620 236 680 L236 905 L140 905 Z",
      hinge: "rotate(20 146 497)",
      sill: "M142 501 L196 501 C192 600 190 760 190 901 L142 901 Z",
      shadow: "M192 505 L190 897",
      skin: "M140 497 C130 600 128 760 132 890 Q134 905 148 905 L186 899 C190 760 194 600 198 505 Z",
      trim: "M148 540 C140 620 138 760 142 860 L172 856 C174 760 178 620 184 540 Z",
      edge: "M136 515 C128 620 128 760 134 885",
    },
    r: {
      d: "M140 909 L236 909 L236 1190 C232 1230 222 1256 208 1268 L140 1268 Z",
      hinge: "rotate(20 146 909)",
      sill: "M142 913 L196 913 C192 1000 190 1140 190 1264 L142 1264 Z",
      shadow: "M192 917 L190 1260",
      skin: "M140 909 C130 1000 128 1140 132 1254 Q134 1268 148 1268 L186 1262 C190 1140 194 1000 198 917 Z",
      trim: "M148 952 C140 1020 138 1140 142 1228 L172 1224 C174 1140 178 1020 184 952 Z",
      edge: "M136 927 C128 1020 128 1140 134 1249",
    },
  },
};

// The shapes `shape:` can pick; `_carShape` chooses one from the car's model.
const CAR_SHAPES = { sedan: CAR, countryman: CAR_COUNTRYMAN };

// The top-down car's body and roof shading. Without a paint colour it is the
// drawing's own silver; with one (the configurator picture's sampled paint) the
// same light-to-edge shading is built from that colour, the roof a shade darker.
const CAR_TONES_DEFAULT = {
  bodyEdge: "#bcc2ca",
  bodyMid: "#dde1e6",
  roofEdge: "#7c828b",
  roofMid: "#969ca4",
};
function carTones(paint) {
  if (!paint) return CAR_TONES_DEFAULT;
  const rgb = [1, 3, 5].map((i) => parseInt(paint.slice(i, i + 2), 16));
  const mix = (target, t) =>
    "#" +
    rgb
      .map((c) => Math.round(c + (target - c) * t))
      .map((c) => c.toString(16).padStart(2, "0"))
      .join("");
  return {
    bodyEdge: mix(0, 0.12),
    bodyMid: mix(255, 0.18),
    roofEdge: mix(0, 0.38),
    roofMid: mix(0, 0.26),
  };
}

class BavarianDataCard extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._sig = null; // force first render
    if (this._hass) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  /** Home Assistant detaches the card when the user switches dashboard tabs,
   * and `ha-map`'s disconnectedCallback destroys its Leaflet map (building a
   * fresh one on reconnect). Our route/cluster layers ride the old instance, so
   * re-draw them here -- the paint guards would otherwise see nothing changed
   * and leave the new map bare. */
  connectedCallback() {
    this._reassertMapOverlays();
  }

  getCardSize() {
    if (this._config && this._config.view === "charging") return 10;
    if (this._config && this._config.view === "trips") return 11;
    if (this._config && this._config.view === "map") return 10;
    if (this._config && this._config.view === "health") return 7;
    if (this._config && this._config.view === "efficiency") return 8;
    return this._config && this._config.cluster ? 6 : 8;
  }

  /** Localized string for the active Home Assistant language. */
  _t(key, vars, dflt) {
    return t(this._hass, key, vars, dflt);
  }

  static getConfigElement() {
    return document.createElement("bavariandata-card-editor");
  }

  static getStubConfig(hass) {
    // Pre-fill the card picker with the first car found.
    const device = BavarianDataCard._firstDevice(hass);
    return device ? { device } : {};
  }

  /**
   * Whether a device is a car rather than the integration's "CarData Debug
   * Device". Both carry a `bavariandata` identifier: a car's is its VIN, the
   * debug device's is the id of its own config entry. The debug device holds
   * diagnostics only, so a card bound to it has nothing to show.
   */
  static _isVehicleDevice(dev) {
    if (!dev) return false;
    const entries = new Set(dev.config_entries || []);
    return (dev.identifiers || []).some(
      (pair) => pair && pair[0] === "bavariandata" && !entries.has(pair[1])
    );
  }

  /** Device ids of every car, in registry order. */
  static _vehicleDevices(hass) {
    if (!hass || !hass.devices) return [];
    return Object.values(hass.devices)
      .filter((dev) => BavarianDataCard._isVehicleDevice(dev))
      .map((dev) => dev.id);
  }

  static _firstDevice(hass) {
    if (!hass || !hass.entities) return undefined;
    const devices = hass.devices || {};
    for (const ent of Object.values(hass.entities)) {
      if (ent.platform !== "bavariandata" || !ent.device_id) continue;
      if (BavarianDataCard._isVehicleDevice(devices[ent.device_id])) return ent.device_id;
    }
    return undefined;
  }

  /* ---- discovery -------------------------------------------------------- */

  _resolveDeviceId() {
    const hass = this._hass;
    const cfg = this._config || {};
    if (cfg.device) {
      const dev = hass.devices && hass.devices[cfg.device];
      // A card saved while the editor still offered the "CarData Debug Device"
      // would stay blank for good: that device holds no car, so find the car.
      if (!dev || BavarianDataCard._isVehicleDevice(dev)) return cfg.device;
    }
    if (cfg.vin && hass.devices) {
      for (const dev of Object.values(hass.devices)) {
        const ids = dev.identifiers || [];
        if (ids.some((pair) => pair && pair[0] === "bavariandata" && pair[1] === cfg.vin)) {
          return dev.id;
        }
      }
    }
    return BavarianDataCard._firstDevice(hass);
  }

  _deviceEntities(deviceId) {
    const hass = this._hass;
    if (!hass || !hass.entities) return [];
    return Object.values(hass.entities)
      .filter(
        (ent) =>
          ent.platform === "bavariandata" &&
          ent.device_id === deviceId &&
          hass.states[ent.entity_id]
      )
      .map((ent) => ent.entity_id);
  }

  _st(entityId) {
    return entityId ? this._hass.states[entityId] : undefined;
  }

  /** `text` without the vehicle's name (`slug`: as it appears in an entity_id). */
  _unnamed(text, slug = false) {
    let name = (this._pickVehicle || "").toLowerCase().trim();
    if (!name) return text;
    if (slug) name = name.replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
    return name ? text.toLowerCase().split(name).join(" ") : text;
  }

  /** Rank candidate entities by keyword preference; return the best entity_id. */
  _pick(entities, { domain = "sensor", prefer = [], avoid = [], deviceClass, unit, usable = false } = {}) {
    const scored = [];
    for (const id of entities) {
      if (domain && !id.startsWith(domain + ".")) continue;
      const st = this._st(id);
      if (!st) continue;
      // `usable`: only an entity that has something to show right now.
      if (usable && UNAVAILABLE.has(st.state)) continue;
      const attrs = st.attributes || {};
      if (deviceClass && attrs.device_class !== deviceClass) continue;
      if (unit && attrs.unit_of_measurement !== unit) continue;
      // Match against the descriptor path too (exposed as an attribute). The
      // entity_id and friendly_name are localized (German, etc.), but the
      // descriptor is always the English BMW path, so keyword matching keeps
      // working regardless of the user's Home Assistant language. Include a
      // space-normalized copy (camelCase + dots/underscores -> spaces) so
      // multi-word English keys like "charging status" or "electric range"
      // match the descriptor and not only the localized friendly_name.
      const descriptor = attrs.descriptor || "";
      const descriptorWords = descriptor
        .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
        .replace(/[._]/g, " ");
      // The vehicle's own name leads both the entity_id and the friendly name,
      // so it is not evidence about the sensor: "M Sport" contains "port" and
      // hid the charging status behind the avoid-list (issue #51).
      const hay = (
        this._unnamed(id, true) +
        " " +
        this._unnamed(attrs.friendly_name || "") +
        " " +
        descriptor +
        " " +
        descriptorWords
      ).toLowerCase();
      if (avoid.some((a) => hay.includes(a))) continue;
      let score = 0;
      prefer.forEach((p, i) => {
        if (hay.includes(p)) score += prefer.length - i;
      });
      if (prefer.length && score === 0) continue;
      scored.push({ id, score });
    }
    scored.sort((a, b) => b.score - a.score);
    return scored.length ? scored[0].id : undefined;
  }

  _overviewEntities(entities, vehicleName = "") {
    const cfg = this._config || {};
    this._pickVehicle = vehicleName;
    return {
      image: cfg.image || entities.find((id) => id.startsWith("image.")),
      soc:
        cfg.soc ||
        // Two battery-class percentages impersonate the live SoC here. "trip"
        // excludes the trip-end one, which only moves when a drive finishes;
        // "charging.level" excludes BMW's *predicted* SoC, which is REST-only
        // and so never updates on the stream at all. Both were silently winning
        // this pick on a car whose live SoC was missing, showing a figure hours
        // old (issue #6). Note "charging.level" is matched against the
        // descriptor rather than the name: "predicted" alone only rejects it in
        // English, and a German install picked it up. Both remain available to
        // the fallback below, where they are chosen knowingly.
        //
        // The integration's own estimate comes first. While the car is parked
        // it *is* the last measured reading; while it charges it is the live
        // figure, because BMW can go silent for hours mid-charge and the
        // measured SoC then sits on its last value -- the ring showed 38 % for
        // two hours on a car that had reached 47 % (26 September 2026). It only
        // exists on a car with a high-voltage battery, so a combustion car never
        // gets here.
        //
        // Next, the measured HV state of charge is named outright: the other
        // battery-class percentages all score zero, so without a preference the
        // tie went to entity-registry order -- and every car also carries its
        // 12 V battery, and older installs the fuel tank tagged as a battery. A
        // plug-in hybrid could show its tank here, a petrol car its 12 V battery.
        //
        // Every pick with a value comes before any without one. The measured SoC
        // reads unknown until the car first reports it -- on a new install, and
        // on one where an old default had left it disabled, so it had nothing to
        // restore -- and naming it outright regardless blanked the ring while the
        // integration's own estimate (whose English name says "Predicted", so the
        // avoid-list skips it) held the right figure. Only when nothing has a
        // value does the gauge bind to the measured SoC, to fill in when it lands.
        this._pick(entities, { unit: "%", prefer: ["soc_estimate"], avoid: ["testing"], usable: true }) ||
        this._pick(entities, {
          deviceClass: "battery",
          unit: "%",
          prefer: ["batterymanagement.header"],
          usable: true,
        }) ||
        this._pick(entities, {
          deviceClass: "battery",
          unit: "%",
          avoid: ["target", "predicted", "health", "testing", "trip", "charging.level", ...NOT_HV_BATTERY],
          usable: true,
        }) ||
        this._pick(entities, {
          deviceClass: "battery",
          unit: "%",
          prefer: ["batterymanagement.header"],
        }) ||
        this._pick(entities, {
          deviceClass: "battery",
          unit: "%",
          avoid: ["target", "predicted", "health", "testing", "trip", "charging.level", ...NOT_HV_BATTERY],
        }) ||
        // Last resort, and it still may not take an impostor: on a car whose
        // only battery-class percentage IS the trip-end SoC, every pick above
        // rejects it and this one used to hand it the ring -- showing a petrol
        // MINI a charge level (issue #23's diagnostics). Better an empty ring
        // than a number from another drivetrain.
        this._pick(entities, {
          unit: "%",
          prefer: ["charge", "soc"],
          avoid: ["target", "rate", "trip", "charging.level", ...NOT_HV_BATTERY],
        }),
      range:
        cfg.range ||
        // Three distance entities answer to "electric range" on a BEV, and BMW
        // named the useless one best. `remainingElectricRange` is, in BMW's own
        // words, "the electric range predicted during charging" -- on a parked
        // car it is whatever was predicted mid-charge, 128 km against a real 379
        // on a car sitting at 86 %. `range.target` is the range at the *target*
        // state of charge, not the current one. The number on the car's own
        // display is `kombiRemainingElectricRange`, so name it outright: all
        // three score identically on the keywords, and the winner of that tie
        // was decided by entity-registry order.
        this._pick(entities, {
          deviceClass: "distance",
          prefer: ["kombi remaining electric range", "electric range", "range"],
          avoid: ["electricengine.remainingelectricrange", "range.target"],
        }) ||
        // Both impostors stay reachable here, for a car that streams nothing
        // better -- chosen knowingly rather than by accident.
        this._pick(entities, { prefer: ["range"] }),
      charging:
        cfg.charging ||
        this._pick(entities, { prefer: ["charging.status", "charging status", "hvstatus", "charging"], avoid: ["port", "cable", "history"] }),
      target:
        cfg.target_soc ||
        this._pick(entities, { deviceClass: "battery", unit: "%", prefer: ["target"] }),
      timeToFull:
        cfg.time_to_full ||
        // The integration's own figure while it has one: at home it follows
        // the wallbox meter and lands within minutes, where BMW's ran up to
        // half an hour long and kept its last value after the charge ended.
        // Unknown away from home, which leaves BMW's.
        this._idByDescriptor(
          entities.filter((id) => !isNaN(parseFloat((this._st(id) || {}).state))),
          "charge_eta"
        ) ||
        this._pick(entities, { prefer: ["fully charged", "time remaining", "timetofully"] }),
      odometer:
        cfg.odometer ||
        this._pick(entities, { prefer: ["mileage", "odometer", "travelled", "traveled"] }),
      plug:
        cfg.plug ||
        // Only a real `isPlugged` (the one boolean given the plug class). A
        // keyword match also caught `isHospitalityActive`, which issue #25's X3
        // streams as a boolean -- a binary sensor named "...plug post-charge
        // lock state" -- and showed it as the plug: "Off" beside a cable in.
        this._pick(entities, { domain: "binary_sensor", deviceClass: "plug" }) ||
        // A car without the `isPlugged` binary sensor (issue #25's X3) says it
        // on the charging port instead, and three of its English names contain
        // "plug" -- the plug state and two *lock* states -- so the keyword pick
        // was a coin toss decided by registry order, and on a German install
        // none of them matched at all. Name the sources outright, in the order
        // the evcc bridge reads them (evcc.PLUG_DESCRIPTORS).
        PLUG_SENSOR_DESCRIPTORS.map((d) => this._idByDescriptor(entities, d)).find(Boolean) ||
        this._pick(entities, { prefer: ["plug", "connection status"], avoid: ["stream", "lock", "hospitality"] }),
    };
  }

  /** The state of a device entity by its `descriptor` attribute.
   * Derived entities (trip flag, monthly distance, battery health) are found this
   * way rather than by name: the descriptor is language-independent, so it works
   * on a German install where the friendly name is localized. */
  _byDescriptor(entities, descriptor) {
    return (
      entities
        .map((id) => this._st(id))
        .find(
          (st) => st && st.attributes && st.attributes.descriptor === descriptor
        ) || null
    );
  }

  /** Entity id of a device entity by its `descriptor` attribute. */
  _idByDescriptor(entities, descriptor) {
    const st = this._byDescriptor(entities, descriptor);
    return st ? st.entity_id : undefined;
  }

  /** The overview layout: "bev", "phev" or "ice".
   *
   * An explicit `drivetrain:` wins. Otherwise it is read from what the car
   * actually streams -- high-voltage battery data and fuel data together make a
   * plug-in hybrid, fuel data alone a petrol or diesel car. BMW's basic data
   * spells an electric car "BEV", but its spelling for the others is unknown, so
   * it only ever confirms electric. A car that has sent neither yet keeps the
   * electric layout this card has always shown. */
  _drivetrain(entities) {
    const wanted = String((this._config || {}).drivetrain || "").toLowerCase();
    if (DRIVETRAINS.has(wanted)) return wanted;
    let hv = false;
    let fuel = false;
    let basic = null;
    let described = 0;
    for (const id of entities) {
      const st = this._st(id);
      const attrs = (st && st.attributes) || {};
      const descriptor = attrs.descriptor || "";
      if (descriptor) described++;
      if (HV_SIGNALS.includes(descriptor)) hv = true;
      if (COMBUSTION_PREFIXES.some((prefix) => descriptor.startsWith(prefix))) fuel = true;
      const bd = attrs.vehicle_basic_data;
      if (!basic && bd && bd.drive_train) basic = String(bd.drive_train).toUpperCase();
    }
    // An i4 streams a tank level although BMW's basic data calls it BEV (#53).
    if (hv && fuel && basic !== "BEV") return "phev";
    if (fuel && basic !== "BEV") return "ice";
    if (hv || basic === "BEV") return "bev";
    // Neither proved, on a car that has plenty else to say: a petrol MINI
    // streams no fuel system and no combustion engine at all (issue #23), and
    // calling that electric hands it a charge ring it can never fill. A car
    // that has said nothing yet keeps the electric layout this card has always
    // shown, so a fresh install is not rearranged for a few seconds.
    return described >= MIN_DESCRIPTORS_TO_JUDGE ? "unknown" : "bev";
  }

  /** Which side the driver sits on: "left" or "right".
   *
   * BMW keys the side doors and windows by seat (`door.row1.driver`), not by
   * side, so drawing them needs to know where the steering wheel is. An
   * explicit `steering:` wins; otherwise BMW's basic data says it. A car whose
   * basic data has not said keeps the left-hand-drive layout this card has
   * always drawn. */
  _steering(entities) {
    const wanted = String((this._config || {}).steering || "").toLowerCase();
    if (STEERING_SIDES.has(wanted)) return wanted;
    for (const id of entities) {
      const st = this._st(id);
      const bd = st && st.attributes && st.attributes.vehicle_basic_data;
      if (bd && STEERING_SIDES.has(bd.steering_side)) return bd.steering_side;
    }
    return "left";
  }

  /** The top-down drawing for this car: an explicit `shape:` wins, otherwise
   * BMW's basic-data model name picks one, otherwise the BMW saloon. */
  _carShape(entities) {
    const shape = this._carShapeOnly(entities);
    // The configurator picture's paint colours the drawing; without it the
    // shape is returned as is, so the default drawing never changes.
    const paint = this._carPaint(entities);
    return paint ? { ...shape, paint } : shape;
  }

  _carShapeOnly(entities) {
    const wanted = String((this._config || {}).shape || "").toLowerCase();
    if (CAR_SHAPES[wanted]) return CAR_SHAPES[wanted];
    for (const id of entities) {
      const st = this._st(id);
      const bd = st && st.attributes && st.attributes.vehicle_basic_data;
      if (bd && /countryman/i.test(String(bd.model_name || ""))) return CAR_COUNTRYMAN;
    }
    return CAR;
  }

  /* ---- formatting ------------------------------------------------------- */

  _fmt(st) {
    if (!st) return "—";
    if (UNAVAILABLE.has(st.state)) return "—";
    const hass = this._hass;
    if (hass.formatEntityState) {
      try {
        return this._esc(hass.formatEntityState(st));
      } catch (e) {
        /* fall through */
      }
    }
    const unit = st.attributes && st.attributes.unit_of_measurement;
    return this._esc(unit ? `${st.state} ${unit}` : st.state);
  }

  _num(st) {
    if (!st || UNAVAILABLE.has(st.state)) return null;
    const n = Number(st.state);
    return Number.isFinite(n) ? n : null;
  }

  // A raw kilometre figure (not a state object) with locale grouping, in
  // `unit` ("km" or "mi"). The integration normalises BMW's KILOMETER/MILE unit
  // to km before storing it, so a figure in an attribute never passes through
  // Home Assistant's unit conversion the way a sensor state does.
  _distance(km, unit = "km") {
    const n = Number(km);
    if (!Number.isFinite(n)) return "—";
    return unit === "mi" ? `${this._dec(n / KM_PER_MILE, 0)} mi` : `${this._dec(n, 0)} km`;
  }

  /** "mi" or "km": the unit this car's distances are shown in.
   *
   * The odometer's own unit first, because a UK install typically keeps the
   * metric unit system and switches the distance sensors to miles one by one;
   * then any other distance sensor of the car's; then the unit system. */
  _lengthUnit(entities) {
    const units = [];
    for (const d of DESC_ODOMETER) {
      const st = this._byDescriptor(entities, d);
      if (st) units.push(st.attributes && st.attributes.unit_of_measurement);
    }
    for (const id of entities) {
      const a = (this._st(id) || {}).attributes || {};
      if (a.device_class === "distance") units.push(a.unit_of_measurement);
    }
    const unit = units.find((u) => u === "mi" || u === "km");
    if (unit) return unit;
    const system = this._hass && this._hass.config && this._hass.config.unit_system;
    return system && system.length === "mi" ? "mi" : "km";
  }

  _relTime(iso) {
    if (!iso) return null;
    const then = new Date(iso).getTime();
    if (Number.isNaN(then)) return null;
    const s = Math.round((Date.now() - then) / 1000);
    if (s < 60) return this._t("just_now");
    const m = Math.round(s / 60);
    if (m < 60) return this._t("min_ago", { n: m });
    const h = Math.round(m / 60);
    if (h < 24) return this._t("h_ago", { n: h });
    return this._t("d_ago", { n: Math.round(h / 24) });
  }

  /** Whether the plug entity says a cable is in, whichever kind it is: the
   * `isPlugged` binary sensor reads "on", the charging port's enum "connected".
   * The icon used to follow *charging* instead, so a car plugged in and done
   * showed a crossed-out plug beside the word "connected". */
  _isPlugged(plugSt) {
    const raw = plugSt && plugSt.state != null ? String(plugSt.state).toLowerCase() : "";
    return raw === "on" || raw === "connected";
  }

  _isCharging(chargingSt, socSt) {
    // The charging.status descriptor is authoritative (see coordinator), so an
    // explicit active/not-charging value settles it before the heuristic.
    const raw = chargingSt && chargingSt.state != null ? String(chargingSt.state).toLowerCase() : "";
    if (CHARGING_ACTIVE_STATES.has(raw)) return true;
    if (NOT_CHARGING_STATES.has(raw) || CHARGING_STOPPED_STATES.has(raw)) return false;
    const hay = [chargingSt && chargingSt.state, socSt && socSt.attributes && socSt.attributes.charging]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    // Boundary before "active" so "inactive" is not read as charging.
    return /(^|_| )(charging|active)|in_progress/.test(hay) && !/not|no_?charging|complete|finished/.test(hay);
  }

  /* ---- render ----------------------------------------------------------- */

  _signature(payload) {
    // Cheap change-detection so we don't rebuild the DOM on every hass tick.
    return JSON.stringify(payload);
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });

    const deviceId = this._resolveDeviceId();
    if (!deviceId) {
      this._renderMessage(this._t("no_vehicle_title"), this._t("no_vehicle_body"));
      return;
    }
    const entities = this._deviceEntities(deviceId);
    if (this._config.view === "charging") {
      this._renderCharging(deviceId, entities);
    } else if (this._config.view === "trips") {
      this._renderTrips(deviceId, entities);
    } else if (this._config.view === "map") {
      this._renderMap(deviceId, entities);
    } else if (this._config.view === "health") {
      this._renderHealth(deviceId, entities);
    } else if (this._config.view === "equipment") {
      this._renderEquipment(deviceId, entities);
    } else if (this._config.view === "efficiency") {
      this._renderEfficiency(deviceId, entities);
    } else if (this._config.cluster === "tire") {
      this._renderTires(deviceId, entities);
    } else if (this._config.cluster === "closures") {
      this._renderClosures(deviceId, entities);
    } else if (this._config.cluster) {
      this._renderCluster(deviceId, entities);
    } else {
      this._renderOverview(deviceId, entities);
    }
  }

  /** VIN behind a device id, read from the integration's device identifier. */
  _deviceVin(deviceId) {
    const dev = this._hass.devices && this._hass.devices[deviceId];
    const ids = (dev && dev.identifiers) || [];
    for (const pair of ids) {
      if (pair && pair[0] === "bavariandata") return pair[1];
    }
    return null;
  }

  /** The config entry a vehicle device belongs to.
   *
   * With two accounts set up -- a BMW and a MINI, say -- the services cannot
   * guess which entry a call means and refuse it, so every call the card makes
   * has to name its entry. Home Assistant has spelled that field differently
   * over time: `config_entry_id` only exists from 2026.8, `primary_config_entry`
   * is there throughout, and the `config_entries` list is deprecated as of
   * 2026.8. Take whichever is present, and `null` where none is -- see
   * `_withEntry` for why that is never sent.
   */
  _deviceEntry(deviceId) {
    const dev = this._hass.devices && this._hass.devices[deviceId];
    if (!dev) return null;
    const entries = Array.isArray(dev.config_entries) ? dev.config_entries : [];
    return dev.config_entry_id || dev.primary_config_entry || entries[0] || null;
  }

  /** Service data with `entry_id` added, but only when it is actually known.
   *
   * The services take `entry_id` as an optional *string*: sending it as null
   * fails schema validation and the whole call errors out, which would break
   * the very views this is here to fix. Leaving it off instead falls back to
   * the single configured entry -- correct for the one-account install, and
   * the only thing we can do when the device registry will not say.
   */
  _withEntry(data, entryId) {
    return entryId ? { ...data, entry_id: entryId } : data;
  }

  _deviceName(deviceId) {
    const dev = this._hass.devices && this._hass.devices[deviceId];
    return (dev && (dev.name_by_user || dev.name)) || "BMW";
  }

  _renderOverview(deviceId, entities) {
    const cfg = this._config || {};
    const drivetrain = this._drivetrain(entities);
    const ice = drivetrain === "ice";
    // A car that proved no drivetrain is laid out like a combustion one -- no
    // charging tiles, nothing electric -- except that it has no tank either, so
    // the ring falls through to empty and the lead row shows what it does have.
    const bare = drivetrain === "unknown";
    const picks = this._overviewEntities(entities, this._deviceName(deviceId));
    const byDesc = (descriptor) => this._idByDescriptor(entities, descriptor);
    const name = cfg.title || this._deviceName(deviceId);
    const trip = this._openTrip(entities);
    const socSt = this._st(picks.soc);
    const chargingSt = this._st(picks.charging);
    const odometerSt = this._st(picks.odometer);

    // What the ring and the tiles show follows the drivetrain. An electric car
    // keeps the layout this card has always had; a plug-in hybrid adds its tank
    // and combined range; a petrol or diesel car swaps the charge ring for its
    // tank and drops every charging tile -- BMW streams an EV charge target even
    // to a petrol M2, where "Target 0 %" means nothing.
    const fuelId =
      cfg.fuel ||
      byDesc("vehicle.drivetrain.fuelSystem.level") ||
      byDesc("vehicle.drivetrain.fuelSystem.remainingFuel");
    const fuelSt = this._st(fuelId);
    const litresId = byDesc("vehicle.drivetrain.fuelSystem.remainingFuel");
    const litresSt = litresId && litresId !== fuelId ? this._st(litresId) : null;
    const rangeId = ice || bare
      ? cfg.range ||
        byDesc("vehicle.drivetrain.lastRemainingRange") ||
        byDesc("vehicle.cabin.infotainment.navigation.remainingRange") ||
        picks.range
      : picks.range;
    const rangeSt = this._st(rangeId);
    const charging = !ice && !bare && this._isCharging(chargingSt, socSt);

    // The ring: state of charge, or the tank on a combustion car -- as a fill
    // level where the car streams a percentage, otherwise its volume on a bare
    // ring (a fill level can't be drawn from litres without the tank size).
    // A car that proved no drivetrain has neither, so it rings the one headline
    // number it does have: how far it can still go.
    const gaugeId = ice ? fuelId : bare ? rangeId : picks.soc;
    const gaugeSt = this._st(gaugeId);
    const gaugeNum = this._num(gaugeSt);
    const gaugeUnit = (gaugeSt && gaugeSt.attributes && gaugeSt.attributes.unit_of_measurement) || "";
    const isPct = (!ice && !bare) || gaugeUnit === "%";
    const pct = isPct && gaugeNum != null ? Math.max(0, Math.min(100, gaugeNum)) : 0;
    const ringColor = charging
      ? "var(--bmw-charge)"
      : !isPct || gaugeNum == null
      ? "var(--divider-color)"
      : gaugeNum <= 15
      ? "var(--bmw-low)"
      : gaugeNum <= 40
      ? "var(--bmw-mid)"
      : "var(--bmw-high)";
    const gaugeVal = gaugeNum == null ? "—" : String(Math.round(gaugeNum));
    const gaugeUnitHtml = isPct ? "%" : this._esc(gaugeUnit);
    const gaugeCap = ice
      ? this._t("fuel")
      : bare
      ? this._t("remaining_range")
      : charging
      ? this._t("charging")
      : this._t("charge");

    const tile = (key, label, st, icon) => ({ key, label, st, icon });
    const plugSt = this._st(picks.plug);
    // "0 min" outside a charge is no charge time, only the end of the last one.
    const ttfRaw = this._st(picks.timeToFull);
    const ttfSt = !charging && ttfRaw && parseFloat(ttfRaw.state) === 0 ? null : ttfRaw;
    const rangeRow = {
      entity: rangeId,
      icon: "mdi:map-marker-distance",
      val: this._fmt(rangeSt),
      lbl: this._t(drivetrain === "phev" ? "electric_range" : "remaining_range"),
    };
    let lead;
    let secondary;
    if (ice || bare) {
      const odometerRow = {
        entity: picks.odometer,
        icon: "mdi:counter",
        val: this._fmt(odometerSt),
        lbl: this._t("odometer"),
      };
      lead = bare
        ? [odometerRow]
        : [
            rangeRow,
            litresSt
              ? { entity: litresId, icon: "mdi:gas-station", val: this._fmt(litresSt), lbl: this._t("tank") }
              : odometerRow,
          ];
      secondary = litresSt && !bare ? [tile("odo", this._t("odometer"), odometerSt, "mdi:counter")] : [];
    } else {
      lead = [
        rangeRow,
        {
          entity: picks.charging,
          icon: charging ? "mdi:battery-charging" : "mdi:ev-station",
          val: this._chargingLabel(chargingSt, charging),
          lbl: this._t("charging_status"),
        },
      ];
      secondary = [
        ...(drivetrain === "phev"
          ? [
              tile("fuel", this._t("tank"), fuelSt, "mdi:gas-station"),
              tile(
                "total",
                this._t("total_range"),
                this._st(byDesc("vehicle.drivetrain.lastRemainingRange")),
                "mdi:map-marker-distance"
              ),
            ]
          : []),
        tile("target", this._t("target"), this._st(picks.target), "mdi:target"),
        tile("plug", this._t("plug"), plugSt, charging || this._isPlugged(plugSt) ? "mdi:power-plug" : "mdi:power-plug-off"),
        tile("ttf", charging ? this._t("time_to_full") : this._t("charge_time"), ttfSt, "mdi:timer-sand"),
        tile("odo", this._t("odometer"), odometerSt, "mdi:counter"),
      ];
    }
    secondary = secondary.filter((m) => m.st);

    // freshest update among the headline entities
    const freshest = [gaugeSt, rangeSt, ice ? null : chargingSt]
      .filter(Boolean)
      .map((s) => s.last_changed)
      .sort()
      .pop();

    const spinInfo = picks.image ? this._spinInfo(picks.image, entities) : null;
    const pano = spinInfo && picks.image ? this._interiorInfo(picks.image) : null;
    const inside = !!pano && this._heroMode === "interior";
    const spin = inside ? null : spinInfo;
    const heroState = spinInfo && !inside ? this._heroState(entities, charging) : null;
    const sig = this._signature({
      m: "ov",
      dt: drivetrain,
      lang: _lang(this._hass),
      name,
      charging,
      img: picks.image,
      spin: spinInfo && spinInfo.url,
      inside,
      pano: !!pano,
      hs: heroState && [heroState.chips.map((c) => c.label), heroState.inset.length],
      fresh: freshest,
      gauge: [gaugeId, gaugeVal, gaugeUnitHtml, pct, ringColor, gaugeCap],
      lead: lead.map((r) => [r.entity, r.lbl, r.val]),
      sec: secondary.map((s) => [s.label, s.st.state]),
      // Elapsed minutes ride the signature so the badge's duration keeps ticking
      // between the entity's throttled attribute writes.
      trip: trip
        ? [trip.distance_km, this._elapsedMin(trip.started), trip.held]
        : null,
    });
    if (sig === this._sig) return;
    this._sig = sig;

    const imgUrl = picks.image ? this._imageUrl(picks.image) : null;
    const rel = this._relTime(freshest);
    // Only over the configurator photo: badges, the inset and the interior
    // switch. Empty otherwise, so the plain hero's markup is unchanged.
    const heroExtras =
      this._heroStateHtml(heroState) +
      (pano
        ? `<button class="hero__mode" data-hero-mode title="${this._esc(this._t(inside ? "hs_exterior" : "hs_interior"))}"><ha-icon icon="${inside ? "mdi:car-side" : "mdi:car-seat"}"></ha-icon></button>`
        : "");

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        <div class="hero ${imgUrl || spinInfo ? "" : "hero--empty"}${heroState && heroState.inset ? " hero--inset" : ""}">
          ${
            inside
              ? this._panoHtml(pano)
              : spin
                ? this._spinHtml(spin, name)
                : imgUrl
                ? `<img class="hero__img" src="${imgUrl}" alt="${this._esc(name)}" />`
                : `<ha-icon class="hero__placeholder" icon="${ice ? "mdi:car" : "mdi:car-electric"}"></ha-icon>`
          }
          <div class="hero__scrim"></div>
          <div class="hero__top">
            <div class="hero__name" title="${this._esc(name)}">${this._esc(name)}</div>
            ${rel ? `<div class="pill" title="${this._t("last_update")}"><span class="dot ${this._staleClass(freshest)}"></span>${rel}</div>` : ""}
          </div>
          ${this._tripPill(trip)}${heroExtras}
        </div>

        <div class="band">
          <button class="gauge" data-entity="${gaugeId || ""}" aria-label="${gaugeCap}">
            <div class="gauge__ring" style="--pct:${pct};--ring:${ringColor}">
              <div class="gauge__hole">
                <span class="gauge__val">${gaugeVal}<i>${gaugeUnitHtml}</i></span>
                <span class="gauge__cap">${gaugeCap}</span>
              </div>
            </div>
            ${charging ? `<ha-icon class="gauge__bolt" icon="mdi:lightning-bolt"></ha-icon>` : ""}
          </button>

          <div class="lead">
            ${lead
              .map(
                (r) => `
            <button class="lead__row" data-entity="${r.entity || ""}">
              <ha-icon icon="${r.icon}"></ha-icon>
              <span class="lead__val">${r.val}</span>
              <span class="lead__lbl">${r.lbl}</span>
            </button>`
              )
              .join("")}
          </div>
        </div>

        ${
          secondary.length
            ? `<div class="grid">
                ${secondary
                  .map(
                    (m) => `
                  <button class="cell" data-entity="${m.st.entity_id}">
                    <ha-icon icon="${m.icon}"></ha-icon>
                    <div class="cell__body">
                      <span class="cell__val">${this._fmt(m.st)}</span>
                      <span class="cell__lbl">${m.label}</span>
                    </div>
                  </button>`
                  )
                  .join("")}
              </div>`
            : ""
        }
      </ha-card>
    `;
    this._wireTaps();
    if (spin) this._wireSpin(spin);
    if (inside) this._wirePano();
    const mode = pano ? this.shadowRoot.querySelector("[data-hero-mode]") : null;
    if (mode) {
      mode.addEventListener("click", (ev) => {
        ev.stopPropagation();
        this._heroMode = this._heroMode === "interior" ? "exterior" : "interior";
        this._sig = null;
        this._render();
      });
    }
  }

  /** The drive under way, read off the trip flag entity — or null when parked.
   *
   * The integration exposes the in-flight trip as a binary sensor with the trip
   * so far as attributes, which is the only place it exists: an open trip is not
   * in the history store until it closes. Deliberately not treated as a "car is
   * moving" signal — see the entity's own docstring. It opens on the first GPS
   * fix that reads as movement and closes a debounce after the last one, so it
   * lingers for some minutes after an arrival. */
  _openTrip(entities) {
    const st = this._byDescriptor(entities, "trip_in_progress");
    if (!st || st.state !== "on") return null;
    const a = st.attributes || {};
    return {
      entity_id: st.entity_id,
      started: a.started || null,
      start_location: a.start_location || null,
      distance_km: a.distance_km == null ? null : Number(a.distance_km),
      soc_start: a.soc_start == null ? null : Number(a.soc_start),
      soc_now: a.soc_now == null ? null : Number(a.soc_now),
      held: !!a.held,
    };
  }

  /** Elapsed whole minutes since an ISO timestamp, or null if unparseable.
   * Computed in the frontend so the figure stays live between the entity's
   * throttled attribute writes. */
  _elapsedMin(iso) {
    if (!iso) return null;
    const t = new Date(iso).getTime();
    if (!Number.isFinite(t)) return null;
    return Math.max(0, Math.floor((Date.now() - t) / 60000));
  }

  /** Hero badge for a drive in progress; empty string when the car is parked. */
  _tripPill(trip) {
    if (!trip) return "";
    const bits = [];
    if (trip.distance_km != null) bits.push(`${this._dec(trip.distance_km, 1)} km`);
    const min = this._elapsedMin(trip.started);
    if (min != null) bits.push(this._t("tr_min", { n: min }));
    return `
      <button class="hero__trip" data-entity="${trip.entity_id}" title="${this._t(
        "tr_open_hint"
      )}">
        <span class="hero__trip-dot"></span>
        <span class="hero__trip-lbl">${this._t("tr_open")}</span>
        ${bits.length ? `<span class="hero__trip-sub">${bits.join(" · ")}</span>` : ""}
      </button>`;
  }

  _chargingLabel(st, charging) {
    const raw = st && st.state != null ? String(st.state).toLowerCase() : "";
    if (!st || UNAVAILABLE.has(st.state) || NOT_CHARGING_STATES.has(raw)) {
      return this._t(charging ? "is_charging" : "not_charging");
    }
    // "chargingactive" et al. are uncatalogued raw tokens; show a clean label.
    if (CHARGING_ACTIVE_STATES.has(raw)) {
      return this._t("is_charging");
    }
    return this._fmt(st);
  }

  _staleClass(iso) {
    const rel = iso ? (Date.now() - new Date(iso).getTime()) / 3600000 : 999;
    return rel > 12 ? "dot--stale" : "dot--live";
  }

  _imageUrl(entityId) {
    const st = this._st(entityId);
    if (!st || !st.attributes) return null;
    // entity_picture carries a signed, cache-busted access token URL.
    return st.attributes.entity_picture || null;
  }

  /** The configurator turntable for the hero, or null until it can be shown.
   *
   * Configure -> Vehicle image set to BMW's configurator gives the image entity
   * a `configurator_spin` attribute: the path of one sprite holding every frame
   * in a grid, served by the integration behind HA's login, plus the same spin
   * with the lights on and as the night scene. The hero shows the night scene
   * after sunset at home, the lights-on spin while the car reports its lights
   * on, and the plain one otherwise; all three share their frames, so the angle
   * survives the swap. Until a path is signed, the entity's own picture shows,
   * which is a still of the spin. */
  _spinInfo(entityId, entities = []) {
    const st = this._st(entityId);
    const spin = st && st.attributes && st.attributes.configurator_spin;
    if (!spin || !spin.path || !(spin.frames > 1) || !(spin.columns > 0)) return null;
    const sun = this._st("sun.sun");
    const lights = this._byDescriptor(entities, "vehicle.body.lights.isRunningOn");
    const lightsOn = !!lights && /^(on|true)$/i.test(String(lights.state));
    let variant = "day";
    if (spin.night_path && sun && sun.state === "below_horizon") variant = "night";
    else if (spin.lights_path && lightsOn) variant = "lights";
    const path = { day: spin.path, lights: spin.lights_path, night: spin.night_path }[variant];
    const url = this._signedUrl(path) || (variant !== "day" ? this._signedUrl(spin.path) : null);
    if (!url) return null;
    return {
      url,
      variant,
      columns: Number(spin.columns),
      rows: Number(spin.rows) || 1,
      frames: Number(spin.frames),
    };
  }

  /** The interior panorama (six cube faces in one image), once signed. */
  _interiorInfo(entityId) {
    const st = this._st(entityId);
    const pano = st && st.attributes && st.attributes.configurator_interior;
    if (!pano || !pano.path || !Array.isArray(pano.faces) || pano.faces.length !== 6) return null;
    const url = this._signedUrl(pano.path);
    return url
      ? { url, faces: pano.faces, columns: Number(pano.columns) || 3, rows: Number(pano.rows) || 2 }
      : null;
  }

  /** A signed URL for one of the integration's picture paths, or null while it
   * is being signed (an <img> or CSS background can't send HA's login). Kept
   * until shortly before the signature expires. */
  _signedUrl(path) {
    if (!path) return null;
    const signed = (this._spinSigned || {})[path];
    if (signed && signed.until >= Date.now()) return signed.url;
    this._signSpin(path);
    return null;
  }

  _signSpin(path) {
    const hass = this._hass;
    if (!hass || typeof hass.callWS !== "function") return;
    this._spinPending = this._spinPending || {};
    if (this._spinPending[path]) return;
    this._spinPending[path] = true;
    const expires = 24 * 3600;
    hass
      .callWS({ type: "auth/sign_path", path, expires })
      .then((res) => {
        this._spinSigned = this._spinSigned || {};
        this._spinSigned[path] = {
          url: hass.hassUrl ? hass.hassUrl(res.path) : res.path,
          until: Date.now() + (expires - 600) * 1000,
        };
        this._sig = null; // repaint with the picture
        this._render();
      })
      .catch(() => {
        /* the still keeps showing */
      })
      .finally(() => {
        delete this._spinPending[path];
      });
  }

  /** What the photo should say about the car right now: badges for anything
   * open, unlocked or charging, and the top-down drawing as an inset when
   * something is open, so it's clear where. Only drawn over the configurator
   * photo; the plain picture keeps the hero as it always was. */
  _heroState(entities, charging) {
    const m = this._closureModel(entities);
    const chips = [];
    const doorsOpen = Object.values(m.doors).filter((d) => d.open).length;
    if (doorsOpen) {
      chips.push({
        icon: "mdi:car-door",
        color: m.ALERT,
        label:
          doorsOpen === 1
            ? this._t("hs_door_open")
            : this._t("hs_doors_open", { n: doorsOpen }),
      });
    }
    if (m.trunk && m.trunk.open) {
      chips.push({ icon: "mdi:car-back", color: m.ALERT, label: this._t("hs_trunk_open") });
    }
    if (m.hood && m.hood.open) {
      chips.push({ icon: "mdi:car-select", color: m.ALERT, label: this._t("hs_hood_open") });
    }
    const glassOpen =
      Object.values(m.windows).some((w) => w.open) ||
      (m.sunroof && m.sunroof.open) ||
      (m.rearWindow && m.rearWindow.open);
    if (glassOpen) {
      chips.push({ icon: "mdi:window-open-variant", color: m.WARN, label: this._t("hs_windows_open") });
    }
    if (m.lockId && m.lock.key === "unlocked") {
      chips.push({ icon: "mdi:lock-open-variant", color: m.ALERT, label: m.lock.label });
    }
    if (charging) {
      chips.push({ icon: "mdi:ev-station", color: "var(--bmw-high)", label: this._t("hs_charging") });
    }
    const inset = m.openItems.length
      ? this._carSvgClosures({
          doors: m.doors,
          windows: m.windows,
          hood: m.hood,
          trunk: m.trunk,
          rearWindow: m.rearWindow,
          sunroof: m.sunroof,
          lock: m.lock,
          lockId: m.lockId,
          colors: { ALERT: m.ALERT, WARN: m.WARN, OK: m.OK },
          car: this._carShape(entities),
        })
      : "";
    return { chips, inset };
  }

  _heroStateHtml(state) {
    if (!state || (!state.chips.length && !state.inset)) return "";
    const shown = state.chips.slice(0, 3);
    const more = state.chips.length - shown.length;
    return `
          <div class="hero__chips">
            ${shown
              .map(
                (c) =>
                  `<span class="hero__chip" style="--chip:${c.color}"><ha-icon icon="${c.icon}"></ha-icon>${this._esc(c.label)}</span>`
              )
              .join("")}
            ${more > 0 ? `<span class="hero__chip">+${more}</span>` : ""}
          </div>
          ${state.inset ? `<div class="hero__inset">${state.inset}</div>` : ""}`;
  }

  /** The interior panorama: six faces on the inside of a CSS cube, turned by
   * dragging. BMW's interior is its default trim (CarData doesn't say which
   * upholstery a car has), and the caption says so. */
  _panoHtml(pano) {
    const look = this._panoLook || { yaw: 0, pitch: 0 };
    // The camera sits in the middle of the cube, so every face is pushed half a
    // side away from it (-Z) and then turned into place, front side inward.
    const away = "translateZ(calc(var(--half) * -1))";
    const place = {
      front: away,
      back: `rotateY(180deg) ${away}`,
      left: `rotateY(90deg) ${away}`,
      right: `rotateY(-90deg) ${away}`,
      up: `rotateX(-90deg) ${away}`,
      down: `rotateX(90deg) ${away}`,
    };
    const faces = pano.faces
      .map((face, i) => {
        const col = i % pano.columns;
        const row = Math.floor(i / pano.columns);
        const x = pano.columns > 1 ? (col / (pano.columns - 1)) * 100 : 0;
        const y = pano.rows > 1 ? (row / (pano.rows - 1)) * 100 : 0;
        return `<div class="pano__face" style="background-image:url('${this._esc(pano.url)}');background-size:${pano.columns * 100}% ${pano.rows * 100}%;background-position:${x}% ${y}%;transform:${place[face] || ""}"></div>`;
      })
      .join("");
    return `<div class="pano-wrap"><div class="pano" role="img" aria-label="${this._esc(this._t("hs_interior"))}">
            <div class="pano__cube" style="transform:translateZ(var(--half)) rotateX(${look.pitch}deg) rotateY(${look.yaw}deg)">${faces}</div>
          </div></div>
          <span class="hero__caption">${this._esc(this._t("hs_default_trim"))}</span>`;
  }

  _wirePano() {
    const el = this.shadowRoot.querySelector(".pano");
    const cube = el && el.querySelector(".pano__cube");
    if (!el || !cube) return;
    // Half the hero's width is the cube's half side and the viewing distance
    // (a 90-degree view across); CSS works it out (`50cqw`), so nothing here
    // depends on the card having been laid out yet.
    const size = () => el.getBoundingClientRect().width;
    let start = null;
    el.addEventListener("pointerdown", (ev) => {
      start = { x: ev.clientX, y: ev.clientY, look: { ...(this._panoLook || { yaw: 0, pitch: 0 }) } };
      if (el.setPointerCapture) el.setPointerCapture(ev.pointerId);
    });
    el.addEventListener("pointermove", (ev) => {
      if (!start) return;
      const k = 90 / Math.max(size(), 1); // a hero's width of drag turns 90 degrees
      const yaw = start.look.yaw + (ev.clientX - start.x) * k;
      const pitch = Math.max(-80, Math.min(80, start.look.pitch - (ev.clientY - start.y) * k));
      this._panoLook = { yaw, pitch };
      cube.style.transform = `translateZ(var(--half)) rotateX(${pitch}deg) rotateY(${yaw}deg)`;
    });
    const end = () => {
      start = null;
    };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
  }

  // Frame 4 is BMW's front three-quarter view, the still the entity shows.
  _spinFrameNow(spin) {
    const f = Number.isInteger(this._spinFrame) ? this._spinFrame : 4;
    return ((f % spin.frames) + spin.frames) % spin.frames;
  }

  _spinPos(spin, frame) {
    const col = frame % spin.columns;
    const row = Math.floor(frame / spin.columns);
    const x = spin.columns > 1 ? (col / (spin.columns - 1)) * 100 : 0;
    const y = spin.rows > 1 ? (row / (spin.rows - 1)) * 100 : 0;
    return `${x}% ${y}%`;
  }

  _spinHtml(spin, name) {
    const frame = this._spinFrameNow(spin);
    const style = [
      `background-image:url('${this._esc(spin.url)}')`,
      `background-size:${spin.columns * 100}% ${spin.rows * 100}%`,
      `background-position:${this._spinPos(spin, frame)}`,
    ].join(";");
    return `<div class="hero__spin" role="img" aria-label="${this._esc(name)}" title="${this._esc(
      this._t("spin_hint")
    )}" style="${style}"></div>
          <ha-icon class="hero__spin-icon" icon="mdi:rotate-3d-variant"></ha-icon>`;
  }

  /** Drag (mouse or finger) across the hero to turn the car. The frame lives
   * on the card, not in the DOM, so a repaint keeps the angle. */
  _wireSpin(spin) {
    const el = this.shadowRoot.querySelector(".hero__spin");
    if (!el) return;
    const step = 14; // pixels of drag per 10-degree frame
    let startX = null;
    let startFrame = 0;
    el.addEventListener("pointerdown", (ev) => {
      startX = ev.clientX;
      startFrame = this._spinFrameNow(spin);
      if (el.setPointerCapture) el.setPointerCapture(ev.pointerId);
    });
    el.addEventListener("pointermove", (ev) => {
      if (startX === null) return;
      // Dragging right turns the car to the right, as on BMW's configurator.
      const frame = startFrame - Math.round((ev.clientX - startX) / step);
      this._spinFrame = ((frame % spin.frames) + spin.frames) % spin.frames;
      el.style.backgroundPosition = this._spinPos(spin, this._spinFrame);
    });
    const end = () => {
      startX = null;
    };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
  }

  /** The car's real paint as `#rrggbb`, when the configurator picture is on. */
  _carPaint(entities) {
    const cfg = this._config || {};
    const id = cfg.image || entities.find((e) => e.startsWith("image."));
    const st = id && this._st(id);
    const paint = st && st.attributes && st.attributes.paint_color;
    return typeof paint === "string" && /^#[0-9a-f]{6}$/i.test(paint) ? paint.toLowerCase() : null;
  }

  _renderCluster(deviceId, entities) {
    const slug = this._config.cluster;
    const rows = entities
      .map((id) => this._st(id))
      .filter((st) => st && st.attributes && st.attributes.cluster === slug)
      .sort((a, b) =>
        (a.attributes.friendly_name || a.entity_id).localeCompare(
          b.attributes.friendly_name || b.entity_id
        )
      );

    // BMW files Check Control messages ("washer fluid low") under usage-based
    // data, yet they are what a driver means by a vehicle event: the events
    // cluster itself holds only two teleservice timestamps, which most cars
    // never send. So that view leads with the messages.
    const cc = slug === "events" ? this._checkControl(entities) : null;
    const messages = cc ? cc.items : [];

    const label = this._config.title || this._clusterLabel(slug);
    const icon = CLUSTER_ICONS[slug] || "mdi:car";
    const name = this._deviceName(deviceId);
    const count = rows.length + messages.length;

    const sig = this._signature({
      m: "cl",
      lang: _lang(this._hass),
      slug,
      rows: rows.map((s) => [s.entity_id, s.state]),
      cc: cc && [cc.id, cc.items, cc.resolved, cc.updated, cc.unit, this._ccExpanded, this._ccEarlier],
    });
    if (sig === this._sig) return;
    this._sig = sig;

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        <div class="chead">
          <ha-icon icon="${icon}"></ha-icon>
          <div class="chead__text">
            <span class="chead__title">${this._esc(label)}</span>
            <span class="chead__sub">${this._esc(name)} · ${count} ${this._t(count === 1 ? "value" : "values")}</span>
          </div>
        </div>
        ${
          cc
            ? `<div class="list__head">${this._t("cc_title")}</div>
              ${
                messages.length
                  ? `<div class="list">${messages.map((msg) => this._ccRow(cc, msg)).join("")}</div>`
                  : `<div class="empty empty--inline">${this._t("cc_none")}</div>`
              }
              ${this._ccEarlierBlock(cc)}
              ${rows.length ? `<div class="list__head">${this._t("cc_teleservice")}</div>` : ""}`
            : ""
        }
        ${
          rows.length
            ? `<div class="list">
                ${rows
                  .map((st) => {
                    return `<button class="item" data-entity="${st.entity_id}">
                      <span class="item__name" title="${this._esc(st.attributes.friendly_name || st.entity_id)}">${this._shortName(st, name)}</span>
                      <span class="item__val">${this._fmt(st)}</span>
                    </button>`;
                  })
                  .join("")}
              </div>`
            : cc
              ? ""
              : `<div class="empty">${this._t("no_cluster_entities", { label: `<b>${this._esc(label)}</b>` })}</div>`
        }
      </ha-card>
    `;
    this._wireTaps();
    // A message is a list item, not an entity: it expands in place, the way a
    // charging session or a trip does, instead of opening more-info.
    this.shadowRoot.querySelectorAll("[data-cc]").forEach((el) => {
      el.addEventListener("click", () => this._toggleCc(el.getAttribute("data-cc")));
    });
    this.shadowRoot.querySelectorAll("[data-cc-earlier]").forEach((el) => {
      el.addEventListener("click", () => this._toggleCcEarlier());
    });
  }

  _toggleCcEarlier() {
    this._ccEarlier = !this._ccEarlier;
    this._sig = null;
    this._render();
  }

  /** Messages the car no longer reports: folded away behind one row, so a
   * cleared warning stops looking current but can still be looked up. */
  _ccEarlierBlock(cc) {
    if (!cc.resolved.length) return "";
    const open = !!this._ccEarlier;
    return `<button class="cc-earlier" data-cc-earlier aria-expanded="${open}">
        <ha-icon icon="${open ? "mdi:chevron-up" : "mdi:chevron-down"}"></ha-icon>
        <span>${this._t("cc_earlier", { n: cc.resolved.length })}</span>
      </button>
      ${open ? `<div class="list list--past">${cc.resolved.map((msg) => this._ccRow(cc, msg, true)).join("")}</div>` : ""}`;
  }

  _toggleCc(key) {
    this._ccExpanded = this._ccExpanded === key ? null : key;
    this._sig = null; // force a repaint with the new expansion state
    this._render();
  }

  /** The Check Control sensor and its messages; null on a car without one. */
  _checkControl(entities) {
    const st = this._byDescriptor(entities, "vehicle.status.checkControlMessages");
    if (!st) return null;
    const attrs = st.attributes || {};
    const odometer = this._byDescriptor(entities, "vehicle.vehicle.travelledDistance");
    const messages = (list) =>
      Array.isArray(list) ? list.filter((msg) => msg && typeof msg === "object") : [];
    return {
      id: st.entity_id,
      items: messages(attrs.items),
      resolved: messages(attrs.resolved),
      updated: attrs.timestamp,
      unit: (odometer && odometer.attributes && odometer.attributes.unit_of_measurement) || "km",
    };
  }

  /** A Check Control field, or null for BMW's placeholders ("-", "NULL"). */
  _ccField(value) {
    if (value == null) return null;
    const text = String(value).trim();
    return text === "" || text === "-" || text.toUpperCase() === "NULL" ? null : text;
  }

  /** One Check Control message. BMW sends the text in English on every install,
   * and on the maintainer's i5 with no title and no date. */
  _ccRow(cc, msg, past = false) {
    const ident = String(msg.id != null ? msg.id : msg.text || "");
    // A message can be current and in the history of an earlier occurrence
    // only in turn, never at once -- but keep the keys apart all the same.
    const key = past ? `past:${ident}:${msg.resolved_at || ""}` : ident;
    const isOpen = this._ccExpanded === key;
    const title = this._ccField(msg.title);
    const text = this._ccField(msg.text);
    const head = title || text || this._ccField(msg.messageType) || "—";
    const day = past ? msg.resolved_at : this._ccField(msg.date);
    const when = day ? this._fmtDay(day) : "";
    const icon = past ? "mdi:check-circle-outline" : "mdi:alert-circle-outline";
    return `<div class="cc${past ? " cc--past" : ""}${isOpen ? " is-open" : ""}">
      <button class="item item--msg" data-cc="${this._attr(key)}" aria-expanded="${isOpen}">
        <ha-icon class="item__icon" icon="${icon}"></ha-icon>
        <span class="item__body">
          <span class="item__text${isOpen ? "" : " item__clamp"}">${this._esc(head)}</span>
          ${title && text && !isOpen ? `<span class="item__sub item__clamp">${this._esc(text)}</span>` : ""}
        </span>
        ${when ? `<span class="item__val">${when}</span>` : ""}
      </button>
      ${isOpen ? this._ccDetail(cc, msg, title && text ? text : "", past) : ""}
    </div>`;
  }

  _ccDetail(cc, msg, body, past = false) {
    const description = this._ccField(msg.description);
    const facts = [];
    // Despite its name this is no distance left: on the maintainer's i5 it was
    // the odometer at the start of the drive on which the car last showed the
    // message (19 596, then 19 599 a drive later, while the car read 19 624).
    const mileage = this._ccField(msg.unitOfLengthRemaining);
    if (mileage && Number.isFinite(Number(mileage))) {
      facts.push([this._t("cc_mileage"), `${this._dec(Number(mileage), 0)} ${this._esc(cc.unit)}`]);
    }
    if (past) {
      // The sensor's own timestamp belongs to the current list, not to this one.
      if (msg.last_reported) facts.push([this._t("cc_reported"), this._fmtDay(msg.last_reported)]);
      if (msg.resolved_at) facts.push([this._t("cc_gone"), this._fmtDay(msg.resolved_at)]);
    } else {
      const sent = this._relTime(cc.updated);
      if (sent) facts.push([this._t("cc_sent"), sent]);
    }
    const code = [this._ccField(msg.messageType), this._ccField(msg.id)].filter(Boolean).join(" ");
    if (code) facts.push([this._t("cc_code"), this._esc(code)]);

    const factRow = facts
      .map(
        ([k, v]) =>
          `<div class="chg__fact"><span class="chg__fact-lbl">${k}</span><span class="chg__fact-val">${v}</span></div>`
      )
      .join("");
    return `
      <div class="chg__detail">
        ${body ? `<p class="cc__text">${this._esc(body)}</p>` : ""}
        ${description ? `<p class="cc__text">${this._esc(description)}</p>` : ""}
        ${factRow ? `<div class="chg__facts">${factRow}</div>` : ""}
      </div>
    `;
  }

  /** Localized display label for a catalogue cluster slug. */
  _clusterLabel(slug) {
    return this._t("cl_" + slug, null, slug);
  }

  _shortName(st, deviceName) {
    let n = st.attributes.friendly_name || st.entity_id;
    if (deviceName && n.startsWith(deviceName + " ")) n = n.slice(deviceName.length + 1);
    return this._esc(n);
  }

  /* ---- charging history ------------------------------------------------- */

  /** Charging, battery health and efficiency all describe a high-voltage
   * battery. On a petrol or diesel car each would sit empty for good and read
   * like a fault, so say why instead -- before any service is called. Returns
   * whether it painted. */
  _renderIceNotice(deviceId, entities) {
    if (this._drivetrain(entities) !== "ice") return false;
    const name = this._config.title || this._deviceName(deviceId);
    const sig = this._signature({ m: "ice", lang: _lang(this._hass), name });
    if (sig === this._sig) return true;
    this._renderMessage(
      this._esc(this._t("ice_view_title")),
      this._esc(this._t("ice_view_body", { name }))
    );
    this._sig = sig;
    return true;
  }

  _renderCharging(deviceId, entities) {
    const vin = this._deviceVin(deviceId);
    if (!vin) {
      this._renderMessage(this._t("no_vehicle_title"), this._t("no_vehicle_body"));
      return;
    }
    if (this._renderIceNotice(deviceId, entities)) return;

    const entryId = this._deviceEntry(deviceId);

    // Sessions come from a service response, not entity state, so they can't be
    // read synchronously off hass. Fetch once, then only re-fetch when a new
    // session is likely: gate on the summary sensor's last_changed rather than
    // polling, so a plain hass tick never hits the service.
    const trigSt = entities
      .map((id) => this._st(id))
      .find(
        (st) =>
          st &&
          st.attributes &&
          (st.attributes.descriptor === "charging_cost_session" ||
            st.attributes.descriptor === "charging_energy_month")
      );
    const trigger = trigSt ? trigSt.last_changed : "";

    const month = this._month("charging");
    const cache = this._chg;
    const current =
      cache && cache.vin === vin && cache.trigger === trigger && cache.month === month;
    if (!current || (!cache.data && !cache.loading)) {
      this._chg = {
        vin,
        trigger,
        month,
        data: current && cache ? cache.data : null,
        loading: true,
        error: false,
      };
      this._fetchCharging(vin, entryId, month);
    }

    this._paintCharging(deviceId, entities);
  }

  _fetchCharging(vin, entryId, month) {
    const req = this._chg;
    const bounds = this._monthBounds(month);
    this._hass
      .callService(
        "bavariandata",
        "get_charging_sessions",
        this._withEntry({ vin, from: bounds.from, to: bounds.to }, entryId),
        undefined,
        false,
        true
      )
      .then((res) => {
        // Ignore a response for a request we've already superseded.
        if (
          !this._chg ||
          this._chg.vin !== vin ||
          this._chg.trigger !== req.trigger ||
          this._chg.month !== month
        )
          return;
        const sessions = (res && res.response && res.response.sessions) || [];
        this._chg = { ...this._chg, data: sessions, loading: false, error: false };
        this._render();
      })
      .catch(() => {
        if (!this._chg || this._chg.vin !== vin || this._chg.trigger !== req.trigger)
          return;
        this._chg = { ...this._chg, loading: false, error: true };
        this._render();
      });
  }

  _paintCharging(deviceId, entities) {
    const name = this._config.title || this._deviceName(deviceId);
    const state = this._chg || {};
    const sessions = state.data;
    const month = this._month("charging");
    const isCurrent = month === this._monthKey();
    // The summary band is fed by the monthly sensors, which only ever describe
    // the month happening now. Showing it above a list of last March would put
    // two different periods in one card, so it goes with the current month.
    const summary = isCurrent ? this._chargingSummary(entities) : null;
    const expanded = this._chgExpanded || null;

    const sig = this._signature({
      m: "chg",
      lang: _lang(this._hass),
      name,
      month,
      loading: state.loading && !sessions,
      error: state.error,
      summary,
      expanded,
      rows: (sessions || []).map((s) => [s.start, s.energy_kwh, s.cost && s.cost.amount]),
    });
    if (sig === this._sig) return;
    this._sig = sig;

    let body;
    if (state.error) {
      body = `<div class="empty">${this._t("ch_error")}</div>`;
    } else if (!sessions && state.loading) {
      body = `<div class="empty">${this._t("ch_loading")}</div>`;
    } else if (!sessions || !sessions.length) {
      body = `<div class="empty">${this._t(
        isCurrent ? "ch_empty" : "ch_empty_month"
      )}</div>`;
    } else {
      body = `<div class="chg__list">${sessions
        .map((s) => this._chargingRow(s, expanded))
        .join("")}</div>`;
    }

    const count = sessions ? sessions.length : 0;
    const countLabel =
      count === 1
        ? this._t("ch_session_one")
        : this._t("ch_session_many", { n: count });

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        <div class="chead">
          <ha-icon icon="mdi:ev-station"></ha-icon>
          <div class="chead__text">
            <span class="chead__title">${this._esc(this._config.title || this._t("ch_title"))}</span>
            <span class="chead__sub">${this._esc(name)}${count ? " · " + countLabel : ""}</span>
          </div>
          ${this._exportButtons("charging")}
        </div>
        ${this._monthNav("charging")}
        ${summary ? this._chargingSummaryBand(summary) : ""}
        ${body}
      </ha-card>
    `;
    this._wireChargingTaps();
    this._wireMonthNav();
  }

  /** "This month" figures, read from the summary sensors when they exist. */
  _chargingSummary(entities) {
    let cost = null;
    let energy = null;
    for (const id of entities) {
      const st = this._st(id);
      const d = st && st.attributes && st.attributes.descriptor;
      if (d === "charging_cost_month") cost = st;
      else if (d === "charging_energy_month") energy = st;
    }
    if (!cost && !energy) return null;
    return {
      cost: cost ? this._fmt(cost) : null,
      energy: energy ? this._fmt(energy) : null,
    };
  }

  _chargingSummaryBand(summary) {
    const cells = [];
    if (summary.energy) {
      cells.push(
        `<div class="chg__stat"><span class="chg__stat-val">${summary.energy}</span><span class="chg__stat-lbl">${this._t("ch_month")}</span></div>`
      );
    }
    if (summary.cost) {
      cells.push(
        `<div class="chg__stat"><span class="chg__stat-val">${summary.cost}</span><span class="chg__stat-lbl">${this._t("ch_month")}</span></div>`
      );
    }
    if (!cells.length) return "";
    return `<div class="chg__summary">${cells.join("")}</div>`;
  }

  _chargingRow(session, expanded) {
    const id = session.start || "";
    const isOpen = expanded === id;
    const date = this._fmtSessionDate(session.start);
    // Prefer the measured grid figure (imported / enriched sessions carry only
    // that); fall back to the battery-side energy for live-only sessions.
    const energyKwh = session.grid_kwh != null ? session.grid_kwh : session.energy_kwh;
    const energy = energyKwh != null ? `${this._dec(energyKwh, 1)} kWh` : "—";
    const soc = this._socArc(session);
    const badge = this._locationBadge(session);
    const partial =
      session.cost && session.cost.partial
        ? `<span class="chg__tag chg__tag--warn">${this._t("ch_partial")}</span>`
        : "";
    const ongoing = session.end
      ? ""
      : `<span class="chg__tag">${this._t("ch_ongoing")}</span>`;
    const solar = this._solarTag(session);

    return `
      <div class="chg__session${isOpen ? " is-open" : ""}">
        <button class="chg__row" data-session="${this._attr(id)}">
          <span class="chg__row-main">
            <span class="chg__date">${date}</span>
            <span class="chg__meta">${soc}${badge}${solar}${ongoing}${partial}</span>
          </span>
          <span class="chg__figures">
            <span class="chg__energy chg__energy--lead">${energy}</span>
          </span>
        </button>
        ${isOpen ? this._chargingDetail(session) : ""}
      </div>
    `;
  }

  // How much of this charge came off the roof, as a compact tag. Absent --
  // rather than "0 %" -- when the energy could not be attributed at all: no PV
  // and grid sensors configured, or a charge from before they were.
  _solarTag(session) {
    const pct = session.energy_mix && session.energy_mix.solar_percent;
    if (pct == null) return "";
    return `<span class="chg__tag chg__tag--sun">\u2600 ${this._dec(
      pct,
      0
    )}% ${this._t("ch_solar")}</span>`;
  }

  // The breakdown behind that tag, in grid-side kWh. Sources with nothing in
  // them are left out, so a night charge reads "Grid 11.2 kWh" and not a list
  // of zeroes.
  _mixLabel(mix) {
    if (!mix) return "";
    const parts = [];
    for (const [key, label] of [
      ["pv", "ch_mix_pv"],
      ["battery", "ch_mix_battery"],
      ["grid", "ch_mix_grid"],
      ["unknown", "ch_mix_unknown"],
    ]) {
      const value = mix[key];
      if (value == null || !(value > 0)) continue;
      parts.push(`${this._t(label)} ${this._dec(value, 1)}`);
    }
    return parts.length ? `${parts.join(" · ")} kWh` : "";
  }

  _chargingDetail(session) {
    const chart = this._powerCurveSvg(session.power_curve);
    const facts = [];
    if (session.peak_power_kw != null) {
      facts.push([this._t("ch_peak"), `${this._dec(session.peak_power_kw, 1)} kW`]);
    }
    const avg = this._avgPowerKw(session);
    if (avg != null) facts.push([this._t("ch_avg"), `${this._dec(avg, 1)} kW`]);
    // duration_s isn't in the service payload; derive it from the timestamps.
    const dur = this._durationLabel(session);
    if (dur) facts.push([this._t("ch_duration"), dur]);
    if (session.grid_kwh != null) {
      facts.push([this._t("ch_grid"), `${this._dec(session.grid_kwh, 1)} kWh`]);
    }
    // Cost moved off the collapsed row to here, so it's kept for tariff users
    // without competing with the kWh for the row's headline figure.
    if (session.cost && session.cost.amount != null) {
      facts.push([this._t("ch_cost"), this._fmtCost(session.cost)]);
    }
    const mixLabel = this._mixLabel(session.energy_mix);
    if (mixLabel) facts.push([this._t("ch_mix"), mixLabel]);

    const factRow = facts
      .map(
        ([k, v]) =>
          `<div class="chg__fact"><span class="chg__fact-lbl">${k}</span><span class="chg__fact-val">${v}</span></div>`
      )
      .join("");

    return `
      <div class="chg__detail">
        ${chart}
        <div class="chg__facts">${factRow}</div>
      </div>
    `;
  }

  /** Inline SVG step chart of the [seconds, kW] power curve. No dependencies.
   *
   * Stepped, not interpolated: each sampled power holds until the next sample
   * arrives, which is what the data means (an average over a block), rather than
   * drawing a diagonal ramp between two readings that never happened. */
  _powerCurveSvg(curve) {
    if (!Array.isArray(curve) || curve.length < 2) return "";
    const W = 260;
    const H = 64;
    const pad = 4;
    const xs = curve.map((p) => p[0]);
    const ys = curve.map((p) => p[1]);
    const xMin = Math.min(...xs);
    const xMax = Math.max(...xs);
    const yMax = Math.max(...ys, 0.1);
    const spanX = xMax - xMin || 1;
    const sx = (x) => pad + ((x - xMin) / spanX) * (W - 2 * pad);
    const sy = (y) => H - pad - (y / yMax) * (H - 2 * pad);
    const pts = curve.map((p) => [sx(p[0]), sy(p[1])]);
    const stepped = [];
    for (let i = 0; i < pts.length; i++) {
      stepped.push(pts[i]);
      // Hold this reading's level across to the next sample's time before
      // stepping to the new level.
      if (i < pts.length - 1) stepped.push([pts[i + 1][0], pts[i][1]]);
    }
    const line = stepped
      .map(([x, y]) => `${this._px(x)},${this._px(y)}`)
      .join(" ");
    const area = `${pad},${H - pad} ${line} ${W - pad},${H - pad}`;
    return `
      <svg class="chg__chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img">
        <polygon points="${area}" class="chg__chart-fill"></polygon>
        <polyline points="${line}" class="chg__chart-line"></polyline>
        <text x="${pad}" y="10" class="chg__chart-max">${this._dec(yMax, 1)} kW</text>
      </svg>
    `;
  }

  _locationBadge(session) {
    const loc = session.location || {};
    const zone = loc.zone;
    const assumed = session.location_assumed;
    // A resolved non-home zone shows its own name; everything else is Home vs
    // Away, with "assumed" spelled out when we had no GPS and fell back to home.
    let label;
    let cls = "chg__badge";
    if (assumed) {
      label = `${this._t("ch_home")} · ${this._t("ch_assumed")}`;
      cls += " chg__badge--assumed";
    } else if (zone && !/^home$/i.test(zone)) {
      label = zone;
      cls += " chg__badge--away";
    } else if (zone) {
      label = this._t("ch_home");
      cls += " chg__badge--home";
    } else if (loc.address) {
      // No matching zone, but BMW gave us the address of a public charge.
      label = loc.address;
      cls += " chg__badge--away";
    } else {
      label = this._t("ch_public");
      cls += " chg__badge--away";
    }
    return `<span class="${cls}">${this._esc(label)}</span>`;
  }

  _socArc(session) {
    const a = session.soc_start;
    const b = session.soc_end;
    if (a == null && b == null) return "";
    const from = a == null ? "?" : Math.round(a);
    const to = b == null ? "?" : Math.round(b);
    return `<span class="chg__soc">${from}→${to}%</span>`;
  }

  _fmtCost(cost) {
    if (!cost || cost.amount == null) {
      return `<span class="chg__cost--none">${this._t("ch_no_cost")}</span>`;
    }
    const locale = _numberLocale(this._hass);
    try {
      if (locale === null) throw new Error("number format: none");
      return this._esc(
        new Intl.NumberFormat(locale, {
          style: "currency",
          currency: cost.currency || "EUR",
          maximumFractionDigits: 2,
        }).format(cost.amount)
      );
    } catch (e) {
      return this._esc(
        `${this._dec(cost.amount, 2)} ${cost.currency || ""}`.trim()
      );
    }
  }

  _fmtSessionDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return this._esc(iso);
    try {
      return d.toLocaleString(_lang(this._hass), {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch (e) {
      return d.toISOString().slice(0, 16).replace("T", " ");
    }
  }

  _durationLabel(session) {
    // A car that reports only once parked says where a trip went, never when
    // it set off: its start is the arrival, and "0 min" would be made up.
    if (!session.start || !session.end || session.duration_unknown) return null;
    const ms = new Date(session.end).getTime() - new Date(session.start).getTime();
    if (!(ms > 0)) return null;
    const mins = Math.round(ms / 60000);
    if (mins < 60) return `${mins} min`;
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    return m ? `${h} h ${m} min` : `${h} h`;
  }

  _avgPowerKw(session) {
    const energyKwh = session.grid_kwh != null ? session.grid_kwh : session.energy_kwh;
    if (energyKwh == null || !session.start || !session.end) return null;
    const hours =
      (new Date(session.end).getTime() - new Date(session.start).getTime()) / 3600000;
    if (!(hours > 0)) return null;
    return energyKwh / hours;
  }

  /* ---- month window ------------------------------------------------------
   *
   * The trips and charging views show one calendar month at a time. History is
   * kept for two years, so the alternative is a list that grows without end and
   * that nothing on screen describes — the "month in review" band above it was
   * always month-scoped, so an unbounded list below it made the card disagree
   * with itself. Paging by month keeps every record reachable and lets the
   * summary, the list and the export all speak about the same period.
   */

  /** "YYYY-MM" for a Date; the current month when given nothing. */
  _monthKey(date) {
    const d = date || new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  }

  /** The month a view is showing, defaulting to the current one. */
  _month(kind) {
    return (kind === "trips" ? this._trpMonth : this._chgMonth) || this._monthKey();
  }

  _shiftMonth(key, delta) {
    const [y, m] = key.split("-").map(Number);
    const d = new Date(y, m - 1 + delta, 1);
    return this._monthKey(d);
  }

  /** First and last instant of a month, as the `from`/`to` the services take. */
  _monthBounds(key) {
    const [y, m] = key.split("-").map(Number);
    return {
      from: `${key}-01`,
      // The services treat `to` as inclusive of the day, so the last day of the
      // month is the bound — computed as "day 0 of next month" to dodge leap
      // years and 30/31-day arithmetic entirely.
      to: `${key}-${String(new Date(y, m, 0).getDate()).padStart(2, "0")}`,
    };
  }

  _monthLabel(key) {
    const [y, m] = key.split("-").map(Number);
    const d = new Date(y, m - 1, 1);
    try {
      const lang = _lang(this._hass);
      return d.toLocaleDateString(lang === "en" ? "en-GB" : lang, {
        month: "long",
        year: "numeric",
      });
    } catch (_e) {
      return key;
    }
  }

  /** ‹ August 2026 › — forward stops at the current month, which is the newest
   * one that can hold anything. */
  _monthNav(kind) {
    const key = this._month(kind);
    const atNewest = key >= this._monthKey();
    return `
      <div class="mnav">
        <button class="mnav__btn" data-month-kind="${kind}" data-month-step="-1"
                title="${this._t("mn_prev")}" aria-label="${this._t("mn_prev")}">
          <ha-icon icon="mdi:chevron-left"></ha-icon>
        </button>
        <span class="mnav__label">${this._monthLabel(key)}</span>
        <button class="mnav__btn" data-month-kind="${kind}" data-month-step="1"
                title="${this._t("mn_next")}" aria-label="${this._t("mn_next")}"
                ${atNewest ? "disabled" : ""}>
          <ha-icon icon="mdi:chevron-right"></ha-icon>
        </button>
      </div>`;
  }

  _wireMonthNav() {
    this.shadowRoot.querySelectorAll("[data-month-step]").forEach((el) => {
      el.addEventListener("click", (ev) => {
        ev.stopPropagation(); // the rows underneath are tappable
        if (el.hasAttribute("disabled")) return;
        const kind = el.getAttribute("data-month-kind");
        const step = Number(el.getAttribute("data-month-step"));
        const next = this._shiftMonth(this._month(kind), step);
        if (next > this._monthKey()) return;
        if (kind === "trips") {
          this._trpMonth = next;
          // An expanded row belongs to the month it was opened in; carrying the
          // id across would leave a detail panel open on a trip not in the list.
          this._trpExpanded = null;
          this._trp = null;
        } else {
          this._chgMonth = next;
          this._chgExpanded = null;
          this._chg = null;
        }
        this._render();
      });
    });
  }

  /* ---- export (roadmap Phase 4) ----------------------------------------- */

  /** Header buttons for the charging and trips views. `kind` scopes the CSV. */
  _exportButtons(kind) {
    return `
      <div class="xbar">
        <button class="xbtn" data-export="csv" data-kind="${kind}"
                title="${this._t("ex_csv_hint")}">
          <ha-icon icon="mdi:file-delimited-outline"></ha-icon>${this._t("ex_csv")}
        </button>
        <button class="xbtn" data-export="html" data-kind="both"
                title="${this._t("ex_report_hint")}">
          <ha-icon icon="mdi:file-document-outline"></ha-icon>${this._t("ex_report")}
        </button>
      </div>`;
  }

  _wireExport() {
    this.shadowRoot.querySelectorAll("[data-export]").forEach((el) => {
      el.addEventListener("click", (ev) => {
        ev.stopPropagation(); // rows below are tappable too
        this._export(el.getAttribute("data-kind"), el.getAttribute("data-export"));
      });
    });
  }

  _export(kind, format) {
    const deviceId = this._resolveDeviceId();
    const vin = this._deviceVin(deviceId);
    // One export at a time: the button stays in the DOM across repaints, and a
    // double tap would otherwise download the same month twice.
    if (!vin || this._exporting) return;
    this._exporting = true;
    // Export what the user is looking at: paging back to March and hitting CSV
    // must not quietly hand over the current month instead.
    const month = this._month(
      this._config.view === "trips" ? "trips" : "charging"
    );
    this._hass
      .callService(
        "bavariandata",
        "export_history",
        this._withEntry(
          { vin, type: kind, format, month },
          this._deviceEntry(deviceId)
        ),
        undefined,
        false,
        true
      )
      .then((res) => {
        const files = (res && res.response && res.response.files) || [];
        const written = files.filter((f) => f && f.content && f.rows);
        if (!written.length) {
          this._notify(this._t("ex_empty"));
          return;
        }
        written.forEach((file) => this._download(file));
      })
      .catch(() => this._notify(this._t("ex_error")))
      .finally(() => {
        this._exporting = false;
      });
  }

  /** Hand the service's file content to the browser as a download. */
  _download(file) {
    const blob = new Blob([file.content], {
      type: `${file.mime || "text/plain"};charset=utf-8`,
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = file.filename || "bavariandata-export";
    document.body.appendChild(link);
    link.click();
    link.remove();
    // Revoking immediately can cancel the download in some browsers.
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  _notify(message) {
    this.dispatchEvent(
      new CustomEvent("hass-notification", {
        detail: { message },
        bubbles: true,
        composed: true,
      })
    );
  }

  _wireChargingTaps() {
    this._wireExport();
    this.shadowRoot.querySelectorAll("[data-session]").forEach((el) => {
      el.addEventListener("click", () => {
        const id = el.getAttribute("data-session");
        this._chgExpanded = this._chgExpanded === id ? null : id;
        this._sig = null; // force a repaint with the new expansion state
        const deviceId = this._resolveDeviceId();
        this._paintCharging(deviceId, this._deviceEntities(deviceId));
      });
    });
  }

  /* ---- trips (Fahrtenbuch) ---------------------------------------------- */

  _renderTrips(deviceId, entities) {
    const vin = this._deviceVin(deviceId);
    if (!vin) {
      this._renderMessage(this._t("no_vehicle_title"), this._t("no_vehicle_body"));
      return;
    }

    const entryId = this._deviceEntry(deviceId);

    // Like charging, trips come from services (a list + the month-in-review),
    // not entity state. Gate the fetch on the monthly-distance sensor's
    // last_changed so a plain hass tick never hits the services.
    const trigSt = this._byDescriptor(entities, "driving_distance_month");
    // A drive under way is not in the store, so the monthly figure doesn't move
    // while it happens: the trip flag's own writes are what refresh the
    // in-progress row. It rewrites at most once a minute while driving (the
    // integration throttles it), so this costs one local service call a minute.
    const tripSt = this._byDescriptor(entities, "trip_in_progress");
    const trigger = [
      trigSt ? trigSt.last_changed : "",
      tripSt ? `${tripSt.state}@${tripSt.last_updated}` : "",
    ].join("|");

    const month = this._month("trips");
    const cache = this._trp;
    const current =
      cache && cache.vin === vin && cache.trigger === trigger && cache.month === month;
    if (!current || (!cache.trips && !cache.loading)) {
      this._trp = {
        vin,
        trigger,
        month,
        entryId: entryId,
        trips: current && cache ? cache.trips : null,
        summary: current && cache ? cache.summary : null,
        open: current && cache ? cache.open : null,
        loading: true,
        error: false,
      };
      this._fetchTrips(vin, entryId, month);
    }

    this._paintTrips(deviceId, entities);
    // The expanded trip's route rides a live ha-map (loaded on demand); mount it
    // after paint so a repaint never tears down an in-place Leaflet instance.
    this._ensureMapLib();
    this._mountTripMiniMap();
  }

  _fetchTrips(vin, entryId, month) {
    const req = this._trp;
    const call = (service, data) =>
      this._hass.callService("bavariandata", service, data, undefined, false, true);
    const bounds = this._monthBounds(month);
    Promise.all([
      // Bounded to the month on the service side rather than fetched wide and
      // sliced here: the store holds two years, and the card should never pull
      // more than it is about to draw.
      call("get_trips", this._withEntry({ vin, from: bounds.from, to: bounds.to }, entryId)),
      call("get_driving_summary", this._withEntry({ vin, month }, entryId)),
    ])
      .then(([tripsRes, sumRes]) => {
        if (
          !this._trp ||
          this._trp.vin !== vin ||
          this._trp.trigger !== req.trigger ||
          this._trp.month !== month
        )
          return;
        const trips = (tripsRes && tripsRes.response && tripsRes.response.trips) || [];
        const summary = (sumRes && sumRes.response && sumRes.response.summary) || null;
        // The drive under way (if any) rides alongside the recorded trips rather
        // than inside them — it has no end and nothing to classify yet.
        const openList =
          (tripsRes && tripsRes.response && tripsRes.response.open_trips) || [];
        const open = openList.length ? openList[0] : null;
        this._trp = {
          ...this._trp,
          trips,
          summary,
          open,
          loading: false,
          error: false,
        };
        this._render();
      })
      .catch(() => {
        if (!this._trp || this._trp.vin !== vin || this._trp.trigger !== req.trigger)
          return;
        this._trp = { ...this._trp, loading: false, error: true };
        this._render();
      });
  }

  _paintTrips(deviceId, _entities) {
    const name = this._config.title || this._deviceName(deviceId);
    const state = this._trp || {};
    const trips = state.trips;
    const summary = state.summary;
    const open = state.open || null;
    const expanded = this._trpExpanded || null;

    const month = this._month("trips");
    const sig = this._signature({
      m: "trp",
      lang: _lang(this._hass),
      name,
      month,
      loading: state.loading && !trips,
      error: state.error,
      summary,
      expanded,
      // Elapsed minutes so the in-progress row's duration ticks between fetches.
      open: open
        ? [open.start, open.distance_km, this._elapsedMin(open.start), open.held]
        : null,
      rows: (trips || []).map((t) => [t.start, t.distance_km, t.classification]),
    });
    if (sig === this._sig) return;
    this._sig = sig;

    // The drive under way leads the list: it's the row a user opening this view
    // mid-drive is looking for, and it is the newest thing there is.
    const openRow = open ? this._tripRow(open, expanded) : "";

    let body;
    if (state.error) {
      body = `<div class="empty">${this._t("tr_error")}</div>`;
    } else if (!trips && state.loading) {
      body = `<div class="empty">${this._t("tr_loading")}</div>`;
    } else if ((!trips || !trips.length) && !open) {
      // Distinguish "nothing yet, ever" from "nothing in the month you paged
      // to" -- the first is onboarding advice, the second just means go back.
      body = `<div class="empty">${this._t(
        month === this._monthKey() ? "tr_empty" : "tr_empty_month"
      )}</div>`;
    } else {
      body = `${this._tripReview(summary)}<div class="chg__list">${openRow}${(
        trips || []
      )
        .map((t) => this._tripRow(t, expanded))
        .join("")}</div>`;
    }

    const count = trips ? trips.length : 0;
    const countLabel =
      count === 1 ? this._t("tr_trip_one") : this._t("tr_trip_many", { n: count });

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        <div class="chead">
          <ha-icon icon="mdi:road-variant"></ha-icon>
          <div class="chead__text">
            <span class="chead__title">${this._esc(this._config.title || this._t("tr_title"))}</span>
            <span class="chead__sub">${this._esc(name)}${count ? " · " + countLabel : ""}</span>
          </div>
          ${this._exportButtons("trips")}
        </div>
        ${this._monthNav("trips")}
        ${body}
      </ha-card>
    `;
    this._wireTripTaps();
    this._wireMonthNav();
  }

  /** The "month in review" panel, built entirely from get_driving_summary. */
  _tripReview(summary) {
    if (!summary || !summary.total_km) return "";
    const km = (v) => (v == null ? "—" : `${this._dec(v, 0)} km`);
    const split = summary.split || {};

    // Headline tiles: distance with a month-over-month arrow, and trip count.
    const delta = summary.mom_delta_percent;
    const arrow = delta == null ? "" : delta > 0 ? "▲" : delta < 0 ? "▼" : "→";
    const deltaTxt =
      delta == null
        ? ""
        : `<span class="tr__delta tr__delta--${delta >= 0 ? "up" : "down"}">${arrow} ${this._dec(
            Math.abs(delta),
            0
          )}% ${this._t("tr_vs_last")}</span>`;

    const tiles = [
      `<div class="tr__tile"><span class="tr__tile-val">${this._dec(
        summary.total_km,
        0
      )} <i>km</i></span><span class="tr__tile-lbl">${this._t("tr_review")}</span>${deltaTxt}</div>`,
    ];
    // Consumption leads with the energy balance: it comes from the charging
    // ledger and the odometer rather than from SoC deltas, so it isn't limited
    // by the one-percent resolution BMW streams SoC at, and it survives a month
    // whose drives were all too short to rate.
    const balance = summary.energy_balance || null;
    const battSide = summary.avg_consumption_kwh_per_100km;
    if (balance && balance.kwh_per_100km != null) {
      const isGrid = balance.source === "grid";
      // The second line only earns its place when the two figures measure
      // *different* things — a grid-side balance against the battery-side trip
      // total, where the gap really is the charging loss. Without a measured
      // grid figure the balance is battery-side too, so printing both would show
      // one quantity twice and dress the difference between their windows up as
      // a loss (it read "-2%" on real data, which is not a thing).
      let foot = "";
      if (isGrid && battSide != null) {
        const loss = (1 - battSide / balance.kwh_per_100km) * 100;
        const lossTxt =
          loss >= 3 && loss <= 30
            ? " · " + this._t("tr_charge_loss", { n: this._dec(loss, 0) })
            : "";
        foot = `<span class="tr__tile-sub">${this._dec(battSide, 1)} ${this._t(
          "tr_at_battery"
        )}${lossTxt}</span>`;
      }
      tiles.push(
        `<div class="tr__tile"><span class="tr__tile-val">${this._dec(
          balance.kwh_per_100km,
          1
        )} <i>kWh/100km</i></span><span class="tr__tile-lbl">${this._t(
          "tr_consumption"
        )} · ${this._t(isGrid ? "tr_at_plug" : "tr_at_battery")}</span>${foot}</div>`
      );
    } else if (battSide != null) {
      tiles.push(
        `<div class="tr__tile"><span class="tr__tile-val">${this._dec(
          battSide,
          1
        )} <i>kWh/100km</i></span><span class="tr__tile-lbl">${this._t(
          "tr_consumption"
        )}</span></div>`
      );
    }
    if (summary.recuperation_kwh_per_100km != null) {
      tiles.push(
        `<div class="tr__tile"><span class="tr__tile-val">${this._dec(
          summary.recuperation_kwh_per_100km,
          1
        )} <i>kWh/100km</i></span><span class="tr__tile-lbl">${this._t(
          "tr_recuperation"
        )}</span></div>`
      );
    }
    if (summary.estimated_cost && summary.estimated_cost.amount != null) {
      const c = summary.estimated_cost;
      tiles.push(
        `<div class="tr__tile"><span class="tr__tile-val">${this._dec(c.amount, 2)} <i>${this._esc(
          c.currency || ""
        )}</i></span><span class="tr__tile-lbl">${this._t("tr_est_cost")}</span></div>`
      );
    }

    // Business / private / commute split as one stacked bar with a legend.
    const segs = [
      ["business", split.business_km, "tr__seg--business"],
      ["commute", split.commute_km, "tr__seg--commute"],
      ["private", split.private_km, "tr__seg--private"],
      ["unclassified", split.unclassified_km, "tr__seg--unc"],
    ];
    const total = summary.total_km || 1;
    const barSegs = segs
      .filter(([, v]) => v)
      .map(
        ([, v, cls]) => `<span class="tr__seg ${cls}" style="width:${(v / total) * 100}%"></span>`
      )
      .join("");
    const legend = segs
      .filter(([, v]) => v)
      .map(
        ([key, v, cls]) =>
          `<span class="tr__leg"><i class="tr__dot ${cls}"></i>${this._t(
            "tr_" + key
          )} ${km(v)}</span>`
      )
      .join("");
    const splitBlock = barSegs
      ? `<div class="tr__split"><div class="tr__bar">${barSegs}</div><div class="tr__legend">${legend}</div></div>`
      : "";

    // Driving-style score (0–5) with a week-over-week trend sparkline.
    let styleBlock = "";
    if (summary.style_score != null) {
      const trend = Array.isArray(summary.style_trend) ? summary.style_trend : [];
      const points = trend.map((pt, i) => [i, pt.score]);
      const chart = points.length >= 2 ? this._healthTrendSvg(points) : "";
      styleBlock = `
        <div class="tr__style">
          <div class="tr__style-head">
            <span class="tr__style-lbl">${this._t("tr_style")}</span>
            <span class="tr__stars">${this._styleStars(summary.style_score)}</span>
          </div>
          ${chart ? `<div class="bh__trend"><span class="bh__trend-title">${this._t("tr_style_trend")}</span>${chart}</div>` : ""}
        </div>`;
    }

    // Top destinations.
    const dests = Array.isArray(summary.top_destinations) ? summary.top_destinations : [];
    const destBlock = dests.length
      ? `<div class="tr__dests"><span class="tr__dests-lbl">${this._t(
          "tr_top_dest"
        )}</span>${dests
          .map(
            (d) =>
              `<span class="tr__dest"><span class="tr__dest-name">${this._esc(
                d.label
              )}</span><span class="tr__dest-n">${this._t("tr_visits", {
                n: d.count,
              })}</span></span>`
          )
          .join("")}</div>`
      : "";

    return `
      <div class="tr__review">
        <div class="tr__tiles">${tiles.join("")}</div>
        ${splitBlock}
        ${styleBlock}
        ${destBlock}
      </div>`;
  }

  /** Five glyphs filled to the nearest half for a 0–5 style score. */
  _styleStars(score) {
    const s = Math.max(0, Math.min(5, score));
    let out = "";
    for (let i = 1; i <= 5; i++) {
      if (s >= i) out += "★";
      else if (s >= i - 0.5) out += "⯪";
      else out += "☆";
    }
    return out;
  }

  _tripRow(trip, expanded) {
    const id = trip.start || "";
    const isOpen = expanded === id;
    const live = !!trip.in_progress;
    const date = this._fmtSessionDate(trip.start);
    const from = this._tripPlace(trip.start_place);
    // A drive under way has no destination yet, and guessing one would be a lie:
    // the arrow trails off instead.
    const to = live ? "…" : this._esc(this._tripPlace(trip.end_place));
    const dist =
      trip.distance_km != null ? `${this._dec(trip.distance_km, 1)} km` : "—";
    // In progress: a live badge in place of a classification (there is nothing to
    // classify until the trip lands) and a duration counted from the start.
    const cls = live
      ? `<span class="tr__badge tr__badge--live"><i class="tr__live-dot"></i>${this._t(
          "tr_open"
        )}</span>`
      : trip.classification
      ? `<span class="tr__badge tr__badge--${trip.classification}">${this._t(
          "tr_" + trip.classification
        )}${
          trip.classification_source === "auto"
            ? ` <i class="tr__auto">${this._t("tr_auto")}</i>`
            : ""
        }</span>`
      : "";
    const min = live ? this._elapsedMin(trip.start) : null;
    const dur = live
      ? min == null
        ? ""
        : this._t("tr_min", { n: min })
      : this._durationLabel(trip);

    return `
      <div class="chg__session${isOpen ? " is-open" : ""}${live ? " is-live" : ""}">
        <button class="chg__row" data-trip="${this._attr(id)}">
          <span class="chg__row-main">
            <span class="chg__date">${this._esc(from)} → ${to}</span>
            <span class="chg__meta"><span class="chg__soc">${date}</span>${cls}</span>
          </span>
          <span class="chg__figures">
            <span class="chg__energy">${dist}</span>
            <span class="chg__cost">${dur || ""}</span>
          </span>
        </button>
        ${isOpen ? this._tripDetail(trip) : ""}
      </div>
    `;
  }

  _tripDetail(trip) {
    const facts = [];
    // Taken from the record, never recomputed here: the integration withholds a
    // consumption figure when the SoC drop behind it was too small to divide by
    // (a 1 km hop that ticked one percent is not a 78 kWh/100 km drive), and
    // dividing energy by distance in the card would print it anyway.
    const cons = trip.consumption_kwh_per_100km;
    if (cons != null) {
      facts.push([this._t("tr_consumption"), `${this._dec(cons, 1)} kWh/100km`]);
    }
    const st = trip.stats || {};
    // BMW's recuperation figure is already an average per 100 km, not a total.
    const recup = st.recuperation_kwh_per_100km ?? st.recuperation_kwh;
    if (recup != null) {
      facts.push([
        this._t("tr_recuperation"),
        `${this._dec(recup, 1)} kWh/100km`,
      ]);
    }
    // Both ends or nothing. A car that never streams its SoC while driving
    // (issue #25's X3) records no end, and "38→?%" on every trip says nothing.
    const soc = trip.soc_start != null && trip.soc_end != null ? this._socArc(trip) : "";

    const factRow = facts
      .map(
        ([k, v]) =>
          `<div class="chg__fact"><span class="chg__fact-lbl">${k}</span><span class="chg__fact-val">${v}</span></div>`
      )
      .join("");

    // Reclassification controls: an auto guess is a guess the user can correct.
    const buttons = ["business", "private", "commute"]
      .map(
        (c) =>
          `<button class="tr__cls-btn tr__badge--${c}${
            trip.classification === c ? " is-active" : ""
          }" data-trip-class="${this._attr(trip.start || "")}" data-class="${c}">${this._t(
            "tr_" + c
          )}</button>`
      )
      .join("");

    // A recorded route (opt-in trip_track) gets a small map of the drive; the
    // ha-map element is mounted into this placeholder after paint (see
    // _mountTripMiniMap), so a repaint can't tear a live Leaflet map down.
    const hasTrack = Array.isArray(trip.track) && trip.track.length >= 2;
    const mini = hasTrack
      ? `<div class="tr__minimap" data-mini="${this._attr(trip.start || "")}"></div>`
      : "";

    // A drive under way gets the route and the figures so far, but no
    // classification controls: there is no stored trip to reclassify yet, and the
    // distance and consumption are provisional. The note says so out loud rather
    // than letting a mid-drive figure read as final.
    const footer = trip.in_progress
      ? `<div class="tr__live-note">${this._t("tr_open_note")}</div>`
      : `<div class="tr__classify">
          <span class="tr__classify-lbl">${this._t("tr_classify")}</span>
          <span class="tr__cls-btns">${buttons}</span>
        </div>`;

    return `
      <div class="chg__detail">
        ${mini}
        ${soc ? `<div class="chg__facts">${soc}</div>` : ""}
        ${factRow ? `<div class="chg__facts">${factRow}</div>` : ""}
        ${footer}
      </div>
    `;
  }

  /** Build (or refresh) the mini-map for the currently expanded trip.
   * Idempotent and called after every trips paint: it mounts the ha-map once the
   * library is ready and the row exists, and re-mounts after a repaint clears it. */
  _mountTripMiniMap() {
    const expanded = this._trpExpanded || null;
    const holder = this.shadowRoot && this.shadowRoot.querySelector(".tr__minimap");
    if (!holder || !expanded) {
      this._miniMapTripId = null;
      return;
    }
    if (this._miniMapTripId === expanded && holder.firstChild) {
      if (this._miniMapEl) this._miniMapEl.hass = this._hass;
      this._reassertMapOverlays(); // the map may have been re-created under us
      // A drive under way keeps adding points. Re-draw the polyline on the map
      // that's already there -- _drawRoute replaces its own layer group -- rather
      // than re-mounting an ha-map every time the route grows.
      const growing = this._tripById(expanded);
      if (growing && growing.in_progress && this._miniMapEl) {
        const live = this._trackCoords(growing);
        if (live.length >= 2 && live.length !== this._miniMapPoints) {
          this._miniMapPoints = live.length;
          this._drawRoute(
            this._miniMapEl,
            live,
            TRIP_CLASS_COLORS[growing.classification] ||
              TRIP_CLASS_COLORS.unclassified
          );
        }
      }
      return; // already mounted for this trip
    }
    if (this._mapLib !== "ready") return; // _ensureMapLib re-renders when ready
    const id = holder.getAttribute("data-mini");
    const trip = this._tripById(id);
    if (!trip || !Array.isArray(trip.track) || trip.track.length < 2) return;

    const coords = this._trackCoords(trip);
    if (coords.length < 2) return;
    this._miniMapPoints = coords.length;

    holder.innerHTML = "";
    const map = document.createElement("ha-map");
    map.autoFit = false;
    map.zoom = 14;
    map.themeMode = "auto";
    map.style.height = "200px";
    map.style.display = "block";
    map.hass = this._hass;
    holder.appendChild(map);
    this._miniMapEl = map;
    this._miniMapTripId = expanded;

    const color =
      TRIP_CLASS_COLORS[trip.classification] || TRIP_CLASS_COLORS.unclassified;
    this._drawRoute(map, coords, color);
  }

  /** A fetched trip by its row id (its start), the drive under way included --
   * an open trip is not in the recorded list, it rides alongside it. */
  _tripById(id) {
    const state = this._trp || {};
    if (state.open && state.open.start === id) return state.open;
    return ((state.trips || []).find((t) => t.start === id)) || null;
  }

  /** A trip's track as clean [lat, lon] tuples, lightly decimated. */
  _trackCoords(trip) {
    const raw = (trip.track || [])
      .filter(
        (p) =>
          Array.isArray(p) &&
          p.length >= 2 &&
          typeof p[0] === "number" &&
          typeof p[1] === "number"
      )
      .map((p) => [p[0], p[1]]);
    return this._decimate(raw, 400);
  }

  /** What we can draw on behind an ha-map, whichever generation it is (both
   * handles are undocumented, so feature-detect). Before HA 2026.10 the
   * element exposed its Leaflet map (`leafletMap` + `Leaflet`); from 2026.10
   * it renders through a map engine (MapLibre GL, or Leaflet on devices that
   * can't do vector) with addPath/addMarker/setClustering and no Leaflet
   * handle. `id` identifies the live instance: both are rebuilt when the
   * element reconnects. null until the async init is done. */
  _mapSurface(mapEl) {
    const lmap = mapEl.leafletMap;
    const L = mapEl.Leaflet;
    if (lmap && L && typeof L.polyline === "function") {
      return { kind: "leaflet", id: lmap, lmap, L };
    }
    const eng = mapEl._engine;
    if (
      eng &&
      typeof eng.addPath === "function" &&
      typeof eng.addMarker === "function" &&
      typeof eng.setClustering === "function"
    ) {
      return { kind: "engine", id: eng, eng };
    }
    return null;
  }

  /** Fit the map to points. The engine path goes through ha-map's own
   * fitBounds, which waits for a sized map and maps `zoom` to maxZoom - 1. */
  _fitMapTo(mapEl, surface, pts, pad, maxZoom) {
    try {
      if (surface.kind === "leaflet") {
        surface.lmap.fitBounds(pts, { padding: [pad, pad], maxZoom });
      } else if (typeof mapEl.fitBounds === "function") {
        mapEl.fitBounds(pts, {
          zoom: maxZoom + 1,
          pad: 0.1,
          padding: { top: pad, right: pad, bottom: pad, left: pad },
        });
      }
    } catch (_) {
      /* transient size race; the next draw re-fits */
    }
  }

  /** A small inline-styled dot element for an engine marker. */
  _dotEl(fill, px) {
    const el = document.createElement("div");
    el.style.cssText =
      `box-sizing:border-box;width:${px}px;height:${px}px;border-radius:50%;` +
      `background:${fill};border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,0.45);`;
    return el;
  }

  /** Draw a route as a clean, dot-free line, unlike ha-map `paths` which mark
   * every vertex, with a green start and a red end marker. The surface
   * appears only after ha-map's async init, so retry until it's there. */
  _drawRoute(mapEl, coords, color) {
    let tries = 0;
    const draw = () => {
      if (!mapEl || !this.shadowRoot.contains(mapEl)) return;
      const surface = this._mapSurface(mapEl);
      if (!surface) {
        if (tries++ < 60) setTimeout(draw, 150);
        return;
      }
      try {
        if (mapEl._bdRoute && mapEl._bdRouteMap === surface.id) mapEl._bdRoute.remove();
        mapEl._bdRoute =
          surface.kind === "leaflet"
            ? this._routeOnLeaflet(surface, coords, color)
            : this._routeOnEngine(surface, coords, color);
        // Remember the instance and the inputs, so a torn-down map (tab switch)
        // can be detected and the route re-drawn without a full re-mount.
        mapEl._bdRouteMap = surface.id;
        mapEl._bdRouteSpec = { coords, color };
        this._fitMapTo(mapEl, surface, coords, 16, 16);
      } catch (_) {
        if (tries++ < 60) setTimeout(draw, 150);
      }
    };
    draw();
  }

  _routeOnLeaflet({ lmap, L }, coords, color) {
    const group = L.layerGroup();
    L.polyline(coords, {
      color,
      weight: 4,
      opacity: 0.9,
      lineJoin: "round",
      lineCap: "round",
    }).addTo(group);
    const dot = (fill) => ({
      radius: 5,
      color: "#fff",
      weight: 2,
      fillColor: fill,
      fillOpacity: 1,
    });
    L.circleMarker(coords[0], dot("#22a06b")).addTo(group); // start
    L.circleMarker(coords[coords.length - 1], dot("#d1453b")).addTo(group); // end
    group.addTo(lmap);
    return { layer: group, remove: () => lmap.removeLayer(group) };
  }

  _routeOnEngine({ eng }, coords, color) {
    // One segment carrying the whole track (a single line, joined cleanly);
    // no path markers, or the engine would dot the vertices like `paths`.
    const line = eng.addPath({
      color,
      segments: [{ points: coords, opacity: 0.9 }],
      markers: [],
    });
    const pin = (fill, at) =>
      eng.addMarker(this._dotEl(fill, 12), at, { size: [12, 12], interactive: false });
    const start = pin("#22a06b", coords[0]);
    const end = pin("#d1453b", coords[coords.length - 1]);
    let removed = false;
    return {
      remove: () => {
        if (removed) return;
        removed = true;
        line.remove();
        start.remove();
        end.remove();
      },
    };
  }

  _tripPlace(place) {
    if (!place) return this._t("tr_unknown_place");
    const label = place.label || place.zone || place.address;
    if (!label || label === "Unknown") return this._t("tr_unknown_place");
    return label;
  }

  _wireTripTaps() {
    this._wireExport();
    this.shadowRoot.querySelectorAll("[data-trip]").forEach((el) => {
      el.addEventListener("click", () => {
        const id = el.getAttribute("data-trip");
        this._trpExpanded = this._trpExpanded === id ? null : id;
        this._sig = null; // force a repaint with the new expansion state
        const deviceId = this._resolveDeviceId();
        this._paintTrips(deviceId, this._deviceEntities(deviceId));
        this._mountTripMiniMap(); // show the newly expanded trip's route at once
      });
    });
    this.shadowRoot.querySelectorAll("[data-trip-class]").forEach((el) => {
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const tripId = el.getAttribute("data-trip-class");
        const cls = el.getAttribute("data-class");
        const vin = this._trp && this._trp.vin;
        const entryId = this._trp && this._trp.entryId;
        if (!vin) return;
        this._hass
          .callService(
            "bavariandata",
            "set_trip_class",
            this._withEntry(
              { vin, trip_id: `${vin}-${tripId}`, classification: cls },
              entryId
            )
          )
          .then(() => {
            // Optimistically reflect the change; the service re-dispatches and
            // the summary sensor's last_changed will trigger a real refetch.
            if (this._trp && Array.isArray(this._trp.trips)) {
              const hit = this._trp.trips.find((t) => t.start === tripId);
              if (hit) {
                hit.classification = cls;
                hit.classification_source = "user";
              }
              this._sig = null;
              const deviceId = this._resolveDeviceId();
              this._paintTrips(deviceId, this._deviceEntities(deviceId));
            }
          })
          .catch(() => {});
      });
    });
  }

  /* ---- trip map --------------------------------------------------------- */

  // The map draws the opt-in route polylines (`trip_track`) on Home Assistant's
  // own Leaflet map element (`ha-map`), reusing the frontend's map component
  // rather than bundling a mapping library. Trips come from `get_trips` (which
  // carries `track`), gated on the monthly-distance sensor like the Trips view.
  _renderMap(deviceId, entities) {
    const vin = this._deviceVin(deviceId);
    if (!vin) {
      this._mapPhase = null;
      this._renderMessage(this._t("no_vehicle_title"), this._t("no_vehicle_body"));
      return;
    }
    this._ensureMapLib();

    const entryId = this._deviceEntry(deviceId);

    const trigSt = entities
      .map((id) => this._st(id))
      .find(
        (st) =>
          st && st.attributes && st.attributes.descriptor === "driving_distance_month"
      );
    const trigger = trigSt ? trigSt.last_changed : "";

    const cache = this._mapData;
    const current = cache && cache.vin === vin && cache.trigger === trigger;
    if (!current || (!cache.trips && !cache.loading)) {
      this._mapData = {
        vin,
        trigger,
        trips: current && cache ? cache.trips : null,
        loading: true,
        error: false,
      };
      this._fetchMapTrips(vin, entryId);
    }

    this._paintMap(deviceId, entities);
  }

  _fetchMapTrips(vin, entryId) {
    const req = this._mapData;
    // A generous limit: a route map wants more than the visible trip list, and
    // get_trips reads the store (zero REST quota), so a wide fetch is cheap.
    this._hass
      .callService(
        "bavariandata",
        "get_trips",
        this._withEntry({ vin, limit: 200 }, entryId),
        undefined,
        false,
        true
      )
      .then((res) => {
        if (!this._mapData || this._mapData.vin !== vin || this._mapData.trigger !== req.trigger)
          return;
        const trips = (res && res.response && res.response.trips) || [];
        this._mapData = { ...this._mapData, trips, loading: false, error: false };
        this._render();
      })
      .catch(() => {
        if (!this._mapData || this._mapData.vin !== vin || this._mapData.trigger !== req.trigger)
          return;
        this._mapData = { ...this._mapData, loading: false, error: true };
        this._render();
      });
  }

  // Force Home Assistant to load its `ha-map` element on demand. Creating a
  // throwaway `map` card pulls in the map-card module, which imports (and thus
  // registers) `ha-map`; the setConfig it then runs may throw for our empty
  // entity list, but by then the element is defined, so we swallow it.
  _ensureMapLib() {
    if (customElements.get("ha-map")) {
      this._mapLib = "ready";
      return;
    }
    if (this._mapLib === "loading" || this._mapLib === "failed") return;
    this._mapLib = "loading";
    const finish = (ok) => {
      this._mapLib = ok ? "ready" : "failed";
      this._render();
    };
    (async () => {
      try {
        const helpers = await window.loadCardHelpers();
        try {
          await helpers.createCardElement({ type: "map", entities: [] });
        } catch (_) {
          /* setConfig may reject an empty map; the import already ran */
        }
        await Promise.race([
          customElements.whenDefined("ha-map"),
          new Promise((_, reject) => setTimeout(reject, 6000)),
        ]);
        finish(!!customElements.get("ha-map"));
      } catch (_) {
        finish(false);
      }
    })();
  }

  /** Trips that carry a drawable track, filtered to the active time window. */
  _mapWindowTrips() {
    const all = (this._mapData && this._mapData.trips) || [];
    const withTrack = all.filter(
      (t) => Array.isArray(t.track) && t.track.length >= 2
    );
    const win = this._mapWindow || "month";
    if (win === "all") return withTrack;
    const now = new Date();
    let cutoff;
    if (win === "3m") {
      cutoff = new Date(now);
      cutoff.setMonth(cutoff.getMonth() - 3);
    } else {
      cutoff = new Date(now.getFullYear(), now.getMonth(), 1);
    }
    return withTrack.filter((t) => {
      const d = new Date(t.start);
      return !isNaN(d.getTime()) && d >= cutoff;
    });
  }

  /** The destination (end point) of every trip with a track, as visit markers.
   * Only the end, not the start: a trip's start is essentially the previous
   * trip's end (the car parks, then drives on from there), so plotting both would
   * double-count every place. End-only gives an honest "times arrived here" count.
   * Routes themselves now live on the trip card's mini-map. */
  _mapEndpoints(trips) {
    const out = [];
    const valid = (p) =>
      Array.isArray(p) && typeof p[0] === "number" && typeof p[1] === "number";
    for (const trip of trips) {
      const track = trip.track;
      if (!Array.isArray(track) || track.length < 2) continue;
      const end = track[track.length - 1];
      if (valid(end))
        out.push({ lat: end[0], lon: end[1], label: this._tripPlace(trip.end_place) });
    }
    return out;
  }

  _paintMap(deviceId, _entities) {
    const name = this._config.title || this._deviceName(deviceId);
    const state = this._mapData || {};
    const win = this._mapWindow || "month";
    const lang = _lang(this._hass);

    const allTrips = state.trips;
    const anyTrack =
      Array.isArray(allTrips) &&
      allTrips.some((t) => Array.isArray(t.track) && t.track.length >= 2);
    const winTrips = allTrips ? this._mapWindowTrips() : [];

    let phase;
    if (state.error) phase = "error";
    else if (this._mapLib === "failed") phase = "unavailable";
    else if ((!allTrips && state.loading) || this._mapLib !== "ready") phase = "loading";
    else if (!anyTrack) phase = "empty_none";
    else if (!winTrips.length) phase = "empty_window";
    else phase = "data";

    // The live map must survive routine hass ticks: rebuild the surrounding
    // chrome only when the phase or a header field changes, and update the
    // ha-map element in place otherwise (a full innerHTML rewrite would tear
    // down Leaflet and reset the user's pan/zoom on every state update).
    const chromeSig = this._signature({ m: "map", phase, name, lang });
    const mounted =
      phase === "data" && this._mapEl && this.shadowRoot.contains(this._mapEl);

    if (chromeSig !== this._mapChromeSig || (phase === "data" && !mounted)) {
      this._mapChromeSig = chromeSig;
      this._buildMapChrome(deviceId, phase, name);
    }

    if (phase === "data") {
      this._refreshDestinations(winTrips, win);
    }
  }

  _buildMapChrome(deviceId, phase, name) {
    const count = phase === "data" ? this._mapWindowTrips().length : 0;
    const countLabel =
      count === 1 ? this._t("tr_trip_one") : this._t("tr_trip_many", { n: count });

    let body;
    if (phase === "error") {
      body = `<div class="empty">${this._t("mp_error")}</div>`;
    } else if (phase === "unavailable") {
      body = `<div class="empty">${this._t("mp_unavailable")}</div>`;
    } else if (phase === "loading") {
      body = `<div class="empty">${this._t("mp_loading")}</div>`;
    } else if (phase === "empty_none") {
      body = `<div class="empty">${this._t("mp_empty_none")}</div>`;
    } else if (phase === "empty_window") {
      body = `${this._mapFilters()}<div class="empty">${this._t("mp_empty_window")}</div>`;
    } else {
      body = `${this._mapFilters()}<div class="map__holder"></div>`;
    }

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        <div class="chead">
          <ha-icon icon="mdi:map-marker-multiple"></ha-icon>
          <div class="chead__text">
            <span class="chead__title">${this._esc(this._config.title || this._t("mp_title"))}</span>
            <span class="chead__sub">${this._esc(name)}${count ? " · " + countLabel : ""}</span>
          </div>
        </div>
        ${body}
      </ha-card>
    `;

    this._mapEl = null;
    if (phase === "data") {
      const holder = this.shadowRoot.querySelector(".map__holder");
      const map = document.createElement("ha-map");
      // We add our own clustered markers to the Leaflet map, so autoFit (entities/
      // zones/layers only) stays off and we fit to the endpoints ourselves.
      map.autoFit = false;
      map.zoom = 13;
      map.themeMode = "auto";
      map.style.height = "360px";
      map.style.display = "block";
      map.hass = this._hass;
      holder.appendChild(map);
      this._mapEl = map;
      this._mapDataSig = null; // force the first cluster build
      this._clusterLayer = null; // fresh map -- the old layer is gone with it
      this._clusterMap = null;
    }
    this._wireMapFilters();
  }

  /** The time-window chip row (This month / 3 months / All). */
  _mapFilters() {
    const win = this._mapWindow || "month";
    const chip = (key, label) =>
      `<button class="map__chip${win === key ? " is-active" : ""}" data-win="${key}">${label}</button>`;
    return `
      <div class="map__filters">
        ${chip("month", this._t("mp_win_month"))}
        ${chip("3m", this._t("mp_win_3m"))}
        ${chip("all", this._t("mp_win_all"))}
      </div>`;
  }

  _wireMapFilters() {
    this.shadowRoot.querySelectorAll("[data-win]").forEach((el) => {
      el.addEventListener("click", () => {
        const win = el.getAttribute("data-win");
        if ((this._mapWindow || "month") === win) return;
        this._mapWindow = win;
        // Re-run the paint: a window change can move between data and
        // empty_window (a chrome rebuild) or just swap the drawn routes.
        this._paintMap(this._resolveDeviceId(), this._deviceEntities(this._resolveDeviceId()));
      });
    });
  }

  /** Rebuild the clustered destination markers when the data/window changes. */
  _refreshDestinations(winTrips, win) {
    if (!this._mapEl) return;
    this._mapEl.hass = this._hass;
    this._reassertMapOverlays(); // the map may have been re-created under us

    const dataSig = this._signature({
      win,
      rows: winTrips.map((t) => [t.start, (t.track || []).length]),
    });
    if (dataSig === this._mapDataSig) return;
    this._mapDataSig = dataSig;

    this._mountClusters(this._mapEndpoints(winTrips));

    // Keep the chip active-state and the trip count honest after a swap.
    const cur = this._mapWindow || "month";
    this.shadowRoot.querySelectorAll("[data-win]").forEach((el) => {
      el.classList.toggle("is-active", el.getAttribute("data-win") === cur);
    });
    const sub = this.shadowRoot.querySelector(".chead__sub");
    if (sub) {
      const name = this._config.title || this._deviceName(this._resolveDeviceId());
      const n = winTrips.length;
      const label = n === 1 ? this._t("tr_trip_one") : this._t("tr_trip_many", { n });
      sub.textContent = `${name}${n ? " · " + label : ""}`;
    }
  }

  /** Add the endpoints to the map as natively clustered markers, reusing
   * whatever clustering Home Assistant's own ha-map ships -- counts, zoom-split
   * and spiderfy are native, no hand-rolled overlay. The surface appears only
   * after ha-map's async init, so retry until it's there. */
  _mountClusters(endpoints) {
    const mapEl = this._mapEl;
    if (!mapEl) return;
    let tries = 0;
    const build = () => {
      if (!mapEl || !this.shadowRoot.contains(mapEl)) return;
      const surface = this._mapSurface(mapEl);
      if (
        !surface ||
        (surface.kind === "leaflet" && typeof surface.L.markerClusterGroup !== "function")
      ) {
        if (tries++ < 60) setTimeout(build, 150);
        return; // the map (or its markercluster) is not ready yet
      }
      try {
        if (this._clusterLayer) {
          if (this._clusterMap === surface.id) this._clusterLayer.remove();
          this._clusterLayer = null;
          this._clusterMap = null;
        }
        this._clusterLayer =
          surface.kind === "leaflet"
            ? this._clustersOnLeaflet(surface, endpoints)
            : this._clustersOnEngine(surface, endpoints);
        this._clusterMap = surface.id; // so a torn-down map is detectable on reconnect
        const pts = endpoints.map((ep) => [ep.lat, ep.lon]);
        if (pts.length) this._fitMapTo(mapEl, surface, pts, 30, 15);
      } catch (_) {
        if (tries++ < 60) setTimeout(build, 150);
      }
    };
    build();
  }

  _clustersOnLeaflet({ lmap, L }, endpoints) {
    const group = L.markerClusterGroup({
      showCoverageOnHover: false,
      maxClusterRadius: 48,
      iconCreateFunction: (c) =>
        L.divIcon({
          html: this._clusterBubbleHtml(c.getChildCount()),
          className: "",
          iconSize: [40, 40],
          iconAnchor: [20, 20],
        }),
    });
    for (const ep of endpoints) {
      const marker = L.marker([ep.lat, ep.lon], {
        icon: L.divIcon({
          html: this._pinHtml(),
          className: "",
          iconSize: [16, 16],
          iconAnchor: [8, 8],
        }),
      });
      if (ep.label) marker.bindTooltip(this._esc(ep.label), { direction: "top" });
      group.addLayer(marker);
    }
    lmap.addLayer(group);
    return { layer: group, remove: () => lmap.removeLayer(group) };
  }

  _clustersOnEngine({ eng }, endpoints) {
    // The engine clusters every marker added with `cluster: true`; a group of
    // more than one gets the element iconBuilder returns.
    const options = {
      radius: 48,
      iconBuilder: (members) => {
        const el = document.createElement("div");
        el.innerHTML = this._clusterBubbleHtml(members.length);
        return { element: el, size: [40, 40] };
      },
    };
    const markers = endpoints.map((ep) => {
      const el = document.createElement("div");
      el.innerHTML = this._pinHtml();
      // `title` lands as a plain attribute (no HTML), so the label goes in raw.
      return eng.addMarker(el, [ep.lat, ep.lon], {
        size: [16, 16],
        cluster: true,
        title: ep.label || undefined,
      });
    });
    eng.setClustering(options);
    let removed = false;
    return {
      // ha-map resets the engine's clustering when it redraws its own entities
      // (a theme change, for one) -- treat that as lost so the pins regroup.
      lost: () => eng._clusterOptions !== undefined && eng._clusterOptions !== options,
      remove: () => {
        if (removed) return;
        removed = true;
        markers.forEach((m) => m.remove());
        if (eng._clusterOptions === options) eng.setClustering(null);
      },
    };
  }

  /** Re-add our overlays when the map has been re-created under them (tab
   * switch -> disconnect -> the old map is destroyed -> new instance) or has
   * dropped them. Only fires for overlays that were actually drawn once, so it
   * never races an initial draw that is still retrying; cheap enough to call
   * from every paint as well as from connectedCallback. The redraw removes the
   * old overlay itself when it still sits on the live map. */
  _reassertMapOverlays() {
    const shadow = this.shadowRoot;
    if (!shadow) return;

    const mini = this._miniMapEl;
    if (
      mini &&
      shadow.contains(mini) &&
      mini._bdRouteSpec &&
      this._layerLost(mini, mini._bdRouteMap, mini._bdRoute)
    ) {
      const spec = mini._bdRouteSpec;
      this._drawRoute(mini, spec.coords, spec.color);
    }

    const mapEl = this._mapEl;
    if (
      mapEl &&
      shadow.contains(mapEl) &&
      this._clusterMap &&
      this._layerLost(mapEl, this._clusterMap, this._clusterLayer)
    ) {
      this._mountClusters(this._mapEndpoints(this._mapWindowTrips()));
    }
  }

  /** True when an overlay we drew is no longer on the element's live map: the
   * map is gone, or was replaced, or dropped the overlay. */
  _layerLost(mapEl, drawnOn, overlay) {
    const surface = this._mapSurface(mapEl);
    if (!surface) return true; // torn down; the draw retry waits for the new one
    if (surface.id !== drawnOn) return true;
    if (!overlay) return false;
    if (overlay.lost) return overlay.lost();
    const lmap = surface.lmap;
    return !!overlay.layer && !!lmap && typeof lmap.hasLayer === "function" && !lmap.hasLayer(overlay.layer);
  }

  /** Inline-styled HTML for a cluster bubble (styles must be inline: the marker
   * lives in ha-map's shadow tree, out of reach of this card's stylesheet). */
  _clusterBubbleHtml(n) {
    return (
      `<div style="display:flex;align-items:center;justify-content:center;` +
      `width:40px;height:40px;border-radius:50%;background:rgba(0,102,177,0.92);` +
      `color:#fff;font:600 14px/1 system-ui,sans-serif;border:2px solid #fff;` +
      `box-shadow:0 1px 5px rgba(0,0,0,0.45);">${n}</div>`
    );
  }

  /** Inline-styled HTML for a single endpoint pin. */
  _pinHtml() {
    return (
      `<div style="width:14px;height:14px;border-radius:50%;background:#0066b1;` +
      `border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,0.45);"></div>`
    );
  }

  /** Keep at most `max` points, always retaining the first and last. */
  _decimate(points, max) {
    if (points.length <= max) return points.slice();
    const step = Math.ceil(points.length / max);
    const out = [];
    for (let i = 0; i < points.length; i += step) out.push(points[i]);
    const last = points[points.length - 1];
    if (out[out.length - 1] !== last) out.push(last);
    return out;
  }

  /* ---- battery health --------------------------------------------------- */

  _renderHealth(deviceId, entities) {
    if (this._renderIceNotice(deviceId, entities)) return;
    const name = this._config.title || this._deviceName(deviceId);
    // Everything the view needs already lives on the battery_health sensor
    // (state + attributes), so unlike the charging view there is no service to
    // call -- the estimate and its trend paint straight from hass.
    const st = entities
      .map((id) => this._st(id))
      .find(
        (s) => s && s.attributes && s.attributes.descriptor === "battery_health"
      );
    const a = (st && st.attributes) || {};
    const confident = !!a.confident;
    const usable = a.usable_capacity_kwh;
    const nominal = a.nominal_capacity_kwh;
    const vsNew = a.vs_new_percent;
    const samples = a.samples || 0;
    const needed = a.samples_needed || 10;
    const suspicious = !!a.suspicious;
    const trend = Array.isArray(a.trend) ? a.trend : [];

    const sig = this._signature({
      m: "bh",
      lang: _lang(this._hass),
      name,
      has: !!st,
      confident,
      usable,
      nominal,
      vsNew,
      samples,
      needed,
      suspicious,
      trend,
    });
    if (sig === this._sig) return;
    this._sig = sig;

    let body;
    if (!st) {
      body = `<div class="empty">${this._t("bh_empty")}</div>`;
    } else if (confident) {
      body = this._healthConfident(usable, nominal, vsNew, samples, trend);
    } else {
      body = this._healthLearning(samples, needed, suspicious);
    }

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        <div class="chead">
          <ha-icon icon="mdi:battery-heart-variant"></ha-icon>
          <div class="chead__text">
            <span class="chead__title">${this._esc(this._config.title || this._t("bh_title"))}</span>
            <span class="chead__sub">${this._esc(name)}</span>
          </div>
        </div>
        ${body}
      </ha-card>
    `;
  }

  _healthLearning(samples, needed, suspicious) {
    const capped = Math.min(samples, needed);
    const pct = needed ? Math.min(100, Math.round((capped / needed) * 100)) : 0;
    // A suspicious estimate is a different message from "not enough data yet":
    // say we're cross-checking rather than implying the car hasn't charged.
    const hint = suspicious ? this._t("bh_suspicious") : this._t("bh_learning_hint");
    return `
      <div class="bh">
        <div class="bh__learn">
          <span class="bh__learn-val">${this._t("bh_learning", { n: capped, total: needed })}</span>
          <div class="bh__bar"><div class="bh__bar-fill" style="width:${pct}%"></div></div>
          <span class="bh__hint">${hint}</span>
        </div>
      </div>
    `;
  }

  _healthConfident(usable, nominal, vsNew, samples, trend) {
    const facts = [];
    if (nominal != null) {
      facts.push([this._t("bh_nominal"), `${this._dec(nominal, 1)} kWh`]);
    }
    facts.push([this._t("bh_analysed"), this._t("bh_samples", { n: samples })]);
    const factRow = facts
      .map(
        ([k, v]) =>
          `<div class="chg__fact"><span class="chg__fact-lbl">${k}</span><span class="chg__fact-val">${v}</span></div>`
      )
      .join("");
    const chart = this._healthTrendSvg(trend);
    return `
      <div class="bh">
        <div class="bh__hero">
          ${this._healthRing(vsNew)}
          <div class="bh__hero-text">
            <span class="bh__usable">${this._dec(usable, 1)} <i>kWh</i></span>
            <span class="bh__usable-lbl">${this._t("bh_usable")}</span>
            ${
              vsNew != null
                ? `<span class="bh__vsnew">${this._t("bh_of_new", { p: this._dec(vsNew, 0) })}</span>`
                : ""
            }
          </div>
        </div>
        ${
          chart
            ? `<div class="bh__trend"><span class="bh__trend-title">${this._t("bh_trend_title")}</span>${chart}</div>`
            : ""
        }
        <div class="chg__facts">${factRow}</div>
      </div>
    `;
  }

  /** A compact donut showing capacity as a percentage of the as-new pack. */
  _healthRing(pct) {
    const r = 34;
    const circ = 2 * Math.PI * r;
    const p = pct == null ? null : Math.max(0, Math.min(100, pct));
    const dash = p == null ? 0 : (p / 100) * circ;
    const label = p == null ? "—" : `${this._dec(p, 0)}%`;
    return `
      <svg class="bh__ring" viewBox="0 0 80 80" role="img">
        <circle class="bh__ring-track" cx="40" cy="40" r="${r}"></circle>
        <circle class="bh__ring-val" cx="40" cy="40" r="${r}"
          stroke-dasharray="${this._px(dash)} ${this._px(circ)}"
          transform="rotate(-90 40 40)"></circle>
        <text x="40" y="45" class="bh__ring-text">${label}</text>
      </svg>
    `;
  }

  /** Inline SVG of the [odometer_km, usable_kwh] trend. Y is scaled to the data
   * range, not zero-based: capacity fade is a few kWh and would be invisible on
   * a 0-based axis. */
  _healthTrendSvg(points) {
    if (!Array.isArray(points) || points.length < 2) return "";
    const W = 260;
    const H = 70;
    const pad = 6;
    const xs = points.map((p) => p[0]);
    const ys = points.map((p) => p[1]);
    const xMin = Math.min(...xs);
    const xMax = Math.max(...xs);
    let yMin = Math.min(...ys);
    let yMax = Math.max(...ys);
    // Give a nearly-flat series some vertical room so it doesn't render as a
    // jagged line amplifying sub-kWh noise into an alarming-looking drop.
    if (yMax - yMin < 1) {
      yMin -= 1;
      yMax += 1;
    }
    const spanX = xMax - xMin || 1;
    const spanY = yMax - yMin || 1;
    const sx = (x) => pad + ((x - xMin) / spanX) * (W - 2 * pad);
    const sy = (y) => H - pad - ((y - yMin) / spanY) * (H - 2 * pad);
    const line = points
      .map((p) => `${this._px(sx(p[0]))},${this._px(sy(p[1]))}`)
      .join(" ");
    return `
      <svg class="bh__chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img">
        <polyline points="${line}" class="chg__chart-line"></polyline>
        <text x="${pad}" y="10" class="chg__chart-max">${this._dec(yMax, 1)} kWh</text>
        <text x="${pad}" y="${H - 3}" class="chg__chart-max">${this._dec(yMin, 1)} kWh</text>
      </svg>
    `;
  }

  /* ---- efficiency & real range ------------------------------------------ */

  _renderEfficiency(deviceId, entities) {
    const vin = this._deviceVin(deviceId);
    if (!vin) {
      this._renderMessage(this._t("no_vehicle_title"), this._t("no_vehicle_body"));
      return;
    }
    if (this._renderIceNotice(deviceId, entities)) return;

    const entryId = this._deviceEntry(deviceId);

    const st = entities
      .map((id) => this._st(id))
      .find((s) => s && s.attributes && s.attributes.descriptor === "real_range");
    const a = (st && st.attributes) || {};

    // The month trend and the running cost come from the charging ledger, so
    // they only move when a charge lands -- gate the service call on the
    // measured consumption rather than on the entity's last_changed, which
    // ticks with every state-of-charge update while the car is plugged in.
    const trigger = [
      a.consumption_kwh_per_100km,
      a.consumption_window_days,
      a.status,
    ].join("|");
    const cache = this._eff;
    const current = cache && cache.vin === vin && cache.trigger === trigger;
    if (!current || (!cache.data && !cache.loading)) {
      this._eff = {
        vin,
        trigger,
        data: current && cache ? cache.data : null,
        loading: true,
        error: false,
      };
      this._fetchEfficiency(vin, entryId);
    }

    this._paintEfficiency(deviceId, st);
  }

  _fetchEfficiency(vin, entryId) {
    const req = this._eff;
    this._hass
      .callService(
        "bavariandata",
        "get_efficiency",
        this._withEntry({ vin }, entryId),
        undefined,
        false,
        true
      )
      .then((res) => {
        if (!this._eff || this._eff.vin !== vin || this._eff.trigger !== req.trigger)
          return;
        const profile = (res && res.response && res.response.efficiency) || null;
        this._eff = { ...this._eff, data: profile, loading: false, error: false };
        this._render();
      })
      .catch(() => {
        if (!this._eff || this._eff.vin !== vin || this._eff.trigger !== req.trigger)
          return;
        this._eff = { ...this._eff, loading: false, error: true };
        this._render();
      });
  }

  /** Every figure here is in kilometres and kWh as the ledger recorded them,
   * never read off the entity's state: a distance state is converted into the
   * viewer's unit system while these attributes stay metric, and mixing the two
   * would put miles and kilometres in one card. */
  _efFigures(st, profile) {
    const a = (st && st.attributes) || {};
    if (st && a.status) {
      return {
        status: a.status,
        nowKm: a.range_now_km,
        fullKm: a.range_full_km,
        soc: a.soc_percent,
        consumption: a.consumption_kwh_per_100km,
        side: a.consumption_source,
        window: a.consumption_window_days,
        gridConsumption: a.grid_consumption_kwh_per_100km,
        loss: a.measured_loss_percent,
        capacity: a.capacity_kwh,
        capacitySource: a.capacity_source,
        bmwKm: a.bmw_range_km,
        vsBmw: a.vs_bmw_percent,
      };
    }
    // No entity (a car with no odometer, or one the owner disabled): the service
    // answers the same questions, just without the per-tick freshness.
    const p = profile || {};
    const range = p.range || {};
    const consumption = p.consumption || {};
    const grid = p.grid_consumption || {};
    return {
      status: p.status,
      nowKm: range.now_km,
      fullKm: range.full_km,
      soc: range.soc_percent,
      consumption: consumption.kwh_per_100km,
      side: consumption.source,
      window: consumption.window_days,
      gridConsumption: grid.kwh_per_100km,
      loss: p.measured_loss_percent,
      capacity: p.capacity_kwh,
      capacitySource: p.capacity_source,
      bmwKm: range.bmw_km,
      vsBmw: range.vs_bmw_percent,
    };
  }

  _paintEfficiency(deviceId, st) {
    const name = this._config.title || this._deviceName(deviceId);
    const state = this._eff || {};
    const profile = state.data;
    const f = this._efFigures(st, profile);
    const trend = (profile && Array.isArray(profile.trend) && profile.trend) || [];

    const sig = this._signature({
      m: "ef",
      lang: _lang(this._hass),
      name,
      loading: state.loading && !profile && !st,
      error: state.error,
      f,
      trend,
      cost: profile && profile.cost_per_100km,
      currency: profile && profile.currency,
      mix: profile && profile.energy_mix,
    });
    if (sig === this._sig) return;
    this._sig = sig;

    let body;
    if (state.error && !st) {
      body = `<div class="empty">${this._t("ef_error")}</div>`;
    } else if (state.loading && !profile && !st) {
      body = `<div class="empty">${this._t("ef_loading")}</div>`;
    } else if (f.status === "plug_in_hybrid") {
      // Not "not enough history yet": on a plug-in hybrid no amount of it would
      // do, because the odometer also counts the kilometres driven on fuel.
      body = `<div class="empty">${this._t("ef_hybrid")}</div>`;
    } else if (f.consumption == null) {
      body = `<div class="empty">${this._t("ef_empty")}</div>`;
    } else if (f.fullKm == null) {
      // Consumption measured, but nothing to divide it into: say which half is
      // missing rather than showing the same "no data yet" as an empty ledger.
      body = `<div class="empty">${this._t("ef_no_capacity")}</div>`;
    } else {
      body = this._efBody(f, trend, profile || {});
    }

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        <div class="chead">
          <ha-icon icon="mdi:map-marker-distance"></ha-icon>
          <div class="chead__text">
            <span class="chead__title">${this._esc(this._config.title || this._t("ef_title"))}</span>
            <span class="chead__sub">${this._esc(name)}</span>
          </div>
        </div>
        ${body}
      </ha-card>
    `;
  }

  _efBody(f, trend, profile) {
    const hero =
      f.nowKm != null
        ? `<span class="ef__hero-val">${this._dec(f.nowKm, 0)} <i>km</i></span>
           <span class="ef__hero-lbl">${this._t("ef_range_now")}${
             f.soc != null
               ? " · " + this._t("ef_at_soc", { p: this._dec(f.soc, 0) })
               : ""
           }</span>`
        : `<span class="ef__hero-val">${this._dec(f.fullKm, 0)} <i>km</i></span>
           <span class="ef__hero-lbl">${this._t("ef_range_full", {
             km: this._dec(f.fullKm, 0),
           })}</span>`;

    let vsBmw = "";
    if (f.vsBmw != null && f.bmwKm != null) {
      const p = Math.round(Math.abs(f.vsBmw));
      const km = this._dec(f.bmwKm, 0);
      const key =
        p < 1 ? "ef_vs_bmw_same" : f.vsBmw > 0 ? "ef_vs_bmw_over" : "ef_vs_bmw_under";
      const tone = p < 1 ? "" : f.vsBmw > 0 ? " ef__vs--over" : " ef__vs--under";
      vsBmw = `<span class="ef__vs${tone}">${this._t(key, { p: this._dec(p, 0), km })}</span>`;
    }

    const sideLabel =
      f.side === "grid" ? this._t("ef_side_grid") : this._t("ef_side_battery");
    const windowLabel =
      f.window != null
        ? this._t("ef_window_days", { n: f.window })
        : this._t("ef_window_all");

    const facts = [
      [
        this._t("ef_consumption"),
        `${this._dec(f.consumption, 1)} kWh/100 km`,
        `${sideLabel} · ${windowLabel}`,
      ],
    ];
    if (f.loss != null && f.gridConsumption != null) {
      facts.push([
        this._t("ef_loss"),
        `${this._dec(f.loss, 0)} %`,
        `${this._dec(f.gridConsumption, 1)} kWh/100 km ${this._t("ef_side_grid")}`,
      ]);
    }
    if (f.capacity != null) {
      facts.push([
        this._t("ef_capacity"),
        `${this._dec(f.capacity, 1)} kWh`,
        f.capacitySource === "measured"
          ? this._t("ef_capacity_measured")
          : this._t("ef_capacity_bmw"),
      ]);
    }
    if (profile.cost_per_100km != null) {
      const currency = profile.currency ? ` ${this._esc(profile.currency)}` : "";
      const mix = profile.energy_mix || {};
      facts.push([
        this._t("ef_cost"),
        `${this._dec(profile.cost_per_100km, 2)}${currency}`,
        mix.solar_percent != null
          ? this._t("ef_solar", { p: this._dec(mix.solar_percent, 0) })
          : "",
      ]);
    }

    const factRow = facts
      .map(
        ([k, v, note]) =>
          `<div class="ef__fact">
             <span class="ef__fact-lbl">${k}</span>
             <span class="ef__fact-val">${v}</span>
             ${note ? `<span class="ef__fact-note">${note}</span>` : ""}
           </div>`
      )
      .join("");

    const chart = this._efTrendSvg(trend);
    return `
      <div class="ef">
        <div class="ef__hero">
          <div class="ef__hero-text">${hero}</div>
          ${vsBmw}
        </div>
        <div class="ef__facts">${factRow}</div>
        ${
          chart
            ? `<div class="ef__trend"><span class="ef__trend-title">${this._t(
                "ef_trend_title"
              )}</span>${chart}</div>`
            : ""
        }
        <span class="ef__note">${this._t("ef_footnote")}</span>
      </div>
    `;
  }

  /** Monthly consumption as bars. Zero-based on purpose, unlike the capacity
   * trend: the summer-to-winter difference is large and real, and a zoomed axis
   * would make a 1 kWh/100 km wobble look like a season. */
  _efTrendSvg(trend) {
    if (!Array.isArray(trend) || trend.length < 2) return "";
    const W = 280;
    const H = 88;
    const pad = 6;
    const bottom = 14;
    const values = trend.map((e) => e.kwh_per_100km);
    const max = Math.max(...values) * 1.15 || 1;
    const slot = (W - 2 * pad) / trend.length;
    // Capped as well as proportional: with two months on file a purely
    // proportional bar is 78 px wide and reads as a block, not a chart.
    const width = Math.min(28, Math.max(4, slot * 0.58));
    const bars = trend
      .map((entry, i) => {
        const x = pad + i * slot + (slot - width) / 2;
        const h = (entry.kwh_per_100km / max) * (H - bottom - pad);
        const y = H - bottom - h;
        // "2026-09" -> "09": a bare month number reads the same in every
        // language, which a translated abbreviation would not.
        const label = String(entry.month || "").slice(5);
        return `<rect class="ef__bar" x="${this._px(x)}" y="${this._px(y)}"
                  width="${this._px(width)}" height="${this._px(
          Math.max(h, 1)
        )}" rx="2"></rect>
                <text class="ef__bar-lbl" x="${this._px(
                  x + width / 2
                )}" y="${H - 3}" text-anchor="middle">${this._esc(label)}</text>`;
      })
      .join("");
    return `
      <svg class="ef__chart" viewBox="0 0 ${W} ${H}" role="img">
        ${bars}
        <text x="${pad}" y="9" class="chg__chart-max">${this._dec(
      max,
      0
    )} kWh/100 km</text>
      </svg>
    `;
  }

  _round(n, dp) {
    const f = Math.pow(10, dp);
    return Math.round(n * f) / f;
  }

  // A figure the card worked out itself (a state object goes through _fmt),
  // rounded to `dp` places and written in the user's number format. A bare
  // `${number}` always prints a point, so a German dashboard read "19.8 kWh"
  // right next to Home Assistant's "110,10 kWh". Display text only -- SVG
  // coordinates go through _px.
  _dec(n, dp) {
    const num = Number(n);
    if (!Number.isFinite(num)) return "—";
    const v = this._round(num, dp) || 0; // never "-0"
    const locale = _numberLocale(this._hass);
    if (locale === null) return String(v);
    try {
      return v.toLocaleString(locale, { maximumFractionDigits: dp });
    } catch (e) {
      return String(v);
    }
  }

  // An SVG coordinate: always a dot decimal, whatever the locale -- "18,4"
  // inside points="…" would draw a different chart, not a German one.
  _px(n) {
    return this._round(n, 1);
  }

  _esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])
    );
  }

  _attr(s) {
    return this._esc(s).replace(/'/g, "&#39;");
  }

  /* ---- tire diagram ----------------------------------------------------- */

  _renderTires(deviceId, entities) {
    // Index tire entities by wheel + metric using the attributes the integration
    // exposes (tire_axle / tire_side / tire_metric), so placement is reliable.
    const wheels = {};
    for (const id of entities) {
      const st = this._st(id);
      const a = st && st.attributes;
      if (!a || a.cluster !== "tire" || !a.tire_axle || !a.tire_side) continue;
      const key = `${a.tire_axle}_${a.tire_side}`;
      (wheels[key] = wheels[key] || {})[a.tire_metric || "other"] = st;
    }

    const name = this._deviceName(deviceId);
    const slots = [
      { key: "row1_left", label: this._t("fl"), full: this._t("front_left") },
      { key: "row1_right", label: this._t("fr"), full: this._t("front_right") },
      { key: "row2_left", label: this._t("rl"), full: this._t("rear_left") },
      { key: "row2_right", label: this._t("rr"), full: this._t("rear_right") },
    ];
    const present = slots.filter((s) => wheels[s.key]);
    const unit = this._lengthUnit(entities);

    const topPhoto = this._topPhoto(entities);
    const sig = this._signature({
      m: "tire",
      top: topPhoto,
      lang: _lang(this._hass),
      paint: this._carPaint(entities),
      unit,
      w: Object.fromEntries(
        Object.entries(wheels).map(([k, m]) => [
          k,
          Object.fromEntries(
            Object.entries(m).map(([mk, s]) => [
              mk,
              // The diagnosis entity's state changes only when the traffic light
              // flips; everything the card draws from it -- remaining mileage,
              // size, tread, season, fitting date -- moves underneath it, so it
              // all has to be in the signature or the card would never redraw.
              mk === "diagnosis" ? this._wearSig(s) : s.state,
            ])
          ),
        ])
      ),
    });
    if (sig === this._sig) return;
    this._sig = sig;

    if (!present.length) {
      this.shadowRoot.innerHTML = `
        ${this._styles()}
        <ha-card>
          ${this._tireHead(name, "—")}
          <div class="empty">${this._t("no_tire_data")}</div>
        </ha-card>`;
      return;
    }

    const summary = this._tireSummary(present.map((s) => wheels[s.key]), unit);

    const colors = {
      fl: this._tireStatus(wheels.row1_left || {}).color,
      fr: this._tireStatus(wheels.row1_right || {}).color,
      rl: this._tireStatus(wheels.row2_left || {}).color,
      rr: this._tireStatus(wheels.row2_right || {}).color,
    };

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        ${this._tireHead(name, summary.overall.t, summary.overall.c)}
        ${this._tireSummaryBar(summary)}
        <div class="tirewrap">
          <div class="tirecar">
            <span class="tirecar__front">${this._t("front")}</span>
            <div class="tirecar__svg">${
              topPhoto
                ? `<img class="tirecar__photo" src="${this._esc(topPhoto)}" alt="" />`
                : this._carSvg(colors, this._carShape(entities))
            }</div>
            ${this._wheelBlock(slots[0], wheels.row1_left, "fl", unit)}
            ${this._wheelBlock(slots[1], wheels.row1_right, "fr", unit)}
            ${this._wheelBlock(slots[2], wheels.row2_left, "rl", unit)}
            ${this._wheelBlock(slots[3], wheels.row2_right, "rr", unit)}
          </div>
        </div>
      </ha-card>`;
    this._wireTaps();
  }

  // Everything the card renders from a diagnosis entity, flattened for the
  // redraw signature.
  _wearSig(st) {
    const a = st.attributes || {};
    return [
      st.state,
      a.due_mileage_km,
      a.dimension,
      a.tread,
      a.tread_manufacturer,
      a.season,
      a.mounting_date,
      a.run_flat,
    ].join("|");
  }

  // The two things that can be wrong with a tyre -- its pressure and its
  // remaining life -- summarised across every wheel BMW reports, plus the
  // combined headline for the card header. Kept separate because "check tyres"
  // must never read as "top up the air".
  _tireSummary(list, unit = "km") {
    const pressures = list.map((w) => this._pressureStatus(w));
    const cls = pressures.map((p) => p.cls);
    const color = cls.includes("low")
      ? "var(--bmw-low)"
      : cls.includes("high")
      ? "var(--bmw-mid)"
      : cls.includes("ok")
      ? "var(--bmw-high)"
      : "var(--divider-color)";
    // The measured spread, not a verdict word -- the header already carries the
    // verdict, and a card that says "Slightly high" twice in 60px says less than
    // one that says which wheels and by how much. Ranked on the raw states
    // because unit conversion is monotonic, so no localized number parsing.
    const pressure = { t: this._pressureRange(list), c: color };

    // The target is per-wheel but shared per axle at worst, so it is only worth
    // a summary line when every wheel agrees on it.
    const targets = list.map((w) => (w.pressureTarget ? this._fmt(w.pressureTarget) : null));
    const known = targets.filter(Boolean);
    const pressureSub =
      known.length && known.length === list.length
        ? known.every((t) => t === known[0])
          ? `${this._t("p_target")} ${this._esc(known[0])}`
          : this._t("p_target_varies")
        : "";

    const wears = list.map((w) => this._tireWear(w)).filter(Boolean);
    const colors = wears.map((w) => w.color);
    const wear = !wears.length
      ? null
      : colors.includes("red")
      ? { t: this._t("w_due"), c: "var(--bmw-low)" }
      : colors.includes("yellow")
      ? { t: this._t("w_soon"), c: "var(--bmw-mid)" }
      : colors.includes("green")
      ? { t: this._t("w_ok"), c: "var(--bmw-high)" }
      : { t: this._t("t_nodata"), c: "var(--divider-color)" };

    // The soonest wheel is the one that decides when the car goes in.
    const dues = wears.map((w) => w.dueKm).filter((v) => v != null);
    const wearSub = dues.length ? `${this._t("wear_due")} ${this._distance(Math.min(...dues), unit)}` : "";

    const worn = colors.includes("red") || colors.includes("yellow");
    const overall = worn
      ? {
          t: this._t("check_tyres"),
          c: colors.includes("red") || cls.includes("low") ? "var(--bmw-low)" : "var(--bmw-mid)",
        }
      : cls.includes("low")
      ? { t: this._t("check_pressure"), c: "var(--bmw-low)" }
      : cls.includes("high")
      ? { t: this._t("slightly_high"), c: "var(--bmw-mid)" }
      : cls.every((c) => c === "ok")
      ? { t: this._t("all_nominal"), c: "var(--bmw-high)" }
      : { t: this._t("of_four", { n: list.length }), c: "var(--secondary-text-color)" };

    return { pressure, pressureSub, wear, wearSub, overall };
  }

  // "280 – 290 kPa" across the reported wheels, or a single value when they
  // agree. Falls back to a dash when no wheel has a measurement.
  _pressureRange(list) {
    const rated = list
      .map((w) => ({ st: w.pressure, n: this._num(w.pressure) }))
      .filter((r) => r.n != null)
      .sort((a, b) => a.n - b.n);
    if (!rated.length) return "—";
    const lo = this._splitValueUnit(rated[0].st);
    const hi = this._splitValueUnit(rated[rated.length - 1].st);
    const unit = hi.unit ? ` ${hi.unit}` : "";
    return lo.value === hi.value ? `${hi.value}${unit}` : `${lo.value} – ${hi.value}${unit}`;
  }

  _tireSummaryBar(s) {
    const cell = (label, v, sub) => `
      <div class="tsum__cell" style="--c:${v.c}">
        <span class="tsum__k">${label}</span>
        <span class="tsum__v"><span class="tstat__dot"></span>${v.t}</span>
        ${sub ? `<span class="tsum__sub">${sub}</span>` : ""}
      </div>`;
    return `
      <div class="tsum">
        ${cell(this._t("sum_pressure"), s.pressure, s.pressureSub)}
        ${s.wear ? cell(this._t("sum_wear"), s.wear, s.wearSub) : ""}
      </div>`;
  }

  _carSvg(c, car = CAR) {
    // Top-down car with each tyre outlined in its tire-status colour.
    return `
      <svg class="carsvg" viewBox="0 0 948 1659" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        ${this._carDefs(car)}
        ${this._carWheels(car, c)}
        ${this._carBody(car)}
      </svg>`;
  }

  // Gradients, the status glow and the clip paths the art and overlays use.
  _carDefs(car) {
    const clips = {
      carClipWs: car.windshield,
      carClipRw: car.rearWindow,
      carClipSr: car.sunroof,
      carClipWf: car.window.f.d,
      carClipWr: car.window.r.d,
    };
    const tone = carTones(car.paint);
    return `
        <defs>
          <linearGradient id="carBody" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stop-color="${tone.bodyEdge}"/>
            <stop offset=".5" stop-color="${tone.bodyMid}"/>
            <stop offset="1" stop-color="${tone.bodyEdge}"/>
          </linearGradient>
          <linearGradient id="carRoof" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stop-color="${tone.roofEdge}"/>
            <stop offset=".5" stop-color="${tone.roofMid}"/>
            <stop offset="1" stop-color="${tone.roofEdge}"/>
          </linearGradient>
          <linearGradient id="carGlass" x1="0" y1="0" x2=".4" y2="1">
            <stop offset="0" stop-color="#43515f"/>
            <stop offset="1" stop-color="#26313c"/>
          </linearGradient>
          <linearGradient id="carTail" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stop-color="#8e1b17"/>
            <stop offset=".6" stop-color="#e4382b"/>
            <stop offset="1" stop-color="#ff7a5c"/>
          </linearGradient>
          <filter id="carGlow" x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="12" result="b"/>
            <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
          </filter>
          ${Object.entries(clips)
            .map(([id, d]) => `<clipPath id="${id}"><path d="${d}"/></clipPath>`)
            .join("")}
        </defs>`;
  }

  // Four tyres peeking out under the body, each outlined (and glowing) in its
  // colour; without a colour (closures) they stay plain rubber.
  _carWheels(car, c = {}) {
    const wheel = ([x, y, w, h], color, flip) =>
      `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="13" class="carsvg__wheel${color ? " carsvg__wheel--lit" : ""}"${
        color ? ` style="stroke:${color}"` : ""
      }${flip ? ` transform="${CAR_FLIP}"` : ""}/>`;
    return (
      wheel(car.tyreFront, c.fl) +
      wheel(car.tyreFront, c.fr, true) +
      wheel(car.tyreRear, c.rl) +
      wheel(car.tyreRear, c.rr, true)
    );
  }

  // Static body art, shared by the tire and closures diagrams; the closures
  // view layers overlays on top. `hide.l`/`hide.r` drop a side mirror whose
  // front door is drawn swung open (the mirror rides on the door).
  _carBody(car, hide = {}) {
    const both = (d, cls) =>
      `<path d="${d}" class="${cls}"/><path d="${d}" class="${cls}" transform="${CAR_FLIP}"/>`;
    const mirrorL = hide.l ? "" : `<path d="${car.mirror}" class="carsvg__mirror"/>`;
    const mirrorR = hide.r ? "" : `<path d="${car.mirror}" class="carsvg__mirror" transform="${CAR_FLIP}"/>`;
    const handles = car.handles
      .map(([x, y]) => `<rect x="${x}" y="${y}" width="7" height="44" rx="3.5" class="carsvg__handle"/>`)
      .join("");
    const sheens = Object.entries(car.sheen)
      .flatMap(([clip, ds]) => ds.map((d) => `<path d="${d}" class="carsvg__sheen" clip-path="url(#${clip})"/>`))
      .join("");
    return `
        ${mirrorL}${mirrorR}
        <path d="${car.body}" class="carsvg__body"/>
        ${both(car.door.f.d, "carsvg__shut")}
        ${both(car.door.r.d, "carsvg__shut")}
        <path d="${car.hood}" class="carsvg__hood"/>
        <path d="${car.creases}" class="carsvg__crease"/>
        <path d="${car.trunk}" class="carsvg__shut"/>
        ${both(car.shuts, "carsvg__shut")}
        <g>${handles}</g><g transform="${CAR_FLIP}">${handles}</g>
        ${both(car.headlight, "carsvg__light")}
        ${both(car.drl, "carsvg__drl")}
        ${both(car.tail, "carsvg__tail")}
        ${both(car.tailCore, "carsvg__tailcore")}
        ${car.grille}
        <path d="${car.windshield}" class="carsvg__glass carsvg__frame"/>
        <path d="${car.roof}" class="carsvg__roof"/>
        <path d="${car.sunroof}" class="carsvg__glass"/>${car.roofDetail || ""}
        ${both(car.window.f.d, "carsvg__glass")}
        ${both(car.window.r.d, "carsvg__glass")}
        <path d="${car.rearWindow}" class="carsvg__glass carsvg__frame"/>
        ${both(car.bPillar, "carsvg__pillar")}
        ${sheens}`;
  }

  // One wheel: pressure headline, then that wheel's own fitment. BMW reports
  // size and tread per wheel and staggered setups are common (the i5 runs 245
  // front / 275 rear), so these facts belong beside their wheel, not in a
  // single line under the diagram that would have to pick one to show.
  _wheelBlock(slot, wheel, pos, unit = "km") {
    const status = wheel ? this._tireStatus(wheel) : { label: "—", color: "var(--divider-color)" };
    const pressure = wheel && wheel.pressure;
    const target = wheel && wheel.pressureTarget;
    const temp = wheel && wheel.temperature;
    const tapId = (pressure && pressure.entity_id) || (temp && temp.entity_id) || "";
    const pv = pressure ? this._splitValueUnit(pressure) : { value: "—", unit: "" };
    const wear = wheel ? this._tireWear(wheel) : null;
    const sub = [target ? `◎ ${this._fmt(target)}` : null, temp ? this._fmt(temp) : null]
      .filter(Boolean)
      .join(" · ");

    const meta = [];
    if (wear) {
      if (wear.dimension) meta.push(this._esc(wear.dimension));
      const tread = [wear.manufacturer, wear.tread].filter(Boolean).join(" ");
      if (tread) meta.push(this._esc(tread));
      const fitline = [
        wear.season ? this._seasonLabel(wear.season) : null,
        wear.runFlat ? this._t("runflat") : null,
        wear.fitted ? `${this._t("fitted")} ${this._fmtDay(wear.fitted)}` : null,
      ]
        .filter(Boolean)
        .join(" · ");
      if (fitline) meta.push(fitline);
      if (wear.dueKm != null)
        meta.push(
          `<b class="wlabel__due">${this._t("wear_due")} ${this._distance(wear.dueKm, unit)}</b>`
        );
    }

    return `
      <button class="wlabel wlabel--${pos}" style="--c:${status.color}" data-entity="${tapId}" title="${slot.full}">
        <span class="wlabel__pos"><b>${slot.label}</b><span class="wlabel__badge">${status.label}</span></span>
        <span class="wlabel__val">${pv.value}${pv.unit ? `<i>${pv.unit}</i>` : ""}</span>
        ${sub ? `<span class="wlabel__sub">${sub}</span>` : ""}
        ${meta.map((m) => `<span class="wlabel__meta">${m}</span>`).join("")}
      </button>`;
  }

  // BMW sends the season as a SUMMER/WINTER/ALL_SEASON token; anything else is
  // passed through so an unknown token still tells the user something.
  _seasonLabel(raw) {
    const key = {
      SUMMER: "season_summer",
      WINTER: "season_winter",
      ALL_SEASON: "season_all",
      ALLSEASON: "season_all",
    }[String(raw).toUpperCase()];
    return key ? this._t(key) : this._esc(raw);
  }

  // A bare calendar day ("2026-06-13"), no time component. Numeric-short: it
  // shares a line with the season inside a wheel column, and a spelled-out
  // month wraps that line on any narrow dashboard.
  _fmtDay(iso) {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return this._esc(iso);
    try {
      return d.toLocaleDateString(_lang(this._hass), { dateStyle: "short" });
    } catch (e) {
      return this._esc(iso);
    }
  }

  _tireHead(name, statusText, statusColor) {
    return `
      <div class="chead">
        <ha-icon icon="mdi:car-tire-alert"></ha-icon>
        <div class="chead__text">
          <span class="chead__title">${this._t("tires")}</span>
          <span class="chead__sub">${this._esc(name)}</span>
        </div>
        ${
          statusText
            ? `<span class="tstat" style="--c:${statusColor || "var(--secondary-text-color)"}"><span class="tstat__dot"></span>${statusText}</span>`
            : ""
        }
      </div>`;
  }

  // BMW's own wear traffic light for a wheel, from the REST tyre diagnosis.
  // Returns null when the car has no diagnosis (most do not until a tyre
  // service has been recorded), so pressure alone decides in that case.
  _tireWear(wheel) {
    const st = wheel && wheel.diagnosis;
    const a = st && st.attributes;
    if (!a) return null;
    return {
      color: st.state,                       // green | yellow | red | grey
      // BMW's tyreWear.value is a rendering of dueMileage, not a tread depth,
      // so only the numeric km figure is kept -- showing both read as two
      // different facts when they are one.
      dueKm: a.due_mileage_km ?? null,
      season: a.season || null,
      dimension: a.dimension || null,
      tread: a.tread || null,
      manufacturer: a.tread_manufacturer || null,
      runFlat: a.run_flat === true,
      fitted: a.mounting_date || null,
      entity_id: st.entity_id,
    };
  }

  // Combined per-wheel status. Wear outranks pressure: a bald tyre at perfect
  // pressure is still the more urgent thing to say, and pressure is trivially
  // fixable where wear is not.
  _tireStatus(wheel) {
    const wear = this._tireWear(wheel);
    if (wear && wear.color === "red")
      return { cls: "low", label: this._t("t_wear"), color: "var(--bmw-low)" };
    if (wear && wear.color === "yellow")
      return { cls: "wear", label: this._t("t_wear"), color: "var(--bmw-mid)" };
    return this._pressureStatus(wheel);
  }

  // Pressure alone, so the summary can report it separately from wear.
  _pressureStatus(wheel) {
    const cur = this._num(wheel.pressure);
    const tgt = this._num(wheel.pressureTarget);
    if (cur == null) return { cls: "na", label: this._t("t_nodata"), color: "var(--divider-color)" };
    if (tgt == null) return { cls: "na", label: this._t("t_current"), color: "var(--divider-color)" };
    const devPct = ((cur - tgt) / tgt) * 100;
    // Asymmetric on purpose. BMW's target is the cold placard pressure while the
    // measurement is whatever the tyre is right now, and a tyre that has been
    // driven on reads 8-10% high -- a symmetric ±4% band flagged every wheel of
    // a perfectly healthy car. Under-inflation is the condition worth an early
    // hint (TPMS itself only warns near -20%), over-inflation only past what
    // warm-up explains.
    if (devPct < -8) return { cls: "low", label: this._t("t_low"), color: "var(--bmw-low)" };
    if (devPct > 15) return { cls: "high", label: this._t("t_high"), color: "var(--bmw-mid)" };
    return { cls: "ok", label: this._t("t_ok"), color: "var(--bmw-high)" };
  }

  /* ---- closures / security diagram -------------------------------------- */

  // Descriptor paths for every closure signal the card knows how to place.
  // The side slots are a left-hand-drive car's: BMW names side doors and
  // windows by seat, so on right-hand drive `_renderClosures` reads each slot
  // from its mirror image (the driver's door is then the right front one).
  static CLOSURE_PATHS = {
    doorOpen: {
      lf: "vehicle.cabin.door.row1.driver.isOpen",
      rf: "vehicle.cabin.door.row1.passenger.isOpen",
      lr: "vehicle.cabin.door.row2.driver.isOpen",
      rr: "vehicle.cabin.door.row2.passenger.isOpen",
    },
    doorPos: {
      lf: "vehicle.cabin.door.row1.driver.position",
      rf: "vehicle.cabin.door.row1.passenger.position",
      lr: "vehicle.cabin.door.row2.driver.position",
      rr: "vehicle.cabin.door.row2.passenger.position",
    },
    window: {
      lf: "vehicle.cabin.window.row1.driver.status",
      rf: "vehicle.cabin.window.row1.passenger.status",
      lr: "vehicle.cabin.window.row2.driver.status",
      rr: "vehicle.cabin.window.row2.passenger.status",
    },
    hood: "vehicle.body.hood.isOpen",
    trunk: "vehicle.body.trunk.isOpen",
    rearWindow: "vehicle.body.trunk.window.isOpen",
    sunroof: ["vehicle.cabin.sunroof.overallStatus", "vehicle.cabin.sunroof.status"],
    // Central lock: door.status first. Both descriptors carry the same lock
    // vocabulary, but door.lock.status is NOT streamable -- it only refreshes on
    // a quota-limited REST fetch, so it can sit on a stale value for days while
    // door.status follows the actual lock within seconds (issue #8).
    lock: ["vehicle.cabin.door.status", "vehicle.cabin.door.lock.status"],
    alarmArm: "vehicle.vehicle.antiTheftAlarmSystem.alarm.armStatus",
    alarmOn: "vehicle.vehicle.antiTheftAlarmSystem.alarm.isOn",
  };

  /** Everything the car says about its doors, windows, lids, lock and alarm,
   * read once for the closures view and for the badges on the overview's photo. */
  _closureModel(entities) {
    const P = BavarianDataCard.CLOSURE_PATHS;
    const byDesc = {};
    for (const id of entities) {
      const st = this._st(id);
      const d = st && st.attributes && st.attributes.descriptor;
      if (d) byDesc[d] = id;
    }
    const find = (path) =>
      Array.isArray(path) ? path.map((p) => byDesc[p]).find(Boolean) : byDesc[path];
    // Like find(), but skips candidates that carry no usable value, so a
    // preferred-but-silent descriptor does not hide a real value on the next
    // one. Falls back to find() when none of them has a value yet.
    const findWithValue = (path) => {
      const ids = (Array.isArray(path) ? path : [path]).map((p) => byDesc[p]).filter(Boolean);
      const usable = ids.find((id) => {
        const st = this._st(id);
        const raw = st ? String(st.state).trim().toLowerCase() : "";
        return st && !UNAVAILABLE.has(raw) && raw !== "invalid";
      });
      return usable || ids[0];
    };

    const ALERT = "var(--bmw-low)";
    const WARN = "var(--bmw-mid)";
    const OK = "var(--bmw-high)";

    // The descriptor slot each drawn slot reads: itself on left-hand drive, its
    // mirror image on right-hand drive, where the driver sits on the right.
    const rhd = this._steering(entities) === "right";
    const MIRROR = { lf: "rf", rf: "lf", lr: "rr", rr: "lr" };
    const seat = (k) => (rhd ? MIRROR[k] : k);

    // Per-slot doors (prefer isOpen; fall back to position sensor).
    const doors = {};
    for (const k of ["lf", "rf", "lr", "rr"]) {
      const id = find(P.doorOpen[seat(k)]) || find(P.doorPos[seat(k)]);
      if (!id) continue;
      const st = this._st(id);
      doors[k] = { id, open: this._openState(st) };
    }
    // Per-slot windows.
    const windows = {};
    for (const k of ["lf", "rf", "lr", "rr"]) {
      const id = find(P.window[seat(k)]);
      if (!id) continue;
      const st = this._st(id);
      windows[k] = { id, open: this._openState(st), partial: this._isPartialState(st) };
    }
    const single = (path) => {
      const id = find(path);
      if (!id) return null;
      const st = this._st(id);
      return { id, st, open: this._openState(st), partial: this._isPartialState(st) };
    };
    const hood = single(P.hood);
    const trunk = single(P.trunk);
    const rearWindow = single(P.rearWindow);
    const sunroof = single(P.sunroof);

    const lockId = findWithValue(P.lock);
    const lock = this._lockInfo(lockId ? this._st(lockId) : null);
    const armId = find(P.alarmArm);
    const onId = find(P.alarmOn);
    const alarm = this._alarmInfo(armId ? this._st(armId) : null, onId ? this._st(onId) : null);

    const present =
      Object.keys(doors).length +
      Object.keys(windows).length +
      [hood, trunk, rearWindow, sunroof].filter(Boolean).length +
      (lockId ? 1 : 0) +
      (alarm ? 1 : 0);

    // Build the itemised list: lock + alarm always shown; then each open part.
    const openItems = [];
    const slotLabel = { lf: "front_left", rf: "front_right", lr: "rear_left", rr: "rear_right" };
    for (const k of ["lf", "rf", "lr", "rr"]) {
      if (doors[k] && doors[k].open) {
        openItems.push({
          id: doors[k].id,
          label: `${this._t(slotLabel[k])} · ${this._t("door_word")}`,
          value: this._t("state_open"),
          color: ALERT,
        });
      }
    }
    for (const k of ["lf", "rf", "lr", "rr"]) {
      if (windows[k] && windows[k].open) {
        openItems.push({
          id: windows[k].id,
          label: `${this._t(slotLabel[k])} · ${this._t("window_word")}`,
          value: this._t(windows[k].partial ? "state_tilted" : "state_open"),
          color: WARN,
        });
      }
    }
    const bodyPart = (part, key, color) => {
      if (part && part.open) {
        openItems.push({
          id: part.id,
          label: this._t(key),
          value: this._t(part.partial ? "state_tilted" : "state_open"),
          color,
        });
      }
    };
    bodyPart(hood, "hood_word", ALERT);
    bodyPart(trunk, "trunk_word", ALERT);
    bodyPart(rearWindow, "rear_window_word", WARN);
    bodyPart(sunroof, "sunroof_word", WARN);

    const anyBodyOpen =
      (hood && hood.open) || (trunk && trunk.open) ||
      Object.values(doors).some((d) => d.open);
    const anyGlassOpen =
      Object.values(windows).some((w) => w.open) ||
      (sunroof && sunroof.open) || (rearWindow && rearWindow.open);
    return {
      doors, windows, hood, trunk, rearWindow, sunroof, lockId, lock, armId, onId, alarm,
      present, rhd, openItems, anyBodyOpen, anyGlassOpen, ALERT, WARN, OK,
    };
  }

  _renderClosures(deviceId, entities) {
    const name = this._deviceName(deviceId);
    const {
      doors, windows, hood, trunk, rearWindow, sunroof, lockId, lock, armId, onId, alarm,
      present, rhd, openItems, anyBodyOpen, anyGlassOpen, ALERT, WARN, OK,
    } = this._closureModel(entities);

    // Change-detection signature.
    const stateOf = (id) => (id && this._st(id) ? this._st(id).state : null);
    const sig = this._signature({
      m: "clo",
      lang: _lang(this._hass),
      paint: this._carPaint(entities),
      rhd,
      doors: Object.fromEntries(Object.entries(doors).map(([k, v]) => [k, stateOf(v.id)])),
      wins: Object.fromEntries(Object.entries(windows).map(([k, v]) => [k, stateOf(v.id)])),
      hood: hood && stateOf(hood.id),
      trunk: trunk && stateOf(trunk.id),
      rw: rearWindow && stateOf(rearWindow.id),
      sr: sunroof && stateOf(sunroof.id),
      lock: stateOf(lockId),
      arm: stateOf(armId),
      on: stateOf(onId),
    });
    if (sig === this._sig) return;
    this._sig = sig;

    if (!present) {
      this.shadowRoot.innerHTML = `
        ${this._styles()}
        <ha-card>
          ${this._closuresHead(name, null)}
          <div class="empty">${this._t("closures_none")}</div>
        </ha-card>`;
      return;
    }

    const overall = this._closuresOverall({ anyBodyOpen, anyGlassOpen, count: openItems.length, lock, alarm });

    const rows = [];
    if (lockId) {
      rows.push({ id: lockId, label: this._t("central_lock"), value: lock.label, color: lock.color });
    }
    if (alarm) {
      rows.push({ id: armId || onId, label: this._t("alarm_word"), value: alarm.label, color: alarm.color });
    }
    rows.push(...openItems);
    if (!openItems.length) {
      rows.push({ id: "", label: this._t("all_closed"), value: "✓", color: OK });
    }

    const diagram = this._carSvgClosures({
      doors, windows, hood, trunk, rearWindow, sunroof, lock, lockId,
      colors: { ALERT, WARN, OK },
      car: this._carShape(entities),
    });

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        ${this._closuresHead(name, overall)}
        <div class="closcar">${diagram}</div>
        <div class="list">
          ${rows
            .map(
              (r) => `<button class="item" data-entity="${r.id}">
                <span class="item__name" title="${r.label}"><span class="item__dot" style="background:${r.color}"></span>${r.label}</span>
                <span class="item__val" style="color:${r.color}">${r.value}</span>
              </button>`
            )
            .join("")}
        </div>
      </ha-card>`;
    this._wireTaps();
  }

  /** BMW's real top view of the car (MINIs only), signed, or null. */
  _topPhoto(entities) {
    const cfg = this._config || {};
    const id = cfg.image || entities.find((e) => e.startsWith("image."));
    const st = id && this._st(id);
    const top = st && st.attributes && st.attributes.configurator_top;
    return top && top.path ? this._signedUrl(top.path) : null;
  }

  /** The factory equipment (`view: equipment`): BMW's configurator names each
   * option code the car was built with. Paint, wheels and trim lead, then the
   * packages and the rest; what BMW itself treats as technical (refrigerant,
   * paperwork language) is folded away. Names come in the language BMW's
   * market for the car offers, which may not be the dashboard's. */
  _renderEquipment(deviceId, entities) {
    const name = this._deviceName(deviceId);
    const cfg = this._config || {};
    const id = cfg.image || entities.find((e) => e.startsWith("image."));
    const st = id && this._st(id);
    const list = (st && st.attributes && Array.isArray(st.attributes.equipment) && st.attributes.equipment) || [];
    const items = list.filter((e) => e && typeof e.name === "string" && typeof e.code === "string");
    const sig = this._signature({
      m: "eq",
      lang: _lang(this._hass),
      name,
      items: items.map((e) => [e.code, e.name, e.kind, e.shown]),
    });
    if (sig === this._sig) return;
    this._sig = sig;

    const head = `
      <div class="chead">
        <ha-icon icon="mdi:format-list-checks"></ha-icon>
        <div class="chead__text">
          <span class="chead__title">${this._t("eq_title")}</span>
          <span class="chead__sub">${this._esc(name)}</span>
        </div>
        ${items.length ? `<span class="tstat" style="--c:var(--secondary-text-color)">${this._t("eq_count", { n: items.filter((e) => e.shown !== false).length })}</span>` : ""}
      </div>`;
    if (!items.length) {
      this.shadowRoot.innerHTML = `
        ${this._styles()}
        <ha-card>
          ${head}
          <div class="empty">${this._t("eq_empty")}</div>
        </ha-card>`;
      return;
    }
    const looks = items.filter((e) => e.shown !== false && ["paint", "rim", "trim"].includes(e.kind));
    const packages = items.filter((e) => e.shown !== false && e.kind === "package");
    const rest = items.filter((e) => e.shown !== false && !["paint", "rim", "trim", "package"].includes(e.kind));
    const technical = items.filter((e) => e.shown === false);
    const rows = (group) =>
      group
        .map(
          (e) => `<div class="eq__row"><span class="eq__name">${this._esc(e.name)}</span><span class="eq__code">${this._esc(e.code)}</span></div>`
        )
        .join("");
    const section = (title, group) =>
      group.length ? `<div class="eq__group"><div class="eq__title">${title}</div>${rows(group)}</div>` : "";
    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        ${head}
        <div class="eq">
          ${section(this._t("eq_looks"), looks)}
          ${section(this._t("eq_packages"), packages)}
          ${section(this._t("eq_features"), rest)}
          ${
            technical.length
              ? `<details class="eq__more"><summary>${this._t("eq_technical", { n: technical.length })}</summary>${rows(technical)}</details>`
              : ""
          }
        </div>
      </ha-card>`;
  }

  _closuresHead(name, overall) {
    return `
      <div class="chead">
        <ha-icon icon="mdi:car-door-lock"></ha-icon>
        <div class="chead__text">
          <span class="chead__title">${this._t("cl_closures")}</span>
          <span class="chead__sub">${this._esc(name)}</span>
        </div>
        ${
          overall
            ? `<span class="tstat" style="--c:${overall.color}"><span class="tstat__dot"></span>${overall.label}</span>`
            : ""
        }
      </div>`;
  }

  _closuresOverall({ anyBodyOpen, anyGlassOpen, count, lock, alarm }) {
    if (alarm && alarm.key === "triggered") return { label: alarm.label, color: "var(--bmw-low)" };
    if (anyBodyOpen) return { label: this._t("n_open", { n: count }), color: "var(--bmw-low)" };
    if (anyGlassOpen) return { label: this._t("windows_open"), color: "var(--bmw-mid)" };
    if (lock.key === "unlocked") return { label: this._t("unlocked"), color: "var(--bmw-low)" };
    if (lock.key === "partial") return { label: this._t("partially_locked"), color: "var(--bmw-mid)" };
    if (lock.key === "secured" || lock.key === "locked") return { label: lock.label, color: "var(--bmw-high)" };
    return { label: this._t("all_closed"), color: "var(--bmw-high)" };
  }

  // Same top-down car as the tire view, with closure overlays layered on top:
  // an open door swings out on its hinge (mirror and all), open glass glows
  // amber, an open hood/trunk glows red, and a padlock on the roof reflects
  // the lock state. Every part is tappable.
  _carSvgClosures(d) {
    const { ALERT, WARN } = d.colors;
    const car = d.car || CAR;
    // Keys are side + row: "lf" = left front, "rr" = right rear.
    const flip = (k) => (k && k[0] === "r" ? ` transform="${CAR_FLIP}"` : "");
    const hit = (path, id, k) => `<path d="${path}" class="cldiag__hit" data-entity="${id}"${flip(k)}/>`;
    const lit = (path, color, k, extra = "") =>
      `<g class="cldiag__lit" style="--c:${color}"${flip(k)}>` +
      `<path d="${path}" class="cldiag__zone"/><path d="${path}" class="cldiag__core"/>${extra}</g>`;
    const parts = [];

    // Doors: swung open when open, always a tap zone.
    for (const k of ["lf", "rf", "lr", "rr"]) {
      const door = d.doors[k];
      if (!door) continue;
      const g = car.door[k[1]];
      if (door.open) {
        const mirror = k[1] === "f" ? `<path d="${car.mirror}" class="carsvg__mirror"/>` : "";
        parts.push(
          `<g class="cldiag__lit" style="--c:${ALERT}"${flip(k)}>` +
            `<path d="${g.sill}" class="cldiag__sill"/><path d="${g.shadow}" class="cldiag__sillshadow"/>` +
            `<g transform="${g.hinge}">` +
            `<path d="${g.skin}" class="cldiag__door"/><path d="${g.trim}" class="cldiag__doortrim"/>` +
            `<path d="${g.edge}" class="cldiag__dooredge"/>${mirror}</g></g>`
        );
      }
      parts.push(hit(g.d, door.id, k));
    }
    // Hood / trunk glow red when open.
    const zone = (part, path) => {
      if (!part) return;
      if (part.open) parts.push(lit(path, ALERT));
      parts.push(hit(path, part.id));
    };
    zone(d.hood, car.hood);
    zone(d.trunk, car.trunk);
    // Glass (windows / sunroof / rear window) glows amber when open; a side
    // window also shows the lowered pane's top edge.
    const glass = (part, path, k, extra) => {
      if (!part) return;
      if (part.open) parts.push(lit(path, WARN, k, extra));
      parts.push(hit(path, part.id, k));
    };
    for (const k of ["lf", "rf", "lr", "rr"]) {
      const w = d.windows[k];
      const g = car.window[k[1]];
      const [x1, x2] = g.dropX || [200, 300];
      if (w) glass(w, g.d, k, `<path d="M${x1} ${g.drop} L${x2} ${g.drop}" class="cldiag__drop" clip-path="url(#${g.clip})"/>`);
    }
    glass(d.sunroof, car.sunroof);
    glass(d.rearWindow, car.rearWindow);

    // Padlock on the sunroof (open shackle when unlocked/unknown).
    const locked = d.lock.key === "locked" || d.lock.key === "secured";
    const shackle = locked ? "M447 880 V850 a27 27 0 0 1 54 0 V880" : "M447 880 V850 a27 27 0 0 1 54 0";
    const padlock = d.lockId
      ? `<g class="cldiag__lock" data-entity="${d.lockId}" style="--c:${d.lock.color}">
           <path d="${shackle}" class="cldiag__shackle"/>
           <rect x="422" y="880" width="104" height="76" rx="14" class="cldiag__lockbody"/>
         </g>`
      : "";

    const hide = { l: d.doors.lf && d.doors.lf.open, r: d.doors.rf && d.doors.rf.open };
    return `
      <svg class="carsvg" viewBox="0 0 948 1659" xmlns="http://www.w3.org/2000/svg">
        ${this._carDefs(car)}
        ${this._carWheels(car)}
        ${this._carBody(car, hide)}
        ${parts.join("\n        ")}
        ${padlock}
      </svg>`;
  }

  // true = open, false = closed, null = unknown/unavailable. Understands the
  // catalogue's OPEN/CLOSED/INTERMEDIATE/TILT vocabulary, boolean on/off/true/
  // false, and numeric door-position percentages.
  _openState(st) {
    if (!st) return null;
    const raw = String(st.state).trim().toLowerCase();
    if (UNAVAILABLE.has(raw) || raw === "invalid") return null;
    if (/^-?\d+(\.\d+)?$/.test(raw)) return Number(raw) > 0;
    if (/\b(closed|secured|off|false)\b/.test(raw)) return false;
    if (/(open|tilt|intermediate|ajar|unlocked|\btrue\b|\bon\b)/.test(raw)) return true;
    if (raw === "locked") return false;
    return null;
  }

  _isPartialState(st) {
    if (!st) return false;
    return /intermediate|tilt/.test(String(st.state).toLowerCase());
  }

  _lockInfo(st) {
    const raw = st ? String(st.state).trim().toUpperCase() : "";
    if (!st || UNAVAILABLE.has(raw.toLowerCase()) || raw === "INVALID" || raw === "")
      return { key: "unknown", color: "var(--divider-color)", label: "—" };
    if (raw.includes("SECURED")) return { key: "secured", color: "var(--bmw-high)", label: this._t("secured") };
    if (raw.includes("SELECTIVE")) return { key: "partial", color: "var(--bmw-mid)", label: this._t("partially_locked") };
    if (raw.includes("UNLOCK")) return { key: "unlocked", color: "var(--bmw-low)", label: this._t("unlocked") };
    if (raw.includes("LOCK")) return { key: "locked", color: "var(--bmw-high)", label: this._t("locked") };
    return { key: "unknown", color: "var(--divider-color)", label: this._fmt(st) };
  }

  _alarmInfo(armSt, onSt) {
    if (!armSt && !onSt) return null;
    const onRaw = onSt ? String(onSt.state).trim().toLowerCase() : "";
    const honking = onSt && !UNAVAILABLE.has(onRaw) && /^(on|true)$/.test(onRaw);
    if (honking) return { key: "triggered", color: "var(--bmw-low)", label: this._t("alarm_triggered") };
    const armRaw = armSt ? String(armSt.state).trim().toLowerCase() : "";
    const known = armSt && !UNAVAILABLE.has(armRaw) && armRaw !== "invalid";
    if (!known) return { key: "unknown", color: "var(--divider-color)", label: "—" };
    if (armRaw === "unarmed")
      return { key: "disarmed", color: "var(--secondary-text-color)", label: this._t("alarm_disarmed") };
    return { key: "armed", color: "var(--bmw-high)", label: this._t("alarm_armed") };
  }

  _splitValueUnit(st) {
    const formatted = this._fmt(st);
    if (formatted === "—") return { value: "—", unit: "" };
    const idx = formatted.indexOf(" ");
    if (idx === -1) return { value: formatted, unit: "" };
    return { value: formatted.slice(0, idx), unit: formatted.slice(idx + 1) };
  }

  _renderMessage(title, html) {
    this._sig = null;
    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        <div class="msg">
          <ha-icon icon="mdi:car-off"></ha-icon>
          <div class="msg__title">${title}</div>
          <div class="msg__body">${html}</div>
        </div>
      </ha-card>`;
  }

  _wireTaps() {
    this.shadowRoot.querySelectorAll("[data-entity]").forEach((el) => {
      const id = el.getAttribute("data-entity");
      if (!id) {
        el.classList.add("is-static");
        return;
      }
      el.addEventListener("click", () => this._moreInfo(id));
    });
  }

  _moreInfo(entityId) {
    const ev = new Event("hass-more-info", { bubbles: true, composed: true });
    ev.detail = { entityId };
    this.dispatchEvent(ev);
  }

  _styles() {
    return `
    <style>
      :host {
        --bmw-charge: #2f80ed;
        --bmw-high: #29a36a;
        --bmw-mid: #e6a417;
        --bmw-low: #d64545;
      }
      ha-card {
        overflow: hidden;
        padding: 0;
      }
      * { box-sizing: border-box; }
      button {
        font: inherit;
        color: inherit;
        background: none;
        border: 0;
        padding: 0;
        text-align: left;
        cursor: pointer;
      }
      button.is-static { cursor: default; }

      /* hero */
      .hero {
        position: relative;
        aspect-ratio: 16 / 9;
        background: var(--secondary-background-color);
        display: flex;
        align-items: center;
        justify-content: center;
      }
      .hero__img {
        width: 100%;
        height: 100%;
        object-fit: contain;
        object-position: center 60%;
      }
      .hero__spin {
        position: absolute; inset: 0;
        background-repeat: no-repeat;
        cursor: grab;
        touch-action: pan-y;
        user-select: none;
      }
      .hero__spin:active { cursor: grabbing; }
      .tirecar__photo { width: 100%; height: 100%; object-fit: contain; display: block; }
      .eq { padding: 4px 16px 14px; }
      .eq__group + .eq__group, .eq__more { margin-top: 12px; }
      .eq__title {
        font-size: 0.72rem; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
        color: var(--secondary-text-color);
        margin: 4px 0 4px;
      }
      .eq__row {
        display: flex; align-items: baseline; justify-content: space-between; gap: 12px;
        padding: 5px 0;
        border-bottom: 1px solid var(--divider-color);
        font-size: 0.9rem;
      }
      .eq__row:last-child { border-bottom: none; }
      .eq__name { min-width: 0; overflow-wrap: anywhere; }
      .eq__code { flex: none; font-size: 0.72rem; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
      .eq__more summary { cursor: pointer; font-size: 0.85rem; color: var(--secondary-text-color); padding: 4px 0; }
      .hero--inset .hero__spin-icon { display: none; }
      .hero__chips {
        position: absolute; left: 16px; top: 52px;
        display: flex; flex-direction: column; align-items: flex-start; gap: 5px;
        max-width: 62%;
        pointer-events: none;
      }
      .hero__chip {
        display: inline-flex; align-items: center; gap: 5px;
        background: rgba(0,0,0,0.55);
        color: #fff;
        border-left: 3px solid var(--chip, transparent);
        border-radius: 999px;
        padding: 3px 10px 3px 7px;
        font-size: 0.74rem;
        font-weight: 500;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        max-width: 100%;
      }
      .hero__chip ha-icon { --mdc-icon-size: 15px; color: var(--chip, #fff); flex: none; }
      .hero__inset {
        position: absolute; right: 12px; bottom: 10px;
        height: 46%;
        aspect-ratio: 948 / 1659;
        filter: drop-shadow(0 1px 3px rgba(0,0,0,0.35));
        pointer-events: none;
      }
      .hero__inset svg { width: 100%; height: 100%; display: block; }
      .hero__mode {
        position: absolute; right: 14px; top: 56px;
        width: 34px; height: 34px;
        display: flex; align-items: center; justify-content: center;
        border: none; border-radius: 50%;
        background: rgba(0,0,0,0.46);
        color: #fff;
        cursor: pointer;
        padding: 0;
      }
      .hero__mode ha-icon { --mdc-icon-size: 19px; }
      .pano-wrap { position: absolute; inset: 0; container-type: size; }
      .pano {
        --half: 50cqw;
        position: absolute; inset: 0;
        overflow: hidden;
        perspective: var(--half);
        cursor: grab;
        touch-action: none;
        user-select: none;
        background: #111;
      }
      .pano:active { cursor: grabbing; }
      .pano__cube {
        position: absolute; left: 50%; top: 50%;
        width: 0; height: 0;
        transform-style: preserve-3d;
      }
      .pano__face {
        position: absolute;
        width: calc(var(--half) * 2 + 2px);
        height: calc(var(--half) * 2 + 2px);
        left: calc(var(--half) * -1 - 1px);
        top: calc(var(--half) * -1 - 1px);
        background-repeat: no-repeat;
        backface-visibility: hidden;
      }
      .hero__caption {
        position: absolute; left: 16px; bottom: 12px;
        background: rgba(0,0,0,0.46);
        color: #fff;
        border-radius: 999px;
        padding: 3px 10px;
        font-size: 0.72rem;
        pointer-events: none;
      }
      .hero__spin-icon {
        position: absolute; right: 12px; bottom: 10px;
        --mdc-icon-size: 20px;
        color: var(--secondary-text-color);
        opacity: 0.7;
        pointer-events: none;
      }
      .hero__placeholder {
        --mdc-icon-size: 72px;
        color: var(--disabled-text-color);
      }
      .hero__scrim {
        position: absolute; inset: 0;
        background: linear-gradient(180deg, rgba(0,0,0,0.42) 0%, rgba(0,0,0,0) 34%);
        pointer-events: none;
      }
      .hero__top {
        position: absolute; top: 0; left: 0; right: 0;
        display: flex; align-items: flex-start; justify-content: space-between; gap: 8px;
        padding: 14px 16px;
      }
      .hero__name {
        color: #fff;
        font-size: 1.15rem;
        font-weight: 600;
        letter-spacing: 0.01em;
        text-shadow: 0 1px 3px rgba(0,0,0,0.55);
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
      }
      /* "trip in progress" badge, bottom-left of the hero so it never competes
         with the vehicle name or the freshness pill above it. */
      .hero__trip {
        position: absolute; left: 16px; bottom: 12px;
        display: inline-flex; align-items: center; gap: 7px;
        max-width: calc(100% - 32px);
        background: rgba(0,0,0,0.46);
        color: #fff;
        border-radius: 999px;
        padding: 5px 12px;
        font-size: 0.74rem;
        font-weight: 600;
        backdrop-filter: blur(3px);
        box-shadow: 0 1px 4px rgba(0,0,0,0.3);
      }
      .hero__trip-dot {
        width: 8px; height: 8px; border-radius: 50%;
        background: var(--bmw-charge, #4cc2ff);
        flex: 0 0 auto;
        animation: bd-trip-pulse 2s ease-in-out infinite;
      }
      .hero__trip-sub {
        font-weight: 500;
        opacity: 0.85;
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
      }
      @keyframes bd-trip-pulse {
        0%, 100% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.45; transform: scale(0.82); }
      }
      /* Respect a reduced-motion preference: the badge still reads as live from
         its colour, so the pulse is simply dropped. */
      @media (prefers-reduced-motion: reduce) {
        .hero__trip-dot { animation: none; }
      }
      .pill {
        display: inline-flex; align-items: center; gap: 6px;
        background: rgba(0,0,0,0.38);
        color: #fff;
        border-radius: 999px;
        padding: 4px 10px;
        font-size: 0.72rem;
        font-weight: 500;
        backdrop-filter: blur(3px);
        white-space: nowrap;
      }
      .dot { width: 7px; height: 7px; border-radius: 50%; }
      .dot--live { background: #37d67a; box-shadow: 0 0 0 0 rgba(55,214,122,0.6); animation: pulse 2.6s infinite; }
      .dot--stale { background: #c9a227; }
      @keyframes pulse {
        0% { box-shadow: 0 0 0 0 rgba(55,214,122,0.55); }
        70% { box-shadow: 0 0 0 6px rgba(55,214,122,0); }
        100% { box-shadow: 0 0 0 0 rgba(55,214,122,0); }
      }

      /* band: gauge + lead metrics */
      .band {
        display: flex;
        align-items: center;
        gap: 18px;
        padding: 18px 18px 8px;
      }
      .gauge {
        position: relative;
        flex: 0 0 auto;
      }
      .gauge__ring {
        width: 96px; height: 96px;
        border-radius: 50%;
        background:
          radial-gradient(closest-side, var(--card-background-color) 70%, transparent 71% 100%),
          conic-gradient(var(--ring) calc(var(--pct) * 1%), var(--divider-color) 0);
        display: grid; place-items: center;
        transition: background 0.6s ease;
      }
      .gauge__hole { text-align: center; line-height: 1; }
      .gauge__val {
        font-size: 1.7rem; font-weight: 600;
        font-variant-numeric: tabular-nums;
        color: var(--primary-text-color);
      }
      .gauge__val i { font-size: 0.85rem; font-weight: 500; font-style: normal; color: var(--secondary-text-color); margin-left: 1px; }
      .gauge__cap {
        display: block; margin-top: 3px;
        font-size: 0.62rem; text-transform: uppercase; letter-spacing: 0.09em;
        color: var(--secondary-text-color);
      }
      .gauge__bolt {
        position: absolute; right: -2px; top: -2px;
        --mdc-icon-size: 20px;
        color: var(--bmw-charge);
        background: var(--card-background-color);
        border-radius: 50%;
        padding: 2px;
      }

      .lead { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 10px; }
      .lead__row {
        display: grid;
        grid-template-columns: 24px 1fr;
        grid-template-rows: auto auto;
        column-gap: 10px;
        align-items: center;
        border-radius: 10px;
        padding: 6px 8px;
        transition: background 0.15s ease;
      }
      .lead__row:hover { background: var(--secondary-background-color); }
      .lead__row ha-icon { grid-row: 1 / 3; color: var(--secondary-text-color); --mdc-icon-size: 22px; }
      .lead__val {
        font-size: 1.05rem; font-weight: 600;
        font-variant-numeric: tabular-nums;
        color: var(--primary-text-color);
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
      }
      .lead__lbl { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--secondary-text-color); }

      /* metric grid */
      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
        gap: 8px;
        padding: 8px 14px 16px;
      }
      .cell {
        display: flex; align-items: center; gap: 10px;
        padding: 10px 12px;
        border-radius: 12px;
        background: var(--secondary-background-color);
        transition: transform 0.12s ease, background 0.15s ease;
      }
      .cell:hover { background: var(--divider-color); }
      .cell:active { transform: scale(0.98); }
      .cell ha-icon { color: var(--secondary-text-color); --mdc-icon-size: 22px; flex: 0 0 auto; }
      .cell__body { min-width: 0; display: flex; flex-direction: column; }
      .cell__val {
        font-size: 0.98rem; font-weight: 600;
        font-variant-numeric: tabular-nums;
        color: var(--primary-text-color);
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
      }
      .cell__lbl { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--secondary-text-color); }

      /* cluster mode */
      .chead {
        display: flex; align-items: center; gap: 12px;
        padding: 16px 18px;
        border-bottom: 1px solid var(--divider-color);
      }
      .chead > ha-icon { --mdc-icon-size: 26px; color: var(--bmw-charge); }
      .chead__text { display: flex; flex-direction: column; }
      .chead__title { font-size: 1.05rem; font-weight: 600; color: var(--primary-text-color); }
      .chead__sub { font-size: 0.74rem; color: var(--secondary-text-color); }
      /* export buttons, pushed to the right edge of the header */
      .xbar { margin-left: auto; display: flex; gap: 6px; flex-shrink: 0; }

      /* month navigator, between the header and the list it scopes */
      .mnav {
        display: flex; align-items: center; justify-content: center; gap: 4px;
        padding: 8px 14px 2px;
      }
      .mnav__label {
        min-width: 11ch; text-align: center;
        font-size: 0.8rem; font-weight: 600; color: var(--primary-text-color);
      }
      .mnav__btn {
        display: inline-flex; align-items: center; justify-content: center;
        width: 28px; height: 28px; padding: 0;
        border: 1px solid var(--divider-color); border-radius: 999px;
        background: transparent; color: var(--secondary-text-color);
        cursor: pointer; font: inherit;
      }
      .mnav__btn > ha-icon { --mdc-icon-size: 18px; }
      .mnav__btn:hover:not([disabled]) {
        border-color: var(--primary-color); color: var(--primary-color);
      }
      /* Forward past the current month leads nowhere, so the control says so
         rather than silently doing nothing. */
      .mnav__btn[disabled] { opacity: 0.35; cursor: default; }
      .xbtn {
        display: inline-flex; align-items: center; gap: 4px;
        padding: 5px 10px 5px 7px;
        font: inherit; font-size: 0.74rem; font-weight: 500;
        color: var(--secondary-text-color);
        background: var(--secondary-background-color);
        border: 1px solid var(--divider-color); border-radius: 16px;
        cursor: pointer;
        transition: color 0.13s ease, border-color 0.13s ease;
      }
      .xbtn:hover { color: var(--primary-text-color); border-color: var(--bmw-charge); }
      .xbtn ha-icon { --mdc-icon-size: 15px; }
      @media (max-width: 420px) {
        /* the labels are the first thing worth losing on a phone */
        .xbtn { font-size: 0; gap: 0; padding: 6px; }
      }
      .list { display: flex; flex-direction: column; padding: 6px 8px 10px; }
      .item {
        display: flex; align-items: center; justify-content: space-between; gap: 12px;
        padding: 11px 12px;
        border-radius: 10px;
        transition: background 0.13s ease;
      }
      .item:hover { background: var(--secondary-background-color); }
      .item + .item { border-top: 1px solid var(--divider-color); }
      .item:hover { border-top-color: transparent; }
      .item__name { color: var(--primary-text-color); font-size: 0.92rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .item__val {
        color: var(--secondary-text-color);
        font-size: 0.92rem; font-weight: 600;
        font-variant-numeric: tabular-nums;
        flex: 0 0 auto; text-align: right;
      }
      .list__head {
        padding: 12px 20px 0;
        color: var(--secondary-text-color);
        font-size: 0.78rem; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase;
      }
      .item--msg { justify-content: flex-start; align-items: flex-start; text-align: left; }
      .item__icon { --mdc-icon-size: 20px; color: var(--warning-color, #ffa600); flex: 0 0 auto; }
      .item__body { display: flex; flex-direction: column; gap: 2px; flex: 1 1 auto; min-width: 0; }
      .item__text { color: var(--primary-text-color); font-size: 0.92rem; }
      .item__sub { color: var(--secondary-text-color); font-size: 0.82rem; }
      .item__clamp {
        display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
      }
      .cc { border-radius: 10px; }
      .cc + .cc { border-top: 1px solid var(--divider-color); }
      .cc.is-open { background: var(--secondary-background-color); border-top-color: transparent; }
      .cc > .item--msg {
        width: 100%; background: none; border: none; color: inherit; font: inherit; cursor: pointer;
      }
      .cc.is-open > .item--msg:hover { background: transparent; }
      .cc--past .item__icon { color: var(--secondary-text-color); }
      .cc--past .item__text { color: var(--secondary-text-color); }
      .cc-earlier {
        display: flex; align-items: center; gap: 6px; width: 100%; padding: 4px 20px 10px;
        background: none; border: none; cursor: pointer; font: inherit; text-align: left;
        color: var(--secondary-text-color); font-size: 0.85rem;
      }
      .cc-earlier ha-icon { --mdc-icon-size: 18px; }
      .cc-earlier:hover { color: var(--primary-text-color); }
      .cc__text { margin: 0 0 8px; color: var(--primary-text-color); font-size: 0.86rem; line-height: 1.4; }
      .empty, .msg__body { color: var(--secondary-text-color); font-size: 0.9rem; }
      .empty { padding: 22px 18px; }
      .empty.empty--inline { padding: 8px 20px 14px; }

      /* message state */
      .msg { padding: 28px 20px; text-align: center; }
      .msg ha-icon { --mdc-icon-size: 40px; color: var(--disabled-text-color); }
      .msg__title { margin-top: 10px; font-weight: 600; color: var(--primary-text-color); }
      .msg__body { margin-top: 6px; }
      .msg code, .empty b { font-family: var(--code-font-family, monospace); }

      /* tire diagram */
      .tstat {
        margin-left: auto;
        display: inline-flex; align-items: center; gap: 6px;
        font-size: 0.74rem; font-weight: 600;
        color: var(--c);
        white-space: nowrap;
      }
      .tstat__dot { width: 8px; height: 8px; border-radius: 50%; background: var(--c); }
      /* Pressure and wear summarised across every wheel, above the diagram. */
      .tsum { display: flex; gap: 8px; padding: 12px 14px 2px; }
      .tsum__cell {
        flex: 1; min-width: 0;
        display: flex; flex-direction: column; gap: 2px;
        padding: 8px 10px; border-radius: 12px;
        background: var(--secondary-background-color);
      }
      .tsum__k {
        font-size: 0.62rem; letter-spacing: 0.08em; text-transform: uppercase;
        color: var(--secondary-text-color);
      }
      .tsum__v {
        display: flex; gap: 6px;
        /* baseline, not center: a long pressure range wraps to two lines in a
           narrow column and the dot belongs on the first one. */
        align-items: baseline;
        font-size: 0.92rem; font-weight: 600; color: var(--c);
      }
      .tsum__v .tstat__dot { flex-shrink: 0; }
      .tsum__sub { font-size: 0.68rem; color: var(--secondary-text-color); }
      /* Grid, not absolute positioning: each wheel's own column sizes to the
         card so long fitment lines wrap instead of being clipped. */
      .tirewrap { container-type: inline-size; }
      .tirecar {
        display: grid;
        grid-template-columns: minmax(0, 1fr) clamp(72px, 24%, 118px) minmax(0, 1fr);
        grid-template-areas:
          ".  front ."
          "fl car   fr"
          "rl car   rr";
        column-gap: 10px; row-gap: 14px;
        padding: 10px 12px 16px;
        align-items: start;
      }
      .tirecar__front {
        grid-area: front;
        text-align: center;
        font-size: 0.56rem; letter-spacing: 0.18em; font-weight: 700;
        color: var(--secondary-text-color);
      }
      .tirecar__svg { grid-area: car; align-self: center; }
      .carsvg {
        display: block;
        width: 100%;
        height: auto;
        margin: 0 auto;
        overflow: visible;
        --tire: #1d2228;
      }
      /* The car is a fixed light-grey picture in both themes; strokes are in
         drawing units (948 wide), so ~6 units is one pixel on a card. */
      .carsvg__body, .carsvg__mirror { fill: url(#carBody); stroke: #a3abb4; stroke-width: 4; }
      .carsvg__hood { fill: url(#carBody); stroke: #b4bbc3; stroke-width: 3; }
      .carsvg__shut { fill: none; stroke: #b0b7bf; stroke-width: 3; }
      .carsvg__crease { fill: none; stroke: #f7f9fb; stroke-width: 4; opacity: 0.9; }
      .carsvg__roof { fill: url(#carRoof); stroke: #858c95; stroke-width: 3; }
      .carsvg__glass { fill: url(#carGlass); stroke: #1a2128; stroke-width: 6; stroke-linejoin: round; }
      .carsvg__frame { stroke-width: 10; }
      .carsvg__sheen { fill: #ffffff; opacity: 0.055; }
      .carsvg__pillar { fill: #232a31; }
      .carsvg__light { fill: #2a323b; }
      .carsvg__drl { fill: none; stroke: #ffffff; stroke-width: 4; stroke-linecap: round; }
      .carsvg__tail { fill: url(#carTail); stroke: #5a0f0b; stroke-width: 2.5; }
      .carsvg__tailcore { fill: none; stroke: #ffb199; stroke-width: 3.5; stroke-linecap: round; opacity: 0.9; }
      .carsvg__chrome { fill: #e9edf0; stroke: #8f979f; stroke-width: 2.5; }
      .carsvg__kidney { fill: #262c33; }
      .carsvg__kbar { fill: none; stroke: #59616a; stroke-width: 3; }
      .carsvg__handle { fill: #d6dbe0; stroke: #9aa2aa; stroke-width: 2; }
      .carsvg__rail { fill: none; stroke: #4c535b; stroke-width: 12; stroke-linecap: round; }
      .carsvg__rail--bar { stroke: #262c33; stroke-width: 8; stroke-linecap: butt; }
      .carsvg__wheel { fill: var(--tire); stroke: var(--tire); stroke-width: 14; stroke-linejoin: round; }
      .carsvg__wheel--lit { filter: url(#carGlow); }

      /* closures / security diagram */
      .closcar { padding: 10px 12px 4px; display: flex; justify-content: center; }
      .closcar .carsvg { width: 50%; min-width: 138px; max-width: 196px; margin: 0; }
      .cldiag__hit { fill: transparent; cursor: pointer; }
      .cldiag__hit:hover { fill: rgba(127, 127, 127, 0.14); }
      .cldiag__lit { pointer-events: none; }
      .cldiag__zone {
        fill: var(--c); fill-opacity: 0.5;
        stroke: var(--c); stroke-width: 14; stroke-linejoin: round;
        filter: url(#carGlow);
      }
      .cldiag__core { fill: none; stroke: rgba(255, 255, 255, 0.55); stroke-width: 3.5; stroke-linejoin: round; }
      .cldiag__drop { fill: none; stroke: var(--c); stroke-width: 10; }
      .cldiag__sill { fill: #a2a9b1; }
      .cldiag__sillshadow { fill: none; stroke: #3b434c; stroke-width: 5; opacity: 0.55; }
      .cldiag__door { fill: var(--c); stroke: var(--c); stroke-width: 8; stroke-linejoin: round; filter: url(#carGlow); }
      .cldiag__doortrim { fill: rgba(0, 0, 0, 0.45); }
      .cldiag__dooredge { fill: none; stroke: rgba(255, 255, 255, 0.5); stroke-width: 4; stroke-linecap: round; }
      .cldiag__lock { cursor: pointer; }
      .cldiag__lockbody { fill: var(--c); }
      .cldiag__shackle { fill: none; stroke: var(--c); stroke-width: 16; stroke-linecap: round; }
      .item__dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 8px; vertical-align: middle; }

      .wlabel {
        min-width: 0;
        display: flex; flex-direction: column; gap: 1px;
        padding: 4px 4px;
        border-radius: 10px;
        transition: background 0.14s ease;
      }
      .wlabel:hover { background: var(--secondary-background-color); }
      .wlabel--fl { grid-area: fl; align-items: flex-end; text-align: right; }
      .wlabel--fr { grid-area: fr; align-items: flex-start; text-align: left; }
      .wlabel--rl { grid-area: rl; align-items: flex-end; text-align: right; }
      .wlabel--rr { grid-area: rr; align-items: flex-start; text-align: left; }
      .wlabel__pos {
        display: inline-flex; align-items: center; gap: 6px;
        font-size: 0.66rem; letter-spacing: 0.05em; text-transform: uppercase;
        color: var(--secondary-text-color);
      }
      .wlabel--fl .wlabel__pos, .wlabel--rl .wlabel__pos { flex-direction: row-reverse; }
      .wlabel__badge { color: var(--c); font-weight: 700; }
      .wlabel__val {
        font-size: 1.45rem; font-weight: 600; line-height: 1.1;
        font-variant-numeric: tabular-nums;
        color: var(--primary-text-color);
        white-space: nowrap;
      }
      .wlabel__val i {
        font-size: 0.66rem; font-weight: 500; font-style: normal;
        color: var(--secondary-text-color); margin-left: 2px;
      }
      .wlabel__sub {
        font-size: 0.66rem; color: var(--secondary-text-color);
        font-variant-numeric: tabular-nums;
      }
      /* Per-wheel fitment. Wraps rather than clips -- a tyre size and a tread
         name are long, and the column is whatever the dashboard gives us. */
      .wlabel__meta {
        font-size: 0.63rem; line-height: 1.35;
        color: var(--secondary-text-color);
        overflow-wrap: anywhere;
      }
      .wlabel__val + .wlabel__meta,
      .wlabel__sub + .wlabel__meta { margin-top: 3px; }
      .wlabel__due { color: var(--primary-text-color); font-weight: 600; }
      /* Narrow columns: the diagram is the first thing worth losing, and the
         four wheels fall back to a 2x2 grid that still reads front-over-rear.
         Must come after the .wlabel rules it overrides. */
      @container (max-width: 340px) {
        .tirecar {
          grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
          grid-template-areas:
            "front front"
            "fl    fr"
            "rl    rr";
        }
        .tirecar__svg { display: none; }
        .wlabel--fl, .wlabel--rl { align-items: flex-start; text-align: left; }
        .wlabel--fl .wlabel__pos, .wlabel--rl .wlabel__pos { flex-direction: row; }
      }

      /* ---- charging history ---- */
      .chg__summary {
        display: flex; gap: 8px; padding: 10px 14px 4px;
      }
      .chg__stat {
        flex: 1; display: flex; flex-direction: column; gap: 2px;
        padding: 8px 10px; border-radius: 12px;
        background: var(--secondary-background-color);
      }
      .chg__stat-val {
        font-size: 1.05rem; font-weight: 600; font-variant-numeric: tabular-nums;
      }
      .chg__stat-lbl {
        font-size: 0.68rem; color: var(--secondary-text-color);
        text-transform: uppercase; letter-spacing: 0.04em;
      }
      .chg__list { padding: 6px 6px 8px; }
      .chg__session { border-radius: 12px; }
      .chg__session.is-open { background: var(--secondary-background-color); }
      .chg__row {
        width: 100%; display: flex; align-items: center; justify-content: space-between;
        gap: 10px; padding: 10px 10px; background: none; border: none;
        color: inherit; text-align: left; cursor: pointer; border-radius: 12px;
      }
      .chg__row:hover { background: var(--secondary-background-color); }
      .chg__session.is-open .chg__row:hover { background: transparent; }
      .chg__row-main { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
      .chg__date { font-weight: 600; font-size: 0.92rem; }
      .chg__meta { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
      .chg__soc {
        font-size: 0.72rem; color: var(--secondary-text-color);
        font-variant-numeric: tabular-nums;
      }
      .chg__figures {
        display: flex; flex-direction: column; align-items: flex-end; gap: 3px;
        white-space: nowrap;
      }
      .chg__energy { font-variant-numeric: tabular-nums; font-size: 0.86rem; }
      .chg__energy--lead { font-size: 0.98rem; font-weight: 600; }
      .chg__cost {
        font-weight: 600; font-variant-numeric: tabular-nums;
      }
      .chg__cost--none {
        font-weight: 400; font-size: 0.72rem; color: var(--secondary-text-color);
      }
      .chg__badge, .chg__tag {
        font-size: 0.66rem; padding: 1px 7px; border-radius: 999px;
        border: 1px solid var(--divider-color); color: var(--secondary-text-color);
        white-space: nowrap;
      }
      .chg__badge--home { border-color: var(--bmw-high); color: var(--bmw-high); }
      .chg__badge--away { border-color: var(--bmw-charge); color: var(--bmw-charge); }
      .chg__badge--assumed { border-style: dashed; }
      .chg__tag--warn { border-color: var(--bmw-mid); color: var(--bmw-mid); }
      .chg__tag--sun { border-color: var(--bmw-high); color: var(--bmw-high); }
      .chg__detail { padding: 2px 12px 12px; }
      .chg__chart {
        width: 100%; height: 64px; display: block; margin-bottom: 8px;
      }
      .chg__chart-line {
        fill: none; stroke: var(--bmw-charge); stroke-width: 2;
        stroke-linejoin: round; stroke-linecap: round;
        vector-effect: non-scaling-stroke;
      }
      .chg__chart-fill { fill: var(--bmw-charge); opacity: 0.12; stroke: none; }
      .chg__chart-max {
        fill: var(--secondary-text-color); font-size: 9px;
      }
      .chg__facts { display: flex; flex-wrap: wrap; gap: 6px; }
      .chg__fact {
        flex: 1 1 40%; display: flex; justify-content: space-between; gap: 8px;
        padding: 6px 10px; border-radius: 10px;
        background: var(--card-background-color);
      }
      .chg__fact-lbl { color: var(--secondary-text-color); font-size: 0.76rem; }
      .chg__fact-val { font-variant-numeric: tabular-nums; font-size: 0.82rem; }

      /* ---- battery health ---- */
      .bh { padding: 6px 14px 14px; display: flex; flex-direction: column; gap: 14px; }
      .bh__hero { display: flex; align-items: center; gap: 16px; }
      .bh__ring { width: 92px; height: 92px; flex: 0 0 auto; }
      .bh__ring-track { fill: none; stroke: var(--divider-color); stroke-width: 7; }
      .bh__ring-val {
        fill: none; stroke: var(--bmw-high); stroke-width: 7; stroke-linecap: round;
        transition: stroke-dasharray 0.6s ease;
      }
      .bh__ring-text {
        fill: var(--primary-text-color); font-size: 17px; font-weight: 600;
        text-anchor: middle; font-variant-numeric: tabular-nums;
      }
      .bh__hero-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
      .bh__usable {
        font-size: 1.9rem; font-weight: 600; line-height: 1;
        font-variant-numeric: tabular-nums;
      }
      .bh__usable i { font-size: 0.9rem; font-weight: 500; font-style: normal;
        color: var(--secondary-text-color); }
      .bh__usable-lbl {
        font-size: 0.7rem; color: var(--secondary-text-color);
        text-transform: uppercase; letter-spacing: 0.04em;
      }
      .bh__vsnew { font-size: 0.82rem; color: var(--bmw-high); font-weight: 500; }
      .bh__learn { display: flex; flex-direction: column; gap: 8px; padding: 10px 0; }
      .bh__learn-val {
        font-size: 1.35rem; font-weight: 600; font-variant-numeric: tabular-nums;
      }
      .bh__bar {
        height: 7px; border-radius: 999px; background: var(--divider-color);
        overflow: hidden;
      }
      .bh__bar-fill {
        height: 100%; border-radius: 999px; background: var(--bmw-charge);
        transition: width 0.6s ease;
      }
      .bh__hint { font-size: 0.78rem; color: var(--secondary-text-color); line-height: 1.35; }
      .bh__trend { display: flex; flex-direction: column; gap: 4px; }
      .bh__trend-title {
        font-size: 0.68rem; color: var(--secondary-text-color);
        text-transform: uppercase; letter-spacing: 0.04em;
      }
      .bh__chart { width: 100%; height: 70px; display: block; }

      /* ---- efficiency & real range ---- */
      .ef { padding: 6px 14px 14px; display: flex; flex-direction: column; gap: 14px; }
      .ef__hero { display: flex; flex-direction: column; gap: 4px; }
      .ef__hero-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
      .ef__hero-val {
        font-size: 2.1rem; font-weight: 600; line-height: 1;
        font-variant-numeric: tabular-nums;
      }
      .ef__hero-val i {
        font-size: 0.95rem; font-weight: 500; font-style: normal;
        color: var(--secondary-text-color);
      }
      .ef__hero-lbl {
        font-size: 0.7rem; color: var(--secondary-text-color);
        text-transform: uppercase; letter-spacing: 0.04em;
      }
      .ef__vs { font-size: 0.82rem; color: var(--secondary-text-color); font-weight: 500; }
      .ef__vs--over { color: var(--bmw-high); }
      .ef__vs--under { color: var(--bmw-mid); }
      .ef__facts { display: flex; flex-wrap: wrap; gap: 8px; }
      .ef__fact {
        flex: 1 1 44%; display: flex; flex-direction: column; gap: 2px;
        padding: 8px 10px; border-radius: 12px;
        background: var(--secondary-background-color);
      }
      .ef__fact-lbl {
        font-size: 0.68rem; color: var(--secondary-text-color);
        text-transform: uppercase; letter-spacing: 0.04em;
      }
      .ef__fact-val { font-size: 1.05rem; font-weight: 600; font-variant-numeric: tabular-nums; }
      .ef__fact-note { font-size: 0.72rem; color: var(--secondary-text-color); }
      .ef__trend { display: flex; flex-direction: column; gap: 4px; }
      .ef__trend-title {
        font-size: 0.68rem; color: var(--secondary-text-color);
        text-transform: uppercase; letter-spacing: 0.04em;
      }
      .ef__chart { width: 100%; height: 88px; display: block; }
      .ef__bar { fill: var(--bmw-charge); opacity: 0.85; }
      .ef__bar-lbl { fill: var(--secondary-text-color); font-size: 8px; }
      .ef__note { font-size: 0.72rem; color: var(--secondary-text-color); line-height: 1.35; }

      /* trips */
      .tr__review { padding: 8px 14px 4px; display: flex; flex-direction: column; gap: 12px; }
      .tr__tiles { display: flex; gap: 8px; flex-wrap: wrap; }
      .tr__tile {
        flex: 1 1 40%; display: flex; flex-direction: column; gap: 2px;
        padding: 8px 10px; border-radius: 12px;
        background: var(--secondary-background-color);
      }
      .tr__tile-val { font-size: 1.05rem; font-weight: 600; font-variant-numeric: tabular-nums; }
      .tr__tile-val i { font-style: normal; font-size: 0.72rem; color: var(--secondary-text-color); }
      .tr__tile-lbl {
        font-size: 0.68rem; color: var(--secondary-text-color);
        text-transform: uppercase; letter-spacing: 0.04em;
      }
      /* The secondary reading under a headline figure (battery-side consumption
         beneath the plug-side one). Deliberately quieter than the label. */
      .tr__tile-sub {
        font-size: 0.68rem; color: var(--secondary-text-color);
        font-variant-numeric: tabular-nums; opacity: 0.85;
      }
      .tr__delta { font-size: 0.7rem; font-variant-numeric: tabular-nums; }
      .tr__delta--up { color: var(--bmw-charge, #34c759); }
      .tr__delta--down { color: var(--error-color, #ff453a); }
      .tr__split { display: flex; flex-direction: column; gap: 6px; }
      .tr__bar {
        display: flex; height: 12px; border-radius: 999px; overflow: hidden;
        background: var(--secondary-background-color);
      }
      .tr__seg { display: block; height: 100%; }
      .tr__seg--business, .tr__dot.tr__seg--business { background: #0066b1; }
      .tr__seg--commute, .tr__dot.tr__seg--commute { background: #00a1e0; }
      .tr__seg--private, .tr__dot.tr__seg--private { background: #7ac142; }
      .tr__seg--unc, .tr__dot.tr__seg--unc { background: var(--disabled-text-color, #8a8a8a); }
      .tr__legend { display: flex; flex-wrap: wrap; gap: 10px; }
      .tr__leg {
        display: inline-flex; align-items: center; gap: 5px;
        font-size: 0.72rem; color: var(--secondary-text-color);
        font-variant-numeric: tabular-nums;
      }
      .tr__dot { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }

      /* ---- trip map ---- */
      .map__filters {
        display: flex; gap: 6px; padding: 4px 14px 10px; flex-wrap: wrap;
      }
      .map__chip {
        border: 1px solid var(--divider-color); background: transparent;
        color: var(--secondary-text-color); cursor: pointer;
        font: inherit; font-size: 0.74rem; padding: 3px 12px; border-radius: 999px;
      }
      .map__chip:hover { border-color: var(--primary-color); }
      .map__chip.is-active {
        background: var(--primary-color); color: var(--text-primary-color, #fff);
        border-color: var(--primary-color);
      }
      .map__holder {
        margin: 0 14px; border-radius: 12px; overflow: hidden;
        border: 1px solid var(--divider-color);
      }
      .map__holder ha-map { width: 100%; }
      .tr__minimap {
        margin: 2px 0 12px; height: 200px; border-radius: 10px; overflow: hidden;
        border: 1px solid var(--divider-color); background: var(--card-background-color);
      }
      .tr__minimap ha-map { width: 100%; }
      .tr__style { display: flex; flex-direction: column; gap: 6px; }
      .tr__style-head { display: flex; align-items: center; justify-content: space-between; }
      .tr__style-lbl {
        font-size: 0.68rem; color: var(--secondary-text-color);
        text-transform: uppercase; letter-spacing: 0.04em;
      }
      .tr__stars { font-size: 0.95rem; color: #f5a623; letter-spacing: 1px; }
      .tr__dests { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
      .tr__dests-lbl {
        font-size: 0.68rem; color: var(--secondary-text-color);
        text-transform: uppercase; letter-spacing: 0.04em; width: 100%;
      }
      .tr__dest {
        display: inline-flex; align-items: center; gap: 6px;
        padding: 4px 8px; border-radius: 999px;
        background: var(--secondary-background-color); font-size: 0.78rem;
      }
      .tr__dest-n { color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
      .tr__badge {
        font-size: 0.66rem; padding: 1px 7px; border-radius: 999px;
        color: #fff; text-transform: uppercase; letter-spacing: 0.03em;
      }
      .tr__badge--business { background: #0066b1; }
      .tr__badge--commute { background: #00a1e0; }
      .tr__badge--private { background: #7ac142; }
      /* A drive under way: the badge that replaces a classification until the
         trip lands and there is something to classify. */
      .tr__badge--live {
        background: var(--bmw-charge, #4cc2ff);
        display: inline-flex; align-items: center; gap: 5px;
      }
      .tr__live-dot {
        width: 6px; height: 6px; border-radius: 50%; background: #fff;
        animation: bd-trip-pulse 2s ease-in-out infinite;
      }
      @media (prefers-reduced-motion: reduce) {
        .tr__live-dot { animation: none; }
      }
      /* The in-progress row leads with an accent edge so it reads as different
         from the recorded trips below it without shouting. */
      .chg__session.is-live > .chg__row {
        border-left: 3px solid var(--bmw-charge, #4cc2ff);
        padding-left: 7px;
      }
      .tr__live-note {
        font-size: 0.72rem; color: var(--secondary-text-color);
        line-height: 1.45; padding: 8px 2px 2px;
      }
      .tr__auto { font-style: normal; opacity: 0.75; text-transform: none; }
      .tr__classify { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 8px 2px 2px; }
      .tr__classify-lbl {
        font-size: 0.68rem; color: var(--secondary-text-color);
        text-transform: uppercase; letter-spacing: 0.04em;
      }
      .tr__cls-btns { display: inline-flex; gap: 6px; flex-wrap: wrap; }
      .tr__cls-btn {
        border: 1px solid var(--divider-color); background: none; cursor: pointer;
        font-size: 0.66rem; padding: 3px 9px; border-radius: 999px;
        color: var(--primary-text-color); text-transform: uppercase; letter-spacing: 0.03em;
        opacity: 0.6;
      }
      .tr__cls-btn:hover { opacity: 1; }
      .tr__cls-btn.is-active { opacity: 1; color: #fff; border-color: transparent; }

      @media (prefers-reduced-motion: reduce) {
        .dot--live { animation: none; }
        .gauge__ring { transition: none; }
      }
      @media (max-width: 360px) {
        .band { flex-direction: column; align-items: stretch; }
        .gauge { align-self: center; }
      }
    </style>`;
  }
}

// Register idempotently. The script can legitimately be evaluated more than once
// in one session (e.g. the integration re-injects a fresh ?v= URL after an update
// on top of the already-loaded copy). A bare customElements.define() would throw
// "the name has already been used" on the second run and abort the module.
//
// The previous `if (!customElements.get(tag)) define(tag)` guard proved unsafe:
// on cold loads the define was sometimes *skipped* while the element was never
// actually registered, leaving every placed card stuck on "config error" (HA's
// whenDefined->rebuild never fires because the tag never becomes defined) until a
// hard refresh. Always attempt the define and swallow only the benign
// already-defined error, so registration can never be silently missed.
defineCardElement("bavariandata-card", BavarianDataCard);
// Back-compat alias: dashboards created before the BavarianData rename still
// reference `custom:bmw-cardata-card`. A custom-element constructor can only be
// bound to ONE tag name -- reusing `BavarianDataCard` here throws "this
// constructor has already been used with this registry", which aborts the rest
// of this module (editor + card-picker registration never run). Register a
// trivial subclass so the legacy tag gets its own constructor. Not advertised
// in the card picker.
defineCardElement("bmw-cardata-card", class extends BavarianDataCard {});

/* ------------------------------------------------------------------------- *
 * Visual editor (config-changed via ha-form)                                *
 * ------------------------------------------------------------------------- */

// Sentinel for "no cluster" so the dropdown always has a concrete value.
const OVERVIEW = "overview";
// The mode dropdown is a single selector for every layout, but charging isn't a
// catalogue cluster -- it's stored as `view: charging`. This sentinel lets the
// one dropdown offer it, mapped to/from `view` in _render and _valueChanged.
const CHARGING_VIEW = "charging";
// Same idea for battery health: a `view:`, not a cluster, sharing the dropdown.
const HEALTH_VIEW = "health";
// And trips (the Fahrtenbuch), also a `view:` sharing the one dropdown.
const TRIPS_VIEW = "trips";
// The trip map (opt-in route polylines on ha-map), also a `view:`.
const MAP_VIEW = "map";
// The `view:` values that are layouts in their own right rather than clusters.
const EFFICIENCY_VIEW = "efficiency";
// The car's factory equipment, named by BMW's configurator (opt-in source).
const EQUIPMENT_VIEW = "equipment";

const VIEW_MODES = new Set([
  CHARGING_VIEW,
  TRIPS_VIEW,
  MAP_VIEW,
  HEALTH_VIEW,
  EFFICIENCY_VIEW,
  EQUIPMENT_VIEW,
]);

class BavarianDataCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _schema() {
    const clusterOptions = [
      { value: OVERVIEW, label: t(this._hass, "ed_overview_option") },
      { value: CHARGING_VIEW, label: t(this._hass, "ch_title") },
      { value: TRIPS_VIEW, label: t(this._hass, "tr_title") },
      { value: MAP_VIEW, label: t(this._hass, "mp_title") },
      { value: HEALTH_VIEW, label: t(this._hass, "bh_title") },
      { value: EFFICIENCY_VIEW, label: t(this._hass, "ef_title") },
      { value: EQUIPMENT_VIEW, label: t(this._hass, "eq_title") },
      { value: "closures", label: t(this._hass, "cl_closures") },
      ...CLUSTER_SLUGS.map((slug) => ({
        value: slug,
        label: t(this._hass, "cl_" + slug, null, slug),
      })),
    ];
    const entitySel = (domain) => ({
      entity: { integration: "bavariandata", ...(domain ? { domain } : {}) },
    });
    const overview =
      !this._config || (!this._config.cluster && !VIEW_MODES.has(this._config.view));
    // Cars only. HA's device picker, filtered by integration, also offers the
    // "CarData Debug Device" -- registered first, it was even preselected -- and
    // a card bound to it has nothing to show.
    const cars = BavarianDataCard._vehicleDevices(this._hass).map((id) => {
      const dev = this._hass.devices[id];
      return { value: id, label: dev.name_by_user || dev.name || id };
    });
    const schema = [
      { name: "device", selector: { select: { mode: "dropdown", options: cars } } },
      { name: "cluster", selector: { select: { mode: "dropdown", options: clusterOptions } } },
      { name: "title", selector: { text: {} } },
    ];
    if (overview) {
      schema.push({
        name: "drivetrain",
        selector: {
          select: {
            mode: "dropdown",
            options: ["auto", "bev", "phev", "ice"].map((value) => ({
              value,
              label: t(this._hass, "dt_" + value),
            })),
          },
        },
      });
      // Entity overrides only make sense for the overview layout.
      schema.push({
        name: "",
        type: "expandable",
        flatten: true,
        title: t(this._hass, "ed_overrides_title"),
        icon: "mdi:tune-variant",
        schema: [
          { name: "image", selector: entitySel("image") },
          {
            type: "grid",
            schema: [
              { name: "soc", selector: entitySel() },
              { name: "range", selector: entitySel() },
              { name: "charging", selector: entitySel() },
              { name: "target_soc", selector: entitySel() },
              { name: "time_to_full", selector: entitySel() },
              { name: "odometer", selector: entitySel() },
              { name: "plug", selector: entitySel() },
              { name: "fuel", selector: entitySel() },
            ],
          },
        ],
      });
    }
    // The closures view draws the side doors and windows, so it is where the
    // steering side matters.
    if (this._config && this._config.cluster === "closures") {
      schema.push({
        name: "steering",
        selector: {
          select: {
            mode: "dropdown",
            options: ["auto", "left", "right"].map((value) => ({
              value,
              label: t(this._hass, value === "auto" ? "dt_auto" : "st_" + value),
            })),
          },
        },
      });
    }
    // The tire and closures views draw the car from above.
    if (this._config && (this._config.cluster === "tire" || this._config.cluster === "closures")) {
      schema.push({
        name: "shape",
        selector: {
          select: {
            mode: "dropdown",
            options: ["auto", ...Object.keys(CAR_SHAPES)].map((value) => ({
              value,
              label: t(this._hass, value === "auto" ? "dt_auto" : "sh_" + value),
            })),
          },
        },
      });
    }
    return schema;
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (s) => t(this._hass, "ed_" + s.name, null, s.name);
      this._form.computeHelper = (s) => t(this._hass, "edh_" + s.name, null, "");
      this._form.addEventListener("value-changed", (ev) => this._valueChanged(ev));
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.schema = this._schema();
    // Present a concrete value so the mode dropdown reflects the current layout.
    // A `view:` layout maps onto its dropdown sentinel (its own value); a cluster
    // onto the cluster; otherwise the overview sentinel.
    const mode = VIEW_MODES.has(this._config.view)
      ? this._config.view
      : this._config.cluster || OVERVIEW;
    this._form.data = {
      ...this._config,
      cluster: mode,
      drivetrain: this._config.drivetrain || "auto",
      steering: this._config.steering || "auto",
      shape: this._config.shape || "auto",
    };
  }

  _valueChanged(ev) {
    ev.stopPropagation();
    if (!this._config) return;
    const value = { ...ev.detail.value };
    // The mode dropdown feeds the `cluster` field; translate its special values
    // back into the real config keys.
    if (VIEW_MODES.has(value.cluster)) {
      value.view = value.cluster;
      delete value.cluster;
    } else {
      delete value.view;
      if (value.cluster === OVERVIEW || !value.cluster) delete value.cluster;
    }
    // "Auto-detect" is the absence of the key, not a value to store.
    if (value.drivetrain === "auto") delete value.drivetrain;
    if (value.steering === "auto") delete value.steering;
    if (value.shape === "auto") delete value.shape;
    // Drop empties so the stored config stays minimal.
    for (const key of Object.keys(value)) {
      if (value[key] === "" || value[key] === undefined || value[key] === null) {
        delete value[key];
      }
    }
    delete value.type;
    const config = { type: this._config.type || "custom:bavariandata-card", ...value };
    this._config = config;
    // Switching to/from a cluster changes which fields are relevant.
    this._form.schema = this._schema();
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config },
        bubbles: true,
        composed: true,
      })
    );
  }
}

defineCardElement("bavariandata-card-editor", BavarianDataCardEditor);
// Legacy alias for the editor element, matching the card alias above. Needs its
// own constructor for the same reason (one class -> one tag name).
defineCardElement("bmw-cardata-card-editor", class extends BavarianDataCardEditor {});

window.customCards = window.customCards || [];
// Only advertise the card once — a second evaluation would otherwise add a
// duplicate entry to the card picker.
if (!window.customCards.some((c) => c.type === "bavariandata-card")) {
  window.customCards.push({
    type: "bavariandata-card",
    name: "BavarianData Card",
    description: "Vehicle render, state of charge and per-cluster data for BMW CarData.",
    preview: true,
    documentationURL: "https://github.com/JustChr/BavarianData",
  });
}

// eslint-disable-next-line no-console
console.info(
  `%c BAVARIANDATA-CARD %c ${CARD_VERSION} `,
  "color:#fff;background:#2f80ed;border-radius:3px 0 0 3px;padding:2px 4px;",
  "color:#2f80ed;background:#0b0f14;border-radius:0 3px 3px 0;padding:2px 4px;"
);
