// Live view state: what the Display menu and the panel filters have selected, plus
// the colors derived from it. `view` holds the values that get reassigned (an ESM
// import binding cannot be assigned by the importing module); the Sets and the ramp
// map are mutated in place, so they are exported directly.
import {DEFAULT_COLOR, DEFAULT_DATASET, FLASH_TIER, FLASH_TIERS, PANEL_MODELS, RAMPS, SEVERITY, SEV_KEYS} from "./config.js";
import {darken} from "./format.js";

// ---- Display settings (all driven by the Display menu) --------------------
// Values that get reassigned live on one object, because an ESM import binding is
// read-only for the importing module: `view.heatOn = false` works from anywhere,
// a bare `heatOn = false` would not. The Sets and the ramp map below are only
// ever mutated in place, so they are exported directly.
// Every display setting as the app opens, written once and used twice: to seed
// `view` below, and by restoreDisplayDefaults() at the bottom of this file. Stating
// them in one place is the point — a default that lives only in the initialiser
// drifts away from the "Restore defaults" button the first time either is edited.
const DISPLAY_DEFAULTS = {
  flashRampId: "reds",
  flashOpacity: 0.5,                // "highly likely" fill; "likely" scales off it
  flashOutlineOnly: false,
  outlineOnly: false,               // draw outlines, skip fills
  fillOpacity: 0.3,                 // resting fill opacity
  // The label that follows the cursor over a flagged cell, naming the models and
  // the severity. On by default — it is the only thing that answers "what is this"
  // without a click. Off, the cell still lights up under the pointer and still
  // selects; it just stops talking, which is what you want while reading the map
  // as a picture rather than interrogating it.
  cellTooltip: true,
  // The severity heat map, and the view the app opens on. It answers the first
  // question a reader has — where is this bad — without asking them to tell three
  // models apart first. The per-model coloring is one checkbox away.
  heatOn: true,
  heatOpacity: 0.6,
  heatRadius: 1,                    // multiplier on the zoom-scaled blur radius
  // Draw the cells two or more models agree on as their own surface, in their own
  // ramp. Off by default: the app opens on one question — where is this bad — and a
  // second hue answering a second one is a choice the viewer should make rather
  // than arrive at. Off, those cells fall back into the severity surface and are
  // told apart only by the panel.
  concurHeat: false,
  concurRampId: "greens",
};
// What the FILTERS menu restores, kept apart from the block above because the two
// menus own different questions and each has its own reset. A setting listed here
// decides which forecasts are on the map; one listed above decides how they look.
const FILTER_DEFAULTS = {
  // Show only the cells two or more models agree on. A filter, not a highlight:
  // coloring concurrence separately marks agreement where it sits among
  // everything else, and what this answers is the different question of what the
  // map looks like with the uncorroborated forecasts taken away.
  //
  // ON at boot, for the cells and the heat map alike. That is a strong default —
  // it hides roughly 96% of the flagged cells — so the panel says so plainly
  // whenever it is in force, or an almost-empty map reads as no data rather than
  // as a filter.
  concurrenceOnly: true,
};
// Outline weight per severity, in pixels. Nested rather than flat so the three
// read as one control, and copied rather than shared on every use — Object.assign
// is shallow, so handing `view` the same object the defaults hold would let a
// slider edit the thing "Restore defaults" restores from.
const LINE_WIDTH_DEFAULTS = {warning: 1, danger: 1, extreme: 1};
// The flash side of the same control, seeded from each tier's own starting weight
// rather than restated here — two places holding the same number is two places to
// edit. Copied, never shared, for the reason above.
const FLASH_LINE_WIDTH_DEFAULTS = Object.fromEntries(
  FLASH_TIERS.map((t) => [t, FLASH_TIER[t].lineWidth]));
// Amber is the app's historic palette and every model opens on it. Deriving the
// map from PANEL_MODELS means a model added there needs no second edit here.
// Every model opens on this one. Models are distinguished by the split bands and
// the panel rather than by color.
// Matches the heat map's own ramp, so switching views changes what is drawn rather
// than what color it is. Keep in step with MODEL_PALETTE in backend/common/config.py,
// which overrides this the moment a release loads — this is only what shows in the
// moment before that.
const DEFAULT_RAMP = "oranges";

export const view = {
  // Wording for the loaded dataset (cell vs basin), replaced once its `kind` is
  // known. Until the data lands we don't know which build it is; stay neutral.
  dataset: {
    unit: "Area", resLabel: "Level", attribution: "",
    emptyTitle: "No area selected",
    emptyBody: "Click a highlighted area on the map to see every forecast inside it.",
  },
  currentDatasetKey: DEFAULT_DATASET,
  selectedBasinId: null,
  // What the pointer does: "select" is everything the map has always done, "pan"
  // is the same map with hover and click switched off so dragging around cannot
  // pick something up by accident. Deliberately NOT in DISPLAY_DEFAULTS — it is a
  // tool, not a drawing setting, and "Restore defaults" has no business reaching
  // out of the Display menu to change which tool is in the user's hand.
  pointerMode: "select",
  ...DISPLAY_DEFAULTS,
  ...FILTER_DEFAULTS,
  lineWidth: {...LINE_WIDTH_DEFAULTS},
  flashLineWidth: {...FLASH_LINE_WIDTH_DEFAULTS},
};

