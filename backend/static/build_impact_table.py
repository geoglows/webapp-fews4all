#!/usr/bin/env python3
"""Static build — the res-6 cell impact table.

Impact never changes between forecast releases, so it is computed once here and
joined by the daily run rather than recomputed every morning. This was the slowest
step in the old GeoJSON pipeline; taking it off the daily path is most of why a
daily run is now measured in seconds.

What it does: the impact statistics are published per HUC12 basin, and the map
reports per H3 cell, so each basin's totals are apportioned to the cells it covers
by the share of the basin's area falling in each. Area-weighting rather than an
even split matters because a basin routinely spans cells it barely touches, and an
even split would move population into a hexagon holding a sliver of farmland.

The candidate cells are the polygon fill EXPANDED BY ONE RING, which is the whole
trick. H3's fill takes a cell only when the basin covers that cell's centre, so
every cell clipping the basin's edge is excluded — and since HUC12 basins are close
in size to a res-6 hexagon, that is not a rounding error: measured over 360 basins,
a median 27.7% of basin area (p90 46.6%) falls in cells the fill leaves out, and
normalising without them shoves that impact inward toward the basin's middle. The
ring brings the edge cells back as candidates; true intersection area then decides
what each actually gets, and candidates with no overlap drop out on their own.

Only res 6 is built. Population, buildings, farmland and road/rail length are all
additive, so the coarser resolutions are the sum of a cell's children — arithmetic
step 3 can do for free, and one fewer thing to keep in step here.

The job is long (a million basins), so it is chunked by parquet row group and
resumable: each chunk writes its own partial file and a completed chunk is never
redone. Rerun the script until it reports done, then it merges.

    python3 backend/static/build_impact_table.py            # run until done
    python3 backend/static/build_impact_table.py --budget 150   # one bounded pass
"""

import csv
import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import config as C          # noqa: E402

BASINS_DIR = os.path.join(C.FILES, "Basins")
IMPACT_DIR = os.path.join(C.FILES, "Impact")
HUC12_PARQUET = os.path.join(BASINS_DIR, "HUC12.parquet")
WORK = os.path.join(C.BACKEND, "static", ".impact_work_v2")
OUT = os.path.join(C.BACKEND, "static", "cell_impact_r6.csv")

# (file, {csv column: impact field}) — HYBAS_ID keyed, with a TOTAL row to skip.
IMPACT_FILES = [
    ("population_statistics.csv", {"pop_value": "population"}),
    ("building_statistics.csv", {"building_count": "buildings"}),
    ("farmland_statistics.csv", {"area_m2": "farmland_m2"}),
    ("transportation_statistics.csv", {"highway_km": "highway_km",
                                       "railway_km": "railway_km"}),
]
IMPACT_FIELDS = ["population", "buildings", "farmland_m2", "highway_km", "railway_km"]


def load_impact_by_basin():
    """{HYBAS_ID: {field: value}} from the per-basin statistics files.

    Cached to a pickle beside the work folder. Parsing four CSVs costs ~10s, which
    is nothing in one long run but real when a resumable build is restarted many
    times — and the statistics do not change between chunks by definition.
    """
    import pickle
    cache = os.path.join(WORK, "_basin_impact.pkl")
    if os.path.exists(cache):
        with open(cache, "rb") as f:
            out = pickle.load(f)
        print(f"  cached: {len(out):,} basin(s)")
        return out
    out = _parse_impact_csvs()
    os.makedirs(WORK, exist_ok=True)
    with open(cache, "wb") as f:
        pickle.dump(out, f, protocol=pickle.HIGHEST_PROTOCOL)
    return out


def _parse_impact_csvs():
    out = {}
    for fname, mapping in IMPACT_FILES:
        path = os.path.join(IMPACT_DIR, fname)
        if not os.path.exists(path):
            print(f"  note: {fname} not found — its fields stay zero", file=sys.stderr)
            continue
        n = 0
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                raw = (row.get("HYBAS_ID") or "").strip()
                if not raw or raw.upper() == "TOTAL":
                    continue
                try:
                    key = int(float(raw))
                except ValueError:
                    continue
                rec = out.setdefault(key, {})
                for col, field in mapping.items():
                    try:
                        rec[field] = float(row.get(col) or 0)
                    except ValueError:
                        pass
                n += 1
        print(f"  {fname:<32} {n:>9,} basin row(s)")
    return out


