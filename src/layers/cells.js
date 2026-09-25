// The flagged-area layer — H3 cells or basins, whichever dataset is loaded
// (public/data_h3cells.geojson, public/data_basins.geojson). Owns the source, the
// paint, the telescoping by zoom, and hover/selection.
import {DATASETS, DATASETS_MENU, DEFAULT_COLOR, EMPTY_FC, FILL, HASH, LINE, PANEL_MODELS, RAMPS, RES_START_ZOOM, RES_ZOOM_STEP, SEV_KEYS, SRC, rampColor, stateOn} from "../config.js";
import {applyLayerOrder, map, tooltip, updateResReadout} from "../map.js";
import {boundsOf, clipToBand, lonRange} from "../geometry.js";
import {darken, ownerModel, worstSeverity} from "../format.js";
import {clearFlashSelection, flashUnderCursor} from "../layers/flash.js";
import {modelRamp, unitLabel, view, visibleModels, visibleSeverities} from "../settings.js";
import {applyDatasetWording, panelError, renderPanel, scrollPanelToSection, showNoData} from "../panel.js";

let interactionsBound = false;      // cell hover/click handlers bound once

let resolutions = [];
let datasetHasCoModels = false;     // set from the collection on load
const byRes = {};

// Injected by main.js so this layer never imports a control or the panel's own
// callers — see the wiring block there.
let actions = {onDatasetChange() {}};
export function init(a) { actions = Object.assign(actions, a); }

// The cell currently selected, for the Zoom To control. Read-only on purpose:
// selection changes through selectFeature/clearSelection.
export const selectedFeature = () => selected;

// MapLibre flattens feature properties through its tiling pipeline, so the
// nested `forecasts`/`impact`/`models` members come back from a rendered
// feature as JSON strings. Keep the real objects here, keyed by the id we
// stamp on each feature, and read everything for the panel out of this map.
const featureById = new Map();
let nextFeatureId = 1;

// Two models are only in agreement where they forecast the SAME place, which at
// a telescoping level means the same finest-resolution cell. A coarse cell holds
// every forecast beneath it, so counting its distinct models would hash it for
// two models that never meet — the pipeline therefore stamps `co_models`: the
// model sets that really do share one finest cell somewhere inside the feature.
// A set counts only while all of its models are switched on. In such a build a
// feature with no `co_models` is emphatically NOT agreeing, so the collection
// says up front that it carries the field (`agreement: "co_models"`); only a
// build that doesn't — a single-level one, where a feature IS a finest cell —
// falls back to counting the feature's own models.
function agrees(props, modelNames) {
  if (!datasetHasCoModels) return modelNames.length >= 2;
  const co = props.co_models;
  if (!Array.isArray(co)) return false;
  return co.some((set) => set.length >= 2 && set.every((m) => visibleModels.has(m)));
}

// Keep only forecasts from visible models and severities; drop emptied cells;
// stamp every colour the layers read, so a ramp change is just a rebuild.
function visibleFeatures(features) {
  const out = [];
  for (const f of features) {
    const fcs = f.properties.forecasts.filter((x) =>
      visibleModels.has(x.model) && visibleSeverities.has((x.severity || "").toLowerCase()));
    if (!fcs.length) continue;
    // PANEL_MODELS order (not first-seen order) so slices sit in a stable place.
    const modelNames = PANEL_MODELS.filter((m) => fcs.some((x) => x.model === m));
    const severity = worstSeverity(fcs);
    const owner = ownerModel(fcs);
    const agree = agrees(f.properties, modelNames);
    const emph = agree && view.hatchOn;
    const fill = rampColor(modelRamp[owner], severity);
    const shared = Object.assign({}, f.properties, {
      forecasts: fcs, model_count: fcs.length, severity,
      models: modelNames, agree, emph,
      hash_image: hashImageId(modelRamp[owner], severity),
      line_color: emph ? darken(fill) : fill,
    });

    // Try to split first: if any slice comes back empty the cell is drawn whole,
    // so a stray geometry can never leave part of a cell unpainted.
    let slices = null;
    if (modelNames.length > 1) {
      const [x0, x1] = lonRange(f.geometry);
      const span = x1 - x0;
      slices = span > 0 ? modelNames.map((m, i) => ({
        model: m,
        geometry: clipToBand(f.geometry,
          x0 + (span * i) / modelNames.length,
          x0 + (span * (i + 1)) / modelNames.length),
      })) : null;
      if (slices && slices.some((sl) => !sl.geometry)) slices = null;
    }

    const whole = {
      type: "Feature",
      id: nextFeatureId++,
      geometry: f.geometry,
      properties: Object.assign({}, shared, {
        role: slices ? "outline" : "cell", fill_color: fill,
      }),
    };
    whole._pieces = [whole.id];
    featureById.set(whole.id, whole);
    out.push(whole);

    if (slices) {
      for (const sl of slices) {
        const sev = worstSeverity(fcs.filter((x) => x.model === sl.model));
        const piece = {
          type: "Feature",
          id: nextFeatureId++,
          geometry: sl.geometry,
          properties: Object.assign({}, shared, {
            role: "slice", half_model: sl.model,
            fill_color: rampColor(modelRamp[sl.model], sev),
          }),
        };
        whole._pieces.push(piece.id);
        // Every piece resolves to the cell it belongs to, so a click on a slice
        // opens the whole cell's panel and frames its full extent.
        featureById.set(piece.id, whole);
        out.push(piece);
      }
    }
  }
  return out;
}
// Colours are stamped per feature in visibleFeatures (a ramp change is a
// rebuild), so the paint properties just read them back.
const FILL_ROLES = ["literal", ["cell", "slice"]];      // carry the fill
const OUTLINE_ROLES = ["literal", ["cell", "outline"]]; // carry border + hash
const featureColor = (prop) => ["coalesce", ["get", prop], DEFAULT_COLOR];

