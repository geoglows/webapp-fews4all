// Display Options: everything about how the map is drawn, split into three tabs —
// the flagged areas (cells or heat), the flash-flood overlay, and the context the
// two are read against.
// The tabs exist so the panel stays short enough to leave most of the map visible.
//
// Rendering only. Which forecasts reach the map — models, severities, concurrence,
// flash tiers — belongs to the Filters menu next door. Keeping both halves here is
// what pushed this panel past its own height into a scrollbar, and it meant the
// severity rung names appeared under two unrelated headings in the same column.
import {CONCUR_COUNTS, CONCUR_LABEL, FLASH_TIER, FLASH_TIERS, PANEL_MODELS, SEVERITY, SEV_KEYS, STREAM_DENSITY_MAX, streamMinOrder} from "../config.js";
import {applyFillStyle} from "../layers/cells_tiles.js";
import {applyHeatStyle} from "../layers/heat.js";
import {applyFlashStyle} from "../layers/flash.js";
import {applyStreamStyle, setStreamsOn, streamsOn} from "../layers/streams.js";
import {dropdownControl} from "../ui/dropdown.js";
import {check, head, note, resetButton, rule} from "../ui/form.js";
import {modelLabel} from "../format.js";
import {claimRamp, modelRamp, palettes, paletteColor, restoreDisplayDefaults, view} from "../settings.js";

// main.js injects what a settings change should trigger, so this control never
// imports the orchestration that imports it.
let actions = {onDisplayChange() {}};
export function init(a) { actions = Object.assign(actions, a); }

let displayPanelEl = null;          // the menu's panel, once mounted
let activeTab = "areas";            // areas | flash | context
let rebuildBody = null;             // set once the panel is mounted; see restate()
// Whether the per-model ramp rows are showing. Closed, the block is one row —
// which is the whole truth whenever the models share a ramp, because then the
// three rows underneath each repeat what that row already says. See rampBlock().
let rampsOpen = false;

/**
 * Rebuild the menu from current settings.
 *
 * The control is added to the map before the release finishes loading, so its
 * first render lists whatever palettes the app ships with rather than the ones
 * the release actually offers. Calling this after a release lands is what makes
 * the two agree — without it the menu is permanently a release behind.
 */
export function restate() {
  if (rebuildBody) rebuildBody();
}

export function highlightDataset() {
  if (!displayPanelEl) return;
  displayPanelEl.querySelectorAll(".ds-row").forEach((b) => {
    const on = b.dataset.ds === view.currentDatasetKey;
    b.style.background = on ? "#0284c7" : "#fff";
    b.style.color = on ? "#fff" : "#334155";
    b.style.borderColor = on ? "#0284c7" : "#cbd5e1";
  });
}

// The three rungs of a palette as one small bar: warning, danger, extreme, left to
// right. It sits beside the picker so the name in the list always has the colors
// it stands for next to it — "Magma" means nothing on its own.
const paletteSample = (id) =>
  `<span class="ramp-preview" data-for-sample="${id}" style="display:inline-flex;` +
  `width:36px;height:14px;border-radius:3px;overflow:hidden;border:1px solid #cbd5e1;` +
  `flex:0 0 auto">` +
  SEV_KEYS.map((k) => `<span style="flex:1;background:${paletteColor(id, k)}"></span>`).join("") +
  `</span>`;

// model name | sample | the list of palettes by name
// The sentinel the "All models" row carries in place of a model key.
const ALL_MODELS = "__all";

// `current` null means the models disagree: no ramp is selected and the sample
// is blank, so the row reports a mixed state instead of claiming one of them.
function rampRow(key, label, current, bold) {
  return `<div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">` +
    `<span style="flex:1;min-width:0;white-space:nowrap;overflow:hidden;` +
    `text-overflow:ellipsis${bold ? ";font-weight:600" : ""}">${label}</span>` +
    (current ? paletteSample(current) : `<span style="width:34px"></span>`) +
    `<select class="ramp-select" data-key="${key}" aria-label="${label} color ramp" ` +
    `style="font:inherit;padding:2px 4px;` +
    `border:1px solid #cbd5e1;border-radius:5px;background:#fff;color:#0f172a;cursor:pointer">` +
    (current ? "" : `<option value="" selected disabled>Mixed</option>`) +
    palettes.map((p) =>
      `<option value="${p.id}"${p.id === current ? " selected" : ""}>${p.label}</option>`).join("") +
    `</select></div>`;
}

