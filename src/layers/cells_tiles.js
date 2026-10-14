// The flagged-area layer, reading cell geometry from PMTiles archives and its
// data from the release tables.
//
// How this differs from the GeoJSON build it replaces, and why:
//
//   Geometry is not ours. The archives hold every cell in the world's land
//   drainage and carry one property, h3_id. What a release does is say which of
//   them to show and what color they are, and that arrives as tables.
//
//   Visibility is a filter, color is a join. MapLibre cannot filter on
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
import {EMPHASIS, EMPTY_FC, SEV_KEYS, hoverFill, selectedFill} from "../config.js";
import {applyLayerOrder, map, registerPointerSource, registerTooltipLayers, updateResReadout} from "../map.js";
import {clipToBand, lonRange, nearestCopy} from "../geometry.js";
import {corroborated, modelLabel, worstSeverity} from "../format.js";
import {modelRamp, paletteColor, rampFor, setModelPalettes, setPalettes, view, visibleModels, visibleSeverities} from "../settings.js";

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
// switching a model off changes the map's colors as well as its contents.
function visibleForecasts(res, id) {
  const fcs = release.forecastsAt(res, id).filter(
    (f) => visibleModels.has(f.model) && visibleSeverities.has(f.severity));
  // The concurrence filter is applied HERE rather than at each of the places that
  // draw something, because this one function is what the paint join, the hover,
  // the click, the coloring and the split bands all ask. Returning an empty
  // list is already how "nothing to show here" is spelled, so every one of them
  // falls into line without being told about the filter separately.
  // `corroborated`, not `concurs`: the question this filter asks is whether the
  // forecast is backed up, and it still is when the backing model is switched off.
  // The strict test made the filter unsatisfiable at one visible model — see the
  // note on both in format.js.
  if (fcs.length && view.concurrenceOnly) {
    const cell = release.cellAt(res, id);
    if (!cell || !corroborated(cell, visibleModels)) return [];
  }
  return fcs;
}

// Whether this cell is corroborated, given the models on screen. One test for the
// filter, the `agree` feature-state that colors it, and the heat surface it lands
// on — they have to be the same question or a cell passes one and fails another.
//
// There used to be a second, stricter one for the hatch (every agreeing model had
// to be visible). The hatch is gone, and with it the only thing that needed it.
function agrees(cell) {
  return corroborated(cell, visibleModels);
}

// The model whose forecast set the cell's severity — it owns the color.
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
// would keep its color until the source reloaded.
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

// Color by the joined severity, in the owning model's palette. Written as a
// nested match so one expression covers every model without a layer per model.
// Severity in a ramp picked by the cell's own severity.
function bySeverity(rampId) {
  const expr = ["match", ["coalesce", ["feature-state", "severity"], ""]];
  for (const k of SEV_KEYS) expr.push(k, paletteColor(rampId, k));
  expr.push("rgba(0,0,0,0)");
  return expr;
}

function fillColorExpr() {
  const byOwner = ["match", ["coalesce", ["feature-state", "owner"], ""]];
  for (const model of Object.keys(modelRamp)) byOwner.push(model, bySeverity(rampFor(model)));
  byOwner.push("rgba(0,0,0,0)");
  if (!view.concurHeat) return byOwner;
  // Concurrence coloring: a corroborated cell is painted in its own ramp instead
  // of its owner's, matching the concurrence surface on the heat map. The test is
  // the `agree` feature-state, which applyState sets from the same predicate the
  // heat uses — so a cell is the same color whichever view is on.
  return ["case",
    ["boolean", ["feature-state", "agree"], false], bySeverity(view.concurRampId),
    byOwner];
}

// Under the heat map the cells go invisible but stay ON the map: MapLibre only
// returns features from visible layers, so hiding them properly (visibility:none)
// would make the surface unclickable — the one thing it must not be. Zero opacity
// keeps every hexagon a hit target.
//
// Hover and selection are the exception, and have to be: without them a click on
// the heat gives no sign of what was picked, and the panel would be reporting on a
// hexagon the viewer cannot see.
//
// Outline-only mode takes the same bargain, and for the same reason. It used to
// return a flat 0 and skip the emphasis entirely, which left it the one mode in the
// app where the shape under the cursor did not lift — the white, heavier border was
// doing all the work alone. Both modes say "nothing at rest", neither says anything
// about the pointer, so both now fall through to the hover and selected cases and
// only their resting value differs. The emphasis works off the fill slider either
// way: outline-only disables that slider, it does not erase the number behind it.
function fillOpacityExpr() {
  return ["case",
    ["==", ["coalesce", ["feature-state", "severity"], ""], ""], 0,
    ["boolean", ["feature-state", "selected"], false], selectedFill(view.fillOpacity),
    ["boolean", ["feature-state", "hover"], false], hoverFill(view.fillOpacity),
    outlineOnly() || view.heatOn ? 0 : view.fillOpacity];
}

// Bands live on a GeoJSON source, so they never receive the feature-state join and
// must not be painted by an expression that reads it — fillOpacityExpr's first
// case ("no joined severity, draw nothing") is true for every band, which is what
// made them invisible. Their color is already stamped per feature, so the only
// thing left to vary is the resting opacity.
function splitOpacityExpr() {
  // The bands exist only on cells holding two or more models, so leaving them
  // drawn under the heat would put the concurrence cells back on the map by the
  // back door — the exact thing the surface is meant to have absorbed.
  if (view.heatOn) return 0;
  return outlineOnly() ? 0 : view.fillOpacity;
}

