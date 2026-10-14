// The right-hand side panel: legends, forecast tiles, impact.
// Renders only; the actions its controls fire are injected by main.js, which keeps
// the panel free of imports from the layers that call it.
import {CONCUR_ANY_LABEL, CONCUR_COUNTS, CONCUR_LABEL, FIELD_LABELS, FLASH_MODELS, FLASH_TIER, FLASH_TIERS, MODEL_HOME, PANEL_MODELS, RIVER_ID_LABEL, SEVERITY, SEV_KEYS} from "./config.js";
import {fillSource, flashChanceLabel, flashNearLabel, fmtCount, fmtFlashIssued, fmtValue, inkOn, modelLabel, modelLink, nearLabel, worstSeverity} from "./format.js";
import {flashFill, flashOutline, paletteColor, rampFor, sevColor, sharedConcurRamp, unitLabel, view, visibleFlashModels, visibleModels, visibleSeverities, visibleTiers} from "./settings.js";
import {icon} from "./icons.js";

// The panel imports no layer module: the layers call in to it, never the other way
// round. That is what keeps cells <-> panel and flash <-> panel from being import
// cycles. It no longer has actions of its own to fire — the model filters that were
// its only interactive controls moved to the Filters menu on the map, so that one
// surface answers "which forecasts am I looking at". What is left here reports.

// What the panel is currently showing: the selected cell's properties, and the
// flash polygon last clicked (pushed in by layers/flash.js). Held here so a
// re-render needs nothing back from the layers.
let current = null;
let flashProps = null;

// Which collapsible section bodies are folded. Remembered for the same reason the
// open filter menus are: any tick or slider rebuilds the whole panel, so a section
// the user opened would close under their hand on the next change.
//
// Seeded with the Impact block, which opens folded. It is reference — what is under
// the cell, read once — rather than the reading itself, and unfolded it pushed the
// forecast tiles off the top of a short panel.
const collapsed = new Set(["sec-impact"]);

// The opening tag of a collapsible body, hidden or not according to that set.
const bodyTag = (id) => `<div id="${id}"${collapsed.has(id) ? ` class="hidden"` : ""}>`;

// Severity legend (Warning/Danger/Extreme + the Multi-model hash), shown under
// the River Floods section title.
// Severity legend. Models sharing a ramp share one row (the usual case, and
// what the app has always shown); as soon as two visible models are on
// different ramps each gets its own labelled row, so a color on the map is
// never ambiguous. The hash entry appears only while the highlight is on.
// A legend row for one of the dark model tiles. Each model carries its own key,
// so the color a tile explains is always the color that model paints — which is
// the whole point of a ramp per model. Shown on an idle tile too: the map is
// covered in that model's colors whether or not a cell happens to be selected.
function tileLegend(entries) {
  if (!entries.length) return "";
  return `<div class="flex flex-wrap gap-x-3 gap-y-1 mb-2">` +
    entries.map(({swatch, label}) =>
      `<span class="flex items-center gap-1.5 text-[10.5px] font-semibold text-slate-400">` +
      `${swatch}${label}</span>`).join("") + `</div>`;
}

