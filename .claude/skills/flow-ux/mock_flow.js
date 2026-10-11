// Stage a flow branch the live instance can't reach (here: the two-car Trips
// screens) in the real HA frontend. The options-flow REST calls are answered the
// way the integration would answer them; HA's own dialog renders the result
// with the installed build's translations. The staged submit never reaches
// Home Assistant, any other flow call is aborted, and closing the browser
// abandons the flow: nothing can be saved.
//
// Run from a directory with playwright-core installed (see the shoot skill):
//   HA_URL=http://<host> HA_TOKEN=<token> node mock_flow.js <lang> <width> <outdir>
// PICKER_ONLY=1 stops after the first screen. Adapt pickerStep/secondCarForm
// for another flow: start from the real step the server returns
// (real-trips-step.json) so the schema shape is HA's own.
const fs = require("fs");
const path = require("path");
const { chromium } = require(require.resolve("playwright-core", { paths: [process.cwd(), __dirname] }));

const URL = process.env.HA_URL;
if (!URL || !process.env.HA_TOKEN) throw new Error("set HA_URL and HA_TOKEN");
const EXE = path.join(process.env.LOCALAPPDATA, "ms-playwright", "chromium-1223", "chrome-win64", "chrome.exe");
const [lang, width, outdir] = process.argv.slice(2);
const MENU = { en: "Trips", de: "Fahrten" };
const SECOND = { vin: "WBADEMO0000000002", name: "530d Touring" };

let realTrips = null; // the server's own single-car action_trips step
let phase = "menu";

function pickerStep(base, firstVin, firstName) {
  return {
    ...base,
    step_id: "trips_vehicle",
    last_step: false,
    data_schema: [
      {
        name: "vehicle",
        required: true,
        default: firstVin,
        selector: {
          select: {
            options: [
              { value: firstVin, label: firstName },
              { value: SECOND.vin, label: SECOND.name },
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
  const shared = new Set(["trip_geocode", "trip_debug"]);
  const fields = base.data_schema.filter((field) => !shared.has(field.name));
  const sharedFields = base.data_schema.filter((field) => shared.has(field.name));
  // The older car: no work zone of its own yet, on end-of-trip reports.
  const car = fields.map((field) => {
    const copy = { ...field };
    if (copy.name === "trip_work_zone") delete copy.description;
    if (copy.name === "trip_detection") copy.default = "reports";
    if (copy.name === "trip_track") copy.default = false;
    if (copy.name === "trip_default_class") copy.default = "private";
    if (copy.name === "trip_commute_gap") copy.default = 30;
    return copy;
  });
  return {
    ...base,
    data_schema: [
      ...car,
      { type: "expandable", name: "all_vehicles", required: true, expanded: false, schema: sharedFields },
    ],
    description_placeholders: { vehicle: SECOND.name },
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

async function shot(page, name, pad = 0) {
  await page.waitForTimeout(1200);
  const rect = await dialogRect(page);
  if (!rect) throw new Error("no dialog for " + name);
  const clip = { x: rect.x, y: rect.y, width: rect.width, height: rect.height + pad };
  const file = path.join(outdir, `two-cars-${lang}-${width}-${name}.png`);
  await page.screenshot({ path: file, clip });
  console.log("saved", file);
}

(async () => {
  const browser = await chromium.launch({ executablePath: EXE });
  const context = await browser.newContext({ viewport: { width: Number(width), height: 1400 }, deviceScaleFactor: 2 });
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

  await page.route("**/api/config/config_entries/options/flow/*", async (route) => {
    const request = route.request();
    if (request.method() !== "POST") return route.continue();
    const body = JSON.parse(request.postData() || "{}");
    if (phase === "menu" && body.next_step_id === "action_trips") {
      // Let the real server open its (single-car) Trips step, keep it as the
      // template, and show the picker in its place.
      const response = await route.fetch();
      realTrips = await response.json();
      fs.writeFileSync(path.join(outdir, "real-trips-step.json"), JSON.stringify(realTrips, null, 2));
      phase = "picker";
      const firstName = realTrips.description_placeholders.vehicle;
      return route.fulfill({ json: pickerStep(realTrips, "WBAREAL0000000001", firstName) });
    }
    if (phase === "picker" && "vehicle" in body) {
      phase = "form";
      return route.fulfill({ json: secondCarForm(realTrips) });
    }
    // Anything else would reach the server: refuse it rather than risk a save.
    return route.abort();
  });

  await page.goto(URL + "/config/integrations/integration/bavariandata");
  await page.waitForFunction(() => document.querySelector("home-assistant")?.hass?.states, null, { timeout: 60000 });
  await page.waitForTimeout(3000);
  await page.getByRole("button", { name: lang === "de" ? "Konfigurieren" : "Configure" }).first().click();
  await page.waitForTimeout(2000);
  await page.getByText(MENU[lang], { exact: true }).first().click();
  await page.waitForTimeout(2500);
  await shot(page, "1-picker");
  if (process.env.PICKER_ONLY) { await browser.close(); return; }

  // Open the dropdown to show both cars.
  const firstName = realTrips.description_placeholders.vehicle;
  const value = page.getByText(firstName, { exact: true }).first();
  const box = await value.boundingBox();
  if (box) {
    const dialog = await dialogRect(page);
    // The arrow at the right end of the field opens the list.
    await page.mouse.click(dialog.x + dialog.width - 70, box.y + box.height / 2 - 8);
    await page.waitForTimeout(1200);
    await shot(page, "2-picker-open", 160);
    const option = page.getByRole("option", { name: SECOND.name });
    if (await option.count()) {
      await option.first().click();
    } else {
      await page.keyboard.press("ArrowDown");
      await page.keyboard.press("Enter");
    }
    await page.waitForTimeout(800);
    await shot(page, "2b-picked");
  }
  await page.getByRole("button", { name: lang === "de" ? "Weiter" : "Next" }).first().click();
  await page.waitForTimeout(2500);
  await shot(page, "3-car-form");

  // Expand "All vehicles".
  await page.getByText(lang === "de" ? "Alle Fahrzeuge" : "All vehicles", { exact: true }).first().click();
  await page.waitForTimeout(1200);
  await shot(page, "4-car-form-expanded");

  await browser.close(); // abandons the flow; nothing was submitted to HA
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
