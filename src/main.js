// Entry point: it wires the modules together and boots the map. Nothing else
// belongs here — if a behaviour has a home, it lives in that module.
//
//   config.js     constants: severity, ramps, wording, every source/layer id
//   settings.js   live view state (`view`) + the colors derived from it
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
import {loadReleaseIndex, releaseBase, resolveRelease} from "./sources.js";
import * as timeline from "./controls/timeline.js";
import {loadRelease, loadReleaseCached} from "./release.js";
import {map, registerPanelFocus} from "./map.js";
import {view, visibleFlashModels} from "./settings.js";
import * as panel from "./panel.js";
import * as cells from "./layers/cells_tiles.js";
import * as heat from "./layers/heat.js";
import * as flash from "./layers/flash.js";
import * as streams from "./layers/streams.js";
import {basemapControl} from "./controls/basemap.js";
import * as views from "./controls/views.js";
import {init as initViews, noteManualChange} from "./views.js";
import {pointerControl} from "./controls/pointer.js";
import * as display from "./controls/display.js";
import * as filters from "./controls/filters.js";

// Everything that decides WHICH flagged areas are drawn. The cell layer and the
// heat surface are two renderings of one filtered set — both read visibleModels,
// visibleSeverities and the concurrence filter — so they are rebuilt together.
//
// They used to be rebuilt separately, and that is exactly how they drifted: the
// Display menu called both, the side panel's model filter called only the cells,
// and so switching a model off left the heat map showing it. That filter lives in
// the Filters menu now and comes through the same single path.
function refreshFlaggedAreas() {
  cells.refresh();
  heat.refresh();
}

// Everything a ramp, severity or model change touches: the map's stamped colors
// and the panel's legends and tiles. Both menus fire this; neither knows what it
// hits. One function rather than two because a filter change and a color change
// land in exactly the same places — the only asymmetry is flash polygons, which
// have to be fetched, and that is wired separately below.
function applyDisplaySettings() {
  refreshFlaggedAreas();
  flash.applyFlashColors();
  panel.rerenderPanel();
}

// ---- Wiring ---------------------------------------------------------------
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

// Touching either menu by hand means no named view describes the map any more,
// so both change channels run through views.noteManualChange first. Here rather
// than in the menus' own handlers: one place to keep right, and a control added
// to either menu later cannot forget to do it.
// Clearing the view also has to redraw the Views list, or reopening it would
// still show "ON" beside a view the map stopped matching.
const handEdit = () => { noteManualChange(); views.restate(); applyDisplaySettings(); };
display.init({onDisplayChange: handEdit});
// A view writes to BOTH menus, so after one lands they both have to redraw from
// the new state or their boxes keep claiming what was ticked a moment ago.
initViews({
  onViewChange: () => {
    display.restate();
    filters.restate();
    applyDisplaySettings();
  },
  setFlashOn: flash.setFlashOn,
});
// Switching a flash model on is the one filter that loads data rather than hiding
// it, so it gets its own channel straight to the layer.
filters.init({
  onFilterChange: handEdit,
  setFlashOn: flash.setFlashOn,
});

// ---- Boot -----------------------------------------------------------------
map.once("load", () => {
  // Pan/select, directly under the zoom buttons it shares a column with.
  map.addControl(pointerControl(), "top-left");
  // The river network, on from the start: the flagged cells are hexagons over an
  // empty basemap without it, and "which river is this" is the first question a
  // warned cell raises. Switched on BEFORE the Display menu is mounted, so its
  // Map tab renders from the state it is reporting rather than being corrected a
  // moment later. The layer's own control used to be a third dropdown in this
  // column; it is one checkbox, and it lives on that tab now.
  streams.setStreamsOn(true);

  // The stacked dropdowns in the top-right column. Display and Filters sit next to
  // each other: how the map is drawn, then what reaches it.
  map.addControl(basemapControl(), "top-right");
  // Views first in the column: it is the control that writes to the two below it,
  // and the one a reader meeting the app should find before either of them.
  map.addControl(views.viewsControl(), "top-right");
  map.addControl(display.displayControl(), "top-right");
  map.addControl(filters.filtersControl(), "top-right");
  // Wording is fixed now that cells are the only flagged-area build.
  view.dataset = DATASETS["h3-telescoping"];
  panel.applyDatasetWording();

  // One release: the rules, the tables, and the tile sources they point at. The
  // map draws nothing until this resolves, which is why failure goes to the
  // panel's error state rather than the console.
  // Draw the panel's model tiles straight away, in standby. The tiles are how a
  // user learns which models exist and what their colors mean, so an empty panel
  // until the first click hides the legend exactly when it is most needed.
  panel.renderPanel(null);

  // Which release to draw comes from the pipeline's own pointer rather than a
  // constant in the source, so the morning's run reaches the map without an edit.
  // Draw a different day on the tiles already loaded.
  //
  // Deliberately NOT display.restate()/filters.restate(): those reset the
  // controls to the release's defaults, which is right once at boot and wrong on
  // every day after — it would throw away the user's model and severity choices
  // each time the timeline moved.
  //
  // The token guards against out-of-order arrivals. Scrubbing fast fires several
  // loads at once, and without it a slow early day could land after a fast later
  // one and leave the map showing a date the header disagrees with.
  let showToken = 0;
  function showDay(date) {
    const token = ++showToken;
    return loadReleaseCached(releaseBase(date), date)
      .then((release) => {
        if (token !== showToken) return;
        cells.swap(release);
        heat.build(release);
        flash.build(release);
        panel.rerenderPanel();
        timeline.setCurrent(date);
      })
      .catch(panel.panelError);
  }

  timeline.init({
    show: showDay,
    // Warm the cache without touching the map, so playback does not stall on a
    // fetch it could have started a second earlier.
    prefetch: (date) => { loadReleaseCached(releaseBase(date), date).catch(() => {}); },
  });

  resolveRelease()
    .then(({release: name, base}) => loadRelease(base).then((release) => {
      return {release, name};
    }))
    .then(({release, name}) => {
      cells.build(release);
      heat.build(release);
      flash.build(release);
      // The controls were mounted before this resolved, so they are still showing
      // the app's built-in defaults. Restate them now that the release has said
      // which palettes exist and which one each model opens on.
      display.restate();
      filters.restate();
      panel.rerenderPanel();
      // The timeline is the last thing to arrive and the only optional one: a
      // deployment without releases.json simply never gets a bar.
      loadReleaseIndex().then((index) => timeline.build(index, name));
    })
    .catch(panel.panelError);
  // Flash models default to on, so draw their polygons at startup.
  if (visibleFlashModels.has("flood_hub")) flash.setFlashOn(true);
});
