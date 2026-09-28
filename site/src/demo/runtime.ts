// Runs the shipped BavarianData card, unmodified, outside Home Assistant.
//
// The card only needs four things from its host: `ha-card`, `ha-icon`,
// `ha-map` and a `hass` object. This file provides small stand-ins for each and
// feeds them one real car (site/src/demo/i5.json, scrubbed -- see
// site/scripts/capture_demo.py). Service calls the card makes are answered from
// that capture, the way the integration's own services would.
//
// Every timestamp in the capture is moved forward, so the newest reading is
// always "a moment ago" and the latest charge and trip land in the current
// month. The history moves by whole days, so it keeps its time of day.

import assets from "../generated/assets.json";

type Lang = "en" | "de";
type State = { entity_id: string; state: string; attributes: Record<string, any>; last_changed: string; last_updated: string };
type Trip = Record<string, any>;
type Demo = {
  captured: string;
  device: Record<string, any>;
  entities: Record<string, Record<string, any>>;
  states: Record<string, Omit<State, "entity_id">>;
  services: Record<string, any>;
  strings: Record<Lang, { names: Record<string, string>; states: Record<string, Record<string, string>> }>;
};

const ICONS: Record<string, string> = assets.icons;

/* ---- the three HA elements the card uses --------------------------------- */

class DemoIcon extends HTMLElement {
  static observedAttributes = ["icon"];
  #root = this.attachShadow({ mode: "open" });
  set icon(value: string) {
    this.setAttribute("icon", value);
  }
  get icon() {
    return this.getAttribute("icon") || "";
  }
  connectedCallback() {
    this.#draw();
  }
  attributeChangedCallback() {
    this.#draw();
  }
  #draw() {
    const path = ICONS[this.icon] || "";
    this.#root.innerHTML = `<style>:host{display:inline-flex;align-items:center;justify-content:center;vertical-align:middle;fill:currentColor;width:var(--mdc-icon-size,24px);height:var(--mdc-icon-size,24px)}svg{width:100%;height:100%;display:block}</style><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${path}"/></svg>`;
  }
}

class DemoCard extends HTMLElement {
  static observedAttributes = ["header"];
  #root = this.attachShadow({ mode: "open" });
  connectedCallback() {
    this.#draw();
  }
  attributeChangedCallback() {
    this.#draw();
  }
  #draw() {
    const header = this.getAttribute("header");
    this.#root.innerHTML = `<style>
      :host{display:block;position:relative;background:var(--ha-card-background,var(--card-background-color));color:var(--primary-text-color);border-radius:var(--ha-card-border-radius,12px);border:1px solid var(--ha-card-border-color,var(--divider-color));box-shadow:var(--ha-card-box-shadow,none);transition:none}
      .card-header{font-size:1.4rem;font-weight:500;padding:20px 16px 12px;line-height:1.2}
    </style>${header ? `<h1 class="card-header">${header.replace(/</g, "&lt;")}</h1>` : ""}<slot></slot>`;
  }
}

// Leaflet and its cluster plugin load only when a map view is first opened.
let leaflet: Promise<any> | null = null;
async function loadLeaflet() {
  leaflet ??= (async () => {
    const L = (await import("leaflet")).default;
    (window as any).L = L;
    await import("leaflet.markercluster");
    const [css, cluster, clusterDefault] = await Promise.all([
      import("leaflet/dist/leaflet.css?inline"),
      import("leaflet.markercluster/dist/MarkerCluster.css?inline"),
      import("leaflet.markercluster/dist/MarkerCluster.Default.css?inline"),
    ]);
    return { L, css: css.default + cluster.default + clusterDefault.default };
  })();
  return leaflet;
}

const isDark = () =>
  document.documentElement.dataset.theme === "dark" ||
  (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);

class DemoMap extends HTMLElement {
  autoFit = false;
  zoom = 13;
  themeMode = "auto";
  hass: unknown;
  leafletMap: any;
  Leaflet: any;
  #root = this.attachShadow({ mode: "open" });
  async connectedCallback() {
    const { L, css } = await loadLeaflet();
    if (!this.isConnected || this.leafletMap) return;
    this.#root.innerHTML = `<style>${css}:host{display:block;position:relative}#m{height:100%;min-height:inherit;border-radius:inherit;background:var(--secondary-background-color)}#m.dark .leaflet-tile-pane{filter:grayscale(.85) invert(1) hue-rotate(200deg) brightness(.85) contrast(.9)}</style><div id="m"></div>`;
    const div = this.#root.getElementById("m")!;
    div.style.height = this.style.height || "100%";
    const map = L.map(div, { zoomControl: true, attributionControl: true }).setView([48.1642, 11.5864], this.zoom);
    // OpenStreetMap's own tiles (CARTO's now need an API key). Dark mode is a
    // filter on the tile pane, not a second tile set.
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);
    if (isDark()) div.classList.add("dark");
    this.Leaflet = L;
    this.leafletMap = map;
  }
  disconnectedCallback() {
    this.leafletMap?.remove();
    this.leafletMap = undefined;
  }
}

