#!/usr/bin/env python3
"""
build_cells_h3.py — flag the H3 cells a flooding river actually runs through,
telescoping from resolution 3 (coarse) to 6 (fine).

GEOGLOWS reaches are joined to res-6 cells through the crosswalk — on `report_id`,
the reporting reach a forecast is issued against, not the individual segment —
exactly the way build_basins.py joins them to HUC08 basins; Flood Hub gauges have no crosswalk
entry (they are gauges, not GEOGLOWS reaches), so they are binned by their own
coordinate. Res-6 results are then rolled up to 5, 4 and 3 through the H3 parent
hierarchy (a cell's parent is a pure function of its id).

The crosswalk lists every cell a reach traverses, so a long river lights up its
whole path instead of the single hexagon holding its centroid — which is the
point of the file and the difference from the old point-binning build.

Multi-model agreement is decided at res 6 and carried up (`co_models`), so a coarse
cell is marked as agreeing only where two models actually meet in one fine cell —
not merely because both appear somewhere under it.

Reads:  ../Files/h3_streams_global_r6.csv   h3_id (res 6) -> river_id, report_id
        <run>/mapstyletable_*.csv           GEOGLOWS: comid, ret_per, mean, lat, lon
        <run>/world_flood_status.csv        Flood Hub: gauge severity + lat/lon
        <run>/RPG_U*.shp                    GloFAS: reporting points + alert level
        ../Files/Basins/HUC12.parquet       impact geometry (area-weighted into cells)
        ../Files/Impact/*.csv               per-HUC12 impact statistics
        ../Files/International_boundaries/geoBoundariesCGAZ_ADM2.gpkg
Writes: ../public/data_h3cells.geojson      one FeatureCollection, features tagged
                                            with `res`, plus a top-level `resolutions`
"""

import csv
import json
import os
import sys
import inspect

RESOLUTIONS = [3, 4, 5, 6]
BASE_RES = 6                      # the resolution the crosswalk is built at
FIX_ANTIMERIDIAN = "split"

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                        # <repo>
FILES = os.path.join(ROOT, "Files")
PUBLIC = os.path.join(ROOT, "public")   # the app reads its data from here
os.makedirs(PUBLIC, exist_ok=True)
OUTPUT = os.path.join(PUBLIC, "data_h3cells.geojson")

MATCHES_CSV = os.path.join(FILES, "h3_streams_global_r6.csv")
# Which id column of the crosswalk is joined to the forecast CSV. `report_id` is
# the reporting reach GEOGLOWS issues a forecast against; `river_id` is the
# individual segment. Every report_id is itself a river_id (and a comid), so both
# join cleanly — reporting reaches are simply the level the forecast is published at.
MATCH_ID_COL = "report_id"

# Every model's forecast for one run lives in a dated folder, so moving to a newer
# run is one constant here. The crosswalk, impact tables and boundary files are
# reference data that does not change per run, and stay at the Files root.
RUN = "9-23-2026_4-30"
RUN_DIR = os.path.join(FILES, RUN)
GEOGLOWS_CSV = os.path.join(RUN_DIR, "mapstyletable_2026-09-23-00.csv")
FLOOD_HUB_CSV = os.path.join(RUN_DIR, "world_flood_status.csv")
GLOFAS_SHP = os.path.join(RUN_DIR, "RPG_U2026092300.shp")

BASINS_DIR = os.path.join(FILES, "Basins")
IMPACT_DIR = os.path.join(FILES, "Impact")
HUC12_PARQUET = os.path.join(BASINS_DIR, "HUC12.parquet")

# geoBoundaries ADM2 (districts): each forecast point is placed in the district it
# physically sits in, giving a specific location instead of a whole-cell one.
BOUNDARIES = os.path.join(FILES, "International_boundaries")
# Tried finest first, falling back to the next level only for the points the finer
# one could not place. CGAZ's ADM2 does not tile every country — Uruguay's units
# cover 37% of its area, Norway's 70% — so an ADM2-only lookup leaves points in
# those gaps with no location at all.
ADMIN_LEVELS = [
    (2, "geoBoundariesCGAZ_ADM2.gpkg"),
    (1, "geoBoundariesCGAZ_ADM1.gpkg"),
    (0, "geoBoundariesCGAZ_ADM0.gpkg"),
]
ADMIN_READ_BATCH = 4000   # polygons per streamed read (keeps memory flat)

