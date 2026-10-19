#!/usr/bin/env python3
"""Static build — the canonical cell registry.

THE ONE QUESTION THIS ANSWERS

Which res-6 cells exist, i.e. which ones the geometry archive must contain. That
is the only question. It deliberately says nothing about which cells are flagged
today, which have exposure numbers, or which any model covers — those are other
files' jobs, and conflating them is what produced the Hawaii bug: the tile build
took the impact table as its universe, so 36 cells flagged by GloFAS had no
hexagon at all and could not be drawn, hovered or clicked.

IT IS A UNION, AND IT IS ONLY EVER A UNION

Every source can ADD cells. No source can remove one. A source that has never
heard of a cell is silent about it, not opposed to it. That property is what
makes the registry safe to rebuild: adding a model, or swapping GEOGLOWS v2 for
v3, can only grow the set, so nothing already on the map can silently vanish.

The corollary is a rule for everything downstream: NEVER filter a forecast by the
registry. If a forecast ever names a cell the registry does not hold, the registry
is wrong, not the forecast. The moment something writes `if cell not in registry:
skip`, the Hawaii bug is back under a nicer name.

THE SOURCES

    land            every cell whose centre falls in a country polygon
                    (geoBoundaries CGAZ ADM0). This is the bulk of it and the
                    part that makes the registry independent of any model's
                    hydrography — the reason a v2-to-v3 switch will not touch it.
    the crosswalks  every cell a GEOGLOWS reach or a HydroRIVERS reach passes
                    through. These are NOT redundant with land: a river mouth or
                    a delta cell can have its centre offshore, and the land fill
                    takes a cell only when the polygon covers its centre.
    GloFAS          every cell holding a pixel of its 0.05-degree river network,
                    read as the MASK rather than a day's alerts — a pixel with no
                    members over threshold today can alert tomorrow, and a static
                    archive has to already hold it.

No ring expansion around the coast: the crosswalk and GloFAS sources already
supply the real offshore cells models can name, and dilating the whole coastline
would invent ocean cells no forecast will ever land on.

Antarctica is skipped by default: ~390k cells, no forecasting model covers it.

THE LAND FILL IS CACHED, AND RESUMABLE

It is the only slow part — roughly half an hour for 218 countries — and it does
not change between runs. So it is written to a cache the first time and read back
afterwards, and while it runs it checkpoints per country: the cells go to a part
file and the finished countries to a done file, so an interruption resumes rather
than starting over. The same bargain build_impact_table.py makes, for the same
reason. Delete the cache, or pass --refresh-land, to rebuild it.

A SOURCE THAT CANNOT BE READ IS SKIPPED, NEVER FATAL

Missing file, missing optional dependency, unreadable geometry: each source is
guarded and the build carries on with the rest, saying what it lost. The registry
is a union, so a missing source makes it smaller, not wrong — and losing half an
hour of land fill because an optional GloFAS reader was absent is exactly the
failure this is written to prevent.

    python3 backend/static/build_cell_universe.py
    python3 backend/static/build_cell_universe.py --refresh-land
    python3 backend/static/build_cell_universe.py --antarctica
    python3 backend/static/build_cell_universe.py --skip-land
"""

import csv
import glob
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common import config as C      # noqa: E402

STATIC = os.path.join(C.BACKEND, "static")
ADM0 = os.path.join(C.BOUNDARIES_DIR, "geoBoundariesCGAZ_ADM0.gpkg")
LAND_CACHE = os.path.join(STATIC, "cell_universe_land_r6.csv")
LAND_PART = os.path.join(STATIC, "cell_universe_land_r6.part.csv")
LAND_DONE = os.path.join(STATIC, "cell_universe_land_r6.done.csv")
SKIP_GROUPS = {"ATA"}              # Antarctica


def _read_ids(path, col=0):
    out = set()
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8-sig") as f:
        rd = csv.reader(f)
        next(rd, None)
        for row in rd:
            if len(row) > col and row[col]:
                out.add(row[col].strip())
    return out


def _write_ids(path, ids, header="h3_id"):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([header])
        for c in sorted(ids):
            w.writerow([c])
    os.replace(tmp, path)           # atomic: a crash cannot leave a half file


def land_cells(keep_antarctica=False, refresh=False):
    """Every res-6 cell whose centre falls inside a country polygon.

    Cached, and checkpointed per country while building.
    """
    if not refresh and os.path.exists(LAND_CACHE):
        cells = _read_ids(LAND_CACHE)
        print(f"  land: {len(cells):,} cell(s) from cache "
              f"({os.path.basename(LAND_CACHE)})")
        return cells
    if not os.path.exists(ADM0):
        print(f"  note: no {os.path.basename(ADM0)}; land source skipped",
              file=sys.stderr)
        return set()

    import h3
    from pyogrio import read_dataframe

    if refresh:
        for p in (LAND_PART, LAND_DONE):
            if os.path.exists(p):
                os.remove(p)

    done = _read_ids(LAND_DONE)
    cells = _read_ids(LAND_PART)
    if done:
        print(f"  land: resuming — {len(done)} country(ies) already done, "
              f"{len(cells):,} cell(s) held")

    df = read_dataframe(ADM0, columns=["shapeGroup", "shapeName"])
    t0, since_flush = time.time(), 0
    part = open(LAND_PART, "a", newline="", encoding="utf-8")
    donef = open(LAND_DONE, "a", newline="", encoding="utf-8")
    try:
        if os.path.getsize(LAND_PART) == 0:
            part.write("h3_id\n")
        if os.path.getsize(LAND_DONE) == 0:
            donef.write("key\n")
        for i, row in enumerate(df.itertuples(), 1):
            name = str(getattr(row, "shapeName", "?"))
            key = f"{i}:{getattr(row, 'shapeGroup', '')}"
            if key in done:
                continue
            if not keep_antarctica and getattr(row, "shapeGroup", "") in SKIP_GROUPS:
                print(f"  [{i:>3}/{len(df)}] {name[:28]:<28} skipped", flush=True)
                donef.write(key + "\n")
                continue
            try:
                got = h3.geo_to_cells(row.geometry.__geo_interface__, C.BASE_RES)
            except Exception as e:
                print(f"  [{i:>3}/{len(df)}] {name[:28]:<28} FAILED: {e}",
                      file=sys.stderr, flush=True)
                continue              # not marked done; a rerun retries it
            new = [c for c in got if c not in cells]
            cells.update(new)
            for c in new:
                part.write(c + "\n")
            donef.write(key + "\n")
            since_flush += len(new)
            if since_flush > 50_000 or i % 25 == 0:
                part.flush(); donef.flush(); since_flush = 0
                print(f"  [{i:>3}/{len(df)}] {name[:28]:<28} +{len(new):>7,}  "
                      f"total {len(cells):>9,}  {time.time() - t0:>5.0f}s", flush=True)
    finally:
        part.close(); donef.close()

    _write_ids(LAND_CACHE, cells)
    for p in (LAND_PART, LAND_DONE):
        if os.path.exists(p):
            os.remove(p)
    print(f"  land: {len(cells):,} cell(s) in {time.time() - t0:.0f}s "
          f"-> cached in {os.path.basename(LAND_CACHE)}")
    return cells