// Resting/hover/selected fill opacity, from the Display menu's slider. Outline
// mode drops the fill entirely and leans on a heavier border.
function fillOpacityExpr() {
  if (view.outlineOnly) return 0;
  return ["case",
    stateOn("selected"), Math.min(1, view.fillOpacity * 0.7),
    stateOn("hover"), Math.min(1, view.fillOpacity * 2),
    view.fillOpacity];
}
function lineWidthExpr() {
  return ["case",
    stateOn("selected"), 4,
    stateOn("hover"), 3,
    ["get", "emph"], 2.5,
    view.outlineOnly ? 2 : 1];
}

// Hash tile: fine diagonal lines in the given colour, marking agreement basins.
// One image per severity (its darker shade). Rendered at pixelRatio 2 so it
// stays crisp on retina screens, then used as a data-driven fill-pattern.
function makeHashImage(color, size = 16, w = 2) {
  const cv = document.createElement("canvas");
  cv.width = cv.height = size;
  const g = cv.getContext("2d");
  g.lineWidth = w;
  g.lineCap = "square";
  g.strokeStyle = color;
  for (const o of [-size, 0, size]) {   // wraps keep the tile seamless
    g.beginPath();
    g.moveTo(o, size);
    g.lineTo(size + o, 0);
    g.stroke();
  }
  return g.getImageData(0, 0, size, size);
}
// One hash image per ramp and severity, made on demand: the pattern is a darker
// shade of the cell's own colour, so it reads as that polygon's hatching rather
// than a separate layer sitting on top.
const hashImageId = (ramp, sev) => `mm-hash-${ramp}-${(sev || "").toLowerCase()}`;
export function ensureHashImages() {
  const ramps = new Set([...PANEL_MODELS.map((m) => modelRamp[m]), RAMPS[0].id]);
  for (const ramp of ramps) {
    for (const k of SEV_KEYS) {
      const id = hashImageId(ramp, k);
      if (!map.hasImage(id)) {
        map.addImage(id, makeHashImage(darken(rampColor(ramp, k))), {pixelRatio: 2});
      }
    }
  }
}

function addCellLayers() {
  const source = {type: "geojson", data: EMPTY_FC};
  if (view.dataset.attribution) source.attribution = view.dataset.attribution;
  map.addSource(SRC, source);
  map.addLayer({
    id: FILL,
    type: "fill",
    source: SRC,
    filter: ["in", ["get", "role"], FILL_ROLES],
    paint: {
      "fill-color": featureColor("fill_color"),
      "fill-opacity": fillOpacityExpr(),
    },
  });
  // Cells where the models concur get a diagonal hash in a darker shade of their
  // own colour. `emph` is already false when the highlight is switched off, so
  // the filter covers the toggle as well as the agreement test.
  ensureHashImages();
  map.addLayer({
    id: HASH,
    type: "fill",
    source: SRC,
    filter: ["all", ["==", ["get", "emph"], true],
      ["in", ["get", "role"], OUTLINE_ROLES]],
    paint: {
      // A plain string expression, like the match this replaced — every feature
      // is stamped with an image id, and ensureHashImages() has registered it.
      "fill-pattern": ["get", "hash_image"],
      "fill-opacity": 0.55,
    },
  });
  map.addLayer({
    id: LINE,
    type: "line",
    source: SRC,
    filter: ["in", ["get", "role"], OUTLINE_ROLES],
    paint: {
      "line-color": [
        "case",
        stateOn("selected"), "#ffffff",
        featureColor("line_color"),
      ],
      "line-width": lineWidthExpr(),
      "line-opacity": ["case", ["get", "emph"], 1, 0.85],
    },
  });
}

