// The right-hand side panel: legends, model filters, forecast tiles, impact.
// Renders only; the actions its controls fire are injected by main.js, which keeps
// the panel free of imports from the layers that call it.
import {FIELD_LABELS, FLASH_MODELS, FLASH_TIER, FLASH_TIERS, MODEL_HOME, PANEL_MODELS, RIVER_ID_LABEL, SEVERITY, SEV_KEYS} from "./config.js";
import {fillSource, flashChanceLabel, flashNearLabel, fmtCount, fmtFlashIssued, fmtValue, inkOn, modelLabel, modelLink, nearLabel, worstSeverity} from "./format.js";
import {flashFill, flashOutline, modelRamp, paletteColor, sevColor, unitLabel, view, visibleFlashModels, visibleModels, visibleSeverities, visibleTiers} from "./settings.js";
import {icon} from "./icons.js";

// The panel imports no layer module: the layers call in to it, and the two actions
// its own controls fire are injected by main.js. That is what keeps cells <-> panel
// and flash <-> panel from being import cycles.
let actions = {refreshFeatures() {}, setFlashOn() {}};
export function init(a) { actions = Object.assign(actions, a); }

// What the panel is currently showing: the selected cell's properties, and the
// flash polygon last clicked (pushed in by layers/flash.js). Held here so a
// re-render needs nothing back from the layers.
let current = null;
let flashProps = null;

// Which filter menus ("river" / "flash") are open. Ticking a box rebuilds the
// whole panel — a river toggle refreshes the map features, which re-renders from
// scratch — so the menu element the user is standing in gets destroyed and
// replaced. Remembering the open ones here, outside the markup, means the rebuilt
// menu comes back open and the user can tick several models in a row. Keyed by
// `data-kind`, so it survives any cause of a re-render, not just this one.
const openFilters = new Set();

// Severity legend (Warning/Danger/Extreme + the Multi-model hash), shown under
// the River Floods section title.
// Severity legend. Models sharing a ramp share one row (the usual case, and
// what the app has always shown); as soon as two visible models are on
// different ramps each gets its own labelled row, so a colour on the map is
// never ambiguous. The hash entry appears only while the highlight is on.
// A legend row for one of the dark model tiles. Each model carries its own key,
// so the colour a tile explains is always the colour that model paints — which is
// the whole point of a ramp per model. Shown on an idle tile too: the map is
// covered in that model's colours whether or not a cell happens to be selected.
function tileLegend(entries) {
  if (!entries.length) return "";
  return `<div class="flex flex-wrap gap-x-3 gap-y-1 mb-2">` +
    entries.map(({swatch, label}) =>
      `<span class="flex items-center gap-1.5 text-[10.5px] font-semibold text-slate-400">` +
      `${swatch}${label}</span>`).join("") + `</div>`;
}

const colorSwatch = (style) =>
  `<span class="inline-block w-3 h-3 rounded-sm shrink-0" style="${style}"></span>`;

// The concurrence hash, drawn light because a tile's background is dark — the
// section legend it replaces sat on the white panel and used HASH_COLOR.
const hashSwatch = () =>
  `<span class="inline-flex w-3 h-3 rounded-sm border border-white/30 overflow-hidden shrink-0">` +
  `<svg viewBox="0 0 14 14" width="12" height="12" aria-hidden="true"><g stroke="#cbd5e1" stroke-width="1.3">` +
  `<line x1="0" y1="5" x2="5" y2="0"/><line x1="0" y1="10" x2="10" y2="0"/>` +
  `<line x1="4" y1="14" x2="14" y2="4"/><line x1="9" y1="14" x2="14" y2="9"/></g></svg></span>`;

// One model's severity key, in that model's ramp, limited to the severities the
// Display Options filter is actually drawing.
function modelLegendHtml(m) {
  const entries = SEV_KEYS.filter((k) => visibleSeverities.has(k)).map((k) => ({
    swatch: colorSwatch(
      `background:${paletteColor(modelRamp[m], k)};border:1px solid rgb(255 255 255 / .25)`),
    label: SEVERITY[k].label,
  }));
  // Concurrence repeats in every model tile — it describes a set of models, not
  // any one of them, and a reader looking at one tile shouldn't have to hunt
  // through the others to learn what the hash means. It goes when the highlight
  // is switched off in Display Options, and also when only one model is on,
  // since one model cannot concur with anything.
  if (view.hatchOn && PANEL_MODELS.filter((x) => visibleModels.has(x)).length > 1) {
    entries.push({swatch: hashSwatch(), label: "Models concur"});
  }
  return tileLegend(entries);
}

