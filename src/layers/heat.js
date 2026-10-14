// The severity heat map: one weighted point per flagged base cell, blurred into a
// continuous surface — with the cells where models agree deliberately left out.
//
// WHY POINTS AND NOT THE HEXAGONS
//
// Painting the hexagons by severity would be a choropleth, which is what the app
// already draws. A heat map says the thing a choropleth cannot: that danger here is
// also danger next door. MapLibre's heatmap layer only takes points, so the surface
// is built from cell CENTRES — an H3 cell's centre is a pure function of its id, so
// nothing is fetched and no tile is touched.
//
// The hexagons stay on the map underneath at zero opacity. They are the hit targets
// the surface has none of: a heat map you cannot click is a picture, and the panel
// still has to answer for a specific cell. See cells_tiles.js.
//
// ONE SURFACE PER MODEL, PLUS ONE FOR CONCURRENCE — A PARTITION
//
// Every flagged cell goes into exactly ONE of them. Never two: a cell feeding both
// its model's surface and the concurrence surface would make each hotter, and the
// reader would be seeing one cell twice.
//
// Which bucket a cell lands in:
//
//   concurrence     two or more models agree there, and the viewer has asked for
//                   concurrence to be colored separately
//   its model       otherwise — the model whose forecast set the cell's severity,
//                   the same owner rule the hexagons use, so a cell is the same
//                   color in both views
//
// At the base resolution a cell holding two models IS a concurrence cell, so with
// that option off "which model owns this" always has exactly one answer.
//
// Hue carries the model (or the agreement), brightness carries severity. That split
// is the point: a density channel cannot hold a categorical fact, because a tight
// cluster of one model's extremes out-sums a lone agreeing cell, so weighting
// concurrence higher would have been unreadable. Color is the channel that CAN
// hold it, and the surfaces otherwise share everything — same weights, same blur,
// same radius — so they register with each other instead of reading as loose layers.
//
// An earlier pass cut the agreeing cells out and drew them as hard-edged hexagons
// over the blur. Same partition, wrong mark: at res 6 a hexagon is a big crisp
// shape and 1,403 of them read as clutter. Blurred in their own hue, they read as
// the same kind of thing as their surroundings, which is what they are.
//
// WHAT THE WEIGHT IS
//
// Severity alone (config.js heatWeight). Nothing else — not impact, not model
// count. The rungs separate on a square curve rather than linearly, because linear
// weights hand half the surface to the warnings simply because there are three
// times as many of them; see HEAT_SEVERITY_EXPONENT for the measured shares.
//
// WHAT A HEAT MAP CONFLATES
//
// Density and intensity. The surface sums weights within a radius, so a long line
// of warnings can out-glow an isolated extreme. That is partly the point —
// concentration IS information — but it means the color is not a reading of any one
// cell. The cell is what the panel is for.
import {cellToLatLng} from "h3-js";
import {EMPTY_FC, HEAT_CONCUR_LAYER, HEAT_CONCUR_SRC, HEAT_MODEL_LAYER, HEAT_MODEL_SRC, PANEL_MODELS, SEV_KEYS, heatWeight} from "../config.js";
import {applyLayerOrder, map} from "../map.js";
import {corroborated, darken, rgba} from "../format.js";
import {paletteColor, rampFor, view, visibleModels, visibleSeverities} from "../settings.js";

let release = null;

// Every base cell's geometry, worked out once when a release lands.
//
// A cell's centre and its latitude scale are pure functions of its H3 id, so they
// cannot change while a release is loaded — but the rebuild was recomputing all
// 32,426 of them on every filter tick, model toggle and ramp change. Measured, the
// h3 lookups alone were about a fifth of a ~96ms rebuild, and the rest was churn
// this array also avoids: the per-cell record is made once and only read after.
let sites = [];

// The base resolution — where agreement is decided and where a cell is a real
// 36 km2 of ground rather than an aggregate of other cells. The surface is built
// from it at every zoom: the blur does the generalising that the coarse
// resolutions do for the hexagons, and it does it continuously.
function baseRes() {
  return release ? release.resolutions[release.resolutions.length - 1] : null;
}

// True while concurrence is being drawn as its own surface, which is what makes
// the two sets a partition. Off, every cell goes to the severity surface and the
// concurrence layer is empty.
function splitConcur() {
  return view.heatOn && view.concurHeat;
}

/**
 * The points for one half of the partition.
 *
 * `agreeing` true builds the concurrence surface, false the severity one. Same
 * weights either way — the hue is carrying the agreement, so making the green
 * heavier as well would be saying it twice and would put concurrence back into the
 * brightness channel severity owns.
 */
function buildSites() {
  sites = [];
  const res = baseRes();
  if (!release || res == null) return;
  for (const [, cell] of release.cells) {
    if (cell.res !== res) continue;
    let lat, lng;
    try {
      [lat, lng] = cellToLatLng(cell.h3_id);
    } catch {
      continue;                       // an id the build does not recognise
    }
    sites.push({cell, lng, lat, k: latScale(lat)});
  }
}