// Opacity and outline mode are pure paint changes — no rebuild needed.
export function applyFillStyle() {
  if (map.getLayer(FILL)) map.setPaintProperty(FILL, "fill-opacity", fillOpacityExpr());
  if (map.getLayer(LINE)) map.setPaintProperty(LINE, "line-width", lineWidthExpr());
  if (map.getLayer(HASH)) {
    map.setLayoutProperty(HASH, "visibility", view.outlineOnly ? "none" : "visible");
  }
}

// Hover label: just the identifier (basin number) and its severity.
function tooltipHtml(p) {
  return `${unitLabel(p)} ${p.cell_id} · <b>${p.severity || "?"}</b>`;
}

// Hover and selection track the whole cell, not the piece under the cursor: a
// split cell is several features on the map but one thing to the user.
let hovered = null;
export let selected = null;

// Apply a feature-state to every piece of a cell (outline + any slices), so a
// split cell highlights as one.
function setCellState(feature, patch) {
  if (!feature || !map.getSource(SRC)) return;
  for (const id of feature._pieces) map.setFeatureState({source: SRC, id}, patch);
}

function setHover(id) {
  const feature = id == null ? null : featureById.get(id) || null;
  if (hovered === feature) return;
  setCellState(hovered, {hover: false});
  hovered = feature;
  setCellState(hovered, {hover: true});
}

function clearSelection() {
  setCellState(selected, {selected: false});
  selected = null;
  view.selectedBasinId = null;
  renderPanel(null);                // standby tiles (both models still listed)
}

function selectFeature(id) {
  const feature = featureById.get(id);
  if (!feature) return;
  setCellState(selected, {selected: false});
  selected = feature;
  setCellState(selected, {selected: true});
  renderPanel(feature.properties);
  scrollPanelToSection("section-river");   // reveal the river tiles just filled
  view.selectedBasinId = String(feature.properties.cell_id);
  fitToFeature(feature);            // frame what was just selected
}

// Frame the selected cell. Was routed through the Zoom To control's basin/stream/
// district choice; with that control gone it is simply the cell's own extent.
function fitToFeature(feature) {
  const b = boundsOf([feature]);
  if (!b.isEmpty()) map.fitBounds(b, {maxZoom: 11, padding: 40});
}

function bindCellInteractions() {
  map.on("mousemove", FILL, (e) => {
    const f = e.features[0];
    if (!f) return;
    map.getCanvas().style.cursor = "pointer";
    setHover(f.id);
    // Only a flash polygon on top outranks the basin tooltip; the basin beats
    // districts (which sit behind it).
    if (flashUnderCursor(e.point)) return;
    tooltip.setLngLat(e.lngLat).setHTML(tooltipHtml(featureById.get(f.id).properties)).addTo(map);
  });
  map.on("mouseleave", FILL, () => {
    map.getCanvas().style.cursor = "";
    setHover(null);
    tooltip.remove();
  });
  // Only the finest telescoping level carries statistics. Clicking a finer
  // cell opens its panel; clicking a coarser cell drills in (zooms to its
  // extent to reveal the next level up); clicking the basemap deselects.
  map.on("click", (e) => {
    // A flash polygon under the cursor handles its own click (fit-to-screen);
    // don't drill cells or deselect underneath it.
    if (flashUnderCursor(e.point)) return;
    const hits = map.queryRenderedFeatures(e.point, {layers: [FILL]});
    const f = hits.length ? featureById.get(hits[0].id) : null;
    // Clicking off every polygon (no basin here, and no flash — that returns
    // above) closes all tiles: clear the flash selection too, then the basin.
    if (!f) { clearFlashSelection(); clearSelection(); return; }
    if (Number(f.properties.res) === Number(finestRes())) selectFeature(hits[0].id);
    else drillInto(f);
  });
}

// ---- Resolution switching -------------------------------------------------

const fcByRes = {};

function fcFor(res) {
  if (!fcByRes[res]) {
    fcByRes[res] = {
      type: "FeatureCollection",
      features: visibleFeatures(byRes[String(res)].features),
    };
  }
  return fcByRes[res];
}

// resolutions is sorted ascending, so the last entry is the finest level.
const finestRes = () => (resolutions.length ? resolutions[resolutions.length - 1] : null);

