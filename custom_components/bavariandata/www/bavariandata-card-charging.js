/*
 * BavarianData Card: the charging view: sessions, places, summary, power curves.
 *
 * One part of the card class. bavariandata-card.js loads it from beside itself
 * (see CARD_PARTS there) and mixes it into BavarianDataCard; what it takes from
 * the card's own scope arrives in SHARED.
 */

export default (Base, { _lang, _numberLocale }) => class ChargingPart extends Base {
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
    if (!current || this._wantsFetch(cache, "data")) {
      this._chg = {
        vin,
        entryId,
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
        this._chg = { ...this._chg, loading: false, error: true, failedAt: Date.now() };
        this._retryLater();
        this._render();
      });
  }

  _paintCharging(deviceId, entities) {
    const name = this._config.title || this._deviceName(deviceId);
    const state = this._chg || {};
    const sessions = state.data;
    const month = this._month("charging");
    const isCurrent = month === this._monthKey();
    // The month's figures come from its own sessions, so every month has them,
    // not only the one the monthly sensors describe.
    const figures = sessions && sessions.length ? this._monthFigures(sessions) : null;
    // The charge in progress is not in the history until it ends; the session
    // sensor tells it while it runs.
    const live = isCurrent ? this._liveCharge(entities, deviceId) : null;
    const expanded = this._chgExpanded || null;

    const sig = this._signature({
      m: "chg",
      lang: _lang(this._hass),
      name,
      month,
      loading: state.loading && !sessions,
      error: state.error,
      figures,
      live,
      expanded,
      rows: (sessions || []).map((s) => [s.start, s.energy_kwh, s.cost && s.cost.amount, s.cost && s.cost.source]),
    });
    if (sig === this._sig) return;
    this._sig = sig;

    let body;
    if (state.error) {
      body = `<div class="empty">${this._t("ch_error")}</div>`;
    } else if (!sessions && state.loading) {
      body = `<div class="empty">${this._t("ch_loading")}</div>`;
    } else if ((!sessions || !sessions.length) && !live) {
      body = `<div class="empty">${this._t(
        isCurrent ? "ch_empty" : "ch_empty_month"
      )}</div>`;
    } else {
      body = `<div class="chg__list">${live ? this._liveRow(live) : ""}${(sessions || [])
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
          ${this._cheadIcon("mdi:ev-station")}
          <div class="chead__text">
            <span class="chead__title">${this._esc(this._config.title || this._t("ch_title"))}</span>
            <span class="chead__sub">${this._esc(name)}${count ? " · " + countLabel : ""}</span>
          </div>
          ${this._exportButtons("charging")}
        </div>
        ${this._chargingTabs("sessions")}
        ${this._monthNav("charging")}
        ${figures ? this._monthFiguresHtml(figures, month) : ""}
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
    if (!current || this._wantsFetch(cache, "data")) {
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
          this._plc = { ...this._plc, loading: false, error: true, failedAt: Date.now() };
          this._retryLater();
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
          ${this._cheadIcon("mdi:ev-station")}
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
      const label = this._isHomeZone(place.zone) ? this._t("ch_home") : place.zone;
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

  /** Whether a stored zone name is the home zone. The server stores the name
   * Home Assistant gives `zone.home`, which follows the location's name
   * ("Thuis", #320), so "Home" alone is not enough. */
  _isHomeZone(zone) {
    return !!zone && (/^home$/i.test(zone) || zone === this._homeZoneName());
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

  /** Whether a session was charged away from home: a named zone that isn't
   * Home, a public address, or no place at all. One assumed at home (no GPS)
   * counts as home, as its badge says. */
  _isAway(session) {
    if (session.location_assumed) return false;
    const loc = session.location || {};
    return !this._isHomeZone(loc.zone);
  }

  /** The energy a session's row shows: the measured grid figure where there is
   * one, the battery side otherwise. */
  _sessionKwh(session) {
    const kwh = session.grid_kwh != null ? session.grid_kwh : session.energy_kwh;
    return kwh == null ? null : Number(kwh);
  }

  /** Where a charge's energy came from, adding up to the charge itself. The
   * mix holds only what was watched live: kWh taken while Home Assistant was
   * restarting reach the charge's total but no source, and count as
   * unattributed here rather than vanish from the breakdown (#320). Null for a
   * charge with no mix at all. */
  _sessionMix(session) {
    const mix = session.energy_mix;
    if (!mix) return null;
    const out = {};
    for (const k of ["pv", "battery", "grid", "unknown"]) out[k] = Number(mix[k] || 0);
    const gap = (this._sessionKwh(session) || 0) - (out.pv + out.battery + out.grid + out.unknown);
    if (gap > 0.05) out.unknown += gap;
    return out;
  }

  /** A month's charging in figures, from its sessions: energy, cost, how many
   * and how many away, where the energy came from, and kWh per day. */
  _monthFigures(sessions) {
    let kwh = 0;
    let cost = 0;
    let currency = null;
    let priced = 0;
    let partial = false;
    let away = 0;
    const mix = { pv: 0, battery: 0, grid: 0, unknown: 0, away: 0 };
    const days = {};
    for (const s of sessions) {
      const e = this._sessionKwh(s) || 0;
      kwh += e;
      if (s.cost && s.cost.amount != null) {
        cost += Number(s.cost.amount);
        currency = currency || s.cost.currency;
        priced += 1;
        if (s.cost.partial) partial = true;
      }
      if (this._isAway(s)) {
        away += 1;
        mix.away += e;
      } else if (s.energy_mix) {
        const own = this._sessionMix(s);
        for (const k of ["pv", "battery", "grid", "unknown"]) mix[k] += own[k];
      } else {
        mix.unknown += e;
      }
      const d = new Date(s.start || "");
      if (Number.isFinite(d.getTime())) days[d.getDate()] = (days[d.getDate()] || 0) + e;
    }
    return {
      kwh,
      count: sessions.length,
      away,
      cost: priced ? { amount: cost, currency } : null,
      partial,
      // The price per kWh at home. Where a home charge was priced only in
      // part, or not at all, the cost is a floor and so is this: it says
      // "at least" then (`perKwhFloor`).
      perKwh: priced && kwh - mix.away > 0 ? cost / (kwh - mix.away) : null,
      perKwhFloor: partial || priced !== sessions.length - away,
      mix,
      days,
    };
  }

  _monthFiguresHtml(f, month) {
    const kpi = (val, lbl) => `<div class="kpi"><span class="kpi__v">${val}</span><span class="kpi__l">${lbl}</span></div>`;
    const kpis = [
      kpi(`${this._dec(f.kwh, 1)}<i>kWh</i>`, this._t("ch_kpi_energy")),
      f.cost
        ? kpi(
            this._fmtCost(f.cost),
            f.perKwh != null
              ? this._t(f.perKwhFloor ? "ch_kpi_per_kwh_min" : "ch_kpi_per_kwh", { v: this._fmtCost({ amount: f.perKwh, currency: f.cost.currency }) })
              : this._t(f.partial ? "ch_partial" : "ch_cost")
          )
        : "",
      kpi(String(f.count), f.away ? this._t("ch_kpi_away", { n: f.away }) : this._t("ch_kpi_sessions")),
    ].join("");
    const total = f.mix.pv + f.mix.battery + f.mix.grid + f.mix.unknown + f.mix.away;
    const parts = [
      ["pv", "ch_mix_pv", "var(--bd-solar)"],
      ["battery", "ch_mix_battery", "var(--bd-house)"],
      ["grid", "ch_mix_grid", "var(--bd-grid)"],
      ["away", "ch_public", "var(--bmw-charge)"],
      ["unknown", "ch_mix_unknown", "var(--divider-color)"],
    ].filter(([k]) => f.mix[k] > 0.05);
    // Only worth a bar where something besides "unattributed" is known.
    const mix =
      total > 0 && parts.some(([k]) => k !== "unknown")
        ? `<div class="sec"><span class="sec__t">${this._t("ch_where_from")}</span></div>
        <div class="mix">
          <div class="mix__bar" role="img" aria-label="${this._esc(parts.map(([k, l]) => `${this._t(l)} ${this._dec(f.mix[k], 1)} kWh`).join(", "))}">
            ${parts.map(([k, , c]) => `<span style="width:${((f.mix[k] / total) * 100).toFixed(1)}%;background:${c}"></span>`).join("")}
          </div>
          <div class="legend">${parts
            .map(([k, l, c]) => `<span><i style="background:${c}"></i>${this._t(l)} <b>${this._dec(f.mix[k], 1)}</b></span>`)
            .join("")}<span>kWh</span></div>
        </div>`
        : "";
    // kWh per day across the month; days still to come stay flat.
    const [y, m] = month.split("-").map(Number);
    const daysIn = new Date(y, m, 0).getDate();
    const now = new Date();
    const thisMonth = now.getFullYear() === y && now.getMonth() + 1 === m;
    const max = Math.max(...Object.values(f.days), 0.1);
    let bars = "";
    for (let d = 1; d <= daysIn; d++) {
      const v = f.days[d] || 0;
      const h = v ? Math.max(3, Math.round((v / max) * 44)) : 3;
      const cls = v ? "spark__bar" : thisMonth && d > now.getDate() ? "spark__bar is-future" : "spark__bar is-empty";
      bars += `<span class="${cls}" style="height:${h}px" title="${d}: ${v ? `${this._dec(v, 1)} kWh` : "—"}"></span>`;
    }
    return `
      <div class="kpis">${kpis}</div>
      ${mix}
      <div class="sec"><span class="sec__t">${this._t("ch_per_day")}</span><span class="sec__s">kWh</span></div>
      <div class="spark" role="img" aria-label="${this._esc(this._t("ch_per_day"))}">${bars}</div>
      <div class="spark-ax"><span>1</span><span>15</span><span>${daysIn}</span></div>`;
  }

  /** The charge in progress, from the session sensor and the car's charge, or
   * null when none runs. */
  _liveCharge(entities, deviceId) {
    const s = this._sessionInfo(entities);
    if (!s.st || s.socStart == null) return null;
    const picks = this._overviewEntities(entities, this._deviceName(deviceId));
    if (!this._isCharging(this._st(picks.charging), this._st(picks.soc))) return null;
    return {
      entity: s.st.entity_id,
      start: s.start,
      from: s.socStart,
      to: this._num(this._st(picks.soc)),
      kwh: this._num(s.st),
      cost: s.cost,
      solar: s.solar,
    };
  }

  _liveRow(l) {
    const soc = this._socBar(l.from, l.to);
    return `
      <div class="chg__session is-live">
        <button class="chg__row" data-entity="${this._attr(l.entity)}">
          <span class="chg__row-main">
            <span class="chg__date"><span class="dot dot--live"></span> ${l.start ? this._t("vs_since", { time: this._clock(l.start) }) : this._t("ch_ongoing")}</span>
            <span class="chg__meta">${soc}${l.solar != null && Math.round(l.solar) > 0 ? `<span class="chg__tag chg__tag--sun">\u2600 ${this._dec(l.solar, 0)}%</span>` : ""}</span>
          </span>
          <span class="chg__figures">
            <span class="chg__energy chg__energy--lead">${l.kwh != null ? `+${this._dec(l.kwh, 1)} kWh` : "—"}</span>
            ${l.cost ? `<span class="chg__cost-sub">${this._fmtCost(l.cost)}</span>` : ""}
          </span>
        </button>
      </div>`;
  }

  /** From → to as a small bar, with the figures beside it. */
  _socBar(a, b) {
    if (a == null && b == null) return "";
    const from = a == null ? null : Math.max(0, Math.min(100, a));
    const to = b == null ? null : Math.max(0, Math.min(100, b));
    const bar =
      from != null && to != null && to >= from
        ? `<span class="socbar"><span style="left:${from}%;width:${Math.max(2, to - from)}%"></span></span>`
        : "";
    return `${bar}<span class="chg__soc">${from == null ? "?" : Math.round(from)}→${to == null ? "?" : Math.round(to)}%</span>`;
  }

  _chargingRow(session, expanded) {
    const id = session.start || "";
    const isOpen = expanded === id;
    const date = this._fmtSessionDate(session.start);
    // Prefer the measured grid figure (imported / enriched sessions carry only
    // that); fall back to the battery-side energy for live-only sessions.
    const energyKwh = this._sessionKwh(session);
    const energy = energyKwh != null ? `${this._dec(energyKwh, 1)} kWh` : "—";
    const soc = this._socBar(session.soc_start, session.soc_end);
    const cost =
      session.cost && session.cost.amount != null
        ? `<span class="chg__cost-sub">${this._fmtCost(session.cost)}</span>`
        : "";
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
            ${cost}
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
    const chart = this._powerCurveSvg(session.power_curve, this._avgPowerKw(session));
    const facts = [];
    if (session.peak_power_kw != null) {
      facts.push([this._t("ch_peak"), `${this._dec(session.peak_power_kw, 1)} kW`]);
    }
    const avg = this._avgPowerKw(session);
    if (avg != null) facts.push([this._t("ch_avg_dashed"), `${this._dec(avg, 1)} kW`]);
    // duration_s isn't in the service payload; derive it from the timestamps.
    const dur = this._durationLabel(session);
    if (dur) facts.push([this._t("ch_duration"), dur]);
    if (session.grid_kwh != null) {
      facts.push([this._t("ch_grid"), `${this._dec(session.grid_kwh, 1)} kWh`]);
    }
    // Cost moved off the collapsed row to here, so it's kept for tariff users
    // without competing with the kWh for the row's headline figure.
    if (session.cost && session.cost.amount != null) {
      const entered = session.cost.source === "user" ? ` · ${this._t("ch_cost_entered")}` : "";
      facts.push([this._t("ch_cost"), `${this._fmtCost(session.cost)}${entered}`]);
    }
    const mixLabel = this._mixLabel(this._sessionMix(session));
    if (mixLabel) facts.push([this._t("ch_mix"), mixLabel]);
    // Why part of it has no price or source.
    if (session.interrupted) facts.push([this._t("ch_restart"), this._t("ch_restart_note")]);

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
        ${session.end && session.id ? this._costEntry(session) : ""}
      </div>
    `;
  }

  // What the charge cost, typed in from a receipt (#324). It replaces BMW's or
  // the tariff's figure; Remove brings that figure back. The amount is in the
  // account's currency, named when a charge in the list already says it.
  _costEntry(session) {
    const cost = session.cost || {};
    const entered = cost.source === "user";
    const draft = this._chgCostDraft && this._chgCostDraft.id === session.id ? this._chgCostDraft.value : null;
    const value = draft != null ? draft : entered && cost.amount != null ? String(cost.amount) : "";
    const known = ((this._chg && this._chg.data) || []).find((s) => s.cost && s.cost.currency);
    const currency = cost.currency || (known && known.cost.currency) || "";
    const id = this._attr(session.id);
    return `<div class="tr__classify chg__paid">
        <span class="tr__classify-lbl">${this._t("ch_paid")}</span>
        <input class="chg__paid-in" type="number" inputmode="decimal" min="0" step="0.01"
          data-cost-in="${id}" value="${this._attr(value)}" aria-label="${this._attr(this._t("ch_paid"))}">
        ${currency ? `<span class="chg__paid-cur">${this._esc(currency)}</span>` : ""}
        <span class="tr__cls-btns">
          <button class="tr__cls-btn" data-cost-save="${id}">${this._t("ch_cost_save")}</button>
          ${entered ? `<button class="tr__cls-btn" data-cost-clear="${id}">${this._t("ch_cost_clear")}</button>` : ""}
        </span>
      </div>`;
  }

  _setChargeCost(sessionId, amount) {
    const vin = this._chg && this._chg.vin;
    if (!vin || !sessionId) return;
    const data = { vin, session_id: sessionId };
    if (amount != null) data.amount = amount;
    this._hass
      .callService("bavariandata", "set_charge_cost", this._withEntry(data, this._chg.entryId))
      .then(() => {
        // Ask again rather than guess: the service decides what the cost falls
        // back to, and the month's figures follow it.
        this._chgCostDraft = null;
        if (!this._chg || this._chg.vin !== vin) return;
        this._chg = { ...this._chg, loading: true };
        this._fetchCharging(vin, this._chg.entryId, this._chg.month);
      })
      .catch(() => {});
  }

  /** Inline SVG step chart of the [seconds, kW] power curve. No dependencies.
   *
   * Stepped, not interpolated: each sampled power holds until the next sample
   * arrives, which is what the data means (an average over a block), rather than
   * drawing a diagonal ramp between two readings that never happened. */
  _powerCurveSvg(curve, avg) {
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
        ${avg != null && avg > 0 && avg <= yMax ? `<line x1="${pad}" x2="${W - pad}" y1="${this._px(sy(avg))}" y2="${this._px(sy(avg))}" class="chg__chart-avg"></line>` : ""}
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
    } else if (zone && !this._isHomeZone(zone)) {
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

  _wireChargingTaps() {
    this._wireTaps();
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
    this.shadowRoot.querySelectorAll("[data-cost-in]").forEach((el) => {
      // Kept across a repaint (a live charge redraws the card while typing).
      el.addEventListener("input", () => {
        this._chgCostDraft = { id: el.getAttribute("data-cost-in"), value: el.value };
      });
    });
    this.shadowRoot.querySelectorAll("[data-cost-save]").forEach((el) => {
      el.addEventListener("click", () => {
        const id = el.getAttribute("data-cost-save");
        const input = [...this.shadowRoot.querySelectorAll("[data-cost-in]")].find(
          (i) => i.getAttribute("data-cost-in") === id
        );
        const amount = input ? parseFloat(String(input.value).replace(",", ".")) : NaN;
        if (!(amount >= 0)) return;
        this._setChargeCost(id, amount);
      });
    });
    this.shadowRoot.querySelectorAll("[data-cost-clear]").forEach((el) => {
      el.addEventListener("click", () => this._setChargeCost(el.getAttribute("data-cost-clear"), null));
    });
  }
};
