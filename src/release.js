// A release: the tables the backend publishes for one forecast run, parsed once
// and indexed for the map and the panel.
//
// The shape of the data changed with the CSV pipeline. There is no longer one
// GeoJSON carrying geometry and nested forecasts together; there are flat tables
// that join by key, and geometry lives in tile archives the map fetches itself.
// So this module is where the joins happen, and it is deliberately pure below
// `loadRelease` — everything except the fetching is a function of its input text,
// which is what lets it be tested without a browser.
//
//   cells.csv           one row per (cell, resolution): severity, models, impact
//   forecasts.csv       one row per forecast: the panel's attribute rows
//   cell_forecasts.csv  which forecasts are in which cell, at every resolution
//   layers.csv          per resolution: which tile archive, and its zoom band
//   palettes.csv        per (palette, severity): the color
//   models.csv          per model: its label and default palette
//
// The style tables are assembled here into the shape the map layer wants — source
// and layer definitions, with each layer's visibility filter built from the cell
// ids in cells.csv. The backend used to ship those id lists; it does not any more,
// because they were the same ids, listed a second time.

// ---- CSV ------------------------------------------------------------------

// A minimal RFC4180 reader. Written out rather than pulled in because the only
// hard part is quoting, and the tables do carry it — a country list like
// "Congo, Dem. Rep." would otherwise split into two columns and shift every
// field after it. Handles quoted fields, escaped quotes, and CRLF.
export function parseCsv(text) {
  const rows = [];
  let row = [], field = "", quoted = false, i = 0;
  // A trailing line break would otherwise produce a final empty row. Python's
  // csv.writer ends lines with CRLF, so stripping only the "\n" leaves a lone
  // "\r" behind — which is still a row terminator, and the phantom row it makes
  // parses as a cell with res NaN.
  let end = text.length;
  while (end > 0 && (text.charCodeAt(end - 1) === 10 || text.charCodeAt(end - 1) === 13)) end--;
  const src = text.slice(0, end);

  while (i < src.length) {
    const c = src[i];
    if (quoted) {
      if (c === '"') {
        if (src[i + 1] === '"') { field += '"'; i += 2; continue; }
        quoted = false; i++; continue;
      }
      field += c; i++; continue;
    }
    if (c === '"') { quoted = true; i++; continue; }
    if (c === ",") { row.push(field); field = ""; i++; continue; }
    if (c === "\n" || c === "\r") {
      if (c === "\r" && src[i + 1] === "\n") i++;
      row.push(field); rows.push(row); row = []; field = ""; i++; continue;
    }
    field += c; i++;
  }
  row.push(field);
  rows.push(row);

  const header = rows.shift() || [];
  return {header, rows};
}

// Rows as objects keyed by column name. Kept separate from parseCsv so a caller
// that only wants two columns out of fourteen can index by position instead.
export function toObjects({header, rows}) {
  return rows.map((r) => {
    const o = {};
    for (let i = 0; i < header.length; i++) o[header[i]] = r[i];
    return o;
  });
}

const num = (v) => {
  const s = String(v ?? "").trim();
  if (s === "") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
};

// ---- Indexing -------------------------------------------------------------

/**
 * Build the in-memory view of a release from the raw file contents.
 *
 * Returns the three lookups the rest of the app needs, and nothing else:
 *   cells       Map "<res>/<h3_id>" -> cell row (severity, models, impact, ...)
 *   forecasts   Map forecast_uid    -> forecast row
 *   byCell      Map "<res>/<h3_id>" -> [forecast row, ...]
 *
 * Keys are strings rather than nested maps because every lookup here is by an
 * exact (res, cell) pair; a composite string key keeps that one operation instead
 * of two, and the resolutions are a fixed tiny set so there is nothing to iterate.
 */
/* `style` is {layers, palettes, models}; the other three are arrays of plain
 * objects, already parsed; `rollupTable` stays in its raw `{columns, rows}` form
 * because it is the biggest table and is only ever read by position. They used to
 * be CSV text that this function parsed itself — the parsing moved out when the
 * published tables became columnar JSON, and nothing else about this changed. */
