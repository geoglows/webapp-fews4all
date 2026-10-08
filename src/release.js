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
//   palettes.csv        per (palette, severity): the colour
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
export function buildRelease(style, cellsText, forecastsText, rollupText) {
  const key = (res, id) => `${res}/${id}`;

  const cells = new Map();
  const cellRows = toObjects(parseCsv(cellsText));
  const impactFields = ["population", "buildings", "farmland_m2", "highway_km", "railway_km"];
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
      // The risk index and the three categories it is built from, delivered
      // together so the panel can say WHY a cell scored rather than only what it
      // scored. Null on a release written before the index existed, so the panel
      // leaves the block out instead of showing a confident zero.
      wri: num(r.wri) === null ? null : {
        score: num(r.wri),
        severity: num(r.wri_severity),
        concurrence: num(r.wri_concurrence),
        impact: num(r.wri_impact),
      },
    });
  }

  const forecasts = new Map();
  for (const r of toObjects(parseCsv(forecastsText))) {
    r.severity = (r.severity || "").toLowerCase();
    forecasts.set(r.forecast_uid, r);
  }

  // The rollup is the biggest table and only ever read by position, so it is
  // parsed without building an object per row.
  const byCell = new Map();
  const roll = parseCsv(rollupText);
  const ir = roll.header.indexOf("res");
  const ih = roll.header.indexOf("h3_id");
  const iu = roll.header.indexOf("forecast_uid");
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
  // palette with a colour per rung.
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

  // The risk index's own constants, as the release that computed them stated them.
  // Read as key/value so a weight added at the backend needs no change here, and
  // so a release written before the index existed simply yields an empty object
  // and every reader falls back to its own default.
  const index = {};
  for (const r of style.index || []) {
    const v = num(r.value);
    if (r.key) index[r.key] = v === null ? r.value : v;
  }

  return {sources, layers, filters, palettes, models: style.models || [], index};
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
export const TABLES = ["cells.csv", "forecasts.csv", "cell_forecasts.csv",
                       "layers.csv", "palettes.csv", "models.csv", "index.csv"];

export async function loadRelease(base) {
  const root = base.endsWith("/") ? base : base + "/";
  const [cellsText, forecastsText, rollupText, layersText, palettesText, modelsText,
         indexText] =
    await Promise.all(TABLES.map((name) => fetch(root + name).then((r) => {
      // index.csv arrived after the other six. A release written before it exists
      // is still perfectly drawable — the risk index simply falls back to its
      // defaults — so a 404 on that one file is not a reason to fail the load.
      if (!r.ok) {
        if (name === "index.csv") return "";
        throw new Error(`${name}: HTTP ${r.status}`);
      }
      return r.text();
    })));
  const style = {
    layers: toObjects(parseCsv(layersText)),
    palettes: toObjects(parseCsv(palettesText)),
    models: toObjects(parseCsv(modelsText)),
    index: indexText ? toObjects(parseCsv(indexText)) : [],
  };
  return buildRelease(style, cellsText, forecastsText, rollupText);
}
