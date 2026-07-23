/*
 * BMW CarData Card
 * A custom Lovelace card for the BMW CarData integration.
 *
 * Two modes:
 *   - overview (default): the vehicle render as a hero, a state-of-charge ring,
 *     range, charging status and a compact grid of key metrics.
 *   - cluster: `cluster: <slug>` renders every entity of that catalogue cluster
 *     (electric, status, tire, ...) as a clean list. The cluster of each entity
 *     is read from its `cluster` attribute, exposed by the integration.
 *
 * The card auto-discovers entities from the vehicle's device, so a minimal
 * config is just `type: custom:bmw-cardata-card`.
 */

const CARD_VERSION = "1.4.0";

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
    time_to_full: "Time to full",
    charge_time: "Charge time",
    odometer: "Odometer",
    last_update: "Last update",
    just_now: "just now",
    min_ago: "{n} min ago",
    h_ago: "{n} h ago",
    d_ago: "{n} d ago",
    // cluster list
    value: "value",
    values: "values",
    no_cluster_entities:
      "No {label} entities for this vehicle. Enable the cluster in the integration options.",
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
    tire_pressure: "Tire pressure",
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
    t_current: "Current",
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
    ch_error: "Couldn't load charging history. Reload the page and try again.",
    ch_home: "Home",
    ch_public: "Away",
    ch_assumed: "assumed",
    ch_partial: "partial price",
    ch_session_one: "1 session",
    ch_session_many: "{n} sessions",
    ch_peak: "Peak",
    ch_avg: "Avg",
    ch_grid: "From grid",
    ch_duration: "Duration",
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
    // trips
    tr_title: "Trips",
    tr_loading: "Loading trips…",
    tr_empty:
      "No trips recorded yet. Once the car is driven, trips appear here automatically.",
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
    ed_overview_option: "Overview (default)",
    ed_overrides_title: "Entity overrides (optional — leave empty to auto-detect)",
    edh_cluster:
      "Overview shows the hero image and key metrics. Charging history lists recorded sessions with cost and power curve. Trips lists recorded drives with a month-in-review summary. Battery health shows learned usable capacity and its trend. A cluster shows every value of that group as a list.",
    edh_title: "Overrides the vehicle name shown on the card.",
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
    time_to_full: "Bis voll",
    charge_time: "Ladezeit",
    odometer: "Kilometerstand",
    last_update: "Letzte Aktualisierung",
    just_now: "gerade eben",
    min_ago: "vor {n} Min.",
    h_ago: "vor {n} Std.",
    d_ago: "vor {n} T.",
    // cluster list
    value: "Wert",
    values: "Werte",
    no_cluster_entities:
      "Keine {label}-Entitäten für dieses Fahrzeug. Aktiviere den Cluster in den Integrationsoptionen.",
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
    tire_pressure: "Reifendruck",
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
    t_current: "Aktuell",
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
    ch_error: "Ladeverlauf konnte nicht geladen werden. Lade die Seite neu und versuche es erneut.",
    ch_home: "Zuhause",
    ch_public: "Unterwegs",
    ch_assumed: "angenommen",
    ch_partial: "Teilpreis",
    ch_session_one: "1 Ladevorgang",
    ch_session_many: "{n} Ladevorgänge",
    ch_peak: "Spitze",
    ch_avg: "Ø",
    ch_grid: "Aus dem Netz",
    ch_duration: "Dauer",
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
    // trips
    tr_title: "Fahrten",
    tr_loading: "Fahrten werden geladen…",
    tr_empty:
      "Noch keine Fahrten aufgezeichnet. Sobald das Fahrzeug bewegt wird, erscheinen Fahrten hier automatisch.",
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
    ed_overview_option: "Übersicht (Standard)",
    ed_overrides_title: "Entitäten überschreiben (optional — leer lassen für Auto-Erkennung)",
    edh_cluster:
      "Die Übersicht zeigt das Fahrzeugbild und Kennzahlen. Der Ladeverlauf listet aufgezeichnete Ladevorgänge mit Kosten und Ladekurve. Fahrten listet aufgezeichnete Fahrten mit einer Monatsübersicht. Der Batteriezustand zeigt die gelernte nutzbare Kapazität und ihren Verlauf. Ein Cluster listet alle Werte dieser Gruppe auf.",
    edh_title: "Überschreibt den auf der Karte angezeigten Fahrzeugnamen.",
  },
};

function _lang(hass) {
  const loc = hass && hass.locale;
  return (loc && loc.language) || (hass && hass.language) || "en";
}

/** Translate `key` for the active hass language, filling `{name}` vars.
 *  Falls back to English, then to `dflt` (or the key itself). */
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