export function buildRelease(style, cellRows, forecastRows, rollupTable) {
  const key = (res, id) => `${res}/${id}`;

  const cells = new Map();
  // Matches backend/common/config.py IMPACT_FIELDS. Roads and railways are one
  // column now, and farmland arrives in km² rather than m² — see the note there.
  const impactFields = ["population", "buildings", "farmland_km2",
                        "transportation_km", "bridges", "dams", "weirs"];
  for (const r of cellRows) {
    // Present when the backend wrote numbers at all, even if every one is zero.
    // Blank means no impact data reached the cell; zero means it did and the
    // answer was none. Collapsing the two would state "nobody lives here" with
    // the confidence of a measurement.
    const impact = {};
    let measured = false;
    for (const f of impactFields) {
      const v = num(r[f]);
      impact[f] = v;
      if (v !== null) measured = true;
    }
    cells.set(key(r.res, r.h3_id), {
      h3_id: r.h3_id,
      res: Number(r.res),
      severity: (r.severity || "").toLowerCase(),
      model_count: num(r.model_count) || 0,
      // The backend writes these plus-joined; the map and panel both want lists.
      models: r.models ? r.models.split("+") : [],
      // Pipe-separated sets of plus-joined members. A cell with no entry is NOT
      // agreeing — see the note in the backend's step 3. Empty array, never null,
      // so callers can iterate without a guard.
      co_models: r.co_models ? r.co_models.split("|").map((s) => s.split("+")) : [],
      forecast_count: num(r.forecast_count) || 0,
      impact: measured ? impact : null,
    });
  }

  const forecasts = new Map();
  for (const r of forecastRows) {
    r.severity = (r.severity || "").toLowerCase();
    forecasts.set(r.forecast_uid, r);
  }

  // The rollup is the biggest table and only ever read by position, so it is
  // parsed without building an object per row.
  const byCell = new Map();
  const roll = rollupTable || {columns: [], rows: []};
  const ir = roll.columns.indexOf("res");
  const ih = roll.columns.indexOf("h3_id");
  const iu = roll.columns.indexOf("forecast_uid");
  for (const row of roll.rows) {
    const fc = forecasts.get(row[iu]);
    if (!fc) continue;              // a uid with no forecast row cannot be shown
    const k = key(row[ir], row[ih]);
    const list = byCell.get(k);
    if (list) list.push(fc); else byCell.set(k, [fc]);
  }

  return {
    rules: buildRules(style, cells),
    cells,
    forecasts,
    byCell,
    key,
    resolutions: (style.layers || []).map((l) => Number(l.res)).sort((a, b) => a - b),
    cellAt: (res, id) => cells.get(key(res, id)) || null,
    forecastsAt: (res, id) => byCell.get(key(res, id)) || [],
  };
}

/**
 * Turn the three style tables into MapLibre's own shapes.
 *
 * A filter is built per resolution from the cells actually in this release, which
 * is what stops the archive drawing every hexagon in the world's land drainage.
 * `in` against a literal list is the only way to do this: MapLibre cannot filter
 * on feature-state, so visibility has to be decided by id before the join runs.
 */
function buildRules(style, cells) {
  const layerRows = [...(style.layers || [])].sort((a, b) => Number(a.res) - Number(b.res));

  const idsByRes = new Map();
  for (const cell of cells.values()) {
    const list = idsByRes.get(cell.res);
    if (list) list.push(cell.h3_id); else idsByRes.set(cell.res, [cell.h3_id]);
  }

  const sources = {};
  const layers = [];
  const filters = {};
  for (const r of layerRows) {
    const res = Number(r.res);
    sources[r.source_id] = {
      type: "vector",
      url: r.url,
      // Hover and selection use feature-state, which needs a stable feature id.
      // Without this the map renders but nothing highlights.
      promoteId: r.promote_id,
    };
    const ids = (idsByRes.get(res) || []).sort();
    filters[String(res)] = ["in", ["get", r.promote_id], ["literal", ids]];
    layers.push({
      id: `cells-fill-${res}`,
      type: "fill",
      source: r.source_id,
      "source-layer": r.source_layer,
      minzoom: Number(r.minzoom),
      ...(String(r.maxzoom).trim() === "" ? {} : {maxzoom: Number(r.maxzoom)}),
      filter: filters[String(res)],
    });
  }

  // palettes.csv is one row per (palette, severity); the map wants one object per
  // palette with a color per rung.
  const palettes = [];
  const byId = new Map();
  for (const row of style.palettes || []) {
    let p = byId.get(row.palette_id);
    if (!p) {
      p = {id: row.palette_id, label: row.label};
      byId.set(row.palette_id, p);
      palettes.push(p);
    }
    p[row.severity] = row.color;
  }

  return {sources, layers, filters, palettes, models: style.models || []};
}

// ---- Loading --------------------------------------------------------------

/**
 * Fetch and index one release. `base` is the delivery folder for a date.
 *
 * The four files are requested together rather than in sequence: they have no
 * dependency on each other, and a release is not usable until all of them are in,
 * so waiting on them one at a time would only add latency.
 */
// Every file a release publishes. Named here rather than discovered from a
// manifest: six fixed names are a convention worth keeping simple, and a manifest
// would be a seventh file to keep in step with them.
/* The five things a release publishes. Named here rather than discovered from a
 * manifest, because that manifest would be a sixth file to keep in step.
 *
 *   release.json      the run record, the day manifest and the style tables —
 *                     four files that were only ever read together
 *   forecasts.json    every forecast in the release, once, each carrying the
 *                     span of days it speaks for. Not split per day: a GloFAS row
 *                     spanning days 0-3 would be written into four files and an
 *                     undated Flood Hub gauge into all sixteen
 *   cell_index.json   which forecast is in which cell, at every resolution.
 *                     Day-independent: a forecast's cell never moves
 *   day_NN.json       that forecast day's cells, and only those
 *
 * The tables inside them are columnar — `{columns, rows}` — which is what let
 * them move to JSON for about 6-10% more bytes than the CSVs they replaced once
 * gzipped, instead of the three times that records-style JSON would have cost.
 */
