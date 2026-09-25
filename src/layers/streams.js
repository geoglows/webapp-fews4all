// The TDX-Hydro stream network (public/data_streams.geojson, from
// scripts/build_streams.py) — the same hydrography GEOGLOWS forecasts on, shown as
// a context layer independent of the flagged-area dataset and drawn under the cells
// so it reads as a backdrop rather than competing with the hazard.
//
// Telescoping: every reach carries its Strahler order, and only the orders worth
// seeing at the current zoom are drawn. At world zoom that is the handful of
// continental rivers; by street zoom it is everything in the file. The filter is
// reset on zoomend rather than expressed as a zoom expression, because MapLibre
// only allows those in a filter in limited positions — and this keeps the rule in
// one readable function.
import {DATA} from "../sources.js";
import {STREAMS_LAYERS, STREAMS_SRC, STREAM_ATTRIBUTION, STREAM_COLOR, STREAM_OPACITY, STREAM_ORDER_DOMAIN,
  STREAM_WIDTH_BASE, STREAM_WIDTH_MAX, STREAM_ZOOM_SCALE} from "../config.js";
import {applyLayerOrder, map} from "../map.js";

// Minimum stream order drawn at a given zoom, matched to River Forecast System v3
// by measurement: rasterising each band over its viewport at its line width puts
// order 7 on RFS's world view and order 5 on its zoom-8 view. Order 5 is the finest
// the build keeps, so from zoom 6 the whole file is on screen; below that the
// telescoping drops reaches that would be sub-pixel anyway.
const ORDER_BY_ZOOM = [
  [0, 7],   // world: trunks and their main tributaries
  [3, 6],
  [5, 5],   // everything in the file
];

export function streamMinOrder(zoom) {
  let min = ORDER_BY_ZOOM[0][1];
  for (const [z, ord] of ORDER_BY_ZOOM) if (zoom >= z) min = ord;
  return min;
}

let data = null;
let loading = false;
let on = false;

function load(cb) {
  if (data) return cb(data);
  if (loading) return;
  loading = true;
  fetch(DATA.globalStreams)
    .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then((d) => { data = d; loading = false; cb(d); })
    .catch((err) => { loading = false; console.warn("global streams:", err.message); cb(null); });
}

function ensureLayers() {
  if (map.getSource(STREAMS_SRC) || !data) return;
  map.addSource(STREAMS_SRC, {type: "geojson", data, attribution: STREAM_ATTRIBUTION});
  map.addLayer({
    id: "streams-line", type: "line", source: STREAMS_SRC,
    layout: {"line-cap": "round", "line-join": "round"},
    paint: {
      "line-color": STREAM_COLOR,
      "line-opacity": ["interpolate", ["linear"], ["zoom"], ...STREAM_OPACITY.flat()],
      "line-width": widthExpr(),
    },
  });
  applyFilter();
}

// RFS v3's width ramp: base..max across the order domain, multiplied by a per-zoom
// scale. `interpolate` clamps at its end stops, so orders outside the domain simply
// sit at one end rather than running off the ramp.
function widthExpr() {
  const byOrder = ["interpolate", ["linear"], ["to-number", ["get", "ord"]],
    STREAM_ORDER_DOMAIN[0], STREAM_WIDTH_BASE,
    STREAM_ORDER_DOMAIN[1], STREAM_WIDTH_MAX];
  return ["interpolate", ["linear"], ["zoom"],
    ...STREAM_ZOOM_SCALE.flatMap(([z, scale]) => [z, ["*", scale, byOrder]])];
}

function applyFilter() {
  const f = [">=", ["get", "ord"], streamMinOrder(map.getZoom())];
  for (const id of STREAMS_LAYERS) if (map.getLayer(id)) map.setFilter(id, f);
}

// Re-telescope on zoom. Cheap: a filter swap, no data round trip.
export function updateStreamsLOD() {
  if (on) applyFilter();
}

export function setStreamsOn(next) {
  on = next;
  if (!on) {
    for (const id of STREAMS_LAYERS) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "none");
    }
    return;
  }
  load((d) => {
    if (!d) { on = false; return; }
    ensureLayers();
    for (const id of STREAMS_LAYERS) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "visible");
    }
    applyFilter();
    applyLayerOrder();     // keep the network beneath the flagged cells
  });
}

export const streamsOn = () => on;