# Impact stats are measured per HUC12 basin; each file is HYBAS_ID + value
# column(s), with a TOTAL row we skip. (file, {csv column: impact field})
IMPACT_FILES = [
    ("population_statistics.csv", {"pop_value": "population"}),
    ("building_statistics.csv", {"building_count": "buildings"}),
    ("farmland_statistics.csv", {"area_m2": "farmland_m2"}),
    ("transportation_statistics.csv", {"highway_km": "highway_km",
                                       "railway_km": "railway_km"}),
]
IMPACT_FIELDS = ["population", "buildings", "farmland_m2", "highway_km", "railway_km"]
HUC12_READ_BATCH = 20_000

# GEOGLOWS has no severity label, only a return period (years); below 2-year is not
# flagged, and streams below the mean-flow floor are dropped. Flood Hub uses labels.
GEOGLOWS_SEVERITY_THRESHOLDS = [(20, "extreme"), (5, "danger"), (2, "warning")]
GEOGLOWS_MIN_MEAN_FLOW = 5
FLOOD_HUB_SEVERITY = {"ABOVE_NORMAL": "warning", "SEVERE": "danger", "EXTREME": "extreme"}

# GloFAS reporting points carry `ThresGroup`: the highest discharge threshold whose
# maximum ensemble exceedance probability reached 30% — 16 of the 51 ECMWF members.
# 0 means no threshold was reached, so it is not an alert. The three thresholds are
# the 2-, 5- and 20-year return periods, the same ladder GEOGLOWS is cut at above,
# so severity means the same thing on all three models.
GLOFAS_SEVERITY = {1: "warning", 2: "danger", 3: "extreme"}
GLOFAS_RETURN_PERIOD = {1: 2, 2: 5, 3: 20}
# The name columns carry literal placeholders rather than blanks on the ~92% of
# alerting points that are dynamic grid cells instead of gauged stations. They are
# normalised to "" so the panel shows an empty row, not "Not a station".
GLOFAS_PLACEHOLDERS = {"not a station", "not found", "na", "n/a", "none", "-", "--"}

SEVERITY_RANK = {"none": 0, "warning": 1, "danger": 2, "extreme": 3}


def _f(v):
    v = (v or "").strip()
    if v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _s(cols, name, i, placeholders=()):
    """One string cell out of a shapefile's column arrays; "" if the column is
    absent, or if the value is one of `placeholders` (matched case-insensitively)."""
    col = cols.get(name)
    if col is None:
        return ""
    v = str(col[i]).strip()
    return "" if v.lower() in placeholders else v


def _int_or_blank(col, i):
    """One integer cell, or "" when the column is absent or the value is a sentinel."""
    if col is None:
        return ""
    try:
        n = int(col[i])
    except (TypeError, ValueError):
        return ""
    return "" if n < 0 else n


def _day_offset(issued, steps, base):
    """`issued` (YYYY-MM-DD) shifted by a whole-day index, as a date string.

    GloFAS counts its 15-day medium-range horizon in whole forecast days rather
    than hours, and the two day columns are numbered differently: PeakTime is
    1-based (1 = the forecast day itself) while LeadtimeH is 0-based, so `base`
    says which. A negative index is the file's not-applicable sentinel (-97 on
    LeadtimeH, meaning the point is already over threshold at issue time) and
    returns "" rather than inventing a date in the past.
    """
    from datetime import date, timedelta
    try:
        n = int(steps)
    except (TypeError, ValueError):
        return ""
    if n < 0:
        return ""
    try:
        y, m, d = (int(x) for x in str(issued).split("-"))
        start = date(y, m, d)
    except ValueError:
        return ""
    return (start + timedelta(days=n - base)).isoformat()


def worst_severity(forecasts):
    best, best_rank = "", -1
    for fc in forecasts:
        r = SEVERITY_RANK.get(str(fc.get("severity", "")).lower(), -1)
        if r > best_rank:
            best_rank, best = r, str(fc.get("severity", "")).lower()
    return best


