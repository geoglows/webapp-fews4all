#!/usr/bin/env python3
"""Static build — a RECORD of what the GEOGLOWS blackout box is swallowing.

    *** THIS DOCUMENTS A BAND-AID. READ backend/common/config.py, the
    *** GEOGLOWS BLACKOUT BOX block, before running or changing it.

NOTHING IN THE DAILY RUN READS THE FILE THIS WRITES. The rule is the box in
config.py; a forecast is withheld because of where it is, not because it is in a
list. This exists for one purpose: so that "what are we actually hiding?" has an
answer that does not depend on anyone still having the October 2026 releases.

Run it before deciding to widen, narrow or remove the box, so the decision is
made against numbers rather than against a memory of a screenshot.

WHAT IT FOUND when the box went in (10 releases, 2026-09-23 .. 2026-10-29)
--------------------------------------------------------------------------
    reaches flagged at least once in the box : 5,577
    of those, flagged on every one of 10 days: 1,379
    of those, same severity on all 10        : 1,054  (76%)
    of those, permanently on the 2-year rung : 1,009  (73%)
    share of ALL GEOGLOWS flags in the box   : 50.7%

That last line is the clearest statement of the problem there is. One region the
size of Finland was producing HALF of every GEOGLOWS flood warning on earth.

    python3 backend/static/build_suppression.py          # report only
    python3 backend/static/build_suppression.py --write  # also write the record
"""

import collections
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from common import config as C          # noqa: E402
from common import tables as T          # noqa: E402

COLUMNS = ["river_id", "lat", "lon", "days_flagged", "days_seen",
           "severities", "return_periods", "first_seen", "last_seen"]


def releases(root):
    return sorted(d for d in os.listdir(root)
                  if d[:2] == "20" and os.path.isdir(os.path.join(root, d))
                  and not d.endswith(".prev"))


def inside(lon, lat, box):
    w, s, e, n = box
    return w <= lon <= e and s <= lat <= n


def sweep(root, box):
    """Every GEOGLOWS reach seen inside the box, with the days it was flagged."""
    days = releases(root)
    seen = collections.defaultdict(list)
    facts = {}
    for rel in days:
        path = os.path.join(root, rel, "forecasts.csv")
        if not os.path.exists(path):
            continue
        here = set()
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if row.get("model") != "geoglows":
                    continue
                try:
                    lon, lat = float(row["lon"]), float(row["lat"])
                except (ValueError, TypeError, KeyError):
                    continue
                if not inside(lon, lat, box):
                    continue
                rid = row["native_id"]
                here.add(rid)
                d = facts.setdefault(rid, {"lat": lat, "lon": lon,
                                           "sev": set(), "rp": set()})
                d["sev"].add(row.get("severity", ""))
                d["rp"].add(row.get("return_period_yr", ""))
        for rid in here:
            seen[rid].append(rel)
    return days, seen, facts


def main():
    box = getattr(C, "GEOGLOWS_BLACKOUT_BOX", None)
    if not box:
        sys.exit("GEOGLOWS_BLACKOUT_BOX is off — nothing is being withheld.")
    root = C.OUTPUT_DIR
    days, seen, facts = sweep(root, box)
    if not days:
        sys.exit(f"No releases under {root}")

    print(f"  box {box}  (west, south, east, north)")
    print(f"  {len(days)} release(s): {days[0]} .. {days[-1]}")
    print(f"  {len(seen):,} GEOGLOWS reach(es) seen inside it\n")

    hist = collections.Counter(len(v) for v in seen.values())
    for n in sorted(hist, reverse=True):
        print(f"    flagged on {n:>2}/{len(days)} day(s): {hist[n]:,}")

    always = [r for r, v in seen.items() if len(v) == len(days)]
    if always:
        const = sum(1 for r in always if len(facts[r]["sev"]) == 1)
        print(f"\n  {len(always):,} flagged on EVERY day; {const:,} "
              f"({const / len(always):.0%}) never changed severity")

    # The share is the headline. A region producing half the world's warnings is
    # not a wet region.
    last = os.path.join(root, days[-1], "forecasts.csv")
    tot = hit = 0
    with open(last, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("model") != "geoglows":
                continue
            tot += 1
            try:
                if inside(float(row["lon"]), float(row["lat"]), box):
                    hit += 1
            except (ValueError, TypeError, KeyError):
                pass
    if tot:
        print(f"\n  on {days[-1]}: {hit:,} of {tot:,} published GEOGLOWS "
              f"forecasts were inside the box ({hit / tot:.1%})")

    rows = [[r, f"{facts[r]['lat']:.5f}", f"{facts[r]['lon']:.5f}",
             len(seen[r]), len(days),
             "|".join(sorted(x for x in facts[r]["sev"] if x)),
             "|".join(sorted(x for x in facts[r]["rp"] if x)),
             seen[r][0], seen[r][-1]]
            for r in sorted(seen, key=lambda x: int(x))]

    if "--write" not in sys.argv:
        print("\n  report only — rerun with --write to replace "
              f"{os.path.relpath(C.GEOGLOWS_SUPPRESSED_CSV, C.ROOT)}")
        return
    size = T.write_csv(C.GEOGLOWS_SUPPRESSED_CSV, COLUMNS, rows)
    print(f"\n  {len(rows):,} reach(es) -> {C.GEOGLOWS_SUPPRESSED_CSV}  "
          f"{size / 1e3:.0f} kB   (a record, not a rule)")


if __name__ == "__main__":
    main()
