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
 *
 * This file is the card's core: shared helpers, entity picking, units, render
 * dispatch and the editor. The views live beside it, one file per group
 * (bavariandata-card-<part>.js, listed in CARD_PARTS), each mixed into the
 * card's class; the strings are bavariandata-card-translations.js.
 */

const CARD_VERSION = "1.16.0";
// How long a view waits before asking a service again after it failed. Asked
// again on the next paint instead, a refused call (an account reloading, for
// one) repeated itself hundreds of times a second (#277).
const FETCH_RETRY_MS = 30000;

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
// The ready-made overview tiles a `tiles:` list may name, in the editor's
// order (`_overviewTile` draws each). Anything else with a dot is an entity.
const OVERVIEW_TILES = [
  "odometer",
  "tank",
  "total_range",
  "target",
  "plug",
  "time_to_full",
  "tires",
  "location",
  "lock",
  "distance_month",
  "battery_12v",
];
// Each ready-made tile's name, as the grid and the editor show it.
const TILE_LABELS = {
  odometer: "odometer",
  tank: "tank",
  total_range: "total_range",
  target: "target",
  plug: "plug",
  time_to_full: "time_to_full",
  tires: "tires",
  location: "ti_location",
  lock: "ti_lock",
  distance_month: "ti_distance_month",
  battery_12v: "ti_battery_12v",
};
// Tiles every car can fill whatever drives it: the default grid of a car
// without charging tiles.
const NEUTRAL_TILES = ["tires", "location", "distance_month"];
// An entity tile's icon where the entity names none (icons.json icons never
// reach the state), by device class.
const DEVICE_CLASS_ICONS = {
  battery: "mdi:battery",
  distance: "mdi:map-marker-distance",
  duration: "mdi:timer-outline",
  energy: "mdi:lightning-bolt",
  power: "mdi:flash",
  pressure: "mdi:gauge",
  speed: "mdi:speedometer",
  temperature: "mdi:thermometer",
  timestamp: "mdi:clock-outline",
  voltage: "mdi:car-battery",
  door: "mdi:car-door",
  lock: "mdi:lock",
  window: "mdi:window-closed-variant",
  plug: "mdi:power-plug",
};
// How much a car must have said before its *silence* about a drivetrain counts
// as evidence. A car mid-bootstrap has sent a handful of fields and proves
// nothing; the MINI that prompted this had 28.
const MIN_DESCRIPTORS_TO_JUDGE = 8;
// Where the driver sits, as `_steering` answers it and `steering:` sets it.
const STEERING_SIDES = new Set(["left", "right"]);
// BMW's two odometer spellings (the coordinator's DESC_ODOMETER); whichever
// the car streams carries the distance unit its owner chose.
const DESC_ODOMETER = ["vehicle.vehicle.mileage", "vehicle.vehicle.travelledDistance"];
// The tank, in litres or as a fill level.
const FUEL_DESCRIPTORS = [
  "vehicle.drivetrain.fuelSystem.remainingFuel",
  "vehicle.drivetrain.fuelSystem.level",
];
// The car's own display setting for distances (units.DESC_DISPLAY_DISTANCE).
const DESC_DISPLAY_DISTANCE = [
  "vehicle.cabin.infotainment.displayUnit.distance",
  "vehicle.cabin.infotainment.hmi.distanceUnit",
];
const KM_PER_MILE = 1.609344;

/** "mi", "km" or null, from any of the spellings BMW and HA use. */
function _miOrKm(unit) {
  if (typeof unit !== "string") return null;
  const u = unit.trim().toLowerCase();
  if (["mi", "mile", "miles"].includes(u)) return "mi";
  if (["km", "kilometer", "kilometers", "kilometre", "kilometres"].includes(u)) return "km";
  return null;
}

/** "l", "l_mpg_uk" or "gal_us": how fuel and its consumption are shown. The
 * same rule as the integration's `units.fuel_unit`: an explicit choice wins;
 * Automatic means US gallons on US customary, imperial mpg (bought in litres)
 * for a car in miles on a metric install, and litres otherwise. */
