// The flagged-area layer, reading cell geometry from PMTiles archives and its
// data from the release tables.
//
// How this differs from the GeoJSON build it replaces, and why:
//
//   Geometry is not ours. The archives hold every cell in the world's land
//   drainage and carry one property, h3_id. What a release does is say which of
//   them to show and what colour they are, and that arrives as tables.
//
//   Visibility is a filter, colour is a join. MapLibre cannot filter on
//   feature-state, so the two are separate mechanisms: an explicit id list from
//   map_rules.json decides what is drawn, and setFeatureState carries severity
//   onto the features so the paint expression can read it. State is stored per
//   source and keyed by id, so it survives tile loads and is applied once rather
//   than on every tile.
//
//   Split cells are back, drawn on top rather than cut from the tile. A polygon
//   inside a vector tile cannot be clipped — but an H3 cell's outline is a pure
//   function of its id, so the hexagons that need splitting are rebuilt from
//   h3-js, cut into vertical bands by the same clipper the GeoJSON build used,
//   and laid over the tile fill as their own source. Only cells with two or more
//   visible models are rebuilt: 746 polygons across every resolution, against
//   4.4M in the archives.
//
//   Resolution switching is MapLibre's. Each resolution has its own source and
//   its own zoom band, so there is no zoomend handler swapping data — the map
//   simply stops requesting one archive and starts requesting the next.
import {cellToBoundary, cellToLatLng} from "h3-js";
import {DEFAULT_COLOR, EMPTY_FC, SEV_KEYS} from "../config.js";
import {applyLayerOrder, map, registerPointerSource, registerTooltipLayers, updateResReadout} from "../map.js";
import {clipToBand, lonRange, nearestCopy} from "../geometry.js";
import {darken, modelLabel, worstSeverity} from "../format.js";
import {modelRamp, paletteColor, setModelPalettes, setPalettes, view, visibleModels, visibleSeverities} from "../settings.js";

let release = null;
let joined = new Set();            // source ids whose state has been applied
const splitIds = new Map();        // res -> [h3_id, ...] currently drawn as bands
let hovered = null;                // {res, id}
export let selected = null;        // {res, id}

let actions = {onSelect() {}, onClear() {}};
export function init(a) { actions = Object.assign(actions, a); }

const SPLIT_SRC = "cells-split";
const splitId = (res) => `cells-split-${res}`;
const fillId = (res) => `cells-fill-${res}`;
const lineId = (res) => `cells-line-${res}`;
const hashId = (res) => `cells-hash-${res}`;

// Every resolution lives in one archive now, as its own named layer — so a
// resolution is addressed by (source, source-layer) rather than by a source of its
// own. feature-state needs both, so the pair is looked up here from the layer
// definitions rather than reconstructed from a naming convention.
const tileRef = (res) => {
  const l = (release?.rules.layers || [])
    .find((x) => Number(String(x.id).split("-").pop()) === res);
  return l ? {source: l.source, sourceLayer: l["source-layer"]} : null;
};

// ---- what a release says about one cell, given the current filters ----------

// The forecasts in a cell that the model and severity filters still allow. Every
// derived value below is computed from this rather than from the stored row, so
// switching a model off changes the map's colours as well as its contents.
function visibleForecasts(res, id) {
  return release.forecastsAt(res, id).filter(
    (f) => visibleModels.has(f.model) && visibleSeverities.has(f.severity));
}

function agrees(cell) {
  return cell.co_models.some((set) => set.length >= 2 && set.every((m) => visibleModels.has(m)));
}

// The model whose forecast set the cell's severity — it owns the colour.
function ownerOf(forecasts) {
  let best = null, rank = -1;
  for (const f of forecasts) {
    const r = SEV_KEYS.indexOf(f.severity);
    if (r > rank) { rank = r; best = f.model; }
  }
  return best;
}

// ---- the join ---------------------------------------------------------------

// Push each visible cell's severity and owner onto its tile feature. Cells with
// nothing visible are cleared rather than left behind, or a model toggled off
// would keep its colour until the source reloaded.
function applyState(res) {
  const ref = tileRef(res);
  if (!ref || !map.getSource(ref.source)) return;
  for (const [, cell] of release.cells) {
    if (cell.res !== res) continue;
    const fcs = visibleForecasts(res, cell.h3_id);
    const state = fcs.length
      ? {severity: worstSeverity(fcs), owner: ownerOf(fcs), agree: agrees(cell)}
      : {severity: "", owner: "", agree: false};
    map.setFeatureState({...ref, id: cell.h3_id}, state);
  }
}

