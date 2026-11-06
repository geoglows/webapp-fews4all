#!/usr/bin/env python3
"""Daily step 5 — publish the release into processed/.

Steps 2-4 build working tables holding every forecast day at once: cells.csv with
a `day` column, forecasts.csv with day_from/day_to, cell_forecasts.csv joining
them. Those are scratch. This turns them into the handful of files the map fetches
and the archive keeps, under the dated tree described in config.DATA_DIR:

    processed/
      release.json          the run record, the day manifest, the style tables
      forecasts.json        every forecast, once, with the days it speaks for
      cell_index.json       which forecast is in which cell, every resolution
      day_00.json .. day_NN.json     that day's cells
      flash_areas.geojson   (written by step 2, moved here)
      summary_<date>.csv    the same forecasts, for a person to open

FIVE KINDS OF FILE, NOT THIRTY

release.json absorbs four files that were always read together and never apart --
run.json, layers.csv, palettes.csv and models.csv. Three of those never varied by
release at all; they were configuration shipped alongside the data so a release
folder was self-contained, and they stay for that reason, just not as four fetches.

A day file holds CELLS ONLY. Cells genuinely differ from one day to the next, so
splitting them is the whole point. Forecasts do not: a row carries the span of
days it speaks for, so writing it into each of those days wrote a GloFAS pixel
four times and an undated Flood Hub gauge sixteen times -- 5.9 MB a release of
the same rows. They are published once and the client keeps the ones whose span
covers the day it is drawing.

The tables are columnar JSON -- column names once, not once per row -- which
costs 6-10% over the CSVs they replace once gzipped, and a third of what
records-style JSON would. See tables.table.

WHY THE DAYS ARE SEPARATE FILES AT ALL

Because "as few files as possible" and "opens fast" pull against each other, and
this is where the line falls. Every day in one file is ~30 MB for a map that
draws one day at a time; one file per day is about a megabyte, which is what the
app already fetched per release before any of this existed. Stepping a day costs
one request either way.

WHAT IS NOT PUBLISHED

The all-days working tables. cells.csv is the same rows as the day files, split
differently, and forecasts.csv is summary_<date>.csv with fewer columns. Shipping
both would be shipping the release twice, and the summary CSV is the copy a human
can open. The working tables stay in staging and are thrown away with it.

    python3 backend/daily/step5_publish.py [<staging dir>]
"""

import csv
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import config as C          # noqa: E402
from common import manifest as MAN      # noqa: E402
from common.tables import read_json, table, write_csv, write_json   # noqa: E402


