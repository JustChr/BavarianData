/*
 * BavarianData Card: the car drawn from above: tires, closures, equipment.
 *
 * One part of the card class. bavariandata-card.js loads it from beside itself
 * (see CARD_PARTS there) and mixes it into BavarianDataCard; what it takes from
 * the card's own scope arrives in SHARED.
 */

export default (Base, { CAR, CAR_FLIP, UNAVAILABLE, _lang, carTones }) => class CarPart extends Base {
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

  /* ---- the car: closures, tires, messages, service, health, lease ------ */

  /** Everything about the car itself in one card: the top-down drawing with
   * what is open lit up and each tire's state on its wheel, the four pressures
   * at its corners (tap one for that tire's detail), then a line each for the
   * doors and lock, Check Control, service, the 12 V battery, battery health
   * and the mileage allowance. Each line only where the car has the data. */
  _renderCar(deviceId, entities) {
    const vin = this._deviceVin(deviceId);
    if (!vin) {
      this._renderMessage(this._t("no_vehicle_title"), this._t("no_vehicle_body"));
      return;
    }
    // The allowance comes from get_mileage_report, the mileage view's cache.
    this._ensureMileage(vin, this._deviceEntry(deviceId), entities);
    this._paintCar(deviceId, entities);
  }

  _paintCar(deviceId, entities) {
    const name = this._config.title || this._deviceName(deviceId);
    const unit = this._distUnit(deviceId, entities);
    const m = this._closureModel(entities);
    const wheels = {};
    for (const id of entities) {
      const st = this._st(id);
      const a = st && st.attributes;
      if (!a || a.cluster !== "tire" || !a.tire_axle || !a.tire_side) continue;
      const key = `${a.tire_axle}_${a.tire_side}`;
      (wheels[key] = wheels[key] || {})[a.tire_metric || "other"] = st;
    }
    const slots = [
      { key: "row1_left", pos: "fl", label: this._t("fl"), full: this._t("front_left") },
      { key: "row1_right", pos: "fr", label: this._t("fr"), full: this._t("front_right") },
      { key: "row2_left", pos: "rl", label: this._t("rl"), full: this._t("rear_left") },
      { key: "row2_right", pos: "rr", label: this._t("rr"), full: this._t("rear_right") },
    ];
    const present = slots.filter((s) => wheels[s.key]);
    const tires = present.length ? this._tireSummary(present.map((s) => wheels[s.key]), unit) : null;
    const status = Object.fromEntries(slots.map((s) => [s.pos, wheels[s.key] ? this._tireStatus(wheels[s.key]) : null]));
    const selected = this._carWheel && wheels[(slots.find((s) => s.pos === this._carWheel) || {}).key] ? this._carWheel : null;

    const cc = this._checkControl(entities);
    const service = this._serviceItems(entities);
    const v12 = this._st(
      this._idByDescriptor(entities, "vehicle.electricalSystem.battery.stateOfCharge") ||
        this._idByDescriptor(entities, "vehicle.electricalSystem.battery.voltage")
    );
    const health = this._drivetrain(entities) === "ice" ? null : this._byDescriptor(entities, "battery_health");
    const allowance = this._mil && this._mil.data && this._mil.data.allowance;

    const sig = this._signature({
      m: "car",
      lang: _lang(this._hass),
      name,
      unit,
      paint: this._carPaint(entities),
      open: m.openItems.map((i) => i.id),
      lock: m.lock && m.lock.key,
      w: Object.fromEntries(
        Object.entries(wheels).map(([k, w]) => [k, Object.entries(w).map(([mk, s]) => (mk === "diagnosis" ? this._wearSig(s) : s.state))])
      ),
      selected,
      cc: cc && [cc.items.length, cc.resolved.length],
      service,
      v12: v12 && v12.state,
      health: health && [health.state, (health.attributes || {}).samples],
      allowance,
    });
    if (sig === this._sig) return;
    this._sig = sig;

    // The banner: the most pressing thing first, or that all is well.
    const ccCount = cc ? cc.items.length : 0;
    let banner;
    if (m.openItems.length) {
      banner = { tone: "alert", icon: "mdi:car-door", t: this._t("vd_open"), s: m.openItems.slice(0, 3).map((i) => this._esc(i.label)).join(" · ") };
    } else if (tires && (tires.overall.c === "var(--bmw-low)" || tires.overall.c === "var(--bmw-mid)")) {
      banner = { tone: "warn", icon: "mdi:tire", t: this._esc(tires.overall.t), s: tires.pressure.t };
    } else if (ccCount) {
      banner = { tone: "warn", icon: "mdi:alert-circle-outline", t: this._t("car_cc_n", { n: ccCount }), s: this._esc(this._ccField(cc.items[0].title) || this._ccField(cc.items[0].text) || "") };
    } else {
      const parts = [
        m.lockId && m.lock.key !== "unlocked" ? this._t("car_closed") : null,
        tires ? this._t("car_tires_ok") : null,
        cc ? this._t("car_no_msgs") : null,
      ].filter(Boolean);
      banner = { tone: "ok", icon: "mdi:check-circle-outline", t: this._t("car_all_good"), s: parts.join(" · ") };
    }

    const colors = { fl: status.fl && status.fl.color, fr: status.fr && status.fr.color, rl: status.rl && status.rl.color, rr: status.rr && status.rr.color };
    const drawing = this._carSvgClosures({
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
      wheelColors: colors,
    });
    const corner = (slot) => {
      const w = wheels[slot.key];
      if (!w) return `<span class="carv__tp carv__tp--${slot.pos}"></span>`;
      const pv = w.pressure ? this._splitValueUnit(w.pressure) : { value: "—", unit: "" };
      const st = status[slot.pos];
      return `<button class="carv__tp carv__tp--${slot.pos}${selected === slot.pos ? " is-sel" : ""}" data-car-wheel="${slot.pos}" aria-pressed="${selected === slot.pos}" style="--c:${st.color}">
          <span class="carv__tp-v">${pv.value}${pv.unit ? `<i>${pv.unit}</i>` : ""}</span>
          <span class="carv__tp-l">${slot.full} · ${st.label}</span>
        </button>`;
    };
    const sel = selected ? slots.find((s) => s.pos === selected) : null;

    const lines = [];
    const line = (dot, title, sub, extra = "", entity = "") =>
      lines.push(`<button class="carv__line" data-entity="${this._attr(entity)}">
          <span class="carv__dot" style="background:${dot}"></span>
          <span class="carv__lt"><span class="carv__t">${title}</span>${sub ? `<span class="carv__s">${sub}</span>` : ""}${extra}</span>
        </button>`);
    if (m.present) {
      const open = m.openItems.length;
      line(
        open ? m.ALERT : m.lock && m.lock.key === "unlocked" ? m.WARN : m.OK,
        this._t("car_doors"),
        open ? m.openItems.map((i) => `${this._esc(i.label)}: ${this._esc(i.value)}`).join(" · ") : [this._t("car_all_closed"), m.lockId ? this._esc(m.lock.label) : null].filter(Boolean).join(" · "),
        "",
        open ? m.openItems[0].id : m.lockId
      );
    }
    if (cc) {
      line(
        ccCount ? m.WARN : m.OK,
        this._t("cc_title"),
        ccCount
          ? cc.items.map((msg) => this._esc(this._ccField(msg.title) || this._ccField(msg.text) || "—")).join(" · ")
          : [this._t("car_no_msgs_full"), cc.resolved.length ? this._t("cc_earlier", { n: cc.resolved.length }) : null].filter(Boolean).join(" · "),
        "",
        cc.id
      );
    }
    if (service && service.items.length) {
      const worst = service.items.some((i) => i.status === "OVERDUE") ? m.ALERT : service.items.some((i) => i.status === "PENDING") ? m.WARN : "var(--bmw-charge)";
      line(
        worst,
        this._t("car_service"),
        service.items
          .slice(0, 3)
          .map((i) => [i.title, i.month ? this._t("car_by", { month: i.month }) : null, i.km != null ? this._t("car_in", { d: this._distance(i.km, unit) }) : null].filter(Boolean).join(" · "))
          .join("<br>"),
        "",
        service.id
      );
    }
    if (v12 && !UNAVAILABLE.has(v12.state)) line(m.OK, this._t("ti_battery_12v"), this._fmt(v12), "", v12.entity_id);
    if (health && !UNAVAILABLE.has(health.state)) {
      const a = health.attributes || {};
      // Learning until the estimate is confident; the state then reads
      // learning_<n> (shown as "Learning (n/10)").
      const learning = a.confident === false || String(health.state).startsWith("learning");
      const n = Number(a.samples) || 0;
      const need = Number(a.samples_needed) || 10;
      line(
        learning ? "var(--divider-color)" : m.OK,
        this._t("bh_title"),
        learning
          ? this._t("bh_learning", { n, total: need })
          : [this._fmt(health), a.vs_new_percent != null ? this._t("bh_of_new", { p: this._dec(a.vs_new_percent, 0) }) : null].filter(Boolean).join(" · "),
        learning ? `<span class="thin"><span style="width:${Math.min(100, (n / need) * 100)}%;background:var(--bmw-charge)"></span></span>` : "",
        health.entity_id
      );
    }
    if (allowance && allowance.allowance_km && allowance.status !== "not_started" && allowance.status !== "unknown") {
      const dist = (v) => this._distance(v, unit);
      const total = allowance.allowance_km;
      const used = allowance.used_km || 0;
      const plan = allowance.plan_km || 0;
      const bal = allowance.balance_km || 0;
      line(
        bal < 0 ? m.WARN : m.OK,
        this._esc(allowance.kind === "lease" ? this._t("mi_lease", { date: this._isoDay(allowance.end) }) : this._t("mi_yearly", { date: this._isoDay(allowance.end) })),
        [
          this._t("mi_of", { used: dist(used), total: dist(total) }),
          Math.abs(bal) < 1 ? this._t("mi_on_plan") : bal > 0 ? this._t("mi_under", { d: dist(bal) }) : this._t("mi_over", { d: dist(-bal) }),
        ].join(" · "),
        `<span class="thin"><span style="width:${Math.min(100, (used / total) * 100)}%;background:var(--bmw-charge)"></span><span class="thin__tick" style="left:${Math.min(100, (plan / total) * 100)}%"></span></span>`
      );
    }

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        <div class="chead">
          <ha-icon icon="mdi:car-info"></ha-icon>
          <div class="chead__text">
            <span class="chead__title">${this._esc(this._config.title || this._t("car_title"))}</span>
            <span class="chead__sub">${this._esc(name)}</span>
          </div>
        </div>
        <div class="carv__banner carv__banner--${banner.tone}">
          <ha-icon icon="${banner.icon}"></ha-icon>
          <span><span class="carv__bt">${banner.t}</span>${banner.s ? `<span class="carv__bs">${banner.s}</span>` : ""}</span>
        </div>
        <div class="carv__draw">
          ${corner(slots[0])}${corner(slots[1])}
          <div class="carv__svg">${drawing}</div>
          ${corner(slots[2])}${corner(slots[3])}
        </div>
        ${sel ? `<div class="carv__wheel">${this._wheelBlock(sel, wheels[sel.key], sel.pos, unit)}</div>` : present.length ? `<p class="carv__hint">${this._t("car_tap_tire")}</p>` : ""}
        <div class="carv__lines">${lines.join("")}</div>
      </ha-card>`;
    this._wireTaps();
    this.shadowRoot.querySelectorAll("[data-car-wheel]").forEach((el) => {
      el.addEventListener("click", () => {
        const pos = el.getAttribute("data-car-wheel");
        this._carWheel = this._carWheel === pos ? null : pos;
        this._sig = null;
        this._paintCar(deviceId, entities);
      });
    });
  }

  /** The car's service items (brake fluid, vehicle check, inspection), soonest
   * first: title, due month, distance left and BMW's status. */
  _serviceItems(entities) {
    const st = this._byDescriptor(entities, "vehicle.status.conditionBasedServices");
    if (!st) return null;
    const raw = Array.isArray((st.attributes || {}).items) ? st.attributes.items : [];
    const field = (v) => this._ccField(v);
    const items = raw
      .filter((i) => i && typeof i === "object")
      .map((i) => {
        const date = field(i.date);
        // BMW's "-" is no distance; Number(null) would read it as 0 km.
        const rawKm = field(i.unitOfLengthRemaining);
        const km = rawKm == null ? NaN : Number(rawKm);
        let month = null;
        if (date && /^\d{4}-\d{2}/.test(date)) {
          const [y, mo] = date.slice(0, 7).split("-").map(Number);
          try {
            month = new Date(y, mo - 1, 1).toLocaleDateString(_lang(this._hass), { month: "short", year: "numeric" });
          } catch (_e) {
            month = date.slice(0, 7);
          }
        }
        return {
          title: this._esc(field(i.title) || field(i.text) || field(i.type) || "—"),
          month: month ? this._esc(month) : null,
          km: Number.isFinite(km) && km >= 0 ? km : null,
          status: String(i.status || "").toUpperCase(),
          sort: date || "9999",
        };
      })
      .sort((a, b) => (a.sort < b.sort ? -1 : a.sort > b.sort ? 1 : 0));
    return { id: st.entity_id, items };
  }

  /** Everything the car says about its doors, windows, lids, lock and alarm,
   * read once for the closures view and for the badges on the overview's photo. */
  _closureModel(entities) {
    const P = CarPart.CLOSURE_PATHS;
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
    // The looks: paint, wheels, trim and the upholstery (BMW's "fabric").
    const LOOKS = ["paint", "rim", "trim", "fabric"];
    const looks = items.filter((e) => e.shown !== false && LOOKS.includes(e.kind));
    const packages = items.filter((e) => e.shown !== false && e.kind === "package");
    const rest = items.filter((e) => e.shown !== false && !LOOKS.includes(e.kind) && e.kind !== "package");
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
        ${this._carWheels(car, d.wheelColors || {})}
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
};
