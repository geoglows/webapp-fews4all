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

import {DATASETS} from "./config.js";
import {RELEASE_BASE} from "./sources.js";
import {loadRelease} from "./release.js";
import {map, registerPanelFocus} from "./map.js";
import {view, visibleFlashModels} from "./settings.js";
import * as panel from "./panel.js";
import * as cells from "./layers/cells_tiles.js";
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
  cells.refresh();
  flash.applyFlashColors();
  panel.rerenderPanel();
}

// ---- Wiring ---------------------------------------------------------------
// The panel's own controls act on the layers; the layers never import a control.
panel.init({
  refreshFeatures: () => cells.refresh(),
  setFlashOn: flash.setFlashOn,
});

// The map layer knows a cell was clicked; the panel knows how to draw one. This
// adapter is the whole join between them: the layer hands over the cell row and
// the forecasts still passing the filters, shaped the way the panel already reads.
cells.init({
  onSelect: (cell, forecasts) => panel.renderPanel({...cell, forecasts}),
  onClear: () => panel.renderPanel(null),
});

// Which section the panel reveals after a click. Registered here rather than in
// the layers, so neither of them has to import the panel. When a click lands on
// both a flagged cell and a flash polygon, map.js prefers "flash": it is the more
// specific thing on screen — a river cell is a 36 km2 hexagon, a flash polygon is
// the actual area forecast to flood.
registerPanelFocus("cells", () => panel.scrollPanelToSection("section-river"));
registerPanelFocus("flash", () => panel.scrollPanelToSection("section-flash"));

display.init({onDisplayChange: applyDisplaySettings});

// ---- Boot -----------------------------------------------------------------
map.once("load", () => {
  map.on("zoomend", boundaries.updateBoundariesLOD);  // ADM0 -> ADM1 -> ADM2
  // Two stacked dropdowns in the top-right column.
  map.addControl(basemapControl(), "top-right");
  map.addControl(display.displayControl(), "top-right");
  map.addControl(contextControl(), "top-right");
  // Wording is fixed now that cells are the only flagged-area build.
  view.dataset = DATASETS["h3-telescoping"];
  panel.applyDatasetWording();

  // One release: the rules, the tables, and the tile sources they point at. The
  // map draws nothing until this resolves, which is why failure goes to the
  // panel's error state rather than the console.
  // Draw the panel's model tiles straight away, in standby. The tiles are how a
  // user learns which models exist and what their colours mean, so an empty panel
  // until the first click hides the legend exactly when it is most needed.
  panel.renderPanel(null);

  loadRelease(RELEASE_BASE)
    .then((release) => {
      cells.build(release);
      // The controls were mounted before this resolved, so they are still showing
      // the app's built-in defaults. Restate them now that the release has said
      // which palettes exist and which one each model opens on.
      display.restate();
      panel.rerenderPanel();
    })
    .catch(panel.panelError);
  // Flash models default to on, so draw their polygons at startup.
  if (visibleFlashModels.has("flood_hub")) flash.setFlashOn(true);
});