export const RELEASE_FILE = "release.json";
export const CELL_INDEX_FILE = "cell_index.json";
export const FORECASTS_FILE = "forecasts.json";
export const dayFile = (day) => `day_${String(day | 0).padStart(2, "0")}.json`;

/* `{columns, rows}` -> the array of plain objects the rest of this module
 * already expects, so nothing downstream learned that the format changed. */
function fromTable(t) {
  const cols = (t && t.columns) || [];
  return ((t && t.rows) || []).map((r) => {
    const o = {};
    for (let i = 0; i < cols.length; i++) o[cols[i]] = r[i] === undefined ? "" : r[i];
    return o;
  });
}

async function getJson(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  const text = await r.text();
  // Vite answers a missing file with index.html and a 200, so `r.ok` is not the
  // test — a body that opens with "<" is.
  if (text.trimStart().startsWith("<")) throw new Error(`${url}: not JSON`);
  return JSON.parse(text);
}


/* Parsed releases, kept by date so scrubbing the timeline does not re-fetch and
 * re-parse ~6 MB of CSV for a day already seen.
 *
 * Bounded, because a parsed release is tens of megabytes of live objects — 25k
 * cells and 17k forecasts as Maps — and a year of them would exhaust the tab.
 *
 * The key is a release AND a forecast day now, so one release can occupy up to
 * fourteen slots and eight is no longer a playback window. Sixteen holds today
 * plus a fortnight of its forecast, which is the sequence a user actually steps
 * through; scrubbing far back into history past that costs one fetch per day, as
 * it always did.
 */
const CACHE_MAX = 16;
const cache = new Map();

export function cachedRelease(key) { return cache.get(key) || null; }

export async function loadReleaseCached(base, key, day = 0) {
  const hit = cache.get(key);
  if (hit) {
    // Re-inserting moves it to the end, so eviction takes the least recently
    // used rather than the oldest fetched.
    cache.delete(key);
    cache.set(key, hit);
    return hit;
  }
  const release = await loadRelease(base, day);
  cache.set(key, release);
  while (cache.size > CACHE_MAX) cache.delete(cache.keys().next().value);
  return release;
}

/* The three files that belong to a RELEASE rather than to one of its days, kept
 * parsed between day steps.
 *
 * Without this, stepping a day re-fetched and re-parsed forecasts.json and
 * cell_index.json every time — and cell_index is 56,000 rows. The browser would
 * have served the bytes from its own cache, but the parse happened sixteen times
 * for an answer that cannot change within a release.
 *
 * Two releases, because scrubbing across the join between today and yesterday
 * should not throw away either. */
const REL_CACHE_MAX = 2;
const relCache = new Map();

async function releaseLevel(root) {
  const hit = relCache.get(root);
  if (hit) {
    relCache.delete(root);           // move to the end: evict least recently used
    relCache.set(root, hit);
    return hit;
  }
  const [rel, forecasts, cellIndex] = await Promise.all([
    getJson(root + RELEASE_FILE),
    getJson(root + FORECASTS_FILE),
    getJson(root + CELL_INDEX_FILE),
  ]);
  const entry = {rel, forecastRows: fromTable(forecasts), cellIndex};
  relCache.set(root, entry);
  while (relCache.size > REL_CACHE_MAX) relCache.delete(relCache.keys().next().value);
  return entry;
}

const inSpan = (r, day) => {
  const lo = Number(r.day_from || 0);
  const hi = Number(r.day_to || 0);
  return lo <= day && day <= hi;
};

export async function loadRelease(base, day = 0) {
  const root = base.endsWith("/") ? base : base + "/";
  const [{rel, forecastRows, cellIndex}, dayData] = await Promise.all([
    releaseLevel(root),
    getJson(root + dayFile(day)),
  ]);
  const style = {
    layers: rel.layers || [],
    palettes: rel.palettes || [],
    models: rel.models || [],
  };
  // The day's forecasts are the ones whose span covers it. Filtering here rather
  // than publishing sixteen copies is the whole reason forecasts.json is one file.
  const release = buildRelease(style, fromTable(dayData.cells),
                               forecastRows.filter((r) => inSpan(r, day)), cellIndex);
  release.run = rel.run || null;
  release.day = day | 0;
  // What the release says about the whole horizon, not just this day: the panel
  // needs to tell "no warnings today" apart from "this model does not forecast
  // this far ahead", and only the record knows the difference.
  release.horizon = Math.max(1, rel.horizon_days | 0 || 1);
  release.lastDay = Number.isFinite(Number(rel.last_day)) ? Number(rel.last_day) : null;
  release.days = Array.isArray(rel.days) ? rel.days : null;
  // Carried on the run record so the timeline, which is handed only this, can
  // still see how far each model reaches.
  if (release.run) {
    release.run.horizon_days = release.horizon;
    release.run.last_day = release.lastDay;
    release.run.days = release.days;
    release.run.model_reach = rel.model_reach || release.run.model_reach || null;
  }
  // The flash polygons are fetched lazily, only if the overlay is switched on,
  // so the release carries the address rather than the file.
  release.flashUrl = root + "flash_areas.geojson";
  return release;
}
