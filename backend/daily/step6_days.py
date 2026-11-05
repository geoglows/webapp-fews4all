#!/usr/bin/env python3
"""Daily step 6 — split a release into the forecast days the map steps through.

Steps 2-5 build ONE set of tables holding every forecast day: cells.csv carries a
`day` column, forecasts.csv carries day_from/day_to. Those stay exactly as they
are -- they are the release's complete record, and the thing to read when the
question is "what did this run actually say".

This writes the view the BROWSER fetches:

    <release>/days/d00/cells.csv            that day's cells, with impact
    <release>/days/d00/forecasts.csv        the forecasts in play that day

cell_forecasts.csv is NOT split. A forecast's cell never moves with the day, so
fourteen copies of it would be fourteen copies of the same answer -- and at 1.6 MB
it was the largest single thing here. The front end fetches the release's one copy
and keeps the rows whose forecast is in that day's forecasts.csv, which it has
already downloaded.

It exists for one reason: payload. The complete cells.csv is ten megabytes and
grows with the horizon, and nobody opening a map wants to download thirteen days
they have not asked to see. A day is about a megabyte -- the size the front end
already fetches per release -- so stepping a day costs what stepping a release
costs, and the opening view costs what it has always cost.

WHY THE ROOT TABLES ARE NOT TRIMMED TO DAY 0

They are the archive. Pruning them to match what the map draws would mean the only
complete copy of a run was the one assembled for a browser, and the first question
anyone asks of a forecast six months later -- "what did it say about the 9th?" --
would have no answer. Disk is the cheapest thing in this pipeline.

HOW LONG THE TIMELINE IS

Not HORIZON_DAYS. That is a ceiling; the length is whatever the data earns.

A day is worth a tick only if some forecast NAMES it -- a GEOGLOWS morning step, a
Flood Hub window. Rows that merely SPAN the horizon do not count: GloFAS has no
time axis to resolve, and a Flood Hub gauge with no published window is on every
day for the same reason, which is the absence of timing rather than timing. A day
carrying only those shows exactly what the day before it showed, so a tick for it
offers the user a step that changes nothing.

With the map-tables product reaching 10 days and Flood Hub windows 7, that is days
0-9 today. It moves on its own if GEOGLOWS publishes further or GloFAS ever starts
resolving days -- nothing to edit.

WHAT A MISSING DAY MEANS

A day folder is written even when nothing is in it, and run.json records which
models reached it. "No model forecasts this far ahead" and "no model has anything
to report today" look identical on a map and are completely different statements,
and this is where the difference is made available to say.

    python3 backend/daily/step6_days.py [<release-dir>]
"""

import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import config as C          # noqa: E402
from common.tables import write_csv     # noqa: E402
from common import manifest as MAN      # noqa: E402


def read_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def span(row):
    """(day_from, day_to) off a forecast row, tolerating a file without them."""
    def n(key):
        try:
            return int(row.get(key) or 0)
        except (TypeError, ValueError):
            return 0
    return n("day_from"), n("day_to")


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
        key = (r.get("model"), r.get("native_id"))
        groups.setdefault(key, []).append(r)

    stamped = 0
    for key, rows_here in groups.items():
        for r in rows_here:
            r["peak_day"] = ""
        days = {span(r)[0] for r in rows_here}
        # One row, or rows that do not each name a single day, is a flat span.
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

        best = max(rows_here, key=rank)
        for r in rows_here:
            r["peak_day"] = span(best)[0]
        stamped += len(rows_here)
    return stamped


def names_a_day(row, horizon):
    """Does this forecast assert a particular day, or is it just spanning?

    The spanning case is exactly what assign_days writes when it cannot date a row:
    the whole horizon, 0..horizon-1. Anything narrower came from a real lead or a
    real published window.
    """
    lo, hi = span(row)
    return not (lo == 0 and hi >= horizon - 1)


