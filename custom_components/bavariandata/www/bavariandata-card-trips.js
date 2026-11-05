/*
 * BavarianData Card: the trips view: the logbook, trip review and detail.
 *
 * One part of the card class. bavariandata-card.js loads it from beside itself
 * (see CARD_PARTS there) and mixes it into BavarianDataCard; what it takes from
 * the card's own scope arrives in SHARED.
 */

export default (Base, { TRIP_CLASS_COLORS, _lang }) => class TripsPart extends Base {
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
    if (!current || this._wantsFetch(cache, "trips")) {
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
        this._trp = { ...this._trp, loading: false, error: true, failedAt: Date.now() };
        this._retryLater();
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
    // The weather along the drive (opt-in route weather): its time-weighted
    // temperature, and the range when it varied on the way.
    const weather = trip.weather;
    if (weather && weather.temp_c != null) {
      const tu = this._tempUnit(this._resolveDeviceId());
      const spread =
        weather.temp_min_c != null && weather.temp_max_c != null && weather.temp_max_c - weather.temp_min_c >= 1
          ? ` (${this._dec(tu === "f" ? weather.temp_min_c * 1.8 + 32 : weather.temp_min_c, 0)}–${this._temp(
              weather.temp_max_c,
              tu
            )})`
          : "";
      facts.push([this._t("tr_outside"), `${this._temp(weather.temp_c, tu)}${spread}`]);
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
};
