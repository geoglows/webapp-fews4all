#!/usr/bin/env python3
"""Daily step 4 — enrich, then write the delivered summary.

Two joins and a copy. Neither join computes anything geospatial from scratch: the
expensive halves were done once in the static tier, and this step spends its time
looking things up rather than intersecting polygons.

  impact onto cells        from the static res-6 table. A cell's impact is that of
                           its WHOLE footprint, not just the part that happens to
                           be flagged — a res-3 cell is drawn whole on the map, so
                           the population it reports is everyone inside it. That
                           means summing every res-6 descendant in the table, not
                           only the flagged ones.

  Near onto forecasts      point-in-polygon against geoBoundaries, ADM2 then ADM1
                           then ADM0. Kept on the forecast's own coordinate rather
                           than the containing cell's, so "Near" keeps meaning the
                           place the forecast is about — a long reach crosses many
                           districts, and naming the cell's would be a different
                           and vaguer claim.

INPUTS
    backend/output/<release>/cells.csv            from step 3
    backend/output/<release>/forecasts.csv        from step 2
    backend/static/cell_impact_r6.csv             static, built once
    Files/International_boundaries/*.gpkg         geoBoundaries CGAZ ADM0/1/2

OUTPUTS
    backend/output/<release>/cells.csv            same file + impact columns
    backend/output/<release>/forecasts.csv        same file + district columns

Both are rewritten in place. There is no separate delivered copy: every column
this step adds is new, and it reads only the columns steps 2 and 3 wrote, so a
rerun is idempotent and the release folder holds exactly one of each table.
"""

import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import admin               # noqa: E402
from common import config as C         # noqa: E402


def needed_base_cells(cell_rows):
    """Every res-6 cell under any cell in the release, keyed by the row it feeds.

    Built before the impact table is opened so the 3.7M-row table can be streamed
    and filtered down to the few hundred thousand cells this release can possibly
    need, instead of being loaded whole.
    """
    import h3
    want, by_row = set(), []
    for r in cell_rows:
        res, cell = int(r["res"]), r["h3_id"]
        kids = [cell] if res == C.BASE_RES else list(h3.cell_to_children(cell, C.BASE_RES))
        by_row.append(kids)
        want.update(kids)
    return want, by_row


def load_impact(wanted):
    """{cell: [values]} for the wanted res-6 cells only, streamed off disk."""
    path = C.CELL_IMPACT_TABLE
    if not os.path.exists(path):
        print(f"  note: {path} not found — impact columns stay empty", file=sys.stderr)
        return {}
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        rd = csv.reader(f)
        header = next(rd)
        cols = [header.index(k) for k in C.IMPACT_FIELDS]
        for row in rd:
            if row[0] in wanted:
                out[row[0]] = [float(row[c] or 0) for c in cols]
    return out


def tidy(values, found):
    """Counts as integers, lengths and areas to one decimal — the precision the
    numbers actually carry, rather than the precision the arithmetic produced.

    A cell no impact data reached comes back blank rather than zero. The two mean
    different things — "nobody lives here" and "we do not know" — and writing 0
    for both would let the panel state the first with the confidence of a measured
    value, and quietly drag any impact-severity cutoff toward zero.
    """
    if not found:
        return [""] * len(C.IMPACT_FIELDS)
    out = []
    for field, v in zip(C.IMPACT_FIELDS, values):
        out.append(int(round(v)) if field in ("population", "buildings") else round(v, 1))
    return out


def read_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


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
    fc_path = os.path.join(out_dir, "forecasts.csv")
    for p in (cells_path, fc_path):
        if not os.path.exists(p):
            sys.exit(f"Not found: {p}\nRun steps 2 and 3 first.")
    print(f"Release: {os.path.basename(os.path.normpath(out_dir))}\n")

    # ---- impact onto cells ------------------------------------------------
    cell_rows = read_rows(cells_path)
    t0 = time.time()
    wanted, by_row = needed_base_cells(cell_rows)
    impact = load_impact(wanted)
    print(f"Impact: {len(wanted):,} res-{C.BASE_RES} descendant(s) needed, "
          f"{len(impact):,} found in the static table  {time.time() - t0:.1f}s")

    out_cells, with_impact = [], 0
    for r, kids in zip(cell_rows, by_row):
        totals = [0.0] * len(C.IMPACT_FIELDS)
        hit = False
        for k in kids:
            v = impact.get(k)
            if v:
                hit = True
                for i, x in enumerate(v):
                    totals[i] += x
        with_impact += hit
        out_cells.append([r[c] for c in C.CELL_COLUMNS] + tidy(totals, hit))
    print(f"  {with_impact:,}/{len(cell_rows):,} cell(s) carry impact")

    # ---- Near onto forecasts ----------------------------------------------
    fc_rows = read_rows(fc_path)
    print(f"\nNear: {len(fc_rows):,} forecast(s)")
    t0 = time.time()
    placed = admin.assign(fc_rows)
    print(f"  {placed:,}/{len(fc_rows):,} placed  {time.time() - t0:.1f}s")

    out_fc = [[r.get(c, "") for c in C.FORECAST_DELIVERY_COLUMNS] for r in fc_rows]

    # ---- written back over their own inputs ---------------------------------
    c_bytes = write_csv(cells_path, C.CELL_DELIVERY_COLUMNS, out_cells)
    f_bytes = write_csv(fc_path, C.FORECAST_DELIVERY_COLUMNS, out_fc)
    print(f"\nWrote {out_dir}")
    print(f"  cells.csv            {len(out_cells):>7,} row(s)  {c_bytes / 1e6:>6.2f} MB"
          f"   (+{len(C.IMPACT_FIELDS)} impact column(s))")
    print(f"  forecasts.csv        {len(out_fc):>7,} row(s)  {f_bytes / 1e6:>6.2f} MB"
          f"   (+district, district_level)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
