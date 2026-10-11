// Shoot a Configure screen in the real HA frontend -- as the live instance shows
// it, or staged as it would look on a two-car account the instance can't be.
//
// Staging answers the options-flow REST calls the way the integration would on
// a two-car account; HA's own dialog renders the result with the installed
// build's translations. The staged submit never reaches Home Assistant, any
// other flow call is aborted, and closing the browser abandons the flow:
// nothing can be saved, in either mode (the real form is never submitted).
// Entity pickers holding a value are blurred: they show the user's own names.
//
// Run from a directory with playwright-core installed (see the shoot skill):
//   HA_URL=http://<host> HA_TOKEN=<token> \
//     node mock_flow.js <trips|charging|bridge> <real|staged> <lang> <width> <outdir>
// A new per-car screen: add it to SCREENS, starting from the real step the
// server returns (written to real-<screen>-step.json) so the schema is HA's own.
// A section serializes as {"type": "expandable", "name", "expanded", "schema"}.
const fs = require("fs");
const path = require("path");
const { chromium } = require(require.resolve("playwright-core", { paths: [process.cwd(), __dirname] }));

const URL = process.env.HA_URL;
if (!URL || !process.env.HA_TOKEN) throw new Error("set HA_URL and HA_TOKEN");
const BROWSERS = path.join(process.env.LOCALAPPDATA, "ms-playwright");
const NEWEST = fs.readdirSync(BROWSERS).filter((d) => /^chromium-\d+$/.test(d)).sort().pop();
const EXE = path.join(BROWSERS, NEWEST, "chrome-win64", "chrome.exe");

const SCREENS = {
  trips: {
    menu: { en: "Trips", de: "Fahrten" },
    step: "action_trips",
    picker: "trips_vehicle",
    second: { vin: "WBADEMO0000000002", name: "530d Touring" },
    car: ["trip_work_zone", "trip_default_class", "trip_commute_gap", "trip_detection", "trip_track"],
    expanded: false,
    // The older car: no work zone of its own yet, on end-of-trip reports.
    values: { trip_work_zone: null, trip_default_class: "private", trip_commute_gap: 30, trip_detection: "reports", trip_track: false },
  },
  charging: {
    menu: { en: "Charging costs & history", de: "Ladekosten & Verlauf" },
    step: "action_charging_costs",
    picker: "charging_vehicle",
    second: { vin: "WBADEMO0000000003", name: "iX1 eDrive20" },
    car: ["grid_energy_entity", "charging_loss_percent"],
    expanded: true,
    // Charged elsewhere: no wallbox meter, losses estimated instead.
    values: { grid_energy_entity: null, charging_loss_percent: 10 },
  },
  bridge: {
    menu: { en: "evcc / wallbox bridge", de: "evcc-/Wallbox-Brücke" },
    step: "action_evcc_bridge",
    picker: "bridge_vehicle",
    second: { vin: "WBADEMO0000000003", name: "iX1 eDrive20" },
    car: ["bridge_enabled"],
    expanded: false,
    values: { bridge_enabled: false },
  },
};

const [screenName, mode, lang, width, outdir] = process.argv.slice(2);
const screen = SCREENS[screenName];
if (!screen || !["real", "staged"].includes(mode)) throw new Error("usage: <trips|charging|bridge> <real|staged> <lang> <width> <outdir>");
const NEXT = { en: "Next", de: "Weiter" };

let realStep = null;
let phase = "menu";

function pickerStep(base) {
  const first = base.description_placeholders.vehicle;
  return {
    ...base,
    step_id: screen.picker,
    last_step: false,
    data_schema: [
      {
        name: "vehicle",
        required: true,
        default: "WBAREAL0000000001",
        selector: {
          select: {
            options: [
              { value: "WBAREAL0000000001", label: first },
              { value: screen.second.vin, label: screen.second.name },
            ].sort((a, b) => a.label.localeCompare(b.label)),
            mode: "dropdown",
            multiple: false,
            custom_value: false,
            sort: false,
          },
        },
      },
    ],
    description_placeholders: null,
    errors: {},
  };
}

function secondCarForm(base) {
  const own = new Set(screen.car);
  const car = base.data_schema
    .filter((field) => own.has(field.name))
    .map((field) => {
      const copy = { ...field };
      const value = screen.values[field.name];
      delete copy.description; // a suggested value would be the first car's
      if (value === null) delete copy.default;
      else copy.default = value;
      return copy;
    });
  const shared = base.data_schema.filter((field) => !own.has(field.name));
  return {
    ...base,
    data_schema: [
      ...car,
      { type: "expandable", name: "all_vehicles", required: true, expanded: screen.expanded, schema: shared },
    ],
    description_placeholders: { vehicle: screen.second.name },
    errors: {},
  };
}