// The heat key: a continuous bar rather than three chips, because the surface is
// continuous. Drawn for a dark tile background, which is where it lives now — the
// standalone light-panel version it replaces sat above the filter and said the same
// thing once for three tiles.
function heatKeyHtml(m) {
  // Only the rungs actually being drawn, exactly as the cells key does it. The bar
  // used to run the full ramp whatever the filter said, which made it the one thing
  // in the app that contradicted a filter rather than following it: hide Warning and
  // the surface loses its lightest cells while the key still promised them.
  const keys = SEV_KEYS.filter((k) => visibleSeverities.has(k));
  if (!keys.length) return "";
  const bar = (rampId) => {
    // 15%..99% across however many rungs survive, so the gradient keeps a flat run
    // of each end color rather than starting mid-transition. One rung is a flat bar.
    const step = keys.length > 1 ? 84 / (keys.length - 1) : 0;
    const stops = keys.map((k, i) =>
      `${paletteColor(rampId, k)} ${keys.length > 1 ? 15 + i * step : 50}%`).join(",");
    return `<div class="h-2 rounded-full border border-white/20" ` +
      `style="background:linear-gradient(to right,${stops})"></div>`;
  };
  const scale =
    `<div class="flex justify-between text-[10px] font-semibold text-slate-400 mt-1">` +
    keys.map((k) => `<span>${SEVERITY[k].label}</span>`).join("") + `</div>`;
  // A second bar when concurrence has a surface of its own: same three rungs, read
  // left to right in both, so the only difference between them is the one thing the
  // hue is there to say. The labels sit under the pair rather than under each,
  // because they mean the same positions on both.
  //
  // It repeats in every model tile, like the old concurrence mark did: it describes
  // a set of models rather than any one of them, and a reader looking at one tile
  // should not have to hunt through the others to learn what the color means.
  return `<div class="mb-2">` + bar(rampFor(m)) + concurBarHtml(bar) + scale + `</div>`;
}

// One bar per agreement count — unless the counts share a ramp, in which case one
// bar reading "2+ models". Two identical bars under two different labels would be
// the legend insisting on a distinction nothing on the map is drawing.
const concurRow = (bar, rampId, label) =>
  `<div class="flex items-center gap-1.5 mt-1.5">` +
  `<div class="flex-1">${bar(rampId)}</div>` +
  `<span class="text-[10px] font-semibold text-slate-400 shrink-0">${label}</span></div>`;

const concurBarHtml = (bar) => {
  if (!view.concurHeat) return "";
  const one = sharedConcurRamp();
  return one
    ? concurRow(bar, one, CONCUR_ANY_LABEL)
    : CONCUR_COUNTS.map((n) => concurRow(bar, view.concurRamp[n], CONCUR_LABEL[n])).join("");
};

const colorSwatch = (style) =>
  `<span class="inline-block w-3 h-3 rounded-sm shrink-0" style="${style}"></span>`;

// One model's severity key, in that model's own ramp — the same ramp its hexagons
// and its heat surface are painted in — limited to the severities the Filters menu
// is actually drawing.
function modelLegendHtml(m) {
  // Under the heat map the map is a blurred field rather than a set of rungs, so
  // the key is a gradient rather than three chips. Same ramp either way.
  if (view.heatOn) return heatKeyHtml(m);
  const entries = SEV_KEYS.filter((k) => visibleSeverities.has(k)).map((k) => ({
    swatch: colorSwatch(
      `background:${paletteColor(rampFor(m), k)};border:1px solid rgb(255 255 255 / .25)`),
    label: SEVERITY[k].label,
  }));
  // Concurrence takes a hue of its own in the cells view too, so it needs saying
  // here as well — one swatch at the worst rung, which is the end of the ramp a
  // reader will actually be looking at.
  if (view.concurHeat) {
    // Same collapse as the heat key: one swatch while the counts share a ramp.
    const one = sharedConcurRamp();
    const swatchFor = (rampId) => colorSwatch(
      `background:${paletteColor(rampId, SEV_KEYS[SEV_KEYS.length - 1])};` +
      `border:1px solid rgb(255 255 255 / .25)`);
    if (one) {
      entries.push({swatch: swatchFor(one), label: CONCUR_ANY_LABEL});
    } else {
      for (const n of CONCUR_COUNTS) {
        entries.push({swatch: swatchFor(view.concurRamp[n]), label: CONCUR_LABEL[n]});
      }
    }
  }
  return tileLegend(entries);
}

// The flash overlay's two tiers, drawn as they appear on the map, limited to the
// tiers being shown.
function flashLegendHtml() {
  return tileLegend(FLASH_TIERS.filter((t) => visibleTiers.has(t)).map((t) => ({
    swatch: colorSwatch(`background:${flashFill(t)};` +
      `border:1.5px solid ${flashOutline(t)}`),
    label: FLASH_TIER[t].label,
  })));
}