def process_group(table, impact_by_basin, writer):
    """Apportion one row group's basins across the res-6 cells they cover."""
    import h3
    from shapely import from_wkb
    from shapely.geometry import mapping

    ids = table.column("HYBAS_ID").to_pylist()
    geoms = from_wkb(table.column("geometry").to_pylist())
    written = 0
    for hyb, geom in zip(ids, geoms):
        try:
            stats = impact_by_basin.get(int(hyb))
        except (TypeError, ValueError):
            continue
        if not stats or geom is None or geom.is_empty:
            continue
        total = geom.area
        if total <= 0:
            continue
        try:
            cells = list(h3.geo_to_cells(mapping(geom), C.BASE_RES))
        except (ValueError, TypeError):
            cells = []
        if not cells:
            # A basin smaller than a hexagon, or a sliver missing every cell
            # centre, still has to land somewhere or its impact vanishes.
            try:
                pt = geom.representative_point()
                cells = [h3.latlng_to_cell(pt.y, pt.x, C.BASE_RES)]
            except Exception:
                continue
        # Expand to the ring in BOTH cases — a filled basin and a fallback point
        # alike. This is the correction: applying it only on the fallback path
        # would leave every ordinary basin with its edge cells still missing.
        cells = _with_ring(cells, h3)
        # Share of the basin's area inside each candidate cell. Normalised at the
        # end so a basin's totals are conserved whatever the candidate set covers
        # — impact is never created or lost, only divided.
        parts = []
        for cell in cells:
            ring = h3.cell_to_boundary(cell)
            poly = _poly([(lng, lat) for lat, lng in ring])
            try:
                parts.append((cell, geom.intersection(poly).area))
            except Exception:
                parts.append((cell, 0.0))
        s = sum(a for _c, a in parts)
        shares = [(c, a / s) for c, a in parts if a > 0] if s > 0 else \
                 [(c, 1.0 / len(cells)) for c in cells]
        for cell, w in shares:
            writer.writerow([cell] + [round(stats.get(f, 0) * w, 4) for f in IMPACT_FIELDS])
            written += 1
    return written


def _with_ring(cells, h3):
    """The filled cells plus one ring around them — the edge cells the centre-in
    fill drops. Overlap is tested for real afterwards, so a candidate that turns
    out not to touch the basin simply contributes nothing."""
    out = set(cells)
    for c in cells:
        try:
            out |= set(h3.grid_ring(c, 1))
        except Exception:
            pass
    return list(out)


def _poly(coords):
    from shapely.geometry import Polygon
    return Polygon(coords)


def merge(done_files):
    """Sum every partial into one row per cell."""
    import pandas as pd
    total = None
    for i, path in enumerate(sorted(done_files)):
        df = pd.read_csv(path)
        total = df if total is None else pd.concat([total, df], ignore_index=True)
        if len(total) > 4_000_000:                 # keep memory flat
            total = total.groupby("h3_id", as_index=False)[IMPACT_FIELDS].sum()
    out = total.groupby("h3_id", as_index=False)[IMPACT_FIELDS].sum()
    out[["population", "buildings"]] = out[["population", "buildings"]].round().astype("int64")
    for c in ["farmland_m2", "highway_km", "railway_km"]:
        out[c] = out[c].round(3)
    out.to_csv(OUT, index=False)
    return out


def main(budget=None):
    import pyarrow.parquet as pq

    os.makedirs(WORK, exist_ok=True)
    print("Loading impact statistics:")
    impact_by_basin = load_impact_by_basin()
    print(f"  {len(impact_by_basin):,} basin(s) carry impact\n")

    pf = pq.ParquetFile(HUC12_PARQUET)
    n_groups = pf.metadata.num_row_groups
    done = {int(os.path.basename(p).split(".")[0]) for p in glob.glob(os.path.join(WORK, "*.csv"))}
    todo = [g for g in range(n_groups) if g not in done]
    print(f"Row groups: {n_groups} total, {len(done)} done, {len(todo)} to go")

    t_start = time.time()
    for g in todo:
        if budget and time.time() - t_start > budget:
            break
        t0 = time.time()
        table = pf.read_row_group(g, columns=["HYBAS_ID", "geometry"])
        part = os.path.join(WORK, f"{g:05d}.csv.tmp")
        with open(part, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["h3_id"] + IMPACT_FIELDS)
            rows = process_group(table, impact_by_basin, w)
        os.replace(part, part[:-4])        # only a finished chunk counts as done
        print(f"  group {g:>5}/{n_groups}  {table.num_rows:>7,} basin(s) -> "
              f"{rows:>8,} row(s)  {time.time() - t0:>5.1f}s")

    done = glob.glob(os.path.join(WORK, "*.csv"))
    if len(done) < n_groups:
        print(f"\n{len(done)}/{n_groups} row groups done — rerun to continue.")
        return
    print(f"\nAll {n_groups} row groups done. Merging...")
    out = merge(done)
    print(f"Wrote {OUT}")
    print(f"  {len(out):,} res-6 cell(s)  {os.path.getsize(OUT) / 1e6:.1f} MB")


if __name__ == "__main__":
    b = None
    if "--budget" in sys.argv:
        b = float(sys.argv[sys.argv.index("--budget") + 1])
    main(budget=b)