function defineOnce(tag: string, cls: CustomElementConstructor) {
  if (!customElements.get(tag)) customElements.define(tag, cls);
}
defineOnce("ha-icon", DemoIcon);
defineOnce("ha-card", DemoCard);
defineOnce("ha-map", DemoMap);

/* ---- the capture, moved to now ------------------------------------------- */

const ISO = /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})/g;

async function loadDemo(src: string): Promise<{ demo: Demo; shift: number }> {
  const text = await (await fetch(src)).text();
  // Anchor on the car's newest reading, so the freshest value reads as current.
  const raw: Demo = JSON.parse(text);
  // BMW's own reading time (the `timestamp` attribute) is what the card ages.
  const newest = Math.max(
    ...Object.values(raw.states)
      .map((s) => Date.parse(s.attributes.timestamp))
      .filter(Number.isFinite)
  );
  const now = Date.now();
  const exact = now - newest - 2 * 60 * 1000;
  // The history (trips, charges) moves by whole days only, so it keeps its
  // time of day: a charge on surplus sun stays at midday, a night-tariff charge
  // stays at night.
  const DAY = 24 * 60 * 60 * 1000;
  const shift = Math.floor(exact / DAY) * DAY;
  // Anything the integration derived after that reading would land in the
  // future; it happened "just now" instead.
  const move = (json: string, by: number) =>
    json.replace(ISO, (s) => new Date(Math.min(Date.parse(s) + by, now)).toISOString());
  const { services, ...live } = raw;
  const demo: Demo = JSON.parse(move(JSON.stringify(live), exact));
  demo.services = JSON.parse(move(JSON.stringify(services), shift));
  return { demo, shift };
}