def geoglows_severity(ret_per):
    for thr, sev in GEOGLOWS_SEVERITY_THRESHOLDS:
        if ret_per >= thr:
            return sev
    return None


# ---- inputs ----------------------------------------------------------------

def read_geoglows_flooding(path):
    """{comid: forecast} for every GEOGLOWS reach above the flood thresholds.

    One dict per reach, reused by every cell the reach runs through, so the
    district lookup below runs once per reach rather than once per cell.
    """
    out = {}
    if not os.path.exists(path):
        print(f"  note: {os.path.basename(path)} not found; GEOGLOWS skipped.", file=sys.stderr)
        return out
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rp = _f(r.get("ret_per"))
            if rp is None:
                continue
            sev = geoglows_severity(int(rp))
            if sev is None:
                continue
            mean = _f(r.get("mean"))
            if GEOGLOWS_MIN_MEAN_FLOW and (mean is None or mean < GEOGLOWS_MIN_MEAN_FLOW):
                continue
            try:
                cid = int(float((r.get("comid") or "").strip()))
            except ValueError:
                continue
            out[cid] = {
                "model": "geoglows", "severity": sev, "riverId": str(cid),
                "country": "", "returnPeriodYr": int(rp), "peakDischargeCms": mean,
                "issuedTime": "", "startTime": "", "peakTime": "", "endTime": "",
                "historicalComparison": "",
                "lat": _f(r.get("lat")), "lon": _f(r.get("lon")),
            }
    return out


def load_matched_cells(path, flooding):
    """res-6 cell -> [forecast, ...] for the flooding reaches crossing that cell.

    The crosswalk is ~12.5M rows, so it is streamed and filtered against the
    flooding set rather than read into memory.

    A row is one (cell, segment) crossing, so several segments of the same
    reporting reach routinely cross the same cell — about 56% of rows repeat a
    (cell, report_id) pair. Those are collapsed here: a cell lists a reach once
    however many of its segments run through it, or the panel would show the same
    forecast several times over and model_count would count each copy.
    """
    base, seen, rows, hits, dups = {}, {}, 0, 0, 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        rd = csv.reader(f)
        header = next(rd, None)
        if not header:
            sys.exit(f"{os.path.basename(path)} is empty.")
        cols = {c.strip(): i for i, c in enumerate(header)}
        for key in ("h3_id", MATCH_ID_COL):
            if key not in cols:
                sys.exit(f"{os.path.basename(path)} has no '{key}' column "
                         f"(found: {list(cols)}).")
        i_cell, i_id = cols["h3_id"], cols[MATCH_ID_COL]
        for row in rd:
            rows += 1
            try:
                rid = int(float(row[i_id]))
            except (ValueError, IndexError):
                continue
            fc = flooding.get(rid)
            if fc is None:
                continue
            cell = row[i_cell].strip()
            if not cell:
                continue
            bucket = seen.setdefault(cell, set())
            if rid in bucket:
                dups += 1
                continue
            bucket.add(rid)
            base.setdefault(cell, []).append(fc)
            hits += 1
    print(f"Crosswalk: {rows:,} row(s) read, {hits:,} cell/reach pair(s) kept "
          f"({dups:,} repeat crossings collapsed), joined on {MATCH_ID_COL}.")
    return base


def read_flood_hub_points(path):
    """Every Flood Hub gauge alert as a point forecast (with its coordinate)."""
    out = []
    if not os.path.exists(path):
        print(f"  note: {os.path.basename(path)} not found; Flood Hub skipped.", file=sys.stderr)
        return out
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            raw = (r.get("severity") or "").strip().upper()
            if raw not in FLOOD_HUB_SEVERITY:
                continue
            lat = _f(r.get("gaugeLocation.latitude"))
            lon = _f(r.get("gaugeLocation.longitude"))
            if lat is None or lon is None:
                continue
            out.append({
                "model": "flood_hub", "severity": FLOOD_HUB_SEVERITY[raw],
                "riverId": (r.get("gaugeId") or "").strip(),
                "country": (r.get("queriedCountryName") or "").strip(),
                "returnPeriodYr": (r.get("returnPeriodYr") or "").strip(),
                "peakDischargeCms": (r.get("dischargePeak_m3s") or r.get("discharge") or "").strip(),
                "issuedTime": (r.get("issuedTime") or "").strip(),
                "startTime": (r.get("forecastTimeRange.start") or "").strip(),
                # New in world_flood_status.csv; the older Flood_Hub_Global.csv
                # export had no peak column at all, which is why this was blank.
                "peakTime": (r.get("peakTime") or "").strip(),
                "endTime": (r.get("forecastTimeRange.end") or "").strip(),
                "historicalComparison": "",
                "lat": lat, "lon": lon,
            })
    return out


