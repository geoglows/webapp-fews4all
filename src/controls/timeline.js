// The timeline: past releases behind, this release's forecast days ahead.
//
// ONE BAR, TWO HALVES
//
// Everything left of the cursor's home position is a release that was published —
// what the models said on that morning, as they said it. Everything right of it
// is this morning's release looking forward: day 1, day 2, and so on to the
// horizon. The bar opens on the join between them, which is today.
//
// They are drawn differently on purpose. A past tick is a fact — it was
// published, and if a model was missing that day the tick says so. A forecast
// tick is a claim about weather that has not happened yet, and the one thing a
// user must never do is read the right-hand half as history.
//
// WHY THIS IS CHEAP
//
// The geometry never changes. cells.pmtiles holds the same 4.4M hexagons every
// day, so moving between releases OR between forecast days is not a reload — it
// is clearing feature-state, rebuilding four filters and re-applying. That is the
// same work a model checkbox already does. The only real cost is fetching and
// parsing the day's CSVs, which is why release.js caches parsed days and why this
// prefetches ahead of playback.
//
// OPEN BY DEFAULT
//
// It was collapsed, on the reasoning that a map is for looking at and the bar is
// a control you touch once. That held while it only went backwards. Now that the
// forecast half is the point — the thing the app is FOR is stepping forward — a
// bar that has to be discovered before it can be used is hiding the feature.
// Closing it still works, and it stays closed for the rest of the session.
//
// PARTIAL DAYS ARE MARKED, NOT HIDDEN
//
// 21 and 22 October carry GEOGLOWS alone; the other models were stale and were
// correctly dropped. Played back unmarked, those days show warnings halving and
// recovering, which reads as weather and is not. Each past tick is therefore
// drawn from its run record, and a day missing a model is amber with the models
// named on hover. The days are still selectable and still play: hiding them
// would be its own kind of lie.
//
// A FORECAST DAY NO MODEL REACHES IS STILL A TICK
//
// Flood Hub stops around day 7 and flash floods at day 0, so the far end of the
// bar is thinner than the near end. Those days still get ticks, drawn hollow,
// with the panel saying which models cannot see that far.
//
// What does NOT get a tick is a day no forecast names at all -- past GEOGLOWS's
// 10-day horizon, every remaining row is a GloFAS pixel or an undated Flood Hub
// gauge spanning the whole period, so the map would be identical to the day
// before. The backend decides where that line falls (step6_days, run.last_day)
// because it is the half that can see every row.

import {icon} from "../icons.js";

// Both states sit mb-6 above the map's bottom edge rather than flush with it.
// MapLibre draws its attribution along that edge, and the Esri and OSM tiles are
// used on the condition it stays legible — a control covering it is not a
// cosmetic problem.
const PLAY_MS = 900;          // one day per step; slow enough to read the map
const PREFETCH = 2;           // days to pull ahead of the cursor during playback

let frames = [];              // [{release, day, kind, meta, empty}]
let idx = 0;                  // which frame is showing
let home = 0;                 // the frame for today — where the bar opens
let open = true;
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

/* The same care, forward: "2026-11-04" + 3 -> "7 Nov 2026". Built through UTC so
 * it cannot drift across a month boundary, then formatted by hand so it cannot
 * drift across a timezone on the way back out. */
function plusDays(date, n) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date || "");
  if (!m) return String(date || "");
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3] + (n | 0)));
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

const current = () => frames[idx] || null;
const isForecast = (f) => f && f.day > 0;

/* A day is "partial" when the pipeline published it without a model. The run
 * record already decided this; nothing is re-derived here. */
const isPartial = (f) =>
  !!f.meta && Array.isArray(f.meta.missing) && f.meta.missing.length > 0;

/* Models the run marked `thin`: present, but carrying far fewer forecasts than
 * their own recent median — the volume guard's way of saying a fetch may have
 * been half a fetch.
 *
 * This is marked here because it stopped being said anywhere else. The header
 * used to carry three states (missing, thin, all clear) beside the date; with the
 * header's stamp gone the timeline is the only thing left reporting on a release,
 * and a quarter-sized GEOGLOWS looked exactly like a quiet day. The status is
 * already in releases.json, so this reads it rather than deriving anything. */
