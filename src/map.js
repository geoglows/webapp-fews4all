// The MapLibre map itself, the shared hover tooltip, layer stacking, and the
// resolution readout. Everything map-related that no single layer owns.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import {AttributionControl, Map as MapLibreMap, NavigationControl, Popup, setWorkerUrl} from "maplibre-gl";
import {BASEMAPS, DEFAULT_BASEMAP, basemapLayerIds} from "./basemaps.js";
import {LAYER_ORDER} from "./config.js";
import {view} from "./settings.js";

setWorkerUrl(maplibreWorkerUrl);

// ---- Map ------------------------------------------------------------------

// A single copy of the world. The data only exists once, so `renderWorldCopies`
// stays off; otherwise you can scroll into empty repeated tiles where no
// cells/basins are drawn. That alone clamps panning to one world width, which
// is what Leaflet needed `maxBounds` for — and just as well, because a
// whole-world `maxBounds` crashes maplibre-gl 6.2.0 inside the constructor's
// first resize (a small regional one is fine).
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
  renderWorldCopies: false,
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
export function applyLayerOrder() {
  // Move bottom-most first so each moveLayer(id)-to-top leaves LAYER_ORDER[0] on top.
  for (let i = LAYER_ORDER.length - 1; i >= 0; i--) {
    if (map.getLayer(LAYER_ORDER[i])) map.moveLayer(LAYER_ORDER[i]);
  }
}