def read_glofas_points(path):
    """Every GloFAS reporting point under alert, as a point forecast.

    The shapefile holds all ~4,100 points whether or not they are flagged; only
    ThresGroup > 0 is an alert, and the rest are the grey "monitored, below
    threshold" points.

    Unlike the other two models this file names the station, river and basin it
    forecasts for, so those ride along on the forecast for the panel to use later.
    `district` is deliberately NOT set here — it comes from the shared ADM cascade
    like every other model, so "Near" keeps one meaning across the whole app.

    `startTime` stays empty. `LeadtimeH` looks like an event start but is not one:
    on 90 of the 1,012 points that have it, it lands AFTER `PeakTime`, and every
    one of those 90 carries EarlyPeak=1. So LeadtimeH is the lead time of the
    strongest exceedance probability, not of the first crossing, and writing it
    into `startTime` would put the start of 9% of events after their own peak. It
    is carried through unchanged as `leadTimeDays` instead.
    """
    out = []
    if not os.path.exists(path):
        print(f"  note: {os.path.basename(path)} not found; GloFAS skipped.", file=sys.stderr)
        return out
    try:
        from pyogrio.raw import read
    except ImportError:
        print("  note: pyogrio not installed; GloFAS skipped.", file=sys.stderr)
        return out

    meta, _meta2, _geom, fields = read(path)
    cols = dict(zip(list(meta["fields"]), fields))
    missing = [c for c in ("ThresGroup", "Lat", "Long", "PointID", "ForecastDa",
                           "PeakTime", "LeadtimeH") if c not in cols]
    if missing:
        sys.exit(f"{os.path.basename(path)} is missing column(s): {missing}")

    skipped = 0
    for i in range(len(cols["ThresGroup"])):
        try:
            group = int(cols["ThresGroup"][i])
        except (TypeError, ValueError):
            continue
        sev = GLOFAS_SEVERITY.get(group)
        if sev is None:                      # group 0 — monitored, not alerting
            continue
        lat, lon = _f(str(cols["Lat"][i])), _f(str(cols["Long"][i]))
        if lat is None or lon is None:
            skipped += 1
            continue
        issued = _s(cols, "ForecastDa", i)
        out.append({
            "model": "glofas", "severity": sev,
            "riverId": _s(cols, "PointID", i),
            "country": _s(cols, "Country", i),
            "returnPeriodYr": GLOFAS_RETURN_PERIOD[group],
            # GloFAS publishes no discharge value on the reporting points, and none
            # of the companion rasters carry one either.
            "peakDischargeCms": "",
            "issuedTime": issued,
            "startTime": "",                 # see the docstring: LeadtimeH is not a start
            "peakTime": _day_offset(issued, cols["PeakTime"][i], base=1),
            "endTime": "",
            "historicalComparison": "",
            # GloFAS-only detail, carried for the panel; harmless to the others.
            "leadTimeDays": _int_or_blank(cols.get("LeadtimeH"), i),
            "station": _s(cols, "Station", i, GLOFAS_PLACEHOLDERS),
            "river": _s(cols, "River", i, GLOFAS_PLACEHOLDERS),
            "basin": _s(cols, "Basin", i, GLOFAS_PLACEHOLDERS),
            "subBasin": _s(cols, "Sub-Basin", i, GLOFAS_PLACEHOLDERS),
            "region": _s(cols, "Region", i, GLOFAS_PLACEHOLDERS),
            "lat": lat, "lon": lon,
        })
    if skipped:
        print(f"  note: {skipped:,} GloFAS alert(s) had no usable coordinate.", file=sys.stderr)
    return out