// ---- paint ------------------------------------------------------------------

// Colour by the joined severity, in the owning model's palette. Written as a
// nested match so one expression covers every model without a layer per model.
function fillColorExpr() {
  const byOwner = ["match", ["coalesce", ["feature-state", "owner"], ""]];
  for (const model of Object.keys(modelRamp)) {
    const bySev = ["match", ["coalesce", ["feature-state", "severity"], ""]];
    for (const k of SEV_KEYS) bySev.push(k, paletteColor(modelRamp[model], k));
    bySev.push("rgba(0,0,0,0)");
    byOwner.push(model, bySev);
  }
  byOwner.push("rgba(0,0,0,0)");
  return byOwner;
}

function fillOpacityExpr() {
  if (view.outlineOnly) return 0;
  return ["case",
    ["==", ["coalesce", ["feature-state", "severity"], ""], ""], 0,
    ["boolean", ["feature-state", "selected"], false], Math.min(1, view.fillOpacity * 0.7),
    ["boolean", ["feature-state", "hover"], false], Math.min(1, view.fillOpacity * 2),
    view.fillOpacity];
}

// Bands live on a GeoJSON source, so they never receive the feature-state join and
// must not be painted by an expression that reads it — fillOpacityExpr's first
// case ("no joined severity, draw nothing") is true for every band, which is what
// made them invisible. Their colour is already stamped per feature, so the only
// thing left to vary is the resting opacity.
function splitOpacityExpr() {
  return view.outlineOnly ? 0 : view.fillOpacity;
}

function lineColorExpr() {
  return ["case",
    ["boolean", ["feature-state", "selected"], false], "#ffffff",
    fillColorExpr()];
}

// Border weight carries severity alongside colour. With the palettes sampled
// through a narrow band the three rungs are close in tone, so the outline is doing
// real work here rather than decoration: an extreme cell reads as heavier even
// where its fill is hard to separate from a warning's. The weights are the
// viewer's to set (Display -> Outline weight); only the fallbacks for a cell with
// no severity yet, and the outline-only boost, are fixed here.
// With no fill to carry the signal, outline-only mode needs every rung heavier.
// A constant rather than a multiplier: it keeps the RATIO the viewer set on the
// sliders, where scaling would stretch the gap between rungs as well.
const OUTLINE_ONLY_BOOST = 0.8;

function severityWidth(byRung, fallback, boost = 0) {
  const expr = ["match", ["coalesce", ["feature-state", "severity"], ""]];
  for (const k of SEV_KEYS) expr.push(k, (byRung[k] || 0) + boost);
  expr.push(fallback);
  return expr;
}

function lineWidthExpr() {
  const base = view.outlineOnly
    ? severityWidth(view.lineWidth, 2, OUTLINE_ONLY_BOOST)
    : severityWidth(view.lineWidth, 1);
  return ["case",
    ["boolean", ["feature-state", "selected"], false], 4,
    ["boolean", ["feature-state", "hover"], false], 3,
    base];
}

/**
 * Every paint property of every layer this module owns, for one resolution.
 *
 * Creation and restyle both read this, which is the point. They used to each
 * spell the paint out separately and had already drifted twice: the bands were
 * created with their own opacity but restyled with the tile fill's — which is
 * zero for anything the feature-state join has not reached, so a palette change
 * made them vanish — and the outline's colour was set at creation and never
 * updated, so after a ramp change a purple cell kept an amber border. Neither is
 * possible when there is one definition.
 */
function paintFor(res) {
  return {
    [fillId(res)]: {
      "fill-color": fillColorExpr(),
      "fill-opacity": fillOpacityExpr(),
    },
    [splitId(res)]: {
      // Bands are on a GeoJSON source and never receive the join, so their paint
      // must not read feature-state at all.
      "fill-color": ["get", "color"],
      "fill-opacity": splitOpacityExpr(),
    },
    [hashId(res)]: {
      "fill-pattern": hashPatternExpr(),
      "fill-opacity": 0.55,
    },
    [lineId(res)]: {
      "line-color": lineColorExpr(),
      "line-width": lineWidthExpr(),
      "line-opacity": ["case",
        ["==", ["coalesce", ["feature-state", "severity"], ""], ""], 0, 0.85],
    },
  };
}