async function dialogRect(page) {
  return page.evaluate(() => {
    const found = [];
    const walk = (root) => {
      for (const el of root.querySelectorAll("*")) {
        const tag = el.tagName.toLowerCase();
        if (tag === "dialog" || el.getAttribute("role") === "dialog") found.push(el);
        if (el.shadowRoot) walk(el.shadowRoot);
      }
    };
    walk(document);
    const rects = found
      .map((el) => el.getBoundingClientRect())
      .filter((r) => r.width > 200 && r.height > 150)
      .sort((a, b) => a.width * a.height - b.width * b.height);
    const r = rects[0];
    return r ? { x: r.x, y: r.y, width: r.width, height: r.height } : null;
  });
}

async function blurValuedPickers(page) {
  await page.evaluate(() => {
    const walk = (root) => {
      for (const el of root.querySelectorAll("*")) {
        if (el.tagName.toLowerCase() === "ha-selector-entity" && el.value) el.style.filter = "blur(7px)";
        if (el.shadowRoot) walk(el.shadowRoot);
      }
    };
    walk(document);
  });
}

async function shot(page, name) {
  await page.waitForTimeout(1200);
  await blurValuedPickers(page);
  await page.waitForTimeout(300);
  const clip = await dialogRect(page);
  if (!clip) throw new Error("no dialog for " + name);
  const file = path.join(outdir, `${screenName}-${mode}-${lang}-${width}-${name}.png`);
  await page.screenshot({ path: file, clip });
  console.log("saved", file);
}

(async () => {
  const browser = await chromium.launch({ executablePath: EXE });
  const context = await browser.newContext({ viewport: { width: Number(width), height: 1600 }, deviceScaleFactor: 2 });
  await context.addInitScript(
    ([token, url, language]) => {
      localStorage.setItem(
        "hassTokens",
        JSON.stringify({
          access_token: token, token_type: "Bearer", expires_in: 1800, hassUrl: url,
          clientId: url + "/", expires: Date.now() + 10 * 365 * 86400 * 1000, refresh_token: "",
        })
      );
      localStorage.setItem("selectedLanguage", JSON.stringify(language));
    },
    [process.env.HA_TOKEN, URL, lang]
  );
  const page = await context.newPage();

  if (mode === "staged") {
    await page.route("**/api/config/config_entries/options/flow/*", async (route) => {
      const request = route.request();
      if (request.method() !== "POST") return route.continue();
      const body = JSON.parse(request.postData() || "{}");
      if (phase === "menu" && body.next_step_id === screen.step) {
        const response = await route.fetch();
        realStep = await response.json();
        fs.writeFileSync(path.join(outdir, `real-${screenName}-step.json`), JSON.stringify(realStep, null, 2));
        phase = "picker";
        return route.fulfill({ json: pickerStep(realStep) });
      }
      if (phase === "picker" && "vehicle" in body) {
        phase = "form";
        return route.fulfill({ json: secondCarForm(realStep) });
      }
      // Anything else would reach the server: refuse it rather than risk a save.
      return route.abort();
    });
  } else {
    // The real form is shown, never submitted; refuse a submit all the same.
    await page.route("**/api/config/config_entries/options/flow/*", async (route) => {
      const request = route.request();
      const body = request.method() === "POST" ? JSON.parse(request.postData() || "{}") : {};
      if (request.method() === "POST" && !("next_step_id" in body)) return route.abort();
      return route.continue();
    });
  }

  await page.goto(URL + "/config/integrations/integration/bavariandata");
  await page.waitForFunction(() => document.querySelector("home-assistant")?.hass?.states, null, { timeout: 60000 });
  await page.waitForTimeout(3000);
  await page.getByRole("button", { name: lang === "de" ? "Konfigurieren" : "Configure" }).first().click();
  await page.waitForTimeout(2000);
  await page.getByText(screen.menu[lang], { exact: true }).first().click();
  await page.waitForTimeout(2500);

  if (mode === "real") {
    await shot(page, "form");
  } else {
    await shot(page, "1-picker");
    const dialog = await dialogRect(page);
    const value = page.getByText(realStep.description_placeholders.vehicle, { exact: true }).first();
    const box = await value.boundingBox();
    await page.mouse.click(dialog.x + dialog.width - 70, box.y + box.height / 2 - 8);
    await page.waitForTimeout(1000);
    const option = page.getByRole("option", { name: screen.second.name });
    if (await option.count()) await option.first().click();
    else {
      await page.keyboard.press("ArrowDown");
      await page.keyboard.press("Enter");
    }
    await page.waitForTimeout(800);
    await page.getByRole("button", { name: NEXT[lang] }).first().click();
    await page.waitForTimeout(2500);
    await shot(page, "2-car-form");
    if (!screen.expanded) {
      await page.getByText(lang === "de" ? "Alle Fahrzeuge" : "All vehicles", { exact: true }).first().click();
      await page.waitForTimeout(1000);
      await shot(page, "3-all-vehicles");
    }
  }
  await browser.close(); // abandons the flow; nothing was submitted to HA
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