/**
 * The worst severity a cell's filters still allow, and the model that set it — the
 * cell's owner. "" / "" when nothing is left showing.
 *
 * One pass, no intermediate array. `fcs.filter(...)` followed by worstSeverity()
 * allocated a throwaway array per cell — 32,426 of them per rebuild — to answer a
 * question that is a single reduction. Strictly-greater wins, so ties go to the
 * first forecast listed, which is the rule the hexagons' own owner test uses.
 */
function pickVisible(fcs) {
  let sev = "", model = "", rank = -1;
  for (const f of fcs) {
    if (!visibleModels.has(f.model) || !visibleSeverities.has(f.severity)) continue;
    const r = SEV_KEYS.indexOf(f.severity);
    if (r > rank) { rank = r; sev = f.severity; model = f.model; }
  }
  return {sev, model};
}

// The bucket key the concurrence surface answers to. Not a model name, and chosen
// so it cannot collide with one.
const CONCUR = "__concur";
const BUCKETS = [...PANEL_MODELS, CONCUR];

/**
 * Every bucket's points, from ONE pass over the cells.
 *
 * One pass rather than one per surface: the test that decides a cell's bucket is
 * the expensive part, and asking it four times to throw three answers away was the
 * obvious way to make a rebuild four times slower.
 */
function buckets() {
  const out = {};
  for (const key of BUCKETS) out[key] = [];
  const res = baseRes();
  if (!release || res == null) return out;

  const split = splitConcur();
  for (const g of sites) {
    const cell = g.cell;
    // Asked once and reused: both the partition and the concurrence filter want the
    // same answer, and it is the more expensive of the per-cell tests. It has to be
    // the SAME test the cells apply, or a cell could pass the filter underneath and
    // be missing from the surface above it.
    const agrees = split || view.concurrenceOnly
      ? corroborated(cell, visibleModels) : false;

    // Same filter the cells obey: the surface and the hexagons underneath it have
    // to be built from the same set, or clicking a warm patch would find nothing.
    if (view.concurrenceOnly && !agrees) continue;

    // The model and severity filters decide what reaches the surface, so turning a
    // model off cools the places only it saw rather than leaving them lit.
    const {sev, model} = pickVisible(release.forecastsAt(res, cell.h3_id));
    const w = heatWeight(sev);
    if (!(w > 0)) continue;

    const list = out[split && agrees ? CONCUR : model];
    if (!list) continue;              // a model this build does not draw
    list.push({
      type: "Feature",
      geometry: {type: "Point", coordinates: [g.lng, g.lat]},
      properties: {w, k: g.k},
    });
  }
  return out;
}

// Where a bucket's points live, and the ramp it is painted in.
const srcOf = (key) => (key === CONCUR ? HEAT_CONCUR_SRC : HEAT_MODEL_SRC(key));
const layerOf = (key) => (key === CONCUR ? HEAT_CONCUR_LAYER : HEAT_MODEL_LAYER(key));
const rampOf = (key) => (key === CONCUR ? view.concurRampId : rampFor(key));
// A model's surface follows the heat toggle; concurrence follows its own as well.
const shown = (key) => (key === CONCUR ? splitConcur() : view.heatOn);

// How much wider the blur has to be at this latitude to cover the same ground.
//
// Web Mercator stretches north-south by 1/cos(latitude), so a cell that is 6.5 km
// across everywhere occupies 2.9x as many pixels at 70 degrees as it does at the
// equator. The radius is in screen pixels, so without this the blur silently
// shrinks in GROUND terms as you pan poleward, and the surface beads into separate
// dots. Measured as radius over centre spacing, where below 1 they stop merging:
//
//          lat      z6     z7     z8
//            0    3.89   2.30   1.47
//           45    2.94   1.74   1.11
//           60    2.12   1.25   0.80      <- beading
//           70    1.36   0.80   0.51
//
// H3 itself is not the problem: res-6 centres sit 6.2 to 7.0 km apart at every
// latitude, and the northern data is MORE contiguous than the tropics (92% of cells
// have two or more flagged neighbours, against 82%). It is only the projection.
//
// Clamped at 75 degrees. The ratio runs away toward the pole, and the furthest
// north anything has ever been flagged is 69.9, so the clamp is headroom rather
// than a limit anything meets.
const MAX_SCALE_LAT = 75;

function latScale(lat) {
  const l = Math.min(Math.abs(lat), MAX_SCALE_LAT);
  return Number((1 / Math.cos((l * Math.PI) / 180)).toFixed(3));
}

// ---- paint ------------------------------------------------------------------

// The ramp the surface runs through, from whichever palette the Display menu has
// the heat on — so the heat and the hexagons beside it are always drawn from one
// set of colors. The first stop is fully TRANSPARENT rather than the palette's
// lightest shade: a heat map covers the whole canvas, and an opaque low end would
// wash over every basemap pixel the data never reached.
function colorExpr(rampId) {
  const c = (k) => paletteColor(rampId, k);
  return ["interpolate", ["linear"], ["heatmap-density"],
    0, rgba(c("warning"), 0),
    0.10, rgba(c("warning"), 0.35),
    0.30, rgba(c("warning"), 0.9),
    0.55, c("danger"),
    0.80, c("extreme"),
    1, darken(c("extreme"), 0.8)];
}

