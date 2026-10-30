// The right-hand side panel: legends, forecast tiles, impact.
// Renders only; the actions its controls fire are injected by main.js, which keeps
// the panel free of imports from the layers that call it.
import {CONCUR_ANY_LABEL, CONCUR_COUNTS, CONCUR_LABEL, FIELD_LABELS, FLASH_MODELS, FLASH_TIER, FLASH_TIERS, MODEL_HOME, PANEL_MODELS, RIVER_ID_LABEL, SEVERITY, SEV_KEYS} from "./config.js";
import {concurCount, fillSource, flashChanceLabel, flashNearLabel, fmtCount, fmtFlashIssued, fmtValue, inkOn, modelLabel, modelLink, nearLabel, worstSeverity} from "./format.js";
import {flashFill, flashOutline, paletteColor, rampFor, sevColor, unitLabel, view, visibleFlashModels, visibleModels, visibleSeverities, visibleTiers} from "./settings.js";
import {icon} from "./icons.js";
// Safe to import: cells_tiles never imports the panel back — it reaches the panel
// only through the callbacks main.js injects into its init().
import {agreementTally, releaseModelStatus} from "./layers/cells_tiles.js";

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
  // The same 58px label column plus 8px gap the Model Agreement block reserves on
  // its right, left empty here. Nothing sits in it, and that is the point: the
  // tiles' bars and the agreement bars are read as one stack down the panel, and
  // bars of two different lengths read as two different scales.
  const scale =
    `<div class="flex justify-between text-[10px] font-semibold text-slate-400 mt-1 pr-[66px]">` +
    keys.map((k) => `<span>${SEVERITY[k].label}</span>`).join("") + `</div>`;
  // Concurrence used to add a second bar HERE, in every model tile. It described a
  // set of models rather than any one of them, so each of the three tiles carried
  // the same two extra rows saying the same thing — the legend repeated three
  // times under three different model names, each implying it was about that
  // model. It is one block under the tiles now; see concurLegendHtml below.
  return `<div class="mb-2">` +
    `<div class="pr-[66px]">` + bar(rampFor(m)) + `</div>` + scale + `</div>`;
}

const colorSwatch = (style) =>
  `<span class="inline-block w-3 h-3 rounded-sm shrink-0" style="${style}"></span>`;

// One model's severity key, in that model's own ramp — the same ramp its hexagons
// and its heat surface are painted in — limited to the severities the Filters menu
// is actually drawing.
/**
 * Is a model's OWN ramp capable of appearing on the map right now?
 *
 * No, when concurrence is colored separately AND the agreement filter is on: every
 * area that survives the filter clears the floor, so every area is painted from the
 * concurrence ramp and the per-model ramps are never reached. A key for a color the
 * map cannot draw is worse than no key -- it invites the reader to go looking for
 * orange that is not there.
 *
 * This is the case that produced it: the Everything view, which gives each model a
 * color of its own, then Filters set to "2 models or more agree".
 */
function modelRampVisible() {
  return !(view.concurHeat && view.concurMin >= CONCUR_COUNTS[0]);
}

function modelLegendHtml(m) {
  if (!modelRampVisible()) return "";
  // Under the heat map the map is a blurred field rather than a set of rungs, so
  // the key is a gradient rather than three chips. Same ramp either way.
  if (view.heatOn) return heatKeyHtml(m);
  const entries = SEV_KEYS.filter((k) => visibleSeverities.has(k)).map((k) => ({
    swatch: colorSwatch(
      `background:${paletteColor(rampFor(m), k)};border:1px solid rgb(255 255 255 / .25)`),
    label: SEVERITY[k].label,
  }));
  // Concurrence is NOT added here either — same reason as the heat key above. It
  // belongs to the set of models, not to the tile it happens to be sitting in.
  return tileLegend(entries);
}

/**
 * Where models agree, as one block on the panel rather than a repeat in every tile.
 *
 * Shown only when concurrence has a hue of its own: with the counts folded back
 * into the severity surface there is no second color to explain, and a legend for
 * a distinction the map is not drawing is worse than no legend. Collapses to a
 * single "2+ models" row while the counts share a ramp, for the same reason — two
 * identical bars under two different labels would be inventing a difference.
 *
 * It follows the severity filter like every other key here: hide Warning and the
 * bars lose their lightest end, so the legend never promises a color the map has
 * stopped drawing. Light-panel styling, not the tiles' dark one, because it now
 * sits on the panel itself.
 */