export const modelRamp = Object.fromEntries(PANEL_MODELS.map((m) => [m, DEFAULT_RAMP]));

// ---- palettes --------------------------------------------------------------
// The backend owns the palettes; these are only what the app shows before a
// release has loaded. `setPalettes` swaps in the real list, in place, because
// the Display menu and the panel hold a reference to this array.
export const palettes = RAMPS.map((r) => ({...r}));

export function setPalettes(list) {
  if (!list || !list.length) return;
  palettes.length = 0;
  for (const p of list) palettes.push(p);
}

/** A severity's color in one palette. The single lookup for the whole app. */
export function paletteColor(id, sev) {
  const k = (sev || "").toLowerCase();
  const p = palettes.find((x) => x.id === id) || palettes[0];
  return (p && p[k]) || DEFAULT_COLOR;
}

/** Default each model to the palette the release says it uses. */
export function setModelPalettes(models) {
  for (const m of models || []) {
    if (m && m.model && m.palette) modelRamp[m.model] = m.palette;
  }
}
export const visibleSeverities = new Set(SEV_KEYS);          // severity filter

// The ramp a model is drawn in. One lookup for everything that paints a cell — the
// fill, the outline, the split bands, its heat surface, the panel's badges and keys
// — so a model looks the same in both views and in the panel beside them.
export const rampFor = (model) => modelRamp[model] || DEFAULT_RAMP;

// A severity's color in whichever ramp that model is currently drawn in.
export function sevColor(s, model) {
  const k = (s || "").toLowerCase();
  if (!SEVERITY[k]) return DEFAULT_COLOR;
  return paletteColor(rampFor(model), k);
}
export const flashFill = (t) => paletteColor(view.flashRampId, (FLASH_TIER[t] || {}).sev);
export const flashOutline = (t) => darken(flashFill(t), 0.7);

export function unitLabel(props) {
  // Fall back to the per-feature tag if a file predates the `kind` member.
  if (props && props.basin_id && view.dataset.unit === "Area") return "Basin";
  return view.dataset.unit;
}

// ---- Filters ---------------------------------------------------------------
// Everything that decides WHICH forecasts reach the map, as opposed to how they
// are drawn. All of it is owned by one control, src/controls/filters.js; the sets
// live here because the layers and the panel read them on every rebuild.
//
// River Floods = forecast models; Flash Floods = flash-flood models. A river model
// switched off drops out of the cells, the heat surface and the panel alike; a
// flash model switched off also takes its polygons off the map.
export const visibleModels = new Set(PANEL_MODELS);
// Flash floods start switched OFF. They cover far more ground than the river
// models — 183 areas against a few thousand reaches — so leaving them on by
// default buries the river signal the app is mainly about. The user turns them on.
export const visibleFlashModels = new Set();

// Which flash tiers are drawn — the flash-side counterpart of visibleSeverities.
export const visibleTiers = new Set(FLASH_TIERS);

// Put every Display Options setting back to the value the app started with.
//
// Scope is deliberately the Display menu and nothing else: the loaded dataset, the
// selected cell and every filter are left alone. Those are choices about WHAT is on
// the map rather than how it is drawn, and sweeping them away would discard work
// this button never offered to touch. The Filters menu has its own reset, below,
// for exactly the same reason in reverse.
//
// Everything here is mutated in place, never reassigned — `view`, `modelRamp` and
// the Sets are all held by reference across the app, so replacing an object would
// leave other modules pointing at the old one.
export function restoreDisplayDefaults() {
  Object.assign(view, DISPLAY_DEFAULTS);
  view.lineWidth = {...LINE_WIDTH_DEFAULTS};
  view.flashLineWidth = {...FLASH_LINE_WIDTH_DEFAULTS};
  for (const m of PANEL_MODELS) modelRamp[m] = DEFAULT_RAMP;
}

// The Filters counterpart: every "which forecasts" choice back to boot state.
//
// Flash models are the one asymmetry. Putting that set back to empty would mean
// pulling polygons off the map, which this module cannot do without importing a
// layer — so the control calls this and then settles the flash overlay itself,
// comparing the set before and after. Mutated in place, as above.
export function restoreFilterDefaults() {
  Object.assign(view, FILTER_DEFAULTS);
  visibleModels.clear();
  for (const m of PANEL_MODELS) visibleModels.add(m);
  visibleSeverities.clear();
  for (const k of SEV_KEYS) visibleSeverities.add(k);
  visibleFlashModels.clear();
  visibleTiers.clear();
  for (const t of FLASH_TIERS) visibleTiers.add(t);
}
