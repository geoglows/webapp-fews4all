#!/usr/bin/env python3
"""Probe — does forecast DAY N flag like DAY 0? Run by hand, answers one question.

WHY THIS EXISTS
---------------
Every size estimate for the per-day timeline rests on one assumption: that the
number of reaches flagged on forecast day 7 is about the same as on day 0. The
daily pipeline cannot check it, because it downloads ONE step (see
GEOGLOWS_PRODUCT in common/config.py) and one step cannot be compared with
itself.

It matters because the assumption is the x14 multiplier in the archive sizing.
At 2,700 flags a day a release is ~5 MB; at 13,000 it is ~26 MB. And it is not
obviously flat: the value thresholded is the ENSEMBLE MEAN, and means flatten as
members spread apart at longer leads, so day 7 may well flag fewer than day 0.
If it falls off steeply, the far end of the timeline is mostly empty and worth
less than it costs.

WHAT IT DOES
------------
Downloads a handful of per-VPU parquets from ONE cycle -- each carries the whole
horizon at 3-hourly steps -- and counts flags per step under the same rule the
pipeline uses. A few VPUs rather than all 161: the shape of the curve is what is
being asked, and ~10 MB a file answers it where 1.7 GB would answer it twice.

It reports two numbers per day, because they are different questions:

    morning   the step at GEOGLOWS_FORECAST_HOUR -- today's policy, repeated
    day-max   the worst step of that day -- the policy we may move to

The gap between those two columns is the cost of the morning rule, measured
across the horizon rather than on the single VPU in config.py's comment.

    python3 backend/static/probe_lead_times.py              # 6 VPUs, latest cycle
    python3 backend/static/probe_lead_times.py --vpus 12    # tighter sample
    python3 backend/static/probe_lead_times.py --cycle 2026110400
"""

import argparse
import collections
import os
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from common import config as C          # noqa: E402

S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


def base():
    return (f"https://{C.GEOGLOWS_BUCKET}"
            f".s3.{C.GEOGLOWS_REGION}.amazonaws.com")


def s3_list(prefix, delimiter=""):
    """(keys, folders) under a prefix. Public bucket, so no credentials."""
    keys, folders, token = [], [], None
    while True:
        url = f"{base()}/?list-type=2&prefix={urllib.parse.quote(prefix)}"
        if delimiter:
            url += f"&delimiter={delimiter}"
        if token:
            url += f"&continuation-token={urllib.parse.quote(token)}"
        with urllib.request.urlopen(url, timeout=60) as r:
            root = ET.fromstring(r.read())
        for c in root.findall(f"{S3_NS}Contents"):
            keys.append((c.findtext(f"{S3_NS}Key"),
                         int(c.findtext(f"{S3_NS}Size") or 0)))
        for p in root.findall(f"{S3_NS}CommonPrefixes"):
            folders.append(p.findtext(f"{S3_NS}Prefix"))
        token = root.findtext(f"{S3_NS}NextContinuationToken")
        if root.findtext(f"{S3_NS}IsTruncated") != "true" or not token:
            return keys, folders


def pick_cycle(want):
    _, folders = s3_list(C.GEOGLOWS_PREFIX, delimiter="/")
    cycles = sorted(f.rstrip("/").rsplit("/", 1)[-1] for f in folders)
    if not cycles:
        sys.exit("the bucket lists no cycles")
    if want:
        if want not in cycles:
            sys.exit(f"cycle {want} not in the bucket; newest is {cycles[-1]}")
        return want
    return cycles[-1]


def sample(keys, n):
    """n parquets spread evenly through the sorted list, for a spread of VPUs."""
    pq = sorted(k for k, _ in keys if k.endswith(".parquet"))
    if not pq:
        return []
    if n >= len(pq):
        return pq
    step = len(pq) / n
    return [pq[int(i * step)] for i in range(n)]