// The ramp every model shares, or null when they differ.
const sharedRamp = (keys) => {
  const first = modelRamp[keys[0]];
  return keys.every((m) => modelRamp[m] === first) ? first : null;
};

// One row per severity rung: a name and the weight of its outline, in px — px
// rather than % because px is what MapLibre takes. The checkbox that used to sit
// in this row, deciding whether the rung was drawn at all, is in Filters now: the
// two were merged to save height, and they answer different questions.
const weightRow = (k, label, width) =>
  `<div style="display:flex;align-items:center;gap:7px;margin-bottom:5px">` +
  `<span style="width:66px;white-space:nowrap;font-weight:400">${label}</span>` +
  `<input type="range" id="disp-lw-${k}" min="0" max="5" step="0.1" value="${width}" ` +
  `aria-label="${label} outline weight, px" ` +
  `style="flex:1;accent-color:#0284c7;cursor:pointer" title="Outline weight, px">` +
  `<span id="disp-lw-${k}-val" style="width:26px;text-align:right;color:#64748b">` +
  `${Number(width).toFixed(1)}</span></div>`;

// A labelled range carrying its value as hundredths, so one control serves both an
// opacity (0..1) and the spread multiplier. `fmt` turns the slider's integer into
// what the readout says, because those two are not the same kind of number: an
// opacity is a percentage of full, a spread is a multiple of the tuned blur.
const asPercent = (v) => `${v}%`;
const asMultiple = (v) => `${(v / 100).toFixed(1)}\u00d7`;

// The hundredths dance above is for values `view` stores as 0..1. A count — a
// Strahler order, say — is already the number the slider carries, so it gets its
// own row rather than being multiplied by 100 and divided back.
const intRange = (id, label, value, min, max, fmt) =>
  `<label style="display:flex;align-items:center;gap:7px;margin-bottom:4px">` +
  `<span style="white-space:nowrap;width:46px">${label}</span>` +
  `<input type="range" id="${id}" min="${min}" max="${max}" step="1" ` +
  `aria-label="${label}" aria-valuetext="${fmt(value)}" ` +
  `value="${value}" style="flex:1;accent-color:#0284c7;cursor:pointer">` +
  `<span id="${id}-val" style="width:38px;text-align:right;color:#64748b">` +
  `${fmt(value)}</span></label>`;

// What the density readout says: the cut it is making, not a made-up percentage.
const densityLabel = (d) => {
  const min = streamMinOrder(d);
  return min <= 1 ? "all" : `\u2265${min}`;
};

const pctRange = (id, label, value, min = 0, max = 100, step = 5, fmt = asPercent) =>
  `<label style="display:flex;align-items:center;gap:7px;margin-bottom:4px">` +
  `<span style="white-space:nowrap;width:46px">${label}</span>` +
  `<input type="range" id="${id}" min="${min}" max="${max}" step="${step}" ` +
  `aria-label="${label}" ` +
  `value="${Math.round(value * 100)}" style="flex:1;accent-color:#0284c7;cursor:pointer">` +
  `<span id="${id}-val" style="width:38px;text-align:right;color:#64748b">` +
  `${fmt(Math.round(value * 100))}</span></label>`;

// The flash counterpart of weightRow. Wider label column than the river side —
// "Highly likely" is a longer word than "Extreme".
const tierWeightRow = (t, label, width) =>
  `<div style="display:flex;align-items:center;gap:7px;margin-bottom:5px">` +
  `<span style="width:90px;white-space:nowrap;font-weight:400">${label}</span>` +
  `<input type="range" id="disp-flw-${t}" min="0" max="5" step="0.1" value="${width}" ` +
  `aria-label="${label} outline weight, px" ` +
  `style="flex:1;accent-color:#0284c7;cursor:pointer" title="Outline weight, px">` +
  `<span id="disp-flw-${t}-val" style="width:26px;text-align:right;color:#64748b">` +
  `${Number(width).toFixed(1)}</span></div>`;

