// Display Options: everything about how the map is drawn, split into two tabs —
// one for the flagged areas (cells or basins), one for the flash-flood overlay.
// The tabs exist so the panel stays short enough to leave most of the map visible.
import {DATASETS_MENU, FLASH_TIER, FLASH_TIERS, PANEL_MODELS, SEVERITY, SEV_KEYS} from "../config.js";
import {applyFillStyle} from "../layers/cells_tiles.js";
import {applyFlashStyle} from "../layers/flash.js";
import {dropdownControl} from "../ui/dropdown.js";
import {modelLabel} from "../format.js";
import {modelRamp, palettes, paletteColor, restoreDisplayDefaults, view, visibleSeverities, visibleTiers} from "../settings.js";

// main.js injects what a settings change should trigger, so this control never
// imports the orchestration that imports it.
let actions = {onDisplayChange() {}};
export function init(a) { actions = Object.assign(actions, a); }

let displayPanelEl = null;          // the menu's panel, once mounted
let activeTab = "areas";            // areas | flash
let rebuildBody = null;             // set once the panel is mounted; see restate()

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

const paletteLabel = (id) => (palettes.find((p) => p.id === id) || {}).label || id;

// The three rungs of a palette as one small bar: warning, danger, extreme, left to
// right. It sits beside the picker so the name in the list always has the colours
// it stands for next to it — "Magma" means nothing on its own.
const paletteSample = (id) =>
  `<span class="ramp-preview" data-for-sample="${id}" style="display:inline-flex;` +
  `width:36px;height:14px;border-radius:3px;overflow:hidden;border:1px solid #cbd5e1;` +
  `flex:0 0 auto">` +
  SEV_KEYS.map((k) => `<span style="flex:1;background:${paletteColor(id, k)}"></span>`).join("") +
  `</span>`;

// model name | sample | the list of palettes by name
function rampRow(key, label, current) {
  return `<div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">` +
    `<span style="flex:1;min-width:0;white-space:nowrap;overflow:hidden;` +
    `text-overflow:ellipsis">${label}</span>` +
    paletteSample(current) +
    `<select class="ramp-select" data-key="${key}" style="font:inherit;padding:2px 4px;` +
    `border:1px solid #cbd5e1;border-radius:5px;background:#fff;color:#0f172a;cursor:pointer">` +
    palettes.map((p) =>
      `<option value="${p.id}"${p.id === current ? " selected" : ""}>${p.label}</option>`).join("") +
    `</select></div>`;
}

const head = (t) =>
  `<div style="font-size:10px;color:#64748b;text-transform:uppercase;` +
  `letter-spacing:.04em;margin:0 0 5px">${t}</div>`;
const rule = `<div style="height:1px;background:#e2e8f0;margin:11px 0"></div>`;
const check = (id, label, on, accent) =>
  `<label style="display:flex;align-items:center;gap:6px;cursor:pointer;margin-bottom:3px">` +
  `<input type="checkbox" id="${id}"${on ? " checked" : ""} ` +
  `style="accent-color:${accent || "#0284c7"}">${label}</label>`;
const slider = (id, value) =>
  `<label style="display:flex;align-items:center;gap:7px;margin-bottom:4px">` +
  `<span style="white-space:nowrap">Fill</span>` +
  `<input type="range" id="${id}" min="0" max="80" step="5" value="${Math.round(value * 100)}" ` +
  `style="flex:1;accent-color:#0284c7;cursor:pointer">` +
  `<span id="${id}-val" style="width:30px;text-align:right;color:#64748b">` +
  `${Math.round(value * 100)}%</span></label>`;

// ---- Tab bodies ------------------------------------------------------------

function areasTab() {
  // The H3/Basins switch is gone for now: the CSV pipeline builds cells only, and
  // an option that 404s is worse than no option. It comes back when basins are
  // rebuilt on the new backend.
  return head("Appearance") +
    PANEL_MODELS.map((m) => rampRow(m, modelLabel(m), modelRamp[m])).join("") +
    slider("disp-opacity", view.fillOpacity) +
    check("disp-outline", "Outline only", view.outlineOnly) + rule +

    head("Highlight") +
    check("disp-hatch", "Hatch where models concur", view.hatchOn) + rule +

    head("Severity shown") +
    SEV_KEYS.map((k) => check("disp-sev-" + k, SEVERITY[k].label, visibleSeverities.has(k))).join("");
}

