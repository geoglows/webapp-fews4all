#!/usr/bin/env python3
"""Daily step 3 — decide agreement, then roll the grid up.

Reads step 2's two tables and writes the cell-level view of a release, at every
resolution the map telescopes through.

The rule this file exists to protect: two models agree only where they forecast
the SAME PLACE, and at a telescoping grid that means the same finest cell. A
coarse cell holds every forecast beneath it, so asking "how many models are in
this res-3 cell" reports agreement between models that are a hundred kilometres
apart. Agreement is therefore decided at res 6 and carried upward — a coarse cell
records the model sets that really do share one fine cell somewhere inside it, and
nothing else counts.

Both halves of that rule live here on purpose. Deciding agreement in one file and
rolling up in another is exactly how the two drift apart, and the drift is silent:
the map simply starts claiming concurrence that isn't there.

INPUTS
    backend/output/<release>/cell_forecasts.csv   (res, cell, forecast) at the
                                                  base resolution, from step 2
    backend/output/<release>/forecasts.csv        each forecast's model + severity

OUTPUTS
    backend/output/<release>/cells.csv            one row per (cell, resolution)
    backend/output/<release>/cell_forecasts.csv   the same file, expanded in place
                                                  to all four resolutions

Expanding the match table in place rather than writing a second one is what keeps
one set of files per release. Reading it filters to the base resolution, so a
rerun of this step sees the same input it saw the first time.
"""

import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import config as C          # noqa: E402


def read_forecasts(path):
    """{forecast_uid: (model, severity)} — all step 3 needs from the forecast table."""
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            out[row["forecast_uid"]] = (row["model"], (row["severity"] or "").lower())
    return out


def read_pairs(path):
    """{base-resolution cell: [forecast_uid, ...]}

    Filtered to the base resolution on the way in. The file may already hold every
    resolution from a previous run of this step, and rolling up a rolled-up table
    would treat coarse cells as if they were fine ones — which is exactly the
    over-reporting the whole co_models mechanism exists to prevent.
    """
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if int(row["res"]) != C.BASE_RES:
                continue
            out.setdefault(row["h3_id"], []).append(row["forecast_uid"])
    return out


def worst(severities):
    """The highest rung reached, by rank rather than by string order."""
    best, best_rank = "", -1
    for s in severities:
        r = C.SEVERITY_RANK.get(s, -1)
        if r > best_rank:
            best_rank, best = r, s
    return best


def ordered(models):
    return [m for m in C.MODEL_ORDER if m in models] + \
           sorted(m for m in models if m not in C.MODEL_ORDER)


def build(base, meta):
    """(cells, rollup) for every resolution, from the res-6 pairs.

    One pass over the base cells does all four resolutions at once: each res-6 cell
    contributes its forecasts to its ancestor at each level, and — if it holds two
    or more models itself — contributes that model set as a fact of agreement the
    ancestor inherits. An ancestor never invents a set of its own.
    """
    import h3

    cells = {}        # (res, cell) -> {"uids": set, "co": set of frozenset}
    for cell, uids in base.items():
        models_here = {meta[u][0] for u in uids if u in meta}
        # The agreement fact, decided here at the base resolution and nowhere else.
        co_here = frozenset(models_here) if len(models_here) >= 2 else None

        for res in C.RESOLUTIONS:
            key = (res, cell if res == C.BASE_RES else h3.cell_to_parent(cell, res))
            rec = cells.setdefault(key, {"uids": set(), "co": set()})
            rec["uids"].update(uids)
            if co_here:
                rec["co"].add(co_here)

    rows, rollup = [], []
    for (res, cell), rec in cells.items():
        uids = rec["uids"]
        models = ordered({meta[u][0] for u in uids if u in meta})
        rows.append([
            cell, res,
            worst(meta[u][1] for u in uids if u in meta),
            len(models), "+".join(models),
            "|".join("+".join(ordered(s)) for s in sorted(rec["co"], key=lambda x: sorted(x))),
            len(uids),
        ])
        for uid in sorted(uids):
            rollup.append([res, cell, uid])
    rows.sort(key=lambda r: (r[1], r[0]))
    rollup.sort()
    return rows, rollup


def write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(columns)
        w.writerows(rows)
    return os.path.getsize(path)


def main(release_dir=None):
    out_dir = release_dir or os.path.join(C.OUTPUT_DIR, os.listdir(C.OUTPUT_DIR)[0])
    f_path = os.path.join(out_dir, "forecasts.csv")
    p_path = os.path.join(out_dir, "cell_forecasts.csv")
    for p in (f_path, p_path):
        if not os.path.exists(p):
            sys.exit(f"Not found: {p}\nRun step 2 first.")

    print(f"Release: {os.path.basename(os.path.normpath(out_dir))}\n")
    meta = read_forecasts(f_path)
    base = read_pairs(p_path)
    gridded = {u for uids in base.values() for u in uids}
    print(f"  {len(meta):,} forecast(s) read, {len(gridded):,} of them gridded")
    print(f"  {len(base):,} res-{C.BASE_RES} cell(s) to roll up\n")

    t0 = time.time()
    rows, rollup = build(base, meta)
    print(f"Rolled up in {time.time() - t0:.1f}s")

    by_res = {}
    for r in rows:
        by_res.setdefault(r[1], [0, 0])
        by_res[r[1]][0] += 1
        if r[5]:
            by_res[r[1]][1] += 1
    for res in C.RESOLUTIONS:
        n, co = by_res.get(res, (0, 0))
        print(f"  res {res}: {n:>7,} cell(s), {co:>5,} with agreement")

    c_bytes = write_csv(os.path.join(out_dir, "cells.csv"), C.CELL_COLUMNS, rows)
    r_bytes = write_csv(p_path, C.MATCH_COLUMNS, rollup)
    print(f"\nWrote {out_dir}")
    print(f"  cells.csv            {len(rows):>7,} row(s)  {c_bytes / 1e6:>6.2f} MB")
    print(f"  cell_forecasts.csv   {len(rollup):>7,} row(s)  {r_bytes / 1e6:>6.2f} MB")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
