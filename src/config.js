// Constants: severity ordering, color ramps, model and dataset wording, and every
// MapLibre source/layer id. Pure data — no state, no map, no DOM.

// Severity is the ordering (and the wording); the color comes from whichever
// ramp the model is currently assigned in the Display menu.
export const SEVERITY = {
  warning: {rank: 1, label: "Warning"},
  danger: {rank: 2, label: "Danger"},
  extreme: {rank: 3, label: "Extreme"},
};
export const SEV_KEYS = Object.keys(SEVERITY);
export const DEFAULT_COLOR = "#4da3ff";

// ---- Color ramps ---------------------------------------------------------
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
// here flashes a wrong color for the moment before the release arrives.
//
// All ten run the same way: darker is worse. The matplotlib maps are sampled
// downwards (0.75 / 0.45 / 0.15) to get there, since they brighten as their value
// rises; the ColorBrewer ones are sampled from the nine-class sequences at 2 / 4 / 6
// and already do. Those used to be the published 3-class schemes, whose lightest
// swatch measured 2.1 to 4.0 dE from the bare basemap at 30% fill — at or under the
// threshold where a color is visible at all. See backend/common/config.py.
export const RAMPS = [
  {id: "viridis", label: "Viridis", warning: "#5ec962", danger: "#25848e", extreme: "#463480"},
  {id: "plasma", label: "Plasma", warning: "#f89540", danger: "#bf3984", extreme: "#5601a4"},
  {id: "inferno", label: "Inferno", warning: "#f98e09", danger: "#a82e5f", extreme: "#2b0b57"},
  {id: "magma", label: "Magma", warning: "#fc8961", danger: "#a1307e", extreme: "#251255"},
  {id: "cividis", label: "Cividis", warning: "#bcae6c", danger: "#727274", extreme: "#243c6e"},
  {id: "blues", label: "Blues", warning: "#6baed6", danger: "#2171b5", extreme: "#08306b"},
  {id: "greens", label: "Greens", warning: "#a1d99b", danger: "#41ab5d", extreme: "#006d2c"},
  {id: "oranges", label: "Oranges", warning: "#fdae6b", danger: "#f16913", extreme: "#a63603"},
  {id: "purples", label: "Purples", warning: "#9e9ac8", danger: "#6a51a3", extreme: "#3f007d"},
  {id: "reds", label: "Reds", warning: "#fc9272", danger: "#ef3b2c", extreme: "#a50f15"},
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
  // "Flow", not "peak" and not "mean". The three models do not put the same
  // quantity here — GEOGLOWS delivers a mean, Flood Hub a published peak, GloFAS
  // nothing at all — and they route and predict differently enough that naming it
  // either way asserts something about the number that is only true for one of
  // them. The honest label is the thing they agree it is: a discharge.
  ["flow_cms", "Flow"],
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

// Flood Hub reports gauges, so a warning lands as one hexagon with its own river
// blank. The pipeline spreads it onto the HydroRIVERS reach the gauge stands on; a
// filled cell carries that gauge in place of an id of its own, as `from <gaugeId>`
// (backend/common/hydrorivers.py, FLOOD_HUB_FILL_PREFIX). The two spellings have
// to match — this is the only place the front end knows it.
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

export const FLASH_SRC = "flash-src";
// Fill is split by type so highly_likely can stack above likely. The fills
// double as hover/click hit targets; FLASH_LAYERS is bottom->top within the group.
export const FLASH_FILL_LAYERS = ["flash-fill-likely", "flash-fill-high"];
export const FLASH_LAYERS = ["flash-fill-likely", "flash-line-likely", "flash-fill-high", "flash-line-high"];

// Flash-flood models listed in the panel's "Flash Floods" dropdown. Only Flood
// Hub exists today; adding a model here (with its own layer/toggle wiring)
// extends the list. `legend` describes the polygon types the model draws, and
// matches the on-map styling (fill + outline).
export const FLASH_MODELS = [
  {key: "flood_hub", label: "Flood Hub", tiers: ["likely", "highly_likely"]},
];
// The two flash tiers, lightest first. Each takes one end of the flash ramp, so
// recoloring the overlay from the Display menu moves the map, the legend and
// the hover tooltip together. `sev` names the ramp stop, not a river severity.
export const FLASH_TIERS = ["likely", "highly_likely"];
// `fillMul` keeps the two tiers in their established proportions off the single
// opacity slider. How a HOVERED or SELECTED polygon differs from a resting one is
// not here — that is EMPHASIS below, shared with the cells, because a map where two
// layers answer the pointer differently reads as two maps.
// `lineWidth` is the STARTING outline weight only — the viewer owns it from there
// (Display Options -> Flash Floods), the same way the river severities work. Both
// tiers draw a solid line now: the lower one used to be dashed, which at the sizes
// these polygons are drawn read as texture rather than as a distinction, and the
// two are already told apart by fill and color.
export const FLASH_TIER = {
  likely: {label: "Likely", sev: "danger",
    fillMul: 0.64, lineWidth: 1.1, lineOpacity: 0.9},
  highly_likely: {label: "Highly likely", sev: "extreme",
    fillMul: 1, lineWidth: 1.8, lineOpacity: 0.95},
};

// What hover and selection do to a shape, for every layer that answers the pointer.
//
// The flagged cells and the flash polygons used to disagree: a cell lifted its fill
// on hover and dropped it when picked, a polygon had no hover at all and simply got
// darker when picked. Two layers under one cursor behaving differently reads as two
// maps, so both now take their emphasis from here.
//
// Selection LIGHTENS the fill — a plain multiplier, which cannot saturate — so the
// white outline and whatever is underneath both read. Hover works the other way,
// and the rule it follows is the one thing here worth reading twice.
//
// Hover used to be a multiplier too (x2), and that broke on the one shape it most
// needed to work on. A "highly likely" flash polygon rests at the full opacity
// slider value, 0.5, so hovering took it to 1.0: opaque, and opaque over the heat
// map means the pointer erases what you moved it there to look at. Doubling is
// fine at 0.3 and meaningless at 0.5.
//
// So hover moves the fill a fixed fraction of the way to a TRANSLUCENCY CEILING
// rather than scaling it. Nothing can reach opaque, the jump stays proportionate
// at any slider setting, and the shapes that were already working barely move:
// a cell at 0.3 goes to 0.575 where it used to go to 0.6, while a highly-likely
// polygon at 0.5 now goes to 0.665 instead of disappearing into itself. Above the
// ceiling the same line LIGHTENS on hover, which is the only signal left once a
// shape is already opaque, and is what you want anyway — it reveals the surface
// underneath instead of sealing it.
//
// The widths are in px, and are floors rather than fixed values: a resting outline
// set heavier than these would otherwise make a picked shape look thinner than an
// unpicked one.
export const EMPHASIS = {
  hoverLift: 0.55,        // fraction of the gap to the ceiling that hover closes
  fillCeiling: 0.8,       // hover never lands past this, so every fill stays see-through
  selectedFill: 0.7,
  hoverLine: 3,
  selectedLine: 4,
  lineOpacity: 1,
};

/** A resting fill opacity as it should be drawn under the cursor. */
export const hoverFill = (base) =>
  Math.min(1, base + (EMPHASIS.fillCeiling - base) * EMPHASIS.hoverLift);

/** ...and as it should be drawn once picked. */
export const selectedFill = (base) => Math.min(1, base * EMPHASIS.selectedFill);
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
// The density control, in Strahler order. The network is drawn from tiles that
// already carry their own level of detail, so zooming in correctly reveals more of
// it — and close in, over a basin, "all of it" is a thicket. The honest knob is a
// single `>=` on the order field, which is what this is: one number, dropping the
// smallest remaining tributaries a rung at a time. It is deliberately NOT a second
// zoom-keyed LOD table racing the one baked into the archive.
//
// The slider counts the other way from the filter so that right means more, which
// is what a slider called Density has to mean. 9 draws everything.
export const STREAM_DENSITY_MAX = 9;
export const streamMinOrder = (density) =>
  STREAM_DENSITY_MAX + 1 - Math.max(1, Math.min(STREAM_DENSITY_MAX, density || STREAM_DENSITY_MAX));
// Multiplier on that ramp by zoom: global, regional, local, then wider still so a
// reach stays easy to click.
export const STREAM_ZOOM_SCALE = [[3, 0.25], [7, 0.5], [12, 1], [16, 2.2]];
// The network's own fade at low zoom, so it sits back behind the flagged cells.
export const STREAM_OPACITY = [[3, 0.65], [9, 0.95]];

// Administrative boundaries were drawn here as their own context layer, from
// backend/static/tiles/adm.pmtiles. Removed: every base map already carries
// national and regional borders, so a second set of outlines over them was two
// lines saying one thing. The geoBoundaries files themselves stay — the backend
// still uses them for each forecast's "Near" district (backend/common/admin.py),
// which is a label, not a layer. The retired layer is in Other/retired/.

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
// ---- Heat map --------------------------------------------------------------
// A continuous surface built from the flagged cells' own centres, weighted by
// severity. It stands in front of the per-model coloring rather than beside it:
// while it is on, the hexagons drop to zero opacity and stay on the map purely as
// hit targets, EXCEPT where models concur — those are drawn as real hexagons and
// left out of the surface, so agreement reads as a hard edge in a blurred field.
// Concurrence gets its own surface, in its own hue. The two are a PARTITION, not
// an overlay: a cell feeds one or the other, never both, so the green is not also
// making the orange underneath it hotter. Same weights, same blur, same radius —
// only the ramp differs, so the hue is carrying the one fact the severity channel
// cannot (how many models are saying it) without touching what the brightness
// means (how bad they say it is).
// Agreement is colored by HOW MANY models share the cell, not just whether any do,
// so there is a surface and a ramp per count rather than one for "2 or more".
// Three is empty in every release measured so far — Flood Hub reaches 182 cells and
// never overlaps GEOGLOWS — but the tier is real, it is the one a reader would most
// want to see, and a color that only appears on the day it matters is worth having
// already chosen. Listed low to high; the cell takes the highest it qualifies for.
export const CONCUR_COUNTS = [2, 3];
export const CONCUR_LABEL = {2: "2 models", 3: "3 models"};
// What one row stands for when the counts showing share a ramp — see the legend
// in panel.js. Only ever used when MORE THAN ONE count is on the map; a single
// surviving count is named by its own number.
export const CONCUR_ANY_LABEL = "2+ models";
// What the counts are called when they are all drawn the same and the legend has
// nothing to tell apart. Naming each count beside an identical bar would be the
// legend claiming a distinction the map is not making.
export const HEAT_CONCUR_SRC = (n) => `heat-concur-${n}-src`;
export const HEAT_CONCUR_LAYER = (n) => `heat-concur-${n}`;

// ...and one per model beside it. The surface used to be a single severity field
// with one ramp, which meant the heat map was the one view where the models could
// not be told apart at all. Each now gets its own layer in its own ramp, and every
// cell lands in exactly ONE of them — at the base resolution a cell with two models
// IS a concurrence cell, so "which model owns this cell" is a question with one
// answer whenever concurrence is not being drawn apart.
export const HEAT_MODEL_SRC = (m) => `heat-src-${m}`;
export const HEAT_MODEL_LAYER = (m) => `heat-${m}`;

// ...and ONE surface carrying all of them, for when they share a ramp.
//
// Not an optimisation. Two heatmap layers in the same colors do not add up to the
// layer you would get from their points combined: each one computes its own
// density, runs it through its own ramp and is then alpha-composited over the
// other. Where two models' blurs overlap the result is darker than either —
// 1-(1-a)^2 at the same opacity — so on the default palette, where all three are
// amber, every model boundary was drawing a seam that meant nothing about the
// data. Merged, the density is summed once, which is the number the ramp is for.
export const HEAT_MERGED_SRC = "heat-merged-src";
export const HEAT_MERGED_LAYER = "heat-merged";

// How steeply the three rungs separate. A rung's weight is (rank / 3) ^ exponent,
// so the curve is one number rather than three hand-set values that drift out of
// proportion with each other.
//
// At 1 (linear: .33 / .67 / 1.00) warnings take 49% of the surface's total weight
// off 69% of the cells, and the map mostly shows where cells are dense rather than
// where it is bad. At 2 (.11 / .44 / 1.00) the three rungs contribute 28 / 34 / 38
// percent, which is the point at which a rung's share stops tracking how many
// cells it has. Raise it to 3 to push the warnings down further.
export const HEAT_SEVERITY_EXPONENT = 2;

export const heatWeight = (sev) => {
  const info = SEVERITY[(sev || "").toLowerCase()];
  if (!info) return 0;
  return (info.rank / SEV_KEYS.length) ** HEAT_SEVERITY_EXPONENT;
};

export const LAYER_GROUPS = {
  contextTop: [
    "ctx-streams-line", "ctx-streams-casing",
    "ctx-districts-line", "ctx-districts-casing", "ctx-districts-fill",
  ],
  flash: ["flash-line-high", "flash-fill-high", "flash-line-likely", "flash-fill-likely"],
  // Concurrence above the models — within a group, earlier draws on top. The model
  // surfaces are disjoint so their order between themselves does not matter; it is
  // fixed to PANEL_MODELS anyway so a restack is never a reshuffle.
  heat: [...CONCUR_COUNTS.map(HEAT_CONCUR_LAYER), HEAT_MERGED_LAYER,
    ...PANEL_MODELS.map(HEAT_MODEL_LAYER)],
  backdrop: [...STREAMS_LAYERS],
};
// Within one resolution: outline on top, then the split bands, and the
// tile fill at the bottom — the order they are created in, stated so it survives
// a re-sort.
export const CELL_LAYER_KINDS = ["cells-line", "cells-split", "cells-fill"];

// ---- Basin context: streams + districts (MapLibre sources/layers) ---------

export const ctxSrc = (which) => "ctx-" + which + "-src";
export const ctxLayers = (which) => which === "streams"
  ? ["ctx-streams-casing", "ctx-streams-line"]
  : ["ctx-districts-fill", "ctx-districts-casing", "ctx-districts-line"];