def grab(key, dest):
    path = os.path.join(dest, os.path.basename(key))
    url = f"{base()}/{urllib.parse.quote(key)}"
    with urllib.request.urlopen(url, timeout=600) as r, open(path, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vpus", type=int, default=6)
    ap.add_argument("--cycle", default=None)
    ap.add_argument("--keep", action="store_true",
                    help="leave the downloaded parquets on disk")
    a = ap.parse_args()

    import pyarrow.compute as pc
    import pyarrow.dataset as ds

    cycle = pick_cycle(a.cycle)
    keys, _ = s3_list(f"{C.GEOGLOWS_PREFIX}{cycle}/")
    want = sample(keys, a.vpus)
    if not want:
        sys.exit(f"cycle {cycle} publishes no parquet files "
                 f"({len(keys)} object(s) there). Try an older cycle.")
    size = sum(s for k, s in keys if k in set(want))
    print(f"  cycle {cycle}: {sum(1 for k, _ in keys if k.endswith('.parquet'))} "
          f"parquet(s); sampling {len(want)} ({size / 1e6:.0f} MB)\n")

    floor = min(yr for yr, _ in C.GEOGLOWS_THRESHOLDS)
    minflow = getattr(C, "GEOGLOWS_MIN_MEAN_FLOW", 0)
    per_step = collections.Counter()

    tmp = tempfile.mkdtemp(prefix="geoglows-probe-")
    try:
        for i, k in enumerate(want, 1):
            print(f"  [{i}/{len(want)}] {os.path.basename(k)}", flush=True)
            path = grab(k, tmp)
            t = ds.dataset(path, format="parquet").to_table(
                columns=["timestamp"],
                filter=(pc.field("ret_per") >= floor)
                & (pc.field("mean") >= minflow))
            for ts in t["timestamp"].to_pylist():
                per_step[ts] += 1
    finally:
        if not a.keep:
            for f in os.listdir(tmp):
                os.remove(os.path.join(tmp, f))
            os.rmdir(tmp)
        else:
            print(f"\n  parquets left in {tmp}")

    if not per_step:
        sys.exit("\n  nothing cleared the threshold in this sample — "
                 "try more VPUs or a wetter cycle")

    steps = sorted(per_step)
    day0 = steps[0].date()
    by_day = collections.defaultdict(dict)
    for ts in steps:
        by_day[(ts.date() - day0).days][ts.hour] = per_step[ts]

    print(f"\n  flags per forecast day  "
          f"(ret_per >= {floor}, mean >= {minflow}; {len(want)} VPUs)\n")
    print(f"  {'day':>4} {'morning':>9} {'day-max':>9} {'steps':>6} "
          f"{'vs day 0':>9} {'morning/max':>12}")
    print("  " + "-" * 56)

    hour = C.GEOGLOWS_FORECAST_HOUR
    tot_morning = tot_max = 0
    first_morning = None
    for d in sorted(by_day):
        hours = by_day[d]
        morning = hours.get(hour, 0)
        dmax = max(hours.values())
        if first_morning is None and morning:
            first_morning = morning
        tot_morning += morning
        tot_max += dmax
        rel = f"{morning / first_morning:.2f}" if first_morning else "-"
        frac = f"{morning / dmax:.0%}" if dmax else "-"
        print(f"  {d:>4} {morning:>9,} {dmax:>9,} {len(hours):>6} "
              f"{rel:>9} {frac:>12}")

    print("  " + "-" * 56)
    print(f"  {'tot':>4} {tot_morning:>9,} {tot_max:>9,}")
    print(f"\n  horizon: {len(by_day)} day(s), {len(steps)} step(s)")
    if first_morning:
        print(f"  summed across the horizon, the morning rule yields "
              f"{tot_morning / first_morning:.1f}x day 0 "
              f"(a flat curve would give {len(by_day)}.0x)")
        print(f"  the morning rule sees {tot_morning / tot_max:.0%} of what a "
              f"day-max rule would")
    print("\n  Scale to a global row count: multiply the day-0 column by\n"
          "  (global day-0 flags) / (this sample's day 0), then apply the\n"
          "  multiplier above. The blackout box removes ~30% after this.")


if __name__ == "__main__":
    main()