// The flash overlay's two tiers, drawn as they appear on the map (dashed outline
// for the lower tier), limited to the tiers being shown.
function flashLegendHtml() {
  return tileLegend(FLASH_TIERS.filter((t) => visibleTiers.has(t)).map((t) => ({
    swatch: colorSwatch(`background:${flashFill(t)};` +
      `border:1.5px ${FLASH_TIER[t].dashed ? "dashed" : "solid"} ${flashOutline(t)}`),
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
    `${icon("chevron-down", "text-[13px] transition-transform")}</button>`;

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
      // at points, so the river between two warned gauges would otherwise be blank;
      // the backend walks HydroRIVERS between them and carries the upstream gauge's
      // forecast down the span. Every row below therefore describes that gauge, not
      // this hexagon — a cell at the far end of a 200-cell span is a long way from
      // it — so the id and the "Near" row are relabelled to say whose numbers these
      // are, and the tile carries a line explaining why there is no gauge here.
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
          `No gauge in this ${view.dataset.unit.toLowerCase()}. It sits on the river ` +
          `between this gauge and the next warned one downstream, and carries the ` +
          `upstream forecast.</p>`
        : "";
      return `<dl class="grid grid-cols-[128px_1fr] gap-x-2.5 gap-y-1 text-[12.5px]${i ? " mt-2 pt-2 border-t border-slate-700/50" : ""}">${rows}</dl>${note}`;
    }).join("");
    return `<div id="tile-river-${m}" class="bg-[#1b2a3a] border border-slate-700 border-l-4 rounded-[10px] px-3.5 py-3 mb-3${hide}" style="border-left-color:${sevColor(worstSeverity(fcs), m)}">` +
      head + modelLegendHtml(m) + `<div id="${bodyId}">` + body + `</div></div>`;
  }

  // Forecast (river) tiles: always list PANEL_MODELS; the River Floods filter
  // hides the ones switched off (see the filter wiring below).
  const tiles = PANEL_MODELS.map(modelTile).join("");

  // Section heading (the largest text in the panel) with a collapse chevron.
  const sectionHeader = (title, target, iconName) =>
    `<h2 class="flex items-center justify-between text-slate-900 font-bold text-[19px] mb-2.5">` +
    `<span class="flex items-center gap-1.5">${iconName ? icon(iconName, "text-slate-500 text-sm") : ""}${title}</span>` +
    collapseBtn(target) + `</h2>`;

  // "All models"-style multi-select filter, shared by both sections.
  // The sentinel the "All" row carries in place of a model key.
  const ALL = "__all";
  const filterLabel = (models, set) => {
    const n = models.filter((m) => set.has(m)).length;
    return n === models.length ? "All models" : n === 0 ? "No models" : `${n} of ${models.length} models`;
  };
  const filterDropdown = (kind, models, set) =>
    `<div class="filter-dd relative mb-3" data-kind="${kind}">` +
    `<button type="button" class="dd-btn w-full flex items-center justify-between gap-2 px-3 py-2 rounded-lg border border-slate-300 bg-white text-[13px] font-medium text-slate-700 hover:bg-slate-50">` +
    `<span class="flex items-center gap-1.5">${icon("funnel", "text-sky-500")}<span class="dd-label">${filterLabel(models, set)}</span></span>` +
    `<span class="dd-caret inline-flex transition-transform"${openFilters.has(kind) ? ` style="transform:rotate(180deg)"` : ""}>${icon("chevron-down", "text-slate-400")}</span></button>` +
    `<div class="dd-menu ${openFilters.has(kind) ? "" : "hidden "}absolute z-[1000] left-0 right-0 mt-1 rounded-lg border border-slate-200 bg-white shadow-lg p-1.5">` +
    // "All" sits above the rule and carries no model of its own: it reads the
    // others and writes to all of them. Indeterminate when the set is mixed, so
    // the box shows three states rather than lying about a partial selection.
    `<label class="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-slate-50 text-[13px] font-medium text-slate-700 cursor-pointer select-none">` +
    `<input type="checkbox" data-model="${ALL}"${models.every((m) => set.has(m)) ? " checked" : ""} class="accent-sky-500 w-3.5 h-3.5">All</label>` +
    `<div class="h-px bg-slate-200 my-1"></div>` +
    models.map((m) =>
      `<label class="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-slate-50 text-[13px] text-slate-700 cursor-pointer select-none">` +
      `<input type="checkbox" data-model="${m}"${set.has(m) ? " checked" : ""} class="accent-sky-500 w-3.5 h-3.5">${modelLabel(m)}</label>`
    ).join("") + `</div></div>`;

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
      head + flashLegendHtml() + `<div id="${bodyId}">` + body + `</div></div>`;
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
      `<div id="sec-impact"><div class="grid grid-cols-2 gap-2">` +
      impTile("building-office-2", "Buildings", fmtCount(imp.buildings)) +
      impTile("area-hash-filled", "Farmland", fmtCount(imp.farmland_m2 / 1e6) + " km²") +
      impTile("car-travel-mode", "Roads", fmtCount(imp.highway_km) + " km") +
      impTile("train-travel-mode", "Railways", fmtCount(imp.railway_km) + " km") +
      impTile("users", "Population", fmtCount(imp.population), true) +
      `</div><p class="text-slate-400 text-[12px] mt-1.5">Totals across the whole ${unitLabel(props).toLowerCase()}.</p></div></div>`
    : "";

  // The risk index: the one score, then the three categories that produced it.
  // Shown decomposed rather than as a bare number — the score folds a near-constant
  // (concurrence varies in 4% of cells), a three-level ladder and a continuous
  // exposure blend into one value, so two cells can tie for completely different
  // reasons and only the parts say which. The bars are the parts.
  const wri = selected ? props.wri : null;
  const wriBar = (label, v, note) => {
    const pct = Math.max(0, Math.min(100, Math.round(100 * (v == null ? 0 : v))));
    return `<div class="mb-2">` +
      `<div class="flex items-baseline justify-between text-[11px] mb-1">` +
      `<span class="text-slate-500 font-medium">${label}</span>` +
      `<span class="text-slate-700 font-semibold tabular-nums">${v == null ? "—" : v.toFixed(2)}</span></div>` +
      `<div class="h-1.5 rounded-full bg-slate-200 overflow-hidden">` +
      `<div class="h-full rounded-full bg-sky-500" style="width:${pct}%"></div></div>` +
      (note ? `<div class="text-[10.5px] text-slate-400 mt-0.5">${note}</div>` : "") +
      `</div>`;
  };
  const wriHtml = wri
    ? `<div class="mt-4">` +
      `<h3 class="flex items-center justify-between text-slate-800 font-semibold text-[11px] uppercase tracking-wider mb-2">` +
      `<span class="flex items-center gap-1.5">${icon("chart-bar", "text-sky-500 text-sm")}Risk index</span>${collapseBtn("sec-wri")}</h3>` +
      `<div id="sec-wri">` +
      `<div class="flex items-baseline gap-1.5 mb-3">` +
      `<span class="text-slate-900 font-bold text-[26px] leading-none tabular-nums">${wri.score.toFixed(2)}</span>` +
      `<span class="text-slate-400 text-[11px]">of 1.00</span></div>` +
      wriBar("Severity", wri.severity,
        `${SEVERITY[props.severity] ? SEVERITY[props.severity].label : "—"} — sets the band this ${view.dataset.unit.toLowerCase()} can score in`) +
      wriBar("Concurrence", wri.concurrence,
        props.model_count > 1
          ? `${props.model_count} models share a base ${view.dataset.unit.toLowerCase()} here`
          : "one model — a flash footprint also counts") +
      wriBar("Impact", wri.impact, "population, buildings, roads, farmland, rail") +
      `<p class="text-slate-400 text-[12px] mt-1.5">Severity picks the band; concurrence and impact ` +
      `position the ${view.dataset.unit.toLowerCase()} inside it, and can never lift it past the band above.</p>` +
      `</div></div>`
    : "";

  // The heat legend, shown only while the surface is drawn. It is not a severity
  // key: the heat is the risk index, which folds severity, concurrence and impact
  // together, so labelling it with the three rungs would be a different claim than
  // the map is making. The model tiles below keep their own keys for the hexagon a
  // click brings back.
  const heatLegendHtml = view.heatOn
    ? `<div class="mb-3 rounded-[10px] border border-slate-200 bg-slate-50 px-3 py-2.5">` +
      `<div class="text-[10.5px] font-semibold uppercase tracking-wide text-slate-500 mb-1.5">` +
      `Heat map — risk index</div>` +
      `<div class="h-2.5 rounded-full border border-slate-200" style="background:linear-gradient(to right,` +
      SEV_KEYS.map((k, i) =>
        `${paletteColor(view.heatRampId, k)} ${15 + i * 42}%`).join(",") + `)"></div>` +
      `<div class="flex justify-between text-[10px] text-slate-400 mt-1">` +
      `<span>lower</span><span>higher</span></div>` +
      `<p class="text-[11px] text-slate-400 mt-1.5 leading-snug">` +
      `Severity, model agreement and impact in one value. ` +
      `${view.dataset.unit}s are still underneath — click one to see its score broken down.</p></div>`
    : "";

  // Section 1 — River Floods: the concurrence key, the All-models filter, the
  // forecast tiles and impact, all collapsible under one heading. The tiles name
  // the selected cell themselves, so the section carries no header of its own.
  const riverSection =
    `<div id="section-river" class="mb-4">` + sectionHeader("River Floods", "sec-river") +
    `<div id="sec-river">` + heatLegendHtml +
    filterDropdown("river", PANEL_MODELS, visibleModels) +
    tiles + impactHtml + wriHtml + `</div></div>`;

  // Section 2 — Flash Floods: a global overlay. Its filter reveals a tile per
  // enabled model (and turns that model's polygons on the map).
  const flashKeys = FLASH_MODELS.map((fm) => fm.key);
  const flashSection =
    `<div id="section-flash" class="mt-12">` + sectionHeader("Flash Floods", "sec-flash") +
    `<div id="sec-flash">` + filterDropdown("flash", flashKeys, visibleFlashModels) +
    FLASH_MODELS.map(flashModelTile).join("") + `</div></div>`;

  panelContent.innerHTML = riverSection + flashSection;

  // Collapse arrows: toggle the target section body and rotate the chevron.
  panelContent.querySelectorAll(".collapse-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const body = document.getElementById(btn.dataset.target);
      if (!body) return;
      const collapsed = body.classList.toggle("hidden");
      const svg = btn.querySelector("svg");
      if (svg) svg.classList.toggle("-rotate-90", collapsed);
    });
  });

  // Filter dropdowns: open/close, and per-model toggle of the matching tile
  // (and, for flash, its map polygons). Outside-click close is handled globally.
  const flashSetter = {flood_hub: (on) => actions.setFlashOn(on)};
  panelContent.querySelectorAll(".filter-dd").forEach((dd) => {
    const isFlash = dd.dataset.kind === "flash";
    const set = isFlash ? visibleFlashModels : visibleModels;
    const models = isFlash ? flashKeys : PANEL_MODELS;
    const menu = dd.querySelector(".dd-menu");
    const caret = dd.querySelector(".dd-caret");
    const label = dd.querySelector(".dd-label");
    dd.querySelector(".dd-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      // classList.toggle returns true when the class was ADDED, i.e. when the
      // menu just became hidden — so read the state first and say what we mean.
      const opening = menu.classList.contains("hidden");
      menu.classList.toggle("hidden", !opening);
      caret.style.transform = opening ? "rotate(180deg)" : "";
      if (opening) openFilters.add(dd.dataset.kind);
      else openFilters.delete(dd.dataset.kind);
    });
    const allBox = dd.querySelector(`input[data-model="${ALL}"]`);
    const boxes = [...dd.querySelectorAll("input[type=checkbox]")]
      .filter((cb) => cb.dataset.model !== ALL);

    // Applies ONE model's state everywhere it shows: the set, its panel tile and,
    // for flash, its map layers. Shared so the "All" row and a single checkbox
    // cannot diverge in what toggling means.
    const applyModel = (m, on) => {
      if (on) set.add(m); else set.delete(m);
      const card = document.getElementById(`tile-${dd.dataset.kind}-${m}`);
      if (card) card.classList.toggle("hidden", !on);
      if (isFlash) (flashSetter[m] || (() => {}))(on);
    };
    const syncAll = () => {
      if (!allBox) return;
      const n = models.filter((m) => set.has(m)).length;
      allBox.checked = n === models.length;
      allBox.indeterminate = n > 0 && n < models.length;
    };
    syncAll();

    if (allBox) allBox.addEventListener("change", () => {
      const on = allBox.checked;
      for (const m of models) applyModel(m, on);
      for (const cb of boxes) cb.checked = on;
      allBox.indeterminate = false;
      label.textContent = filterLabel(models, set);
      if (!isFlash) actions.refreshFeatures();
    });

    boxes.forEach((cb) => {
      cb.addEventListener("change", () => {
        applyModel(cb.dataset.model, cb.checked);
        syncAll();
        label.textContent = filterLabel(models, set);
        if (!isFlash) actions.refreshFeatures();
      });
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

// One document-level handler closes any open filter-dropdown menu on an
// outside click (the per-menu wiring in renderPanel only handles open/toggle).
document.addEventListener("click", (e) => {
  document.querySelectorAll(".filter-dd").forEach((dd) => {
    if (dd.contains(e.target)) return;
    const menu = dd.querySelector(".dd-menu");
    const caret = dd.querySelector(".dd-caret");
    if (menu) menu.classList.add("hidden");
    if (caret) caret.style.transform = "";
    // Forget it too, or the next re-render would bring it back open.
    openFilters.delete(dd.dataset.kind);
  });
});

export function panelError(err) {
  panelContent.hidden = true;
  panelEmpty.hidden = false;
  document.getElementById("panel-empty").innerHTML =
    "<h2 class=\"text-slate-800 font-semibold text-[15px] mb-1\">Couldn't load forecast data</h2>" +
    "<p class=\"text-sm leading-relaxed max-w-[240px]\">" +
    String(err && err.message ? err.message : err) +
    "</p>";
}