function _fuelUnitRule(choice, distance, systemLength) {
  if (["l", "l_mpg_uk", "gal_us"].includes(choice)) return choice;
  if (_miOrKm(systemLength) === "mi") return "gal_us";
  return distance === "mi" ? "l_mpg_uk" : "l";
}

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
 * The tables live next door, in bavariandata-card-translations.js. They are *
 * imported with this file's own `?v=`, so a browser never pairs a new card  *
 * with strings it cached from an old release. Should they fail to load, the *
 * card still draws, with each string's key in its place.                    *
 * ------------------------------------------------------------------------- */
// translations-import-start (tests/card_source.py cuts this block out)
let TRANSLATIONS = { en: {} };
try {
  // The URL is this file's own, with the file name swapped: nothing from outside.
  // eslint-disable-next-line no-unsanitized/method
  ({ TRANSLATIONS } = await import(
    new URL(`./bavariandata-card-translations.js${new URL(import.meta.url).search}`, import.meta.url)
  ));
} catch (err) {
  // eslint-disable-next-line no-console -- the one clue a blank card leaves
  console.error("BavarianData card: could not load its translations", err);
}
// translations-import-end

// The card class comes in parts, one file each beside this one, mixed in below.
// Each is a function (Base, SHARED) => class extends Base, so a part reaches
// the card's own helpers through SHARED rather than a static import: a static
// import would drop the ?v= this file was loaded with, and a browser could run a
// new card against an old part after an update.
const CARD_PARTS = ["overview", "clusters", "charging", "trips", "map", "stats", "car", "styles"];
// parts-import-start (tests/card_source.py swaps this block for the parts themselves)
const PART_FACTORIES = await Promise.all(
  CARD_PARTS.map((part) =>
    // The URL is this file's own, with the file name swapped: nothing from outside.
    // eslint-disable-next-line no-unsanitized/method
    import(new URL(`./bavariandata-card-${part}.js${new URL(import.meta.url).search}`, import.meta.url)).then(
      (module) => module.default
    )
  )
).catch((err) => {
  // eslint-disable-next-line no-console -- the one clue a missing card leaves
  console.error("BavarianData card: could not load its parts", err);
  throw err;
});
// parts-import-end

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
// How far the car is driven, and its mileage allowance.
const MILEAGE_VIEW = "mileage";
// Refuels and fuel consumption, for a car with an engine.
const FUEL_VIEW = "fuel";

const VIEW_MODES = new Set([
  CHARGING_VIEW,
  TRIPS_VIEW,
  MAP_VIEW,
  HEALTH_VIEW,
  EFFICIENCY_VIEW,
  MILEAGE_VIEW,
  FUEL_VIEW,
  EQUIPMENT_VIEW,
]);

// What the parts take from this file. Each part names the ones it uses.
const SHARED = {
  CAR,
  CAR_FLIP,
  CHARGING_ACTIVE_STATES,
  CLUSTER_ICONS,
  DESC_ODOMETER,
  DEVICE_CLASS_ICONS,
  FUEL_DESCRIPTORS,
  KM_PER_MILE,
  NEUTRAL_TILES,
  NOT_CHARGING_STATES,
  TRIP_CLASS_COLORS,
  UNAVAILABLE,
  _lang,
  _numberLocale,
  carTones,
};

const CardParts = PART_FACTORIES.reduce((Base, part) => part(Base, SHARED), HTMLElement);

