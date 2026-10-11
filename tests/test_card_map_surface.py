"""The card's map overlays must draw on both generations of ``ha-map``.

The trip mini-map's route and the trip map's clustered destinations are drawn
straight onto whatever ``ha-map`` renders with. Before Home Assistant 2026.10
that was a Leaflet map exposed as ``leafletMap`` + ``Leaflet``; 2026.10 moved to
a map engine (MapLibre GL, Leaflet as the fallback) behind ``_engine``, with
``addPath`` / ``addMarker`` / ``setClustering`` and no Leaflet handle at all --
and the card, still waiting for ``leafletMap``, silently drew nothing.

Neither handle is documented, and Home Assistant cannot run here, so this runs
the shipped card under Node against a fake of each and checks that the overlay
lands, is removed before a redraw, and comes back when the map is rebuilt under
it (a dashboard tab switch). Skipped where Node is not installed.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CARD = _ROOT / "custom_components" / "bavariandata" / "www" / "bavariandata-card.js"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

_HARNESS = r"""
const fs = require("fs");
globalThis.HTMLElement = class {};
globalThis.customElements = { define() {}, get() {}, whenDefined() {} };
globalThis.window = globalThis;
globalThis.document = { createElement: () => ({ style: {}, innerHTML: "" }) };
console.info = () => {};
const src = fs.readFileSync(process.argv[1], "utf8");
const Card = new Function(src + "\nreturn BavarianDataCard;")();

// Pre-2026.10: a Leaflet map and the Leaflet namespace on the element.
function leafletMapEl() {
  const layers = new Set();
  const lmap = {
    addLayer: (l) => layers.add(l),
    removeLayer: (l) => layers.delete(l),
    hasLayer: (l) => layers.has(l),
    fitBounds: () => {},
  };
  const layer = () => {
    const kids = [];
    const g = { addLayer: (k) => kids.push(k), addTo: (m) => { (m.addLayer || m.addLayer)(g); return g; }, kids };
    return g;
  };
  const part = () => ({ addTo: (g) => { g.addLayer(part); return part; }, bindTooltip() {} });
  const L = {
    layerGroup: layer,
    markerClusterGroup: layer,
    polyline: part,
    circleMarker: part,
    marker: () => ({ bindTooltip() {} }),
    divIcon: (o) => o,
  };
  return { el: { leafletMap: lmap, Leaflet: L }, count: () => layers.size };
}

// 2026.10+: a map engine, no Leaflet handle.
function engine() {
  const e = { paths: 0, markers: 0, _clusterOptions: undefined };
  e.addPath = () => { e.paths++; let gone = false; return { remove: () => { if (gone) throw new Error("double remove"); gone = true; e.paths--; } }; };
  e.addMarker = () => { e.markers++; return { remove: () => { e.markers--; } }; };
  e.setClustering = (o) => { e._clusterOptions = o; };
  return e;
}

function card() {
  const c = Object.create(Card.prototype);
  c._config = {};
  c.shadowRoot = { contains: () => true };
  c._mapWindowTrips = () => [];
  c._mapEndpoints = () => endpoints; // what the reassert re-reads
  return c;
}

const coords = [[48.2, 16.3], [48.3, 16.4], [48.4, 16.5]];
const endpoints = [{ lat: 48.2, lon: 16.3, label: "A" }, { lat: 48.4, lon: 16.5 }];
const out = {};

{ // Legacy Leaflet: route and clusters land, a redraw replaces rather than stacks.
  const c = card();
  const route = leafletMapEl();
  c._drawRoute(route.el, coords, "#f00");
  c._drawRoute(route.el, coords, "#f00");
  const map = leafletMapEl();
  c._mapEl = map.el;
  c._mountClusters(endpoints);
  c._mountClusters(endpoints);
  out.leaflet = {
    surface: c._mapSurface(route.el).kind,
    routeLayers: route.count(),
    clusterLayers: map.count(),
    lost: c._layerLost(map.el, c._clusterMap, c._clusterLayer),
  };
}