// ---- hatch ------------------------------------------------------------------

// Whether the concurrence hatch draws at all. One definition, read both when the
// layer is built and whenever the display settings change, so the two can never
// disagree about a setting that starts out off.
const hashVisibility = () => (view.hatchOn && !view.outlineOnly ? "visible" : "none");

function makeHashImage(color, size = 16, w = 2) {
  const cv = document.createElement("canvas");
  cv.width = cv.height = size;
  const g = cv.getContext("2d");
  g.lineWidth = w;
  g.lineCap = "square";
  g.strokeStyle = color;
  for (const o of [-size, 0, size]) {
    g.beginPath();
    g.moveTo(o, size);
    g.lineTo(size + o, 0);
    g.stroke();
  }
  return g.getImageData(0, 0, size, size);
}

const hashImageId = (ramp, sev) => `mm-hash-${ramp}-${sev}`;

export function ensureHashImages() {
  const ramps = new Set(Object.values(modelRamp));
  for (const ramp of ramps) {
    for (const k of SEV_KEYS) {
      const id = hashImageId(ramp, k);
      if (!map.hasImage(id)) {
        map.addImage(id, makeHashImage(darken(paletteColor(ramp, k))), {pixelRatio: 2});
      }
    }
  }
}

// The hatch cannot key off feature-state — a fill-pattern needs a concrete image
// id per feature, and state cannot be used in a filter either. So agreement is
// drawn as its own layer whose filter lists the agreeing cells outright, rebuilt
// whenever the model filter changes.
function agreeingIds(res) {
  const out = [];
  for (const [, cell] of release.cells) {
    if (cell.res !== res || !agrees(cell)) continue;
    if (visibleForecasts(res, cell.h3_id).length) out.push(cell.h3_id);
  }
  return out;
}

// ---- split cells ------------------------------------------------------------

/** The hexagon for a cell id, as a GeoJSON Polygon — or null across the antimeridian.
 *
 * A cell spanning the 180th meridian comes back with longitudes on both sides, and
 * banding it would cut a stripe across the whole map. There are a handful and they
 * sit in open Pacific, so they fall back to the undivided tile fill.
 */
function hexagon(id) {
  const ring = cellToBoundary(id, true);     // [lng, lat] pairs, GeoJSON order
  let min = Infinity, max = -Infinity;
  for (const [lng] of ring) {
    if (lng < min) min = lng;
    if (lng > max) max = lng;
  }
  if (max - min > 180) return null;
  return {type: "Polygon", coordinates: [[...ring, ring[0]]]};
}

/**
 * One band per model for every cell holding more than one, at every resolution.
 *
 * The bands are cut in PANEL_MODELS order so a model always occupies the same
 * slot — a cell's left-hand band is GEOGLOWS wherever GEOGLOWS is present, rather
 * than moving with whichever model happened to be listed first. Colour is stamped
 * per feature so the paint is a plain lookup and a palette change is a rebuild of
 * 746 features rather than an expression swap on millions.
 */
