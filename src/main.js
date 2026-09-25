// Entry point: it wires the modules together and boots the map. Nothing else
// belongs here — if a behaviour has a home, it lives in that module.
//
//   config.js     constants: severity, ramps, wording, every source/layer id
//   settings.js   live view state (`view`) + the colours derived from it
//   format.js     pure formatting, labels, links, severity ranking
//   geometry.js   bounds + the clipping that splits a cell between models
//   basemaps.js   base-map definitions and thumbnails
//   map.js        the MapLibre map, shared tooltip, layer stacking, readout
//   panel.js      the side panel (renders only; its actions are injected here)
//   ui/           generic widgets with no app knowledge
//   layers/       one module per pipeline output: cells, flash, context
//   controls/     the map's dropdown controls
//
// The layers call into the panel, and the panel calls back out through the
// actions injected below — that injection is what keeps the import graph acyclic.
import "./style.css";                       // Tailwind + theme + MapLibre overrides
import "maplibre-gl/dist/maplibre-gl.css";  // MapLibre's own stylesheet (from npm)

import {DEFAULT_DATASET} from "./config.js";
import {map} from "./map.js";
import {visibleFlashModels} from "./settings.js";
import * as panel from "./panel.js";
import * as cells from "./layers/cells.js";
import * as flash from "./layers/flash.js";
import * as boundaries from "./layers/boundaries.js";
import * as streams from "./layers/streams.js";
import {basemapControl} from "./controls/basemap.js";
import {contextControl} from "./controls/context.js";
import * as display from "./controls/display.js";

// Everything a ramp/hatch/severity change touches: the map's stamped colours and
// the panel's legends. The Display menu fires this; it doesn't know what it hits.
function applyDisplaySettings() {
  cells.ensureHashImages();
  cells.refreshFeatures();
  flash.applyFlashColors();
  panel.rerenderPanel();
}

// ---- Wiring ---------------------------------------------------------------
// The panel's own controls act on the layers; the layers never import a control.
panel.init({
  refreshFeatures: cells.refreshFeatures,
  setFlashOn: flash.setFlashOn,
});

// Switching dataset has to restate the Display menu's highlight, which cells.js
// must not reach into directly.
cells.init({onDatasetChange: display.highlightDataset});

display.init({onDisplayChange: applyDisplaySettings});

// ---- Boot -----------------------------------------------------------------
map.once("load", () => {
  map.on("zoomend", cells.onZoomEnd);
  map.on("zoomend", streams.updateStreamsLOD);        // rivers telescope by zoom
  map.on("zoomend", boundaries.updateBoundariesLOD);  // ADM0 -> ADM1 -> ADM2
  // Two stacked dropdowns in the top-right column.
  map.addControl(basemapControl(), "top-right");
  map.addControl(display.displayControl(), "top-right");
  map.addControl(contextControl(), "top-right");
  // `false` = don't reframe to the data's extent; the configured world view above
  // is the opening shot, so it doesn't drift as the flagged footprint changes.
  cells.loadDataset(DEFAULT_DATASET, false);  // interactions bind on first build
  // Flash models default to on, so draw their polygons at startup.
  if (visibleFlashModels.has("flood_hub")) flash.setFlashOn(true);
});