def read_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def read_table(path):
    """(columns, rows) straight off a CSV, values untouched."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rd = csv.reader(f)
        return next(rd), list(rd)


def span(row):
    """(day_from, day_to) off a forecast row, tolerating a file without them."""
    def n(key):
        try:
            return int(row.get(key) or 0)
        except (TypeError, ValueError):
            return 0
    return n("day_from"), n("day_to")


def names_a_day(row, horizon):
    """Does this forecast assert a particular day, or is it just spanning?

    The spanning case is exactly what assign_days writes when it cannot date a
    row: the whole horizon, 0..horizon-1. Anything narrower came from a real lead
    or a real published window.
    """
    lo, hi = span(row)
    return not (lo == 0 and hi >= horizon - 1)


def mark_peak_day(rows):
    """Stamp peak_day on every row, and return how many got one.

    A forecast is one row PER DAY only where the model resolves days -- GEOGLOWS.
    Those rows share a native_id, so the worst of them is the peak, by the same
    severity ladder the rest of the pipeline ranks with, with return period
    breaking a tie inside a rung.

    A model whose row SPANS its days (GloFAS, a Flood Hub window) is left blank on
    purpose. One row carrying one severity across six days does not know which of
    them is worse, and the field it publishes for exactly this -- Flood Hub's
    peakTime -- lands outside its own window on most rows. Deriving a peak from a
    flat span would manufacture precision the source does not have.
    """
    groups = {}
    for r in rows:
        groups.setdefault((r.get("model"), r.get("native_id")), []).append(r)

    stamped = 0
    for rows_here in groups.values():
        for r in rows_here:
            r["peak_day"] = ""
        days = {span(r)[0] for r in rows_here}
        if len(rows_here) < 2 or len(days) < 2:
            continue
        if any(span(r)[0] != span(r)[1] for r in rows_here):
            continue

        def rank(r):
            try:
                rp = float(r.get("return_period_yr") or 0)
            except (TypeError, ValueError):
                rp = 0.0
            return (C.SEVERITY_RANK.get((r.get("severity") or "").lower(), -1), rp)

        best = span(max(rows_here, key=rank))[0]
        for r in rows_here:
            r["peak_day"] = best
        stamped += len(rows_here)
    return stamped


def style_tables():
    """The three tables step 5 used to write as CSV, as plain JSON.

    They are configuration rather than data -- the same every release -- and they
    are here so a release folder still answers "what did this look like" on its
    own, without the app that drew it.
    """
    order = sorted(C.RESOLUTIONS)
    layers = [{
        "res": res, "source_id": "cells", "url": C.CELLS_TILES_URL,
        "source_layer": C.CELLS_SOURCE_LAYER.format(res=res),
        "promote_id": C.CELLS_PROMOTE_ID,
        "minzoom": C.RES_ZOOM[res],
        "maxzoom": C.RES_ZOOM[order[i + 1]] if i + 1 < len(order) else "",
    } for i, res in enumerate(order)]
    palettes = [{"palette_id": p["id"], "label": p["label"], "severity": sev,
                 "color": p[sev]}
                for p in C.PALETTES for sev in ("warning", "danger", "extreme")]
    models = [{"model": m, "label": C.MODEL_LABELS.get(m, m),
               "palette": C.MODEL_PALETTE.get(m, C.DEFAULT_PALETTE), "sort_order": i}
              for i, m in enumerate(C.MODEL_ORDER)]
    return layers, palettes, models


def main(staged=None, release=None):
    staged = staged or C.current_release()
    if not staged:
        sys.exit("No staged release to publish. Run step 2 first.")
    release = release or C.release_of_path(staged) or os.path.basename(
        os.path.normpath(staged))

    cells_path = os.path.join(staged, "cells.csv")
    fc_path = os.path.join(staged, "forecasts.csv")
    ci_path = os.path.join(staged, "cell_forecasts.csv")
    for p in (cells_path, fc_path, ci_path):
        if not os.path.exists(p):
            sys.exit(f"Not found: {p}\nRun steps 2 to 5 first.")

    out = os.path.join(staged, "processed")
    os.makedirs(out, exist_ok=True)
    print(f"Release: {release}\n")
    t0 = time.time()

    fc = read_rows(fc_path)
    peaks = mark_peak_day(fc)
    horizon = C.HORIZON_DAYS
    print(f"  {peaks:,} forecast row(s) carry a peak day "
          f"({len(fc) - peaks:,} span their days and have none)")

    dated = [r for r in fc if names_a_day(r, horizon)]
    last = max((span(r)[1] for r in dated), default=0)
    print(f"  {len(dated):,} row(s) name a day; the furthest is d{last:02d}, "
          f"so the timeline runs d00..d{last:02d} of a {horizon}-day ceiling\n")

    # ---- the day files ------------------------------------------------------
    cell_cols, cell_rows = read_table(cells_path)
    day_at = cell_cols.index("day")
    cells_by_day = {}
    for r in cell_rows:
        try:
            cells_by_day.setdefault(int(r[day_at]), []).append(r)
        except (TypeError, ValueError):
            cells_by_day.setdefault(0, []).append(r)

    fc_cols = C.FORECAST_DELIVERY_COLUMNS
    print(f"  {'day':>4} {'cells':>9} {'forecasts':>10} {'naming it':>10} {'MB':>6}  models")
    summary, reach = [], {}
    for r in fc:
        m = r.get("model")
        if m:
            reach[m] = max(reach.get(m, 0), span(r)[1])

    for day in range(last + 1):
        here = [r for r in fc if span(r)[0] <= day <= span(r)[1]]
        named = sum(1 for r in here if names_a_day(r, horizon))
        rows_c = cells_by_day.get(day, [])
        models = sorted({r["model"] for r in here})
        size = write_json(os.path.join(out, C.PROCESSED["day"].format(day=day)), {
            "release": release, "day": day,
            "cells": table(cell_cols, rows_c),
        })
        print(f"  {day:>4} {len(rows_c):>9,} {len(here):>10,} {named:>10,} "
              f"{size / 1e6:>6.2f}  {', '.join(models) or '--'}")
        summary.append({"day": day, "cells": len(rows_c), "forecasts": len(here),
                        "dated": named, "models": models,
                        "dated_models": sorted({r["model"] for r in here
                                                if names_a_day(r, horizon)})})

    # ---- the things that do not vary by day ---------------------------------
    fc_size = write_json(os.path.join(out, C.PROCESSED["forecasts"]),
                         table(fc_cols,
                               [[r.get(c, "") for c in fc_cols] for r in fc]))

    ci_cols, ci_rows = read_table(ci_path)
    ci_size = write_json(os.path.join(out, C.PROCESSED["cell_index"]),
                         table(ci_cols, ci_rows))

    layers, palettes, models = style_tables()
    run = MAN.read(staged) or {}
    rel_size = write_json(os.path.join(out, C.PROCESSED["release"]), {
        "release": release,
        "horizon_days": horizon,
        "last_day": last,
        "days": summary,
        "model_reach": reach,
        "run": run,
        "layers": layers,
        "palettes": palettes,
        "models": models,
    })

    # ---- the copy a person can open -----------------------------------------
    sum_name = C.PROCESSED["summary"].format(release=release)
    # fc_cols already ends with peak_day -- it is part of the forecast contract,
    # not an extra this step bolts on.
    sum_size = write_csv(os.path.join(out, sum_name), fc_cols,
                         [[r.get(c, "") for c in fc_cols] for r in fc])

    # ---- flash polygons ride along -------------------------------------------
    flash_src = os.path.join(staged, "flash_areas.geojson")
    if os.path.exists(flash_src):
        shutil.copyfile(flash_src, os.path.join(out, C.PROCESSED["flash"]))

    total = sum(os.path.getsize(os.path.join(out, f)) for f in os.listdir(out))
    print(f"\nWrote {out}  {time.time() - t0:.1f}s")
    print(f"  {C.PROCESSED['release']:<26} {rel_size / 1e3:>7.1f} kB")
    print(f"  {C.PROCESSED['forecasts']:<26} {fc_size / 1e6:>7.2f} MB  {len(fc):,} row(s)")
    print(f"  {C.PROCESSED['cell_index']:<26} {ci_size / 1e6:>7.2f} MB  "
          f"{len(ci_rows):,} row(s)")
    print(f"  {sum_name:<26} {sum_size / 1e6:>7.2f} MB  {len(fc):,} forecast(s)")
    print(f"  {last + 1} day file(s)")
    print(f"  {len(os.listdir(out))} file(s), {total / 1e6:.1f} MB in total")

    empty = [s["day"] for s in summary if not s["dated"]]
    MAN.update(staged, horizon_days=horizon, last_day=last, days=summary,
               model_reach=reach,
               steps={"step5_publish": {
                   "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "days": len(summary), "last_day": last,
                   "empty_days": empty, "files": len(os.listdir(out)),
                   "bytes": total}})
    return out


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
