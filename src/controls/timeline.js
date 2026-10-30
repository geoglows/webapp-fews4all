// The retrospective timeline: browse past releases, or play them as a timelapse.
//
// WHY THIS IS CHEAP
//
// The geometry never changes. cells.pmtiles holds the same 4.4M hexagons every
// day, so moving between releases is not a reload — it is clearing feature-state,
// rebuilding four filters and re-applying. That is the same work a model
// checkbox already does. The only real cost is fetching and parsing ~6 MB of CSV
// per day, which is why release.js caches parsed releases and why this prefetches
// ahead of playback.
//
// COLLAPSED BY DEFAULT
//
// A map is for looking at. The bar shows only the date until it is asked for, so
// the thing most users want most of the time — today's warnings — is not sharing
// the screen with a control they will touch once.
//
// PARTIAL DAYS ARE MARKED, NOT HIDDEN
//
// 21 and 22 October carry GEOGLOWS alone; the other models were stale and were
// correctly dropped. Played back unmarked, those days show warnings halving and
// recovering, which reads as weather and is not. Each day's tick is therefore
// drawn from its run record, and a day missing a model is amber with the models
// named on hover. The days are still selectable and still play: hiding them
// would be its own kind of lie.

import {icon} from "../icons.js";

// Both states sit mb-6 above the map's bottom edge rather than flush with it.
// MapLibre draws its attribution along that edge, and the Esri and OSM tiles are
// used on the condition it stays legible — a control covering it is not a
// cosmetic problem.
const PLAY_MS = 900;          // one day per step; slow enough to read the map
const PREFETCH = 2;           // days to pull ahead of the cursor during playback

let releases = [];            // [{release, models, missing, cells, agreement}]
let idx = 0;                  // which one is showing
let open = false;
let timer = null;
let host = null;
let actions = {show: () => {}, prefetch: () => {}};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/* "2026-10-23" -> "23 Oct 2026". Parsed by hand rather than through Date(),
 * which reads a bare ISO date as UTC midnight and shows the day before to anyone
 * west of Greenwich. */
function full(date) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date || "");
  return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}` : String(date || "");
}

const current = () => releases[idx] || null;

/* A day is "partial" when the pipeline published it without a model. The run
 * record already decided this; nothing is re-derived here. */
const isPartial = (r) => Array.isArray(r.missing) && r.missing.length > 0;

/* Models the run marked `thin`: present, but carrying far fewer forecasts than
 * their own recent median — the volume guard's way of saying a fetch may have
 * been half a fetch.
 *
 * This is marked here because it stopped being said anywhere else. The header
 * used to carry three states (missing, thin, all clear) beside the date; with the
 * header's stamp gone the timeline is the only thing left reporting on a release,
 * and a quarter-sized GEOGLOWS looked exactly like a quiet day. The status is
 * already in releases.json, so this reads it rather than deriving anything. */
const thinModels = (r) =>
  Object.keys(r.models || {}).filter((m) => r.models[m] === "thin").sort();

/* Either kind of trouble. One amber for both: the question a mark answers is
 * "should I trust this day's totals", and missing and thin are both no. Which it
 * is, is what the note and the hover say. */
const isFlagged = (r) => isPartial(r) || thinModels(r).length > 0;

/* The short form, for the 150px block beside the date. Both halves when a day
 * manages both, truncated with the full text on hover. */
function noteText(r) {
  const bits = [];
  if (isPartial(r)) bits.push(`without ${r.missing.join(", ")}`);
  const thin = thinModels(r);
  if (thin.length) bits.push(`${thin.join(", ")} thin`);
  return bits.join(" · ");
}

function tickTitle(r) {
  const cells = (r.cells || {})["6"];
  const bits = [full(r.release)];
  if (typeof cells === "number") bits.push(`${cells.toLocaleString()} cells`);
  const note = noteText(r);
  if (note) bits.push(note);
  return bits.join(" · ");
}

// ---- rendering --------------------------------------------------------------

function render() {
  if (!host) return;
  const r = current();
  if (!r) { host.innerHTML = ""; return; }

  host.innerHTML = open ? expanded(r) : collapsed(r);
  bind();
}

function collapsed(r) {
  // The triangle was a hover-only `title` wrapping an aria-hidden icon, i.e. it
  // announced nothing at all and vanished on a touch screen. role="img" plus a
  // real label makes the one "do not trust today's totals" signal reachable.
  const warn = isFlagged(r)
    ? `<span class="text-amber-700" role="img" aria-label="${noteText(r)}" ` +
      `title="${tickTitle(r)}">${icon("exclamation-triangle", "text-[11px]")}</span>`
    : "";
  return `
    <div class="pointer-events-auto mx-auto mb-6 w-fit flex items-center gap-2
                rounded-full bg-white/95 shadow-md ring-1 ring-slate-300 px-3 py-1.5">
      <button id="tl-open" title="Browse past days"
              class="flex items-center gap-1.5 text-[12.5px] text-slate-700 hover:text-slate-900">
        ${icon("clock", "text-[13px] text-sky-600")}
        <span class="font-semibold tabular-nums">${full(r.release)}</span>
        ${warn}
      </button>
    </div>`;
}

function expanded(r) {
  const last = releases.length - 1;
  const ticks = releases.map((x, i) => {
    const on = i === idx;
    const flagged = isFlagged(x);
    const bg = on ? (flagged ? "#b45309" : "#0284c7")
                  : (flagged ? "#fcd34d" : "#cbd5e1");
    return `<button data-i="${i}" title="${tickTitle(x)}"
      class="tl-tick shrink-0 rounded-sm transition-all"
      style="width:${on ? 10 : 6}px;height:${on ? 22 : 14}px;background:${bg}"></button>`;
  }).join("");

  // Fixed width, always rendered, even on a complete day. The missing-models
  // line appears and disappears as the timeline moves, and if it were allowed to
  // size this block the track would grow and shrink under the cursor — the ticks
  // would shift sideways mid-scrub, which is maddening to aim at.
  const noteStr = noteText(r);
  const note = noteStr
    ? `<div class="text-[10.5px] text-amber-700 truncate" title="${noteStr}">${noteStr}</div>`
    : `<div class="text-[10.5px] text-slate-400">&nbsp;</div>`;

  const step = (id, glyph, label, off) => `
    <button id="${id}" title="${label}" aria-label="${label}" ${off ? "disabled" : ""}
      class="shrink-0 w-8 h-8 rounded-lg bg-white ring-1 ring-slate-300 text-slate-700
             flex items-center justify-center text-[13px] leading-none
             hover:bg-slate-100 hover:text-slate-900 hover:ring-slate-400
             disabled:opacity-30 disabled:hover:bg-white disabled:cursor-default">${glyph}</button>`;

  return `
    <div class="pointer-events-auto mx-3 mb-6 rounded-xl bg-white/95 shadow-lg
                ring-1 ring-slate-300 px-3 py-2">
      <div class="flex items-center gap-2.5">
        <button id="tl-play" title="${timer ? "Pause" : "Play through the days"}"
                aria-label="${timer ? "Pause" : "Play"}"
                class="shrink-0 w-8 h-8 rounded-lg bg-sky-600 hover:bg-sky-700 text-white
                       flex items-center justify-center text-[12px] leading-none shadow-sm">
          ${timer ? "❚❚" : "▶"}
        </button>
        ${step("tl-prev", "◀", "Previous day", idx === 0)}

        <div class="flex-1 min-w-0">
          <div class="flex items-end justify-center gap-[3px] h-[24px]">${ticks}</div>
          <input id="tl-range" type="range" min="0" max="${last}" value="${idx}" step="1"
                 class="w-full mt-1 accent-sky-600" aria-label="Forecast day"
                 aria-valuetext="${full(r.release)}${isFlagged(r) ? `, ${noteText(r)}` : ""}"/>
        </div>

        ${step("tl-next", "▶", "Next day", idx === last)}

        <div class="shrink-0 w-[150px] text-right leading-tight">
          <div class="text-[12.5px] font-semibold text-slate-800 tabular-nums">${full(r.release)}</div>
          ${note}
        </div>

        <button id="tl-close" title="Hide the timeline" aria-label="Hide the timeline"
                class="shrink-0 text-slate-400 hover:text-slate-700 text-[13px]">✕</button>
      </div>
    </div>`;
}

