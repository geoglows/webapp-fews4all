// Filters: everything that decides WHICH forecasts reach the map — models,
// severities, concurrence, flash tiers. Its neighbour, Display Options, decides
// how whatever survives these is drawn.
//
// The split is the point. These four questions used to be answered in three
// different places: the river model filter lived in the side panel, the severity
// and concurrence filters in Display Options, the flash tiers in Display Options'
// second tab. So "which forecasts am I looking at" had no single answer, Display
// Options overflowed its own height into a scrollbar, and the panel carried a
// control that had nothing to do with the forecast it was showing.
import {CONCUR_COUNTS, CONCUR_LABEL, FLASH_MODELS, FLASH_TIER, FLASH_TIERS, PANEL_MODELS, SEVERITY, SEV_KEYS} from "../config.js";
import {dropdownControl} from "../ui/dropdown.js";
import {check, head, note, radio, resetButton, rule} from "../ui/form.js";
import {modelLabel} from "../format.js";
import {applyFlashStyle} from "../layers/flash.js";
import {
  restoreFilterDefaults, view, visibleFlashModels, visibleModels, visibleSeverities, visibleTiers,
} from "../settings.js";

// main.js injects what a filter change should trigger, so this control never
// imports the orchestration that imports it. `setFlashOn` is separate from
// `onFilterChange` because turning a flash model on LOADS its polygons, which the
// river filters never do.
let actions = {onFilterChange() {}, setFlashOn() {}};
export function init(a) { actions = Object.assign(actions, a); }

let rebuild = null;    // set once the panel is mounted; see restate()

/**
 * Rebuild the menu from the current filter state.
 *
 * Needed because the filters are not only set from here: "Restore defaults" in
 * Display Options used to reach across and clear them, and a release landing can
 * change which models exist. Anything that writes to the sets from outside calls
 * this, or the boxes go on claiming the state they had when the menu was built.
 */
export function restate() {
  if (rebuild) rebuild();
}

// The sentinel the "All" row carries in place of a model key.
const ALL = "__all";

// The agreement floor, as the menu offers it. Derived from CONCUR_COUNTS so a
// fourth model added there needs no edit here; the last rung says "all" rather
// than its number, because that is the claim being made.
const CONCUR_LEVELS = [{value: 0, label: "Any \u2014 no agreement needed"}].concat(
  CONCUR_COUNTS.map((n) => ({
    value: n,
    label: n >= PANEL_MODELS.length ? `All ${n} models agree` : `${CONCUR_LABEL[n]} or more agree`,
  })));

// A model list with an "All" row above the rule. "All" carries no model of its
// own: it reads the others and writes to all of them, and goes indeterminate on a
// mixed set rather than lying about a partial selection.
const modelList = (models, set) =>
  check(`flt-${ALL}`, "All", models.every((m) => set.has(m))) +
  `<div style="height:1px;background:#e2e8f0;margin:5px 0 6px"></div>` +
  models.map((m) => check(`flt-model-${m}`, modelLabel(m), set.has(m))).join("");

function body() {
  // Models first: it is the coarsest cut, and the one most often reached for.
  const modelBlock =
    head("River models") +
    modelList(PANEL_MODELS, visibleModels) +
    note("Switching one off takes it out of the cells, the heat surface and the " +
      "side panel at once.", 7);

  // Concurrence sits under the three rungs but is not a fourth one: the severities
  // are an OR — show me warnings and extremes — and this is an AND on top of
  // whichever of them are ticked. The divider above it says so without a sentence.
  const severityBlock =
    head("Severity") +
    SEV_KEYS.map((k) => check(`flt-sev-${k}`, SEVERITY[k].label, visibleSeverities.has(k))).join("") +
    `<div style="height:1px;background:#e2e8f0;margin:7px 0 6px"></div>` +
    // Three mutually exclusive levels rather than one checkbox: "2 or more agree"
    // and "all agree" are different claims, and only the first could be asked for
    // before. Radios, not checkboxes, because the levels nest -- ticking both
    // would mean the stricter one and say the looser one too.
    head("Agreement") +
    CONCUR_LEVELS.map(({value, label}) =>
      radio("flt-concur", `flt-concur-${value}`, label, view.concurMin === value)).join("") +
    note("Agreement is counted across the models ticked above.", 3);

  // The flash overlay's own two cuts, model and tier. Unlike the river models,
  // ticking one of these fetches and draws polygons rather than filtering a set
  // already on the map — which is why it opens off.
  const flashBlock =
    head("Flash floods") +
    FLASH_MODELS.map((fm) =>
      check(`flt-flash-${fm.key}`, fm.label || modelLabel(fm.key), visibleFlashModels.has(fm.key))).join("") +
    `<div style="height:1px;background:#e2e8f0;margin:7px 0 6px"></div>` +
    FLASH_TIERS.map((t) => check(`flt-tier-${t}`, FLASH_TIER[t].label, visibleTiers.has(t))).join("") +
    // Ticking a tier with the overlay off changes nothing on screen, which reads as
    // a broken checkbox rather than as a setting waiting for its layer.
    (visibleFlashModels.size ? "" :
      note("Tiers take effect once the overlay above is on.", 6));

  return [modelBlock, severityBlock, flashBlock].join(rule) +
    rule + resetButton("flt-reset", "Reset filters");
}

