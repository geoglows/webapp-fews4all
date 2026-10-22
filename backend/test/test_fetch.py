#!/usr/bin/env python3
"""Step 1's retrieval logic, exercised without a network.

    python3 backend/test/test_fetch.py

The failures this step exists to survive are the ones you cannot arrange on
demand: a truncated download, a cycle that never landed, a morning rerun after an
interruption. So the bucket is faked here — the listing XML and the object bodies
are served from memory — and what is tested is the part that decides, not the part
that transfers.

Nothing here touches the real bucket, the real input folder, or the network.
"""

import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "daily"))

from common import config as C          # noqa: E402
import step1_fetch as F                 # noqa: E402

PASS, FAIL = [], []


def ok(cond, what):
    (PASS if cond else FAIL).append(what)
    print(("  pass  " if cond else "  FAIL  ") + what)


# ---- a bucket in memory ----------------------------------------------------

NS = 'xmlns="http://s3.amazonaws.com/doc/2006-03-01/"'
CYCLES = ["2026101800", "2026101900", "2026102000", "2026102100"]
BODY = b"comid,mean,thickness,ret_per\n" + b"1,0,1,0\n" * 500


def _objects(cycle):
    out = [(f"map-tables/{cycle}/mapstyletable_"
            f"{cycle[0:4]}-{cycle[4:6]}-{cycle[6:8]}-{h:02d}.csv", len(BODY))
           for h in range(0, 24, 3)]
    out += [(f"map-tables/{cycle}/mapstyletable_{v}_{cycle}.parquet", 1000 + v)
            for v in (101, 102, 201)]
    return out


class FakeBucket:
    """Serves listings and bodies, and can be told to truncate a download."""

    def __init__(self):
        self.truncate = set()
        self.calls = 0

    def open(self, url, timeout=None):
        self.calls += 1
        if "list-type=2" in url:
            return io.BytesIO(self._listing(url))
        key = urllib_unquote(url.split(".amazonaws.com/", 1)[1])
        body = BODY[:10] if key in self.truncate else BODY
        return io.BytesIO(body)

    def _listing(self, url):
        q = dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&"))
        prefix = urllib_unquote(q.get("prefix", ""))
        token = q.get("continuation-token")
        if "delimiter" in q:
            pre = "".join(f"<CommonPrefixes><Prefix>map-tables/{c}/</Prefix>"
                          f"</CommonPrefixes>" for c in CYCLES)
            return (f'<?xml version="1.0"?><ListBucketResult {NS}>{pre}'
                    f"<IsTruncated>false</IsTruncated></ListBucketResult>").encode()
        cycle = prefix.rstrip("/").rsplit("/", 1)[-1]
        objs = _objects(cycle) if cycle in CYCLES else []
        # deliberately paged, so the continuation path is exercised
        half = len(objs) // 2
        page, more = (objs[:half], True) if not token else (objs[half:], False)
        body = "".join(f"<Contents><Key>{k}</Key><Size>{n}</Size></Contents>"
                       for k, n in page)
        trunc = "true" if more else "false"
        nxt = "<NextContinuationToken>T</NextContinuationToken>" if more else ""
        return (f'<?xml version="1.0"?><ListBucketResult {NS}>{body}'
                f"<IsTruncated>{trunc}</IsTruncated>{nxt}"
                f"</ListBucketResult>").encode()


def urllib_unquote(s):
    import urllib.parse
    return urllib.parse.unquote(s)


class _Ctx:
    def __init__(self, b): self.b = b
    def __enter__(self): return self.b
    def __exit__(self, *a): self.b.close(); return False


def install(bucket):
    import urllib.request
    urllib.request.urlopen = lambda url, timeout=None: _Ctx(bucket.open(url, timeout))


# ---- the tests -------------------------------------------------------------

def test_discovery():
    print("\nFinding the cycle")
    ok(F.latest_cycle() == "2026102100",
       "the newest cycle comes from the bucket, not from the clock")
    ok(F.release_of("2026102100") == "2026-10-21",
       "the cycle stamp becomes a YYYY-MM-DD release name")
    ok(not __import__("re").fullmatch(r"\d{4}-\d{2}-\d{2}", "2026102100"),
       "...which the raw stamp would fail, so the pointer would be ignored")


