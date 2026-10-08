// The risk-index heat map: one weighted point per flagged base cell, blurred into
// a continuous surface.
//
// WHY POINTS AND NOT THE HEXAGONS
//
// Painting the hexagons by the index would be a choropleth, not a heat map — the
// same thing the app already does, with a different variable in it. A heat map
// says something a choropleth cannot: that danger here is also danger next door.
// MapLibre's heatmap layer only takes points, so the surface is built from cell
// CENTRES, which cost nothing — an H3 cell's centre is a pure function of its id,
// so no geometry is fetched and no tile is touched.
//
// The hexagons stay on the map underneath, drawn at zero opacity. They are the hit
// targets: a heat map you cannot click is a picture, and the panel still has to be
// able to answer for a specific cell. See cells_tiles.js, which keeps hover and
// selection visible while suppressing the resting paint.
//
// WHAT THE WEIGHT IS
//
// The index's score, rescaled from its own floor rather than from zero. The lowest
// a flagged cell can score is a bare warning — 0.222 — and nothing exists below
// that, so scaling from 0 would spend a fifth of the ramp on values that never
// occur and leave every warning glowing faintly. Rescaled, the bands separate
// cleanly:
//
//     warning   0.00 .. 0.14
//     danger    0.29 .. 0.57
//     extreme   0.57 .. 1.00
//
// The floor comes from the release (index.csv), not from a constant here, because
// it is derived from the severity ladder and would drift the moment that changed.
//
// WHAT A HEAT MAP CONFLATES, AND WHAT TO WATCH
//
// Density and intensity. The surface sums weights within a radius, so a long river
// of warnings can out-glow a compact cluster of extremes. That is partly the point
// — concentration IS information — but it means the colour is not a reading of any
// one cell. The cell is what the panel is for.
import {cellToLatLng} from "h3-js";
import {EMPTY_FC, HEAT_LAYER, HEAT_SRC} from "../config.js";
import {applyLayerOrder, map} from "../map.js";
import {darken, rgba} from "../format.js";
import {paletteColor, view, visibleModels, visibleSeverities} from "../settings.js";

let release = null;

// Only used for a release written before index.csv existed: a bare warning over the
// headroom the modifiers get. The release's own value wins whenever it has one.
const FALLBACK_FLOOR = 1 / 3 / 1.5;

function floor() {
  const v = release && release.rules.index ? release.rules.index.floor : null;
  return typeof v === "number" && v > 0 && v < 1 ? v : FALLBACK_FLOOR;
}

function weightOf(score) {
  const f = floor();
  return Math.max(0, Math.min(1, (score - f) / (1 - f)));
}

// The base resolution — where the index is decided and where every cell is a real
// 36 km2 of ground rather than an aggregate of other cells. The surface is built
// from it at every zoom: the blur does the generalising that the coarse
// resolutions do for the hexagons, and it does it continuously.
function baseRes() {
  return release ? release.resolutions[release.resolutions.length - 1] : null;
}

function points() {
  const res = baseRes();
  const features = [];
  if (!release || res == null) return {type: "FeatureCollection", features};

  for (const [, cell] of release.cells) {
    if (cell.res !== res || !cell.wri) continue;
    // The model and severity filters decide which cells CONTRIBUTE; they do not
    // recompute the score, which the backend fixed over every model. So switching
    // GloFAS off removes the cells only GloFAS saw, and leaves the rest reading
    // exactly as they did — the honest reading of a filter that cannot rerun the
    // index. A cell whose every forecast is filtered out drops out of the surface.
    const visible = release.forecastsAt(res, cell.h3_id)
      .some((f) => visibleModels.has(f.model) && visibleSeverities.has(f.severity));
    if (!visible) continue;

    let lat, lng;
    try {
      [lat, lng] = cellToLatLng(cell.h3_id);
    } catch {
      continue;
    }
    features.push({
      type: "Feature",
      geometry: {type: "Point", coordinates: [lng, lat]},
      properties: {w: weightOf(cell.wri.score)},
    });
  }
  return {type: "FeatureCollection", features};
}

// ---- paint ------------------------------------------------------------------

// The ramp the surface runs through, taken from whichever palette the Display menu
// has the heat on. The first stop is fully TRANSPARENT rather than the palette's
// lightest colour: a heat map covers the whole canvas, and an opaque low end would
// lay a wash over every basemap pixel the data never reached.
function colorExpr() {
  const c = (k) => paletteColor(view.heatRampId, k);
  return ["interpolate", ["linear"], ["heatmap-density"],
    0, rgba(c("warning"), 0),
    0.10, rgba(c("warning"), 0.35),
    0.30, rgba(c("warning"), 0.9),
    0.55, c("danger"),
    0.80, c("extreme"),
    1, darken(c("extreme"), 0.8)];
}

// Radius is in SCREEN pixels, so it has to grow with zoom or the surface collapses
// to a stipple of single cells as soon as the map is anywhere but world view. The
// stops roughly track a res-6 hexagon's size on screen; the multiplier is the
// viewer's, because how much to blur is a judgement about the question being asked
// rather than a fact about the data.
function radiusExpr() {
  const m = view.heatRadius || 1;
  return ["interpolate", ["linear"], ["zoom"],
    1, 6 * m, 4, 14 * m, 7, 26 * m, 10, 48 * m, 14, 90 * m];
}

// Density is summed per screen pixel, so the same ground reads hotter when zoomed
// out simply because more cells land on one pixel. Intensity rises with zoom to
// push back against that, so a place keeps roughly the same colour as you go in.
function intensityExpr() {
  return ["interpolate", ["linear"], ["zoom"], 1, 0.7, 6, 1.2, 12, 2.2];
}

function paint() {
  return {
    "heatmap-weight": ["coalesce", ["get", "w"], 0],
    "heatmap-intensity": intensityExpr(),
    "heatmap-radius": radiusExpr(),
    "heatmap-color": colorExpr(),
    "heatmap-opacity": view.heatOpacity,
  };
}

// ---- build / restyle ---------------------------------------------------------

export function build(rel) {
  release = rel;
  if (!map.getSource(HEAT_SRC)) {
    map.addSource(HEAT_SRC, {type: "geojson", data: EMPTY_FC});
  }
  if (!map.getLayer(HEAT_LAYER)) {
    map.addLayer({
      id: HEAT_LAYER,
      type: "heatmap",
      source: HEAT_SRC,
      // Stated at creation rather than left to MapLibre's default, for the same
      // reason the concurrence hatch is: a layer whose own toggle starts off has
      // to start off, and nothing runs between creation and the first interaction.
      layout: {visibility: view.heatOn ? "visible" : "none"},
      paint: paint(),
    });
    // Added after the cells, so it arrives on top of them; the stack says it
    // belongs underneath.
    applyLayerOrder();
  }
  refresh();
}

/** Rebuild the surface. Needed whenever which cells qualify changes. */
export function refresh() {
  const src = map.getSource(HEAT_SRC);
  if (!src || !release) return;
  src.setData(points());
  applyHeatStyle();
}

/** Paint and visibility only — the cheap path the sliders drag through. */
export function applyHeatStyle() {
  if (!map.getLayer(HEAT_LAYER)) return;
  map.setLayoutProperty(HEAT_LAYER, "visibility", view.heatOn ? "visible" : "none");
  for (const [prop, value] of Object.entries(paint())) {
    map.setPaintProperty(HEAT_LAYER, prop, value);
  }
}

/** How many cells the surface is currently built from — for the legend's note. */
export function heatCount() {
  const src = map.getSource(HEAT_SRC);
  return src && src._data ? (src._data.features || []).length : 0;
}
