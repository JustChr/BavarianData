/*
 * BavarianData Card: the overview: hero render, 360° spin, interior panorama, tiles.
 *
 * One part of the card class. bavariandata-card.js loads it from beside itself
 * (see CARD_PARTS there) and mixes it into BavarianDataCard; what it takes from
 * the card's own scope arrives in SHARED.
 */

export default (Base, { DEVICE_CLASS_ICONS, UNAVAILABLE, _lang }) => class OverviewPart extends Base {
  _renderOverview(deviceId, entities) {
    const cfg = this._config || {};
    const drivetrain = this._drivetrain(entities);
    const ice = drivetrain === "ice";
    const phev = drivetrain === "phev";
    const picks = this._overviewEntities(entities, this._deviceName(deviceId));
    const byDesc = (descriptor) => this._idByDescriptor(entities, descriptor);
    const name = cfg.title || this._deviceName(deviceId);
    const unit = this._distUnit(deviceId, entities);
    const trip = this._openTrip(entities);
    const socSt = this._st(picks.soc);
    const chargingSt = this._st(picks.charging);
    const odometerSt = this._st(picks.odometer);

    // What the meter and the tiles show follows the drivetrain. An electric car
    // bars its charge; a plug-in hybrid adds its tank as a second line; a petrol
    // or diesel car bars its tank and drops everything about charging -- BMW
    // streams an EV charge target even to a petrol M2, where "Target 0 %" means
    // nothing.
    const fuelId =
      cfg.fuel ||
      byDesc("vehicle.drivetrain.fuelSystem.level") ||
      byDesc("vehicle.drivetrain.fuelSystem.remainingFuel");
    const fuelSt = this._st(fuelId);
    // A car that proved no drivetrain is laid out like a combustion one -- no
    // charging, nothing electric -- except that it has no tank either, so its
    // meter is its range and the side shows what it does have. So does a
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
    const electric = !ice && !bare;
    const charging = electric && this._isCharging(chargingSt, socSt);
    const plugSt = electric ? this._st(picks.plug) : null;
    const plugged = charging || this._isPlugged(plugSt);
    // A target of 0 is no target: what BMW sends when none is set.
    const targetNum = electric ? this._num(this._st(picks.target)) : null;
    const target = targetNum != null && targetNum > 0 ? targetNum : null;
    const session = electric ? this._sessionInfo(entities) : null;
    const closures = this._closureModel(entities);

    // The meter: state of charge, or the tank on a combustion car -- as a fill
    // level where the car streams a percentage, otherwise its volume with no
    // bar (a fill level can't be drawn from litres without the tank size). A
    // car that proved no drivetrain has neither, so its meter is the one
    // headline number it does have: how far it can still go.
    const gaugeId = bare ? rangeId : ice ? fuelId : picks.soc;
    const gaugeSt = this._st(gaugeId);
    const gaugeNum = this._num(gaugeSt);
    const gaugeUnit = (gaugeSt && gaugeSt.attributes && gaugeSt.attributes.unit_of_measurement) || "";
    const isPct = electric || gaugeUnit === "%";
    const pct = isPct && gaugeNum != null ? Math.max(0, Math.min(100, gaugeNum)) : 0;
    const fillColor = charging
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

    // What the charge (or the drive) has done to the bar so far.
    let gain = null;
    let used = null;
    if (electric && gaugeNum != null) {
      if (charging && session.socStart != null && pct > session.socStart) {
        gain = [session.socStart, pct - session.socStart];
      }
      if (trip && trip.soc_start != null && trip.soc_now != null && trip.soc_start > trip.soc_now) {
        used = [Math.max(0, trip.soc_now), Math.min(100, trip.soc_start) - Math.max(0, trip.soc_now)];
      }
    }
    let legL = "";
    let legR = "";
    if (electric) {
      if (charging) {
        legL = gain && session.start
          ? this._t("ov_since", { n: Math.round(gain[1]), time: this._clock(session.start) })
          : "";
      } else if (used) {
        legL = this._t("ov_trip_used", { n: Math.round(used[1]) });
      } else if (plugSt && !UNAVAILABLE.has(plugSt.state)) {
        legL = this._t(plugged ? "ov_plugged_idle" : "ov_unplugged");
      }
      if (target != null) legR = this._t("ov_target", { n: Math.round(target) });
    } else if (ice && litresSt) {
      legL = this._t("ov_tank_left", { v: this._fmt(litresSt) });
    }

    // Beside the big number: the range, with what the car really does on a
    // battery car; a bare car shows its odometer there instead.
    const realSt = electric && !phev ? this._byDescriptor(entities, "real_range") : null;
    const side = bare
      ? { entity: picks.odometer, val: this._fmt(odometerSt), cap: this._t("odometer") }
      : {
          entity: rangeId,
          val: this._fmt(rangeSt),
          cap:
            realSt && this._num(realSt) != null
              ? this._t("ov_real", { v: this._fmt(realSt) })
              : this._t(phev ? "electric_range" : "remaining_range"),
        };
    // A plug-in hybrid's tank, as a thin second line under the battery.
    const tankNum = phev ? this._num(fuelSt) : null;
    const tank =
      phev && fuelSt
        ? {
            entity: fuelId,
            val: this._fmt(fuelSt),
            pct: tankNum != null && (fuelSt.attributes || {}).unit_of_measurement === "%" ? Math.max(0, Math.min(100, tankNum)) : null,
            total: this._fmt(this._st(byDesc("vehicle.drivetrain.lastRemainingRange"))),
          }
        : null;

    const verdict = this._verdict({
      ice, bare, electric, charging, plugged, plugSt, target, trip, unit, closures, session,
      gaugeNum: isPct ? gaugeNum : null,
      rangeSt,
      realSt,
      zone: this._st(entities.find((id) => id.startsWith("device_tracker."))),
      timeToFullSt: this._st(picks.timeToFull),
      ids: { timeToFull: picks.timeToFull, charging: picks.charging, gauge: gaugeId, plug: picks.plug },
    });

    // `tiles:` replaces the grid whole, in the order given. Without it the grid
    // follows what the car is doing: the four first of the situation's list
    // that the car has data for.
    const own = Array.isArray(cfg.tiles) && cfg.tiles.length;
    const wanted = own ? cfg.tiles : this._situationTiles({ ice, bare, electric, charging, trip, open: verdict.key === "open" });
    const tileCtx = { entities, picks, fuelSt, odometerSt, charging, session, trip, unit, closures, name: this._deviceName(deviceId) };
    let secondary = wanted.map((item) => this._overviewTile(item, tileCtx)).filter((m) => m && m.st);
    if (!own) secondary = secondary.slice(0, 4);

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
    const inset = spinInfo && !inside ? this._heroInset(closures, entities) : "";
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
      inset: inset.length,
      fresh: freshest,
      verdict,
      meter: [gaugeId, gaugeVal, gaugeUnitHtml, pct, fillColor, gaugeCap, gain, used, legL, legR],
      side,
      tank,
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
    // Only over the configurator photo: the drawing of what is open and the
    // interior switch. Empty otherwise, so the plain hero's markup is unchanged.
    const heroExtras =
      (inset ? `<div class="hero__inset">${inset}</div>` : "") +
      (pano
        ? `<button class="hero__mode" data-hero-mode title="${this._esc(this._t(inside ? "hs_exterior" : "hs_interior"))}"><ha-icon icon="${inside ? "mdi:car-side" : "mdi:car-seat"}"></ha-icon></button>`
        : "");
    const bar = isPct && gaugeNum != null
      ? this._barHtml({ pct, color: fillColor, charging, gain, used, tick: target, label: `${gaugeCap} ${gaugeVal} %` })
      : "";

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        <div class="hero ${imgUrl || spinInfo ? "" : "hero--empty"}${inset ? " hero--inset" : ""}${spin && spin.variant === "night" ? " hero--night" : ""}">
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
          ${this._tripPill(trip, unit)}${heroExtras}
        </div>

        <button class="verdict" data-entity="${this._esc(verdict.entity || "")}">
          <span class="verdict__t verdict__t--${verdict.tone}">${verdict.title}</span>
          ${verdict.sub ? `<span class="verdict__s">${verdict.sub}</span>` : ""}
        </button>

        <div class="meter">
          <div class="meter__row">
            <button class="meter__main" data-entity="${gaugeId || ""}">
              <span class="meter__big">${gaugeVal}<i>${gaugeUnitHtml}</i></span>
              <span class="meter__cap">${gaugeCap}</span>
            </button>
            <button class="meter__side" data-entity="${side.entity || ""}">
              <span class="meter__mid">${side.val}</span>
              <span class="meter__cap">${side.cap}</span>
            </button>
          </div>
          ${bar}
          ${legL || legR ? `<div class="bar__legend"><span>${legL}</span><span>${legR}</span></div>` : ""}
          ${
            tank
              ? `<button class="meter__tank" data-entity="${tank.entity || ""}">
              <ha-icon icon="mdi:gas-station"></ha-icon>
              <span class="meter__tank-body">
                <span class="meter__tank-row"><span><b>${tank.val}</b> ${this._t("tank")}</span><span>${this._t("ov_together", { v: tank.total })}</span></span>
                ${tank.pct != null ? `<span class="thin"><span style="width:${tank.pct}%"></span></span>` : ""}
              </span>
            </button>`
              : ""
          }
        </div>

        ${
          secondary.length
            ? `<div class="grid">
                ${secondary
                  .map(
                    (m) => `
                  <button class="cell${m.tone ? ` cell--${m.tone}` : ""}" data-entity="${this._esc(m.st.entity_id)}">
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

  /** The bar under the big number: the level, what a charge has added (striped)
   * or a drive has used (faint) so far, and the target as a tick. Shared by
   * every view that bars a charge. */
  _barHtml({ pct, color, charging, gain, used, tick, label }) {
    const at = (n) => Math.max(0, Math.min(100, Number(n)));
    return `<div class="bar" role="img" aria-label="${this._esc(label || "")}">
            <span class="bar__fill${charging ? " bar__fill--charging" : ""}" style="width:${at(pct)}%;background:${color}"></span>
            ${gain ? `<span class="bar__gain" style="left:${at(gain[0])}%;width:${at(gain[1])}%"></span>` : ""}
            ${used ? `<span class="bar__used" style="left:${at(used[0])}%;width:${at(used[1])}%;background:${color}"></span>` : ""}
            ${tick != null ? `<span class="bar__tick" style="left:${at(tick)}%"></span>` : ""}
          </div>`;
  }

  /** The charge in progress, as the session sensor tells it: when it began, at
   * which charge, and its cost and solar share so far (each null when unknown).
   * The attributes only exist while a charge runs. */
  _sessionInfo(entities) {
    const st = this._byDescriptor(entities, "charged_energy_session");
    const a = (st && st.attributes) || {};
    const num = (v) => (v == null || v === "" || !Number.isFinite(Number(v)) ? null : Number(v));
    return {
      st,
      start: a.last_reset || null,
      socStart: num(a.soc_start),
      cost: num(a.cost_so_far) == null ? null : { amount: num(a.cost_so_far), currency: a.currency },
      solar: num(a.solar_percent),
    };
  }

  /** A time of day as the user's profile writes it. */
  _clock(iso) {
    const d = new Date(iso);
    if (!Number.isFinite(d.getTime())) return "";
    const opts = { hour: "2-digit", minute: "2-digit" };
    const format = this._hass && this._hass.locale && this._hass.locale.time_format;
    if (format === "12" || format === "24") opts.hour12 = format === "12";
    try {
      return this._esc(d.toLocaleTimeString(_lang(this._hass), opts));
    } catch (_e) {
      return this._esc(d.toISOString().slice(11, 16));
    }
  }

  /** The line on top of the overview: what the car is doing, in words, and the
   * one or two facts behind it. First match wins:
   *   something open (not while driving) -> unlocked while parked -> charging
   *   -> on the road -> plugged in below its target -> low -> ready.
   * An open door outranks a charge: left open overnight on the drive is
   * exactly what must not hide behind "done at 05:40". Merely unlocked does
   * not; plenty of cars charge unlocked in a garage.
   * (A refused login never gets here: the card says so instead.) `title` and
   * `sub` come back escaped; `tone` is ok, live, warn or alert. */
  _verdict(c) {
    const join = (parts) => parts.filter(Boolean).join(" · ");
    const m = c.closures;
    const unlocked = !!(m.lockId && m.lock.key === "unlocked");
    const zone = c.zone && !UNAVAILABLE.has(c.zone.state) ? this._fmt(c.zone) : null;
    if (!c.trip && (m.openItems.length || (unlocked && !c.charging))) {
      const open = m.openItems.slice(0, 2).map((i) => this._esc(i.label));
      if (m.openItems.length > 2) open.push(this._t("ov_more", { n: m.openItems.length - 2 }));
      return {
        key: "open",
        tone: "alert",
        entity: m.openItems.length ? m.openItems[0].id : m.lockId,
        title: this._t(m.openItems.length ? "vd_open" : "vd_unlocked"),
        sub: join([...open, unlocked && m.openItems.length ? this._esc(m.lock.label) : null, zone]),
      };
    }
    if (c.charging) {
      const done = this._doneAt(c.timeToFullSt);
      return {
        key: "charging",
        tone: "live",
        // The minutes left are a tap away; the line itself says the clock time.
        entity: c.ids.timeToFull || c.ids.charging,
        title: done ? this._t("vd_charging_done", { time: done }) : this._t("vd_charging"),
        sub: join([
          c.session && c.session.start ? this._t("vs_since", { time: this._clock(c.session.start) }) : null,
          zone,
        ]),
      };
    }
    if (c.trip) {
      const min = this._elapsedMin(c.trip.started);
      return {
        key: "driving",
        tone: "live",
        entity: c.trip.entity_id,
        title: this._t("vd_driving"),
        sub: join([
          c.trip.distance_km != null ? this._distance(c.trip.distance_km, c.unit, 1) : null,
          min != null ? this._t("tr_min", { n: min }) : null,
          c.trip.start_location ? this._t("vs_from", { place: this._esc(c.trip.start_location) }) : null,
        ]),
      };
    }
    // The lock has a tile of its own, so the line names where, not how locked.
    if (c.electric && c.plugged && c.target != null && c.gaugeNum != null && c.gaugeNum < c.target - 1) {
      return {
        key: "waiting",
        tone: "ok",
        entity: c.ids.plug || c.ids.gauge,
        title: this._t("vd_waiting"),
        sub: join([this._t("vs_target", { n: Math.round(c.target) }), zone]),
      };
    }
    const real = c.realSt && this._num(c.realSt) != null ? this._fmt(c.realSt) : this._fmt(c.rangeSt);
    if (c.gaugeNum != null && c.gaugeNum < 20 && !c.plugged && !c.bare) {
      return {
        key: "low",
        tone: "warn",
        entity: c.ids.gauge,
        title: this._t(c.ice ? "vd_low_fuel" : "vd_low"),
        sub: join([this._t("vs_left", { v: real }), zone]),
      };
    }
    return {
      key: "ready",
      tone: "ok",
      entity: m.lockId || c.ids.gauge,
      title: this._t("vd_ready"),
      sub: join([
        c.electric && c.plugged && c.target != null && c.gaugeNum != null
          ? this._t("vs_at_target", { n: Math.round(c.target) })
          : null,
        zone,
      ]),
    };
  }

  /** A power state in kW, whatever unit it comes in; null when there is none. */
  _kw(st) {
    const n = this._num(st);
    if (n == null) return null;
    const u = String((st.attributes || {}).unit_of_measurement || "W");
    const kw = u === "kW" ? n : u === "MW" ? n * 1000 : n / 1000;
    if (kw <= 0) return null;
    return `${this._dec(kw, 1)} kW`;
  }

  /** The tiles the grid shows when `tiles:` is not set, best first: the four
   * that fit what the car is doing. Each is only shown where the car has data,
   * so the later ones stand in for the missing. */
  _situationTiles({ ice, bare, electric, charging, trip, open }) {
    // The target sits on the bar and a bare car's odometer beside its range,
    // so neither is offered again here.
    if (charging) return ["power", "session_energy", "session_solar", "session_cost", "time_to_full", "plug"];
    if (trip) return electric ? ["trip_distance", "trip_time", "trip_used", "lock"] : ["trip_distance", "trip_time", "lock", "odometer"];
    if (open) return ["lock", "open_items", "location", "tires"];
    if (bare) return ["lock", "tires", "location", "distance_month"];
    if (ice) return ["lock", "tires", "location", "odometer", "distance_month"];
    return ["lock", "plug", "tires", "location", "odometer", "distance_month"];
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
        // While charging: the clock time it will be done, which is what one plans
        // around; the remaining minutes are a tap away in the entity's dialog.
        const doneAt = charging ? this._doneAt(st) : null;
        if (doneAt) return tile(this._t("done_at"), st, "mdi:timer-sand", { val: doneAt });
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
        const m = ctx.closures || this._closureModel(entities);
        if (!m.lockId) return null;
        return tile(this._t("ti_lock"), this._st(m.lockId), m.lock.key === "unlocked" ? "mdi:lock-open-variant" : "mdi:lock", {
          val: this._esc(m.lock.label),
          color: m.lock.color,
        });
      }
      case "distance_month":
        return tile(this._t("ti_distance_month"), this._byDescriptor(entities, "driving_distance_month"), "mdi:calendar-month");
      case "power": {
        // Only while charging: BMW keeps the last power long after a charge.
        if (!charging) return null;
        const st = this._st(byDesc("vehicle.powertrain.electric.battery.charging.power"));
        const kw = this._kw(st);
        return kw ? tile(this._t("ti_power"), st, "mdi:lightning-bolt", { val: kw, tone: "live" }) : null;
      }
      case "session_energy": {
        const s = ctx.session;
        if (!charging || !s || !s.st || this._num(s.st) == null) return null;
        return tile(this._t("ti_added"), s.st, "mdi:battery-plus-variant", { val: `+${this._fmt(s.st)}`, tone: "live" });
      }
      case "session_solar": {
        const s = ctx.session;
        if (!charging || !s || s.solar == null) return null;
        return tile(this._t("ti_solar"), s.st, "mdi:solar-power-variant", { val: `${this._dec(s.solar, 0)} %`, tone: "solar" });
      }
      case "session_cost": {
        const s = ctx.session;
        if (!charging || !s || !s.cost) return null;
        return tile(this._t("ti_cost"), s.st, "mdi:cash", { val: this._fmtCost(s.cost) });
      }
      case "trip_distance": {
        const t = ctx.trip;
        if (!t || t.distance_km == null) return null;
        return tile(this._t("ti_trip"), this._st(t.entity_id), "mdi:map-marker-path", {
          val: this._esc(this._distance(t.distance_km, ctx.unit, 1)),
          tone: "live",
        });
      }
      case "trip_time": {
        const t = ctx.trip;
        const min = t ? this._elapsedMin(t.started) : null;
        if (min == null) return null;
        return tile(this._t("ti_trip_time"), this._st(t.entity_id), "mdi:timer-outline", {
          val: this._t("tr_min", { n: min }),
          tone: "live",
        });
      }
      case "trip_used": {
        const t = ctx.trip;
        if (!t || t.soc_start == null || t.soc_now == null) return null;
        const drop = Math.max(0, t.soc_start - t.soc_now);
        return tile(this._t("ti_used"), this._st(t.entity_id), "mdi:battery-arrow-down-outline", {
          val: `−${this._dec(drop, 0)} %`,
        });
      }
      case "open_items": {
        const items = (ctx.closures || this._closureModel(entities)).openItems;
        if (!items.length) return null;
        return tile(items.length === 1 ? this._esc(items[0].label) : this._t("ti_open"), this._st(items[0].id), "mdi:car-door", {
          val: items.length === 1 ? this._esc(items[0].value) : this._t("ov_n_open", { n: items.length }),
          tone: "alert",
        });
      }
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

  /** When a charge will be done, as a clock time (with the weekday when that is
   * not today), from a remaining-time state; null without a usable figure.
   * Counted from the state's last write, so it holds still between writes; a
   * figure whose finish is already past (a stale count) gives none, and the
   * tile falls back to the remaining time. */
  _doneAt(st) {
    if (!st || UNAVAILABLE.has(st.state)) return null;
    const n = parseFloat(st.state);
    if (!Number.isFinite(n) || n <= 0) return null;
    const unit = String((st.attributes || {}).unit_of_measurement || "min").toLowerCase();
    const perUnit = { s: 1e3, min: 6e4, h: 36e5, d: 864e5 }[unit];
    if (!perUnit) return null;
    const from = new Date(st.last_updated || "").getTime();
    const now = Date.now();
    const done = new Date((Number.isFinite(from) ? from : now) + n * perUnit);
    if (done.getTime() < now) return null;
    const opts = { hour: "2-digit", minute: "2-digit" };
    const format = this._hass && this._hass.locale && this._hass.locale.time_format;
    if (format === "12" || format === "24") opts.hour12 = format === "12";
    if (done.toDateString() !== new Date(now).toDateString()) opts.weekday = "short";
    try {
      return this._esc(done.toLocaleString(_lang(this._hass), opts));
    } catch (_e) {
      return this._esc(done.toISOString().slice(11, 16));
    }
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

  /** Over the configurator photo, the top-down drawing with whatever is open
   * lit up, so it's clear where; empty while everything is shut. What is open
   * is said in words on the line under the photo. */
  _heroInset(m, entities) {
    if (!m.openItems.length) return "";
    return this._carSvgClosures({
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
    });
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
};