const thinModels = (f) => {
  const models = (f.meta && f.meta.models) || {};
  return Object.keys(models).filter((m) => models[m] === "thin").sort();
};

/* Either kind of trouble. One amber for both: the question a mark answers is
 * "should I trust this day's totals", and missing and thin are both no. Which it
 * is, is what the note and the hover say. Forecast days are never flagged this
 * way — the warning there is that they are forecasts, which the whole right-hand
 * half of the bar already says. */
const isFlagged = (f) => !isForecast(f) && (isPartial(f) || thinModels(f).length > 0);

/* The date this frame is ABOUT, which on a forecast day is not its release. */
const frameDate = (f) => (f.day ? plusDays(f.release, f.day) : full(f.release));

/* The short label beside the bar. A forecast day leads with how far ahead it is,
 * because that is the thing being chosen; the date follows for anyone matching it
 * against a calendar. */
function frameLabel(f) {
  if (!f) return "";
  if (f.day === 0 && f.kind === "today") return `Today · ${full(f.release)}`;
  if (!f.day) return full(f.release);
  return `+${f.day} day${f.day === 1 ? "" : "s"} · ${plusDays(f.release, f.day)}`;
}

/* The short form, for the 150px block beside the date. Both halves when a day
 * manages both, truncated with the full text on hover. */
function noteText(f) {
  if (isForecast(f)) {
    if (f.empty) return "nothing new on this day";
    const models = (f.models || []).length;
    return models ? `forecast · ${models} model${models === 1 ? "" : "s"}` : "forecast";
  }
  const bits = [];
  if (isPartial(f)) bits.push(`without ${f.meta.missing.join(", ")}`);
  const thin = thinModels(f);
  if (thin.length) bits.push(`${thin.join(", ")} thin`);
  return bits.join(" · ");
}

function tickTitle(f) {
  const bits = [frameLabel(f)];
  if (isForecast(f)) {
    bits.push(f.empty ? "no model reaches this day"
                      : `forecast from the ${full(f.release)} run`);
    if ((f.models || []).length) bits.push(f.models.join(", "));
  } else {
    const cells = ((f.meta && f.meta.cells) || {})["6"];
    if (typeof cells === "number") bits.push(`${cells.toLocaleString()} cells`);
  }
  const note = !isForecast(f) ? noteText(f) : "";
  if (note) bits.push(note);
  return bits.join(" · ");
}

// ---- rendering --------------------------------------------------------------

function render() {
  if (!host) return;
  const f = current();
  if (!f) { host.innerHTML = ""; return; }
  host.innerHTML = open ? expanded(f) : collapsed(f);
  bind();
}

function collapsed(f) {
  // The triangle was a hover-only `title` wrapping an aria-hidden icon, i.e. it
  // announced nothing at all and vanished on a touch screen. role="img" plus a
  // real label makes the one "do not trust today's totals" signal reachable.
  const warn = isFlagged(f)
    ? `<span class="text-amber-700" role="img" aria-label="${noteText(f)}" ` +
      `title="${tickTitle(f)}">${icon("exclamation-triangle", "text-[11px]")}</span>`
    : "";
  // A forecast day says so in the collapsed state too. Collapsed is where the app
  // spends most of its life, and "4 Nov" on a map of the 7th would be the single
  // most damaging thing this control could show.
  const ahead = isForecast(f)
    ? `<span class="rounded px-1 py-[1px] text-[10px] font-semibold
                    bg-sky-100 text-sky-800 ring-1 ring-sky-200">+${f.day}d</span>`
    : "";
  return `
    <div class="pointer-events-auto mx-auto mb-6 w-fit flex items-center gap-2
                rounded-full bg-white shadow-md ring-1 ring-slate-300 px-3 py-1.5">
      <button id="tl-open" title="Browse days and forecasts"
              class="flex items-center gap-1.5 text-[12.5px] text-slate-700 hover:text-slate-900">
        ${icon("clock", "text-[13px] text-sky-600")}
        <span class="font-semibold tabular-nums">${frameDate(f)}</span>
        ${ahead}${warn}
      </button>
    </div>`;
}

