#!/usr/bin/env python3
"""Static build — where each GEOGLOWS v2 reach is.

WHY THIS FILE EXISTS

A reach's coordinate never changes, but it was arriving in a daily file. The
mapstyletable CSV we used to read carried `lat`/`lon` beside the forecast, so the
reach's location was being re-downloaded every morning as if it were news.

The map-tables in the bucket do not carry those columns at all — they are
`comid, mean, thickness, ret_per` and nothing else. That matters more than it
sounds: admin.assign places every forecast by coordinate alone, and GEOGLOWS has
no country field for the fallback to use, so without lat/lon every GEOGLOWS
forecast loses its district.

So the coordinates move to where static data belongs. Built once, read every day.

THE SOURCE

Any mapstyletable CSV that still carries lat/lon — one row per reach, one file per
timestep, so a single file covers the whole network. The v2 stream geopackage
would also serve, but it is far larger and needs pyogrio; this needs neither.

The output is parquet, sorted by comid and written in row groups, because the
daily read wants a few thousand rows out of ~6.8 million and parquet answers that
from row-group statistics instead of scanning: 0.67s against 6.85s for a streamed
gzip of the same data. The gzip was smaller on disk (67 MB against 94 MB) and
needed no dependencies, which was the original reason to prefer it — but reading
the daily map-tables already requires pyarrow, so that saving buys nothing.

    python3 backend/static/build_reach_points.py
    python3 backend/static/build_reach_points.py --source <a mapstyletable CSV>
"""

import csv
import glob
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common import config as C      # noqa: E402

PLACES = 5          # ~1 m at the equator; the reaches are not surveyed to more


def find_source(explicit=None):
    if explicit:
        return explicit
    hits = sorted(glob.glob(os.path.join(C.INPUT_DIR, "mapstyletable*.csv")))
    return hits[-1] if hits else None


def read_points(path):
    """{comid: (lat, lon)} for every reach the file names.

    Later rows win, but they should not differ — a reach has one coordinate, and a
    disagreement means the source is not what we think it is, so it is counted and
    reported rather than silently resolved.
    """
    pts, conflicts, skipped = {}, 0, 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        if not {"comid", "lat", "lon"} <= set(rd.fieldnames or []):
            sys.exit(f"{os.path.basename(path)} has no lat/lon columns "
                     f"(found: {', '.join(rd.fieldnames or [])}).\n"
                     f"Point --source at a mapstyletable CSV that still carries them.")
        for row in rd:
            try:
                cid = int(float(row["comid"]))
                la = round(float(row["lat"]), PLACES)
                lo = round(float(row["lon"]), PLACES)
            except (TypeError, ValueError):
                skipped += 1
                continue
            if cid in pts and pts[cid] != (la, lo):
                conflicts += 1
            pts[cid] = (la, lo)
    return pts, conflicts, skipped


def write_points(pts, path):
    """Sorted by comid and written atomically.

    Sorted because the daily lookup pushes an `isin` filter down to the reader,
    and a sorted column gives every row group a tight min/max — which is what lets
    it skip most of the file instead of scanning it. Sorting also makes the table
    diffable between network versions.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    ids = sorted(pts)
    table = pa.table({
        "comid": pa.array(ids, pa.int32()),
        "lat": pa.array([pts[c][0] for c in ids], pa.float32()),
        "lon": pa.array([pts[c][1] for c in ids], pa.float32()),
    })
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    pq.write_table(table, tmp, compression="zstd", row_group_size=200_000,
                   sorting_columns=[pq.SortingColumn(0)])
    os.replace(tmp, path)
    return os.path.getsize(path)


def main(source=None):
    t0 = time.time()
    src = find_source(source)
    if not src or not os.path.exists(src):
        sys.exit(f"No mapstyletable CSV found in {C.INPUT_DIR}. "
                 f"Pass --source <path> to one that carries lat/lon.")
    print(f"Source: {src}  ({os.path.getsize(src) / 1e6:.0f} MB)")

    pts, conflicts, skipped = read_points(src)
    if not pts:
        sys.exit("No reaches read — refusing to write an empty table over a good one.")
    print(f"  {len(pts):,} reach(es) read in {time.time() - t0:.0f}s"
          + (f", {skipped:,} row(s) unparseable" if skipped else ""))
    if conflicts:
        print(f"  note: {conflicts:,} reach(es) appeared twice with different "
              f"coordinates; the last was kept", file=sys.stderr)

    size = write_points(pts, C.REACH_POINTS)
    print(f"\nWrote {C.REACH_POINTS}")
    print(f"  {len(pts):,} reach(es)   {size / 1e6:.0f} MB   "
          f"{time.time() - t0:.0f}s total")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[a.index("--source") + 1] if "--source" in a else None)
