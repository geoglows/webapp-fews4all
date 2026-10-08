// The MapLibre map itself, the shared hover tooltip, layer stacking, and the
// resolution readout. Everything map-related that no single layer owns.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import {AttributionControl, Map as MapLibreMap, NavigationControl, Popup, addProtocol, setWorkerUrl} from "maplibre-gl";
import {Protocol} from "pmtiles";
import {BASEMAPS, DEFAULT_BASEMAP, basemapLayerIds} from "./basemaps.js";
import {CELL_LAYER_KINDS, LAYER_GROUPS} from "./config.js";
import {view} from "./settings.js";

setWorkerUrl(maplibreWorkerUrl);

// A PMTiles archive is one file holding a whole tile pyramid, read with HTTP range
// requests. MapLibre has no idea what a `pmtiles://` url means until a handler is
// registered, and it must exist before any source using one is added — so it
// happens here, at module load, alongside the map. The instance is kept in a
// binding because the protocol closes over its own cache.
const pmtilesProtocol = new Protocol();
addProtocol("pmtiles", pmtilesProtocol.tile);

// ---- Map ------------------------------------------------------------------

// The world repeats east and west. MapLibre draws every source into each copy —
// raster basemap, the cell and ADM vector tiles, the GeoJSON split bands — so the
// forecast carries across the seam instead of stopping at a hard edge.
//
// What does NOT repeat is our data's coordinates: a cell is only ever at its real
// longitude in [-180, 180], while the camera can sit at 200 or 560. Anything that
// moves the camera from a coordinate has to shift it into the copy the viewer is
// actually in first (see nearestCopy / boundsNear in geometry.js), or clicking a
// cell pans the whole world backwards to reach it.
//
// No `maxBounds` here either, and not only because it is now pointless: a
// whole-world maxBounds crashes maplibre-gl 6.2.0 inside the constructor.
export const map = new MapLibreMap({
  container: "map",
  style: {
    version: 8,
    sources: Object.fromEntries(BASEMAPS.flatMap((b) =>
      b.overlay ? [[b.id, b.source], [`${b.id}-overlay`, b.overlay]] : [[b.id, b.source]])),
    // Every base map lives in the style at once; switching just flips which one
    // is visible, so no restyle and no re-fetch of tiles already cached. Only the
    // visible one's attribution shows.
    layers: BASEMAPS.flatMap((b) =>
      basemapLayerIds(b).map((lid) => ({
        id: lid,
        type: "raster",
        source: lid,
        layout: {visibility: b.id === DEFAULT_BASEMAP ? "visible" : "none"},
      }))),
  },
  // Opening view, matched to River Forecast System v3: the whole world, centred
  // just north of the equator so Arctic and Antarctic both sit inside the frame.
  center: [0, 12],
  zoom: 1.3,
  minZoom: 1,
  maxZoom: 19,
  renderWorldCopies: true,
  dragRotate: false,
  pitchWithRotate: false,
  attributionControl: false,
});
map.touchZoomRotate.disableRotation();
map.addControl(new NavigationControl({showCompass: false}), "top-left");
map.addControl(new AttributionControl({compact: false}), "bottom-right");

// Current resolution readout, bottom-left.
map.addControl({
  onAdd() {
    this._el = document.createElement("div");
    this._el.className = "maplibregl-ctrl res-readout";
    this._el.id = "res-readout";
    this._el.textContent = view.dataset.resLabel + " —";
    return this._el;
  },
  onRemove() {
    this._el.remove();
  },
}, "bottom-left");

export function updateResReadout(res) {
  const el = document.getElementById("res-readout");
  if (el) el.textContent = view.dataset.resLabel + " " + res;
}

export const tooltip = new Popup({
  closeButton: false, closeOnClick: false, className: "cell-tooltip", offset: 12, maxWidth: "340px",
});
// ---- pointer coordination ---------------------------------------------------
// Hover and click used to be owned layer by layer: the cells module ran its own
// map-level mousemove, flash bound one per polygon layer. Where a flash polygon
// lies over a flagged cell BOTH fired, and the tooltip showed whichever happened
// to run last — a race, not a decision.
//
// Modules now register what they can answer for, and this composes. One query, in
// registration order, so an overlap reports every layer under the cursor instead
// of one of them at random.
const pointerSources = [];
// Declared order, not registration order. The cells register when the release
// lands and flash when it is first switched on, so which arrives first is a
// timing accident; this fixes the reading order of an overlap (river, then flash)
// and the panel's preference below. A source not named here sorts last.
const POINTER_ORDER = ["cells", "flash"];
const rank = (key) => {
  const i = POINTER_ORDER.indexOf(key);
  return i === -1 ? POINTER_ORDER.length : i;
};
const orderedSources = () => [...pointerSources].sort((a, b) => rank(a.key) - rank(b.key));

