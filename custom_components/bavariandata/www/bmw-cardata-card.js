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

const CARD_VERSION = "1.1.0";

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
      "Overview shows the hero image and key metrics. A cluster shows every value of that group as a list.",
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
      "Die Übersicht zeigt das Fahrzeugbild und Kennzahlen. Ein Cluster listet alle Werte dieser Gruppe auf.",
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
    if (this._config.cluster === "tire") {
      this._renderTires(deviceId, entities);
    } else if (this._config.cluster) {
      this._renderCluster(deviceId, entities);
    } else {
      this._renderOverview(deviceId, entities);
    }
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
    // Top-down symbolic car. Wheels are drawn first so the body overlaps their
    // inner edge (looks mounted); each wheel is stroked in its status colour.
    const wheel = (x, y, color) =>
      `<rect x="${x}" y="${y}" width="16" height="38" rx="7" class="carsvg__wheel" style="stroke:${color}"/>`;
    return `
      <svg class="carsvg" viewBox="0 0 140 214" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        ${wheel(15, 40, c.fl)}
        ${wheel(109, 40, c.fr)}
        ${wheel(15, 150, c.rl)}
        ${wheel(109, 150, c.rr)}
        <rect x="30" y="6" width="80" height="202" rx="30" class="carsvg__body"/>
        <path d="M46 56 H94 L88 72 H52 Z" class="carsvg__glass"/>
        <rect x="50" y="72" width="40" height="66" rx="9" class="carsvg__roof"/>
        <path d="M52 138 H88 L94 156 H46 Z" class="carsvg__glass"/>
        <path d="M70 16 L58 30 H82 Z" class="carsvg__hood"/>
      </svg>`;
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
      .chead ha-icon { --mdc-icon-size: 26px; color: var(--bmw-charge); }
      .chead__text { display: flex; flex-direction: column; }
      .chead__title { font-size: 1.05rem; font-weight: 600; color: var(--primary-text-color); }
      .chead__sub { font-size: 0.74rem; color: var(--secondary-text-color); }
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
        width: 42%;
        min-width: 116px;
        max-width: 168px;
        height: auto;
        margin: 4px auto 0;
        overflow: visible;
      }
      .carsvg__body { fill: var(--secondary-background-color); stroke: var(--divider-color); stroke-width: 1.5; }
      .carsvg__roof { fill: var(--card-background-color); }
      .carsvg__glass { fill: var(--divider-color); opacity: 0.7; }
      .carsvg__hood { fill: var(--secondary-text-color); opacity: 0.35; }
      .carsvg__wheel { fill: #15181d; stroke-width: 4; }

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

// Guard against double-definition: the script can legitimately be evaluated more
// than once (e.g. auto-registered by the integration *and* still present as a
// leftover manual Lovelace resource from an earlier version). A bare
// customElements.define() would throw "the name has already been used" on the
// second run, aborting the rest of the module and leaving the card broken —
// which shows up as a "config error" on placed cards and an endless spinner in
// the card picker. Defining only when absent keeps a double-load harmless.
if (!customElements.get("bmw-cardata-card")) {
  customElements.define("bmw-cardata-card", BmwCardataCard);
}

/* ------------------------------------------------------------------------- *
 * Visual editor (config-changed via ha-form)                                *
 * ------------------------------------------------------------------------- */

// Sentinel for "no cluster" so the dropdown always has a concrete value.
const OVERVIEW = "overview";

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
      ...CLUSTER_SLUGS.map((slug) => ({
        value: slug,
        label: t(this._hass, "cl_" + slug, null, slug),
      })),
    ];
    const entitySel = (domain) => ({
      entity: { integration: "bavariandata", ...(domain ? { domain } : {}) },
    });
    const overview = !this._config || !this._config.cluster;
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
    // Present a concrete cluster value so the dropdown reflects the mode.
    this._form.data = { cluster: OVERVIEW, ...this._config };
  }

  _valueChanged(ev) {
    ev.stopPropagation();
    if (!this._config) return;
    const value = { ...ev.detail.value };
    if (value.cluster === OVERVIEW || !value.cluster) delete value.cluster;
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

if (!customElements.get("bmw-cardata-card-editor")) {
  customElements.define("bmw-cardata-card-editor", BmwCardataCardEditor);
}

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
