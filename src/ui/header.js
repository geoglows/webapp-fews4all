// The release stamp in the top bar.
//
// A forecast map with no date on it is the most quietly dangerous thing this app
// could ship: every screenshot, every briefing and every "is this current?" rests
// on a number that used to live only in a folder name on the server. So the day
// being drawn is stated on screen, and so is anything that happened to the models
// behind it.
//
// It reads the run record (run.json) the pipeline writes. That file is optional —
// an older release does not have one — in which case only the date is shown,
// which is still the part that matters most.

import {MODEL_LABELS} from "../config.js";

const el = (id) => document.getElementById(id);

// "2026-09-23" -> "23 Sep 2026", without pulling in a date library and without
// going through Date(), which would read a bare ISO date as UTC midnight and show
// the previous day to anyone west of Greenwich.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function prettyDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ""));
  if (!m) return String(iso || "");
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
}

const label = (m) => MODEL_LABELS[m] || m;

/* The one-line summary under the date.
 *
 * Phrased by exception. On an ordinary morning every model is in and the line
 * says so in three words; the moment one is missing, that is the whole line.
 * Returns {text, tone, title} — `title` is the hover detail, which is where the
 * reasons live so the bar itself stays one line.
 */
export function describeRun(run) {
  const models = (run && run.models) || {};
  const names = Object.keys(models);
  if (!names.length) return null;

  const groups = {ok: [], thin: [], dropped: [], skipped: []};
  for (const m of names) (groups[models[m].status] || groups.skipped).push(m);

  const missing = [...groups.dropped, ...groups.skipped];
  const detail = names
    .filter((m) => models[m].status !== "ok" || (models[m].notes || []).length)
    .map((m) => `${label(m)}: ${models[m].status}` +
                ((models[m].notes || []).length ? ` — ${models[m].notes.join("; ")}` : ""))
    .join("\n");

  if (missing.length) {
    return {
      text: `${missing.map(label).join(", ")} not in this release`,
      tone: "warn",
      title: detail,
    };
  }
  if (groups.thin.length) {
    return {
      text: `${groups.thin.map(label).join(", ")} unusually thin`,
      tone: "warn",
      title: detail,
    };
  }
  return {text: `All ${names.length} models`, tone: "ok", title: detail};
}

/* Fill the stamp. Safe to call with a release that has no run record. */
export function showRelease(release, fallbackDate) {
  const run = release && release.run;
  const date = (run && run.release) || fallbackDate;
  const dateEl = el("release-date");
  const noteEl = el("release-note");
  if (!dateEl || !noteEl) return;

  dateEl.textContent = prettyDate(date);
  dateEl.setAttribute("datetime", date || "");

  const d = describeRun(run);
  if (!d) {
    noteEl.textContent = "";
    noteEl.removeAttribute("title");
    return;
  }
  noteEl.textContent = d.text;
  noteEl.className = d.tone === "warn"
    ? "text-[11px] truncate text-amber-800 font-medium"
    : "text-[11px] truncate text-slate-800/70";
  if (d.title) noteEl.title = d.title; else noteEl.removeAttribute("title");
}