def csv_cells(path, col, label):
    if not os.path.exists(path):
        print(f"  note: no {os.path.basename(path)}; {label} skipped", file=sys.stderr)
        return set()
    t0 = time.time()
    out = _read_ids(path, col)
    print(f"  {label}: {len(out):,} cell(s) in {time.time() - t0:.0f}s")
    return out


def glofas_mask_cells(input_dir):
    """Every res-6 cell holding a GloFAS river pixel — the mask, not the alerts."""
    try:
        import numpy as np
        import netCDF4
        import h3
    except ImportError as e:
        print(f"  note: {e.name} not installed; GloFAS river mask skipped. "
              f"The registry is a union, so this only makes it smaller — but "
              f"install {e.name} and rerun to include it.", file=sys.stderr)
        return set()

    paths = sorted(glob.glob(os.path.join(input_dir, f"{C.GLOFAS_GRID_VAR}_*_AL_*.nc")))
    if not paths:
        print(f"  note: no GloFAS grids in {input_dir}; skipped", file=sys.stderr)
        return set()
    cells, t0 = set(), time.time()
    for path in paths:
        try:
            with netCDF4.Dataset(path) as d:
                lat = np.asarray(d.variables["lat"][:])
                lon = np.asarray(d.variables["lon"][:])
                v = d.variables[C.GLOFAS_GRID_VAR]
                arr = np.ma.filled(v[:], v._FillValue)
                iy, ix = np.nonzero(arr != v._FillValue)
                for y, x in zip(iy, ix):
                    cells.add(h3.latlng_to_cell(float(lat[y]), float(lon[x]), C.BASE_RES))
        except Exception as e:
            print(f"  note: {os.path.basename(path)} unreadable ({e}); skipped",
                  file=sys.stderr)
    print(f"  GloFAS river mask: {len(cells):,} cell(s) in {time.time() - t0:.0f}s")
    return cells


def main(keep_antarctica=False, skip_land=False, refresh_land=False, input_dir=None):
    t0 = time.time()
    print("Building the cell registry\n")

    def guarded(label, fn, *a):
        try:
            return fn(*a)
        except Exception as e:                      # a source must never be fatal
            print(f"  note: {label} failed ({e}); skipped", file=sys.stderr)
            return set()

    land = set() if skip_land else guarded(
        "land", land_cells, keep_antarctica, refresh_land)
    streams = guarded("GEOGLOWS crosswalk", csv_cells, C.CROSSWALK_CSV, 0,
                      "GEOGLOWS crosswalk")
    hydro = guarded("HydroRIVERS crosswalk", csv_cells, C.HYDRORIVERS_CROSSWALK_CSV, 0,
                    "HydroRIVERS crosswalk")
    impact = guarded("impact table", csv_cells, C.CELL_IMPACT_TABLE, 0, "impact table")
    glofas = guarded("GloFAS river mask", glofas_mask_cells, input_dir or C.INPUT_DIR)

    universe = set()
    print("\n  what each source CONTRIBUTES that nothing before it had:")
    for name, s in (("land", land), ("GEOGLOWS crosswalk", streams),
                    ("HydroRIVERS crosswalk", hydro), ("impact table", impact),
                    ("GloFAS river mask", glofas)):
        before = len(universe)
        universe |= s
        print(f"    {name:<24} +{len(universe) - before:>9,}   running {len(universe):>9,}")

    if not universe:
        sys.exit("No source produced a single cell — refusing to write an empty "
                 "registry over a good one.")

    if land:
        offshore = (streams | hydro | glofas) - land
        print(f"\n  cells a model knows that the land fill does not: {len(offshore):,}")

    _write_ids(C.CELL_UNIVERSE, universe)
    size = os.path.getsize(C.CELL_UNIVERSE) / 1e6
    print(f"\nWrote {C.CELL_UNIVERSE}")
    print(f"  {len(universe):,} res-{C.BASE_RES} cell(s)   {size:.0f} MB   "
          f"{time.time() - t0:.0f}s total")


if __name__ == "__main__":
    a = sys.argv[1:]
    folder = a[a.index("--input") + 1] if "--input" in a else None
    main("--antarctica" in a, "--skip-land" in a, "--refresh-land" in a, folder)
