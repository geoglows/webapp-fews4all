// Constants: severity ordering, colour ramps, model and dataset wording, and every
// MapLibre source/layer id. Pure data — no state, no map, no DOM.
import {DATA} from "./sources.js";

// Severity is the ordering (and the wording); the colour comes from whichever
// ramp the model is currently assigned in the Display menu.
export const SEVERITY = {
  warning: {rank: 1, label: "Warning"},
  danger: {rank: 2, label: "Danger"},
  extreme: {rank: 3, label: "Extreme"},
};
export const SEV_KEYS = Object.keys(SEVERITY);
export const DEFAULT_COLOR = "#4da3ff";
export const HASH_COLOR = "#0f172a";   // neutral dark for the multi-model legend swatch
                                // (on the map the hash/outline are per-severity)

// ---- Colour ramps ---------------------------------------------------------
// One ramp = three shades, light (warning) to dark (extreme). A ramp is picked
// per model, so GEOGLOWS and Flood Hub can be told apart at a glance; the flash
// overlay takes its two tiers from the light and dark ends of its own ramp.
// "Amber" is the app's historic palette and stays the default everywhere, so
// the map opens looking as it always has until a ramp is changed.
export const RAMPS = [
  {id: "amber", label: "Amber – Red", warning: "#ffd21f", danger: "#ff8c00", extreme: "#e0201b"},
  {id: "blue", label: "Blues", warning: "#93c5fd", danger: "#3b82f6", extreme: "#1e40af"},
  {id: "purple", label: "Purples", warning: "#a78bfa", danger: "#8b5cf6", extreme: "#6d28d9"},
  {id: "green", label: "Greens", warning: "#86efac", danger: "#22c55e", extreme: "#15803d"},
  {id: "teal", label: "Teals", warning: "#5eead4", danger: "#14b8a6", extreme: "#0f766e"},
  {id: "pink", label: "Pinks", warning: "#f9a8d4", danger: "#ec4899", extreme: "#9d174d"},
];
const RAMP_BY_ID = Object.fromEntries(RAMPS.map((r) => [r.id, r]));
const rampOf = (id) => RAMP_BY_ID[id] || RAMPS[0];
export const rampColor = (id, sev) => rampOf(id)[(sev || "").toLowerCase()] || DEFAULT_COLOR;

// MapLibre measures zoom against a 512px world where Leaflet used 256px, so
// every zoom number in this file sits one step below the Leaflet equivalent
// for the same view on screen.
export const RES_START_ZOOM = 2;
export const RES_ZOOM_STEP = 2;

// Which layer the map opens on. H3 cells are built from the res-6 stream
// crosswalk (scripts/build_cells_h3.py), so a flooding river lights up its whole
// path; the basin build stays available from the Flagged Area Type control.
export const DEFAULT_DATASET = "h3";

export const FIELD_LABELS = [
  ["severity", "Severity"],
  ["riverId", "River ID"],
  ["district", "Near"],
  ["returnPeriodYr", "Return period"],
  ["peakDischargeCms", "Mean discharge"], //this will need to be changed into peak, the info from geoglows is in mean discharge.
  ["issuedTime", "Issued"],
  ["startTime", "Start"],
  ["peakTime", "Peak"],
  ["endTime", "End"],
  ["historicalComparison", "Historical"],
];
// The riverId row is labelled per model: GEOGLOWS forecasts a reach ("River
// ID"), Flood Hub a gauge, GloFAS a fixed reporting point on its own grid —
// which is neither a reach nor a gauge. Falls back to the label above.
export const RIVER_ID_LABEL = {
  geoglows: "River ID", flood_hub: "Gauge", glofas: "Reporting point",
};