function splitFeatures() {
  const out = [];
  splitIds.clear();
  for (const [, cell] of release.cells) {
    const fcs = visibleForecasts(cell.res, cell.h3_id);
    if (fcs.length < 2) continue;
    const models = Object.keys(modelRamp).filter((m) => fcs.some((f) => f.model === m));
    if (models.length < 2) continue;

    // Splitting is only worth doing when it shows something. If every model in the
    // cell is on the same palette, the bands would be three shades of one ramp
    // divided by invisible seams — so the cell is left whole and the tile fill
    // paints it at the WORST severity present, which is the reading that matters.
    // Bands come back as soon as two models are on different palettes, where the
    // division carries real information.
    if (new Set(models.map((m) => modelRamp[m])).size < 2) continue;

    const geom = hexagon(cell.h3_id);
    if (!geom) continue;
    const [x0, x1] = lonRange(geom);
    const span = x1 - x0;
    if (!(span > 0)) continue;

    // Cut first, keep only if every band survives: a stray geometry must never
    // leave part of a cell unpainted, and the tile fill underneath is the
    // fallback that makes that safe.
    const bands = models.map((m, i) => ({
      model: m,
      geometry: clipToBand(geom, x0 + (span * i) / models.length,
        x0 + (span * (i + 1)) / models.length),
    }));
    if (bands.some((b) => !b.geometry)) continue;

    const listed = splitIds.get(cell.res);
    if (listed) listed.push(cell.h3_id); else splitIds.set(cell.res, [cell.h3_id]);

    for (const b of bands) {
      const sev = worstSeverity(fcs.filter((f) => f.model === b.model));
      out.push({
        type: "Feature",
        geometry: b.geometry,
        // h3_id is what makes a band clickable: cutting split cells out of the
        // tile fill left nothing underneath to hit, so the band itself has to say
        // which cell it belongs to.
        properties: {h3_id: cell.h3_id, res: cell.res, model: b.model,
                     color: paletteColor(modelRamp[b.model], sev)},
      });
    }
  }
  return {type: "FeatureCollection", features: out};
}

/**
 * The tile fill's filter, minus whatever the bands are covering.
 *
 * Both layers are semi-transparent, so a band drawn over the whole-cell fill would
 * blend with it and show a muddied colour rather than the model's own. A split
 * cell is therefore cut out of the fill entirely and painted only by its bands,
 * which together cover the hexagon exactly.
 */
function fillFilterFor(res) {
  const base = (release.rules.layers || [])
    .find((l) => Number(String(l.id).split("-").pop()) === res)?.filter;
  const covered = splitIds.get(res) || [];
  if (!base || !covered.length) return base;
  const ref = tileRef(res);
  const key = (ref && release.rules.sources[ref.source]?.promoteId) || "h3_id";
  return ["all", base, ["!", ["in", ["get", key], ["literal", covered]]]];
}

function refreshSplits() {
  const src = map.getSource(SPLIT_SRC);
  if (!src) return;
  src.setData(splitFeatures());
  for (const res of release.resolutions) {
    if (map.getLayer(fillId(res))) map.setFilter(fillId(res), fillFilterFor(res));
  }
}

// ---- build ------------------------------------------------------------------

export function build(rel) {
  release = rel;
  // The release decides which palettes exist and which one each model opens on.
  setPalettes(rel.rules.palettes);
  setModelPalettes(rel.rules.models);

  for (const [id, def] of Object.entries(rel.rules.sources || {})) {
    if (!map.getSource(id)) map.addSource(id, def);
  }

  for (const layer of rel.rules.layers || []) {
    const res = Number(String(layer.id).split("-").pop());
    if (map.getLayer(layer.id)) continue;

    const paint = paintFor(res);
    map.addLayer({...layer, paint: paint[fillId(res)]});

    // Bands sit directly over the tile fill and under the hatch and outline, so a
    // split cell reads as one hexagon divided rather than shapes stacked on it.
    if (!map.getSource(SPLIT_SRC)) map.addSource(SPLIT_SRC, {type: "geojson", data: EMPTY_FC});
    map.addLayer({
      id: splitId(res), type: "fill", source: SPLIT_SRC,
      minzoom: layer.minzoom, ...(layer.maxzoom ? {maxzoom: layer.maxzoom} : {}),
      filter: ["==", ["get", "res"], res],
      paint: paint[splitId(res)],
    });

    ensureHashImages();
    map.addLayer({
      id: hashId(res), type: "fill", source: layer.source,
      "source-layer": layer["source-layer"],
      minzoom: layer.minzoom, ...(layer.maxzoom ? {maxzoom: layer.maxzoom} : {}),
      filter: ["in", ["get", "h3_id"], ["literal", agreeingIds(res)]],
      // Stated at creation, not left to MapLibre's default of "visible". The
      // hatch is off by default, but refresh() is what used to apply that, and
      // refresh() does not run until something changes — so the hatch drew on
      // first load with its own checkbox unticked, and ticking it twice was the
      // only way to clear it.
      layout: {visibility: hashVisibility()},
      paint: paint[hashId(res)],
    });

    map.addLayer({
      id: lineId(res), type: "line", source: layer.source,
      "source-layer": layer["source-layer"],
      minzoom: layer.minzoom, ...(layer.maxzoom ? {maxzoom: layer.maxzoom} : {}),
      filter: layer.filter,
      paint: paint[lineId(res)],
    });

    // The two hit targets this resolution answers hover on: the tile fill and the
    // bands drawn over split cells. Registering them is what makes the boundary
    // label stand down here.
    registerTooltipLayers(fillId(res), splitId(res));
  }

  // The join runs once per source, when that source first has data. Feature
  // state is held per source rather than per tile, so it does not need to be
  // reapplied as tiles come and go.
  // One archive serves every resolution, so the join runs for all of them the
  // first time that source reports itself loaded. Feature state is held per
  // source and keyed by id, so it survives tile churn and is applied once.
  const cellSources = new Set((release.rules.layers || []).map((l) => l.source));
  map.on("sourcedata", (e) => {
    if (!e.sourceId || !cellSources.has(e.sourceId)) return;
    if (!e.isSourceLoaded || joined.has(e.sourceId)) return;
    joined.add(e.sourceId);
    for (const res of release.resolutions) applyState(res);
  });

  refreshSplits();
  bindInteractions();
  applyLayerOrder();
}

