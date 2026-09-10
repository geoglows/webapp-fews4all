#!/usr/bin/env python3
"""
build_context.py — secondary context layers tied to the flagged basins.

The basins (build_basins.py -> data_basins.geojson) stay the primary hazard layer.
This script keeps the river segments and administrative districts tied to those
flagged basins so the front end can switch them on as context without drowning the
map in the whole planet's rivers or borders:

  * streams   — segments intersecting a flagged basin ("in_basin"), PLUS every
                segment on the downstream path from them, followed via NEXT_DOWN to
                the outlet ("downstream"), so the flood's downstream reach is visible.
  * districts — ADM2 units intersecting a flagged basin.

Every feature is tagged with the id(s) of the basin(s) it belongs to (so the front
end shows it only for the selected basin) and the worst severity of those basins.

Reads:  ../data_basins.geojson                              (flagged basins)
        ../Files/HydroRIVERS_v10_shp/**/HydroRIVERS_v10.shp  (8.48M segments, WGS84)
        ../Files/International_boundaries/geoBoundariesCGAZ_ADM2.gpkg
Writes: ../data_basin_streams.geojson     kind "streams"
        ../data_basin_districts.geojson   kind "districts-context"
"""

import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                        # <repo>
FILES = os.path.join(ROOT, "Files")

PUBLIC = os.path.join(ROOT, "public")   # the app reads its data from here
os.makedirs(PUBLIC, exist_ok=True)
BASINS_FILE = os.path.join(PUBLIC, "data_basins.geojson")   # input, from build_basins.py
OUT_STREAMS = os.path.join(PUBLIC, "data_basin_streams.geojson")
OUT_DISTRICTS = os.path.join(PUBLIC, "data_basin_districts.geojson")

_rivers = glob.glob(os.path.join(FILES, "HydroRIVERS_v10_shp", "**", "HydroRIVERS_v10.shp"),
                    recursive=True)
HYDRORIVERS_SHP = _rivers[0] if _rivers else os.path.join(FILES, "HydroRIVERS_v10.shp")
# ADM2 = municipalities/districts (finer, more locally recognizable). Swap to ADM1
# for provinces/regions.
DISTRICTS_GPKG = os.path.join(FILES, "International_boundaries", "geoBoundariesCGAZ_ADM2.gpkg")

# Optional (min_lon, min_lat, max_lon, max_lat) to build one region only. None = global.
# Note: with global data the downstream trace can leave a BBOX, so BBOX is best for
# quick tests rather than a faithful downstream network.
BBOX = None
READ_BATCH = 100_000
# Safety cap on how many segments to follow downstream from one seed (rivers to the
# ocean can be long; this just prevents pathological loops / runaway chains).
MAX_DOWNSTREAM_STEPS = 3000

SEVERITY_RANK = {"none": 0, "warning": 1, "danger": 2, "extreme": 3}


def worst_severity(sevs):
    best, best_rank = "", -1
    for s in sevs:
        r = SEVERITY_RANK.get(str(s or "").lower(), -1)
        if r > best_rank:
            best_rank, best = r, str(s or "").lower()
    return best


def load_basins():
    """Flagged basin polygons + their severities, plus an STRtree over them."""
    from shapely.geometry import shape
    from shapely import STRtree

    if not os.path.exists(BASINS_FILE):
        sys.exit(f"Not found: {BASINS_FILE}\nRun build_basins.py first.")
    data = json.load(open(BASINS_FILE, encoding="utf-8"))
    geoms, sevs, ids = [], [], []
    for f in data.get("features", []):
        g = f.get("geometry")
        if not g:
            continue
        props = f.get("properties") or {}
        geoms.append(shape(g))
        sevs.append(props.get("severity", ""))
        ids.append(str(props.get("basin_id") or props.get("cell_id") or ""))
    if not geoms:
        sys.exit("data_basins.geojson has no basin features.")
    print(f"Flagged basins: {len(geoms):,}")
    return geoms, sevs, ids, STRtree(geoms)


def _worse(a, b):
    """Return whichever severity ranks higher."""
    return a if SEVERITY_RANK.get(str(a or "").lower(), -1) >= \
        SEVERITY_RANK.get(str(b or "").lower(), -1) else b


