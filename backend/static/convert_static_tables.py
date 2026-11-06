#!/usr/bin/env python3
"""One-time: CSV -> parquet for the big static tables, and tidy static/ around them.

    *** RUN ONCE, THEN DELETE. Like the dated-tree migration, this exists to
    *** cross one change rather than to be part of the pipeline.

WHY PARQUET

These tables are read the same way every morning: pull two or three columns out
of several million rows. Measured on the impact table, doing exactly what step 4
does -- 260,000 wanted cells out of 2.3 million:

    csv, stdlib, streamed        1.46 s     89 MB
    json, columnar               5.16 s    131 MB
    parquet, pyarrow             0.47 s     16 MB

JSON is the worst of the three for this shape, for a structural reason: json.load
has to build every row as a Python object before you can discard one, where a CSV
reader streams and parquet never materialises a row you did not ask for.

WHAT MOVES WHERE

    tables/   the big derived tables. Rebuildable by the build_*.py beside them,
              which is what makes them safe to keep out of git.
    ref/      small files a person reads or edits: country codes, the patch list
              of cells the automated sources miss, the record of what the GEOGLOWS
              blackout box is swallowing, cached lookups.
    tiles/    the tile archives, unchanged.

The builders stay at the top of static/ because they are code, and this is a
code directory that happens to hold what its code produced.

    python3 backend/static/convert_static_tables.py --dry-run
    python3 backend/static/convert_static_tables.py --limit 2
    python3 backend/static/convert_static_tables.py
"""

import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from common import config as C          # noqa: E402

STATIC = os.path.join(C.BACKEND, "static")

# name -> folder. CSVs become parquet on the way; everything else is just moved.
TO_PARQUET = [
    ("stream_attrs.csv", STATIC),
    ("cell_impact_r6.csv", STATIC),
    ("cell_universe_r6.csv", STATIC),
    ("cell_universe_land_r6.csv", STATIC),
    ("hydrorivers_topology.csv", STATIC),
    ("h3_hydrorivers_r6.csv", STATIC),
    ("h3_streams_global_r6.csv", C.FILES),      # the crosswalk, read every run
]
JUST_MOVE = {
    "reach_points_v2.parquet": "tables",
    "country_codes.csv": "ref",
    "cell_universe_extra.csv": "ref",
    "geoglows_suppressed.csv": "ref",
    "flash_polygons.json": "ref",
    "flood_hub_gauge_models.json": "ref",
    "cell_impact_r6_metadata.csv": "ref",
}


def convert(src, dest, dry):
    import pyarrow.csv as pacsv
    import pyarrow.parquet as pq

    if os.path.exists(dest):
        return None, "already converted"
    if not os.path.exists(src):
        return None, "not on disk"
    before = os.path.getsize(src)
    if dry:
        return before, "would convert"
    t = time.time()
    tbl = pacsv.read_csv(src)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    pq.write_table(tbl, tmp, compression="zstd")
    os.replace(tmp, dest)
    after = os.path.getsize(dest)
    return before, (f"{before / 1e6:>6.0f} -> {after / 1e6:>5.0f} MB  "
                    f"{tbl.num_rows:>12,} rows  {time.time() - t:>5.1f}s")


def main():
    dry = "--dry-run" in sys.argv
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    print(f"{'DRY RUN — ' if dry else ''}converting static tables\n")
    done = 0
    for name, folder in TO_PARQUET:
        src = os.path.join(folder, name)
        # The crosswalk stays in Files/; everything else lands in static/tables/.
        out_dir = folder if folder == C.FILES else os.path.join(STATIC, "tables")
        dest = os.path.join(out_dir, name[:-4] + ".parquet")
        if limit is not None and done >= limit and not os.path.exists(dest):
            print(f"  {name:<28} (left for the next pass)")
            continue
        _before, note = convert(src, dest, dry)
        if note not in ("already converted", "not on disk"):
            done += 1
        print(f"  {name:<28} {note}")

    if dry:
        return
    for name, folder in JUST_MOVE.items():
        s = os.path.join(STATIC, name)
        d = os.path.join(STATIC, folder, name)
        if os.path.exists(s) and not os.path.exists(d):
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.move(s, d)
            print(f"  {name:<28} -> {folder}/")

    left = [n for n, f in TO_PARQUET
            if not os.path.exists(os.path.join(
                f if f == C.FILES else os.path.join(STATIC, "tables"),
                n[:-4] + ".parquet")) and os.path.exists(os.path.join(f, n))]
    if left:
        print(f"\n  {len(left)} table(s) left — run again to continue.")
    else:
        print("\n  All converted. The .csv originals are still in place; delete them")
        print("  once a run has read the parquets, with:")
        print("    python3 backend/static/convert_static_tables.py --drop-csv")


def drop_csv():
    freed = 0
    for name, folder in TO_PARQUET:
        src = os.path.join(folder, name)
        out_dir = folder if folder == C.FILES else os.path.join(STATIC, "tables")
        if os.path.exists(os.path.join(out_dir, name[:-4] + ".parquet")) \
                and os.path.exists(src):
            freed += os.path.getsize(src)
            os.remove(src)
            print(f"  removed {name}")
    print(f"  freed {freed / 1e9:.2f} GB")


if __name__ == "__main__":
    drop_csv() if "--drop-csv" in sys.argv else main()