const slider = (id, value) =>
  `<label style="display:flex;align-items:center;gap:7px;margin-bottom:4px">` +
  `<span style="white-space:nowrap">Fill</span>` +
  `<input type="range" id="${id}" min="0" max="100" step="5" value="${Math.round(value * 100)}" ` +
  `aria-label="Fill opacity" style="flex:1;accent-color:#0284c7;cursor:pointer">` +
  `<span id="${id}-val" style="width:30px;text-align:right;color:#64748b">` +
  `${Math.round(value * 100)}%</span></label>`;

// ---- Tab bodies ------------------------------------------------------------

function areasTab() {
  // The tab opens on the choice everything below it depends on: which of the two
  // renderings the flagged areas get. Nothing but the toggle lives here, so the
  // question reads as a question — the settings for whichever rendering it picks
  // are further down, in a block named after that rendering.
  const typeBlock =
    head("Flagged area type") +
    check("disp-heat", "Draw severity as heat", view.heatOn) +
    (view.heatOn
      ? note("One surface per model, in the colors below. Cells go invisible " +
        "underneath but stay clickable — hover or select one and it comes back.")
      : note("Cells are drawn as hexagons, in the colors below."));

  // One ramp per model, and it now drives BOTH views — the model's hexagons and
  // the model's own heat surface. There is no separate heat ramp any more.
  //
  // Only the "All models" row is shown while the models agree, because the three
  // rows under it would then be three copies of it — 78px of panel height spent
  // restating one word. The moment they stop agreeing the All row reads "Mixed",
  // which is not a state anyone can act on, so the rows open themselves and stay
  // open: `shared` is null exactly when they are the only way to read the ramps.
  const shared = sharedRamp(PANEL_MODELS);
  const open = rampsOpen || !shared;
  const rampBlock =
    head("Colors &nbsp;·&nbsp; per model") +
    rampRow(ALL_MODELS, "All models", shared, true) +
    (open ? PANEL_MODELS.map((m) => rampRow(m, modelLabel(m), modelRamp[m])).join("") : "") +
    (shared
      ? `<button type="button" id="disp-ramps-toggle" style="border:0;background:none;` +
        `padding:0;margin:1px 0 0;cursor:pointer;font:inherit;font-weight:400;` +
        `color:#0284c7">${open ? "Hide" : "Show"} each model</button>`
      : "");

  // The H3/Basins switch is gone for now: the CSV pipeline builds cells only, and
  // an option that 404s is worse than no option. It comes back when basins are
  // rebuilt on the new backend.
  const cellBlock = view.heatOn ? "" :
    head("Cells") +
    slider("disp-opacity", view.fillOpacity) +
    check("disp-outline", "Outline only", view.outlineOnly);

  // Switched on automatically the moment the model ramps stop matching — see the
  // ramp handler. With two models on different colors, a cell holding both has no
  // single right answer, and this gives it one.
  const concurBlock =
    head("Concurrence") +
    check("disp-concur-heat", "Color concurrence separately", view.concurHeat) +
    (view.concurHeat
      ? CONCUR_COUNTS.map((n) =>
          rampRow(`concur${n}`, CONCUR_LABEL[n], view.concurRamp[n])).join("")
      : "");

  // Outline weights, and only the weights — which rungs are drawn at all is a
  // Filters question. Gone entirely under the heat map, where nothing has an
  // outline to weight: a slider that moves and paints nothing is worse than no
  // slider, and it is the block this tab can most afford to lose.
  const weightBlock = view.heatOn ? "" :
    head("Outline weight &nbsp;·&nbsp; px") +
    SEV_KEYS.map((k) => weightRow(k, SEVERITY[k].label, view.lineWidth[k])).join("");

  // The heat map's own two settings, the counterpart of the Cells block. Both
  // blocks sit in the same place in the list and only one of them is ever built,
  // so switching the type swaps a section rather than reshuffling the tab.
  const heatBlock = !view.heatOn ? "" :
    head("Heat map") +
    pctRange("disp-heat-opacity", "Opacity", view.heatOpacity) +
    // Floor at 0.7x, not lower. Measured: below that the blur is narrower than the
    // gap between cell centres from z8 in (ratio 0.62 at 0.4x), so the surface
    // breaks into separate dots — the exact failure the ground-constant radius was
    // built to prevent. 0.7x is the lowest setting that still merges at every zoom.
    pctRange("disp-heat-radius", "Spread", view.heatRadius, 70, 250, 10, asMultiple);

  // Type, then the two settings that describe the data either way, then whichever
  // rendering is on and what only it can be asked about.
  return [typeBlock, rampBlock, concurBlock, cellBlock, weightBlock, heatBlock]
    .filter(Boolean).join(rule);
}