function lineColorExpr() {
  return ["case",
    ["boolean", ["feature-state", "selected"], false], "#ffffff",
    fillColorExpr()];
}

// Border weight carries severity alongside color. With the palettes sampled
// through a narrow band the three rungs are close in tone, so the outline is doing
// real work here rather than decoration: an extreme cell reads as heavier even
// where its fill is hard to separate from a warning's. The weights are the
// viewer's to set (Display -> Outline weight); only the fallbacks for a cell with
// no severity yet, and the outline-only boost, are fixed here.
// With no fill to carry the signal, outline-only mode needs every rung heavier.
// A constant rather than a multiplier: it keeps the RATIO the viewer set on the
// sliders, where scaling would stretch the gap between rungs as well.
const OUTLINE_ONLY_BOOST = 0.8;

// "Outline only" belongs to the CELLS view, and its checkbox is shown only there.
// A setting the viewer cannot see must not still be acting on the map: left on and
// then switched to the heat map, it used to flatten the fill of the one hexagon
// under the cursor to nothing, so a pick on the surface showed a bare outline and
// no control anywhere explained why. Read through here, never off `view` directly.
const outlineOnly = () => view.outlineOnly && !view.heatOn;

function severityWidth(byRung, fallback, boost = 0) {
  const expr = ["match", ["coalesce", ["feature-state", "severity"], ""]];
  for (const k of SEV_KEYS) expr.push(k, (byRung[k] || 0) + boost);
  expr.push(fallback);
  return expr;
}

function lineWidthExpr() {
  const base = outlineOnly()
    ? severityWidth(view.lineWidth, 2, OUTLINE_ONLY_BOOST)
    : severityWidth(view.lineWidth, 1);
  return ["case",
    ["boolean", ["feature-state", "selected"], false], EMPHASIS.selectedLine,
    ["boolean", ["feature-state", "hover"], false], EMPHASIS.hoverLine,
    base];
}

/**
 * Every paint property of every layer this module owns, for one resolution.
 *
 * Creation and restyle both read this, which is the point. They used to each
 * spell the paint out separately and had already drifted twice: the bands were
 * created with their own opacity but restyled with the tile fill's — which is
 * zero for anything the feature-state join has not reached, so a palette change
 * made them vanish — and the outline's color was set at creation and never
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
    [lineId(res)]: {
      "line-color": lineColorExpr(),
      "line-width": lineWidthExpr(),
      // Same bargain as the fill: nothing at rest under the heat, but a hovered or
      // selected hexagon keeps its outline, which is what makes a pick legible
      // against a blurred surface.
      "line-opacity": ["case",
        ["==", ["coalesce", ["feature-state", "severity"], ""], ""], 0,
        ["boolean", ["feature-state", "selected"], false], EMPHASIS.lineOpacity,
        ["boolean", ["feature-state", "hover"], false], EMPHASIS.lineOpacity,
        view.heatOn ? 0 : 0.85],
    },
  };
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
 * than moving with whichever model happened to be listed first. Color is stamped
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

    // Concurrence coloring already has an answer for this cell — one hue meaning
    // "models agree here" — and bands would be arguing with it. Left whole, the
    // tile fill paints it in the concurrence ramp. With that option off, nothing
    // changes and the bands behave exactly as they always have.
    if (view.concurHeat && agrees(cell)) continue;

    // Splitting is only worth doing when it shows something. If every model in the
    // cell is on the same palette, the bands would be three shades of one ramp
    // divided by invisible seams — so the cell is left whole and the tile fill
    // paints it at the WORST severity present, which is the reading that matters.
    // Bands come back as soon as two models are on different palettes, where the
    // division carries real information.
    if (new Set(models.map(rampFor)).size < 2) continue;

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
                     color: paletteColor(rampFor(b.model), sev)},
      });
    }
  }
  return {type: "FeatureCollection", features: out};
}

/**
 * The tile fill's filter, minus whatever the bands are covering.
 *
 * Both layers are semi-transparent, so a band drawn over the whole-cell fill would
 * blend with it and show a muddied color rather than the model's own. A split
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

    // Bands sit directly over the tile fill and under the outline, so a
    // split cell reads as one hexagon divided rather than shapes stacked on it.
    if (!map.getSource(SPLIT_SRC)) map.addSource(SPLIT_SRC, {type: "geojson", data: EMPTY_FC});
    map.addLayer({
      id: splitId(res), type: "fill", source: SPLIT_SRC,
      minzoom: layer.minzoom, ...(layer.maxzoom ? {maxzoom: layer.maxzoom} : {}),
      filter: ["==", ["get", "res"], res],
      paint: paint[splitId(res)],
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
  // bands — it is cut out of the fill so the colors stay clean — so querying the
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
      // AFTER setHover, deliberately: the toggle silences the label, it does not
      // stop the cell lighting up or being clickable. Returning "" is how a source
      // says it has nothing to put in the tooltip, so flash can still fill it on an
      // overlap.
      if (!view.cellTooltip) return "";
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
// cells qualify, so it re-runs the join — but never
// refetches a tile, because the geometry did not change.
// Opacity and outline-only are pure paint. They never change which cells qualify,
// so the join is left alone — this is the cheap path the
// slider drags through.
export function applyFillStyle() { refresh({rejoin: false}); }

export function refresh({rejoin = true} = {}) {
  if (!release) return;
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
    if (rejoin) applyState(res);
  }
  // Bands carry their colors as data, so any restyle rebuilds them. 746 features
  // is cheaper to regenerate than to express as a data-driven paint.
  refreshSplits();
}
