/*
 * BavarianData Card: the overview: hero render, 360° spin, interior panorama, tiles.
 *
 * One part of the card class. bavariandata-card.js loads it from beside itself
 * (see CARD_PARTS there) and mixes it into BavarianDataCard; what it takes from
 * the card's own scope arrives in SHARED.
 */

export default (Base, { CHARGING_ACTIVE_STATES, DEVICE_CLASS_ICONS, NEUTRAL_TILES, NOT_CHARGING_STATES, UNAVAILABLE, _lang }) => class OverviewPart extends Base {
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
};