const shiftMonth = (key: string, shift: number) => {
  const d = new Date(Date.parse(`${key}-15T12:00:00Z`) + shift);
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
};
const localDay = (iso: string) => {
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const inRange = (iso: string, from?: string, to?: string) => {
  const day = localDay(iso);
  return (!from || day >= from) && (!to || day <= to);
};
const monthOf = (iso: string) => localDay(iso).slice(0, 7);

/* ---- hass ------------------------------------------------------------------- */

const BINARY: Record<Lang, Record<string, [string, string]>> = {
  en: { opening: ["Closed", "Open"], door: ["Closed", "Open"], window: ["Closed", "Open"], lock: ["Locked", "Unlocked"], default: ["Off", "On"] },
  de: { opening: ["Geschlossen", "Offen"], door: ["Geschlossen", "Offen"], window: ["Geschlossen", "Offen"], lock: ["Verriegelt", "Entriegelt"], default: ["Aus", "An"] },
};
const TRACKER: Record<Lang, Record<string, string>> = {
  en: { home: "Home", not_home: "Away" },
  de: { home: "Zuhause", not_home: "Abwesend" },
};
const UNAVAILABLE: Record<Lang, Record<string, string>> = {
  en: { unknown: "Unknown", unavailable: "Unavailable" },
  de: { unknown: "Unbekannt", unavailable: "Nicht verfügbar" },
};

function summarize(trips: Trip[], month: string, previous: Trip[]) {
  const total = trips.reduce((s, t) => s + (t.distance_km || 0), 0);
  const km = (cls: string) => trips.filter((t) => (t.classification || "unclassified") === cls).reduce((s, t) => s + (t.distance_km || 0), 0);
  const pct = (v: number) => (total ? Math.round((v / total) * 1000) / 10 : 0);
  const split = {
    business_km: km("business"),
    private_km: km("private"),
    commute_km: km("commute"),
    unclassified_km: km("unclassified"),
  };
  const rated = trips.filter((t) => typeof t.consumption_kwh_per_100km === "number" && t.distance_km >= 5);
  const by = (a: Trip, b: Trip) => a.consumption_kwh_per_100km - b.consumption_kwh_per_100km;
  const pick = (t?: Trip) =>
    t ? { id: `${t.vin}-${t.start}`, label: t.end_place?.label, consumption: t.consumption_kwh_per_100km, distance_km: t.distance_km } : null;
  const energy = trips.reduce((s, t) => s + (t.energy_kwh || 0), 0);
  const dest = new Map<string, number>();
  for (const t of trips) {
    const label = t.end_place?.label;
    if (label) dest.set(label, (dest.get(label) || 0) + 1);
  }
  const prevTotal = previous.reduce((s, t) => s + (t.distance_km || 0), 0);
  const round = (v: number) => Math.round(v * 10) / 10;
  return {
    month,
    summary: {
      total_km: round(total),
      trip_count: trips.length,
      avg_trip_km: trips.length ? round(total / trips.length) : 0,
      split: {
        ...Object.fromEntries(Object.entries(split).map(([k, v]) => [k, round(v)])),
        business_percent: pct(split.business_km),
        private_percent: pct(split.private_km),
        commute_percent: pct(split.commute_km),
      },
      avg_consumption_kwh_per_100km: total ? round((energy / total) * 100) : null,
      energy_balance: null,
      best_trip: pick([...rated].sort(by)[0]),
      worst_trip: pick([...rated].sort(by).at(-1)),
      recuperation_kwh_per_100km: null,
      style_score: null,
      style_trend: [],
      top_destinations: [...dest.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3).map(([label, count]) => ({ label, count })),
      longest_trip_km: trips.reduce((m, t) => Math.max(m, t.distance_km || 0), 0),
      prev_total_km: round(prevTotal),
      mom_delta_km: round(total - prevTotal),
      mom_delta_percent: prevTotal ? round(((total - prevTotal) / prevTotal) * 100) : null,
      estimated_cost: null,
    },
  };
}

function csv(rows: Record<string, unknown>[]) {
  if (!rows.length) return "";
  const cols = Object.keys(rows[0]);
  const cell = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [cols.join(","), ...rows.map((r) => cols.map((c) => cell(r[c])).join(","))].join("\n");
}

export function makeHass(demo: Demo, shift: number, lang: Lang, image?: string) {
  const strings = demo.strings[lang];
  const deviceName = demo.device.name;
  const states: Record<string, State> = {};
  for (const [id, st] of Object.entries(demo.states)) {
    const name = strings.names[id];
    states[id] = {
      entity_id: id,
      ...st,
      attributes: { ...st.attributes, friendly_name: name ? `${deviceName} ${name}` : deviceName },
    };
    if (image && st.attributes.entity_picture) states[id].attributes.entity_picture = image;
  }
  const trips: Trip[] = demo.services.get_trips.trips;
  const sessions: Trip[] = demo.services.get_charging_sessions.sessions;
  const efficiency = structuredClone(demo.services.get_efficiency);
  for (const row of efficiency.efficiency?.trend || []) row.month = shiftMonth(row.month, shift);

  const numberLocale = lang === "de" ? "de-DE" : "en-US";
  const blankBeforePercent = lang === "de" ? " " : "";

  const formatEntityState = (st: State) => {
    const domain = st.entity_id.split(".")[0];
    const a = st.attributes || {};
    if (st.state in UNAVAILABLE[lang]) return UNAVAILABLE[lang][st.state];
    if (domain === "binary_sensor") {
      const pair = BINARY[lang][a.device_class] || BINARY[lang].default;
      return st.state === "on" ? pair[1] : pair[0];
    }
    if (domain === "device_tracker") return TRACKER[lang][st.state] || st.state;
    const translated = strings.states[st.entity_id]?.[st.state];
    if (translated) return translated;
    if (a.device_class === "timestamp" || (domain === "image" && !Number.isNaN(Date.parse(st.state)))) {
      return new Intl.DateTimeFormat(numberLocale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(st.state));
    }
    const num = Number(st.state);
    if (st.state !== "" && Number.isFinite(num)) {
      const precision = demo.entities[st.entity_id]?.display_precision;
      const decimals = precision ?? (st.state.split(".")[1] || "").length;
      const value = new Intl.NumberFormat(numberLocale, { minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(num);
      const unit = a.unit_of_measurement;
      if (!unit) return value;
      return unit === "%" ? `${value}${blankBeforePercent}%` : unit.startsWith("°") ? `${value}${unit}` : `${value} ${unit}`;
    }
    return st.state;
  };

  const respond = (response: unknown) => Promise.resolve({ context: { id: "demo" }, response });

  const callService = (domain: string, service: string, data: Record<string, any> = {}) => {
    if (domain !== "bavariandata") return respond(null);
    switch (service) {
      case "get_charging_sessions":
        return respond({ sessions: sessions.filter((s) => inRange(s.start, data.from, data.to)) });
      case "get_trips": {
        const within = trips.filter((t) => inRange(t.start, data.from, data.to));
        return respond({ trips: data.limit ? within.slice(0, data.limit) : within, open_trips: [] });
      }
      case "get_driving_summary": {
        const month = data.month || monthOf(new Date().toISOString());
        const [y, m] = month.split("-").map(Number);
        const prev = `${m === 1 ? y - 1 : y}-${String(m === 1 ? 12 : m - 1).padStart(2, "0")}`;
        return respond(summarize(trips.filter((t) => monthOf(t.start) === month), month, trips.filter((t) => monthOf(t.start) === prev)));
      }
      case "get_efficiency":
        return respond(efficiency);
      case "set_trip_class": {
        const trip = trips.find((t) => `${t.vin}-${t.start}` === data.trip_id);
        if (trip) {
          trip.classification = data.classification;
          trip.classification_source = "manual";
        }
        return respond(null);
      }
      case "export_history": {
        const month = data.month;
        const isTrips = data.type === "trips";
        const rows = isTrips
          ? trips.filter((t) => monthOf(t.start) === month).map((t) => ({
              start: t.start, end: t.end, from: t.start_place?.label, to: t.end_place?.label,
              distance_km: t.distance_km, energy_kwh: t.energy_kwh, classification: t.classification,
            }))
          : sessions.filter((s) => monthOf(s.start) === month).map((s) => ({
              start: s.start, end: s.end, soc_start: s.soc_start, soc_end: s.soc_end, energy_kwh: s.energy_kwh,
              grid_kwh: s.grid_kwh, cost: s.cost?.amount, currency: s.cost?.currency, solar_percent: s.energy_mix?.solar_percent,
            }));
        const content = csv(rows);
        return respond({ files: content ? [{ filename: `bavariandata-demo-${data.type}-${month}.csv`, mime: "text/csv", content, rows: rows.length }] : [] });
      }
      default:
        return respond(null);
    }
  };

  return {
    states,
    entities: Object.fromEntries(Object.entries(demo.entities).map(([id, e]) => [id, { ...e, entity_id: id }])),
    devices: { [demo.device.id]: demo.device },
    language: lang,
    locale: { language: lang, number_format: "language", time_format: "language" },
    themes: { darkMode: isDark() },
    formatEntityState,
    callService,
    callWS: () => Promise.reject(new Error("not available in the demo")),
  };
}

/* ---- mounting --------------------------------------------------------------- */

// One capture and one hass per page, however many cards are on it.
let shared: Promise<{ demo: Demo; hass: ReturnType<typeof makeHass> }> | null = null;
function sharedHass(host: HTMLElement) {
  shared ??= (async () => {
    const [{ demo, shift }] = await Promise.all([loadDemo(host.dataset.src!), loadCard(host.dataset.card!)]);
    return { demo, hass: makeHass(demo, shift, (host.dataset.lang as Lang) || "en", host.dataset.image) };
  })();
  return shared;
}

type CardEl = HTMLElement & { setConfig(c: object): void; hass: unknown };
function createCard(demo: Demo, hass: unknown, config: object): CardEl {
  const card = document.createElement("bavariandata-card") as CardEl;
  card.setConfig({ type: "custom:bavariandata-card", device: demo.device.id, ...config });
  card.hass = hass;
  return card;
}

/** A single card view, e.g. `{ view: "charging" }`, with no tabs. */
export async function mountSingle(host: HTMLElement) {
  const { demo, hass } = await sharedHass(host);
  host.replaceChildren(createCard(demo, hass, JSON.parse(host.dataset.config || "{}")));
  host.dataset.ready = "true";
}

export async function mountDemo(host: HTMLElement) {
  const { demo, hass } = await sharedHass(host);
  const stage = host.querySelector<HTMLElement>("[data-stage]")!;
  const cards = new Map<string, CardEl>();

  const show = (tab: HTMLButtonElement) => {
    const key = tab.dataset.view!;
    host.querySelectorAll<HTMLButtonElement>("[data-view]").forEach((b) => {
      b.setAttribute("aria-selected", String(b === tab));
      b.tabIndex = b === tab ? 0 : -1;
    });
    let card = cards.get(key);
    if (!card) {
      card = createCard(demo, hass, JSON.parse(tab.dataset.config || "{}"));
      cards.set(key, card);
    }
    stage.replaceChildren(card!);
    stage.setAttribute("aria-labelledby", tab.id);
  };

  const tabs = [...host.querySelectorAll<HTMLButtonElement>("[data-view]")];
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => show(tab));
    tab.addEventListener("keydown", (e) => {
      const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (!step) return;
      e.preventDefault();
      const next = tabs[(i + step + tabs.length) % tabs.length];
      next.focus();
      show(next);
    });
  });
  host.dataset.ready = "true";
  show(tabs[0]);
}

function loadCard(src: string) {
  if (customElements.get("bavariandata-card")) return Promise.resolve();
  return new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("card failed to load"));
    document.head.append(script);
  });
}