// Radius is in SCREEN pixels, so it has to grow with zoom or the surface collapses
// into a stipple of single cells as soon as the map is anywhere but world view. The
// stops up to z8 are what they always were, tuned by eye. The multiplier is the
// viewer's, because how much to blur is a judgement about the question being asked
// rather than a fact about the data.
//
// From z8 inward the radius DOUBLES with every zoom level, which is the rate the map
// itself scales at, so the blur covers a constant patch of GROUND rather than a
// shrinking one. z8 is where the res-5 cells give way to res 6 — the point past
// which the old ramp started pulling the surface apart.
//
// The arithmetic, because "it looked right" is not a reason that survives a retune.
// Res-6 cell centres sit about 6.3 km apart. At z8 and 26 deg latitude that is 23
// screen pixels against a 33 px radius: the blobs overlap by half and the field
// reads as continuous. The old ramp grew the radius 1.85x over three zoom levels
// while the scale grew 8x, so by z10 the spacing was 92 px against a 48 px radius
// and the cells separated into dots. Doubling holds that 33:23 ratio at every zoom
// from z8 in, so what is connected here stays connected all the way down.
//
// It stops doubling at z13, where a single cell's blur already covers the whole
// viewport and there is nothing left to join up. Cost stays flat as the radius
// grows, because the number of points on screen falls at the same rate the quads
// expand — roughly 120 points at z10, three at z13.
const HOLD_ZOOM = 8;          // where res 5 hands over to res 6
const HOLD_RADIUS = 33.33;    // what the old ramp reached there
const HOLD_UNTIL = 13;

function radiusExpr() {
  const m = view.heatRadius || 1;
  // Each stop is scaled by the point's own latitude factor rather than the whole
  // ramp being multiplied afterwards: `zoom` is only allowed as the input to the
  // OUTERMOST interpolate, so the per-feature part has to live inside the outputs.
  const k = ["coalesce", ["get", "k"], 1];
  const at = (px) => ["*", px * m, k];
  // One stop per zoom level above the hold point: MapLibre interpolates one way
  // across the whole expression, so the doubling is written as stops rather than
  // as an exponential base, which would also have reshaped every segment below it.
  const stops = [1, at(6), 4, at(14), 7, at(26)];
  for (let z = HOLD_ZOOM; z <= HOLD_UNTIL; z++) {
    stops.push(z, at(HOLD_RADIUS * 2 ** (z - HOLD_ZOOM)));
  }
  return ["interpolate", ["linear"], ["zoom"], ...stops];
}

// Density is summed per screen pixel, so the same ground reads hotter zoomed out
// simply because more cells land on one pixel. Intensity rises with zoom to push
// back, so a place keeps roughly the same color as you go in.
function intensityExpr() {
  return ["interpolate", ["linear"], ["zoom"], 1, 0.7, 6, 1.2, 12, 2.2];
}

// Both surfaces share everything but the ramp, so they blur at the same scale and
// register with each other rather than reading as two unrelated layers.
function paint(rampId) {
  return {
    "heatmap-weight": ["coalesce", ["get", "w"], 0],
    "heatmap-intensity": intensityExpr(),
    "heatmap-radius": radiusExpr(),
    "heatmap-color": colorExpr(rampId),
    "heatmap-opacity": view.heatOpacity,
  };
}

// ---- build / restyle ---------------------------------------------------------

export function build(rel) {
  release = rel;
  // Once per release, before anything asks for points.
  buildSites();
  for (const key of BUCKETS) {
    if (!map.getSource(srcOf(key))) {
      map.addSource(srcOf(key), {type: "geojson", data: EMPTY_FC});
    }
    if (map.getLayer(layerOf(key))) continue;
    map.addLayer({
      id: layerOf(key),
      type: "heatmap",
      source: srcOf(key),
      // Stated at creation rather than left to MapLibre's default of "visible",
      // for the same reason the concurrence surface is: a layer whose own toggle
      // starts off has to start off, and nothing runs between creation and the
      // first interaction to correct it.
      layout: {visibility: shown(key) ? "visible" : "none"},
      paint: paint(rampOf(key)),
    });
  }
  // Added after the cells, so they arrive above them; the stack says they belong
  // underneath, where a hovered or selected hexagon can draw over them.
  applyLayerOrder();
  refresh();
}

/** Rebuild every surface. Needed whenever which cells qualify changes. */
export function refresh() {
  if (!release) return;
  const sets = buckets();
  for (const key of BUCKETS) {
    const src = map.getSource(srcOf(key));
    if (src) src.setData({type: "FeatureCollection", features: sets[key]});
  }
  applyHeatStyle();
}

/** Paint and visibility only — the cheap path the sliders drag through. */
export function applyHeatStyle() {
  for (const key of BUCKETS) {
    const id = layerOf(key);
    if (!map.getLayer(id)) continue;
    map.setLayoutProperty(id, "visibility", shown(key) ? "visible" : "none");
    for (const [prop, value] of Object.entries(paint(rampOf(key)))) {
      map.setPaintProperty(id, prop, value);
    }
  }
}