def _iter_batches(path, columns):
    """Yield read() results for a shapefile, one BBOX read or many streamed batches."""
    from pyogrio.raw import read
    read_kw = dict(columns=columns)
    if BBOX:
        read_kw["bbox"] = BBOX
        yield read(path, **read_kw)
        return
    skip = 0
    while True:
        res = read(path, skip_features=skip, max_features=READ_BATCH, **read_kw)
        n = len(res[2])                     # res = (meta, fid, geometry, fields)
        if n == 0:
            return
        yield res
        skip += n
        if n < READ_BATCH:
            return


def trace_downstream(seed_basins, seed_sev, next_of):
    """From every seed segment, walk the NEXT_DOWN chain to the outlet, tagging each
    segment on the way with the seed's basin(s) and severity.

    Returns {HYRIV_ID: {"basins": set, "sev": str, "seed": bool}} covering seeds and
    all their downstream segments.
    """
    info = {}
    for hid, basins in seed_basins.items():
        e = info.setdefault(hid, {"basins": set(), "sev": "", "seed": False})
        e["basins"] |= basins
        e["seed"] = True
        e["sev"] = _worse(e["sev"], seed_sev[hid])
    for hid, basins in seed_basins.items():
        sev = seed_sev[hid]
        cur, seen, steps = hid, {hid}, 0
        while steps < MAX_DOWNSTREAM_STEPS:
            nd = next_of.get(cur, 0)
            if not nd or nd in seen:        # 0 = outlet; guard against cycles
                break
            seen.add(nd)
            e = info.setdefault(nd, {"basins": set(), "sev": "", "seed": False})
            e["basins"] |= basins
            e["sev"] = _worse(e["sev"], sev)
            cur, steps = nd, steps + 1
    return info


def build_streams(basin_sevs, basin_ids, tree):
    """Segments intersecting a flagged basin ('in_basin'), plus the full downstream
    path from each (following NEXT_DOWN, tagged 'downstream'). Every segment keeps the
    basin id(s) it belongs to so the front end shows it for the selected basin."""
    from shapely import from_wkb
    from shapely.geometry import mapping

    if not os.path.exists(HYDRORIVERS_SHP):
        sys.exit(f"Not found: {HYDRORIVERS_SHP}")

    # --- pass 1: build the NEXT_DOWN lookup + find the seeds (segments in basins) ---
    next_of = {}                 # HYRIV_ID -> NEXT_DOWN (0 = outlet)
    seed_basins = {}             # HYRIV_ID -> set(basin ids)
    seed_sev = {}                # HYRIV_ID -> worst basin severity
    seed_geom = {}               # HYRIV_ID -> geometry
    seed_ord = {}                # HYRIV_ID -> ORD_STRA (Strahler order, for LOD)
    scanned = 0
    for meta, _fid, geom, fields in _iter_batches(HYDRORIVERS_SHP,
                                                  ["HYRIV_ID", "NEXT_DOWN", "ORD_STRA"]):
        col = {name: i for i, name in enumerate(meta["fields"])}
        ids, nexts, ords = fields[col["HYRIV_ID"]], fields[col["NEXT_DOWN"]], fields[col["ORD_STRA"]]
        for hid, nd in zip(ids, nexts):
            next_of[int(hid)] = int(nd)
        segs = from_wkb([bytes(g) for g in geom])
        seg_i, basin_i = tree.query(segs, predicate="intersects")
        per = {}
        for si, bi in zip(seg_i, basin_i):
            per.setdefault(int(si), []).append(bi)
        for si, bidxs in per.items():
            hid = int(ids[si])
            seed_basins.setdefault(hid, set()).update(
                basin_ids[b] for b in bidxs if basin_ids[b])
            seed_sev[hid] = _worse(seed_sev.get(hid, ""),
                                   worst_severity([basin_sevs[b] for b in bidxs]))
            seed_geom[hid] = segs[si]
            seed_ord[hid] = int(ords[si])
        scanned += len(ids)
        if not BBOX and scanned % 1_000_000 == 0:
            print(f"  ...scanned {scanned:,} segments, {len(seed_basins):,} seeds")
    print(f"Streams pass 1: scanned {scanned:,}; {len(seed_basins):,} seed segment(s).")

    # --- trace the downstream network from the seeds -------------------------------
    info = trace_downstream(seed_basins, seed_sev, next_of)
    downstream_needed = {hid for hid, e in info.items()
                         if not e["seed"] and hid not in seed_geom}
    print(f"Streams: {len(info):,} segment(s) after downstream trace "
          f"({len(downstream_needed):,} downstream geometries to fetch).")

    # --- pass 2: geometry for the downstream segments ------------------------------
    ds_geom, ds_ord = {}, {}
    if downstream_needed:
        for meta, _fid, geom, fields in _iter_batches(HYDRORIVERS_SHP, ["HYRIV_ID", "ORD_STRA"]):
            col = {name: i for i, name in enumerate(meta["fields"])}
            ids, ords = fields[col["HYRIV_ID"]], fields[col["ORD_STRA"]]
            want = [i for i, h in enumerate(ids) if int(h) in downstream_needed]
            if not want:
                continue
            gg = from_wkb([bytes(geom[i]) for i in want])
            for k, i in enumerate(want):
                ds_geom[int(ids[i])] = gg[k]
                ds_ord[int(ids[i])] = int(ords[i])
            if len(ds_geom) >= len(downstream_needed):
                break

    # --- emit ----------------------------------------------------------------------
    features = []
    for hid, e in info.items():
        g = seed_geom.get(hid) or ds_geom.get(hid)
        if g is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": mapping(g),
            "properties": {
                "HYRIV_ID": hid,
                "severity": e["sev"],
                # basins: every flagged basin this reach belongs to (physical
                # membership OR downstream of), used to show it for a selection.
                "basins": sorted(e["basins"]),
                # in_basins: only the basin(s) this reach physically sits inside.
                # Empty for pure downstream-trace reaches. Lets the front end tell,
                # per selected basin, the in-basin channel from the downstream trace
                # (the global `reach` label can't, since a reach may be a seed for
                # one basin while being downstream of another).
                "in_basins": sorted(seed_basins.get(hid, ())),
                "reach": "in_basin" if e["seed"] else "downstream",
                "ord": seed_ord.get(hid) or ds_ord.get(hid) or 1,  # Strahler order (LOD)
            },
        })
    print(f"Streams: {len(features):,} feature(s) written "
          f"({sum(1 for f in features if f['properties']['reach']=='downstream'):,} downstream).")
    return features


