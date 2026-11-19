#!/usr/bin/env python3
"""Step 5's two quiet rules, and the reach-id spelling step 2 joins on.

    python3 backend/test/test_publish.py

All three of these were live bugs, and none of them raised anything. That is
what they have in common and why they are pinned here: a release came out, the
map drew, every count in the run log looked ordinary, and the thing that was
wrong was visible only to someone comparing two files that nobody compares.

  the sweep        step 5 wrote day_00..day_NN into a folder it never cleared,
                   so a rerun that produced fewer days left the longer run's
                   files behind. Release 2026-11-05 shipped seven day files
                   from one run and nine from another, in one folder.

  the reach        model_reach counted rows that span the horizon, so one
                   Flood Hub gauge with no published window made the whole
                   model claim it forecast out to day 15.

  the id           step 2 keyed flagged reaches on str(native_id) while the
                   crosswalk side read ids back through int(float(...)). A
                   reach written "110137730.0" matched nothing and no counter
                   moved.

Nothing here touches the real input or output folders.
"""

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "daily"))

from common import config as C          # noqa: E402
import step2_intersect as s2            # noqa: E402
import step5_publish as s5              # noqa: E402

PASS, FAIL = [], []


def ok(cond, what):
    (PASS if cond else FAIL).append(what)
    print(("  pass  " if cond else "  FAIL  ") + what)


def row(model, lo, hi):
    return {"model": model, "day_from": str(lo), "day_to": str(hi)}


def test_sweep(out):
    print("\nStep 5 clears its own output first")
    d = os.path.join(out, "processed")
    os.makedirs(d, exist_ok=True)
    names = ["day_00.json", "day_07.json", "day_15.json",
             C.PROCESSED["release"], C.PROCESSED["forecasts"],
             C.PROCESSED["cell_index"], "summary_2026-11-05.csv"]
    for n in names:
        open(os.path.join(d, n), "w").close()
    # Not step 5's, and not step 5's to delete.
    open(os.path.join(d, "notes.txt"), "w").close()

    gone = s5.clear_products(d)
    left = sorted(os.listdir(d))
    ok(gone == len(names), f"every file this step writes is cleared ({gone})")
    ok(left == ["notes.txt"], f"and nothing else is touched ({left})")

    ok(s5.clear_products(d) == 0, "a second sweep of a clean folder removes nothing")


def test_reach():
    print("\nHow far a model says it forecasts")
    horizon = 16
    last = horizon - 1

    rows = [row("geoglows", d, d) for d in range(10)]
    rows += [row("glofas", 0, 3), row("glofas", 4, 10), row("glofas", 11, 15)]
    rows += [row("flood_hub", 5, 6)]
    r = s5.model_reach(rows, horizon)
    ok(r.get("geoglows") == 9, f"a model that names single days reaches the last of them ({r.get('geoglows')})")
    ok(r.get("glofas") == 15, f"a banded model reaches the end of its last band ({r.get('glofas')})")
    ok(r.get("flood_hub") == 6, f"a published window reaches its own end ({r.get('flood_hub')})")

    # The bug: one undated gauge used to drag the whole model out to the horizon.
    rows2 = rows + [row("flood_hub", 0, last)]
    r2 = s5.model_reach(rows2, horizon)
    ok(r2.get("flood_hub") == 6,
       f"a row spanning the horizon names no day and moves nothing ({r2.get('flood_hub')})")

    only_spanning = [row("flood_hub", 0, last)]
    r3 = s5.model_reach(only_spanning, horizon)
    ok("flood_hub" not in r3,
       "a model with nothing but spanning rows makes no claim at all, rather than claiming 0")


def test_reach_ids():
    print("\nA reach id is spelled one way on both sides of the join")
    ok(s2._reach_int("110137730") == 110137730, "a bare integer reads as itself")
    ok(s2._reach_int("110137730.0") == 110137730, "...and so does the same id written as a float")
    ok(s2._reach_int(" 110137730 ") == 110137730, "...and with whitespace around it")
    ok(s2._reach_int(-5) == -5, "a negative id survives")
    ok(s2._reach_int("not-a-reach") is None, "something that is not a number is None, not a guess")
    ok(s2._reach_int("") is None, "and so is a blank")

    # The failure this prevents: two spellings of one reach becoming two keys,
    # one of which the crosswalk can never match.
    spellings = ["110137730", "110137730.0", 110137730, 110137730.0]
    keys = {str(s2._reach_int(s)) for s in spellings}
    ok(len(keys) == 1, f"every spelling of one reach collapses to one key ({keys})")


def main():
    out = tempfile.mkdtemp(prefix="fews-publish-")
    try:
        test_sweep(out)
        test_reach()
        test_reach_ids()
    finally:
        shutil.rmtree(out, ignore_errors=True)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
