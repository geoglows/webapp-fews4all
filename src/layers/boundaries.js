// ADM boundaries (public/data_boundaries.geojson, from scripts/build_boundaries.py):
// geoBoundaries CGAZ at ADM0 countries, ADM1 regions and ADM2 districts, one level
// per zoom band rather than all three at once.
//
// Showing one level at a time is what keeps this readable: ADM1 units tile their
// country and ADM2 tile their ADM1, so the national border is still on screen at
// every zoom — drawn as the outer edge of whichever level is active — without three
// sets of lines stacking up on top of each other.
import {DATA} from "../sources.js";
import {BOUNDARIES_SRC, BOUNDARY_ATTRIBUTION, BOUNDARY_COLOR, BOUNDARY_FILL, BOUNDARY_LINE} from "../config.js";
import {applyLayerOrder, map, tooltip} from "../map.js";

// Minimum zoom at which each level takes over. Each level's geometry is simplified
// to the resolution of its own band (see the build script), so a level is never
// drawn at a zoom it wasn't prepared for.
const LEVEL_BY_ZOOM = [
  [0, 0],   // countries
  [4, 1],   // regions / states
  [7, 2],   // districts
];

export function boundaryLevel(zoom) {
  let level = LEVEL_BY_ZOOM[0][1];
  for (const [z, lvl] of LEVEL_BY_ZOOM) if (zoom >= z) level = lvl;
  return level;
}

const LEVEL_LABEL = ["Country", "Region", "District"];

let data = null;
let loading = false;
let on = false;
let hoverBound = false;

function load(cb) {
  if (data) return cb(data);
  if (loading) return;
  loading = true;
  fetch(DATA.boundaries)
    .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then((d) => { data = d; loading = false; cb(d); })
    .catch((err) => { loading = false; console.warn("boundaries:", err.message); cb(null); });
}

function ensureLayers() {
  if (map.getSource(BOUNDARIES_SRC) || !data) return;
  map.addSource(BOUNDARIES_SRC, {type: "geojson", data, attribution: BOUNDARY_ATTRIBUTION});
  // A near-invisible fill: the outlines alone are too thin to hover, so this gives
  // the whole unit a hit target — the same trick the per-basin districts layer uses.
  map.addLayer({
    id: BOUNDARY_FILL, type: "fill", source: BOUNDARIES_SRC,
    paint: {"fill-color": BOUNDARY_COLOR, "fill-opacity": 0.001},
  });
  map.addLayer({
    id: BOUNDARY_LINE, type: "line", source: BOUNDARIES_SRC,
    layout: {"line-cap": "round", "line-join": "round"},
    paint: {
      "line-color": BOUNDARY_COLOR,
      "line-opacity": 0.55,
      "line-width": ["interpolate", ["linear"], ["zoom"], 1, 0.7, 6, 1, 12, 1.5],
    },
  });
  bindHover();
  applyFilter();
}

function applyFilter() {
  const f = ["==", ["get", "adm"], boundaryLevel(map.getZoom())];
  for (const id of [BOUNDARY_FILL, BOUNDARY_LINE]) {
    if (map.getLayer(id)) map.setFilter(id, f);
  }
}

// Hover the unit to see its name. Boundaries sit under everything else, so the
// label only appears where nothing the map is actually about is on top of it.
function bindHover() {
  if (hoverBound) return;
  hoverBound = true;
  map.on("mousemove", BOUNDARY_FILL, (e) => {
    const f = e.features && e.features[0];
    if (!f || anythingAbove(e.point)) return;
    const p = f.properties;
    const label = LEVEL_LABEL[p.adm] || "Area";
    // A few dozen ADM2 units ship with an empty shapeName (mostly CHN and JPN);
    // fall back to the country code rather than showing a bare dash.
    const name = p.name || p.group || "—";
    const group = p.group && p.adm > 0 && p.group !== name
      ? ` <span style="opacity:.6">${p.group}</span>` : "";
    tooltip.setLngLat(e.lngLat)
      .setHTML(`<span style="opacity:.6">${label}</span> <b>${name}</b>${group}`)
      .addTo(map);
  });
  map.on("mouseleave", BOUNDARY_FILL, () => tooltip.remove());
}

// Anything the boundary label should yield to: a flagged cell, a flash polygon or a
// selected basin's districts. Resolved by query rather than by import so this layer
// stays independent of the ones above it.
function anythingAbove(point) {
  const layers = ["cells-fill", "flash-fill-likely", "flash-fill-high", "ctx-districts-fill"]
    .filter((id) => map.getLayer(id));
  return layers.length > 0 && map.queryRenderedFeatures(point, {layers}).length > 0;
}

// Re-telescope on zoom: a filter swap, no data round trip.
export function updateBoundariesLOD() {
  if (on) applyFilter();
}

export function setBoundariesOn(next) {
  on = next;
  if (!on) {
    for (const id of [BOUNDARY_FILL, BOUNDARY_LINE]) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "none");
    }
    tooltip.remove();
    return;
  }
  load((d) => {
    if (!d) { on = false; return; }
    ensureLayers();
    for (const id of [BOUNDARY_FILL, BOUNDARY_LINE]) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "visible");
    }
    applyFilter();
    applyLayerOrder();
  });
}

export const boundariesOn = () => on;