# ---- H3 plumbing (vgridpandas) ---------------------------------------------

def _pick(params, names):
    return next((n for n in names if n in params), None)


def detect_token_col(orig_cols, orig_index, gridded, pd):
    new_cols = [c for c in gridded.columns if c not in orig_cols]
    token_candidates = [c for c in new_cols if not str(c).endswith("_res")]
    if token_candidates:
        return token_candidates[0], gridded
    if gridded.index.name not in (None, orig_index):
        return gridded.index.name, gridded.reset_index()
    if not isinstance(gridded.index, pd.RangeIndex) and gridded.index.name is None:
        return "h3", gridded.rename_axis("h3").reset_index()
    return None, gridded


def cells_to_geom(uniq_df, token_col, fix):
    """cell id -> shapely polygon, via vgridpandas' h32geo."""
    h32geo = uniq_df.h3.h32geo
    params = inspect.signature(h32geo).parameters
    col_kw = _pick(params, ("h3_col", "h3_column", "column", "col"))
    kwargs = {col_kw: token_col} if col_kw else {}
    if "fix_antimeridian" in params:
        kwargs["fix_antimeridian"] = fix
    try:
        gdf = h32geo(**kwargs)
    except (ValueError, TypeError) as e:
        print(f"  note: h32geo({kwargs}) failed ({e}); retrying without "
              f"fix_antimeridian.", file=sys.stderr)
        kwargs.pop("fix_antimeridian", None)
        gdf = h32geo(**kwargs)
    if token_col in gdf.columns:
        return dict(zip(gdf[token_col], gdf.geometry))
    return dict(zip(gdf.index, gdf.geometry))


def cells_for_points(points, res, pd):
    """The res-N cell each (lat, lon) point falls in, in input order."""
    df = pd.DataFrame({"lat": [p["lat"] for p in points],
                       "lon": [p["lon"] for p in points]})
    l2h_params = inspect.signature(df.h3.latlon2h3).parameters
    lat_kw = _pick(l2h_params, ("lat_col",))
    lon_kw = _pick(l2h_params, ("lon_col", "lng_col", "long_col", "longitude_col"))
    kwargs = {}
    if lat_kw:
        kwargs[lat_kw] = "lat"
    if lon_kw:
        kwargs[lon_kw] = "lon"
    orig_cols, orig_index = list(df.columns), df.index.name
    gridded = df.h3.latlon2h3(res, **kwargs)
    token_col, gridded = detect_token_col(orig_cols, orig_index, gridded, pd)
    if token_col is None:
        sys.exit(
            f"Could not find the H3 cell column added by latlon2h3 (res {res}).\n"
            f"  columns now: {list(gridded.columns)}\n"
            f"  index name:  {gridded.index.name}"
        )
    return [str(t) for t in gridded[token_col].tolist()]


def cooccurring_sets(base, res, h3):
    """cell -> the model sets that genuinely share one res-6 cell inside it.

    Two models forecasting in neighbouring res-6 cells are not agreement, but a
    rolled-up cell holds both their forecasts and would otherwise look like it.
    So agreement is decided at the base resolution and carried upward: a coarse
    cell records the sets that actually co-occur somewhere beneath it, and the
    front end hashes only those (and only while every model in a set is switched
    on). A cell with nothing co-occurring gets no entry at all.
    """
    out = {}
    for cell, fcs in base.items():
        models = {fc["model"] for fc in fcs}
        if len(models) < 2:
            continue
        parent = cell if res == BASE_RES else h3.cell_to_parent(cell, res)
        out.setdefault(parent, set()).add(frozenset(models))
    return {cell: sorted(sorted(ms) for ms in sets) for cell, sets in out.items()}