class BmwCardataCard extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._sig = null; // force first render
    if (this._hass) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    if (this._config && this._config.view === "charging") return 10;
    if (this._config && this._config.view === "trips") return 11;
    if (this._config && this._config.view === "health") return 7;
    return this._config && this._config.cluster ? 6 : 8;
  }

  /** Localized string for the active Home Assistant language. */
  _t(key, vars, dflt) {
    return t(this._hass, key, vars, dflt);
  }

  static getConfigElement() {
    return document.createElement("bmw-cardata-card-editor");
  }

  static getStubConfig(hass) {
    // Pre-fill the card picker with the first BMW CarData device found.
    const device = BmwCardataCard._firstDevice(hass);
    return device ? { device } : {};
  }

  static _firstDevice(hass) {
    if (!hass || !hass.entities) return undefined;
    for (const ent of Object.values(hass.entities)) {
      if (ent.platform === "bavariandata" && ent.device_id) return ent.device_id;
    }
    return undefined;
  }

  /* ---- discovery -------------------------------------------------------- */

  _resolveDeviceId() {
    const hass = this._hass;
    const cfg = this._config || {};
    if (cfg.device) return cfg.device;
    if (cfg.vin && hass.devices) {
      for (const dev of Object.values(hass.devices)) {
        const ids = dev.identifiers || [];
        if (ids.some((pair) => pair && pair[0] === "bavariandata" && pair[1] === cfg.vin)) {
          return dev.id;
        }
      }
    }
    return BmwCardataCard._firstDevice(hass);
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

  /** Rank candidate entities by keyword preference; return the best entity_id. */
  _pick(entities, { domain = "sensor", prefer = [], avoid = [], deviceClass, unit } = {}) {
    const scored = [];
    for (const id of entities) {
      if (domain && !id.startsWith(domain + ".")) continue;
      const st = this._st(id);
      if (!st) continue;
      const attrs = st.attributes || {};
      if (deviceClass && attrs.device_class !== deviceClass) continue;
      if (unit && attrs.unit_of_measurement !== unit) continue;
      // Match against the descriptor path too (exposed as an attribute). The
      // entity_id and friendly_name are localized (German, etc.), but the
      // descriptor is always the English BMW path, so keyword matching keeps
      // working regardless of the user's Home Assistant language.
      const hay = (
        id +
        " " +
        (attrs.friendly_name || "") +
        " " +
        (attrs.descriptor || "")
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

  _overviewEntities(entities) {
    const cfg = this._config || {};
    return {
      image: cfg.image || entities.find((id) => id.startsWith("image.")),
      soc:
        cfg.soc ||
        this._pick(entities, {
          deviceClass: "battery",
          unit: "%",
          avoid: ["target", "predicted", "health", "testing"],
        }) ||
        this._pick(entities, { prefer: ["charge", "soc"], unit: "%", avoid: ["target", "rate"] }),
      range:
        cfg.range ||
        this._pick(entities, { deviceClass: "distance", prefer: ["electric range", "range"] }) ||
        this._pick(entities, { prefer: ["range"] }),
      charging:
        cfg.charging ||
        this._pick(entities, { prefer: ["charging status", "hvstatus", "charging"], avoid: ["port", "cable", "history"] }),
      target:
        cfg.target_soc ||
        this._pick(entities, { deviceClass: "battery", unit: "%", prefer: ["target"] }),
      timeToFull:
        cfg.time_to_full ||
        this._pick(entities, { prefer: ["fully charged", "time remaining", "timetofully"] }),
      odometer:
        cfg.odometer ||
        this._pick(entities, { prefer: ["mileage", "odometer", "travelled", "traveled"] }),
      plug:
        cfg.plug ||
        this._pick(entities, { domain: "binary_sensor", prefer: ["plug", "connector"] }) ||
        this._pick(entities, { prefer: ["plug", "connection status"], avoid: ["stream"] }),
    };
  }

  /* ---- formatting ------------------------------------------------------- */

  _fmt(st) {
    if (!st) return "—";
    if (UNAVAILABLE.has(st.state)) return "—";
    const hass = this._hass;
    if (hass.formatEntityState) {
      try {
        return hass.formatEntityState(st);
      } catch (e) {
        /* fall through */
      }
    }
    const unit = st.attributes && st.attributes.unit_of_measurement;
    return unit ? `${st.state} ${unit}` : st.state;
  }

  _num(st) {
    if (!st || UNAVAILABLE.has(st.state)) return null;
    const n = Number(st.state);
    return Number.isFinite(n) ? n : null;
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

  _isCharging(chargingSt, socSt) {
    const hay = [chargingSt && chargingSt.state, socSt && socSt.attributes && socSt.attributes.charging]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return /(^|_| )charging|active|in_progress/.test(hay) && !/not|no_?charging|complete|finished/.test(hay);
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
    } else if (this._config.view === "health") {
      this._renderHealth(deviceId, entities);
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

  _deviceName(deviceId) {
    const dev = this._hass.devices && this._hass.devices[deviceId];
    return (dev && (dev.name_by_user || dev.name)) || "BMW";
  }

  _renderOverview(deviceId, entities) {
    const picks = this._overviewEntities(entities);
    const socSt = this._st(picks.soc);
    const chargingSt = this._st(picks.charging);
    const rangeSt = this._st(picks.range);
    const soc = this._num(socSt);
    const charging = this._isCharging(chargingSt, socSt);
    const name = this._config.title || this._deviceName(deviceId);

    // freshest update among the headline entities
    const freshest = [socSt, rangeSt, chargingSt]
      .filter(Boolean)
      .map((s) => s.last_changed)
      .sort()
      .pop();

    const secondary = [
      // Range and charging status live in the lead band above; the grid carries
      // the rest of the at-a-glance metrics.
      { key: "target", label: this._t("target"), st: this._st(picks.target), icon: "mdi:target" },
      { key: "plug", label: this._t("plug"), st: this._st(picks.plug), icon: charging ? "mdi:power-plug" : "mdi:power-plug-off" },
      { key: "ttf", label: charging ? this._t("time_to_full") : this._t("charge_time"), st: this._st(picks.timeToFull), icon: "mdi:timer-sand" },
      { key: "odo", label: this._t("odometer"), st: this._st(picks.odometer), icon: "mdi:counter" },
    ].filter((m) => m.st);

    const sig = this._signature({
      m: "ov",
      lang: _lang(this._hass),
      name,
      soc,
      charging,
      img: picks.image,
      fresh: freshest,
      sec: secondary.map((s) => [s.label, s.st.state]),
      plug: picks.plug && this._st(picks.plug) && this._st(picks.plug).state,
    });
    if (sig === this._sig) return;
    this._sig = sig;

    const imgUrl = picks.image ? this._imageUrl(picks.image) : null;
    const ringColor = charging
      ? "var(--bmw-charge)"
      : soc == null
      ? "var(--divider-color)"
      : soc <= 15
      ? "var(--bmw-low)"
      : soc <= 40
      ? "var(--bmw-mid)"
      : "var(--bmw-high)";
    const pct = soc == null ? 0 : Math.max(0, Math.min(100, soc));
    const rel = this._relTime(freshest);

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        <div class="hero ${imgUrl ? "" : "hero--empty"}">
          ${imgUrl ? `<img class="hero__img" src="${imgUrl}" alt="${name}" />` : `<ha-icon class="hero__placeholder" icon="mdi:car-electric"></ha-icon>`}
          <div class="hero__scrim"></div>
          <div class="hero__top">
            <div class="hero__name" title="${name}">${name}</div>
            ${rel ? `<div class="pill" title="${this._t("last_update")}"><span class="dot ${this._staleClass(freshest)}"></span>${rel}</div>` : ""}
          </div>
        </div>

        <div class="band">
          <button class="gauge" data-entity="${picks.soc || ""}" aria-label="State of charge">
            <div class="gauge__ring" style="--pct:${pct};--ring:${ringColor}">
              <div class="gauge__hole">
                <span class="gauge__val">${soc == null ? "—" : Math.round(soc)}<i>%</i></span>
                <span class="gauge__cap">${charging ? this._t("charging") : this._t("charge")}</span>
              </div>
            </div>
            ${charging ? `<ha-icon class="gauge__bolt" icon="mdi:lightning-bolt"></ha-icon>` : ""}
          </button>

          <div class="lead">
            <button class="lead__row" data-entity="${picks.range || ""}">
              <ha-icon icon="mdi:map-marker-distance"></ha-icon>
              <span class="lead__val">${this._fmt(rangeSt)}</span>
              <span class="lead__lbl">${this._t("remaining_range")}</span>
            </button>
            <button class="lead__row" data-entity="${picks.charging || ""}">
              <ha-icon icon="${charging ? "mdi:battery-charging" : "mdi:ev-station"}"></ha-icon>
              <span class="lead__val">${this._chargingLabel(chargingSt, charging)}</span>
              <span class="lead__lbl">${this._t("charging_status")}</span>
            </button>
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
  }

  _chargingLabel(st, charging) {
    const raw = st && st.state != null ? String(st.state).toLowerCase() : "";
    if (!st || UNAVAILABLE.has(st.state) || NOT_CHARGING_STATES.has(raw)) {
      return this._t(charging ? "is_charging" : "not_charging");
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

    const label = this._config.title || this._clusterLabel(slug);
    const icon = CLUSTER_ICONS[slug] || "mdi:car";
    const name = this._deviceName(deviceId);

    const sig = this._signature({
      m: "cl",
      lang: _lang(this._hass),
      slug,
      rows: rows.map((s) => [s.entity_id, s.state]),
    });
    if (sig === this._sig) return;
    this._sig = sig;

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        <div class="chead">
          <ha-icon icon="${icon}"></ha-icon>
          <div class="chead__text">
            <span class="chead__title">${label}</span>
            <span class="chead__sub">${name} · ${rows.length} ${this._t(rows.length === 1 ? "value" : "values")}</span>
          </div>
        </div>
        ${
          rows.length
            ? `<div class="list">
                ${rows
                  .map((st) => {
                    const category = st.attributes.category;
                    return `<button class="item" data-entity="${st.entity_id}">
                      <span class="item__name" title="${st.attributes.friendly_name || st.entity_id}">${this._shortName(st, name)}</span>
                      <span class="item__val">${this._fmt(st)}</span>
                    </button>`;
                  })
                  .join("")}
              </div>`
            : `<div class="empty">${this._t("no_cluster_entities", { label: `<b>${label}</b>` })}</div>`
        }
      </ha-card>
    `;
    this._wireTaps();
  }

  /** Localized display label for a catalogue cluster slug. */
  _clusterLabel(slug) {
    return this._t("cl_" + slug, null, slug);
  }

  _shortName(st, deviceName) {
    let n = st.attributes.friendly_name || st.entity_id;
    if (deviceName && n.startsWith(deviceName + " ")) n = n.slice(deviceName.length + 1);
    return n;
  }

  /* ---- charging history ------------------------------------------------- */

  _renderCharging(deviceId, entities) {
    const vin = this._deviceVin(deviceId);
    if (!vin) {
      this._renderMessage(this._t("no_vehicle_title"), this._t("no_vehicle_body"));
      return;
    }

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

    const cache = this._chg;
    const current = cache && cache.vin === vin && cache.trigger === trigger;
    if (!current || (!cache.data && !cache.loading)) {
      this._chg = {
        vin,
        trigger,
        data: current && cache ? cache.data : null,
        loading: true,
        error: false,
      };
      this._fetchCharging(vin);
    }

    this._paintCharging(deviceId, entities);
  }

  _fetchCharging(vin) {
    const req = this._chg;
    this._hass
      .callService(
        "bavariandata",
        "get_charging_sessions",
        { vin, limit: 40 },
        undefined,
        false,
        true
      )
      .then((res) => {
        // Ignore a response for a request we've already superseded.
        if (!this._chg || this._chg.vin !== vin || this._chg.trigger !== req.trigger)
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
    const summary = this._chargingSummary(entities);
    const expanded = this._chgExpanded || null;

    const sig = this._signature({
      m: "chg",
      lang: _lang(this._hass),
      name,
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
      body = `<div class="empty">${this._t("ch_empty")}</div>`;
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
            <span class="chead__title">${this._config.title || this._t("ch_title")}</span>
            <span class="chead__sub">${name}${count ? " · " + countLabel : ""}</span>
          </div>
          ${this._exportButtons("charging")}
        </div>
        ${summary ? this._chargingSummaryBand(summary) : ""}
        ${body}
      </ha-card>
    `;
    this._wireChargingTaps();
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
    const energy =
      session.energy_kwh != null ? `${this._round(session.energy_kwh, 1)} kWh` : "—";
    const cost = this._fmtCost(session.cost);
    const soc = this._socArc(session);
    const badge = this._locationBadge(session);
    const partial =
      session.cost && session.cost.partial
        ? `<span class="chg__tag chg__tag--warn">${this._t("ch_partial")}</span>`
        : "";
    const ongoing = session.end
      ? ""
      : `<span class="chg__tag">${this._t("ch_ongoing")}</span>`;

    return `
      <div class="chg__session${isOpen ? " is-open" : ""}">
        <button class="chg__row" data-session="${this._attr(id)}">
          <span class="chg__row-main">
            <span class="chg__date">${date}</span>
            <span class="chg__meta">${soc}${badge}${ongoing}${partial}</span>
          </span>
          <span class="chg__figures">
            <span class="chg__energy">${energy}</span>
            <span class="chg__cost">${cost}</span>
          </span>
        </button>
        ${isOpen ? this._chargingDetail(session) : ""}
      </div>
    `;
  }

  _chargingDetail(session) {
    const chart = this._powerCurveSvg(session.power_curve);
    const facts = [];
    if (session.peak_power_kw != null) {
      facts.push([this._t("ch_peak"), `${this._round(session.peak_power_kw, 1)} kW`]);
    }
    const avg = this._avgPowerKw(session);
    if (avg != null) facts.push([this._t("ch_avg"), `${avg} kW`]);
    // duration_s isn't in the service payload; derive it from the timestamps.
    const dur = this._durationLabel(session);
    if (dur) facts.push([this._t("ch_duration"), dur]);
    if (session.grid_kwh != null) {
      facts.push([this._t("ch_grid"), `${this._round(session.grid_kwh, 1)} kWh`]);
    }

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

  /** Inline SVG line chart of the [seconds, kW] power curve. No dependencies. */
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
    const line = curve.map((p) => `${this._round(sx(p[0]), 1)},${this._round(sy(p[1]), 1)}`).join(" ");
    const area = `${pad},${H - pad} ${line} ${W - pad},${H - pad}`;
    return `
      <svg class="chg__chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img">
        <polygon points="${area}" class="chg__chart-fill"></polygon>
        <polyline points="${line}" class="chg__chart-line"></polyline>
        <text x="${pad}" y="10" class="chg__chart-max">${this._round(yMax, 1)} kW</text>
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
    const lang = _lang(this._hass);
    try {
      return new Intl.NumberFormat(lang, {
        style: "currency",
        currency: cost.currency || "EUR",
        maximumFractionDigits: 2,
      }).format(cost.amount);
    } catch (e) {
      return `${this._round(cost.amount, 2)} ${cost.currency || ""}`.trim();
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
    if (!session.start || !session.end) return null;
    const ms = new Date(session.end).getTime() - new Date(session.start).getTime();
    if (!(ms > 0)) return null;
    const mins = Math.round(ms / 60000);
    if (mins < 60) return `${mins} min`;
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    return m ? `${h} h ${m} min` : `${h} h`;
  }

  _avgPowerKw(session) {
    if (session.energy_kwh == null || !session.start || !session.end) return null;
    const hours =
      (new Date(session.end).getTime() - new Date(session.start).getTime()) / 3600000;
    if (!(hours > 0)) return null;
    return this._round(session.energy_kwh / hours, 1);
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
    const vin = this._deviceVin(this._resolveDeviceId());
    // One export at a time: the button stays in the DOM across repaints, and a
    // double tap would otherwise download the same month twice.
    if (!vin || this._exporting) return;
    this._exporting = true;
    this._hass
      .callService(
        "bavariandata",
        "export_history",
        { vin, type: kind, format },
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

    // Like charging, trips come from services (a list + the month-in-review),
    // not entity state. Gate the fetch on the monthly-distance sensor's
    // last_changed so a plain hass tick never hits the services.
    const trigSt = entities
      .map((id) => this._st(id))
      .find(
        (st) =>
          st &&
          st.attributes &&
          st.attributes.descriptor === "driving_distance_month"
      );
    const trigger = trigSt ? trigSt.last_changed : "";

    const cache = this._trp;
    const current = cache && cache.vin === vin && cache.trigger === trigger;
    if (!current || (!cache.trips && !cache.loading)) {
      this._trp = {
        vin,
        trigger,
        trips: current && cache ? cache.trips : null,
        summary: current && cache ? cache.summary : null,
        loading: true,
        error: false,
      };
      this._fetchTrips(vin);
    }

    this._paintTrips(deviceId, entities);
  }

  _fetchTrips(vin) {
    const req = this._trp;
    const call = (service, data) =>
      this._hass.callService("bavariandata", service, data, undefined, false, true);
    Promise.all([
      call("get_trips", { vin, limit: 60 }),
      call("get_driving_summary", { vin }),
    ])
      .then(([tripsRes, sumRes]) => {
        if (!this._trp || this._trp.vin !== vin || this._trp.trigger !== req.trigger)
          return;
        const trips = (tripsRes && tripsRes.response && tripsRes.response.trips) || [];
        const summary = (sumRes && sumRes.response && sumRes.response.summary) || null;
        this._trp = { ...this._trp, trips, summary, loading: false, error: false };
        this._render();
      })
      .catch(() => {
        if (!this._trp || this._trp.vin !== vin || this._trp.trigger !== req.trigger)
          return;
        this._trp = { ...this._trp, loading: false, error: true };
        this._render();
      });
  }

  _paintTrips(deviceId, entities) {
    const name = this._config.title || this._deviceName(deviceId);
    const state = this._trp || {};
    const trips = state.trips;
    const summary = state.summary;
    const expanded = this._trpExpanded || null;

    const sig = this._signature({
      m: "trp",
      lang: _lang(this._hass),
      name,
      loading: state.loading && !trips,
      error: state.error,
      summary,
      expanded,
      rows: (trips || []).map((t) => [t.start, t.distance_km, t.classification]),
    });
    if (sig === this._sig) return;
    this._sig = sig;

    let body;
    if (state.error) {
      body = `<div class="empty">${this._t("tr_error")}</div>`;
    } else if (!trips && state.loading) {
      body = `<div class="empty">${this._t("tr_loading")}</div>`;
    } else if (!trips || !trips.length) {
      body = `<div class="empty">${this._t("tr_empty")}</div>`;
    } else {
      body = `${this._tripReview(summary)}<div class="chg__list">${trips
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
            <span class="chead__title">${this._config.title || this._t("tr_title")}</span>
            <span class="chead__sub">${name}${count ? " · " + countLabel : ""}</span>
          </div>
          ${this._exportButtons("trips")}
        </div>
        ${body}
      </ha-card>
    `;
    this._wireTripTaps();
  }

  /** The "month in review" panel, built entirely from get_driving_summary. */
  _tripReview(summary) {
    if (!summary || !summary.total_km) return "";
    const km = (v) => (v == null ? "—" : `${this._round(v, 0)} km`);
    const split = summary.split || {};

    // Headline tiles: distance with a month-over-month arrow, and trip count.
    const delta = summary.mom_delta_percent;
    const arrow = delta == null ? "" : delta > 0 ? "▲" : delta < 0 ? "▼" : "→";
    const deltaTxt =
      delta == null
        ? ""
        : `<span class="tr__delta tr__delta--${delta >= 0 ? "up" : "down"}">${arrow} ${Math.abs(
            this._round(delta, 0)
          )}% ${this._t("tr_vs_last")}</span>`;

    const tiles = [
      `<div class="tr__tile"><span class="tr__tile-val">${this._round(
        summary.total_km,
        0
      )} <i>km</i></span><span class="tr__tile-lbl">${this._t("tr_review")}</span>${deltaTxt}</div>`,
    ];
    if (summary.avg_consumption_kwh_per_100km != null) {
      tiles.push(
        `<div class="tr__tile"><span class="tr__tile-val">${this._round(
          summary.avg_consumption_kwh_per_100km,
          1
        )} <i>kWh/100km</i></span><span class="tr__tile-lbl">${this._t(
          "tr_consumption"
        )}</span></div>`
      );
    }
    if (summary.recuperation_kwh != null) {
      tiles.push(
        `<div class="tr__tile"><span class="tr__tile-val">${this._round(
          summary.recuperation_kwh,
          1
        )} <i>kWh</i></span><span class="tr__tile-lbl">${this._t(
          "tr_recuperation"
        )}</span></div>`
      );
    }
    if (summary.estimated_cost && summary.estimated_cost.amount != null) {
      const c = summary.estimated_cost;
      tiles.push(
        `<div class="tr__tile"><span class="tr__tile-val">${this._round(c.amount, 2)} <i>${
          c.currency || ""
        }</i></span><span class="tr__tile-lbl">${this._t("tr_est_cost")}</span></div>`
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
    const date = this._fmtSessionDate(trip.start);
    const from = this._tripPlace(trip.start_place);
    const to = this._tripPlace(trip.end_place);
    const dist =
      trip.distance_km != null ? `${this._round(trip.distance_km, 1)} km` : "—";
    const cls = trip.classification
      ? `<span class="tr__badge tr__badge--${trip.classification}">${this._t(
          "tr_" + trip.classification
        )}${
          trip.classification_source === "auto"
            ? ` <i class="tr__auto">${this._t("tr_auto")}</i>`
            : ""
        }</span>`
      : "";
    const dur = this._durationLabel(trip);

    return `
      <div class="chg__session${isOpen ? " is-open" : ""}">
        <button class="chg__row" data-trip="${this._attr(id)}">
          <span class="chg__row-main">
            <span class="chg__date">${this._esc(from)} → ${this._esc(to)}</span>
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
    const cons =
      trip.energy_kwh != null && trip.distance_km
        ? this._round((trip.energy_kwh / trip.distance_km) * 100, 1)
        : null;
    if (cons != null) facts.push([this._t("tr_consumption"), `${cons} kWh/100km`]);
    const st = trip.stats || {};
    if (st.recuperation_kwh != null) {
      facts.push([this._t("tr_recuperation"), `${this._round(st.recuperation_kwh, 1)} kWh`]);
    }
    const soc = this._socArc(trip);

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

    return `
      <div class="chg__detail">
        ${soc ? `<div class="chg__facts">${soc}</div>` : ""}
        ${factRow ? `<div class="chg__facts">${factRow}</div>` : ""}
        <div class="tr__classify">
          <span class="tr__classify-lbl">${this._t("tr_classify")}</span>
          <span class="tr__cls-btns">${buttons}</span>
        </div>
      </div>
    `;
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
      });
    });
    this.shadowRoot.querySelectorAll("[data-trip-class]").forEach((el) => {
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const tripId = el.getAttribute("data-trip-class");
        const cls = el.getAttribute("data-class");
        const vin = this._trp && this._trp.vin;
        if (!vin) return;
        this._hass
          .callService("bavariandata", "set_trip_class", {
            vin,
            trip_id: `${vin}-${tripId}`,
            classification: cls,
          })
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

  /* ---- battery health --------------------------------------------------- */

  _renderHealth(deviceId, entities) {
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
            <span class="chead__title">${this._config.title || this._t("bh_title")}</span>
            <span class="chead__sub">${name}</span>
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
      facts.push([this._t("bh_nominal"), `${this._round(nominal, 1)} kWh`]);
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
            <span class="bh__usable">${this._round(usable, 1)} <i>kWh</i></span>
            <span class="bh__usable-lbl">${this._t("bh_usable")}</span>
            ${
              vsNew != null
                ? `<span class="bh__vsnew">${this._t("bh_of_new", { p: this._round(vsNew, 0) })}</span>`
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
    const label = p == null ? "—" : `${this._round(p, 0)}%`;
    return `
      <svg class="bh__ring" viewBox="0 0 80 80" role="img">
        <circle class="bh__ring-track" cx="40" cy="40" r="${r}"></circle>
        <circle class="bh__ring-val" cx="40" cy="40" r="${r}"
          stroke-dasharray="${this._round(dash, 1)} ${this._round(circ, 1)}"
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
      .map((p) => `${this._round(sx(p[0]), 1)},${this._round(sy(p[1]), 1)}`)
      .join(" ");
    return `
      <svg class="bh__chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img">
        <polyline points="${line}" class="chg__chart-line"></polyline>
        <text x="${pad}" y="10" class="chg__chart-max">${this._round(yMax, 1)} kWh</text>
        <text x="${pad}" y="${H - 3}" class="chg__chart-max">${this._round(yMin, 1)} kWh</text>
      </svg>
    `;
  }

  _round(n, dp) {
    const f = Math.pow(10, dp);
    return Math.round(n * f) / f;
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

    const sig = this._signature({
      m: "tire",
      lang: _lang(this._hass),
      w: Object.fromEntries(
        Object.entries(wheels).map(([k, m]) => [
          k,
          Object.fromEntries(Object.entries(m).map(([mk, s]) => [mk, s.state])),
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

    // Fleet-wide status summary for the header.
    const statuses = present.map((s) => this._tireStatus(wheels[s.key]).cls);
    const worst = statuses.includes("low")
      ? { t: this._t("check_pressure"), c: "var(--bmw-low)" }
      : statuses.includes("high")
      ? { t: this._t("slightly_high"), c: "var(--bmw-mid)" }
      : statuses.every((c) => c === "ok")
      ? { t: this._t("all_nominal"), c: "var(--bmw-high)" }
      : { t: this._t("of_four", { n: present.length }), c: "var(--secondary-text-color)" };

    const colors = {
      fl: this._tireStatus(wheels.row1_left || {}).color,
      fr: this._tireStatus(wheels.row1_right || {}).color,
      rl: this._tireStatus(wheels.row2_left || {}).color,
      rr: this._tireStatus(wheels.row2_right || {}).color,
    };

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        ${this._tireHead(name, worst.t, worst.c)}
        <div class="tirecar">
          <span class="tirecar__front">${this._t("front")}</span>
          ${this._carSvg(colors)}
          ${this._wheelLabel(slots[0], wheels.row1_left, "fl")}
          ${this._wheelLabel(slots[1], wheels.row1_right, "fr")}
          ${this._wheelLabel(slots[2], wheels.row2_left, "rl")}
          ${this._wheelLabel(slots[3], wheels.row2_right, "rr")}
        </div>
      </ha-card>`;
    this._wireTaps();
  }

  _carSvg(c) {
    // Top-down BMW sedan with each wheel stroked in its tire-status colour.
    return `
      <svg class="carsvg" viewBox="0 0 130 228" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        ${this._carWheels(c)}
        ${this._carBody()}
      </svg>`;
  }

  // Four wheels, each stroked in its colour (falls back to the neutral divider
  // colour when a caller doesn't care about per-wheel status, e.g. closures).
  // Tucked under the flared arches so the tyre reads as a wheel, not a block.
  _carWheels(c = {}) {
    const n = "var(--divider-color)";
    const wheel = (x, y, color) =>
      `<rect x="${x}" y="${y}" width="13" height="34" rx="6" class="carsvg__wheel" style="stroke:${color || n}"/>`;
    return `${wheel(13, 40, c.fl)}${wheel(104, 40, c.fr)}${wheel(13, 160, c.rl)}${wheel(104, 160, c.rr)}`;
  }

  // Static body art. Shared by the tire diagram and the closures diagram; the
  // latter layers overlays on top. Design language: taut rectilinear silhouette,
  // gradient-modelled sheet metal (no cartoon keyline), long-hood / cab-rearward
  // stance, and correctly-scaled BMW cues (twin front-of-bumper kidneys, swept
  // corner-wrapping lamps). viewBox 130x228, centreline x=65.
  _carBody() {
    return `
        <defs>
          <linearGradient id="bodyGrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stop-color="var(--body-lo)"/>
            <stop offset=".5" stop-color="var(--body-hi)"/>
            <stop offset="1" stop-color="var(--body-lo)"/>
          </linearGradient>
          <linearGradient id="roofGrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stop-color="var(--roof-lo)"/>
            <stop offset=".5" stop-color="var(--roof-hi)"/>
            <stop offset="1" stop-color="var(--roof-lo)"/>
          </linearGradient>
          <linearGradient id="glassGrad" x1="0" y1="0" x2=".35" y2="1">
            <stop offset="0" stop-color="var(--glass-hi)"/>
            <stop offset="1" stop-color="var(--glass-lo)"/>
          </linearGradient>
        </defs>

        <!-- fender flares over each wheel -->
        <rect x="20" y="39" width="5" height="34" rx="2.5" class="carsvg__flare"/>
        <rect x="105" y="39" width="5" height="34" rx="2.5" class="carsvg__flare"/>
        <rect x="20" y="159" width="5" height="34" rx="2.5" class="carsvg__flare"/>
        <rect x="105" y="159" width="5" height="34" rx="2.5" class="carsvg__flare"/>

        <!-- taut body: wider stance, squarer bumpers, straight flanks -->
        <path d="M40 6 L90 6 C99 6 107 12 108 24 L108 198 C107 211 103 219 94 222 L36 222 C27 219 23 211 22 198 L22 24 C23 12 31 6 40 6 Z" class="carsvg__body"/>

        <!-- side mirrors at the cowl -->
        <path d="M22 88 L14 84 L12 90 L21 94 Z" class="carsvg__mirror"/>
        <path d="M108 88 L116 84 L118 90 L109 94 Z" class="carsvg__mirror"/>

        <!-- bumper/hood seam + hood centreline + hood shut lines -->
        <path d="M38 22 C52 20.5 78 20.5 92 22" class="carsvg__seam"/>
        <path d="M65 24 L65 86" class="carsvg__crease"/>
        <path d="M32 38 C29 55 29 74 34 88 M98 38 C101 55 101 74 96 88" class="carsvg__seam"/>

        <!-- twin kidneys at the very front of the bumper -->
        <rect x="54" y="6.5" width="22" height="13" rx="2" class="carsvg__chrome"/>
        <rect x="55" y="7.5" width="9.3" height="11" rx="1.5" class="carsvg__kidney"/>
        <rect x="65.7" y="7.5" width="9.3" height="11" rx="1.5" class="carsvg__kidney"/>
        <path d="M57 8.5 V17.5 M60 8.5 V17.5 M67.5 8.5 V17.5 M70.5 8.5 V17.5" class="carsvg__kbar"/>

        <!-- headlights: fat at the outer bumper corner, tapering inward -->
        <path d="M22.5 23 C21 11 29 6.5 40 6.5 L47 7 C50.5 8.5 50 10.5 47.5 11.5 C39 12.5 30 15.5 22.5 23 Z" class="carsvg__light"/>
        <path d="M107.5 23 C109 11 101 6.5 90 6.5 L83 7 C79.5 8.5 80 10.5 82.5 11.5 C91 12.5 100 15.5 107.5 23 Z" class="carsvg__light"/>

        <!-- windshield -->
        <path d="M31 88 L99 88 L87 112 L43 112 Z" class="carsvg__glass"/>

        <!-- roof + sunroof -->
        <path d="M43 112 L87 112 L86 164 L44 164 Z" class="carsvg__roof"/>
        <rect x="53" y="120" width="24" height="28" rx="2" class="carsvg__glassdk"/>

        <!-- side windows: front pair butts the windshield; rear pair matched in length -->
        <path d="M33 98 L43 112 L43 136 L35 136 Z" class="carsvg__glass"/>
        <path d="M35 140 L43 140 L43 162 L37 162 Z" class="carsvg__glass"/>
        <path d="M97 98 L87 112 L87 136 L95 136 Z" class="carsvg__glass"/>
        <path d="M95 140 L87 140 L87 162 L93 162 Z" class="carsvg__glass"/>

        <!-- door shut seams + handles (4 doors) -->
        <path d="M22 138 L43 138 M108 138 L87 138" class="carsvg__seam"/>
        <rect x="26" y="122" width="6" height="1.8" rx=".9" class="carsvg__handle"/>
        <rect x="27" y="150" width="6" height="1.8" rx=".9" class="carsvg__handle"/>
        <rect x="98" y="122" width="6" height="1.8" rx=".9" class="carsvg__handle"/>
        <rect x="97" y="150" width="6" height="1.8" rx=".9" class="carsvg__handle"/>

        <!-- rear window -->
        <path d="M43 164 L87 164 L97 182 L33 182 Z" class="carsvg__glass"/>

        <!-- rear deck: trunk seam + corner-wrapping tail lights + diffuser -->
        <path d="M33 187 C48 190 82 190 97 187" class="carsvg__seam"/>
        <path d="M22.5 205 C21 217 29 221.5 40 221.5 L47 221 C49.5 219.5 49 218.5 47 217.8 C39 217 30 214.5 22.5 205 Z" class="carsvg__tail"/>
        <path d="M107.5 205 C109 217 101 221.5 90 221.5 L83 221 C80.5 219.5 81 218.5 83 217.8 C91 217 100 214.5 107.5 205 Z" class="carsvg__tail"/>
        <path d="M52 217 L78 217" class="carsvg__crease"/>`;
  }

  _wheelLabel(slot, wheel, pos) {
    const status = wheel ? this._tireStatus(wheel) : { label: "—", color: "var(--divider-color)" };
    const pressure = wheel && wheel.pressure;
    const target = wheel && wheel.pressureTarget;
    const temp = wheel && wheel.temperature;
    const tapId = (pressure && pressure.entity_id) || (temp && temp.entity_id) || "";
    const pv = pressure ? this._splitValueUnit(pressure) : { value: "—", unit: "" };
    const sub = [
      target ? `◎ ${this._fmt(target)}` : null,
      temp ? this._fmt(temp) : null,
    ]
      .filter(Boolean)
      .join(" · ");
    return `
      <button class="wlabel wlabel--${pos}" style="--c:${status.color}" data-entity="${tapId}" title="${slot.full}">
        <span class="wlabel__pos"><b>${slot.label}</b><span class="wlabel__badge">${status.label}</span></span>
        <span class="wlabel__val">${pv.value}${pv.unit ? `<i>${pv.unit}</i>` : ""}</span>
        ${sub ? `<span class="wlabel__sub">${sub}</span>` : ""}
      </button>`;
  }

  _tireHead(name, statusText, statusColor) {
    return `
      <div class="chead">
        <ha-icon icon="mdi:car-tire-alert"></ha-icon>
        <div class="chead__text">
          <span class="chead__title">${this._t("tire_pressure")}</span>
          <span class="chead__sub">${name}</span>
        </div>
        ${
          statusText
            ? `<span class="tstat" style="--c:${statusColor || "var(--secondary-text-color)"}"><span class="tstat__dot"></span>${statusText}</span>`
            : ""
        }
      </div>`;
  }

  _tireStatus(wheel) {
    const cur = this._num(wheel.pressure);
    const tgt = this._num(wheel.pressureTarget);
    if (cur == null) return { cls: "na", label: this._t("t_nodata"), color: "var(--divider-color)" };
    if (tgt == null) return { cls: "na", label: this._t("t_current"), color: "var(--divider-color)" };
    const devPct = ((cur - tgt) / tgt) * 100;
    const tol = 4; // ±4% of target counts as nominal
    if (devPct < -tol) return { cls: "low", label: this._t("t_low"), color: "var(--bmw-low)" };
    if (devPct > tol) return { cls: "high", label: this._t("t_high"), color: "var(--bmw-mid)" };
    return { cls: "ok", label: this._t("t_ok"), color: "var(--bmw-high)" };
  }

  /* ---- closures / security diagram -------------------------------------- */

  // Descriptor paths for every closure signal the card knows how to place.
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
    lock: "vehicle.cabin.door.lock.status",
    alarmArm: "vehicle.vehicle.antiTheftAlarmSystem.alarm.armStatus",
    alarmOn: "vehicle.vehicle.antiTheftAlarmSystem.alarm.isOn",
  };

  _renderClosures(deviceId, entities) {
    const P = BmwCardataCard.CLOSURE_PATHS;
    const byDesc = {};
    for (const id of entities) {
      const st = this._st(id);
      const d = st && st.attributes && st.attributes.descriptor;
      if (d) byDesc[d] = id;
    }
    const find = (path) =>
      Array.isArray(path) ? path.map((p) => byDesc[p]).find(Boolean) : byDesc[path];

    const name = this._deviceName(deviceId);
    const ALERT = "var(--bmw-low)";
    const WARN = "var(--bmw-mid)";
    const OK = "var(--bmw-high)";

    // Per-slot doors (prefer isOpen; fall back to position sensor).
    const doors = {};
    for (const k of ["lf", "rf", "lr", "rr"]) {
      const id = find(P.doorOpen[k]) || find(P.doorPos[k]);
      if (!id) continue;
      const st = this._st(id);
      doors[k] = { id, open: this._openState(st) };
    }
    // Per-slot windows.
    const windows = {};
    for (const k of ["lf", "rf", "lr", "rr"]) {
      const id = find(P.window[k]);
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

    const lockId = find(P.lock);
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

    // Change-detection signature.
    const stateOf = (id) => (id && this._st(id) ? this._st(id).state : null);
    const sig = this._signature({
      m: "clo",
      lang: _lang(this._hass),
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

  _closuresHead(name, overall) {
    return `
      <div class="chead">
        <ha-icon icon="mdi:car-door-lock"></ha-icon>
        <div class="chead__text">
          <span class="chead__title">${this._t("cl_closures")}</span>
          <span class="chead__sub">${name}</span>
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
  // open doors sprout a coloured flap, open glass is tinted, hood/trunk shade,
  // and a central padlock reflects the lock state. Every part is tappable.
  _carSvgClosures(d) {
    const { ALERT, WARN } = d.colors;
    const doorGeo = {
      lf: { flap: "M22 116 L5 110 L7 128 L22 134 Z", hit: "22 112 21 27" },
      lr: { flap: "M22 142 L5 136 L7 154 L22 160 Z", hit: "22 139 21 25" },
      rf: { flap: "M108 116 L125 110 L123 128 L108 134 Z", hit: "87 112 21 27" },
      rr: { flap: "M108 142 L125 136 L123 154 L108 160 Z", hit: "87 139 21 25" },
    };
    const winGeo = {
      lf: "M33 98 L43 112 L43 136 L35 136 Z",
      lr: "M35 140 L43 140 L43 162 L37 162 Z",
      rf: "M97 98 L87 112 L87 136 L95 136 Z",
      rr: "M95 140 L87 140 L87 162 L93 162 Z",
    };
    const hit = (spec, id) => {
      const [x, y, w, h] = spec.split(" ");
      return `<rect x="${x}" y="${y}" width="${w}" height="${h}" class="cldiag__hit" data-entity="${id}"/>`;
    };
    const parts = [];

    // Doors: flap when open, always a tap zone.
    for (const k of ["lf", "rf", "lr", "rr"]) {
      const door = d.doors[k];
      if (!door) continue;
      if (door.open) {
        parts.push(`<path d="${doorGeo[k].flap}" class="cldiag__flap" style="fill:${ALERT};stroke:${ALERT}"/>`);
      }
      parts.push(hit(doorGeo[k].hit, door.id));
    }
    // Zones (hood / trunk) shaded when open.
    const zone = (part, path) => {
      if (!part) return;
      if (part.open) parts.push(`<path d="${path}" class="cldiag__zone" style="fill:${ALERT}"/>`);
      parts.push(`<path d="${path}" class="cldiag__hit" data-entity="${part.id}"/>`);
    };
    zone(d.hood, "M38 26 H92 L96 88 H34 Z");
    zone(d.trunk, "M34 184 H96 L93 218 H37 Z");
    // Glass (windows / sunroof / rear window) tinted amber when open.
    const glass = (part, path) => {
      if (!part) return;
      if (part.open) parts.push(`<path d="${path}" class="cldiag__glass-open" style="fill:${WARN}"/>`);
      parts.push(`<path d="${path}" class="cldiag__hit" data-entity="${part.id}"/>`);
    };
    for (const k of ["lf", "rf", "lr", "rr"]) {
      const w = d.windows[k];
      if (w) glass(w, winGeo[k]);
    }
    glass(d.sunroof, "M53 120 H77 V148 H53 Z");
    glass(d.rearWindow, "M43 164 L87 164 L97 182 L33 182 Z");

    // Central padlock (open shackle when unlocked/unknown).
    const locked = d.lock.key === "locked" || d.lock.key === "secured";
    const shackle = locked
      ? "M61 134 V130 a4 4 0 0 1 8 0 V134"
      : "M61 134 V130 a4 4 0 0 1 8 0";
    const padlock = d.lockId
      ? `<g class="cldiag__lock" data-entity="${d.lockId}" style="--c:${d.lock.color}">
           <path d="${shackle}" class="cldiag__shackle"/>
           <rect x="58" y="134" width="14" height="10" rx="1.8" class="cldiag__lockbody"/>
         </g>`
      : "";

    return `
      <svg class="carsvg" viewBox="0 0 130 228" xmlns="http://www.w3.org/2000/svg">
        ${this._carWheels()}
        ${this._carBody()}
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
      .empty, .msg__body { color: var(--secondary-text-color); font-size: 0.9rem; }
      .empty { padding: 22px 18px; }

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
      .tirecar {
        position: relative;
        padding: 14px 12px 18px;
        min-height: 260px;
      }
      .tirecar__front {
        position: absolute; top: 8px; left: 0; right: 0;
        text-align: center;
        font-size: 0.56rem; letter-spacing: 0.18em; font-weight: 700;
        color: var(--secondary-text-color);
      }
      .carsvg {
        display: block;
        width: 40%;
        min-width: 112px;
        max-width: 162px;
        height: auto;
        margin: 4px auto 0;
        overflow: visible;
        /* Surface-modelling tokens derived from the active HA theme, so the
           metal/glass sheen holds up in both light and dark. */
        --body-hi: color-mix(in srgb, var(--secondary-background-color), white 20%);
        --body-lo: color-mix(in srgb, var(--secondary-background-color), black 14%);
        --roof-hi: color-mix(in srgb, var(--card-background-color), white 12%);
        --roof-lo: color-mix(in srgb, var(--card-background-color), black 6%);
        --glass-hi: color-mix(in srgb, var(--divider-color) 66%, #4c5c6e);
        --glass-lo: color-mix(in srgb, var(--divider-color) 50%, #0e141b);
        --chrome: color-mix(in srgb, var(--secondary-text-color), white 22%);
        --edge: var(--divider-color);
        --seam-c: var(--secondary-text-color);
        --tire: #14171b;
      }
      .carsvg__body { fill: url(#bodyGrad); stroke: var(--edge); stroke-width: 0.7; }
      .carsvg__flare { fill: var(--secondary-text-color); opacity: 0.26; }
      .carsvg__crease { stroke: var(--seam-c); stroke-width: 0.7; opacity: 0.35; fill: none; stroke-linecap: round; }
      .carsvg__seam { stroke: var(--seam-c); stroke-width: 0.8; opacity: 0.55; fill: none; stroke-linecap: round; }
      .carsvg__roof { fill: url(#roofGrad); }
      .carsvg__glassdk { fill: var(--glass-lo); opacity: 0.85; }
      .carsvg__glass { fill: url(#glassGrad); }
      .carsvg__handle { fill: var(--seam-c); opacity: 0.55; }
      .carsvg__mirror { fill: url(#bodyGrad); stroke: var(--edge); stroke-width: 0.6; }
      .carsvg__chrome { fill: var(--chrome); }
      .carsvg__kidney { fill: #0c0f13; }
      .carsvg__kbar { stroke: var(--chrome); stroke-width: 0.5; opacity: 0.55; }
      .carsvg__light { fill: var(--secondary-text-color); opacity: 0.7; }
      .carsvg__tail { fill: #c0392b; opacity: 0.82; }
      .carsvg__wheel { fill: var(--tire); stroke-width: 3.4; stroke-linejoin: round; }

      /* closures / security diagram */
      .closcar { padding: 10px 12px 4px; display: flex; justify-content: center; }
      .closcar .carsvg { width: 50%; min-width: 138px; max-width: 196px; margin: 0; }
      .cldiag__hit { fill: transparent; cursor: pointer; }
      .cldiag__hit:hover { fill: rgba(127, 127, 127, 0.14); }
      .cldiag__flap { stroke-width: 1.2; opacity: 0.92; stroke-linejoin: round; }
      .cldiag__zone { opacity: 0.42; pointer-events: none; }
      .cldiag__glass-open { opacity: 0.7; pointer-events: none; }
      .cldiag__lock { cursor: pointer; }
      .cldiag__lockbody { fill: var(--c); }
      .cldiag__shackle { fill: none; stroke: var(--c); stroke-width: 2.2; stroke-linecap: round; }
      .item__dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 8px; vertical-align: middle; }

      .wlabel {
        position: absolute;
        width: 29%;
        display: flex; flex-direction: column; gap: 1px;
        padding: 4px 2px;
        border-radius: 10px;
        transition: background 0.14s ease;
      }
      .wlabel:hover { background: var(--secondary-background-color); }
      .wlabel--fl { top: 16%; left: 3%; align-items: flex-end; text-align: right; }
      .wlabel--fr { top: 16%; right: 3%; align-items: flex-start; text-align: left; }
      .wlabel--rl { bottom: 14%; left: 3%; align-items: flex-end; text-align: right; }
      .wlabel--rr { bottom: 14%; right: 3%; align-items: flex-start; text-align: left; }
      .wlabel__pos {
        display: inline-flex; align-items: center; gap: 6px;
        font-size: 0.66rem; letter-spacing: 0.05em; text-transform: uppercase;
        color: var(--secondary-text-color);
      }
      .wlabel--fl .wlabel__pos, .wlabel--rl .wlabel__pos { flex-direction: row-reverse; }
      .wlabel__badge { color: var(--c); font-weight: 700; white-space: nowrap; }
      .wlabel__val {
        font-size: 1.55rem; font-weight: 600; line-height: 1.05;
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
        font-variant-numeric: tabular-nums; white-space: nowrap;
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
defineCardElement("bmw-cardata-card", BmwCardataCard);

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
// The `view:` values that are layouts in their own right rather than clusters.
const VIEW_MODES = new Set([CHARGING_VIEW, TRIPS_VIEW, HEALTH_VIEW]);

class BmwCardataCardEditor extends HTMLElement {
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
      { value: HEALTH_VIEW, label: t(this._hass, "bh_title") },
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
    const schema = [
      { name: "device", selector: { device: { integration: "bavariandata" } } },
      { name: "cluster", selector: { select: { mode: "dropdown", options: clusterOptions } } },
      { name: "title", selector: { text: {} } },
    ];
    if (overview) {
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
            ],
          },
        ],
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
    this._form.data = { ...this._config, cluster: mode };
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
    // Drop empties so the stored config stays minimal.
    for (const key of Object.keys(value)) {
      if (value[key] === "" || value[key] === undefined || value[key] === null) {
        delete value[key];
      }
    }
    delete value.type;
    const config = { type: this._config.type || "custom:bmw-cardata-card", ...value };
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

defineCardElement("bmw-cardata-card-editor", BmwCardataCardEditor);

window.customCards = window.customCards || [];
// Only advertise the card once — a second evaluation would otherwise add a
// duplicate entry to the card picker.
if (!window.customCards.some((c) => c.type === "bmw-cardata-card")) {
  window.customCards.push({
    type: "bmw-cardata-card",
    name: "BMW CarData Card",
    description: "Vehicle render, state of charge and per-cluster data for BMW CarData.",
    preview: true,
    documentationURL: "https://github.com/JustChr/BavarianData",
  });
}

// eslint-disable-next-line no-console
console.info(
  `%c BMW-CARDATA-CARD %c ${CARD_VERSION} `,
  "color:#fff;background:#2f80ed;border-radius:3px 0 0 3px;padding:2px 4px;",
  "color:#2f80ed;background:#0b0f14;border-radius:0 3px 3px 0;padding:2px 4px;"
);
