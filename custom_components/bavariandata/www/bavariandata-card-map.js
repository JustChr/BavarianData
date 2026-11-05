/*
 * BavarianData Card: the map view and the map drawing trips and places share.
 *
 * One part of the card class. bavariandata-card.js loads it from beside itself
 * (see CARD_PARTS there) and mixes it into BavarianDataCard; what it takes from
 * the card's own scope arrives in SHARED.
 */

export default (Base, { _lang }) => class MapPart extends Base {
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
    if (!current || this._wantsFetch(cache, "trips")) {
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
        this._mapData = { ...this._mapData, loading: false, error: true, failedAt: Date.now() };
        this._retryLater();
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
};