def rollup(base, res, h3):
    """Group the res-6 cells into their ancestors at `res`, keeping each forecast
    once per ancestor (one reach usually crosses several children)."""
    out = {}
    for cell, fcs in base.items():
        parent = cell if res == BASE_RES else h3.cell_to_parent(cell, res)
        seen, bucket = out.setdefault(parent, (set(), []))
        for fc in fcs:
            if id(fc) in seen:
                continue
            seen.add(id(fc))
            bucket.append(fc)
    return {cell: bucket for cell, (_seen, bucket) in out.items()}


# ---- district lookup (copied from build_basins.py; same contract) -----------

def _names_at_level(path, pts, wanted):
    """{point index: containing polygon's name} for the points listed in `wanted`.

    Streams the layer (STRtree per batch) so memory stays flat, and queries only
    the points still unplaced, so each fallback level costs less than the one
    before it."""
    import numpy as np
    from pyogrio.raw import read
    from shapely import from_wkb, STRtree

    if not os.path.exists(path):
        print(f"  note: {os.path.basename(path)} not found; level skipped.", file=sys.stderr)
        return {}
    order = np.array(sorted(wanted))
    sub = pts[order]
    found = {}
    skip = 0
    while True:
        meta, _fid, geom, fields = read(path, columns=["shapeName"],
                                        skip_features=skip, max_features=ADMIN_READ_BATCH)
        n = len(geom)
        if n == 0:
            break
        names = fields[{name: i for i, name in enumerate(meta["fields"])}["shapeName"]]
        polys = from_wkb([bytes(g) for g in geom])
        pi, gi = STRtree(polys).query(sub, predicate="intersects")
        for p_idx, g_idx in zip(pi, gi):
            key = int(order[p_idx])
            if key in found:
                continue
            if polys[g_idx].contains(sub[p_idx]):
                nm = (names[g_idx] or "").strip()
                if nm:
                    found[key] = nm
        skip += n
        if n < ADMIN_READ_BATCH or len(found) >= len(sub):
            break
    return found


def assign_admin(forecasts):
    """Tag each forecast dict with the place its gauge/reach physically sits in, by
    point-in-polygon against geoBoundaries — the ADM2 district where there is one,
    else the ADM1 region, else the ADM0 country. Sets fc["district"] to the name and
    fc["districtLevel"] to which level it came from (2/1/0), so the panel can say
    what kind of place it is rather than calling a country a district. Both stay
    empty where there is no usable coordinate or no containing unit at any level."""
    from shapely import points as shapely_points

    fcs = list(forecasts)
    coords, idx = [], []
    for i, fc in enumerate(fcs):
        fc["district"] = ""                       # default for every forecast
        fc["districtLevel"] = ""
        try:
            coords.append((float(fc["lon"]), float(fc["lat"])))
            idx.append(i)
        except (KeyError, TypeError, ValueError):
            continue
    if not coords:
        return

    pts = shapely_points(coords)
    remaining = set(range(len(coords)))
    for level, fname in ADMIN_LEVELS:
        if not remaining:
            break
        found = _names_at_level(os.path.join(BOUNDARIES, fname), pts, remaining)
        for p_idx, nm in found.items():
            fcs[idx[p_idx]]["district"] = nm
            fcs[idx[p_idx]]["districtLevel"] = level
            remaining.discard(p_idx)
        if found:
            print(f"  ADM{level}: placed {len(found):,} point(s)"
                  + (f", {len(remaining):,} still unplaced" if remaining else ""))
    print(f"District lookup: {len(coords) - len(remaining):,}/{len(coords):,} "
          f"forecast point(s) matched.")


# ---- impact ----------------------------------------------------------------

