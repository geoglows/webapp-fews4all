// The TDX-Hydro stream network — River Forecast System v3's own PMTiles archive,
// so this draws the identical tiles RFS v3 draws rather than an approximation of
// them. A context layer, independent of the flagged-area dataset and beneath the
// cells so it reads as backdrop rather than competing with the hazard.
//
// Nothing is loaded up front. The archive is one 3.4 GB file holding the whole
// tile pyramid, read with HTTP range requests, so the browser fetches only the
// tiles on screen — a world view costs a couple of tiles, not the file. There is
// no "download the network" step to wait on and no in-memory copy to hold. The
// `pmtiles://` protocol handler is registered in map.js.
//
// Telescoping is not ours to do. The archive was cut by tippecanoe with the
// level-of-detail rules baked into the tiles themselves:
//
//     order >= 7  at every zoom          order >= 4  from z7
//     order >= 6  from z5                order >= 2  from z9
//
// A tile simply does not contain the reaches that would be sub-pixel at its zoom.
// The retired GeoJSON build re-filtered on `zoomend` to approximate exactly this
// rule, measured off RFS v3's rendered map; keeping that filter now could only
// subtract from what the tiles have already decided. If the network ever reads too
// dense up close, the honest knob is one `>=` filter on the order field here — not
// a second LOD table racing the one in the data.
import {STREAMS_LAYERS, STREAMS_SOURCE_LAYER, STREAMS_SRC, STREAMS_TILES_URL,
  STREAM_ATTRIBUTION, STREAM_COLOR, STREAM_OPACITY, STREAM_ORDER_DOMAIN,
  STREAM_ORDER_FIELD, STREAM_WIDTH_BASE, STREAM_WIDTH_MAX,
  STREAM_ZOOM_SCALE} from "../config.js";
import {applyLayerOrder, map} from "../map.js";

let on = false;

// RFS v3's width ramp: base..max across the order domain, multiplied by a per-zoom
// scale. `interpolate` clamps at its end stops, so orders outside the domain simply
// sit at one end rather than running off the ramp.
function widthExpr() {
  const byOrder = ["interpolate", ["linear"], ["to-number", ["get", STREAM_ORDER_FIELD]],
    STREAM_ORDER_DOMAIN[0], STREAM_WIDTH_BASE,
    STREAM_ORDER_DOMAIN[1], STREAM_WIDTH_MAX];
  return ["interpolate", ["linear"], ["zoom"],
    ...STREAM_ZOOM_SCALE.flatMap(([z, scale]) => [z, ["*", scale, byOrder]])];
}

// The archive arrives tile by tile, so a wrong url — or a host that does not
// honour range requests — surfaces as tile errors rather than one failed load,
// and the layer would otherwise just quietly draw nothing. Say it once, with the
// url, because that is the whole diagnosis.
let warnedOnce = false;
function warnOnSourceError() {
  map.on("error", (e) => {
    if (warnedOnce || !e || e.sourceId !== STREAMS_SRC) return;
    warnedOnce = true;
    console.warn(
      "stream tiles failed:", (e.error && e.error.message) || e.error || "unknown error",
      "\n  archive:", STREAMS_TILES_URL,
      "\n  PMTiles is read with HTTP range requests — check the url resolves and",
      "that the host supports them.");
  });
}

function ensureLayers() {
  if (map.getSource(STREAMS_SRC)) return;
  warnOnSourceError();
  // `url` rather than `tiles` lets the protocol hand MapLibre the archive's own
  // TileJSON, so the zoom range comes from the file: below its minzoom nothing is
  // requested, above its maxzoom the deepest tiles are overzoomed. That is right
  // for vector data — the lines stay sharp, they just stop gaining detail — and it
  // means this code never hardcodes the archive's depth.
  map.addSource(STREAMS_SRC, {
    type: "vector",
    url: STREAMS_TILES_URL,
    attribution: STREAM_ATTRIBUTION,
  });
  map.addLayer({
    id: "streams-line",
    type: "line",
    source: STREAMS_SRC,
    "source-layer": STREAMS_SOURCE_LAYER,
    layout: {"line-cap": "round", "line-join": "round"},
    paint: {
      "line-color": STREAM_COLOR,
      "line-opacity": ["interpolate", ["linear"], ["zoom"], ...STREAM_OPACITY.flat()],
      "line-width": widthExpr(),
    },
  });
}

export function setStreamsOn(next) {
  on = next;
  if (!on) {
    for (const id of STREAMS_LAYERS) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "none");
    }
    return;
  }
  // No await: the source is declarative and tiles stream in behind it, so the
  // toggle is instant where the GeoJSON build had to fetch 183 MB before drawing.
  ensureLayers();
  for (const id of STREAMS_LAYERS) {
    if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "visible");
  }
  applyLayerOrder();     // keep the network beneath the flagged cells
}

export const streamsOn = () => on;