export function filtersControl() {
  return dropdownControl({
    iconName: "funnel",
    title: "Filters",
    // Measured at 523px, which fits unaided. The cap is only a floor for a short
    // window — if it ever starts biting on a normal screen, that is the signal
    // something which is not a filter has crept in here.
    panelStyle: "padding:10px 12px;width:248px;max-height:calc(100vh - 150px);" +
      "overflow-y:auto;font:600 12px system-ui,sans-serif;color:#0f172a",
    render(panel) {
      function renderBody() {
        panel.innerHTML = body();
        bind();
      }
      rebuild = renderBody;

      function bind() {
        const $ = (sel) => panel.querySelector(sel);

        // --- river models ---
        const allBox = $(`#flt-${ALL}`);
        const boxes = PANEL_MODELS.map((m) => [m, $(`#flt-model-${m}`)]).filter(([, el]) => el);
        const syncAll = () => {
          if (!allBox) return;
          const n = PANEL_MODELS.filter((m) => visibleModels.has(m)).length;
          allBox.checked = n === PANEL_MODELS.length;
          allBox.indeterminate = n > 0 && n < PANEL_MODELS.length;
        };
        syncAll();

        if (allBox) allBox.addEventListener("change", () => {
          const on = allBox.checked;
          for (const m of PANEL_MODELS) { if (on) visibleModels.add(m); else visibleModels.delete(m); }
          for (const [, el] of boxes) el.checked = on;
          allBox.indeterminate = false;
          actions.onFilterChange();
        });

        boxes.forEach(([m, el]) => el.addEventListener("change", () => {
          if (el.checked) visibleModels.add(m); else visibleModels.delete(m);
          syncAll();
          actions.onFilterChange();
        }));

        // --- severity and concurrence ---
        SEV_KEYS.forEach((k) => {
          const cb = $(`#flt-sev-${k}`);
          if (!cb) return;
          cb.addEventListener("change", () => {
            if (cb.checked) visibleSeverities.add(k); else visibleSeverities.delete(k);
            actions.onFilterChange();
          });
        });

        CONCUR_LEVELS.forEach(({value}) => {
          const rb = $(`#flt-concur-${value}`);
          if (!rb) return;
          rb.addEventListener("change", () => {
            if (!rb.checked) return;
            view.concurMin = value;
            // Changes which cells qualify, not just how they look, so this goes
            // through the full path: the feature-state join, the split bands and
            // the heat surfaces are all rebuilt from it. The banner follows.
            actions.onFilterChange();
          });
        });

        // --- flash ---
        FLASH_MODELS.forEach((fm) => {
          const cb = $(`#flt-flash-${fm.key}`);
          if (!cb) return;
          cb.addEventListener("change", () => {
            if (cb.checked) visibleFlashModels.add(fm.key); else visibleFlashModels.delete(fm.key);
            setFlash(fm.key, cb.checked);
            actions.onFilterChange();     // the panel's flash tile follows
          });
        });

        FLASH_TIERS.forEach((t) => {
          const cb = $(`#flt-tier-${t}`);
          if (!cb) return;
          cb.addEventListener("change", () => {
            if (cb.checked) visibleTiers.add(t); else visibleTiers.delete(t);
            applyFlashStyle();            // paint only; the polygons are already loaded
            actions.onFilterChange();     // the flash legend follows the tiers
          });
        });

        $("#flt-reset").addEventListener("click", () => {
          // Snapshot first: restoreFilterDefaults empties the flash set, and the
          // overlay has to be told about the models that were in it a moment ago.
          const wasOn = [...visibleFlashModels];
          restoreFilterDefaults();
          for (const key of wasOn) setFlash(key, false);
          renderBody();
          actions.onFilterChange();
        });
      }

      renderBody();
    },
  });
}

// Flood Hub is the only flash model with polygons behind it; the rest are listed
// but draw nothing yet. Routed through one function so the checkbox, the "All"
// row and the reset cannot each decide differently what switching one means.
function setFlash(key, on) {
  if (key === "flood_hub") actions.setFlashOn(on);
}