// Every pipeline stamps the FeatureCollection with a `kind`, so all the
// user-facing wording (readout, attribution, panel copy) comes from one place
// instead of being hardcoded to H3.
export const DATASETS = {
  "basins-telescoping": {
    unit: "Basin",
    resLabel: "Basin level",
    attribution: "HydroBASINS",
    emptyTitle: "No basin selected",
    emptyBody: "Click a highlighted basin on the map to see every forecast inside it.",
  },
  "h3-telescoping": {
    unit: "Cell",
    resLabel: "H3 res",
    attribution: "Grid: H3 (Uber H3)",
    emptyTitle: "No cell selected",
    emptyBody: "Click a highlighted grid cell on the map to see every forecast inside it.",
  },
};

// ---- Dataset switcher + basin-context state (logic further down) -----------
// Data URLs come from the source hub in sources.js (see DATA import above).
export const DATASETS_MENU = [
  {key: "h3", label: "H3 cells", url: DATA.h3},
  {key: "basins", label: "Basins", url: DATA.basins},
];
// MapLibre zoom sits ~1 below Leaflet, so the "show all tributaries" zoom is a
// step lower than the local build's 10.
export const STREAM_ALL_ZOOM = 9;
export const FLASH_SRC = "flash-src";
// Fill is split by type so highly_likely can stack above likely. The fills
// double as hover/click hit targets; FLASH_LAYERS is bottom->top within the group.
export const FLASH_FILL_LAYERS = ["flash-fill-likely", "flash-fill-high"];
export const FLASH_LAYERS = ["flash-fill-likely", "flash-line-likely", "flash-fill-high", "flash-line-high"];

// Flash-flood models listed in the panel's "Flash Floods" dropdown. Only Flood
// Hub exists today; adding a model here (with its own layer/toggle wiring)
// extends the list. `legend` describes the polygon types the model draws, and
// matches the on-map styling (fill + outline, dashed for the lower tier).
export const FLASH_MODELS = [
  {key: "flood_hub", label: "Flood Hub", tiers: ["likely", "highly_likely"]},
];
// The two flash tiers, lightest first. Each takes one end of the flash ramp, so
// recolouring the overlay from the Display menu moves the map, the legend and
// the hover tooltip together. `sev` names the ramp stop, not a river severity.
export const FLASH_TIERS = ["likely", "highly_likely"];
// `fillMul` and `selMul` keep the two tiers in their established proportions off
// the single opacity slider: at the default 0.5 they reproduce exactly the fixed
// opacities the overlay shipped with (0.32/0.55 and 0.50/0.72).
export const FLASH_TIER = {
  likely: {label: "Likely", sev: "warning", dashed: true,
    fillMul: 0.64, selMul: 1.72, lineWidth: 1.1, selWidth: 3},
  highly_likely: {label: "Highly likely", sev: "extreme", dashed: false,
    fillMul: 1, selMul: 1.44, lineWidth: 1.8, selWidth: 3.5},
};
// tier -> its fill and line layer ids
export const FLASH_TIER_LAYERS = {
  likely: ["flash-fill-likely", "flash-line-likely"],
  highly_likely: ["flash-fill-high", "flash-line-high"],
};

export const MODEL_HOME = {
  geoglows: "https://hydroviewer.geoglows.org/",
  flood_hub: "https://sites.research.google/floods/",
  glofas: "https://global-flood.emergency.copernicus.eu/glofas-forecasting/",
};
// Order matters twice over: it fixes the order of the panel's model tiles, and
// it fixes which vertical slice a model takes when several share one cell, so a
// model always sits in the same place rather than moving with the data.
export const PANEL_MODELS = ["geoglows", "flood_hub", "glofas"];
export const MODEL_LABELS = {
  geoglows: "GEOGLOWS", flood_hub: "Flood Hub", glofas: "GloFAS",
};