// The minimum zoom at which a resolution appears. A dataset can override the
// per-level thresholds (see DATASETS.zoomByRes); otherwise every level steps
// up uniformly from RES_START_ZOOM.
function resZoomThreshold(res, index) {
  return view.dataset.zoomByRes && view.dataset.zoomByRes[res] != null
    ? view.dataset.zoomByRes[res]
    : RES_START_ZOOM + index * RES_ZOOM_STEP;
}

function zoomToRes(zoom) {
  let chosen = resolutions[0];
  resolutions.forEach((r, i) => {
    if (zoom >= resZoomThreshold(r, i)) chosen = r;
  });
  return chosen;
}

// Clicking a coarser (non-finest) cell drills in: zoom to that cell's extent,
// and at least to the next level's threshold, so the finer cells inside it
// come into view. The normal zoom-driven telescoping then takes over.
function drillInto(feature) {
  const b = boundsOf([feature]);
  if (b.isEmpty()) return;
  const i = resolutions.indexOf(Number(feature.properties.res));
  const next = (i >= 0 && i < resolutions.length - 1) ? resolutions[i + 1] : null;
  const cam = map.cameraForBounds(b, {padding: 40});
  let zoom = cam ? cam.zoom : map.getZoom();
  if (next != null) zoom = Math.max(zoom, resZoomThreshold(next, i + 1) + 0.05);
  map.easeTo({center: cam ? cam.center : b.getCenter(), zoom, duration: 600});
}

let currentRes = null;

function showRes(res) {
  if (res === currentRes) return;
  clearSelection();
  setHover(null);
  tooltip.remove();
  map.getSource(SRC).setData(fcFor(res));
  currentRes = res;
  updateResReadout(res);
}

// Rebuild the current data after a model toggle (cached collections are stale).
export function refreshFeatures() {
  for (const k in fcByRes) delete fcByRes[k];
  featureById.clear();
  const res = currentRes;
  currentRes = null;
  if (res != null) showRes(res);
}

function buildFromGeojson(geo, fit) {
  // Resolve the wording before anything renders — the attribution is baked
  // into the GeoJSON source, so it has to be known before we add it.
  if (geo && DATASETS[geo.kind]) view.dataset = DATASETS[geo.kind];
  // Belt and braces: honour the flag, but also accept an older file that
  // carries co_models without announcing it.
  datasetHasCoModels = !!geo && (geo.agreement === "co_models" ||
    (geo.features || []).some((f) => f.properties && f.properties.co_models));

  const feats = (geo && geo.features) || [];
  const grouped = {};
  for (const f of feats) {
    const r = (f.properties && f.properties.res != null) ? f.properties.res : 0;
    (grouped[r] = grouped[r] || []).push(f);
  }
  resolutions = (Array.isArray(geo.resolutions) && geo.resolutions.length
    ? geo.resolutions.map(Number)
    : Object.keys(grouped).map(Number)).sort((a, b) => a - b);
  for (const r of resolutions) {
    byRes[String(r)] = {type: "FeatureCollection", features: grouped[r] || []};
  }

  if (!resolutions.some((r) => byRes[String(r)].features.length)) {
    showNoData(view.dataset.unit.toLowerCase());
    return;
  }

  applyDatasetWording();

  addCellLayers();
  // Bind hover/click once, now that the fill layer first exists; the layer id is
  // reused on every dataset switch so the delegated handlers keep working.
  if (!interactionsBound) { bindCellInteractions(); interactionsBound = true; }
  applyLayerOrder();   // restore rivers/flash/basins/districts stacking after rebuild

  showRes(resolutions[0]);
  if (fit) {
    const extent = boundsOf(fcFor(resolutions[0]).features);
    if (!extent.isEmpty()) map.fitBounds(extent, {padding: 40, maxZoom: 5, duration: 0});
  }
  showRes(zoomToRes(map.getZoom()));
}

// ---- Dataset switching (h3 / basins) --------------------------------------

function teardown() {
  clearSelection();
  setHover(null);
  tooltip.remove();
  for (const id of [LINE, HASH, FILL]) if (map.getLayer(id)) map.removeLayer(id);
  if (map.getSource(SRC)) map.removeSource(SRC);
  for (const k in fcByRes) delete fcByRes[k];
  for (const k in byRes) delete byRes[k];
  featureById.clear();
  resolutions = [];
  currentRes = null;
}

export function loadDataset(key, fit) {
  const ds = DATASETS_MENU.find((d) => d.key === key);
  if (!ds) return;
  view.currentDatasetKey = key;
  actions.onDatasetChange();           // let the Display menu restate itself
  teardown();
  fetch(ds.url)
    .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status + " for " + ds.url); return r.json(); })
    .then((geo) => buildFromGeojson(geo, fit))
    .catch(panelError);
}

export function onZoomEnd() {
  showRes(zoomToRes(map.getZoom()));
}
