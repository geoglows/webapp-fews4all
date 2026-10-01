#!/usr/bin/env python3
"""Daily step 5 — the map's style tables.

INPUTS
    backend/output/<release>/cells.csv        from step 4, for the run summary only

OUTPUTS
    backend/output/<release>/layers.csv       one row per resolution: which archive
                                              it reads and the zoom band it draws in
    backend/output/<release>/palettes.csv     one row per (palette, severity)
    backend/output/<release>/models.csv       one row per model: label and palette

CSV throughout, like every other table the pipeline publishes. Two things had to
change for that to be honest rather than JSON hidden inside a CSV cell:

  The cell filters are gone. They were 99.8% of the old map_rules.json — 19,954
  cell ids, listed once under `filters` and again inline in each layer — and
  cells.csv already holds exactly those ids with their resolutions. The front end
  builds the filter from the table it downloads anyway, so this deletes a
  duplicate rather than moving one.

  Paint expressions are gone too. They were nested MapLibre arrays, which is the
  one shape a CSV cannot hold without stuffing JSON into a field. They were also
  dead: the front end overrode them on every layer it added, because a palette
  swap has to rebuild them client-side regardless. What the backend actually owns
  is the palette VALUES and which model uses which — and those are tabular.

So the backend still decides what is on the map and what it may look like; the
front end assembles MapLibre's syntax from these tables, which is where knowledge
of MapLibre's syntax belongs.

Note this output no longer varies by release — it is configuration, and every run
writes the same three files. It stays here so a release folder is self-contained.
"""

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import config as C          # noqa: E402

LAYER_COLUMNS = ["res", "source_id", "url", "source_layer", "promote_id",
                 "minzoom", "maxzoom"]
PALETTE_COLUMNS = ["palette_id", "label", "severity", "color"]
MODEL_COLUMNS = ["model", "label", "palette", "sort_order"]


def layer_rows():
    """One row per resolution: its archive, and the zoom band it draws in.

    maxzoom is the next resolution's start — where this one hands over — and is
    left blank for the finest, which draws to the end. It must agree with the band
    the archive was tiled at; both come from C.RES_ZOOM for that reason.
    """
    order = sorted(C.RESOLUTIONS)
    rows = []
    for i, res in enumerate(order):
        lo = C.RES_ZOOM[res]
        hi = C.RES_ZOOM[order[i + 1]] if i + 1 < len(order) else ""
        # One source id and one url across every row: all four resolutions live in
        # a single archive now, told apart by source_layer rather than by file.
        rows.append([res, "cells", C.CELLS_TILES_URL,
                     C.CELLS_SOURCE_LAYER.format(res=res), C.CELLS_PROMOTE_ID, lo, hi])
    return rows


def palette_rows():
    return [[p["id"], p["label"], sev, p[sev]]
            for p in C.PALETTES for sev in ("warning", "danger", "extreme")]


def model_rows():
    return [[m, C.MODEL_LABELS.get(m, m),
             C.MODEL_PALETTE.get(m, C.DEFAULT_PALETTE), i]
            for i, m in enumerate(C.MODEL_ORDER)]


def write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(columns)
        w.writerows(rows)
    return os.path.getsize(path)


def main(release_dir=None):
    out_dir = release_dir or os.path.join(C.OUTPUT_DIR, sorted(os.listdir(C.OUTPUT_DIR))[0])
    cells_path = os.path.join(out_dir, "cells.csv")
    if not os.path.exists(cells_path):
        sys.exit(f"Not found: {cells_path}\nRun step 4 first.")

    print(f"Release: {os.path.basename(os.path.normpath(out_dir))}\n")

    per_res = {}
    with open(cells_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            per_res[int(row["res"])] = per_res.get(int(row["res"]), 0) + 1

    tables = [
        ("layers.csv", LAYER_COLUMNS, layer_rows()),
        ("palettes.csv", PALETTE_COLUMNS, palette_rows()),
        ("models.csv", MODEL_COLUMNS, model_rows()),
    ]
    written = [(name, len(rows), write_csv(os.path.join(out_dir, name), cols, rows))
               for name, cols, rows in tables]

    for res, _sid, _url, _sl, _pid, lo, hi in layer_rows():
        print(f"  res {res}: {per_res.get(res, 0):>7,} cell(s), drawn z{lo}"
              + (f"-{hi}" if hi != "" else "+"))
    print(f"\nWrote {out_dir}")
    for name, n, size in written:
        print(f"  {name:<16} {n:>4} row(s)  {size / 1024:>6.1f} KB")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
