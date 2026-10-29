#!/usr/bin/env python3
"""The live-data guards, exercised without a live feed.

    python3 backend/test/test_guards.py

These are the rules that decide what goes into a release and what is held back,
and they are the hardest part of the pipeline to check by running it: the failures
they exist for — a stale file left in the folder, a truncated download, a feed
that went quiet — are exactly the days you cannot arrange on demand. So they are
exercised here against synthetic manifests and status rows instead, which also
means a change to the thresholds in config shows up as a failing line rather than
as a surprise some morning in February.

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
from common import manifest as MAN      # noqa: E402
import step2_intersect as s2            # noqa: E402

PASS, FAIL = [], []


def ok(cond, what):
    (PASS if cond else FAIL).append(what)
    print(("  pass  " if cond else "  FAIL  ") + what)


def st(**kw):
    row = {"status": C.MODEL_OK, "kind": "point", "notes": []}
    row.update(kw)
    return row


def run(by, status, release, out):
    return s2.apply_guards(by, status, release, out)


def models(*names):
    return {m: {"kind": "point", "path": "x", "records": [1]} for m in names}


def test_release_naming(out):
    print("\nWhich date names the release")
    status = {"a": st(issued="2026-09-23", issued_from="issued_time", flagged=1),
              "b": st(issued="2026-09-24", issued_from="file mtime", flagged=1)}
    rel, dropped = run(models("a", "b"), status, None, out)
    ok(rel == "2026-09-23",
       f"a model's own issue date outranks another's file mtime (got {rel})")
    ok(dropped == [], "and the mtime model, being ahead, is not called stale")

    status = {"a": st(issued="2026-09-24", issued_from="file mtime", flagged=1)}
    rel, _ = run(models("a"), status, None, out)
    ok(rel == "2026-09-24", "mtime names the release when nothing else can")


def test_staleness(out):
    print("\nStaleness — yesterday's file left in the folder")
    status = {"a": st(issued="2026-09-23", issued_from="issued_time", flagged=1),
              "b": st(issued="2026-09-23", issued_from="issued_time", flagged=1),
              "c": st(issued="2026-09-20", issued_from="issued_time", flagged=1)}
    by = models("a", "b", "c")
    rel, dropped = run(by, status, None, out)
    ok(dropped == ["c"], f"a three-day-old model is dropped (got {dropped})")
    ok(status["c"]["status"] == C.MODEL_DROPPED, "and marked dropped in the record")
    ok(by["c"]["records"] == [], "and its forecasts are taken out of the run")
    ok(status["c"]["notes"], "with a reason attached")
    ok(status["a"]["status"] == C.MODEL_OK, "the fresh models are untouched")

    status = {"a": st(issued="2026-09-23", issued_from="issued_time", flagged=1),
              "b": st(issued="2026-09-22", issued_from="issued_time", flagged=1)}
    _, dropped = run(models("a", "b"), status, None, out)
    ok(dropped == [], "one day behind is a late cycle, not a stale file")


def test_volume(out):
    print("\nVolume — a truncated download against a quiet day")
    root = os.path.join(out, "history")
    for i, n in enumerate([1000, 1100, 900, 1050, 950], start=10):
        MAN.write(os.path.join(root, f"2026-09-{i}"),
                  {"models": {"a": {"flagged": n, "status": C.MODEL_OK}}})
    ok(MAN.baseline("a", root) == 1000, "the baseline is the median of recent runs")

    status = {"a": st(issued="2026-09-23", issued_from="issued_time", flagged=100)}
    by = models("a")
    _, dropped = run(by, status, "2026-09-23", root)
    ok(status["a"]["status"] == C.MODEL_THIN, "a tenth of the usual volume reads as thin")
    ok(dropped == [], "but thin is a warning, never a drop")
    ok(by["a"]["records"] == [1], "and not one forecast is discarded over it")

    status = {"a": st(issued="2026-09-23", issued_from="issued_time", flagged=800)}
    run(models("a"), status, "2026-09-23", root)
    ok(status["a"]["status"] == C.MODEL_OK, "four fifths of the usual volume is an ordinary day")


def test_baseline_abstains(out):
    print("\nThe baseline abstains rather than guessing")
    ok(MAN.baseline("never-seen", out) is None, "an unknown model has no yardstick")

    two = os.path.join(out, "two")
    for d in ("2026-01-01", "2026-01-02"):
        MAN.write(os.path.join(two, d), {"models": {"a": {"flagged": 5, "status": C.MODEL_OK}}})
    ok(MAN.baseline("a", two) is None, "two samples is not enough to set one")

    untrusted = os.path.join(out, "untrusted")
    for i, (n, status) in enumerate([(10, C.MODEL_DROPPED)] * 3 + [(1000, C.MODEL_OK)], start=1):
        MAN.write(os.path.join(untrusted, f"2026-02-0{i}"),
                  {"models": {"a": {"flagged": n, "status": status}}})
    ok(MAN.baseline("a", untrusted) is None,
       "days the model was not trusted do not get to lower the bar")

    broken = os.path.join(out, "broken", "2026-03-01")
    os.makedirs(broken, exist_ok=True)
    with open(os.path.join(broken, C.MANIFEST_NAME), "w", encoding="utf-8") as f:
        f.write("{ not json")
    ok(MAN.read(broken) is None, "an unparseable run record reads as absent")
    ok(MAN.baseline("a", os.path.join(out, "broken")) is None,
       "and costs the baseline rather than the run")


def test_pointer(out):
    print("\nThe release pointer")
    C.write_release_pointer("2026-09-23", out)
    os.makedirs(os.path.join(out, "2026-09-23"), exist_ok=True)
    ok(C.current_release(out) == os.path.join(out, "2026-09-23"),
       "a written pointer is followed")

    os.makedirs(os.path.join(out, "2026-09-30"), exist_ok=True)
    ok(C.current_release(out) == os.path.join(out, "2026-09-23"),
       "even when a newer folder exists beside it — a rerun must not drift forward")

    with open(C.release_pointer(out), "w", encoding="utf-8") as f:
        f.write("2026-12-25\n")
    ok(C.current_release(out) == os.path.join(out, "2026-09-30"),
       "a pointer at a folder that is not there falls back to the newest")

    os.remove(C.release_pointer(out))
    ok(C.current_release(out) == os.path.join(out, "2026-09-30"),
       "and so does no pointer at all")


def test_suppression(out):
    """The GEOGLOWS blackout box. TEMPORARY — delete this with the box itself.

    Pinned because a blackout that silently stops working is the worse half of the
    bargain: the blob comes back and nobody connects it to a change here. And
    because a blackout that silently WIDENS is worse still.
    """
    print("\nThe GEOGLOWS blackout box")
    from common import models as M

    ok(getattr(C, "GEOGLOWS_BLACKOUT_BOX", None),
       f"a box is configured ({getattr(C, 'GEOGLOWS_BLACKOUT_BOX', None)})")

    saved = C.GEOGLOWS_BLACKOUT_BOX
    try:
        C.GEOGLOWS_BLACKOUT_BOX = (17.0, 57.0, 45.0, 71.0)
        path = os.path.join(out, "geoglows.csv")
        # Helsinki is inside; Lisbon and Nairobi are not. The fourth row carries no
        # coordinates at all, which is the case that must fail OPEN.
        with open(path, "w", encoding="utf-8") as f:
            f.write("comid,mean,ret_per,lat,lon\n")
            f.write("111,900,5,60.17,24.94\n")      # Helsinki — inside
            f.write("222,900,5,38.72,-9.14\n")      # Lisbon — outside
            f.write("333,900,5,-1.29,36.82\n")      # Nairobi — outside (lat below)
            f.write("444,900,5,,\n")                # no coordinate
        ids = {r["native_id"] for r in M.read_geoglows(path)}
        ok("111" not in ids, "a reach inside the box never leaves the reader")
        ok({"222", "333"} <= ids, f"reaches outside it are untouched ({sorted(ids)})")
        ok("444" in ids,
           "a reach with no coordinate is KEPT — a doubtful case must not be hidden")
        ok(M.suppressed_last == 1, f"and the count is recorded ({M.suppressed_last})")

        # Nairobi shares the box's longitude but not its latitude: both bounds have
        # to bite, or the blackout becomes a band around the planet.
        C.GEOGLOWS_BLACKOUT_BOX = (17.0, -5.0, 45.0, 71.0)
        ids = {r["native_id"] for r in M.read_geoglows(path)}
        ok("333" not in ids, "widening the latitude does reach Nairobi — both bounds bite")

        C.GEOGLOWS_BLACKOUT_BOX = None
        ids = {r["native_id"] for r in M.read_geoglows(path)}
        ok(len(ids) == 4, "and no box at all withholds nothing")
        ok(M.suppressed_last == 0, "with the count back to zero")
    finally:
        C.GEOGLOWS_BLACKOUT_BOX = saved


def main():
    out = tempfile.mkdtemp(prefix="fews-guards-")
    try:
        test_release_naming(out)
        test_staleness(out)
        test_volume(out)
        test_baseline_abstains(out)
        test_pointer(out)
        test_suppression(out)
    finally:
        shutil.rmtree(out, ignore_errors=True)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