class BavarianDataCard extends CardParts {
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
    if (this._config && this._config.view === "mileage") return 8;
    if (this._config && this._config.view === "fuel") return 8;
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
   * diagnostics only, so a card bound to it has nothing to show. The owning
   * entry is read under every name Home Assistant has used (see
   * `_deviceEntry`): `config_entries` goes away in 2027.10.
   */
  static _isVehicleDevice(dev) {
    if (!dev) return false;
    const entries = new Set(Array.isArray(dev.config_entries) ? dev.config_entries : []);
    if (dev.config_entry_id) entries.add(dev.config_entry_id);
    if (dev.primary_config_entry) entries.add(dev.primary_config_entry);
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
   * decides where it uses a spelling we have seen ("BEV", "PHEV", "CONV"). A
   * car that has sent neither yet keeps the electric layout this card has
   * always shown. */
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
    // BMW's basic data, where it has a spelling: "PHEV" on an X3 30e (#25),
    // "CONV" on a petrol MINI that streams no fuel system at all (#100), which
    // otherwise sat in the "unknown" layout for good.
    if (basic === "PHEV") return "phev";
    if (basic === "CONV") return "ice";
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
  _distance(km, unit = "km", digits = 0) {
    if (km == null) return "—";
    const n = Number(km);
    if (!Number.isFinite(n)) return "—";
    return unit === "mi"
      ? `${this._dec(n / KM_PER_MILE, digits)} mi`
      : `${this._dec(n, digits)} km`;
  }

  /** A distance in `unit`, as a bare number for a tile that sets the unit apart. */
  _inUnit(km, unit) {
    const n = Number(km);
    return unit === "mi" ? n / KM_PER_MILE : n;
  }

  /** A per-100 km figure per 100 of `unit`: more per longer unit. */
  _perDist(value, unit) {
    const n = Number(value);
    return unit === "mi" ? n * KM_PER_MILE : n;
  }

  /** "kWh/100 km" or "kWh/100 mi". */
  _kwhPer(unit) {
    return `kWh/${this._per100(unit)}`;
  }

  _per100(unit) {
    return unit === "mi" ? "100 mi" : "100 km";
  }

  /** Fuel consumption recorded in l/100 km, shown the way `fuel` says. */
  _fuelRate(lPer100km, fuel) {
    const n = Number(lPer100km);
    if (lPer100km == null || !Number.isFinite(n)) return "—";
    if (fuel !== "l_mpg_uk" && fuel !== "gal_us") return `${this._dec(n, 1)} l/100 km`;
    if (n <= 0) return "—";
    // Miles per gallon: imperial (4.546 L) or US (3.785 L) gallons.
    const k = fuel === "gal_us" ? 235.215 : 282.481;
    return `${this._dec(k / n, 1)} mpg`;
  }

  /** A volume recorded in litres, in US gallons where `fuel` says so. */
  _fuelVolume(litres, fuel, digits = 0) {
    const n = Number(litres);
    return fuel === "gal_us"
      ? `${this._dec(n / 3.785411784, digits)} gal`
      : `${this._dec(n, digits)} L`;
  }

  /** "mi" or "km": the unit this car's distances are shown in. The same rule
   * as the integration's `units.distance_unit` (issue #126), in the same order:
   *
   * 1. the units the car's distance sensors are displayed in, odometer first,
   *    because a UK install typically keeps the metric unit system and
   *    switches the distance sensors to miles one by one;
   * 2. the car's own display setting, when the car streams it;
   * 3. Home Assistant's unit system. */
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
    for (const d of DESC_DISPLAY_DISTANCE) {
      const st = this._byDescriptor(entities, d);
      if (st) units.push(st.state);
    }
    for (const u of units) {
      const unit = _miOrKm(u);
      if (unit) return unit;
    }
    const system = this._hass && this._hass.config && this._hass.config.unit_system;
    return system && system.length === "mi" ? "mi" : "km";
  }

  /** The account's Units setting (Configure → Units), as the integration
   * publishes it on the account's connection sensor: `{ distance, fuel, … }`,
   * each "auto" or a unit. Empty when that sensor is missing or disabled, which
   * reads as Automatic everywhere. */
  _unitChoices(deviceId) {
    const units = this._accountAttribute(deviceId, "display_units");
    return units && typeof units === "object" ? units : {};
  }

  /** An attribute the integration publishes on the car's account device (its
   * connection sensor), or `undefined` when that sensor is missing or disabled.
   * The account's own sensors stay available while the cars' entities do not. */
  _accountAttribute(deviceId, name) {
    const hass = this._hass;
    const entry = deviceId && hass ? this._deviceEntry(deviceId) : null;
    if (!entry || !hass.entities || !hass.devices || !hass.states) return undefined;
    for (const [entityId, ent] of Object.entries(hass.entities)) {
      if (!ent || ent.platform !== "bavariandata" || !ent.device_id) continue;
      const dev = hass.devices[ent.device_id];
      const account = (dev && dev.identifiers) || [];
      if (!account.some((pair) => pair && pair[0] === "bavariandata" && pair[1] === entry)) continue;
      const st = hass.states[entityId];
      if (st && st.attributes && name in st.attributes) return st.attributes[name];
    }
    return undefined;
  }

