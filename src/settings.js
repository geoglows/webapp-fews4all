// Live view state: what the Display menu and the panel filters have selected, plus
// the colours derived from it. `view` holds the values that get reassigned (an ESM
// import binding cannot be assigned by the importing module); the Sets and the ramp
// map are mutated in place, so they are exported directly.
import {DEFAULT_COLOR, DEFAULT_DATASET, FLASH_MODELS, FLASH_TIER, FLASH_TIERS, PANEL_MODELS, RAMPS, SEVERITY, SEV_KEYS, rampColor} from "./config.js";
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
  flashRampId: "purple",
  flashOpacity: 0.5,                // "highly likely" fill; "likely" scales off it
  flashOutlineOnly: false,
  hatchOn: true,                    // hash + outline emphasis
  outlineOnly: false,               // draw outlines, skip fills
  fillOpacity: 0.3,                 // resting fill opacity
};
// Amber is the app's historic palette and every model opens on it. Deriving the
// map from PANEL_MODELS means a model added there needs no second edit here.
const DEFAULT_RAMP = RAMPS[0].id;

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
export const visibleSeverities = new Set(SEV_KEYS);          // severity filter

// A severity's colour in one model's ramp (the panel's badges and accents).
export function sevColor(s, model) {
  const k = (s || "").toLowerCase();
  if (!SEVERITY[k]) return DEFAULT_COLOR;
  return rampColor(modelRamp[model] || RAMPS[0].id, k);
}
export const flashFill = (t) => rampColor(view.flashRampId, (FLASH_TIER[t] || {}).sev);
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
export const visibleFlashModels = new Set(FLASH_MODELS.map((fm) => fm.key));

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