function flashTab() {
  return head("Appearance") +
    rampRow("flash", "Flash floods", view.flashRampId) +
    slider("disp-flash-opacity", view.flashOpacity) +
    check("disp-flash-outline", "Outline only", view.flashOutlineOnly) + rule +

    head("Tiers shown") +
    FLASH_TIERS.map((t) =>
      check("disp-tier-" + t, FLASH_TIER[t].label, visibleTiers.has(t))).join("") +

    `<p style="font:400 11px system-ui,sans-serif;color:#94a3b8;margin:9px 0 0">` +
    `Switch the overlay itself on or off from Flash Floods in the side panel.</p>`;
}

// ---- The control -----------------------------------------------------------

export function displayControl() {
  const TABS = [["areas", "River Floods"], ["flash", "Flash Floods"]];

  return dropdownControl({
    iconName: "monitor",
    title: "Display Options",
    panelStyle: "padding:10px 12px;width:286px;max-height:min(560px,calc(100vh - 150px));" +
      "overflow-y:auto;font:600 12px system-ui,sans-serif;color:#0f172a",
    render(panel) {
      // The tab bar is rendered once; only the body below it is swapped, so the
      // bar never flickers and its listener is bound a single time.
      panel.innerHTML =
        `<div id="disp-tabs" style="display:flex;gap:4px;padding:3px;margin-bottom:10px;` +
        `background:#f1f5f9;border-radius:8px">` +
        TABS.map(([key, label]) =>
          `<button type="button" class="disp-tab" data-tab="${key}" style="flex:1;border:0;` +
          `padding:5px 6px;border-radius:6px;cursor:pointer;font:inherit;white-space:nowrap">${label}</button>`).join("") +
        `</div><div id="disp-body"></div>` + rule +
        `<button type="button" id="disp-reset" style="width:100%;border:1px solid #cbd5e1;` +
        `background:#fff;padding:6px 8px;border-radius:6px;cursor:pointer;color:#334155;` +
        `font:inherit">Restore defaults</button>`;

      const body = panel.querySelector("#disp-body");

      function paintTabs() {
        panel.querySelectorAll(".disp-tab").forEach((b) => {
          const on = b.dataset.tab === activeTab;
          b.style.background = on ? "#fff" : "transparent";
          b.style.color = on ? "#0f172a" : "#64748b";
          b.style.boxShadow = on ? "0 1px 2px rgb(0 0 0 / .12)" : "none";
        });
      }

      function renderTab() {
        body.innerHTML = activeTab === "areas" ? areasTab() : flashTab();
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
          if (key === "flash") view.flashRampId = sel.value; else modelRamp[key] = sel.value;
          renderTab();                    // the sample beside the picker follows
          actions.onDisplayChange();      // the map and the panel's legends
        }));

        // --- areas tab ---
        const hatch = $("#disp-hatch");
        if (hatch) hatch.addEventListener("change", (e) => {
          view.hatchOn = e.target.checked;
          actions.onDisplayChange();
        });

        SEV_KEYS.forEach((k) => {
          const cb = $("#disp-sev-" + k);
          if (!cb) return;
          cb.addEventListener("change", (e) => {
            if (e.target.checked) visibleSeverities.add(k); else visibleSeverities.delete(k);
            actions.onDisplayChange();
          });
        });

        bindFill($("#disp-opacity"), $("#disp-outline"), "fillOpacity", "outlineOnly", applyFillStyle);

        // --- flash tab ---
        FLASH_TIERS.forEach((t) => {
          const cb = $("#disp-tier-" + t);
          if (!cb) return;
          cb.addEventListener("change", (e) => {
            if (e.target.checked) visibleTiers.add(t); else visibleTiers.delete(t);
            applyFlashStyle();
            actions.onDisplayChange();    // the flash legend follows the tiers
          });
        });

        bindFill($("#disp-flash-opacity"), $("#disp-flash-outline"),
          "flashOpacity", "flashOutlineOnly", applyFlashStyle);
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
        });
        outline.addEventListener("change", (e) => {
          view[outlineKey] = e.target.checked;
          range.disabled = view[outlineKey];
          apply();
        });
      }

      panel.querySelectorAll(".disp-tab").forEach((b) => b.addEventListener("click", () => {
        if (b.dataset.tab === activeTab) return;
        activeTab = b.dataset.tab;
        renderTab();
      }));

      // Reset covers both tabs at once, so it needs all three update paths: the
      // opacity/outline pair are pure paint, the ramps, hatch and severity filter
      // need the feature rebuild, and renderTab redraws the controls themselves so
      // the sliders and checkboxes show the values that were just restored.
      panel.querySelector("#disp-reset").addEventListener("click", () => {
        restoreDisplayDefaults();
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
