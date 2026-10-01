#!/usr/bin/env python3
"""Static build — the H3 cell geometry archive, as PMTiles.

Built once. The daily run never touches this: a release says WHICH cells are
flagged, and this archive says what a cell looks like. Rebuild it only when the
universe of cells changes, which in practice means when the impact table is
rebuilt.

The universe is exactly the impact table's res-6 cells plus their ancestors. That
keeps the archive to land drainage — about 4.4M hexagons rather than the 16.5M a
full global res-3-6 grid would need — and makes it self-consistent: any cell with
geometry also has impact.

One archive holding one layer per resolution. H3's resolutions already ARE the
level of detail, so each occupies its own zoom band and no tile is ever asked to
hold more than it can:

    res 3   13,242 cells   from z2    worst case  8,099 per tile
    res 4   82,827         from z4                3,544
    res 5  549,187         from z6                1,550
    res 6  3,751,739       from z8                  679

All far under GDAL's 200,000-feature ceiling, so nothing is ever dropped and the
tiler never has to make a judgement call.

Each resolution is still converted on its own — a single-layer write is the PMTiles
path GDAL supports without ogr2ogr's multi-layer CONF — and the four are then
merged by pmtiles_merge, which is ours and needs nothing installed. (tile-join
would also do it, but only on a recent enough tippecanoe, and a missing one used
to leave the build silently unmerged.) The layer names must differ (`cells_r3`
and so on), because
the bands overlap at z4, z6 and z8 and tile-join merges same-named layers: two
resolutions of hexagon would end up in one tile with nothing to separate them.

Geometry is emitted as FlatGeobuf — streaming, GDAL-native, no multi-gigabyte
GeoJSON intermediate — and handed to ogr2ogr. If ogr2ogr is not on PATH the
FlatGeobuf files are still written and the exact commands are printed, so the
conversion can be run wherever GDAL lives.

    python3 backend/static/build_cell_tiles.py              # everything
    python3 backend/static/build_cell_tiles.py --limit 50000  # a quick sample
"""

import csv
import os
import shutil
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import config as C          # noqa: E402

OUT_DIR = os.path.join(C.BACKEND, "static", "tiles")
# Which zoom each resolution starts being drawn at. Must agree with the front end;
# both read it from config so they cannot drift.
RES_ZOOM = C.RES_ZOOM        # one definition, shared with step 5
# Each resolution is tiled ONLY across the zoom band its layer draws in. Tiling
# every resolution to z11 is the expensive mistake here: res-3 hexagons would be
# rewritten into tiles at nine zoom levels nobody ever asks for, and res 6 — 3.75M
# cells — would run to roughly half a million tiles at z11 alone.
#
# Stopping a band early costs nothing, because MapLibre overzooms past a source's
# maxzoom by scaling its deepest tiles, and vector geometry stays sharp doing it.
# At z9 a tile's 4096-unit extent works out to ~19 m on the ground against a res-6
# hexagon edge of ~1.5 km, so there is nothing left to resolve by going deeper.
def band(res):
    """(minzoom, maxzoom) for one resolution's archive."""
    order = sorted(RES_ZOOM)
    lo = RES_ZOOM[res]
    i = order.index(res)
    hi = RES_ZOOM[order[i + 1]] if i + 1 < len(order) else lo + 1
    return lo, hi
# GDAL drops features from a tile that exceeds this, with only a warning. The
# default 500 KB is uncomfortably close to a full res-3 tile, so it is raised.
MAX_TILE_BYTES = "5000000"

# A hexagon's WKB, built directly rather than through shapely. At 4.4M cells the
# object overhead of a geometry library is the difference between a job that fits
# in memory and one that does not; the bytes themselves are trivial to write.
_HEAD = struct.Struct("<BIII")
_PT = struct.Struct("<dd")


def hexagon_wkb(boundary):
    """WKB Polygon for one H3 ring, or None if it wraps the antimeridian.

    A cell spanning the 180th meridian comes back with longitudes on both sides,
    and writing it as-is draws a band right across the map. There are only a
    handful and they sit in open Pacific, so they are dropped and counted rather
    than silently mangled.
    """
    lngs = [lng for _lat, lng in boundary]
    if max(lngs) - min(lngs) > 180:
        return None
    n = len(boundary) + 1
    out = [_HEAD.pack(1, 3, 1, n)]
    for lat, lng in boundary:
        out.append(_PT.pack(lng, lat))
    lat0, lng0 = boundary[0]
    out.append(_PT.pack(lng0, lat0))        # rings close
    return b"".join(out)


def read_universe(limit=None):
    """The res-6 cells the archive covers, and every ancestor of them."""
    import h3
    r6 = []
    with open(C.CELL_IMPACT_TABLE, newline="", encoding="utf-8-sig") as f:
        rd = csv.reader(f)
        next(rd)
        for row in rd:
            r6.append(row[0])
            if limit and len(r6) >= limit:
                break
    by_res = {6: r6}
    for res in (5, 4, 3):
        child = by_res[res + 1]
        by_res[res] = sorted({h3.cell_to_parent(c, res) for c in child})
    return by_res