def load_impacts(geom_by_key):
    """Area-weighted HUC12 impact statistics for every flagged cell.

    geom_by_key: {(res, cell_id): shapely polygon} for every cell we will draw.
    Returns {(res, cell_id): {population, buildings, ...}}.

    Basins can sum whole HUC12 units because a HUC12 nests inside its parent
    basin; an H3 hexagon does not respect basin boundaries, so each HUC12's
    statistics are split between the cells it overlaps, in proportion to the
    overlapping area. Areas are planar degrees — the ratio is taken between two
    overlapping shapes in the same place, so the projection distortion cancels.
    Each resolution is weighted against its own hexagon, so a coarse cell's total
    covers the whole hexagon drawn, the way a coarse basin's covers the whole basin.
    """
    import pyarrow.parquet as pq
    from shapely import from_wkb, STRtree

    if not os.path.isdir(IMPACT_DIR):
        print(f"  note: {IMPACT_DIR} not found; skipping impact.", file=sys.stderr)
        return {}
    if not os.path.exists(HUC12_PARQUET):
        print(f"  note: {HUC12_PARQUET} not found; skipping impact.", file=sys.stderr)
        return {}

    keys = list(geom_by_key)
    tree = STRtree([geom_by_key[k] for k in keys])

    # HYBAS_ID -> [(key index, share of this HUC12 inside that cell), ...]
    weights = {}
    pf = pq.ParquetFile(HUC12_PARQUET)
    scanned = 0
    for batch in pf.iter_batches(batch_size=HUC12_READ_BATCH,
                                 columns=["HYBAS_ID", "geometry"]):
        d = batch.to_pydict()
        geoms = from_wkb([bytes(g) for g in d["geometry"]])
        hybs = d["HYBAS_ID"]
        scanned += len(hybs)
        hi, ci = tree.query(geoms, predicate="intersects")
        for h_idx, c_idx in zip(hi, ci):
            g = geoms[h_idx]
            total = g.area
            if not total:
                continue
            try:
                share = g.intersection(geom_by_key[keys[c_idx]]).area / total
            except Exception:                     # invalid ring, self-intersection
                continue
            if share <= 0:
                continue
            weights.setdefault(hybs[h_idx], []).append((c_idx, share))
    print(f"Impact: {scanned:,} HUC12 basin(s) scanned, "
          f"{len(weights):,} overlap a flagged cell.")

    totals = {}
    matched_rows = 0
    for fname, colmap in IMPACT_FILES:
        path = os.path.join(IMPACT_DIR, fname)
        if not os.path.exists(path):
            print(f"  note: missing {fname}; skipped.", file=sys.stderr)
            continue
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                raw = (row.get("HYBAS_ID") or "").strip()
                if not raw or raw.upper() == "TOTAL":
                    continue
                try:
                    hyb = int(raw)
                except ValueError:
                    continue
                shares = weights.get(hyb)
                if not shares:
                    continue
                vals = {}
                for col, field in colmap.items():
                    try:
                        vals[field] = float(row.get(col) or 0)
                    except ValueError:
                        vals[field] = 0.0
                for c_idx, share in shares:
                    bucket = totals.setdefault(keys[c_idx],
                                               {k: 0.0 for k in IMPACT_FIELDS})
                    for field, v in vals.items():
                        bucket[field] += v * share
                matched_rows += 1
    print(f"Impact: {matched_rows:,} HUC12 row(s) fell inside a flagged cell.")
    return totals


def tidy(d):
    return {
        "population": round(d["population"]),
        "buildings": round(d["buildings"]),
        "farmland_m2": round(d["farmland_m2"]),
        "highway_km": round(d["highway_km"], 1),
        "railway_km": round(d["railway_km"], 1),
    }


# ---- build -----------------------------------------------------------------