// The third tab: what the hazard layers are read AGAINST rather than either of
// them. The stream network is the context itself, and the pointer label is drawn
// over flash polygons as readily as over cells, so it has no business on either
// layer's tab.
//
// It had more company once — a "flash floods above cells" switch — until that
// stopped being a question. Flash now always draws under both renderings of the
// cells; see layerOrder() in map.js for why that has one right answer.
function contextTab() {
  // The stream network used to have a map control of its own, two buttons down the
  // same column. One checkbox does not earn a dropdown, and it is a rendering
  // choice about the map like everything else on this tab.
  const contextBlock =
    head("River network") +
    check("disp-streams", "GEOGLOWS V3 streams", streamsOn(), "#2b7fd4") +
    (streamsOn()
      ? intRange("disp-stream-density", "Density", view.streamDensity,
          1, STREAM_DENSITY_MAX, densityLabel) +
        note("Zoomed in over one basin the whole network is a thicket. Lower this " +
          "to drop the smallest tributaries a rung at a time — the readout is the " +
          "Strahler order it keeps.", 7)
      : note("The network itself, drawn under the flagged areas. It carries no " +
          "forecast of its own."));

  const pointerBlock =
    head("Pointer") +
    check("disp-cell-tip",
      `Label the ${view.dataset.unit.toLowerCase()} under the cursor`, view.cellTooltip) +
    note("Hovering still lights the shape up and clicking still selects it; " +
      "this is only the label that follows the cursor.");

  return [contextBlock, pointerBlock].join(rule);
}

function flashTab() {
  return head("Appearance") +
    rampRow("flash", "Flash floods", view.flashRampId) +
    slider("disp-flash-opacity", view.flashOpacity) +
    check("disp-flash-outline", "Outline only", view.flashOutlineOnly) + rule +

    head("Outline weight &nbsp;·&nbsp; px") +
    FLASH_TIERS.map((t) =>
      tierWeightRow(t, FLASH_TIER[t].label, view.flashLineWidth[t])).join("") +

    note("Switch the overlay on, and choose which tiers it shows, under Filters.", 9);
}

// ---- The control -----------------------------------------------------------