function bind() {
  const on = (id, ev, fn) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener(ev, fn);
  };
  on("tl-open", "click", () => { open = true; render(); });
  on("tl-close", "click", () => { stop(); open = false; render(); });
  on("tl-play", "click", () => (timer ? stop() : play()));
  on("tl-prev", "click", () => { stop(); go(idx - 1); });
  on("tl-next", "click", () => { stop(); go(idx + 1); });
  on("tl-range", "input", (e) => { stop(); go(Number(e.target.value)); });
  for (const b of host.querySelectorAll(".tl-tick")) {
    b.addEventListener("click", () => { stop(); go(Number(b.dataset.i)); });
  }
}

// ---- moving -----------------------------------------------------------------

function go(next) {
  if (next < 0 || next >= releases.length || next === idx) return;
  idx = next;
  render();
  actions.show(releases[idx].release);
  for (let i = 1; i <= PREFETCH; i++) {
    const ahead = releases[idx + i];
    if (ahead) actions.prefetch(ahead.release);
  }
}

function play() {
  if (timer) return;
  // From the end, start over rather than refusing — the common reason to press
  // play on the last day is to watch the whole sequence.
  if (idx >= releases.length - 1) {
    idx = 0;
    actions.show(releases[0].release);
  }
  timer = setInterval(() => {
    if (idx >= releases.length - 1) return stop();
    go(idx + 1);
  }, PLAY_MS);
  render();
}

function stop() {
  if (!timer) return;
  clearInterval(timer);
  timer = null;
  render();
}

// ---- the public surface -----------------------------------------------------

export function init(a) { actions = Object.assign(actions, a); }

/* Mount the bar. `index` is releases.json; `showing` is the release the map is
 * already drawing, so the cursor starts where the user is rather than at one end.
 *
 * Fewer than two releases means there is nothing to browse, so nothing is drawn
 * — a first install gets no empty control asking to be used. */
export function build(index, showing) {
  host = document.getElementById("timeline");
  releases = (index && index.releases) || [];
  if (!host || releases.length < 2) { if (host) host.innerHTML = ""; return; }
  const found = releases.findIndex((r) => r.release === showing);
  idx = found >= 0 ? found : releases.length - 1;
  open = false;
  render();
}

/* Keep the bar in step when something else changes the release. */
export function setCurrent(date) {
  const found = releases.findIndex((r) => r.release === date);
  if (found < 0 || found === idx) return;
  idx = found;
  render();
}

export function isPlaying() { return !!timer; }