def test_plan():
    print("\nChoosing what to download")
    csvs = F.plan_geoglows("2026102100", "csv")
    ok(len(csvs) == 1, f"csv: exactly one object, the configured step ({len(csvs)})")
    ok(f"-{C.GEOGLOWS_FORECAST_HOUR:02d}.csv" in csvs[0][0],
       f"csv: it is the {C.GEOGLOWS_FORECAST_HOUR:02d}Z step")

    pqs = F.plan_geoglows("2026102100", "parquet")
    ok(len(pqs) == 3, f"parquet: every per-VPU file of the cycle ({len(pqs)})")
    ok(all(k.endswith(".parquet") for k, _ in pqs), "parquet: and only those")

    try:
        F.plan_geoglows("2026010100", "csv")
        ok(False, "a cycle the bucket does not hold raises")
    except RuntimeError:
        ok(True, "a cycle the bucket does not hold raises rather than writing")

    keep = C.GEOGLOWS_FORECAST_HOUR
    C.GEOGLOWS_FORECAST_HOUR = 7                       # not a published step
    try:
        F.plan_geoglows("2026102100", "csv")
        ok(False, "an unpublished hour raises")
    except RuntimeError as e:
        ok("GEOGLOWS_FORECAST_HOUR" in str(e),
           "an unpublished hour names the setting to check")
    finally:
        C.GEOGLOWS_FORECAST_HOUR = keep


def test_transfer(bucket, tmp):
    print("\nGetting bytes onto disk")
    dest = os.path.join(tmp, "2026-10-21")
    plan = F.plan_geoglows("2026102100", "csv")
    key, size = plan[0]

    status, path = F.fetch_one(key, size, dest, "2026102100")
    ok(status == "fetched" and os.path.getsize(path) == size, "a file downloads")
    ok("_2026102100.csv" in os.path.basename(path),
       "and is saved with the cycle stamp the bucket keeps in the folder name")

    status, _ = F.fetch_one(key, size, dest, "2026102100")
    ok(status == "skipped", "a second run skips it rather than re-downloading")

    with open(path, "ab") as f:
        f.write(b"x")
    status, _ = F.fetch_one(key, size, dest, "2026102100")
    ok(status == "fetched", "a file of the wrong size is fetched again")

    os.remove(path)
    bucket.truncate.add(key)
    try:
        F.fetch_one(key, size, dest, "2026102100")
        ok(False, "a truncated download is refused")
    except RuntimeError as e:
        ok("advertised" in str(e), "a truncated download is refused, with both sizes")
    bucket.truncate.discard(key)
    ok(not os.path.exists(path), "...and leaves no file behind")
    ok(not [f for f in os.listdir(dest) if f.endswith(".part")],
       "...and no .part either")


def test_run(tmp):
    print("\nA whole run")
    rel = F.main(input_root=tmp)
    dest = os.path.join(tmp, rel)
    ok(rel == "2026-10-21", f"the release is named for the cycle ({rel})")

    with open(os.path.join(dest, F.FETCH_RECORD), encoding="utf-8") as f:
        rec = json.load(f)
    ok(rec["models"]["geoglows"]["status"] == C.MODEL_OK, "geoglows is recorded ok")
    ok(rec["models"]["glofas"]["status"] == C.MODEL_SKIPPED,
       "the models that are not automated say so rather than being omitted")

    with open(os.path.join(tmp, F.INPUT_POINTER), encoding="utf-8") as f:
        ok(f.read().strip() == rel, "the pointer names the folder step 2 should read")

    # a run that cannot fetch must not move the pointer onto a bad folder
    before = open(os.path.join(tmp, F.INPUT_POINTER), encoding="utf-8").read()
    F.main(cycle="2026010100", input_root=tmp)
    after = open(os.path.join(tmp, F.INPUT_POINTER), encoding="utf-8").read()
    ok(before == after, "a failed fetch leaves the pointer on the last good folder")


def main():
    bucket = FakeBucket()
    install(bucket)
    tmp = tempfile.mkdtemp(prefix="fews-fetch-")
    try:
        test_discovery()
        test_plan()
        test_transfer(bucket, tmp)
        test_run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
