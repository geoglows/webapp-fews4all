#!/usr/bin/env python3
"""Static build — HydroRIVERS topology and its H3 crosswalk.

Flood Hub reports gauges, not reaches, so its warnings land on the map as
isolated dots with nothing between them. Flood Hub is built on HydroSHEDS, and
HydroRIVERS v10 is the matching stream network, so the gap between two warned
gauges on one river can be filled by walking the network between them.

Two tables come out of one pass over the shapefile:

  hydrorivers_topology.csv   hyriv_id, next_down, hybas_l12, upland_skm, main_riv
      The network as a graph. `next_down` is the reach immediately downstream and
      is what the daily run walks; 0 means the reach is an outlet. `hybas_l12` is
      the join key for Flood Hub's own gauge ids, which are literally
      `hybas_<HYBAS_L12>` — 67 of today's 99 flagged gauges resolve by string
      match alone, no spatial search.

  h3_hydrorivers_r6.csv      h3_id, hyriv_id
      Which res-6 cells each reach passes through, so a traced path becomes cells.

The crosswalk is the expensive half. A reach's vertices are not enough to find the
cells it crosses: the median reach is 4 km and 62% are longer than 3 km, against a
hexagon about 6 km across, so a straight reach can enter and leave a cell without
placing a vertex in it. Points are interpolated along every segment at a fixed
spacing below half the cell width, which is what makes the cell list continuous.

Both stages are resumable in slices — the shapefile is 1.2 GB and this is often
run where a long-lived process is not guaranteed.

    python3 backend/static/build_hydrorivers.py --topology
    python3 backend/static/build_hydrorivers.py --crosswalk [--limit N]
"""

import csv
import math
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from common import config as C          # noqa: E402

READ_CHUNK = 200_000          # features per streamed read
# Half a res-6 cell's width, near enough. A hexagon at res 6 is about 6 km across
# its narrowest axis, so samples this close cannot step over one.
SAMPLE_KM = 1.5
TOPOLOGY_COLUMNS = ["hyriv_id", "next_down", "hybas_l12", "upland_skm", "main_riv"]
CROSSWALK_COLUMNS = ["h3_id", "hyriv_id"]


def _count(path):
    import pyogrio
    return pyogrio.read_info(path)["features"]


def build_topology(src, out):
    import pyogrio
    total = _count(src)
    print(f"  {os.path.basename(src)}: {total:,} reach(es)")
    t0 = time.time()
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(TOPOLOGY_COLUMNS)
        for start in range(0, total, READ_CHUNK):
            df = pyogrio.read_dataframe(
                src, columns=["HYRIV_ID", "NEXT_DOWN", "HYBAS_L12", "UPLAND_SKM", "MAIN_RIV"],
                read_geometry=False, skip_features=start, max_features=READ_CHUNK)
            if df.empty:
                break
            for row in df.itertuples(index=False):
                w.writerow([int(row.HYRIV_ID), int(row.NEXT_DOWN), int(row.HYBAS_L12),
                            row.UPLAND_SKM, int(row.MAIN_RIV)])
            print(f"    {min(start + READ_CHUNK, total):,}/{total:,}  "
                  f"{time.time() - t0:.0f}s", flush=True)
    print(f"\n  -> {out}  {os.path.getsize(out) / 1e6:.0f} MB  {time.time() - t0:.0f}s")


def cells_of(coords, h3, res):
    """Every res-6 cell a reach passes through, in order, duplicates collapsed.

    Interpolating rather than taking vertices alone: see the module docstring.
    """
    out = []
    last = None
    for i in range(len(coords) - 1):
        (x0, y0), (x1, y1) = coords[i], coords[i + 1]
        # Rough great-circle length of this segment, enough to choose a step count.
        dy = (y1 - y0) * 111.32
        dx = (x1 - x0) * 111.32 * math.cos(math.radians((y0 + y1) / 2))
        steps = max(1, int(math.hypot(dx, dy) / SAMPLE_KM))
        for s in range(steps + 1):
            t = s / steps
            c = h3.latlng_to_cell(y0 + (y1 - y0) * t, x0 + (x1 - x0) * t, res)
            if c != last:
                out.append(c)
                last = c
    return out


def build_crosswalk(src, out, limit=None):
    import h3
    import pyogrio
    total = _count(src)
    mark = out + ".progress"
    done = 0
    if os.path.exists(mark) and os.path.exists(out + ".raw"):
        try:
            done = int(open(mark).read().strip())
        except ValueError:
            done = 0
    if done >= total:
        print(f"  crosswalk sweep already complete ({done:,} reaches)")
        return _finish_crosswalk(out)

    end = total if limit is None else min(total, done + limit)
    print(f"  reaches {done:,} -> {end:,} of {total:,}")
    t0, rows = time.time(), 0
    with open(out + ".raw", "a" if done else "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        at = done
        while at < end:
            n = min(READ_CHUNK, end - at)
            df = pyogrio.read_dataframe(src, columns=["HYRIV_ID"],
                                        skip_features=at, max_features=n)
            if df.empty:
                break
            for row in df.itertuples(index=False):
                g = row.geometry
                if g is None:
                    continue
                parts = (g.geoms if g.geom_type == "MultiLineString" else [g])
                seen = set()
                for part in parts:
                    for cell in cells_of(list(part.coords), h3, C.BASE_RES):
                        if cell in seen:
                            continue
                        seen.add(cell)
                        w.writerow([cell, int(row.HYRIV_ID)])
                        rows += 1
            at += n
            with open(mark, "w") as m:
                m.write(str(at))
            print(f"    {at:,}/{total:,}  {rows:,} rows this pass  "
                  f"{time.time() - t0:.0f}s", flush=True)

    if at < total:
        print(f"  paused at {at:,}/{total:,}. Run again to continue.")
        return
    _finish_crosswalk(out)


def _finish_crosswalk(out):
    """Sort and dedupe on disk; the raw sweep repeats a cell per reach segment."""
    raw, tmp = out + ".raw", out + ".sorted"
    print("  sorting and deduplicating...", flush=True)
    tmpdir = os.environ.get("TMPDIR") or "/tmp"
    rc = subprocess.run(["sort", "-u", "-S", "256M", "-T", tmpdir, "-o", tmp, raw])
    if rc.returncode != 0:
        sys.exit("sort failed")
    with open(out, "w", encoding="utf-8") as o, open(tmp) as body:
        o.write(",".join(CROSSWALK_COLUMNS) + "\n")
        for line in body:
            o.write(line)
    for junk in (raw, tmp, out + ".progress"):
        try:
            os.remove(junk)
        except OSError:
            pass
    print(f"  -> {out}  {os.path.getsize(out) / 1e6:.0f} MB")


def main():
    src = C.HYDRORIVERS_SHP
    if not os.path.exists(src):
        sys.exit(f"Not found: {src}")
    if "--topology" in sys.argv:
        build_topology(src, C.HYDRORIVERS_TOPOLOGY_CSV)
    elif "--crosswalk" in sys.argv:
        lim = None
        if "--limit" in sys.argv:
            lim = int(sys.argv[sys.argv.index("--limit") + 1])
        build_crosswalk(src, C.HYDRORIVERS_CROSSWALK_CSV, lim)
    else:
        sys.exit("Pass --topology or --crosswalk")


if __name__ == "__main__":
    main()