// One image id per (ramp, severity), chosen by the same owner/severity pair the
// fill uses, so the hatch is always a darker shade of the cell it sits on.
function hashPatternExpr() {
  const byOwner = ["match", ["coalesce", ["feature-state", "owner"], ""]];
  for (const model of Object.keys(modelRamp)) {
    const bySev = ["match", ["coalesce", ["feature-state", "severity"], ""]];
    for (const k of SEV_KEYS) bySev.push(k, hashImageId(modelRamp[model], k));
    bySev.push(hashImageId(modelRamp[model], SEV_KEYS[0]));
    byOwner.push(model, bySev);
  }
  byOwner.push(hashImageId(modelRamp[Object.keys(modelRamp)[0]], SEV_KEYS[0]));
  return byOwner;
}

// ---- interaction ------------------------------------------------------------

// Which resolution the map is showing, derived from the layers' own zoom bands
// rather than tracked separately — the bands are the single source of truth.
export function currentRes() {
  const z = map.getZoom();
  let chosen = release ? release.resolutions[0] : null;
  for (const layer of (release?.rules.layers || [])) {
    if (z >= (layer.minzoom ?? 0)) chosen = Number(String(layer.id).split("-").pop());
  }
  return chosen;
}

function setStateFor(target, patch) {
  if (!target) return;
  const ref = tileRef(target.res);
  if (!ref || !map.getSource(ref.source)) return;
  map.setFeatureState({...ref, id: target.id}, patch);
}

function setHover(next) {
  if (hovered && next && hovered.id === next.id && hovered.res === next.res) return;
  setStateFor(hovered, {hover: false});
  hovered = next;
  setStateFor(hovered, {hover: true});
}

export function clearSelection() {
  setStateFor(selected, {selected: false});
  selected = null;
  view.selectedBasinId = null;
  actions.onClear();
}

// The finest resolution in the release — where reporting happens, and where a
// click on anything coarser is taking you.
function finestRes() {
  return release ? release.resolutions[release.resolutions.length - 1] : null;
}

function bandStart(res) {
  const band = (release.rules.layers || [])
    .find((l) => Number(String(l.id).split("-").pop()) === res);
  return band?.minzoom ?? 0;
}

/**
 * Take a click down to the finest resolution, centred on the cell.
 *
 * Centres on the CELL rather than on whatever shape was clicked: a click can land
 * on one band of a split hexagon, and that band's middle is half a cell off from
 * the cell's own. The centre comes from the id, which is exact and needs no
 * geometry passed around.
 *
 * Lands at the START of the finest band rather than fitting the cell, which would
 * put one hexagon across the whole map. The current zoom wins when it is already
 * deeper, so clicking around inside res 6 recentres without yanking the view back
 * out to where the band began.
 */
function drillToFinest(id) {
  let centre;
  try {
    const [lat, lng] = cellToLatLng(id);
    // The cell's longitude is its real one; the viewer may be several world
    // copies away from it. Move to the copy they are in, not back across the map.
    centre = [nearestCopy(lng, map.getCenter().lng), lat];
  } catch {
    return;
  }
  map.easeTo({
    center: centre,
    zoom: Math.max(map.getZoom(), bandStart(finestRes())),
    duration: 600,
  });
}

