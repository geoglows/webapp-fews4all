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

// TICKS ARE A WINDOW; THE SLIDER IS THE WHOLE RANGE
//
// Every published release used to get a tick, so the strip grew by one every
// morning: by the end of November it was 31 past days against 16 forecast ones
// and it ran out from under the step arrows. The forecast half is the half this
// app is FOR, and it was being squeezed by history nobody had asked to see.
//
// So the ticks show as many days as the window has room for, and PAN when there
// are more than that. Step back past the left edge and the strip slides: a day
// appears on the left and one drops off the right. The ticks keep the sizes they
// always had -- they are aim targets, and shrinking them to fit more in makes
// them worse at the only job they have.
//
// How many fit is MEASURED, not guessed. The strip is a flex child with a fixed
// height and overflow-hidden, so its width comes from the row and never from its
// own contents -- which is what makes measuring it safe: adding ticks cannot
// change the number that fit. On a laptop at full width the whole record fits
// and nothing pans at all; dock the panel wider and it starts to.
//
// The slider underneath is not windowed either way. It spans every day the bar
// knows about, so one drag can go from a release three weeks back to day 15 of
// the forecast without letting go. That is also why its `max` never changes once
// the bar is built, which is what lets a drag survive a repaint -- see paint().
const TICK_PX = 6;            // the width of an ordinary tick
// One spacing, always. The strip briefly stretched its gaps to fill a wide
// window, which made the pitch of the axis a property of the browser window:
// the same week of days sat 3px apart docked and 9px apart full-screen, and
// stepping between the two redrew the bar at a different scale. A day is a day.
// Short of filling the track the strip just centres, and the white space either
// side is the honest statement -- there is no more record yet.
const TICK_GAP = 3;
const TICK_SPARE = 10;        // the active tick is 10px and today's 8, not 6
const MIN_VISIBLE = 8;        // narrower than this, pan rather than vanish
let visible = 23;             // ticks on the strip at once — measured in fitTicks()

let frames = [];              // [{release, day, kind, meta, empty}] — ALL of them
let idx = 0;                  // which frame is showing, as an index into frames
let winStart = 0;             // first frame the tick strip is showing
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
  fitTicks();
}

/* How many ticks the strip has room for, from the strip itself.
 *
 * Measured after the bar is in the document, because the width this depends on
 * is the one the flex row hands out -- there is no arithmetic from the viewport
 * that survives the panel being resized, the date column wrapping, or a button
 * being added to the row later. Reading it back is the only answer that stays
 * true, and it is one clientWidth per render.
 *
 * Redrawing just the strip when the count changes: everything else on the bar is
 * already correct, and the slider in particular must not be rebuilt -- see
 * paint().
 */
function fitTicks() {
  if (!open || !frames.length) return;
  const strip = document.getElementById("tl-ticks");
  const room = strip && strip.clientWidth;
  if (!room) return;                       // not laid out yet; keep what we have

  const fits = Math.floor((room - TICK_SPARE + TICK_GAP) / (TICK_PX + TICK_GAP));
  const next = Math.max(MIN_VISIBLE, Math.min(frames.length, fits));
  if (next === visible) return;
  visible = next;
  // Keep the day being shown on the strip, and prefer to keep today's end of it
  // in view: widening should reveal history, not scroll away from the forecast.
  winStart = clampWin(Math.min(winStart, frames.length - visible));
  followWindow(idx);
  strip.innerHTML = tickStrip();
}

/* The window is a function of the layout, so it has to be re-asked when the
 * layout changes. Debounced: a drag of a window edge fires this continuously,
 * and each one is a measure plus, at most, one strip redraw. */