/**
 * @param {object} src
 * @param {string} src.key      identifies the source to the click resolver
 * @param {function} src.layers the layer ids it answers for, evaluated each time
 *                              because they come and go with the release
 * @param {function} [src.hover] (features, e) -> tooltip html, or "" for nothing.
 *                              Called with an EMPTY list when it has no hit, so a
 *                              source can clear its own hover state.
 * @param {function} [src.click] (features, e, ctx) -> {panel, camera} or falsy.
 *   `panel` means it filled a panel section, and decides which section is
 *   revealed. `camera` means it moved the view. `ctx.cameraTaken` says an earlier
 *   source already did — a source that would move the view must check it and
 *   stand down, or the two fight and the later one silently wins.
 */
export function registerPointerSource(src) {
  pointerSources.push(src);
}

const liveFeatures = (src, point) => {
  const ids = src.layers().filter((id) => map.getLayer(id));
  return ids.length ? map.queryRenderedFeatures(point, {layers: ids}) : [];
};

map.on("mousemove", (e) => {
  const parts = [];
  let anyHit = false;
  for (const src of orderedSources()) {
    const features = liveFeatures(src, e.point);
    if (features.length) anyHit = true;
    const html = src.hover ? src.hover(features, e) : "";
    if (html) parts.push(html);
  }
  map.getCanvas().style.cursor = anyHit ? "pointer" : "";
  if (parts.length) {
    tooltip.setLngLat(e.lngLat).setHTML(
      parts.join(`<div style="height:3px"></div>`)).addTo(map);
  } else {
    tooltip.remove();
  }
});

map.on("click", (e) => {
  // The camera is the one thing two sources cannot share. Earlier sources claim
  // it first: a flagged cell is the smaller, more precisely aimed target, and
  // when it is coarse the move is a drill toward the resolution that can actually
  // report — framing a flash polygon over the top of that would strand the viewer
  // at a zoom where the cell never fills.
  const ctx = {handled: new Set(), cameraTaken: false};
  for (const src of orderedSources()) {
    if (!src.click) continue;
    const r = src.click(liveFeatures(src, e.point), e, ctx);
    if (!r) continue;
    if (r.panel) ctx.handled.add(src.key);
    if (r.camera) ctx.cameraTaken = true;
  }
  const handled = ctx.handled;
  // Which section the panel jumps to when a click lands on both. Flash wins: it
  // is the smaller, more specific thing — a river cell is 36 km2 of hexagon,
  // a flash polygon is the actual area forecast to flood.
  for (const key of [...POINTER_ORDER].reverse()) {
    if (handled.has(key)) { panelFocus[key] && panelFocus[key](); break; }
  }
});

// Section to reveal per source, filled in by the modules so this file needs no
// import from the panel.
const panelFocus = {};
export function registerPanelFocus(key, fn) { panelFocus[key] = fn; }

// Layers that own a tooltip of their own. Each registers itself as it is created,
// and the boundary label — which lies beneath everything — yields wherever one of
// them is rendered under the cursor. A registry rather than a list in the boundary
// module: that list named `cells-fill`, and went stale the moment the cells moved
// into vector tiles and became one layer per resolution.
const tooltipLayers = new Set();

export function registerTooltipLayers(...ids) {
  for (const id of ids) tooltipLayers.add(id);
}

export function unregisterTooltipLayers(...ids) {
  for (const id of ids) tooltipLayers.delete(id);
}

// Features under `point` belonging to any registered, visible tooltip layer.
export function tooltipFeaturesAt(point) {
  const live = [...tooltipLayers].filter(
    (id) => map.getLayer(id) && map.getLayoutProperty(id, "visibility") !== "none");
  return live.length ? map.queryRenderedFeatures(point, {layers: live}) : [];
}

// Every cell layer currently on the map, coarsest resolution first so the finest
// ends up on top where the bands overlap at a handover zoom.
function cellLayers() {
  const found = map.getStyle().layers
    .map((l) => l.id)
    .filter((id) => CELL_LAYER_KINDS.some((k) => id.startsWith(k + "-")));
  const res = (id) => Number(id.split("-").pop());
  const kind = (id) => CELL_LAYER_KINDS.findIndex((k) => id.startsWith(k + "-"));
  return found.sort((a, b) => res(b) - res(a) || kind(a) - kind(b));
}

// Top of the array draws on top.
function layerOrder() {
  const {contextTop, flash, heat, backdrop} = LAYER_GROUPS;
  const cells = cellLayers();
  return [
    ...contextTop,
    ...(view.flashAboveCells ? [...flash, ...cells] : [...cells, ...flash]),
    // Under the cells on purpose. In heat mode the cells are invisible at rest, so
    // nothing of theirs covers the surface — but a hovered or selected hexagon has
    // to draw OVER it, or picking a place on the heat map gives no visible answer.
    ...heat,
    ...backdrop,
  ];
}

export function applyLayerOrder() {
  const order = layerOrder();
  // Move bottom-most first, so each moveLayer(id)-to-top leaves order[0] on top.
  for (let i = order.length - 1; i >= 0; i--) {
    if (map.getLayer(order[i])) map.moveLayer(order[i]);
  }
}