def main(release_dir=None):
    out_dir = release_dir or C.current_release()
    if not out_dir:
        sys.exit(f"No release folder under {C.OUTPUT_DIR}. Run step 2 first.")
    cells_path = os.path.join(out_dir, "cells.csv")
    fc_path = os.path.join(out_dir, "forecasts.csv")
    for p in (cells_path, fc_path):
        if not os.path.exists(p):
            sys.exit(f"Not found: {p}\nRun steps 2 to 5 first.")

    print(f"Release: {os.path.basename(os.path.normpath(out_dir))}\n")
    t0 = time.time()

    cells = read_rows(cells_path)
    fc = read_rows(fc_path)
    peaks = mark_peak_day(fc)

    cells_by_day = {}
    for row in cells:
        try:
            cells_by_day.setdefault(int(row["day"]), []).append(row)
        except (TypeError, ValueError):
            cells_by_day.setdefault(0, []).append(row)

    horizon = C.HORIZON_DAYS
    print(f"  {peaks:,} forecast row(s) carry a peak day "
          f"({len(fc) - peaks:,} span their days and have none)")

    # The last day any forecast actually names. Everything past it would redraw
    # the spanning rows unchanged, so it is where the timeline ends.
    dated = [r for r in fc if names_a_day(r, horizon)]
    last = max((span(r)[1] for r in dated), default=0)
    print(f"  {len(dated):,} row(s) name a day; the furthest is d{last:02d}, "
          f"so the timeline runs d00..d{last:02d} of a {horizon}-day ceiling\n")

    print(f"  {'day':>4} {'cells':>9} {'forecasts':>10} {'naming it':>10}  models")
    summary = []
    for day in range(last + 1):
        here = [r for r in fc if span(r)[0] <= day <= span(r)[1]]
        named = sum(1 for r in here if names_a_day(r, horizon))
        rows_c = cells_by_day.get(day, [])
        models = sorted({r["model"] for r in here})

        d = C.day_dir(out_dir, day)
        os.makedirs(d, exist_ok=True)
        write_csv(os.path.join(d, "cells.csv"), C.CELL_DELIVERY_COLUMNS,
                  [[r.get(c, "") for c in C.CELL_DELIVERY_COLUMNS] for r in rows_c])
        write_csv(os.path.join(d, "forecasts.csv"), C.FORECAST_DELIVERY_COLUMNS,
                  [[r.get(c, "") for c in C.FORECAST_DELIVERY_COLUMNS] for r in here])
        print(f"  {day:>4} {len(rows_c):>9,} {len(here):>10,} {named:>10,}  "
              f"{', '.join(models) or '--'}")
        summary.append({"day": day, "cells": len(rows_c),
                        "forecasts": len(here), "dated": named,
                        "models": models,
                        # Which models actually name this day, as opposed to
                        # merely covering it. The panel needs the distinction to
                        # say whether stepping here told the user anything.
                        "dated_models": sorted({r["model"] for r in here
                                                if names_a_day(r, horizon)})})

    # How far each model REACHES, which is not the same as where it last had
    # something to say. A model that forecasts to day 13 and flags nothing on day
    # 9 is absent from day 9's model list for a completely different reason than
    # flash floods are, and the panel has to say which -- "no warnings" and "does
    # not see this far" are opposite statements about the same blank map.
    reach = {}
    for r in fc:
        m = r.get("model")
        if m:
            reach[m] = max(reach.get(m, 0), span(r)[1])
    print("\n  model reach: " + "  ".join(
        f"{m}->d{reach[m]:02d}" for m in C.MODEL_ORDER if m in reach))

    empty = [s["day"] for s in summary if not s["dated"]]
    print(f"\nWrote {len(summary)} day folder(s) under {os.path.join(out_dir, 'days')}"
          f"  {time.time() - t0:.1f}s")
    if empty:
        print(f"  day(s) no model reaches: {', '.join(str(d) for d in empty)}")
    MAN.update(out_dir, horizon_days=horizon, last_day=last, days=summary,
               model_reach=reach,
               steps={"step6_days": {
                   "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "days": len(summary), "last_day": last,
                   "empty_days": empty}})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
