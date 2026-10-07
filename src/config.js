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
// overlay takes its two tiers from the MIDDLE and dark ends of its own ramp —
// "likely" on the middle rung rather than the lightest, which on a 3-class
// ColorBrewer scheme is close to invisible over the basemap.
//
// What the app shows before a release has loaded. The backend owns the real list
// (backend/common/config.py PALETTES -> palettes.csv) and settings.setPalettes
// swaps it in; these exist so the Display menu and the panel legends are never
// empty during that first moment. Keep the two lists in step — a ramp missing
// here flashes a wrong colour for the moment before the release arrives.
//
// All ten run the same way: darker is worse. The matplotlib maps are sampled
// downwards (0.75 / 0.45 / 0.15) to get there, since they brighten as their
// value rises; the ColorBrewer 3-class schemes are taken whole and already do.
export const RAMPS = [
  {id: "viridis", label: "Viridis", warning: "#5ec962", danger: "#25848e", extreme: "#463480"},
  {id: "plasma", label: "Plasma", warning: "#f89540", danger: "#bf3984", extreme: "#5601a4"},
  {id: "inferno", label: "Inferno", warning: "#f98e09", danger: "#a82e5f", extreme: "#2b0b57"},
  {id: "magma", label: "Magma", warning: "#fc8961", danger: "#a1307e", extreme: "#251255"},
  {id: "cividis", label: "Cividis", warning: "#bcae6c", danger: "#727274", extreme: "#243c6e"},
  {id: "blues", label: "Blues", warning: "#deebf7", danger: "#9ecae1", extreme: "#3182bd"},
  {id: "greens", label: "Greens", warning: "#e5f5e0", danger: "#a1d99b", extreme: "#31a354"},
  {id: "oranges", label: "Oranges", warning: "#fee6ce", danger: "#fdae6b", extreme: "#e6550d"},
  {id: "purples", label: "Purples", warning: "#efedf5", danger: "#bcbddc", extreme: "#756bb1"},
  {id: "reds", label: "Reds", warning: "#fee0d2", danger: "#fc9272", extreme: "#de2d26"},
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

// Keyed to the columns of the delivered forecasts.csv. The CSV is snake_case and
// is the contract now, so the panel reads those names directly rather than keeping
// a camelCase alias layer that would have to be maintained in step with it.
// "Historical" is gone: no model ever filled it, and the backend no longer emits
// a column for it.
export const FIELD_LABELS = [
  ["severity", "Severity"],
  ["native_id", "River ID"],
  ["district", "Near"],
  ["return_period_yr", "Return period"],
  ["peak_discharge_cms", "Mean discharge"], //this will need to be changed into peak, the info from geoglows is in mean discharge.
  ["issued_time", "Issued"],
  ["start_time", "Start"],
  ["peak_time", "Peak"],
  ["end_time", "End"],
];
// The riverId row is labelled per model: GEOGLOWS forecasts a reach ("River
// ID"), Flood Hub a gauge, GloFAS a fixed reporting point on its own grid —
// which is neither a reach nor a gauge. Falls back to the label above.
export const RIVER_ID_LABEL = {
  geoglows: "River ID", flood_hub: "Gauge", glofas: "Reporting point",
  flash: "Area",
};

// Flood Hub reports gauges, so the river between two warned gauges would be blank.
// The pipeline walks HydroRIVERS between them and fills it with the UPSTREAM
// gauge's forecast; a filled cell carries that gauge in place of an id of its own,
// as `from <gaugeId>` (backend/common/hydrorivers.py, FLOOD_HUB_FILL_PREFIX). The
// two spellings have to match — this is the only place the front end knows it.
export const FILL_PREFIX = "from ";

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
  likely: {label: "Likely", sev: "danger", dashed: true,
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
// A vector source is a bundle of named layers; this is the one tippecanoe wrote.
export const STREAMS_SOURCE_LAYER = "streams";
// RFS v3's tiles spell Strahler order `strahlerOrder`; the retired GeoJSON build
// called the same value `ord`.
export const STREAM_ORDER_FIELD = "strahlerOrder";
// Dev path: Vite serves the project root and honours range requests, so the
// archive resolves where the static tier keeps it. For production, serve
// backend/static/tiles/ from somewhere real and repoint this — it is the only
// place the archive is named.
export const STREAMS_TILES_URL = "pmtiles:///backend/static/tiles/streams.pmtiles";
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

// Administrative boundaries: one tile archive holding all three levels as named
// layers, each a hit-target fill under a thin outline. The zoom each level takes
// over at is the same table backend/common/config.py tiles to, so the bands the
// archive was cut for and the bands it is drawn at cannot drift apart.
export const BOUNDARIES_SRC = "boundaries-src";
export const ADM_TILES_URL = "pmtiles:///backend/static/tiles/adm.pmtiles";
export const ADM_LEVELS = [0, 1, 2];
export const ADM_ZOOM = {0: 0, 1: 4, 2: 7};
export const ADM_LABEL = ["Country", "Region", "District"];
export const ADM_SOURCE_LAYER = (level) => `adm${level}`;
export const ADM_FILL = (level) => `boundaries-fill-${level}`;
export const ADM_LINE = (level) => `boundaries-line-${level}`;
export const BOUNDARY_COLOR = "#475569";
// geoBoundaries is CC BY 4.0, so this credit is a licence condition, not a courtesy.
export const ADM_ATTRIBUTION =
  'Boundaries: <a href="https://www.geoboundaries.org">geoBoundaries CGAZ</a> (CC BY 4.0)';

// Top to bottom, in named groups so the stack can be rearranged at runtime
// rather than being one frozen list. The global backdrops sit at the bottom —
// boundaries and the river network are reference, so the flagged areas draw over
// them — and the two flood groups swap by preference (Display -> Flash floods
// above cells).
//
// The cell layers are NOT listed: there is one set per resolution, created from
// whatever the release declares, so they are collected from the map by name at
// the moment the order is applied. A static list went stale the moment the cells
// moved into vector tiles, and silently stopped ordering them at all.
export const LAYER_GROUPS = {
  contextTop: [
    "ctx-streams-line", "ctx-streams-casing",
    "ctx-districts-line", "ctx-districts-casing", "ctx-districts-fill",
  ],
  flash: ["flash-line-high", "flash-fill-high", "flash-line-likely", "flash-fill-likely"],
  backdrop: [
    ...[...ADM_LEVELS].sort((a, b) => b - a).flatMap((l) => [ADM_LINE(l), ADM_FILL(l)]),
    ...STREAMS_LAYERS,
  ],
};
// Within one resolution: outline on top, then the hatch, the split bands, and the
// tile fill at the bottom — the order they are created in, stated so it survives
// a re-sort.
export const CELL_LAYER_KINDS = ["cells-line", "cells-hash", "cells-split", "cells-fill"];

// ---- Basin context: streams + districts (MapLibre sources/layers) ---------

export const ctxSrc = (which) => "ctx-" + which + "-src";
export const ctxLayers = (which) => which === "streams"
  ? ["ctx-streams-casing", "ctx-streams-line"]
  : ["ctx-districts-fill", "ctx-districts-casing", "ctx-districts-line"];