function concurLegendHtml() {
  if (!view.concurHeat) return "";
  const keys = SEV_KEYS.filter((k) => visibleSeverities.has(k));
  if (!keys.length) return "";
  // Only the counts that can actually reach the map: at filter level 3 a "2 models"
  // row would be a key to cells that are not drawn. This is what the old collapse
  // got wrong -- it labelled the single row "2+ models" at every level, including
  // the one where nothing below three exists.
  // Only the counts that can actually reach the map: at filter level 3 a "2 models"
  // row would be a key to cells that are not drawn.
  //
  // And only the counts something is actually drawn in. A color key for an
  // agreement level that no area reaches today is the same lie as a key for a ramp
  // the filter has switched off -- the reader goes hunting for a purple that is not
  // there. The banner above says why it is missing, so the row can simply go.
  const {drawn} = agreementTally();
  const counts = CONCUR_COUNTS.filter(
    (n) => (!view.concurMin || n >= view.concurMin) && drawn[n] > 0);
  if (!counts.length) return "";

  // Those counts, collapsed to ONE row when they share a ramp: two identical bars
  // under two different labels would claim a distinction the map is not drawing.
  // The collapsed row reads "2+ models" because it covers a range -- but only when
  // a range is what survived. A single remaining count keeps its own number.
  const shared = counts.every((n) => view.concurRamp[n] === view.concurRamp[counts[0]]);
  const rows = shared
    ? [{ramp: view.concurRamp[counts[0]],
        label: counts.length > 1 ? CONCUR_ANY_LABEL : CONCUR_LABEL[counts[0]]}]
    : counts.map((n) => ({ramp: view.concurRamp[n], label: CONCUR_LABEL[n]}));

  let rowsHtml;
  if (view.heatOn) {
    // Same 15%..99% run as the tiles' heat key, so a bar here and a bar up there
    // are read the same way.
    const step = keys.length > 1 ? 84 / (keys.length - 1) : 0;
    const bar = (rampId) => {
      const stops = keys.map((k, i) =>
        `${paletteColor(rampId, k)} ${keys.length > 1 ? 15 + i * step : 50}%`).join(",");
      return `<div class="h-2 rounded-full border border-slate-300/60" ` +
        `style="background:linear-gradient(to right,${stops})"></div>`;
    };
    rowsHtml = rows.map(({ramp, label}) =>
      `<div class="flex items-center gap-2 mt-1.5">` +
      `<div class="flex-1">${bar(ramp)}</div>` +
      `<span class="text-[10px] font-semibold text-slate-500 shrink-0 w-[58px] text-right">` +
      `${label}</span></div>`).join("") +
      // One scale under the stack: every bar spans the same rungs, so repeating the
      // rung names per bar would say the same thing two or three times over.
      `<div class="flex justify-between text-[10px] font-semibold text-slate-600 mt-1 pr-[66px]">` +
      keys.map((k) => `<span>${SEVERITY[k].label}</span>`).join("") + `</div>`;
  } else {
    // Cells view: one chip per count, at the worst rung still being drawn — the end
    // of the ramp a reader is actually looking at.
    const worst = keys[keys.length - 1];
    rowsHtml = `<div class="flex flex-wrap gap-x-3 gap-y-1 mt-1.5">` +
      rows.map(({ramp, label}) =>
        `<span class="flex items-center gap-1.5 text-[10.5px] font-semibold text-slate-500">` +
        colorSwatch(`background:${paletteColor(ramp, worst)};` +
          `border:1px solid rgb(148 163 184 / .5)`) + `${label}</span>`).join("") +
      `</div>`;
  }

  // When the agreement filter is on this block is the ONLY color key the map has,
  // because the models' own ramps are no longer drawn. Saying so here is cheaper
  // than leaving a reader to wonder why three tiles lost their color bars.
  const note = modelRampVisible() ? "" :
    `<div class="text-[10px] leading-snug text-slate-600 mt-1.5">` +
    `Model colors aren't drawn while the agreement filter is on.</div>`;
  return `<div class="mb-3 rounded-[10px] border border-slate-200 bg-slate-50 px-3 py-2">` +
    `<div class="text-[10px] uppercase tracking-[.04em] text-slate-500 font-semibold">` +
    `Model Agreement</div>` + rowsHtml + note + `</div>`;
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

  // Readable names for the collapse buttons, keyed by the body id they fold.
  const SECTION_NAMES = {
    "sec-river": "River Floods", "sec-flash": "Flash Floods", "sec-impact": "Impact",
  };
  const sectionName = (target) =>
    SECTION_NAMES[target] ||
    (target.startsWith("sec-model-")
      ? `${modelLabel(target.slice("sec-model-".length))} details`
      : "section");

  // A chevron that collapses the section body with matching id (wired after render).
  // Every one of these used to be called "Collapse section" -- six at once, all
  // identical, and still saying "Collapse" when the section was already folded.
  // aria-expanded is the part that actually carries the state; aria-controls ties
  // the button to the body it folds.
  const collapseBtn = (target, cls = "text-slate-500 hover:text-slate-700") =>
    `<button type="button" class="collapse-btn shrink-0 ${cls}" data-target="${target}" ` +
    `aria-controls="${target}" aria-expanded="${!collapsed.has(target)}" ` +
    `aria-label="${collapsed.has(target) ? "Expand" : "Collapse"} ${sectionName(target)}">` +
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
  /**
   * The color the SELECTED cell is actually painted in, or null when the tiles'
   * own legends are showing and can answer for themselves.
   *
   * The left accent on a tile is the only colored thing left once the agreement
   * filter hides the model legends, so it is what a reader matches against the
   * hexagon they just clicked. Drawn from the model's ramp it was answering a
   * different question: the map had painted that cell from the CONCURRENCE ramp,
   * so the tile showed blue beside a purple cell.
   *
   * Severity is the cell's worst across every visible forecast, not the tile's own
   * -- that is what the map's feature state carries, so it is what the cell is.
   */
  function cellPaintColor() {
    if (modelRampVisible() || !selected) return null;
    const fcs = (props.forecasts || []).filter(
      (f) => visibleModels.has(f.model) && visibleSeverities.has(f.severity));
    if (!fcs.length) return null;
    const n = concurCount(props, visibleModels);
    // The highest count the cell qualifies for, matching the paint expression,
    // which tests the counts from the top down.
    const level = [...CONCUR_COUNTS].reverse().find((c) => n >= c);
    if (!level) return null;
    return paletteColor(view.concurRamp[level], worstSeverity(fcs));
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
        `<div class="text-slate-400 text-[12px]">On standby — ${selected ? "no forecast for this area" : `click a ${view.dataset.unit.toLowerCase()} on the map`}.</div></div>`;
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
    const accent = cellPaintColor() || sevColor(worstSeverity(fcs), m);
    return `<div id="tile-river-${m}" class="bg-[#1b2a3a] border border-slate-700 border-l-4 rounded-[10px] px-3.5 py-3 mb-3${hide}" style="border-left-color:${accent}">` +
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
  // One shape for every filter notice in the panel, so four of them cannot drift
  // apart on padding, icon size or type scale.
  //
  // `info` is slate: the reader set this filter, so the box is reporting a state,
  // not raising an alarm. `warn` is amber and is reserved for news about the DATA
  // -- the one case the reader did not cause and cannot fix by changing a setting.
  //
  // Bodies are deliberately short. The title already names the filter and the
  // reader set it moments ago, so "Change it under Filters -> X" was telling them
  // something they knew; it is kept only where nothing is drawn at all and the way
  // back is genuinely less obvious.
  const NOTICE = {
    info: ["border-slate-300 bg-slate-50", "text-slate-500", "text-slate-800", "text-slate-600"],
    warn: ["border-amber-300 bg-amber-50", "text-amber-600", "text-amber-900", "text-amber-800"],
  };
  const notice = (tone, title, bodyText) => {
    const [box, ink, head_, body_] = NOTICE[tone] || NOTICE.info;
    return `<div class="mb-3 rounded-[10px] border ${box} px-3 py-2">` +
      `<div class="flex items-start gap-1.5">` +
      icon("funnel", `${ink} text-[13px] mt-[1px]`) +
      `<div><div class="text-[12px] font-semibold ${head_}">${title}</div>` +
      `<div class="text-[11px] leading-snug ${body_} mt-0.5">${bodyText}</div>` +
      `</div></div></div>`;
  };

  // Named per section: with every river model off, two identical copies of this
  // used to stack up, one in each section, with nothing saying which was which.
  const noneShown = (models, set, what) => !models.some((m) => set.has(m)) ?
    `<div class="rounded-[10px] border border-dashed border-slate-700 px-3.5 py-3 mb-3 ` +
    `text-slate-500 text-[12px] leading-snug">${what} Choose under ` +
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
      body = `<div class="text-slate-400 text-[12px]">On standby — click a flash flood area on the map.</div>`;
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
    `<div class="flex items-center gap-1 text-slate-600 text-[10px] font-semibold uppercase tracking-wide mb-1">${icon(name, "text-[12px]")}${label}</div>` +
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
      `</div><p class="text-slate-500 text-[12px] mt-1.5">Totals across the whole ${unitLabel(props).toLowerCase()}.</p></div></div>`
    : "";

  // The concurrence filter hides about 96% of the flagged cells. Unannounced that
  // reads as a quiet day rather than as a filter, so it says so where the hidden
  // forecasts would have been, and names the control that turns it off. Amber
  // rather than slate: a state to notice, not a label. It no longer fires at boot —
  // the filter opens off — but it is the same strong effect whenever it is on.
  // The banner names the LEVEL, because "concurrence only" read the same at 2 and
  // at 3 while the two hide very different amounts of the map.
  // A filter that hides most of the map has to announce itself, or an emptied
  // panel reads as a quiet day. The severity cut is the other one that can do it:
  // "Extreme only" takes away roughly nine tenths of the forecasts, and until now
  // said nothing about it anywhere.
  // Agreement levels that are being asked for but which nothing reaches today.
  // Without this the legend row simply vanishes, and someone who came looking for
  // concurrence reads that as the feature being broken rather than as the day
  // being quiet. Say which it is.
  const tally = agreementTally();
  const askedFor = CONCUR_COUNTS.filter(
    (n) => (view.concurHeat || view.concurMin) && (!view.concurMin || n >= view.concurMin));
  // `today`, not `drawn`: if the agreement exists and the reader has filtered it
  // away, that is the severity or model filter's news to break, not this banner's.
  // Claiming "no agreement today" over a day that has four three-model cells is
  // simply false, and it sends someone looking for a bug that is not there.
  const emptyLevels = askedFor.filter((n) => !tally.today[n]);
  // The ONLY amber one. Everything else here restates a choice the reader just
  // made, which is a status, not a warning; this is the one that is news about the
  // data. Five identical amber boxes made that distinction invisible and made an
  // ordinary filtered view look like something had gone wrong.
  const noAgreementHtml = emptyLevels.length
    ? notice("warn",
        `No ${emptyLevels.map((n) => `${n}-model`).join(" or ")} agreement`,
        "Nothing overlaps today. The models are reporting normally.")
    : "";

  const hiddenSevs = SEV_KEYS.filter((k) => !visibleSeverities.has(k));
  const shownSevs = SEV_KEYS.filter((k) => visibleSeverities.has(k));
  const sevNoticeHtml = hiddenSevs.length
    ? (shownSevs.length
        ? notice("info",
            shownSevs.length === 1 ? `${SEVERITY[shownSevs[0]].label} only` : "Severity filtered",
            `${hiddenSevs.map((k) => SEVERITY[k].label).join(" and ")} hidden.`)
        // Nothing can be drawn at all, so this one keeps the pointer: the way out
        // is less obvious than for a filter that still shows something.
        : notice("info", "No severity shown",
            "All three rungs are off — nothing can be drawn. Filters → Severity."))
    : "";

  // #3: agreement is a fact about the FORECASTS, so a cell still qualifies when
  // the model corroborating it is switched off. Deliberate, documented, and
  // baffling on screen -- single-model cells apparently surviving a filter that
  // says it removes them. Said out loud only when it can actually bite.
  const hiddenModels = PANEL_MODELS.filter((m) => !visibleModels.has(m));
  const partnerNote = hiddenModels.length ? " Agreement counts models you've hidden." : "";
  const concurNoticeHtml = view.concurMin && tally.today[view.concurMin]
    ? notice("info",
        view.concurMin >= PANEL_MODELS.length
          ? `All ${PANEL_MODELS.length} models agree`
          : `${view.concurMin}+ models agree`,
        (view.concurMin >= PANEL_MODELS.length
          ? "Areas fewer models saw are hidden."
          : "Areas a single model saw alone are hidden.") + partnerNote)
    : "";

  // #1: a model the pipeline dropped from this release. The app knew and never
  // said so once the header stamp went; the tile just sat on standby.
  const status = releaseModelStatus();
  const missing = (m) => ["dropped", "skipped"].includes(status[m]);
  const missingRiver = PANEL_MODELS.filter((m) => missing(m) && visibleModels.has(m));
  const missingHtml = missingRiver.length
    ? notice("warn",
        `${missingRiver.map(modelLabel).join(" and ")} not in this release`,
        `The run could not use ${missingRiver.length > 1 ? "them" : "it"} on this day. ` +
        `Other models are unaffected.`)
    : "";

  // #2: each filter is doing what it says and together they leave nothing. No
  // single notice owns that, and an empty map reads as a broken app. Replaces the
  // others rather than joining them -- the reasons are listed in its own body.
  const activeFilters = [];
  if (view.concurMin) {
    activeFilters.push(view.concurMin >= PANEL_MODELS.length
      ? `all ${PANEL_MODELS.length} models must agree`
      : `${view.concurMin}+ models must agree`);
  }
  if (hiddenSevs.length) {
    activeFilters.push(`${hiddenSevs.map((k) => SEVERITY[k].label).join(" and ")} hidden`);
  }
  if (hiddenModels.length) {
    activeFilters.push(`${hiddenModels.map(modelLabel).join(" and ")} hidden`);
  }
  // Only when something IS selected: with no models ticked the dashed box below
  // says it better, and with no severities the severity notice names the way out.
  const nothingHtml = (visibleModels.size && shownSevs.length && !tally.drawnTotal)
    ? notice("info", "Nothing to show",
        `Your filters leave no areas on the map` +
        (activeFilters.length ? ` — ${activeFilters.join("; ")}.` : "."))
    : "";

  // Section 1 — River Floods: the concurrence notice, the forecast tiles and
  // impact, all collapsible under one heading. The tiles name the selected cell
  // themselves, so the section carries no header of its own. A model switched off
  // in Filters drops its tile here, because the tile markup reads the same set.
  const riverSection =
    `<div id="section-river" class="mb-4">` + sectionHeader("River Floods", "sec-river") +
    bodyTag("sec-river") + missingHtml +
    (nothingHtml || noAgreementHtml + concurNoticeHtml + sevNoticeHtml) +
    noneShown(PANEL_MODELS, visibleModels, "No river models selected.") +
    concurLegendHtml() + tiles +
    impactHtml + `</div></div>`;

  // Section 2 — Flash Floods: a global overlay, one tile per enabled model.
  const flashKeys = FLASH_MODELS.map((fm) => fm.key);
  // The flash tiers are filtered exactly like the river severities, so they get
  // the same treatment: a tier switched off takes polygons off the map, and an
  // overlay that looks thinner than it is reads as missing data. The overlay being
  // switched off entirely is already stated by noneShown below.
  // The flash counterpart of missingHtml: 2026-10-29 dropped flash entirely, and
  // with the overlay switched on the panel showed a legend and standby text over a
  // map with no polygons on it.
  const flashDroppedHtml = (visibleFlashModels.size && missing("flash"))
    ? notice("warn", "Flash floods not in this release",
        "The run could not use them on this day.")
    : "";

  const hiddenTiers = FLASH_TIERS.filter((t) => !visibleTiers.has(t));
  const shownTiers = FLASH_TIERS.filter((t) => visibleTiers.has(t));
  const tierNoticeHtml = (visibleFlashModels.size && hiddenTiers.length)
    ? (shownTiers.length
        ? notice("info",
            `${FLASH_TIER[shownTiers[0]].label} only`,
            `${hiddenTiers.map((t) => FLASH_TIER[t].label).join(" and ")} hidden.`)
        : notice("info", "No tiers shown",
            "Both tiers are off — nothing can be drawn. Filters → Flash floods."))
    : "";
  const flashSection =
    `<div id="section-flash" class="mt-12">` + sectionHeader("Flash Floods", "sec-flash") +
    bodyTag("sec-flash") + flashDroppedHtml + tierNoticeHtml +
    noneShown(flashKeys, visibleFlashModels, "Flash overlay is off.") +
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
      // The name and the state have to follow the click, or the button keeps
      // announcing the action it performed last rather than the one it offers.
      btn.setAttribute("aria-expanded", folded ? "false" : "true");
      btn.setAttribute("aria-label",
        `${folded ? "Expand" : "Collapse"} ${sectionName(btn.dataset.target)}`);
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