let fitTimer = null;
function onResize() {
  if (fitTimer) clearTimeout(fitTimer);
  fitTimer = setTimeout(() => { fitTimer = null; fitTicks(); }, 120);
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

/* The tick strip: `visible` frames starting at winStart, each carrying its
 * ABSOLUTE index, so a click means the same thing however far the strip has
 * panned. Pulled out of expanded() because paint() redraws just this during a
 * scrub -- rebuilding the whole bar would replace the range input under the
 * reader's finger and end the drag mid-gesture. */
function tickStrip() {
  return frames.slice(winStart, winStart + visible).map((x, n) => {
    const i = winStart + n;
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
}

function expanded(f) {
  const last = frames.length - 1;
  const ticks = tickStrip();

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
  const note = `<div id="tl-note" class="text-[10.5px] ${tone} leading-[1.25] h-[26px] overflow-hidden"
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
          <div id="tl-ticks"
               class="flex items-end justify-center gap-[3px] h-[24px] overflow-hidden">${ticks}</div>
          <input id="tl-range" type="range" min="0" max="${last}" value="${idx}" step="1"
                 class="w-full mt-1 accent-sky-600" aria-label="Day shown"
                 aria-valuetext="${frameLabel(f)}${noteStr ? `, ${noteStr}` : ""}"/>
        </div>

        ${step("tl-next", "▶", "Next day", idx === last)}
        ${todayBtn}

        <div class="shrink-0 w-[196px] text-right leading-tight">
          <div id="tl-label" class="text-[12.5px] font-semibold text-slate-800 tabular-nums">${frameLabel(f)}</div>
          ${note}
        </div>

        <button id="tl-close" title="Hide the timeline" aria-label="Hide the timeline"
                class="shrink-0 text-slate-400 hover:text-slate-700 text-[13px]">✕</button>
      </div>
    </div>`;
}

/* Update the bar for a new `idx` WITHOUT rebuilding it.
 *
 * render() replaces host.innerHTML, which replaces the range input -- and a
 * replaced input is one the browser is no longer tracking a drag on, so the
 * thumb stops dead under the finger. That is why scrubbing never felt smooth:
 * every pixel of movement destroyed the control being moved. Nothing about the
 * bar's STRUCTURE depends on which day is showing (the tick count is fixed, the
 * slider's range never changes), so a day change only has to repaint.
 *
 * The input itself is written to only when something other than the reader
 * moved the cursor; writing the value they are already dragging is at best a
 * no-op and at worst a fight with the pointer.
 */
function paint({fromSlider = false} = {}) {
  if (!host) return;
  const f = current();
  if (!f) return;
  // Collapsed is a different control with none of these parts in it, and it
  // carries the date too -- so there is nothing to patch, only to rebuild.
  if (!open) return render();
  const el = (id) => document.getElementById(id);

  const playBtn = el("tl-play");
  if (playBtn) {
    playBtn.innerHTML = timer ? "❚❚" : "▶";
    playBtn.title = timer ? "Pause" : "Play through the days";
    playBtn.setAttribute("aria-label", timer ? "Pause" : "Play");
  }

  const strip = el("tl-ticks");
  if (strip) strip.innerHTML = tickStrip();

  const label = el("tl-label");
  if (label) label.textContent = frameLabel(f);

  const noteStr = noteText(f);
  const note = el("tl-note");
  if (note) {
    note.innerHTML = noteStr || "&nbsp;";
    note.title = noteStr;
    note.className = "text-[10.5px] leading-[1.25] h-[26px] overflow-hidden " +
      (isForecast(f) ? (f.empty ? "text-slate-400" : "text-sky-700") : "text-amber-700");
  }

  const range = el("tl-range");
  if (range) {
    if (!fromSlider) range.value = String(idx);
    range.setAttribute("aria-valuetext",
      `${frameLabel(f)}${noteStr ? `, ${noteStr}` : ""}`);
  }

  const prev = el("tl-prev"); if (prev) prev.disabled = idx === 0;
  const next = el("tl-next"); if (next) next.disabled = idx === frames.length - 1;

  const today = el("tl-today");
  if (today) {
    const atHome = idx === home;
    today.classList.toggle("invisible", atHome);
    today.classList.toggle("pointer-events-none", atHome);
    today.disabled = atHome;
    // Hidden from the pointer AND from the reading order, the same way the first
    // render writes it -- a button that is invisible but still tabbable is a
    // focus trap on an empty rectangle.
    today.tabIndex = atHome ? -1 : 0;
    today.setAttribute("aria-hidden", atHome ? "true" : "false");
  }
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
  // Scrubbing is two things at two speeds. The BAR follows the thumb exactly,
  // every event, because that is the feedback that makes a drag feel attached to
  // the finger. The MAP follows when the thumb settles: each day is a fetch and
  // a re-join, and firing one per pixel across three weeks would queue dozens of
  // loads for days nobody stopped on. SCRUB_MS is short enough that a pause
  // mid-drag still shows you where you are.
  on("tl-range", "input", (e) => {
    stop();
    scrub(Number(e.target.value), true);
  });
  // Letting go commits immediately rather than waiting out the debounce.
  on("tl-range", "change", (e) => commit(Number(e.target.value)));
  // One listener on the strip rather than one per tick: paint() replaces all
  // twenty-three of them on every event of a drag, and re-attaching that many
  // handlers sixty times a second is work the drag can feel.
  on("tl-ticks", "click", (e) => {
    const t = e.target.closest ? e.target.closest(".tl-tick") : null;
    if (!t) return;
    stop();
    go(Number(t.dataset.i));
  });
}

// ---- scrubbing ---------------------------------------------------------------

const SCRUB_MS = 110;         // how long the thumb must settle before the map moves
let scrubTimer = null;
let shown = -1;               // the frame the MAP is on, which lags idx while dragging

function scrub(next, fromSlider) {
  if (next < 0 || next >= frames.length || next === idx) return;
  idx = next;
  followWindow(idx);
  paint({fromSlider});
  if (scrubTimer) clearTimeout(scrubTimer);
  scrubTimer = setTimeout(() => commit(idx), SCRUB_MS);
}

/* Put the map on the frame the bar is showing, if it is not there already. */
function commit(next) {
  if (scrubTimer) { clearTimeout(scrubTimer); scrubTimer = null; }
  if (next >= 0 && next < frames.length && next !== idx) {
    idx = next;
    followWindow(idx);
    paint();
  }
  if (idx === shown) return;
  shown = idx;
  actions.show(frames[idx]);
  for (let i = 1; i <= PREFETCH; i++) {
    const ahead = frames[idx + i];
    if (ahead) actions.prefetch(ahead);
  }
}

// ---- moving -----------------------------------------------------------------

function go(next) {
  if (next < 0 || next >= frames.length || next === idx) return;
  idx = next;
  followWindow(idx);
  paint();
  commit(idx);
}

function play() {
  if (timer) return;
  // From the end, start over rather than refusing — the common reason to press
  // play on the last day is to watch the whole sequence.
  if (idx >= frames.length - 1) {
    idx = 0;
    followWindow(idx);
    commit(idx);
  }
  timer = setInterval(() => {
    if (idx >= frames.length - 1) return stop();
    go(idx + 1);
  }, PLAY_MS);
  paint();
}

function stop() {
  if (!timer) return;
  clearInterval(timer);
  timer = null;
  paint();
}

// ---- the public surface -----------------------------------------------------

export function init(a) {
  actions = Object.assign(actions, a);
  if (typeof window !== "undefined" && !init.listening) {
    init.listening = true;
    window.addEventListener("resize", onResize);
  }
}

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
  shown = idx;                  // the map is already drawing today
  // Open at the right-hand end: today and the whole forecast half, with whatever
  // history the window has room for behind it.
  winStart = clampWin(frames.length - visible);
  open = true;
  render();
}

/* Keep the tick window inside the range, and inside the day being shown. */
function clampWin(start) {
  return Math.max(0, Math.min(start, Math.max(0, frames.length - visible)));
}

/* Slide the strip the smallest distance that puts `i` on it. Stepping off the
 * left edge therefore drops a day off the right, which is the whole trick: the
 * ticks stay the size they were and the strip moves instead of filling up. */
function followWindow(i) {
  if (i < winStart) winStart = clampWin(i);
  else if (i > winStart + visible - 1) winStart = clampWin(i - visible + 1);
  else winStart = clampWin(winStart);
}

/* Keep the bar in step when something else changes what is shown. */
export function setCurrent(release, day = 0) {
  const found = frames.findIndex((f) => f.release === release && f.day === (day | 0));
  if (found < 0 || found === idx) return;
  idx = found;
  // Something else moved the map, so the map is already there: record it, or the
  // next scrub back to this day would fetch what is already on screen.
  shown = idx;
  followWindow(idx);
  render();
}

/* What the map is showing, for anything that needs to say it out loud. */
export function currentFrame() { return current(); }

export function isPlaying() { return !!timer; }