{ // Engine: a line plus start/end pins; clusters as clustered markers.
  const c = card();
  const eng = engine();
  const el = { _engine: eng, fitBounds: () => { out.engineFit = true; } };
  c._drawRoute(el, coords, "#f00");
  const afterFirst = { paths: eng.paths, markers: eng.markers };
  c._drawRoute(el, coords, "#f00");
  const mapEng = engine();
  c._mapEl = { _engine: mapEng, fitBounds: () => {} };
  c._mountClusters(endpoints);
  c._mountClusters(endpoints);
  const clustered = { markers: mapEng.markers, ours: !!(mapEng._clusterOptions && mapEng._clusterOptions.iconBuilder) };
  const bubble = mapEng._clusterOptions.iconBuilder([{}, {}, {}]);
  // ha-map resets the clustering when it redraws its own entities.
  mapEng.setClustering(null);
  const lostOnReset = c._layerLost(c._mapEl, c._clusterMap, c._clusterLayer);
  c._reassertMapOverlays();
  out.engine = {
    surface: c._mapSurface(el).kind,
    afterFirst,
    afterRedraw: { paths: eng.paths, markers: eng.markers },
    clustered,
    bubbleSize: bubble.size,
    bubbleHasCount: bubble.element.innerHTML.includes(">3<"),
    lostOnReset,
    afterReassert: { markers: mapEng.markers, ours: mapEng._clusterOptions !== null },
  };
}

{ // Tab switch: the element gets a fresh engine; the route is redrawn on it.
  const c = card();
  const first = engine();
  const el = { _engine: first, fitBounds: () => {} };
  c._miniMapEl = el;
  c._drawRoute(el, coords, "#f00");
  const steady = c._layerLost(el, el._bdRouteMap, el._bdRoute);
  const second = engine();
  el._engine = second;
  const lost = c._layerLost(el, el._bdRouteMap, el._bdRoute);
  c._reassertMapOverlays();
  out.reconnect = { steady, lost, redrawn: second.paths, drawnOn: el._bdRouteMap === second };
}

{ // Neither handle yet (async init): no surface, nothing thrown.
  const c = card();
  out.none = c._mapSurface({}) === null;
}

process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result() -> dict:
    run = subprocess.run(
        [NODE, "-e", _HARNESS, str(_CARD)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return json.loads(run.stdout)


def test_legacy_leaflet_map_still_draws(result: dict) -> None:
    leaflet = result["leaflet"]
    assert leaflet["surface"] == "leaflet"
    assert leaflet["routeLayers"] == 1  # redrawn, not stacked
    assert leaflet["clusterLayers"] == 1
    assert leaflet["lost"] is False


def test_engine_draws_route_and_pins(result: dict) -> None:
    engine = result["engine"]
    assert engine["surface"] == "engine"
    assert engine["afterFirst"] == {"paths": 1, "markers": 2}  # line + start + end
    assert engine["afterRedraw"] == {"paths": 1, "markers": 2}
    assert result["engineFit"] is True


def test_engine_clusters_destinations(result: dict) -> None:
    engine = result["engine"]
    assert engine["clustered"] == {"markers": 2, "ours": True}  # redrawn, not stacked
    assert engine["bubbleSize"] == [40, 40]
    assert engine["bubbleHasCount"] is True


def test_engine_clustering_reset_is_reasserted(result: dict) -> None:
    engine = result["engine"]
    assert engine["lostOnReset"] is True
    assert engine["afterReassert"] == {"markers": 2, "ours": True}


def test_route_returns_after_the_map_is_rebuilt(result: dict) -> None:
    assert result["reconnect"] == {"steady": False, "lost": True, "redrawn": 1, "drawnOn": True}


def test_no_surface_before_init(result: dict) -> None:
    assert result["none"] is True
