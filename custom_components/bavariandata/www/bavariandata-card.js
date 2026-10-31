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

const CARD_VERSION = "1.16.0";

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
    // A car that proved no drivetrain is laid out like a combustion one -- no
    // charging tiles, nothing electric -- except that it has no tank either, so
    // it rings its range and the lead row shows what it does have. So does a
    // combustion car that streams no tank: a petrol MINI sends none (#100).
    const bare = drivetrain === "unknown" || (ice && !fuelId);
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
    const gaugeId = bare ? rangeId : ice ? fuelId : picks.soc;
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
    const gaugeCap = bare
      ? this._t("remaining_range")
      : ice
      ? this._t("fuel")
      : charging
      ? this._t("charging")
      : this._t("charge");

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
      // The grid has room a combustion car's charging tiles used to fill; give
      // it what such a car does stream, each tile only where it has data.
      secondary = [...(litresSt && !bare ? ["odometer"] : []), ...NEUTRAL_TILES];
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
        ...(drivetrain === "phev" ? ["tank", "total_range"] : []),
        "target",
        "plug",
        "time_to_full",
        "odometer",
      ];
    }
    // `tiles:` replaces the grid whole, in the order given.
    if (Array.isArray(cfg.tiles) && cfg.tiles.length) secondary = cfg.tiles;
    const tileCtx = { entities, picks, fuelSt, odometerSt, charging, name: this._deviceName(deviceId) };
    secondary = secondary.map((item) => this._overviewTile(item, tileCtx)).filter((m) => m && m.st);

    // freshest update among the headline entities
    const freshest = [gaugeSt, rangeSt, ice ? null : chargingSt]
      .filter(Boolean)
      .map((s) => s.last_changed)
      .sort()
      .pop();

    if (picks.image) this._heroLoad(picks.image);
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
      sec: secondary.map((s) => [s.label, s.val == null ? s.st.state : s.val, s.icon, s.color]),
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
        <div class="hero ${imgUrl || spinInfo ? "" : "hero--empty"}${heroState && heroState.inset ? " hero--inset" : ""}${spin && spin.variant === "night" ? " hero--night" : ""}">
          ${
            inside
              ? this._panoHtml(pano)
              : spin
                ? this._spinHtml(spin, name)
                : imgUrl
                ? `<img class="hero__img" src="${imgUrl}" alt="${this._esc(name)}" />`
                : `<ha-icon class="hero__placeholder" icon="${ice || drivetrain === "unknown" ? "mdi:car" : "mdi:car-electric"}"></ha-icon>`
          }
          <div class="hero__scrim"></div>
          <div class="hero__top">
            <div class="hero__name" title="${this._esc(name)}">${this._esc(name)}</div>
            ${rel ? `<div class="pill" title="${this._t("last_update")}"><span class="dot ${this._staleClass(freshest)}"></span>${rel}</div>` : ""}
          </div>
          ${this._tripPill(trip, this._distUnit(deviceId, entities))}${heroExtras}
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
                  <button class="cell" data-entity="${this._esc(m.st.entity_id)}">
                    <ha-icon icon="${this._esc(m.icon)}"${m.color ? ` style="color:${m.color}"` : ""}></ha-icon>
                    <div class="cell__body">
                      <span class="cell__val">${m.val == null ? this._fmt(m.st) : m.val}</span>
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
        this._heroSave();
        this._sig = null;
        this._render();
      });
    }
  }

  /** One tile of the overview grid, or null where the car has nothing for it.
   *
   * `item` is a ready-made key (`OVERVIEW_TILES`), an entity id, or
   * `{entity, name, icon}`. A tile carries the state it opens (`st`), and a
   * `val` (already escaped) and `color` where it shows more than that state. */
  _overviewTile(item, ctx) {
    const spec = typeof item === "string" ? (item.includes(".") ? { entity: item } : { tile: item }) : item;
    if (!spec || typeof spec !== "object") return null;
    if (spec.entity) return this._entityTile(spec, ctx.name);
    const { entities, picks, charging } = ctx;
    const byDesc = (d) => this._idByDescriptor(entities, d);
    const tile = (label, st, icon, extra) => ({ label, st, icon, ...extra });
    switch (spec.tile) {
      case "odometer":
        return tile(this._t("odometer"), ctx.odometerSt, "mdi:counter");
      case "tank":
        return tile(this._t("tank"), ctx.fuelSt, "mdi:gas-station");
      case "total_range":
        return tile(this._t("total_range"), this._st(byDesc("vehicle.drivetrain.lastRemainingRange")), "mdi:map-marker-distance");
      case "target":
        return tile(this._t("target"), this._st(picks.target), "mdi:target");
      case "plug": {
        const st = this._st(picks.plug);
        return tile(this._t("plug"), st, charging || this._isPlugged(st) ? "mdi:power-plug" : "mdi:power-plug-off");
      }
      case "time_to_full": {
        // "0 min" outside a charge is no charge time, only the end of the last one.
        const raw = this._st(picks.timeToFull);
        const st = !charging && raw && parseFloat(raw.state) === 0 ? null : raw;
        return tile(charging ? this._t("time_to_full") : this._t("charge_time"), st, "mdi:timer-sand");
      }
      case "tires": {
        // The pressure spread across the wheels, colored like the tire view's
        // header; opens the first wheel's pressure.
        const wheels = {};
        for (const id of entities) {
          const st = this._st(id);
          const a = st && st.attributes;
          if (!a || a.cluster !== "tire" || !a.tire_axle || !a.tire_side) continue;
          const key = `${a.tire_axle}_${a.tire_side}`;
          (wheels[key] = wheels[key] || {})[a.tire_metric || "other"] = st;
        }
        const list = Object.keys(wheels).sort().map((k) => wheels[k]).filter((w) => w.pressure);
        if (!list.length) return null;
        const summary = this._tireSummary(list);
        // The color carries the verdict; an alert icon would cry wolf on a
        // healthy car.
        return tile(this._t("tires"), list[0].pressure, "mdi:tire", {
          val: summary.pressure.t,
          color: summary.overall.c,
        });
      }
      case "location":
        return tile(this._t("ti_location"), this._st(entities.find((id) => id.startsWith("device_tracker."))), "mdi:map-marker");
      case "lock": {
        const m = this._closureModel(entities);
        if (!m.lockId) return null;
        return tile(this._t("ti_lock"), this._st(m.lockId), m.lock.key === "unlocked" ? "mdi:lock-open-variant" : "mdi:lock", {
          val: this._esc(m.lock.label),
          color: m.lock.color,
        });
      }
      case "distance_month":
        return tile(this._t("ti_distance_month"), this._byDescriptor(entities, "driving_distance_month"), "mdi:calendar-month");
      case "battery_12v":
        return tile(
          this._t("ti_battery_12v"),
          this._st(byDesc("vehicle.electricalSystem.battery.stateOfCharge") || byDesc("vehicle.electricalSystem.battery.voltage")),
          "mdi:car-battery"
        );
      default:
        return null;
    }
  }

  /** A tile for any entity: its own name without the car's, its own icon. */
  _entityTile(spec, vehicleName) {
    const st = this._st(spec.entity);
    if (!st) return null;
    const a = st.attributes || {};
    let label = spec.name || a.friendly_name || st.entity_id;
    if (!spec.name && vehicleName && label.startsWith(vehicleName + " ")) {
      label = label.slice(vehicleName.length + 1);
    }
    const domain = st.entity_id.split(".")[0];
    const icon =
      spec.icon ||
      a.icon ||
      DEVICE_CLASS_ICONS[a.device_class] ||
      (domain === "device_tracker" ? "mdi:map-marker" : "mdi:information-outline");
    return { label: this._esc(label), st, icon };
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
  _tripPill(trip, unit) {
    if (!trip) return "";
    const bits = [];
    if (trip.distance_km != null) bits.push(this._distance(trip.distance_km, unit, 1));
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
    let url = this._signedUrl(path);
    if (!url && variant !== "day") {
      // Shown by day until the other sprite is signed, so no night backdrop yet.
      url = this._signedUrl(spin.path);
      variant = "day";
    }
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
   * dragging. */
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
          </div></div>`;
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
      if (start) this._heroSave();
      start = null;
    };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
  }

  /** The hero view this viewer last chose for this picture: the spin's angle,
   * inside or out, and where they looked inside. Kept in the browser, so a
   * reload or another dashboard shows the car as it was left. Read once per
   * image entity; storage that is blocked or empty just means the defaults. */
  _heroLoad(entityId) {
    if (this._heroLoaded === entityId) return;
    this._heroLoaded = entityId;
    let saved;
    try {
      saved = JSON.parse(window.localStorage.getItem(`bavariandata-hero:${entityId}`) || "null");
    } catch (e) {
      return;
    }
    if (!saved || typeof saved !== "object") return;
    if (Number.isInteger(saved.frame)) this._spinFrame = saved.frame;
    if (saved.mode === "interior" || saved.mode === "exterior") this._heroMode = saved.mode;
    if (Number.isFinite(saved.yaw) && Number.isFinite(saved.pitch)) {
      this._panoLook = { yaw: saved.yaw, pitch: Math.max(-80, Math.min(80, saved.pitch)) };
    }
  }

  _heroSave() {
    if (!this._heroLoaded) return;
    const look = this._panoLook || {};
    const saved = {
      frame: Number.isInteger(this._spinFrame) ? this._spinFrame : undefined,
      mode: this._heroMode,
      yaw: look.yaw,
      pitch: look.pitch,
    };
    try {
      window.localStorage.setItem(`bavariandata-hero:${this._heroLoaded}`, JSON.stringify(saved));
    } catch (e) {
      /* private window or blocked storage: the view lasts until the page reloads */
    }
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
      if (startX !== null) this._heroSave();
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

  /** Battery health describes a high-voltage battery. On a petrol or diesel
   * car it would sit empty for good and read like a fault, so say why instead
   * -- before any service is called. (Charging and efficiency show the Fuel
   * view there.) Returns whether it painted. */
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
    // A petrol or diesel car has no charges; its tank is the record.
    if (this._drivetrain(entities) === "ice") {
      this._renderFuel(deviceId, entities);
      return;
    }

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

    // Places is the other tab of this view: where the car charges, over the
    // whole stored history rather than one month (see _renderPlaces).
    if (this._chgTab === "places") {
      this._renderPlaces(deviceId, entities, vin, entryId, trigger);
      return;
    }

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
        ${this._chargingTabs("sessions")}
        ${this._monthNav("charging")}
        ${summary ? this._chargingSummaryBand(summary) : ""}
        ${body}
      </ha-card>
    `;
    this._wireChargingTaps();
    this._wireMonthNav();
  }

  /** The Sessions | Places switch at the top of the charging view. */
  _chargingTabs(active) {
    const tab = (key, label) =>
      `<button class="ctabs__btn${active === key ? " is-active" : ""}" data-chg-tab="${key}"
               aria-pressed="${active === key}">${this._t(label)}</button>`;
    return `<div class="ctabs" role="group">${tab("sessions", "ch_tab_sessions")}${tab(
      "places",
      "ch_tab_places"
    )}</div>`;
  }

  _wireChargingTabs() {
    this.shadowRoot.querySelectorAll("[data-chg-tab]").forEach((el) => {
      el.addEventListener("click", () => {
        const tab = el.getAttribute("data-chg-tab");
        if ((this._chgTab || "sessions") === tab) return;
        this._chgTab = tab;
        this._sig = null;
        this._render();
      });
    });
  }

  /* ---- charging places ---------------------------------------------------
   *
   * The places a car charges at, from `get_charging_places` (worked out from
   * the stored sessions on the server, history/stations.py). Fetched like the
   * sessions: once, then again only when the session sensor changes. A map
   * appears only when places carry coordinates -- which needs the opt-in
   * "Remember charging locations"; without it the list stands alone, with no
   * hint nudging anyone toward storing positions. */
  _renderPlaces(deviceId, entities, vin, entryId, trigger) {
    const cache = this._plc;
    const current = cache && cache.vin === vin && cache.trigger === trigger;
    if (!current || (!cache.data && !cache.loading)) {
      this._plc = {
        vin,
        trigger,
        data: current && cache ? cache.data : null,
        loading: true,
        error: false,
      };
      const req = this._plc;
      this._hass
        .callService(
          "bavariandata",
          "get_charging_places",
          this._withEntry({ vin }, entryId),
          undefined,
          false,
          true
        )
        .then((res) => {
          if (!this._plc || this._plc.vin !== vin || this._plc.trigger !== req.trigger) return;
          const places = (res && res.response && res.response.places) || [];
          this._plc = { ...this._plc, data: places, loading: false, error: false };
          this._render();
        })
        .catch(() => {
          if (!this._plc || this._plc.vin !== vin || this._plc.trigger !== req.trigger) return;
          this._plc = { ...this._plc, loading: false, error: true };
          this._render();
        });
    }
    this._paintPlaces(deviceId);
    // Only a list with positions has a map to load.
    if (this.shadowRoot.querySelector(".pl__map")) {
      this._ensureMapLib();
      this._mountPlacesMap();
    }
  }

  _paintPlaces(deviceId) {
    const name = this._config.title || this._deviceName(deviceId);
    const state = this._plc || {};
    const places = state.data;
    const expanded = this._plcExpanded || null;
    const sig = this._signature({
      m: "plc",
      lang: _lang(this._hass),
      name,
      loading: state.loading && !places,
      error: state.error,
      expanded,
      rows: (places || []).map((p) => [p.id, p.visits, p.last_visit, p.avg_kw]),
    });
    if (sig === this._sig) return;
    this._sig = sig;

    let body;
    if (state.error) {
      body = `<div class="empty">${this._t("pl_error")}</div>`;
    } else if (!places && state.loading) {
      body = `<div class="empty">${this._t("pl_loading")}</div>`;
    } else if (!places || !places.length) {
      body = `<div class="empty">${this._t("pl_empty")}</div>`;
    } else {
      const mapped = places.some((p) => p.latitude != null && p.longitude != null);
      body = `${mapped ? '<div class="pl__map"></div>' : ""}<div class="chg__list">${places
        .map((p) => this._placeRow(p, expanded))
        .join("")}</div>`;
    }
    const count = places ? places.length : 0;
    const countLabel =
      count === 1 ? this._t("pl_place_one") : this._t("pl_place_many", { n: count });

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        <div class="chead">
          <ha-icon icon="mdi:ev-station"></ha-icon>
          <div class="chead__text">
            <span class="chead__title">${this._esc(this._config.title || this._t("ch_title"))}</span>
            <span class="chead__sub">${this._esc(name)}${count ? " · " + countLabel : ""}</span>
          </div>
        </div>
        ${this._chargingTabs("places")}
        ${body}
      </ha-card>
    `;
    this._placesMapEl = null;
    this._wireChargingTabs();
    this.shadowRoot.querySelectorAll("[data-place]").forEach((el) => {
      el.addEventListener("click", () => {
        const id = el.getAttribute("data-place");
        this._plcExpanded = this._plcExpanded === id ? null : id;
        this._sig = null;
        this._paintPlaces(this._resolveDeviceId());
        this._mountPlacesMap();
      });
    });
  }

  /** What a place is called: its zone, else operator and address, else BMW's
   * address, else "Away" with the rounded point so two such rows differ. */
  _placeName(place) {
    if (place.unnamed) return this._t("pl_unnamed");
    if (place.zone) {
      const home = /^home$/i.test(place.zone) || place.zone === this._homeZoneName();
      const label = home ? this._t("ch_home") : place.zone;
      return place.assumed ? `${label} · ${this._t("ch_assumed")}` : label;
    }
    const parts = [place.provider, place.address].filter((v) => v);
    if (parts.length) return parts.join(" · ");
    if (place.latitude != null && place.longitude != null) {
      return `${this._t("ch_public")} (${this._dec(place.latitude, 3)}, ${this._dec(
        place.longitude,
        3
      )})`;
    }
    return this._t("ch_public");
  }

  _homeZoneName() {
    const st = this._hass && this._hass.states && this._hass.states["zone.home"];
    return (st && st.attributes && st.attributes.friendly_name) || "Home";
  }

  /** The row's one-line verdict: "10.9 kW · 1 h" or "142 kW avg · 18 min". */
  _placeSummary(place) {
    const bits = [];
    if (place.avg_kw != null) {
      const kw = this._dec(place.avg_kw, place.avg_kw >= 20 ? 0 : 1);
      // "avg" only where it is one: a single visit is that visit's figure.
      bits.push(place.power_visits === 1 ? `${kw} kW` : this._t("pl_avg_many", { kw }));
    }
    if (place.typical_minutes != null) {
      bits.push(this._minutesLabel(place.typical_minutes));
    }
    return bits.join(" · ");
  }

  _minutesLabel(mins) {
    const m = Math.round(mins);
    if (m < 60) return `${m} min`;
    const h = Math.floor(m / 60);
    const r = m % 60;
    return r ? `${h} h ${r} min` : `${h} h`;
  }

  _placeRow(place, expanded) {
    const id = place.id || "";
    const isOpen = expanded === id;
    const kind = place.kind === "mixed" ? "AC/DC" : place.kind ? place.kind.toUpperCase() : "";
    const visits =
      place.visits === 1
        ? this._t("pl_visit_one")
        : this._t("pl_visit_many", { n: place.visits });
    const away = !place.zone || place.unnamed;
    const summary = this._placeSummary(place);
    const badge = kind
      ? `<span class="chg__badge ${away ? "chg__badge--away" : "chg__badge--home"}">${kind}</span>`
      : "";
    return `
      <div class="chg__session${isOpen ? " is-open" : ""}">
        <button class="chg__row" data-place="${this._attr(id)}">
          <span class="chg__row-main">
            <span class="chg__date pl__name">${this._esc(this._placeName(place))}</span>
            <span class="chg__meta">${badge}${summary ? `<span class="chg__soc">${summary}</span>` : ""}</span>
          </span>
          <span class="chg__figures">
            <span class="chg__energy chg__energy--lead">${visits}</span>
          </span>
        </button>
        ${isOpen ? this._placeDetail(place) : ""}
      </div>
    `;
  }

  _placeDetail(place) {
    const facts = [];
    const kw = (v) => `${this._dec(v, v >= 20 ? 0 : 1)} kW`;
    if (place.avg_kw != null) facts.push([this._t("ch_avg"), kw(place.avg_kw)]);
    if (place.best_kw != null) facts.push([this._t("pl_best"), kw(place.best_kw)]);
    if (place.minutes_per_10 != null) {
      facts.push([this._t("pl_rate"), `${this._dec(place.minutes_per_10, 1)} min`]);
    }
    const pre = place.preconditioning;
    if (pre) {
      for (const [side, label] of [
        ["on", "pl_precond_on"],
        ["off", "pl_precond_off"],
      ]) {
        const fig = pre[side];
        const val = [
          fig.avg_kw != null ? kw(fig.avg_kw) : null,
          fig.minutes_per_10 != null ? `${this._dec(fig.minutes_per_10, 1)} min / 10 %` : null,
        ]
          .filter((v) => v)
          .join(" · ");
        if (val) facts.push([`${this._t(label)} (${fig.visits}×)`, val]);
      }
    }
    if (place.energy_kwh != null) {
      facts.push([this._t("pl_energy"), `${this._dec(place.energy_kwh, 1)} kWh`]);
    }
    if (place.last_visit) {
      facts.push([this._t("pl_last"), this._fmtSessionDate(place.last_visit)]);
    }

    const factRow = facts
      .map(
        ([k, v]) =>
          `<div class="chg__fact"><span class="chg__fact-lbl">${k}</span><span class="chg__fact-val">${v}</span></div>`
      )
      .join("");
    return `
      <div class="chg__detail">
        ${this._placeCurvesSvg(place.curves)}
        <div class="chg__facts">${factRow}</div>
      </div>
    `;
  }

  /** Every recent visit's power curve on one shared axis, newest strongest,
   * so a place's visits can be compared at a glance. Stepped, like the
   * session chart. */
  _placeCurvesSvg(curves) {
    const usable = (curves || []).filter((c) => Array.isArray(c.points) && c.points.length >= 2);
    if (!usable.length) return "";
    const W = 260;
    const H = 64;
    const pad = 4;
    const spans = usable.map((c) => c.points[c.points.length - 1][0] - c.points[0][0]);
    const spanX = Math.max(...spans, 1);
    const yMax = Math.max(0.1, ...usable.flatMap((c) => c.points.map((p) => p[1])));
    const sx = (x) => pad + (x / spanX) * (W - 2 * pad);
    const sy = (y) => H - pad - (y / yMax) * (H - 2 * pad);
    const lines = usable
      .map((c, i) => {
        const x0 = c.points[0][0];
        const pts = [];
        c.points.forEach((p, j) => {
          pts.push([sx(p[0] - x0), sy(p[1])]);
          const next = c.points[j + 1];
          if (next) pts.push([sx(next[0] - x0), sy(p[1])]);
        });
        const opacity = i === 0 ? 1 : Math.max(0.25, 0.7 - i * 0.12);
        const line = pts.map(([x, y]) => `${this._px(x)},${this._px(y)}`).join(" ");
        return `<polyline points="${line}" class="chg__chart-line" style="opacity:${opacity}"></polyline>`;
      })
      .join("");
    return `
      <svg class="chg__chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img">
        ${lines}
        <text x="${pad}" y="10" class="chg__chart-max">${this._dec(yMax, 1)} kW</text>
      </svg>
    `;
  }

  /** Mount the places map once the map library is ready, and again after a
   * repaint replaced its holder. One dot per place with a position. */
  _mountPlacesMap() {
    const holder = this.shadowRoot && this.shadowRoot.querySelector(".pl__map");
    if (!holder || this._mapLib !== "ready") return;
    if (this._placesMapEl && holder.contains(this._placesMapEl)) {
      this._placesMapEl.hass = this._hass;
      return;
    }
    const points = ((this._plc && this._plc.data) || [])
      .filter((p) => p.latitude != null && p.longitude != null)
      .map((p) => ({ at: [p.latitude, p.longitude], home: !!p.zone && !p.unnamed }));
    if (!points.length) return;
    holder.innerHTML = "";
    const map = document.createElement("ha-map");
    map.autoFit = false;
    map.zoom = 12;
    map.themeMode = "auto";
    map.style.height = "200px";
    map.style.display = "block";
    map.hass = this._hass;
    holder.appendChild(map);
    this._placesMapEl = map;
    this._drawPlaceDots(map, points);
  }

  _drawPlaceDots(mapEl, points) {
    let tries = 0;
    const draw = () => {
      if (!mapEl || !this.shadowRoot.contains(mapEl)) return;
      const surface = this._mapSurface(mapEl);
      if (!surface) {
        if (tries++ < 60) setTimeout(draw, 150);
        return;
      }
      try {
        const fill = (p) => (p.home ? "#22a06b" : "#1c69d4");
        if (surface.kind === "leaflet") {
          const group = surface.L.layerGroup();
          points.forEach((p) =>
            surface.L.circleMarker(p.at, {
              radius: 6,
              color: "#fff",
              weight: 2,
              fillColor: fill(p),
              fillOpacity: 1,
            }).addTo(group)
          );
          group.addTo(surface.lmap);
        } else {
          points.forEach((p) =>
            surface.eng.addMarker(this._dotEl(fill(p), 14), p.at, {
              size: [14, 14],
              interactive: false,
            })
          );
        }
        this._fitMapTo(
          mapEl,
          surface,
          points.map((p) => p.at),
          24,
          15
        );
      } catch (_) {
        if (tries++ < 60) setTimeout(draw, 150);
      }
    };
    draw();
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
  // and grid sensors configured, or a charge from before they were. Absent
  // too when nothing came off the roof: on a night charge "☀ 0% solar" is a
  // sun icon announcing there was no sun, and the detail's breakdown already
  // says where the energy came from.
  _solarTag(session) {
    const pct = session.energy_mix && session.energy_mix.solar_percent;
    if (pct == null || Math.round(pct) === 0) return "";
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
    this._wireChargingTabs();
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

  _paintTrips(deviceId, entities) {
    const name = this._config.title || this._deviceName(deviceId);
    const unit = this._distUnit(deviceId, entities);
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
      unit,
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
    const openRow = open ? this._tripRow(open, expanded, unit) : "";

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
      body = `${this._tripReview(summary, unit)}<div class="chg__list">${openRow}${(
        trips || []
      )
        .map((t) => this._tripRow(t, expanded, unit))
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
  _tripReview(summary, unit) {
    if (!summary || !summary.total_km) return "";
    const km = (v) => this._distance(v, unit);
    const per = (v) => this._dec(this._perDist(v, unit), 1);
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
        this._inUnit(summary.total_km, unit),
        0
      )} <i>${unit === "mi" ? "mi" : "km"}</i></span><span class="tr__tile-lbl">${this._t("tr_review")}</span>${deltaTxt}</div>`,
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
        foot = `<span class="tr__tile-sub">${per(battSide)} ${this._t(
          "tr_at_battery"
        )}${lossTxt}</span>`;
      }
      tiles.push(
        `<div class="tr__tile"><span class="tr__tile-val">${per(
          balance.kwh_per_100km
        )} <i>${this._kwhPer(unit)}</i></span><span class="tr__tile-lbl">${this._t(
          "tr_consumption"
        )} · ${this._t(isGrid ? "tr_at_plug" : "tr_at_battery")}</span>${foot}</div>`
      );
    } else if (battSide != null) {
      tiles.push(
        `<div class="tr__tile"><span class="tr__tile-val">${per(battSide)} <i>${this._kwhPer(
          unit
        )}</i></span><span class="tr__tile-lbl">${this._t(
          "tr_consumption"
        )}</span></div>`
      );
    }
    if (summary.recuperation_kwh_per_100km != null) {
      tiles.push(
        `<div class="tr__tile"><span class="tr__tile-val">${per(
          summary.recuperation_kwh_per_100km
        )} <i>${this._kwhPer(unit)}</i></span><span class="tr__tile-lbl">${this._t(
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

  _tripRow(trip, expanded, unit) {
    const id = trip.start || "";
    const isOpen = expanded === id;
    const live = !!trip.in_progress;
    const date = this._fmtSessionDate(trip.start);
    const from = this._tripPlace(trip.start_place);
    // A drive under way has no destination yet, and guessing one would be a lie:
    // the arrow trails off instead.
    const to = live ? "…" : this._esc(this._tripPlace(trip.end_place));
    const dist = this._distance(trip.distance_km, unit, 1);
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
        ${isOpen ? this._tripDetail(trip, unit) : ""}
      </div>
    `;
  }

  _tripDetail(trip, unit) {
    const facts = [];
    // Taken from the record, never recomputed here: the integration withholds a
    // consumption figure when the SoC drop behind it was too small to divide by
    // (a 1 km hop that ticked one percent is not a 78 kWh/100 km drive), and
    // dividing energy by distance in the card would print it anyway.
    const cons = trip.consumption_kwh_per_100km;
    if (cons != null) {
      facts.push([
        this._t("tr_consumption"),
        `${this._dec(this._perDist(cons, unit), 1)} ${this._kwhPer(unit)}`,
      ]);
    }
    const st = trip.stats || {};
    // BMW's recuperation figure is already an average per 100 km, not a total.
    const recup = st.recuperation_kwh_per_100km ?? st.recuperation_kwh;
    if (recup != null) {
      facts.push([
        this._t("tr_recuperation"),
        `${this._dec(this._perDist(recup, unit), 1)} ${this._kwhPer(unit)}`,
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
    if (this._drivetrain(entities) === "ice") {
      this._renderFuel(deviceId, entities);
      return;
    }

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

  /** A plug-in hybrid's consumption, from BMW's lifetime counters. */
  _efHybridLifetime(life, unit, fuel) {
    const dist = (km) => this._distance(km, unit);
    const per = (v) => `${this._dec(this._perDist(v, unit), 1)} ${this._kwhPer(unit)}`;
    const facts = [];
    if (life.electric_kwh_per_100km != null) {
      facts.push([
        this._t("ef_electric"),
        per(life.electric_kwh_per_100km),
        life.electric_km != null ? this._t("ef_electric_note", { d: dist(life.electric_km) }) : "",
      ]);
    }
    if (life.electric_share_percent != null) {
      facts.push([this._t("ef_electric_share"), `${this._dec(life.electric_share_percent, 0)} %`, ""]);
    }
    if (life.fuel_l_per_100km != null) {
      facts.push([
        this._t("fu_lifetime"),
        this._fuelRate(life.fuel_l_per_100km, fuel),
        life.distance_km != null ? this._t("fu_lifetime_note", { d: dist(life.distance_km) }) : "",
      ]);
    }
    if (life.grid_kwh_per_100km != null) {
      facts.push([this._t("ef_grid_lifetime"), per(life.grid_kwh_per_100km), ""]);
    }
    const row = facts
      .map(
        ([k, v, note]) =>
          `<div class="ef__fact">
             <span class="ef__fact-lbl">${k}</span>
             <span class="ef__fact-val">${v}</span>
             ${note ? `<span class="ef__fact-note">${note}</span>` : ""}
           </div>`
      )
      .join("");
    return `
      <div class="ef">
        <div class="ef__facts">${row}</div>
        <span class="ef__note">${this._t("ef_hybrid_lifetime")}</span>
      </div>
    `;
  }

  _paintEfficiency(deviceId, st) {
    const name = this._config.title || this._deviceName(deviceId);
    const unit = this._distUnit(deviceId);
    const fuel = this._fuelUnit(deviceId);
    const state = this._eff || {};
    const profile = state.data;
    const f = this._efFigures(st, profile);
    const trend = (profile && Array.isArray(profile.trend) && profile.trend) || [];

    const sig = this._signature({
      m: "ef",
      lang: _lang(this._hass),
      name,
      unit,
      fuel,
      loading: state.loading && !profile && !st,
      error: state.error,
      f,
      trend,
      cost: profile && profile.cost_per_100km,
      currency: profile && profile.currency,
      mix: profile && profile.energy_mix,
      life: profile && profile.bmw_lifetime,
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
      // BMW's own counters split the distance by whether the engine ran, so
      // where the car sends them they are the figure instead.
      const life = profile && profile.bmw_lifetime;
      body = life
        ? this._efHybridLifetime(life, unit, fuel)
        : `<div class="empty">${this._t("ef_hybrid")}</div>`;
    } else if (f.consumption == null) {
      // "50 km or so", in round miles where the car counts in them.
      body = `<div class="empty">${this._t("ef_empty", {
        d: unit === "mi" ? "30 mi" : "50 km",
      })}</div>`;
    } else if (f.fullKm == null) {
      // Consumption measured, but nothing to divide it into: say which half is
      // missing rather than showing the same "no data yet" as an empty ledger.
      body = `<div class="empty">${this._t("ef_no_capacity")}</div>`;
    } else {
      body = this._efBody(f, trend, profile || {}, unit);
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

  _efBody(f, trend, profile, unit) {
    const unitLabel = unit === "mi" ? "mi" : "km";
    const hero =
      f.nowKm != null
        ? `<span class="ef__hero-val">${this._dec(this._inUnit(f.nowKm, unit), 0)} <i>${unitLabel}</i></span>
           <span class="ef__hero-lbl">${this._t("ef_range_now")}${
             f.soc != null
               ? " · " + this._t("ef_at_soc", { p: this._dec(f.soc, 0) })
               : ""
           }</span>`
        : `<span class="ef__hero-val">${this._dec(this._inUnit(f.fullKm, unit), 0)} <i>${unitLabel}</i></span>
           <span class="ef__hero-lbl">${this._t("ef_range_full", {
             d: this._distance(f.fullKm, unit),
           })}</span>`;

    let vsBmw = "";
    if (f.vsBmw != null && f.bmwKm != null) {
      const p = Math.round(Math.abs(f.vsBmw));
      const d = this._distance(f.bmwKm, unit);
      const key =
        p < 1 ? "ef_vs_bmw_same" : f.vsBmw > 0 ? "ef_vs_bmw_over" : "ef_vs_bmw_under";
      const tone = p < 1 ? "" : f.vsBmw > 0 ? " ef__vs--over" : " ef__vs--under";
      vsBmw = `<span class="ef__vs${tone}">${this._t(key, { p: this._dec(p, 0), d })}</span>`;
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
        `${this._dec(this._perDist(f.consumption, unit), 1)} ${this._kwhPer(unit)}`,
        `${sideLabel} · ${windowLabel}`,
      ],
    ];
    if (f.loss != null && f.gridConsumption != null) {
      facts.push([
        this._t("ef_loss"),
        `${this._dec(f.loss, 0)} %`,
        `${this._dec(this._perDist(f.gridConsumption, unit), 1)} ${this._kwhPer(unit)} ${this._t(
          "ef_side_grid"
        )}`,
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
        this._t("ef_cost", { per: this._per100(unit) }),
        `${this._dec(this._perDist(profile.cost_per_100km, unit), 2)}${currency}`,
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

    const chart = this._efTrendSvg(trend, unit);
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
  _efTrendSvg(trend, unit) {
    if (!Array.isArray(trend) || trend.length < 2) return "";
    const W = 280;
    const H = 88;
    const pad = 6;
    const bottom = 14;
    const values = trend.map((e) => this._perDist(e.kwh_per_100km, unit));
    const max = Math.max(...values) * 1.15 || 1;
    const slot = (W - 2 * pad) / trend.length;
    // Capped as well as proportional: with two months on file a purely
    // proportional bar is 78 px wide and reads as a block, not a chart.
    const width = Math.min(28, Math.max(4, slot * 0.58));
    const bars = trend
      .map((entry, i) => {
        const x = pad + i * slot + (slot - width) / 2;
        const h = (values[i] / max) * (H - bottom - pad);
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
    )} ${this._kwhPer(unit)}</text>
      </svg>
    `;
  }

  /* ---- mileage ---------------------------------------------------------- */

  /** How far the car is driven, and its mileage allowance when it has one.
   * Everything comes from `get_mileage_report` (the daily odometer log, no
   * quota). Re-fetched when the odometer moves, on a new day, and every ten
   * minutes so a freshly configured allowance shows up without a reload. */
  /* ---- fuel (petrol, diesel, plug-in hybrid) --------------------------- */

  /** Refuels and consumption, read off the tank level (``history/fuel.py``),
   * plus BMW's own lifetime figure where the car sends its counters. The
   * combustion car's answer to the charging and efficiency views. */
  _renderFuel(deviceId, entities) {
    const vin = this._deviceVin(deviceId);
    if (!vin) {
      this._renderMessage(this._t("no_vehicle_title"), this._t("no_vehicle_body"));
      return;
    }
    const entryId = this._deviceEntry(deviceId);
    const tank = FUEL_DESCRIPTORS.map((d) => this._byDescriptor(entities, d)).find(Boolean);
    // A refuel can only appear with a new tank reading, so that is what
    // re-asks; the ten-minute tick keeps "since the last refuel" moving.
    const trigger = [tank ? tank.state : "", Math.floor(Date.now() / 600000)].join("|");
    const cache = this._fuel;
    const current = cache && cache.vin === vin && cache.trigger === trigger;
    if (!current || (!cache.data && !cache.loading)) {
      this._fuel = {
        vin,
        trigger,
        data: cache && cache.vin === vin ? cache.data : null,
        loading: true,
        error: false,
      };
      this._fetchFuel(vin, entryId);
    }
    this._paintFuel(deviceId, this._distUnit(deviceId, entities), this._fuelUnit(deviceId, entities));
  }

  _fetchFuel(vin, entryId) {
    const req = this._fuel;
    this._hass
      .callService(
        "bavariandata",
        "get_fuel_history",
        this._withEntry({ vin }, entryId),
        undefined,
        false,
        true
      )
      .then((res) => {
        if (!this._fuel || this._fuel.vin !== vin || this._fuel.trigger !== req.trigger) return;
        const data = (res && res.response && res.response.fuel) || null;
        this._fuel = { ...this._fuel, data, loading: false, error: false };
        this._render();
      })
      .catch(() => {
        if (!this._fuel || this._fuel.vin !== vin || this._fuel.trigger !== req.trigger) return;
        this._fuel = { ...this._fuel, loading: false, error: true };
        this._render();
      });
  }

  _paintFuel(deviceId, unit, fuel) {
    const name = this._config.title || this._deviceName(deviceId);
    const state = this._fuel || {};
    const data = state.data;
    const sig = this._signature({
      m: "fu",
      lang: _lang(this._hass),
      name,
      unit,
      fuel,
      loading: state.loading && !data,
      error: state.error && !data,
      data,
    });
    if (sig === this._sig) return;
    this._sig = sig;

    let body;
    if (state.error && !data) {
      body = `<div class="empty">${this._t("fu_error")}</div>`;
    } else if (state.loading && !data) {
      body = `<div class="empty">${this._t("fu_loading")}</div>`;
    } else {
      body = this._fuBody(data || {}, unit, fuel);
    }

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        <div class="chead">
          <ha-icon icon="mdi:gas-station"></ha-icon>
          <div class="chead__text">
            <span class="chead__title">${this._esc(this._config.title || this._t("fu_title"))}</span>
            <span class="chead__sub">${this._esc(name)}</span>
          </div>
        </div>
        ${body}
      </ha-card>
    `;
  }

  _fuBody(data, unit, fuel) {
    const dist = (km) => this._distance(km, unit);
    const litres = (l) => this._fuelVolume(l, fuel);
    const rate = (r) => this._fuelRate(r, fuel);
    const fact = (label, value, note) =>
      value
        ? `<div class="ef__fact">
             <span class="ef__fact-lbl">${label}</span>
             <span class="ef__fact-val">${value}</span>
             ${note ? `<span class="ef__fact-note">${note}</span>` : ""}
           </div>`
        : "";

    const tank = data.tank || {};
    const tankValue =
      tank.litres != null ? litres(tank.litres) : tank.percent != null ? `${this._dec(tank.percent, 0)} %` : "";
    const tankNote =
      tank.litres != null && tank.percent != null
        ? `${this._dec(tank.percent, 0)} %`
        : tank.range_km != null
        ? this._t("fu_range", { d: dist(tank.range_km) })
        : "";
    const avg = data.average;
    const since = data.since_refuel;
    const sinceValue =
      since && since.km != null ? dist(since.km) : "";
    const sinceNote = since
      ? since.l_per_100km != null
        ? rate(since.l_per_100km)
        : since.litres != null && since.litres > 0
        ? this._t("fu_used", { l: litres(since.litres) })
        : ""
      : "";
    const life = data.bmw_lifetime;
    const facts = [
      fact(this._t("fu_tank"), tankValue, tankNote),
      fact(
        this._t("fu_average"),
        avg ? rate(avg.l_per_100km) : "",
        avg ? this._t("fu_average_note", { d: dist(avg.km) }) : ""
      ),
      fact(this._t("fu_since"), sinceValue, sinceNote),
      fact(
        this._t("fu_lifetime"),
        life && life.fuel_l_per_100km != null ? rate(life.fuel_l_per_100km) : "",
        life && life.distance_km != null ? this._t("fu_lifetime_note", { d: dist(life.distance_km) }) : ""
      ),
    ].join("");

    const refuels = Array.isArray(data.refuels) ? data.refuels : [];
    const rows = refuels
      .map((r) => {
        const added =
          r.added_l != null
            ? `+${litres(r.added_l)}`
            : r.added_percent != null
            ? `+${this._dec(r.added_percent, 0)} %`
            : "—";
        const meta = [];
        if (r.km != null) meta.push(`<span class="chg__tag">${dist(r.km)}</span>`);
        if (r.l_per_100km != null) {
          meta.push(
            `<span class="chg__tag">${this._t("fu_window", {
              r: rate(r.l_per_100km),
              d: dist(r.window_km),
            })}</span>`
          );
        }
        return `
          <div class="chg__session">
            <div class="chg__row">
              <span class="chg__row-main">
                <span class="chg__date">${this._fmtSessionDate(r.t)}</span>
                <span class="chg__meta">${meta.join("")}</span>
              </span>
              <span class="chg__figures">
                <span class="chg__energy chg__energy--lead">${added}</span>
              </span>
            </div>
          </div>`;
      })
      .join("");

    return `
      <div class="ef">
        <div class="ef__facts">${facts}</div>
        ${
          rows
            ? `<div class="ef__trend"><span class="ef__trend-title">${this._t("fu_refuels")}</span></div>
               <div class="chg__list">${rows}</div>`
            : `<span class="ef__note">${this._t("fu_no_refuels")}</span>`
        }
        <span class="ef__note">${this._t("fu_note")}</span>
      </div>
    `;
  }

  _renderMileage(deviceId, entities) {
    const vin = this._deviceVin(deviceId);
    if (!vin) {
      this._renderMessage(this._t("no_vehicle_title"), this._t("no_vehicle_body"));
      return;
    }
    const entryId = this._deviceEntry(deviceId);
    const odo = DESC_ODOMETER.map((d) => this._byDescriptor(entities, d)).find(Boolean);
    const trigger = [
      odo ? odo.state : "",
      this._monthKey(),
      new Date().getDate(),
      Math.floor(Date.now() / 600000),
    ].join("|");
    const cache = this._mil;
    const current = cache && cache.vin === vin && cache.trigger === trigger;
    if (!current || (!cache.data && !cache.loading)) {
      this._mil = {
        vin,
        trigger,
        data: cache && cache.vin === vin ? cache.data : null,
        loading: true,
        error: false,
      };
      this._fetchMileage(vin, entryId);
    }
    this._paintMileage(deviceId, this._distUnit(deviceId, entities));
  }

  _fetchMileage(vin, entryId) {
    const req = this._mil;
    this._hass
      .callService(
        "bavariandata",
        "get_mileage_report",
        this._withEntry({ vin }, entryId),
        undefined,
        false,
        true
      )
      .then((res) => {
        if (!this._mil || this._mil.vin !== vin || this._mil.trigger !== req.trigger) return;
        const data = (res && res.response) || null;
        this._mil = { ...this._mil, data, loading: false, error: false };
        this._render();
      })
      .catch(() => {
        if (!this._mil || this._mil.vin !== vin || this._mil.trigger !== req.trigger) return;
        this._mil = { ...this._mil, loading: false, error: true };
        this._render();
      });
  }

  /** "31 Aug 2028" in the viewer's language, from an ISO date. */
  _isoDay(iso) {
    if (!iso) return "";
    const [y, m, d] = String(iso).split("-").map(Number);
    try {
      const lang = _lang(this._hass);
      return new Date(y, m - 1, d).toLocaleDateString(lang === "en" ? "en-GB" : lang, {
        day: "numeric",
        month: "short",
        year: "numeric",
      });
    } catch (_e) {
      return String(iso);
    }
  }

  _paintMileage(deviceId, unit) {
    const name = this._config.title || this._deviceName(deviceId);
    const state = this._mil || {};
    const data = state.data;
    const sig = this._signature({
      m: "mi",
      lang: _lang(this._hass),
      name,
      unit,
      loading: state.loading && !data,
      error: state.error && !data,
      data,
    });
    if (sig === this._sig) return;
    this._sig = sig;

    let body;
    if (state.error && !data) {
      body = `<div class="empty">${this._t("mi_error")}</div>`;
    } else if (state.loading && !data) {
      body = `<div class="empty">${this._t("mi_loading")}</div>`;
    } else if (!data || data.odometer_km == null) {
      body = `<div class="empty">${this._t("mi_empty")}</div>`;
    } else {
      body = this._miBody(data, unit);
    }

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        <div class="chead">
          <ha-icon icon="mdi:counter"></ha-icon>
          <div class="chead__text">
            <span class="chead__title">${this._esc(this._config.title || this._t("mi_title"))}</span>
            <span class="chead__sub">${this._esc(name)}</span>
          </div>
        </div>
        ${body}
      </ha-card>
    `;
  }

  _miBody(data, unit) {
    const dist = (km) => this._distance(km, unit);
    const tile = (label, part) => {
      if (!part) return "";
      // A year begun before the log: the estimate first, what was recorded below.
      const estimated = part.estimated_km != null;
      const note = estimated
        ? this._t("mi_recorded_since", { d: dist(part.km), date: this._isoDay(data.since) })
        : part.partial
        ? this._t("mi_partial")
        : "";
      return `<div class="ef__fact">
          <span class="ef__fact-lbl">${label}</span>
          <span class="ef__fact-val">${estimated ? "≈ " + dist(part.estimated_km) : dist(part.km)}</span>
          ${note ? `<span class="ef__fact-note">${note}</span>` : ""}
        </div>`;
    };
    const perDay =
      data.km_per_day != null
        ? `<div class="ef__fact">
             <span class="ef__fact-lbl">${this._t("mi_per_day")}</span>
             <span class="ef__fact-val">${dist(data.km_per_day)}</span>
           </div>`
        : "";
    const pace =
      data.projected_year_km != null
        ? this._t("mi_pace", { d: dist(data.projected_year_km) })
        : this._t("mi_no_pace");
    const chart = this._miMonthsSvg(data.months || [], unit);
    const allowance = data.allowance
      ? this._miAllowance(data.allowance, dist)
      : `<span class="ef__note">${this._t("mi_hint")}</span>`;
    return `
      <div class="ef">
        <div class="ef__facts">
          ${tile(this._t("mi_this_month"), data.this_month)}
          ${tile(this._t("mi_this_year"), data.this_year)}
          ${perDay}
        </div>
        <span class="ef__vs">${pace}</span>
        ${
          chart
            ? `<div class="ef__trend"><span class="ef__trend-title">${this._t(
                "mi_months"
              )}</span>${chart}</div>`
            : ""
        }
        ${allowance}
      </div>
    `;
  }

  _miAllowance(a, dist) {
    const end = this._isoDay(a.end);
    const head =
      a.kind === "lease"
        ? this._t("mi_lease", { date: end })
        : this._t("mi_yearly", { date: end });
    let rows;
    if (a.status === "not_started") {
      rows = `<span class="ef__note">${this._t("mi_not_started", {
        date: this._isoDay(a.start),
      })}</span>`;
    } else if (a.status === "unknown") {
      rows = `<span class="ef__note">${this._t("mi_unknown")}</span>`;
    } else {
      const total = a.allowance_km || 0;
      const used = a.used_km || 0;
      const pct = total > 0 ? Math.min(100, (used / total) * 100) : 0;
      const plan = total > 0 ? Math.min(100, ((a.plan_km || 0) / total) * 100) : 0;
      const over = used > total;
      const balance = a.balance_km || 0;
      const tone = balance < 0 ? " ef__vs--over" : "";
      const balanceText =
        Math.abs(balance) < 1
          ? this._t("mi_on_plan")
          : balance > 0
          ? this._t("mi_under", { d: dist(balance) })
          : this._t("mi_over", { d: dist(-balance) });
      const lines = [`<span class="ef__vs${tone}">${balanceText}</span>`];
      if (a.status === "ended") {
        lines.push(`<span class="ef__note">${this._t("mi_ended")}</span>`);
      } else {
        if (a.km_per_day_left != null && a.km_per_day_left >= 0) {
          lines.push(
            `<span class="ef__note">${this._t("mi_left_per_day", {
              d: dist(a.km_per_day_left),
            })}</span>`
          );
        }
        if (a.projected_km != null) {
          const diff = a.projected_over_km || 0;
          const sign = diff > 0 ? "+" : diff < 0 ? "−" : "±";
          lines.push(
            `<span class="ef__note">${this._t("mi_projected", {
              d: dist(a.projected_km),
              diff: `${sign}${dist(Math.abs(diff))}`,
            })}</span>`
          );
        }
      }
      rows = `
        <div class="mi__bar${over ? " mi__bar--over" : ""}" role="img"
             aria-label="${this._attr(this._t("mi_of", { used: dist(used), total: dist(total) }))}">
          <span class="mi__fill" style="width:${this._px(pct)}%"></span>
          <span class="mi__plan" style="left:${this._px(plan)}%"></span>
        </div>
        <span class="mi__of">${this._t("mi_of", { used: dist(used), total: dist(total) })}</span>
        ${lines.join("")}`;
    }
    return `<div class="mi__allowance">
        <span class="ef__trend-title">${head}</span>
        ${rows}
      </div>`;
  }

  /** Distance per month as bars, oldest left. A month the log doesn't reach
   * draws no bar; the months before the first reading are left out. */
  _miMonthsSvg(months, unit) {
    const first = months.findIndex((m) => m.km != null);
    if (first < 0) return "";
    const shown = months.slice(first);
    if (shown.length < 2) return "";
    const W = 280;
    const H = 88;
    const pad = 6;
    const bottom = 14;
    const factor = unit === "mi" ? 1 / KM_PER_MILE : 1;
    const values = shown.map((m) => (m.km || 0) * factor);
    const max = Math.max(...values) * 1.15 || 1;
    const slot = (W - 2 * pad) / shown.length;
    const width = Math.min(28, Math.max(4, slot * 0.58));
    const bars = shown
      .map((entry, i) => {
        const x = pad + i * slot + (slot - width) / 2;
        const h = ((entry.km || 0) * factor / max) * (H - bottom - pad);
        const y = H - bottom - h;
        const label = String(entry.month || "").slice(5);
        const cls = entry.partial ? "ef__bar mi__bar-partial" : "ef__bar";
        return `${
          entry.km != null
            ? `<rect class="${cls}" x="${this._px(x)}" y="${this._px(y)}"
                  width="${this._px(width)}" height="${this._px(Math.max(h, 1))}" rx="2"></rect>`
            : ""
        }<text class="ef__bar-lbl" x="${this._px(x + width / 2)}" y="${H - 3}"
                  text-anchor="middle">${this._esc(label)}</text>`;
      })
      .join("");
    return `<svg class="ef__chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${bars}</svg>`;
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
    const unit = this._distUnit(deviceId, entities);

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
      .map((w) => ({ st: w.pressure, n: this._pressureKpa(w.pressure) }))
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

  // A pressure state in kPa, whatever unit Home Assistant displays it in. The
  // display unit is a per-entity setting, so one wheel's target can read "240
  // kPa" beside three that read "2.4 bar" (issue #235). null for a unit that
  // is not a known pressure: no verdict beats a wrong one.
  _pressureKpa(st) {
    const n = this._num(st);
    if (n == null) return null;
    const unit = String((st.attributes && st.attributes.unit_of_measurement) || "kPa")
      .trim()
      .toLowerCase();
    const factor = { kpa: 1, bar: 100, mbar: 0.1, hpa: 0.1, pa: 0.001, psi: 6.894757 }[unit];
    return factor == null ? null : n * factor;
  }

  // Pressure alone, so the summary can report it separately from wear.
  _pressureStatus(wheel) {
    const cur = this._pressureKpa(wheel.pressure);
    const tgt = this._pressureKpa(wheel.pressureTarget);
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
          // "Tilted" is the sunroof's word; a side window is partly down.
          value: this._t(windows[k].partial ? "state_partly_open" : "state_open"),
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
   * market for the car offers, which may not be the dashboard's. Options BMW
   * has a picture for show it in front of the name, cut from one sheet. */
  _renderEquipment(deviceId, entities) {
    const name = this._deviceName(deviceId);
    const cfg = this._config || {};
    const id = cfg.image || entities.find((e) => e.startsWith("image."));
    const st = id && this._st(id);
    const list = (st && st.attributes && Array.isArray(st.attributes.equipment) && st.attributes.equipment) || [];
    const items = list.filter((e) => e && typeof e.name === "string" && typeof e.code === "string");
    // `pictures: false` keeps the plain list; the sheet then isn't even signed.
    const pics = cfg.pictures !== false && st && st.attributes && st.attributes.configurator_options;
    const grid =
      pics && pics.path && pics.index && typeof pics.index === "object" && Number(pics.columns) > 0
        ? { columns: Number(pics.columns), rows: Number(pics.rows) || 1 }
        : null;
    const sheet = grid ? this._signedUrl(pics.path) : null;
    const sig = this._signature({
      m: "eq",
      lang: _lang(this._hass),
      name,
      sheet,
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
    const pic = (code) => {
      if (!sheet) return "";
      const n = Object.prototype.hasOwnProperty.call(pics.index, code) ? Number(pics.index[code]) : -1;
      if (!Number.isInteger(n) || n < 0 || n >= grid.columns * grid.rows) {
        // No picture for this option: an empty tile keeps the names in line.
        return `<span class="eq__pic eq__pic--none"><ha-icon icon="mdi:image-off-outline"></ha-icon></span>`;
      }
      const style = [
        `background-image:url('${this._esc(sheet)}')`,
        `background-size:${grid.columns * 100}% ${grid.rows * 100}%`,
        `background-position:${this._spinPos(grid, n)}`,
      ].join(";");
      return `<span class="eq__pic" style="${style}"></span>`;
    };
    const rows = (group) =>
      group
        .map(
          (e) =>
            `<div class="eq__row">${pic(e.code)}<span class="eq__name">${this._esc(e.name)}</span><span class="eq__code">${this._esc(e.code)}</span></div>`
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
    // `hinge` turns the glow with an open door, so a window rides on it.
    const lit = (path, color, k, extra = "", hinge = "") =>
      `<g class="cldiag__lit" style="--c:${color}"${flip(k)}>` +
      (hinge ? `<g transform="${hinge}">` : "") +
      `<path d="${path}" class="cldiag__zone"/><path d="${path}" class="cldiag__core"/>${extra}` +
      (hinge ? "</g>" : "") +
      `</g>`;
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
    // window also shows the lowered pane's top edge. The pane is in the door,
    // so with its door open it is drawn swung out with it, not in the gap.
    const glass = (part, path, k, extra, hinge) => {
      if (!part) return;
      if (part.open) parts.push(lit(path, WARN, k, extra, hinge));
      parts.push(hit(path, part.id, k));
    };
    for (const k of ["lf", "rf", "lr", "rr"]) {
      const w = d.windows[k];
      const g = car.window[k[1]];
      const [x1, x2] = g.dropX || [200, 300];
      const door = d.doors[k];
      const hinge = door && door.open ? car.door[k[1]].hinge : "";
      if (w) glass(w, g.d, k, `<path d="M${x1} ${g.drop} L${x2} ${g.drop}" class="cldiag__drop" clip-path="url(#${g.clip})"/>`, hinge);
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
      /* The night spin is a cut-out: a dark room with a pool of light under the
         car, the same in light and dark themes, as the night is. */
      .hero--night {
        background: radial-gradient(ellipse 52% 30% at 50% 74%, #464a4f 0%, #2c2e32 55%, #18191b 100%);
      }
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
      .eq__pic {
        flex: none; align-self: center;
        width: 56px; aspect-ratio: 168 / 95;
        border-radius: 4px;
        background-repeat: no-repeat;
        background-color: var(--secondary-background-color);
      }
      .eq__pic + .eq__name { flex: 1; }
      .eq__pic--none {
        display: flex; align-items: center; justify-content: center;
        color: var(--disabled-text-color);
        --mdc-icon-size: 18px;
      }
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
      /* Sessions | Places */
      .ctabs { display: flex; gap: 6px; padding: 10px 14px 0; }
      .ctabs__btn {
        flex: 1; padding: 6px 10px;
        font: inherit; font-size: 0.8rem; font-weight: 600;
        color: var(--secondary-text-color); background: transparent;
        border: 1px solid var(--divider-color); border-radius: 999px; cursor: pointer;
      }
      .ctabs__btn.is-active {
        color: var(--bmw-charge); border-color: var(--bmw-charge);
        background: var(--secondary-background-color);
      }
      .pl__map { padding: 10px 14px 0; }
      .pl__map ha-map { width: 100%; border-radius: 10px; overflow: hidden; }
      .pl__name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
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
      .mi__allowance { display: flex; flex-direction: column; gap: 6px; }
      .mi__bar {
        position: relative; height: 10px; border-radius: 5px; overflow: hidden;
        background: var(--divider-color, rgba(127, 127, 127, 0.25));
      }
      .mi__fill { position: absolute; inset: 0 auto 0 0; background: var(--bmw-charge); }
      .mi__bar--over .mi__fill { background: var(--bmw-high); }
      .mi__plan {
        position: absolute; top: 0; bottom: 0; width: 2px; margin-left: -1px;
        background: var(--primary-text-color); opacity: 0.6;
      }
      .mi__of { font-size: 1.05rem; font-weight: 600; font-variant-numeric: tabular-nums; }
      .mi__bar-partial { opacity: 0.45; }

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