function expanded(f) {
  const last = frames.length - 1;
  const ticks = frames.map((x, i) => {
    const on = i === idx;
    const fc = isForecast(x);
    const flagged = isFlagged(x);
    // Today is the join between the two halves and the one tick worth finding at
    // a glance, so it is green and stands a little proud of its neighbours
    // whether or not it is the day being shown. Everything else carries meaning
    // in colour already — amber for a short run, sky for a forecast — and green
    // is the only one left that reads as "you are here" rather than as a state.
    const todayTick = x.kind === "today";
    let bg = on ? "#0284c7" : "#cbd5e1";
    if (todayTick) bg = on ? "#15803d" : "#22c55e";
    else if (flagged) bg = on ? "#b45309" : "#fcd34d";
    else if (fc) bg = on ? "#0284c7" : (x.empty ? "transparent" : "#bae6fd");
    const ring = (fc && x.empty && !on) ? "box-shadow:inset 0 0 0 1px #bae6fd;" : "";
    const w = on ? 10 : (todayTick ? 8 : 6);
    const h = on ? 24 : (todayTick ? 19 : 14);
    return `<button data-i="${i}" title="${tickTitle(x)}"
      class="tl-tick shrink-0 rounded-sm transition-all"
      style="width:${w}px;height:${h}px;background:${bg};${ring}"></button>`;
  }).join("");

  // Fixed width, always rendered, even on a complete day. The note line appears
  // and disappears as the timeline moves, and if it were allowed to size this
  // block the track would grow and shrink under the cursor — the ticks would
  // shift sideways mid-scrub, which is maddening to aim at.
  //
  // It wraps to two lines rather than truncating. "without flash, flood_hub, gl…"
  // named two of the three models that were missing and then gave up, which is
  // the one thing this note exists to say. The height is fixed for the same
  // reason the width is, so growing to a second line does not move anything.
  const noteStr = noteText(f);
  const tone = isForecast(f) ? (f.empty ? "text-slate-400" : "text-sky-700")
                             : "text-amber-700";
  const note = `<div class="text-[10.5px] ${tone} leading-[1.25] h-[26px] overflow-hidden"
       title="${noteStr}">${noteStr || "&nbsp;"}</div>`;

  const step = (id, glyph, label, off) => `
    <button id="${id}" title="${label}" aria-label="${label}" ${off ? "disabled" : ""}
      class="shrink-0 w-8 h-8 rounded-lg bg-white ring-1 ring-slate-300 text-slate-700
             flex items-center justify-center text-[13px] leading-none
             hover:bg-slate-100 hover:text-slate-900 hover:ring-slate-400
             disabled:opacity-30 disabled:hover:bg-white disabled:cursor-default">${glyph}</button>`;

  // One button back to today, because the bar now has two directions to get lost
  // in and "where was I" is the question a user asks most after scrubbing.
  //
  // It is always in the layout and only sometimes visible. Adding and removing it
  // resized the track under the cursor, so every tick jumped sideways the instant
  // you stepped off today — in a control whose whole job is aiming at small
  // targets, the one moment it moved was the moment you had just clicked.
  const atHome = idx === home;
  const todayBtn = `
    <button id="tl-today" title="Back to today" aria-label="Back to today"
      ${atHome ? 'tabindex="-1" aria-hidden="true" disabled' : ""}
      class="shrink-0 h-8 px-2 rounded-lg bg-white ring-1 ring-slate-300 text-[11px]
             font-semibold text-green-700 hover:bg-green-50 hover:ring-green-300
             ${atHome ? "invisible pointer-events-none" : ""}">Today</button>`;

  return `
    <div class="pointer-events-auto mx-3 mb-6 rounded-xl bg-white shadow-lg
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
                 class="w-full mt-1 accent-sky-600" aria-label="Day shown"
                 aria-valuetext="${frameLabel(f)}${noteStr ? `, ${noteStr}` : ""}"/>
        </div>

        ${step("tl-next", "▶", "Next day", idx === last)}
        ${todayBtn}

        <div class="shrink-0 w-[196px] text-right leading-tight">
          <div class="text-[12.5px] font-semibold text-slate-800 tabular-nums">${frameLabel(f)}</div>
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
  on("tl-today", "click", () => { stop(); go(home); });
  on("tl-range", "input", (e) => { stop(); go(Number(e.target.value)); });
  for (const b of host.querySelectorAll(".tl-tick")) {
    b.addEventListener("click", () => { stop(); go(Number(b.dataset.i)); });
  }
}

// ---- moving -----------------------------------------------------------------

function go(next) {
  if (next < 0 || next >= frames.length || next === idx) return;
  idx = next;
  render();
  actions.show(frames[idx]);
  for (let i = 1; i <= PREFETCH; i++) {
    const ahead = frames[idx + i];
    if (ahead) actions.prefetch(ahead);
  }
}

function play() {
  if (timer) return;
  // From the end, start over rather than refusing — the common reason to press
  // play on the last day is to watch the whole sequence.
  if (idx >= frames.length - 1) {
    idx = 0;
    actions.show(frames[0]);
  }
  timer = setInterval(() => {
    if (idx >= frames.length - 1) return stop();
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

/* Mount the bar.
 *
 *   index    releases.json
 *   showing  the release the map is already drawing
 *   run      that release's run.json, for the forecast half: how many days it
 *            carries and which models reach each one. Absent (an older release,
 *            or a deployment without the day axis) means no forecast half at all,
 *            and the bar is exactly the retrospective one it has always been.
 *
 * Nothing to browse in EITHER direction means nothing is drawn — a first install
 * gets no empty control asking to be used.
 */
export function build(index, showing, run) {
  host = document.getElementById("timeline");
  const past = (index && index.releases) || [];
  const at = past.findIndex((r) => r.release === showing);

  // Everything up to and including the release on screen is history; the forecast
  // half belongs to that release and nothing after it.
  const upto = at >= 0 ? past.slice(0, at + 1) : past;
  frames = upto.map((r, i) => ({
    release: r.release, day: 0, meta: r,
    kind: i === upto.length - 1 ? "today" : "past",
  }));
  home = Math.max(0, frames.length - 1);

  // The forecast half runs to run.last_day, not to the horizon. A day earns a
  // tick by carrying a forecast that NAMES it; a day holding only rows that span
  // the whole horizon -- GloFAS, a Flood Hub gauge with no published window --
  // would draw exactly what the day before it drew, and offering a step that
  // changes nothing is worse than not offering it.
  //
  // Older releases have no last_day, so they fall back to whatever days they
  // list, which is what they meant.
  const days = (run && Array.isArray(run.days)) ? run.days : [];
  const lastDay = (run && Number.isFinite(Number(run.last_day)))
    ? Number(run.last_day) : Infinity;
  const base = showing || (frames[home] && frames[home].release);
  for (const d of days) {
    if (!d || !d.day || d.day > lastDay) continue;  // day 0 is the "today" frame
    frames.push({
      release: base, day: d.day, kind: "forecast",
      models: d.models || [],
      // "Nothing new here", not "nothing here": the map may still be drawing
      // GloFAS. What is absent is a forecast that picked this day out.
      empty: !(d.dated > 0),
    });
  }

  if (!host || frames.length < 2) { if (host) host.innerHTML = ""; return; }
  idx = home;
  open = true;
  render();
}

/* Keep the bar in step when something else changes what is shown. */
export function setCurrent(release, day = 0) {
  const found = frames.findIndex((f) => f.release === release && f.day === (day | 0));
  if (found < 0 || found === idx) return;
  idx = found;
  render();
}

/* What the map is showing, for anything that needs to say it out loud. */
export function currentFrame() { return current(); }

export function isPlaying() { return !!timer; }
