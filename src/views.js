// Views: named, one-click combinations of Display settings and Filters.
//
// WHY THIS EXISTS
// ---------------
// Everything a view does was already reachable — pick a ramp per model, turn on
// concurrence coloring, choose an agreement level, uncheck two severities. But
// reaching a USEFUL combination meant knowing which four controls across two
// menus to touch and in what order, and most readers never found out that the
// map could answer "where do all three models agree" at all. A view is not new
// capability; it is the capability named.
//
// WHY A VIEW WRITES FILTERS TOO
// -----------------------------
// Display Options and Filters deliberately own different questions, and neither
// reaches into the other. A view sits ABOVE both and writes to each, because the
// combinations worth naming do not respect that line: "Highest confidence" is a
// filter (keep only 3-model cells) and a color choice (draw them in their own
// ramp) and it is useless as either half. Reaching across is the whole point of
// the control, which is why it is its own menu rather than a block inside one of
// theirs — and why applying one says, in the panel, that filters moved.
//
// EVERY VIEW STARTS FROM DEFAULTS
// -------------------------------
// apply() restores both menus first, then sets only what the view is about. So a
// view means the same thing whatever was on screen before it, and no setting can
// survive from one view into another and quietly change what it says. The cost is
// that a view discards hand-tuning, which is what the "Custom" state is for: the
// moment the viewer touches either menu the active view is cleared, because the
// map is no longer showing what the name on it claims.
import {CONCUR_COUNTS, PANEL_MODELS, rampColor} from "./config.js";
import {
  claimRamp, modelRamp, restoreDisplayDefaults, restoreFilterDefaults, view,
  visibleFlashModels, visibleModels, visibleSeverities,
} from "./settings.js";

// main.js injects the repaint and the flash loader, so this module never imports
// the orchestration that imports it — the same contract display.js and filters.js
// have. `setFlashOn` is separate because switching a flash model on FETCHES its
// polygons; no other setting here loads anything.
let actions = {onViewChange() {}, setFlashOn() {}};
export function init(a) { actions = Object.assign(actions, a); }

// True while apply() is running, so the manual-change hook below ignores the
// repaint a view triggers itself. Without it every view would clear its own name
// the instant it set it.
let applying = false;

/**
 * The viewer touched Display or Filters directly, so no view describes the map.
 *
 * Called from main.js on both menus' change channels rather than from each of
 * their dozens of handlers — one place to keep correct, and it cannot be missed
 * by a control added later.
 */
export function noteManualChange() {
  if (!applying) view.preset = null;
}

// Ramps chosen for distinctness at a glance rather than for beauty: blue, purple
// and red are far apart for the common forms of color blindness, and they run
// light to dark in that order, so "more models agree" also reads as "heavier" on
// a basemap. Named here rather than inline so two views that mean the same thing
// by "3 models agree" cannot drift to different reds.
const ONE = "blues";
const TWO = "purples";
const THREE = "reds";

// The per-model colors "Everything" draws, by name rather than by position, so
// they stay attached to the model whatever order PANEL_MODELS ends up in. SPARE
// catches a model added later, which gets a ramp of its own rather than quietly
// sharing one; it is only a fallback, so a model worth naming belongs above.
const MODEL_RAMP = {geoglows: "oranges", flood_hub: "blues", glofas: "greens"};
const SPARE = ["cividis", "magma", "viridis"];

/** Put every model on one ramp, and mark them so a release cannot stamp it back. */
function allModels(ramp) {
  for (const m of PANEL_MODELS) { modelRamp[m] = ramp; claimRamp(m); }
}

/** The per-count concurrence ramps, from a {count: ramp} map. */
function concur(map) {
  for (const n of CONCUR_COUNTS) if (map[n]) view.concurRamp[n] = map[n];
  view.concurHeat = true;
}

// The views, in the order the menu lists them: the two that show everything the
// release holds, then the two about agreement, then the two narrowing cuts.
//
// No description per view -- the NAME has to carry it, with the swatch strip
// beside it saying how many colors are in play. A list where every row needs a
// sentence is a list that has stopped being quicker than the menus it stands in
// front of.
export const VIEWS = [
  {
    id: "default",
    label: "Default",
    swatches: ["oranges"],
    apply() {},            // the restore both menus already did IS this view
  },
  {
    id: "everything",
    label: "Everything",
    swatches: [MODEL_RAMP.geoglows, MODEL_RAMP.flood_hub, MODEL_RAMP.glofas, TWO],
    apply() {
      PANEL_MODELS.forEach((m, i) => {
        modelRamp[m] = MODEL_RAMP[m] || SPARE[i % SPARE.length];
        claimRamp(m);
      });
      // With the models on different ramps a shared cell has no single right
      // color, so agreement has to take one of its own. Both counts get the same
      // purple here: this view is about telling the MODELS apart, and a second
      // distinction competing with it would make the map harder to read, not
      // easier. "Agreement levels" above is the view that draws that one.
      concur({2: TWO, 3: TWO});
      // Everything means everything: the flash overlay comes on too, which is the
      // one thing here that loads data rather than recoloring what is already up.
      visibleFlashModels.add("flood_hub");
    },
  },
  {
    id: "agreement",
    label: "Agreement levels",
    swatches: [ONE, TWO, THREE],
    apply() {
      allModels(ONE);
      concur({2: TWO, 3: THREE});
    },
  },
  {
    id: "agree-only",
    label: "Agreement only",
    swatches: [TWO, THREE],
    apply() {
      allModels(ONE);
      concur({2: TWO, 3: THREE});
      view.concurMin = 2;
    },
  },
  {
    id: "extreme",
    label: "Extreme only",
    swatches: [THREE],
    apply() {
      allModels(THREE);
      visibleSeverities.clear();
      visibleSeverities.add("extreme");
    },
  },
  {
    id: "flash",
    label: "Flash focus",
    swatches: ["reds"],
    apply() {
      // River models off rather than merely recolored: flash polygons are far
      // larger than the hexagons and read as background noise underneath them.
      visibleModels.clear();
      visibleFlashModels.add("flood_hub");
      view.flashOpacity = 0.55;
    },
  },
];

export const viewById = (id) => VIEWS.find((v) => v.id === id) || null;

/** The colors a view's swatch strip shows — the middle rung of each of its ramps. */
export const swatchColors = (v) => v.swatches.map((r) => rampColor(r, "danger"));

/**
 * Put the map into a view: both menus back to defaults, then the view's own
 * settings, then one repaint.
 *
 * Flash is the single asymmetry, exactly as it is in the Filters menu's own
 * reset: emptying `visibleFlashModels` does not pull polygons off the map, and
 * adding to it does not fetch them. So the set is compared before and after and
 * the layer is told about the difference — once, after everything else is
 * settled, so a view that both clears and re-adds flash does not load twice.
 */
export function applyView(id) {
  const v = viewById(id);
  if (!v) return;
  applying = true;
  try {
    const flashBefore = new Set(visibleFlashModels);
    restoreDisplayDefaults();
    restoreFilterDefaults();
    v.apply();
    view.preset = id;
    for (const key of new Set([...flashBefore, ...visibleFlashModels])) {
      const on = visibleFlashModels.has(key);
      if (on !== flashBefore.has(key)) setFlash(key, on);
    }
  } finally {
    applying = false;
  }
  actions.onViewChange();
}

// Flood Hub is the only flash model with polygons behind it; the rest are listed
// but draw nothing yet. Same routing as the Filters menu, for the same reason.
function setFlash(key, on) {
  if (key === "flood_hub") actions.setFlashOn(on);
}