export function displayControl() {
  const TABS = [["areas", "River"], ["flash", "Flash"], ["context", "Context"]];

  return dropdownControl({
    iconName: "monitor",
    title: "Display Options",
    // 680 rather than the old 560. The cap was set when this menu held the filters
    // too and was overflowing whatever it was given; with those gone, every default
    // state renders at 419-531px and needs no cap at all. What is left for the cap
    // to catch is one case: the per-model ramps opened AND concurrence colored
    // separately AND the heat map off, which measures 657px. 680 clears it on any
    // screen over ~810px tall, and `min` still yields to a shorter one.
    panelStyle: "padding:10px 12px;width:364px;max-height:min(680px,calc(100vh - 150px));" +
      "overflow-y:auto;font:600 12px system-ui,sans-serif;color:#0f172a",
    render(panel) {
      // The tab bar is rendered once; only the body below it is swapped, so the
      // bar never flickers and its listener is bound a single time.
      panel.innerHTML =
        `<div id="disp-tabs" style="display:flex;gap:4px;padding:3px;margin-bottom:10px;` +
        `background:#f1f5f9;border-radius:8px" role="tablist">` +
        TABS.map(([key, label]) =>
          `<button type="button" class="disp-tab" data-tab="${key}" role="tab" ` +
          `aria-selected="${key === activeTab}" style="flex:1;border:0;` +
          `padding:5px 6px;border-radius:6px;cursor:pointer;font:inherit;white-space:nowrap">${label}</button>`).join("") +
        `</div><div id="disp-body"></div>` + rule +
        resetButton("disp-reset", "Restore defaults");

      const body = panel.querySelector("#disp-body");

      function paintTabs() {
        panel.querySelectorAll(".disp-tab").forEach((b) => {
          const on = b.dataset.tab === activeTab;
          b.style.background = on ? "#fff" : "transparent";
          // Inactive tab: slate-600, not slate-500 — on the #f1f5f9 tab strip the
          // lighter step measured 4.34:1, just under AA for 12px.
          b.style.color = on ? "#0f172a" : "#475569";
          b.setAttribute("aria-selected", on ? "true" : "false");
          b.style.boxShadow = on ? "0 1px 2px rgb(0 0 0 / .12)" : "none";
        });
      }

      function renderTab() {
        body.innerHTML =
          activeTab === "areas" ? areasTab() : activeTab === "flash" ? flashTab() : contextTab();
        paintTabs();
        bindBody();
        if (activeTab === "areas") highlightDataset();
      }
      rebuildBody = renderTab;

      // Every control in the body is re-bound whenever the body is rebuilt.
      function bindBody() {
        const $ = (sel) => body.querySelector(sel);

        body.querySelectorAll(".ramp-select").forEach((sel) => sel.addEventListener("change", () => {
          const key = sel.dataset.key;
          // claimRamp marks the model as the viewer's choice, so the next release
          // -- or the next step along the timeline -- seeds around it instead of
          // stamping the release's own palette back over it.
          if (key === ALL_MODELS) {
            for (const m of PANEL_MODELS) { modelRamp[m] = sel.value; claimRamp(m); }
          } else if (key === "flash") view.flashRampId = sel.value;
          else if (key.startsWith("concur")) view.concurRamp[Number(key.slice(6))] = sel.value;
          else { modelRamp[key] = sel.value; claimRamp(key); }
          // Once two models are on different ramps, a cell holding both has no
          // single right color — so the moment they stop matching, concurrence
          // takes a color of its own and the question stops arising. Switched on
          // automatically, never off: putting the ramps back in step leaves it
          // alone, because by then it may be what the viewer actually wants.
          if (!sharedRamp(PANEL_MODELS)) view.concurHeat = true;
          renderTab();                    // the sample beside the picker follows
          actions.onDisplayChange();      // the map and the panel's legends
        }));

        // --- context tab ---
        const streamsCb = $("#disp-streams");
        if (streamsCb) streamsCb.addEventListener("change", (e) => {
          setStreamsOn(e.target.checked);
          renderTab();                    // the density slider appears with it
        });

        const density = $("#disp-stream-density");
        if (density) {
          const readout = body.querySelector("#disp-stream-density-val");
          density.addEventListener("input", () => {
            view.streamDensity = Number(density.value);
            if (readout) readout.textContent = densityLabel(view.streamDensity);
            // The slider counts the opposite way from the order it keeps, so the
            // raw position is meaningless read aloud; the cut is the fact.
            density.setAttribute("aria-valuetext", densityLabel(view.streamDensity));
            applyStreamStyle();           // one setFilter; nothing refetches
          });
        }

        const rampsToggle = $("#disp-ramps-toggle");
        if (rampsToggle) rampsToggle.addEventListener("click", () => {
          rampsOpen = !rampsOpen;
          renderTab();                    // markup only; nothing on the map moves
        });

        const heatCb = $("#disp-heat");
        if (heatCb) heatCb.addEventListener("change", (e) => {
          view.heatOn = e.target.checked;
          // The menu itself changes: the two views own different controls.
          renderTab();
          applyHeatStyle();
          // And the cells' own paint, which goes to zero under the surface and
          // takes the heat's ramp for whatever is still drawn.
          applyFillStyle();
          actions.onDisplayChange();      // the join and the panel keys
        });
        bindPct($("#disp-heat-opacity"), "heatOpacity");
        bindPct($("#disp-heat-radius"), "heatRadius", asMultiple);

        const cellTip = $("#disp-cell-tip");
        if (cellTip) cellTip.addEventListener("change", (e) => {
          // Nothing to repaint or rebuild: the hover handler reads this live on the
          // next mouse move, and the map has no pointer on it while this menu is open.
          view.cellTooltip = e.target.checked;
        });

        const concurHeat = $("#disp-concur-heat");
        if (concurHeat) concurHeat.addEventListener("change", (e) => {
          view.concurHeat = e.target.checked;
          // Reveals or hides its ramp picker, and repartitions both point sets.
          renderTab();
          actions.onDisplayChange();
        });

        SEV_KEYS.forEach((k) => {
          const range = $("#disp-lw-" + k);
          if (!range) return;
          const readout = body.querySelector("#disp-lw-" + k + "-val");
          range.addEventListener("input", () => {
            view.lineWidth[k] = Number(range.value);
            if (readout) readout.textContent = Number(range.value).toFixed(1);
            actions.onDisplayChange();
          });
        });

        bindFill($("#disp-opacity"), $("#disp-outline"), "fillOpacity", "outlineOnly", applyFillStyle);

        // --- flash tab ---
        FLASH_TIERS.forEach((t) => {
          const range = $("#disp-flw-" + t);
          if (!range) return;
          const readout = body.querySelector("#disp-flw-" + t + "-val");
          range.addEventListener("input", () => {
            view.flashLineWidth[t] = Number(range.value);
            if (readout) readout.textContent = Number(range.value).toFixed(1);
            applyFlashStyle();
          });
        });

        bindFill($("#disp-flash-opacity"), $("#disp-flash-outline"),
          "flashOpacity", "flashOutlineOnly", applyFlashStyle);
      }

      // A percentage range bound straight onto one `view` key. Paint only — the
      // surface's geometry never changes, so none of these rebuild anything.
      // Both of these go through actions.onDisplayChange() as well as their own
      // paint call. Without it they were the one set of Display controls that did
      // not clear the active View -- so dragging Fill or Opacity after applying
      // "Flash focus" left the Views menu still marked ON for a view whose own
      // settings had just been overwritten.
      function bindPct(range, key, fmt = asPercent) {
        if (!range) return;
        const readout = body.querySelector("#" + range.id + "-val");
        range.addEventListener("input", () => {
          view[key] = Number(range.value) / 100;
          if (readout) readout.textContent = fmt(Number(range.value));
          applyHeatStyle();
          actions.onDisplayChange();
        });
      }

      // Slider + outline-only pair, shared by both tabs: both are pure paint
      // changes, so they never trigger a feature rebuild.
      function bindFill(range, outline, opacityKey, outlineKey, apply) {
        if (!range || !outline) return;
        const readout = body.querySelector("#" + range.id + "-val");
        range.disabled = view[outlineKey];
        range.addEventListener("input", () => {
          view[opacityKey] = Number(range.value) / 100;
          if (readout) readout.textContent = range.value + "%";
          apply();
          actions.onDisplayChange();
        });
        outline.addEventListener("change", (e) => {
          view[outlineKey] = e.target.checked;
          range.disabled = view[outlineKey];
          apply();
          actions.onDisplayChange();
        });
      }

      panel.querySelectorAll(".disp-tab").forEach((b) => b.addEventListener("click", () => {
        if (b.dataset.tab === activeTab) return;
        activeTab = b.dataset.tab;
        renderTab();
      }));

      // Reset covers both tabs at once, so it needs all three update paths: the
      // opacity/outline pair are pure paint, the ramps need the feature rebuild,
      // and renderTab redraws the controls themselves so the sliders and checkboxes
      // show the values that were just restored. It reaches no filter: those have
      // their own reset, in their own menu.
      panel.querySelector("#disp-reset").addEventListener("click", () => {
        restoreDisplayDefaults();
        applyStreamStyle();             // the density cut is a Display setting too
        // The ramp disclosure is menu state, not a `view` setting, so the restore
        // cannot reach it — and a tab that came back on its defaults with a block
        // still hanging open has not quite been restored.
        rampsOpen = false;
        renderTab();
        applyFillStyle();
        applyFlashStyle();
        actions.onDisplayChange();
      });

      renderTab();
    },
    onReady(container, panel) {
      displayPanelEl = panel;
      highlightDataset();
    },
  });
}
