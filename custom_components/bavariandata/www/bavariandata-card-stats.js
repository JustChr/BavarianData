/*
 * BavarianData Card: the health, efficiency, fuel and mileage views.
 *
 * One part of the card class. bavariandata-card.js loads it from beside itself
 * (see CARD_PARTS there) and mixes it into BavarianDataCard; what it takes from
 * the card's own scope arrives in SHARED.
 */

export default (Base, { DESC_ODOMETER, FUEL_DESCRIPTORS, KM_PER_MILE, _lang }) => class StatsPart extends Base {
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
    if (!current || this._wantsFetch(cache, "data")) {
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
        this._eff = { ...this._eff, loading: false, error: true, failedAt: Date.now() };
        this._retryLater();
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
    const tempUnit = this._tempUnit(deviceId);
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
      temps: profile && profile.by_temperature,
      tempUnit,
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
      body = this._efBody(f, trend, profile || {}, unit, tempUnit);
    }

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        <div class="chead">
          ${this._cheadIcon("mdi:map-marker-distance")}
          <div class="chead__text">
            <span class="chead__title">${this._esc(this._config.title || this._t("ef_title"))}</span>
            <span class="chead__sub">${this._esc(name)}</span>
          </div>
        </div>
        ${body}
      </ha-card>
    `;
  }

  _efBody(f, trend, profile, unit, tempUnit = "c") {
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
        ${this._efCompare(f, profile.by_temperature, unit, tempUnit)}
        <div class="ef__facts">${factRow}</div>
        ${
          chart
            ? `<div class="ef__trend"><span class="ef__trend-title">${this._t(
                "ef_trend_title"
              )}</span>${chart}</div>`
            : ""
        }
        ${this._efTemperature(profile.by_temperature, unit, tempUnit)}
        <span class="ef__note">${this._t("ef_footnote")}</span>
      </div>
    `;
  }

  /** The real range against the car's own estimate and, where the trips'
   * weather says it, against what the same charge would go in mild weather
   * (15-25 degrees). Bars on one scale, so the gap is seen, not just read. */
  _efCompare(f, byTemp, unit, tempUnit) {
    const now = f.nowKm != null ? f.nowKm : f.fullKm;
    if (now == null) return "";
    const rows = [["ef_cmp_you", now, "var(--bmw-charge)"]];
    if (f.bmwKm != null) rows.push(["ef_cmp_bmw", f.bmwKm, "var(--bd-grid)"]);
    const mild = byTemp && byTemp.status === "ok" ? byTemp.mild_kwh_per_100km : null;
    if (mild > 0 && f.capacity != null) {
      const full = (f.capacity / mild) * 100;
      rows.push(["ef_cmp_mild", f.nowKm != null && f.soc != null ? (full * f.soc) / 100 : full, "color-mix(in srgb, var(--bd-grid) 50%, transparent)"]);
    }
    if (rows.length < 2) return "";
    const max = Math.max(...rows.map((r) => r[1])) || 1;
    const mildLabel = `${this._dec(tempUnit === "f" ? 59 : 15, 0)}–${this._temp(25, tempUnit)}`;
    return `<div class="cmp" role="img" aria-label="${this._esc(
      rows.map(([k, v]) => `${this._t(k, { mild: mildLabel })} ${this._distance(v, unit)}`).join(", ")
    )}">${rows
      .map(
        ([k, v, c], i) =>
          `<div class="cmp__row${i ? " cmp__row--quiet" : ""}"><span class="cmp__lbl">${this._t(k, {
            mild: mildLabel,
          })}</span><span class="cmp__track"><span style="width:${((v / max) * 100).toFixed(1)}%;background:${c}"></span></span><span class="cmp__v">${this._distance(
            v,
            unit
          )}</span></div>`
      )
      .join("")}</div>`;
  }

  /** Consumption per outside-temperature band (opt-in route weather), and how
   * much more the car uses in the cold. Nothing at all until a trip has its
   * weather: the switch is off by default, and an empty chart would nag. */
  _efTemperature(byTemp, unit, tempUnit) {
    if (!byTemp || byTemp.status !== "ok" || !Array.isArray(byTemp.bands)) return "";
    // Empty bands are trimmed off the ends only: one missing in the middle
    // stays as a stub, or the axis would skip it without saying so.
    const used = byTemp.bands.map((b) => b.km > 0);
    const first = used.indexOf(true);
    if (first < 0) return "";
    const bands = byTemp.bands.slice(first, used.lastIndexOf(true) + 1);
    const edge = (c) => this._dec(tempUnit === "f" ? c * 1.8 + 32 : c, 0);
    const label = (b) =>
      b.from_c == null ? `< ${edge(b.to_c)}` : b.to_c == null ? `≥ ${edge(b.from_c)}` : `${edge(b.from_c)}–${edge(b.to_c)}`;
    const W = 280;
    const H = 88;
    const pad = 6;
    const bottom = 14;
    const values = bands.map((b) => (b.kwh_per_100km == null ? null : this._perDist(b.kwh_per_100km, unit)));
    // Headroom for the value printed over the tallest bar, clear of the unit.
    const max = Math.max(...values.filter((v) => v != null), 0) * 1.3 || 1;
    const slot = (W - 2 * pad) / bands.length;
    const width = Math.min(28, Math.max(4, slot * 0.58));
    const bars = bands
      .map((b, i) => {
        const x = pad + i * slot + (slot - width) / 2;
        // A band with too few km behind it gets a stub, not a figure.
        const h = values[i] == null ? 2 : (values[i] / max) * (H - bottom - pad);
        const y = H - bottom - h;
        const cls = values[i] == null ? "ef__bar ef__bar--thin" : "ef__bar";
        return `<rect class="${cls}" x="${this._px(x)}" y="${this._px(y)}" width="${this._px(
          width
        )}" height="${this._px(Math.max(h, 1))}" rx="2"></rect>
                ${values[i] == null ? "" : this._efBarValue(x + width / 2, y, values[i])}
                <text class="ef__bar-lbl" x="${this._px(x + width / 2)}" y="${H - 3}" text-anchor="middle">${this._esc(
          label(b)
        )}</text>`;
      })
      .join("");
    // Mild weather as a dashed line across: each band reads against it.
    const mildV = byTemp.mild_kwh_per_100km == null ? null : this._perDist(byTemp.mild_kwh_per_100km, unit);
    const mildY = mildV == null ? null : H - bottom - (mildV / max) * (H - bottom - pad);
    const chart = `
      <svg class="ef__chart" viewBox="0 0 ${W} ${H}" role="img">
        ${bars}
        ${mildY == null ? "" : `<line class="chg__chart-avg" x1="${pad}" x2="${W - pad}" y1="${this._px(mildY)}" y2="${this._px(mildY)}"></line>`}
        <text x="${pad}" y="9" class="chg__chart-max">${this._kwhPer(unit)}</text>
      </svg>`;
    const cold = this._temp(5, tempUnit);
    const mild = `${edge(15)}–${this._temp(25, tempUnit)}`;
    const p = byTemp.cold_penalty_percent;
    const line =
      p == null
        ? this._t("ef_temp_waiting", { cold, mild, d: unit === "mi" ? "60 mi" : "100 km" })
        : this._t(p >= 0 ? "ef_temp_more" : "ef_temp_less", { p: this._dec(Math.abs(p), 0), cold, mild });
    return `<div class="ef__trend"><span class="ef__trend-title">${this._t("ef_temp_title", {
      u: tempUnit === "f" ? "°F" : "°C",
    })}</span>${chart}<span class="ef__fact-note">${line}</span></div>`;
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
    const max = Math.max(...values) * 1.3 || 1;
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
                ${this._efBarValue(x + width / 2, y, values[i])}
                <text class="ef__bar-lbl" x="${this._px(
                  x + width / 2
                )}" y="${H - 3}" text-anchor="middle">${this._esc(label)}</text>`;
      })
      .join("");
    return `
      <svg class="ef__chart" viewBox="0 0 ${W} ${H}" role="img">
        ${bars}
        <text x="${pad}" y="9" class="chg__chart-max">${this._kwhPer(unit)}</text>
      </svg>
    `;
  }

  /** A bar's own figure, just above it; the unit stands once in the corner. */
  _efBarValue(cx, top, value) {
    return `<text class="ef__bar-val" x="${this._px(cx)}" y="${this._px(
      top - 3
    )}" text-anchor="middle">${this._dec(value, 1)}</text>`;
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
    this._ensureFuel(vin, this._deviceEntry(deviceId), entities);
    this._paintFuel(deviceId, this._distUnit(deviceId, entities), this._fuelUnit(deviceId, entities));
  }

  /** Fetch get_fuel_history when the tank reading changes and every ten
   * minutes; shared by the fuel view and the hub's Fuel row. */
  _ensureFuel(vin, entryId, entities) {
    const tank = FUEL_DESCRIPTORS.map((d) => this._byDescriptor(entities, d)).find(Boolean);
    // A refuel can only appear with a new tank reading, so that is what
    // re-asks; the ten-minute tick keeps "since the last refuel" moving.
    const trigger = [tank ? tank.state : "", Math.floor(Date.now() / 600000)].join("|");
    const cache = this._fuel;
    const current = cache && cache.vin === vin && cache.trigger === trigger;
    if (!current || this._wantsFetch(cache, "data")) {
      this._fuel = {
        vin,
        trigger,
        data: cache && cache.vin === vin ? cache.data : null,
        loading: true,
        error: false,
      };
      this._fetchFuel(vin, entryId);
    }
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
        this._fuel = { ...this._fuel, loading: false, error: true, failedAt: Date.now() };
        this._retryLater();
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
          ${this._cheadIcon("mdi:gas-station")}
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
    this._ensureMileage(vin, this._deviceEntry(deviceId), entities);
    this._paintMileage(deviceId, this._distUnit(deviceId, entities));
  }

  /** Fetch get_mileage_report when the odometer moves, on a new day, and every
   * ten minutes; shared by the mileage and car views. */
  _ensureMileage(vin, entryId, entities) {
    const odo = DESC_ODOMETER.map((d) => this._byDescriptor(entities, d)).find(Boolean);
    const trigger = [
      odo ? odo.state : "",
      this._monthKey(),
      new Date().getDate(),
      Math.floor(Date.now() / 600000),
    ].join("|");
    const cache = this._mil;
    const current = cache && cache.vin === vin && cache.trigger === trigger;
    if (!current || this._wantsFetch(cache, "data")) {
      this._mil = {
        vin,
        trigger,
        data: cache && cache.vin === vin ? cache.data : null,
        loading: true,
        error: false,
      };
      this._fetchMileage(vin, entryId);
    }
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
        // An answer without a body is still an answer: stored as null it read as
        // "never fetched" and asked again at once, in a loop.
        const data = (res && res.response) || {};
        this._mil = { ...this._mil, data, loading: false, error: false };
        this._render();
      })
      .catch(() => {
        if (!this._mil || this._mil.vin !== vin || this._mil.trigger !== req.trigger) return;
        this._mil = { ...this._mil, loading: false, error: true, failedAt: Date.now() };
        this._retryLater();
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
};