def main():
    try:
        import pandas as pd
        from shapely.geometry import mapping
        from vgridpandas import h3pandas  # noqa: F401
        import h3
    except ImportError as e:
        sys.exit(
            f"Missing dependency: {e.name}. This build needs your GDAL/vgridpandas "
            f"environment:\n"
            f"    conda install -c conda-forge gdal geopandas\n"
            f"    pip install vgridpandas h3"
        )

    for p in (MATCHES_CSV, GEOGLOWS_CSV):
        if not os.path.exists(p):
            sys.exit(f"Not found: {p}")

    flooding = read_geoglows_flooding(GEOGLOWS_CSV)
    print(f"GEOGLOWS reaches flooding: {len(flooding):,}")

    base = load_matched_cells(MATCHES_CSV, flooding)
    print(f"Cells flagged (GEOGLOWS, res {BASE_RES}): {len(base):,}")

    gauges = read_flood_hub_points(FLOOD_HUB_CSV)
    print(f"Flood Hub alerts with coordinates: {len(gauges):,}")
    if gauges:
        for cell, fc in zip(cells_for_points(gauges, BASE_RES, pd), gauges):
            base.setdefault(cell, []).append(fc)

    # GloFAS reporting points bin by their own coordinate, exactly like Flood Hub
    # gauges: they sit on the GloFAS grid, not on GEOGLOWS reaches, so the
    # crosswalk has no entry for them to join through.
    glofas = read_glofas_points(GLOFAS_SHP)
    print(f"GloFAS points under alert: {len(glofas):,}")
    if glofas:
        for cell, fc in zip(cells_for_points(glofas, BASE_RES, pd), glofas):
            base.setdefault(cell, []).append(fc)

    print(f"Cells flagged (all models, res {BASE_RES}): {len(base):,}")

    if not base:
        sys.exit("No cells flagged — nothing to write.")

    # Attach each forecast's district (point-in-polygon). Every cell shares the
    # same forecast dicts, so one pass over the unique ones covers every level.
    unique_fcs = list({id(fc): fc for fcs in base.values() for fc in fcs}.values())
    assign_admin(unique_fcs)

    # Last resort for the handful the cascade cannot place — coastal and delta
    # points that fall just outside every CGAZ polygon. GloFAS and Flood Hub both
    # name the country on the forecast itself, so a point with no containing unit
    # can still say which country it is in rather than showing nothing at all.
    # Level 0 is what the panel already calls a country, so no new wording.
    rescued = 0
    for fc in unique_fcs:
        if not fc.get("district") and str(fc.get("country", "")).strip():
            fc["district"] = str(fc["country"]).strip()
            fc["districtLevel"] = 0
            rescued += 1
    if rescued:
        print(f"  fallback: {rescued:,} point(s) placed by the forecast's own country field")

    # Cells per resolution, then one geometry lookup per resolution.
    by_res = {res: rollup(base, res, h3) for res in RESOLUTIONS}
    geom_by_key = {}
    for res in RESOLUTIONS:
        cells = sorted(by_res[res])
        uniq = pd.DataFrame({"cell_id": cells})
        geoms = cells_to_geom(uniq, "cell_id", FIX_ANTIMERIDIAN)
        missing = 0
        for cell in cells:
            g = geoms.get(cell)
            if g is None:
                missing += 1
                continue
            geom_by_key[(res, cell)] = g
        print(f"  res {res}: {len(cells):,} cell(s)"
              + (f" ({missing:,} without geometry)" if missing else ""))

    impacts = load_impacts(geom_by_key)

    features = []
    for res in RESOLUTIONS:
        co_by_cell = cooccurring_sets(base, res, h3)
        for cell, fcs in by_res[res].items():
            g = geom_by_key.get((res, cell))
            if g is None:
                print(f"  warning: no geometry for cell {cell} (res {res})", file=sys.stderr)
                continue
            props = {
                "res": res, "cell_id": cell,
                "severity": worst_severity(fcs), "model_count": len(fcs),
                "forecasts": fcs,
            }
            co = co_by_cell.get(cell)
            if co:
                props["co_models"] = co
            imp = impacts.get((res, cell))
            if imp:
                props["impact"] = tidy(imp)
            features.append({"type": "Feature", "geometry": mapping(g),
                             "properties": props})

    with_co = sum(1 for f in features if "co_models" in f["properties"])
    print(f"Cells where models meet in one res-{BASE_RES} cell: {with_co:,}/{len(features):,}")
    with_impact = sum(1 for f in features if "impact" in f["properties"])
    print(f"Cells with impact totals: {with_impact:,}/{len(features):,}")

    payload = {
        "type": "FeatureCollection",
        "kind": "h3-telescoping",
        "resolutions": RESOLUTIONS,
        # Tells the front end that agreement is carried by `co_models` here, so a
        # cell without the field is NOT agreeing. Without this flag it would fall
        # back to "two models somewhere in this feature", which is exactly the
        # rolled-up over-reporting co_models exists to prevent.
        "agreement": "co_models",
        "features": features,
    }
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"Wrote {len(RESOLUTIONS)} resolution(s), {len(features):,} feature(s) "
          f"total -> {OUTPUT}")


if __name__ == "__main__":
    main()
