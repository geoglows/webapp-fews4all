// Live view state: what the Display menu and the panel filters have selected, plus
// the colours derived from it. `view` holds the values that get reassigned (an ESM
// import binding cannot be assigned by the importing module); the Sets and the ramp
// map are mutated in place, so they are exported directly.
import {DEFAULT_COLOR, DEFAULT_DATASET, FLASH_MODELS, FLASH_TIER, FLASH_TIERS, PANEL_MODELS, RAMPS, SEVERITY, SEV_KEYS} from "./config.js";
import {darken} from "./format.js";

// ---- Display settings (all driven by the Display menu) --------------------
// Values that get reassigned live on one object, because an ESM import binding is
// read-only for the importing module: `view.hatchOn = false` works from anywhere,
// a bare `hatchOn = false` would not. The Sets and the ramp map below are only
// ever mutated in place, so they are exported directly.
// Every display setting as the app opens, written once and used twice: to seed
// `view` below, and by restoreDisplayDefaults() at the bottom of this file. Stating
// them in one place is the point — a default that lives only in the initialiser
// drifts away from the "Restore defaults" button the first time either is edited.
const DISPLAY_DEFAULTS = {
  flashRampId: "inferno",
  flashOpacity: 0.5,                // "highly likely" fill; "likely" scales off it
  flashOutlineOnly: false,
  hatchOn: false,                   // the concurrence hash; off until asked for
  outlineOnly: false,               // draw outlines, skip fills
  fillOpacity: 0.3,                 // resting fill opacity
};
// Amber is the app's historic palette and every model opens on it. Deriving the
// map from PANEL_MODELS means a model added there needs no second edit here.
// Every model opens on this one. Models are distinguished by the split bands and
// the panel rather than by colour.
const DEFAULT_RAMP = "inferno";

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
  ...DISPLAY_DEFAULTS,
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

/** A severity's colour in one palette. The single lookup for the whole app. */
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

// A severity's colour in one model's ramp (the panel's badges and accents).
export function sevColor(s, model) {
  const k = (s || "").toLowerCase();
  if (!SEVERITY[k]) return DEFAULT_COLOR;
  return paletteColor(modelRamp[model] || DEFAULT_RAMP, k);
}
export const flashFill = (t) => paletteColor(view.flashRampId, (FLASH_TIER[t] || {}).sev);
export const flashOutline = (t) => darken(flashFill(t), 0.7);

export function unitLabel(props) {
  // Fall back to the per-feature tag if a file predates the `kind` member.
  if (props && props.basin_id && view.dataset.unit === "Area") return "Basin";
  return view.dataset.unit;
}

// ---- Model filters (one per panel section) --------------------------------
// River Floods = forecast models; Flash Floods = flash-flood models. Both
// default to all-on; each section's dropdown toggles its tiles (and, for flash,
// the map polygons).
export const visibleModels = new Set(PANEL_MODELS);
// Flash floods start switched OFF. They cover far more ground than the river
// models — 183 areas against a few thousand reaches — so leaving them on by
// default buries the river signal the app is mainly about. The user turns them on.
export const visibleFlashModels = new Set();

// Which flash tiers are drawn — the flash-side counterpart of visibleSeverities.
export const visibleTiers = new Set(FLASH_TIERS);

// Put every Display Options setting back to the value the app started with.
//
// Scope is deliberately the Display menu and nothing else. The loaded dataset,
// the selected cell and the side-panel model filters are left alone: they are
// choices about WHAT is on the map rather than how it is drawn, and sweeping them
// away would discard work this button never offered to touch.
//
// Everything here is mutated in place, never reassigned — `view`, `modelRamp` and
// the Sets are all held by reference across the app, so replacing an object would
// leave other modules pointing at the old one.
export function restoreDisplayDefaults() {
  Object.assign(view, DISPLAY_DEFAULTS);
  for (const m of PANEL_MODELS) modelRamp[m] = DEFAULT_RAMP;
  visibleSeverities.clear();
  for (const k of SEV_KEYS) visibleSeverities.add(k);
  visibleTiers.clear();
  for (const t of FLASH_TIERS) visibleTiers.add(t);
}