// Deep link a single forecast back into its source application, centered on the
// reach/gauge coordinate the pipeline now carries. Returns null when there's no
// usable coordinate (older data, or a model we don't have a URL scheme for), so
// the caller can fall back to a plain, unlinked title. URL patterns live here in
// one place — if a provider changes routing, this is the only thing to update.
// GloFAS is absent on purpose: its public viewer has no documented URL scheme
// for centring on a coordinate, so a GloFAS tile links to the viewer's home page
// (MODEL_HOME) rather than to a link that might quietly stop resolving.
export const LINK_ZOOM = {geoglows: 13, flood_hub: 10};

// ---- Cell/basin layer -----------------------------------------------------

export const SRC = "cells";
export const FILL = "cells-fill";
export const LINE = "cells-line";
export const HASH = "cells-hash";   // hash-pattern overlay marking multi-model basins
export const EMPTY_FC = {type: "FeatureCollection", features: []};

export const stateOn = (key) => ["boolean", ["feature-state", key], false];

// Desired stacking, top -> bottom: rivers, districts, flash (highly_likely over
// likely), basins. Re-applied whenever any of these layers (re)appears, since
// they're added at different times (dataset build, flash/context toggles).
// The global stream network. A single line, no casing — matching River Forecast
// System v3, which draws its rivers as one flat blue over a light base map.
export const STREAMS_SRC = "streams-src";
export const STREAMS_LAYERS = ["streams-line"];
// River Forecast System v3's own river styling, taken from its streams.js rather
// than approximated: the blue is its STANDARD_COLOR, and the width ramp is its
// WIDTH_BASE/WIDTH_MAX across ORDER_DOMAIN, scaled by its zoom stops. Keying width
// on Strahler order means an order-6 reach is drawn at the same weight in both
// apps, whatever else differs between them.
export const STREAM_COLOR = "#3182bd";
// Shown in the map's attribution while the layer is on.
export const STREAM_ATTRIBUTION =
  'Streams: <a href="https://geoglows.org">TDX-Hydro / GEOGLOWS</a>';
export const STREAM_WIDTH_BASE = 4;     // the thinnest reach in the ramp
export const STREAM_WIDTH_MAX = 10;     // the thickest
export const STREAM_ORDER_DOMAIN = [2, 10];
// Multiplier on that ramp by zoom: global, regional, local, then wider still so a
// reach stays easy to click.
export const STREAM_ZOOM_SCALE = [[3, 0.25], [7, 0.5], [12, 1], [16, 2.2]];
// The network's own fade at low zoom, so it sits back behind the flagged cells.
export const STREAM_OPACITY = [[3, 0.65], [9, 0.95]];

// Administrative boundaries: one source, a hit-target fill under a thin outline.
export const BOUNDARIES_SRC = "boundaries-src";
export const BOUNDARY_FILL = "boundaries-fill";
export const BOUNDARY_LINE = "boundaries-line";
export const BOUNDARY_COLOR = "#475569";
// geoBoundaries is CC BY 4.0, so this credit is a licence condition, not a courtesy.
export const BOUNDARY_ATTRIBUTION =
  'Boundaries: <a href="https://www.geoboundaries.org">geoBoundaries CGAZ</a> (CC BY 4.0)';

// Top to bottom. The global backdrops sit at the bottom of the group — boundaries
// and the river network are reference, so the flagged cells and every per-basin
// overlay draw over them.
export const LAYER_ORDER = [
  "ctx-streams-line", "ctx-streams-casing",
  "ctx-districts-line", "ctx-districts-casing", "ctx-districts-fill",
  "flash-line-high", "flash-fill-high", "flash-line-likely", "flash-fill-likely",
  LINE, HASH, FILL,
  BOUNDARY_LINE, BOUNDARY_FILL,
  ...STREAMS_LAYERS,
];

// ---- Basin context: streams + districts (MapLibre sources/layers) ---------

export const ctxSrc = (which) => "ctx-" + which + "-src";
export const ctxLayers = (which) => which === "streams"
  ? ["ctx-streams-casing", "ctx-streams-line"]
  : ["ctx-districts-fill", "ctx-districts-casing", "ctx-districts-line"];