  /** "mi" or "km" for a car: the Units setting when it names one, otherwise
   * `_lengthUnit`'s automatic rule (the integration's `car_distance_unit`). */
  _distUnit(deviceId, entities) {
    const choice = this._unitChoices(deviceId).distance;
    if (choice === "km" || choice === "mi") return choice;
    return this._lengthUnit(entities || this._deviceEntities(deviceId));
  }

  /** "l", "l_mpg_uk" or "gal_us" for a car (the integration's `car_fuel_unit`). */
  _fuelUnit(deviceId, entities) {
    const system = this._hass && this._hass.config && this._hass.config.unit_system;
    return _fuelUnitRule(
      this._unitChoices(deviceId).fuel,
      this._distUnit(deviceId, entities),
      system && system.length
    );
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
    // BMW refuses the account's login: the car's entities are unavailable and
    // carry no attributes, so every view would be empty without saying why.
    if (this._accountAttribute(deviceId, "login_refused") === true) {
      this._renderMessage(this._t("refused_title"), this._t("refused_body"));
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
    } else if (this._config.view === "mileage") {
      this._renderMileage(deviceId, entities);
    } else if (this._config.view === "fuel") {
      this._renderFuel(deviceId, entities);
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
  /** Whether a view's cached answer (`cache[field]`) has to be asked for again.
   * A failure is asked again only once `FETCH_RETRY_MS` has passed. */
  _wantsFetch(cache, field) {
    if (cache[field] || cache.loading) return false;
    return !cache.failedAt || Date.now() - cache.failedAt >= FETCH_RETRY_MS;
  }

  /** Paint again once a failed call may be retried; one timer for all views. */
  _retryLater() {
    if (this._retryTimer) return;
    this._retryTimer = setTimeout(() => {
      this._retryTimer = null;
      this._render();
    }, FETCH_RETRY_MS);
  }

  _withEntry(data, entryId) {
    return entryId ? { ...data, entry_id: entryId } : data;
  }

  _deviceName(deviceId) {
    const dev = this._hass.devices && this._hass.devices[deviceId];
    return (dev && (dev.name_by_user || dev.name)) || "BMW";
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
      { value: MILEAGE_VIEW, label: t(this._hass, "mi_title") },
      { value: FUEL_VIEW, label: t(this._hass, "fu_title") },
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
      // The grid's tiles: ready-made ones first, then this car's entities. A
      // list written in YAML with `{entity, name, icon}` items is left to YAML,
      // since the picker can only hold plain values.
      const tiles = this._config && this._config.tiles;
      if (!Array.isArray(tiles) || tiles.every((item) => typeof item === "string")) {
        schema.push({
          name: "tiles",
          selector: {
            select: {
              multiple: true,
              reorder: true,
              custom_value: true,
              mode: "dropdown",
              options: [
                ...OVERVIEW_TILES.map((value) => ({ value, label: t(this._hass, TILE_LABELS[value]) })),
                ...this._carEntityOptions(),
              ],
            },
          },
        });
      }
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
    // The equipment view can drop the option pictures for a plain list.
    if (this._config && this._config.view === EQUIPMENT_VIEW) {
      schema.push({ name: "pictures", selector: { boolean: {} } });
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

  /** The chosen car's entities, by name, for the tiles picker. */
  _carEntityOptions() {
    const device = this._config && this._config.device;
    const registry = (this._hass && this._hass.entities) || {};
    if (!device) return [];
    return Object.values(registry)
      .filter((e) => e.device_id === device && !e.hidden)
      .map((e) => {
        const st = this._hass.states[e.entity_id];
        return { value: e.entity_id, label: (st && st.attributes.friendly_name) || e.entity_id };
      })
      .sort((a, b) => a.label.localeCompare(b.label));
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
      pictures: this._config.pictures !== false,
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
    // Pictures on is the default; only `false` is worth storing, and only for
    // the view that has them.
    if (value.pictures !== false || value.view !== EQUIPMENT_VIEW) delete value.pictures;
    // No tiles is the drivetrain's own grid, not an empty one.
    if (Array.isArray(value.tiles) && !value.tiles.length) delete value.tiles;
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
