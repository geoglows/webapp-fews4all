#!/usr/bin/env python3
"""One-time move: backend/input + backend/output -> fews4all_data/YYYY/MM/DD.

    *** RUN ONCE, THEN DELETE THIS FILE. It exists to cross one change, not to
    *** be part of the pipeline.

WHAT IT DOES

Raw downloads MOVE, because they are three gigabytes and the old location is
being retired; each file lands in the folder for the model that produced it, and
the two Flood Hub files gain the date they were always missing from their names.

Published releases are REBUILT rather than copied. A release published before the
forecast timeline existed has no `day` column and no day spans, so there is
nothing to split by -- and a tree where some days answer "what about Thursday"
and others cannot would be worse than one where none of them do. Rebuilding runs
each release's own forecasts back through steps 3, 4 and 5, so every day in the
archive ends up the same shape as tomorrow's.

That also re-joins impact from whatever cell_impact_r6.csv currently holds. Impact
does not vary with time, so recomputing it is the correct answer rather than a
side effect -- but it does mean every release's impact figures move to the current
table, which is a real change to numbers already published.

RESUMABLE. A release that already has a processed/ folder is skipped, so an
interrupted run is restarted by running it again. Nothing is deleted from the old
locations except the raw files it has just moved.

    python3 backend/static/migrate_to_dated_tree.py --dry-run
    python3 backend/static/migrate_to_dated_tree.py --limit 2   # a bounded pass
    python3 backend/static/migrate_to_dated_tree.py             # until done

--limit exists because rebuilding a release takes the better part of a minute and
some environments will not let a long job run unattended. Each pass picks up where
the last stopped, so `--limit 2` in a loop finishes exactly like one long run.
"""

import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from common import config as C          # noqa: E402
from common import manifest as MAN      # noqa: E402
from common import models as M          # noqa: E402
from common.tables import write_csv     # noqa: E402

OLD_IN = os.path.join(C.BACKEND, "input")
OLD_OUT = os.path.join(C.BACKEND, "output")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# Which model each raw filename belongs to. Matched in order, first hit wins.
RAW_OWNER = [
    ("mapstyletable", "geoglows"),
    ("sumAL", "glofas"), ("summary_alerts", "glofas"), ("RPG_U", "glofas"),
    ("world_flood_status", "flood_hub"),
    ("urban_flash_floods", "flash"),
]


def owner(name):
    for frag, model in RAW_OWNER:
        if name.startswith(frag):
            return model
    return None


def dated(name, release):
    """Give the two Flood Hub files the date their names never carried."""
    stem, ext = os.path.splitext(name)
    if DATE.search(stem):
        return name
    if stem in ("world_flood_status", "urban_flash_floods"):
        return f"{stem}_{release}{ext}"
    return name


def move_raw(release, dry):
    src = os.path.join(OLD_IN, release)
    if not os.path.isdir(src):
        return 0, 0
    moved = skipped = 0
    for name in sorted(os.listdir(src)):
        s = os.path.join(src, name)
        if not os.path.isfile(s):
            continue
        if name == "fetch.json":
            d = os.path.join(C.raw_dir(release), name)
        else:
            m = owner(name)
            if not m:
                skipped += 1
                continue
            d = os.path.join(C.raw_dir(release, m), dated(name, release))
        if os.path.exists(d):
            skipped += 1
            continue
        if not dry:
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.move(s, d)
        moved += 1
    return moved, skipped


def rebuild(release, dry):
    """Run this release's own tables back through steps 3, 4 and 5."""
    from daily import step3_compare, step4_summary, step5_publish

    src = os.path.join(OLD_OUT, release)
    fc = os.path.join(src, "forecasts.csv")
    ci = os.path.join(src, "cell_forecasts.csv")
    if not (os.path.exists(fc) and os.path.exists(ci)):
        return "no tables to rebuild from"
    if dry:
        return "would rebuild"

    stage = os.path.join(C.DATA_DIR, C.STAGING_DIR, release)
    os.makedirs(stage, exist_ok=True)
    shutil.copyfile(ci, os.path.join(stage, "cell_forecasts.csv"))

    import csv
    rows = list(csv.DictReader(open(fc, newline="", encoding="utf-8-sig")))
    M.assign_days(rows, release)
    cols = C.FORECAST_DELIVERY_COLUMNS
    write_csv(os.path.join(stage, "forecasts.csv"), cols,
              [[r.get(c, "") for c in cols] for r in rows])

    for name in ("flash_areas.geojson", C.MANIFEST_NAME):
        p = os.path.join(src, name)
        if os.path.exists(p):
            shutil.copyfile(p, os.path.join(stage, name))

    step3_compare.main(stage)
    step4_summary.main(stage)
    out = step5_publish.main(stage, release)

    live = C.processed_dir(release)
    os.makedirs(os.path.dirname(live), exist_ok=True)
    if os.path.exists(live):
        shutil.rmtree(live)
    shutil.move(out, live)
    shutil.rmtree(stage, ignore_errors=True)
    return f"{len(rows):,} forecast(s)"


def main():
    dry = "--dry-run" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    releases = sorted({n for d in (OLD_IN, OLD_OUT) if os.path.isdir(d)
                       for n in os.listdir(d) if DATE.fullmatch(n)})
    if not releases:
        sys.exit("Nothing to migrate: no dated folders under backend/input or output.")
    print(f"{'DRY RUN — ' if dry else ''}{len(releases)} release(s) to move\n")

    worked = 0
    for r in releases:
        done = os.path.isdir(C.processed_dir(r))
        if done:
            print(f"  {r}   already published")
            continue
        if limit is not None and worked >= limit:
            print(f"  {r}   (left for the next pass)")
            continue
        moved, skipped = move_raw(r, dry)
        note = rebuild(r, dry)
        worked += 1
        print(f"  {r}   raw: {moved:>4} moved, {skipped:>3} skipped   "
              f"published: {note}", flush=True)

    remaining = [r for r in releases if not os.path.isdir(C.processed_dir(r))]
    if remaining and not dry:
        print(f"\n  {len(remaining)} release(s) left — run again to continue.")

    if not dry and not remaining:
        for name in (C.RELEASE_POINTER, C.RELEASE_INDEX):
            old = os.path.join(OLD_OUT, name)
            if os.path.exists(old) and not os.path.exists(os.path.join(C.DATA_DIR, name)):
                shutil.move(old, os.path.join(C.DATA_DIR, name))
        idx = MAN.write_index(C.DATA_DIR)
        current = sorted(C.list_releases())
        if current:
            C.write_release_pointer(current[-1])
        print(f"\n  index rebuilt: {len((idx or {}).get('releases', []))} release(s)")
        print(f"  pointer -> {current[-1] if current else '(none)'}")
        print(f"\n  Old folders are left in place. Once the map looks right from")
        print(f"  {C.DATA_DIR}, backend/input and backend/output can go.")


if __name__ == "__main__":
    main()