// Flash-flood tier legend (Likely / Highly likely), shared by all flash models,
// shown under the Flash Floods section title.
export const panelEmpty = document.getElementById("panel-empty");
export const panelContent = document.getElementById("panel-content");

// The panel always lists both models. A model shows a full card when the selected
// feature carries its forecast, otherwise a shrunken "standby" card (name linked to
// its own app). Flash-flood toggles live in their own section near the bottom.
export function renderPanel(props) {
  current = props || null;
  panelEmpty.hidden = true;
  panelContent.hidden = false;
  const selected = !!props;

  // The ink follows the fill: ramps now run dark-is-worse, so an `extreme` badge
  // can be near-black and the fixed dark text on it was unreadable.
  const badge = (sev, color) =>
    `<span class="inline-block px-2.5 py-0.5 rounded-full text-[11px] font-semibold capitalize" ` +
    `style="background:${color};color:${inkOn(color)}">${sev || "—"}</span>`;

  // A chevron that collapses the section body with matching id (wired after render).
  const collapseBtn = (target, cls = "text-slate-400 hover:text-slate-600") =>
    `<button type="button" class="collapse-btn shrink-0 ${cls}" data-target="${target}" aria-label="Collapse section">` +
    icon("chevron-down",
      `text-[13px] transition-transform${collapsed.has(target) ? " -rotate-90" : ""}`) +
    `</button>`;

  function modelTitle(m, fc) {
    const label = modelLabel(m);
    const href = (fc && modelLink(fc)) || MODEL_HOME[m];
    return `<a href="${href}" target="_blank" rel="noopener noreferrer" title="Open ${label}"
       class="inline-flex items-center gap-1 text-sky-300 hover:text-sky-200 hover:underline">${label}${
      icon("arrow-top-right-on-square", "text-[11px] opacity-80")}</a>`;
  }
  function modelTile(m) {
    const fcs = selected ? props.forecasts.filter((f) => f.model === m) : [];
    const has = fcs.length > 0;
    const titleSpan =
      `<span class="flex items-center gap-1.5 font-semibold text-[13px] capitalize ${has ? "text-slate-100" : "text-slate-400"}">` +
      // A cluster of hexagons: what a river model actually puts on the map, and
      // what tells these tiles apart from the flash ones at a glance.
      icon("hexagon-2", has ? "text-sky-300" : "text-slate-500") + modelTitle(m, fcs[0]) + `</span>`;
    const hide = visibleModels.has(m) ? "" : " hidden";
    if (!has) {
      return `<div id="tile-river-${m}" class="bg-[#141e2a] border border-slate-700/60 rounded-[10px] px-3.5 py-2.5 mb-3${hide}">` +
        `<div class="flex items-center justify-between gap-2 mb-1.5">${titleSpan}</div>` +
        modelLegendHtml(m) +
        `<div class="text-slate-500 text-[12px]">On standby — ${selected ? "no forecast for this area" : `click a ${view.dataset.unit.toLowerCase()} on the map`}.</div></div>`;
    }
    const bodyId = `sec-model-${m}`;
    const head =
      `<div class="flex items-center justify-between gap-2 mb-1.5">${titleSpan}` +
      collapseBtn(bodyId, "text-slate-400 hover:text-slate-200") + `</div>`;
    const body = fcs.map((fc, i) => {
      // A Flood Hub cell the pipeline INFERRED rather than gauged. Flood Hub warns
      // at points, so the gauge's own river would otherwise be blank; the backend
      // spreads it along the HydroRIVERS reach the gauge stands on. Every row below
      // therefore describes that gauge, not this hexagon — so the id and the
      // "Near" row are relabelled to say whose numbers these are, and the tile
      // carries a line explaining why there is no gauge here.
      const from = fillSource(fc);
      const rows = FIELD_LABELS.map(([k, label]) => {
        let lbl = k === "native_id" ? (RIVER_ID_LABEL[m] || label) : label;
        if (from && k === "native_id") lbl = "Filled from";
        if (from && k === "district") lbl = "Gauge near";
        const dt = `<dt class="text-slate-400">${lbl}</dt>`;
        if (k === "severity")
          return `${dt}<dd class="m-0">${badge(fc.severity, sevColor(fc.severity, m))}</dd>`;
        // The delivered id is the whole `from <gaugeId>` string; the label already
        // says "filled from", so the row shows the gauge alone.
        const val = from && k === "native_id" ? from
          : k === "district"
            ? nearLabel(fc.district, fc.district_count, fc.country, fc.district_level)
            : fmtValue(k, fc[k]);
        return `${dt}<dd class="m-0 text-slate-100 break-words">${val}</dd>`;
      }).join("");
      const note = from
        ? `<p class="mt-2 text-[11.5px] leading-snug text-slate-400 italic">` +
          `No gauge in this ${view.dataset.unit.toLowerCase()}. It sits on the same ` +
          `river reach as this gauge, and carries that gauge's forecast.</p>`
        : "";
      return `<dl class="grid grid-cols-[128px_1fr] gap-x-2.5 gap-y-1 text-[12.5px]${i ? " mt-2 pt-2 border-t border-slate-700/50" : ""}">${rows}</dl>${note}`;
    }).join("");
    return `<div id="tile-river-${m}" class="bg-[#1b2a3a] border border-slate-700 border-l-4 rounded-[10px] px-3.5 py-3 mb-3${hide}" style="border-left-color:${sevColor(worstSeverity(fcs), m)}">` +
      head + modelLegendHtml(m) + bodyTag(bodyId) + body + `</div></div>`;
  }

  // Forecast (river) tiles: always list PANEL_MODELS; the River Floods filter
  // hides the ones switched off (see the filter wiring below).
  const tiles = PANEL_MODELS.map(modelTile).join("");

  // A section whose models are all switched off used to render as a heading and
  // nothing else — and since the model filter moved to the Filters menu, nothing
  // inside the section could switch them back on either. That is a dead end at the
  // exact moment the viewer is wondering where their data went, so it says what
  // happened and where the control is. Flash floods start switched off, so this is
  // what that section shows at boot rather than an empty box.
  const noneShown = (models, set) => !models.some((m) => set.has(m)) ?
    `<div class="rounded-[10px] border border-dashed border-slate-700 px-3.5 py-3 mb-3 ` +
    `text-slate-400 text-[12px] leading-snug">No models selected. Choose them under ` +
    `<span class="text-slate-600 font-semibold">Filters</span> on the map.</div>` : "";

  // Section heading (the largest text in the panel) with a collapse chevron.
  const sectionHeader = (title, target, iconName) =>
    `<h2 class="flex items-center justify-between text-slate-900 font-bold text-[19px] mb-2.5">` +
    `<span class="flex items-center gap-1.5">${iconName ? icon(iconName, "text-slate-500 text-sm") : ""}${title}</span>` +
    collapseBtn(target) + `</h2>`;


  // A flash-flood model tile (dark card, violet accent): linked name, then the
  // attributes of the selected flash polygon (or a standby line). Interactive
  // like the river tiles — clicking a polygon fills its model's tile. Its
  // collapse body is `sec-flashmodel-<key>`.
  const flashModelTile = (fm) => {
    const bodyId = `sec-flashmodel-${fm.key}`;
    const link = MODEL_HOME[fm.key] || "#";
    const title =
      `<a href="${link}" target="_blank" rel="noopener noreferrer" title="Open ${fm.label}" ` +
      `class="inline-flex items-center gap-1 text-sky-300 hover:text-sky-200 hover:underline">${fm.label}` +
      icon("arrow-top-right-on-square", "text-[11px] opacity-80") + `</a>`;
    // Flood Hub is the only flash model today, so it carries the current selection.
    const p = fm.key === "flood_hub" ? flashProps : null;
    let body;
    if (p) {
      const rows = [
        ["Chance", flashChanceLabel(p.polygon_type)],
        ["Near", flashNearLabel(p)],
        ["Issued", fmtFlashIssued(p.forecastIssueTime)],
        ["Forecast window", p.forecastPeriodHours ? `Next ${p.forecastPeriodHours} hours` : "—"],
      ].map(([k, v]) =>
        `<dt class="text-slate-400">${k}</dt><dd class="m-0 text-slate-100 break-words">${v}</dd>`
      ).join("");
      body = `<dl class="grid grid-cols-[118px_1fr] gap-x-2.5 gap-y-1 text-[12.5px]">${rows}</dl>`;
    } else {
      body = `<div class="text-slate-500 text-[12px]">On standby — click a flash flood area on the map.</div>`;
    }
    // Collapse arrow and the violet left-accent only appear once a polygon is
    // selected; on standby the tile is a plain card.
    const head =
      `<div class="flex items-center justify-between gap-2 mb-1.5">` +
      `<span class="flex items-center gap-1.5 font-semibold text-[13px] text-slate-100">${icon("bolt", "text-violet-300")}${title}</span>` +
      (p ? collapseBtn(bodyId, "text-slate-400 hover:text-slate-200") : "") + `</div>`;
    const hide = visibleFlashModels.has(fm.key) ? "" : " hidden";
    const accent = p ? ` border-l-4` : "";
    const accentStyle = p ? ` style="border-left-color:#7c3aed"` : "";
    return `<div id="tile-flash-${fm.key}" class="bg-[#1b2a3a] border border-slate-700${accent} rounded-[10px] px-3.5 py-3 mb-3${hide}"${accentStyle}>` +
      head + flashLegendHtml() + bodyTag(bodyId) + body + `</div></div>`;
  };

  const imp = selected ? props.impact : null;
  const impTile = (name, label, value, span) =>
    `<div class="${span ? "col-span-2 " : ""}rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2">` +
    `<div class="flex items-center gap-1 text-slate-400 text-[10px] font-semibold uppercase tracking-wide mb-1">${icon(name, "text-[12px]")}${label}</div>` +
    `<div class="text-slate-800 font-bold text-[15px] leading-none">${value}</div></div>`;
  const impactHtml = imp
    ? `<div class="mt-4">` +
      `<h3 class="flex items-center justify-between text-slate-800 font-semibold text-[11px] uppercase tracking-wider mb-2">` +
      `<span class="flex items-center gap-1.5">${icon("exclamation-triangle", "text-amber-500 text-sm")}Impact</span>${collapseBtn("sec-impact")}</h3>` +
      bodyTag("sec-impact") + `<div class="grid grid-cols-2 gap-2">` +
      impTile("building-office-2", "Buildings", fmtCount(imp.buildings)) +
      impTile("area-hash-filled", "Farmland", fmtCount(imp.farmland_m2 / 1e6) + " km²") +
      impTile("car-travel-mode", "Roads", fmtCount(imp.highway_km) + " km") +
      impTile("train-travel-mode", "Railways", fmtCount(imp.railway_km) + " km") +
      impTile("users", "Population", fmtCount(imp.population), true) +
      `</div><p class="text-slate-400 text-[12px] mt-1.5">Totals across the whole ${unitLabel(props).toLowerCase()}.</p></div></div>`
    : "";

  // The concurrence filter hides about 96% of the flagged cells. Unannounced that
  // reads as a quiet day rather than as a filter, so it says so where the hidden
  // forecasts would have been, and names the control that turns it off. Amber
  // rather than slate: a state to notice, not a label. It no longer fires at boot —
  // the filter opens off — but it is the same strong effect whenever it is on.
  const concurNoticeHtml = view.concurrenceOnly
    ? `<div class="mb-3 rounded-[10px] border border-amber-300 bg-amber-50 px-3 py-2">` +
      `<div class="flex items-start gap-1.5">` +
      icon("funnel", "text-amber-600 text-[13px] mt-[1px]") +
      `<div><div class="text-[12px] font-semibold text-amber-900">Concurrence only</div>` +
      `<div class="text-[11px] leading-snug text-amber-800 mt-0.5">` +
      `Showing just the ${view.dataset.unit.toLowerCase()}s two or more models agree on. ` +
      `Turn it off under Filters → Severity.</div></div></div></div>`
    : "";

  // Section 1 — River Floods: the concurrence notice, the forecast tiles and
  // impact, all collapsible under one heading. The tiles name the selected cell
  // themselves, so the section carries no header of its own. A model switched off
  // in Filters drops its tile here, because the tile markup reads the same set.
  const riverSection =
    `<div id="section-river" class="mb-4">` + sectionHeader("River Floods", "sec-river") +
    bodyTag("sec-river") + concurNoticeHtml +
    noneShown(PANEL_MODELS, visibleModels) + tiles + impactHtml + `</div></div>`;

  // Section 2 — Flash Floods: a global overlay, one tile per enabled model.
  const flashKeys = FLASH_MODELS.map((fm) => fm.key);
  const flashSection =
    `<div id="section-flash" class="mt-12">` + sectionHeader("Flash Floods", "sec-flash") +
    bodyTag("sec-flash") + noneShown(flashKeys, visibleFlashModels) +
    FLASH_MODELS.map(flashModelTile).join("") + `</div></div>`;

  panelContent.innerHTML = riverSection + flashSection;

  // Collapse arrows: toggle the target section body and rotate the chevron.
  panelContent.querySelectorAll(".collapse-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const body = document.getElementById(btn.dataset.target);
      if (!body) return;
      const folded = body.classList.toggle("hidden");
      if (folded) collapsed.add(btn.dataset.target);
      else collapsed.delete(btn.dataset.target);
      const svg = btn.querySelector("svg");
      if (svg) svg.classList.toggle("-rotate-90", folded);
    });
  });

}