function selectCell(res, id) {
  setStateFor(selected, {selected: false});
  selected = {res, id};
  setStateFor(selected, {selected: true});
  view.selectedBasinId = String(id);
  actions.onSelect(release.cellAt(res, id), visibleForecasts(res, id));
  drillToFinest(id);
}

function bindInteractions() {
  // Both the tile fills and the bands are hit targets. A split cell exists only as
  // bands — it is cut out of the fill so the colours stay clean — so querying the
  // fills alone would make exactly the cells with two models unclickable.
  // `cells-fill-6` and `cells-split-6` both end in the resolution, so one parse
  // handles either kind of hit.
  const hitLayers = () => (release.rules.layers || [])
    .flatMap((l) => {
      const res = Number(String(l.id).split("-").pop());
      return [l.id, splitId(res)];
    })
    .filter((id) => map.getLayer(id));

  registerPointerSource({
    key: "cells",
    layers: hitLayers,
    hover(hits) {
      const f = hits[0];
      if (!f) { setHover(null); return ""; }
      const res = Number(String(f.layer.id).split("-").pop());
      const id = f.properties.h3_id;
      setHover({res, id});
      const cell = release.cellAt(res, id);
      const fcs = visibleForecasts(res, id);
      if (!cell || !fcs.length) return "";
      // Which models are flagging here, and how badly. The cell id told the
      // reader nothing — it is an opaque token — whereas the model names are the
      // one thing a hover can usefully answer, and on a concurrence cell they
      // are the point.
      const names = Object.keys(modelRamp)
        .filter((m) => fcs.some((x) => x.model === m))
        .map(modelLabel)
        .join(" + ");
      return `${names} · <b>${worstSeverity(fcs)}</b>`;
    },
    click(hits) {
      const f = hits[0];
      if (!f) { clearSelection(); return null; }
      const res = Number(String(f.layer.id).split("-").pop());
      const id = f.properties.h3_id;
      if (!visibleForecasts(res, id).length) { clearSelection(); return null; }

      // Only the finest resolution reports. A coarse cell holds every forecast
      // beneath it, spread over an area far larger than any one of them
      // describes, so filling the panel from it would attribute a specific
      // reading to a place it was never about. Clicking one drills in instead,
      // and the panel fills once the click lands on a cell fine enough to mean
      // something.
      // A drill claims the camera without filling the panel: nothing is reported
      // yet, but the move toward the finest band has to survive whatever else was
      // under the cursor.
      if (res !== finestRes()) {
        clearSelection();
        drillToFinest(id);
        return {panel: false, camera: true};
      }
      selectCell(res, id);
      return {panel: true, camera: true};
    },
  });

  map.on("zoomend", () => updateResReadout(currentRes()));
}

// ---- restyling --------------------------------------------------------------

// A palette change is paint only. A model or severity toggle also changes which
// cells qualify, so it re-runs the join and the hatch filters — but never
// refetches a tile, because the geometry did not change.
// Opacity and outline-only are pure paint. They never change which cells qualify,
// so the join and the hatch filters are left alone — this is the cheap path the
// slider drags through.
export function applyFillStyle() { refresh({rejoin: false}); }

export function refresh({rejoin = true} = {}) {
  if (!release) return;
  ensureHashImages();
  for (const layer of release.rules.layers || []) {
    const res = Number(String(layer.id).split("-").pop());
    // Re-apply every property of every layer from the one definition, rather than
    // picking out the ones that seemed to need it.
    for (const [id, props] of Object.entries(paintFor(res))) {
      if (!map.getLayer(id)) continue;
      for (const [prop, value] of Object.entries(props)) {
        map.setPaintProperty(id, prop, value);
      }
    }
    if (map.getLayer(hashId(res))) {
      map.setLayoutProperty(hashId(res), "visibility", hashVisibility());
      if (rejoin) map.setFilter(hashId(res), ["in", ["get", "h3_id"], ["literal", agreeingIds(res)]]);
    }
    if (rejoin) applyState(res);
  }
  // Bands carry their colours as data, so any restyle rebuilds them. 746 features
  // is cheaper to regenerate than to express as a data-driven paint.
  refreshSplits();
}