def write_layer(res, cells, out_dir):
    """One resolution's hexagons to FlatGeobuf. Returns (path, written, skipped)."""
    import numpy as np
    import h3
    from pyogrio.raw import write

    geoms, ids, skipped = [], [], 0
    for c in cells:
        wkb = hexagon_wkb(h3.cell_to_boundary(c))
        if wkb is None:
            skipped += 1
            continue
        geoms.append(wkb)
        ids.append(c)

    path = os.path.join(out_dir, f"cells_r{res}.fgb")
    write(path,
          np.array(geoms, dtype=object),
          field_data=[np.array(ids, dtype=object)],
          fields=[C.CELLS_PROMOTE_ID],
          geometry_type="Polygon", crs="EPSG:4326", driver="FlatGeobuf")
    return path, len(ids), skipped


def ogr2ogr_command(fgb_path, res):
    """The conversion for one resolution, as a list ready for subprocess."""
    out = fgb_path.replace(".fgb", ".part.pmtiles")
    lo, hi = band(res)
    return [
        "ogr2ogr", "-f", "PMTiles", out, fgb_path,
        "-dsco", f"MINZOOM={lo}",
        "-dsco", f"MAXZOOM={hi}",
        "-dsco", f"MAX_SIZE={MAX_TILE_BYTES}",
        "-nln", C.CELLS_SOURCE_LAYER.format(res=res),
    ], out





def main(limit=None):
    os.makedirs(OUT_DIR, exist_ok=True)
    if not os.path.exists(C.CELL_IMPACT_TABLE):
        sys.exit(f"Not found: {C.CELL_IMPACT_TABLE}\nBuild the impact table first.")

    t0 = time.time()
    print("Reading the cell universe from the impact table...")
    by_res = read_universe(limit)
    for res in sorted(by_res):
        print(f"  res {res}: {len(by_res[res]):>10,} cell(s)")
    print(f"  {time.time() - t0:.1f}s\n")

    built = []
    for res in sorted(by_res):
        t = time.time()
        path, n, skipped = write_layer(res, by_res[res], OUT_DIR)
        built.append((res, path, n))
        print(f"  res {res}: {n:>10,} hexagon(s) -> {os.path.basename(path)}  "
              f"{os.path.getsize(path) / 1e6:>7.1f} MB  {time.time() - t:>5.1f}s"
              + (f"   [{skipped} antimeridian cell(s) dropped]" if skipped else ""))

    have_ogr = shutil.which("ogr2ogr") is not None
    print(f"\nogr2ogr: {'found' if have_ogr else 'NOT on PATH'}")

    parts, commands = [], []
    for res, path, _n in built:
        cmd, out = ogr2ogr_command(path, res)
        parts.append(out)
        commands.append(cmd)
        if not have_ogr:
            continue
        t = time.time()
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  res {res}: FAILED\n{r.stderr.strip()[:400]}")
            continue
        # GDAL warns rather than errors when it drops features from a tile, which
        # is the one failure mode that would quietly thin the archive.
        if "features" in r.stderr.lower() and "skip" in r.stderr.lower():
            print(f"  res {res}: WARNING — {r.stderr.strip()[:200]}")
        print(f"  res {res}: {os.path.basename(out)}  "
              f"{os.path.getsize(out) / 1e6:>7.1f} MB  {time.time() - t:>5.1f}s")

    combined = os.path.join(OUT_DIR, "cells.pmtiles")
    if not have_ogr:
        print("\nThe FlatGeobuf files are written. Run these where GDAL lives:\n")
        for c in commands:
            print("  " + " ".join(c))
        print(f"  python3 {os.path.relpath(__file__, C.ROOT)} --merge-only")
        return

    print("\nMerging into one archive...")
    merge_parts(parts, combined)


def merge_parts(parts, combined):
    """Fold the per-resolution archives into the one the app reads."""
    missing = [p for p in parts if not os.path.exists(p)]
    if missing:
        sys.exit("Missing: " + ", ".join(os.path.basename(p) for p in missing))

    from pmtiles_merge import merge
    merge(combined, parts)

    for p in parts:
        try:
            os.remove(p)          # the parts exist only to be merged
        except OSError as e:
            print(f"  (left {os.path.basename(p)} in place: {e.strerror})")


if __name__ == "__main__":
    if "--merge-only" in sys.argv:
        # The geometry is already tiled; just fold the parts together.
        parts = [os.path.join(OUT_DIR, f"cells_r{r}.part.pmtiles") for r in C.RESOLUTIONS]
        merge_parts(parts, os.path.join(OUT_DIR, "cells.pmtiles"))
        sys.exit(0)

    lim = None
    if "--limit" in sys.argv:
        lim = int(sys.argv[sys.argv.index("--limit") + 1])
    main(limit=lim)