// Re-render the panel keeping the current basin selection. Used when only the
// flash selection changed, so the Flood Hub tile updates without disturbing
// the river section.
export function rerenderPanel() {
  renderPanel(current);
}

// layers/flash.js pushes its selection here rather than the panel reaching into
// it; null clears the Flood Hub tile back to standby.
export function setFlashProps(props) { flashProps = props || null; }

// Empty-state copy, owned by the panel: the wording for the loaded dataset, and
// the "this file has no features" case.
export function applyDatasetWording() {
  const h = document.querySelector("#panel-empty h2");
  const p = document.querySelector("#panel-empty p");
  if (h) h.textContent = view.dataset.emptyTitle;
  if (p) p.textContent = view.dataset.emptyBody;
}

export function showNoData(unit) {
  panelContent.hidden = true;
  panelEmpty.hidden = false;
  panelEmpty.innerHTML =
    `<h2 class="text-slate-800 font-semibold text-[15px] mb-1">No ${unit} data</h2>` +
    `<p class="text-sm leading-relaxed max-w-[240px]">This dataset's file loaded but has no features yet.</p>`;
}

// Bring a panel section into view after a selection (the panel just re-rendered,
// so wait a frame for layout). `sectionId` is section-river / section-flash.
export function scrollPanelToSection(sectionId) {
  requestAnimationFrame(() => {
    const el = document.getElementById(sectionId);
    if (el) el.scrollIntoView({behavior: "smooth", block: "start"});
  });
}

export function panelError(err) {
  panelContent.hidden = true;
  panelEmpty.hidden = false;
  document.getElementById("panel-empty").innerHTML =
    "<h2 class=\"text-slate-800 font-semibold text-[15px] mb-1\">Couldn't load forecast data</h2>" +
    "<p class=\"text-sm leading-relaxed max-w-[240px]\">" +
    String(err && err.message ? err.message : err) +
    "</p>";
}