def build_districts(basin_sevs, basin_ids, tree):
    """ADM2 districts that intersect a flagged basin. Each keeps the id(s) of the
    basin(s) it touches so the front end can show it only for the selected one."""
    from pyogrio.raw import read
    from shapely import from_wkb
    from shapely.geometry import mapping

    if not os.path.exists(DISTRICTS_GPKG):
        sys.exit(f"Not found: {DISTRICTS_GPKG}")

    read_kw = dict(columns=["shapeName", "shapeID", "shapeGroup"])
    if BBOX:
        read_kw["bbox"] = BBOX

    features = []
    total = 0
    skip = 0
    while True:
        meta, _fid, geom, fields = read(DISTRICTS_GPKG, skip_features=skip,
                                        max_features=READ_BATCH, **read_kw)
        n = len(geom)
        if n == 0:
            break
        col = {name: i for i, name in enumerate(meta["fields"])}
        names, ids, groups = fields[col["shapeName"]], fields[col["shapeID"]], fields[col["shapeGroup"]]
        polys = from_wkb([bytes(g) for g in geom])
        dist_i, basin_i = tree.query(polys, predicate="intersects")
        per_dist = {}
        for di, bi in zip(dist_i, basin_i):
            per_dist.setdefault(int(di), []).append(bi)
        for di, bidxs in per_dist.items():
            nm = (names[di] or "").strip() or str(ids[di])
            features.append({
                "type": "Feature",
                "geometry": mapping(polys[di]),
                "properties": {
                    "name": nm,
                    "admin_id": str(ids[di]),
                    "country": (groups[di] or "").strip(),
                    "severity": worst_severity([basin_sevs[b] for b in bidxs]),
                    "basins": sorted({basin_ids[b] for b in bidxs if basin_ids[b]}),
                },
            })
        total += n
        skip += n
        if n < READ_BATCH:
            break

    print(f"Districts: scanned {total:,}; kept {len(features):,} intersecting flagged basins.")
    return features


def write(path, kind, features):
    fc = {"type": "FeatureCollection", "kind": kind, "features": features}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False)
    print(f"Wrote {len(features):,} feature(s) -> {path}")


def main():
    _geoms, sevs, ids, tree = load_basins()
    write(OUT_STREAMS, "streams", build_streams(sevs, ids, tree))
    write(OUT_DISTRICTS, "districts-context", build_districts(sevs, ids, tree))


if __name__ == "__main__":
    main()
