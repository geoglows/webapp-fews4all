#!/usr/bin/env python3
"""Daily step 1 — put the day's model files on disk, and nothing else.

WHY RETRIEVAL IS ITS OWN STEP

"Could not download" and "downloaded but unreadable" are different failures with
different answers, and interleaving them makes both harder to recover from. So
every model is fetched first, into one folder, and only then does step 2 read
anything. After an interruption there is exactly one question to ask — is the file
on disk and complete? — instead of one question per model per stage.

WHAT IT GUARANTEES

  Nothing half-written is visible. Every download goes to `<name>.part` and is
  renamed only after its size matches what the bucket advertised. A truncated
  119 MB CSV is still a valid, shorter CSV — step 2 would publish a thin release
  from it and nothing would look wrong — so the size check is the whole point.

  Rerunning is cheap. A file already present at the right size is skipped, so an
  interrupted morning resumes rather than re-downloading.

  One model cannot stop the others. Each is fetched inside a guard and recorded,
  the same bargain step 2 makes with its readers.

WHAT IT DOES NOT DO

  It does not choose the release date from the clock. The newest cycle is whatever
  the bucket says is newest — the feed has real gaps (2026-08-03, -06 and -13 are
  simply missing), and a computed filename 404s on an ordinary day.

  It does not decide anything about the forecast. Which timestep matters is the
  reader's business; this only puts bytes where the reader can find them.

    python3 backend/daily/step1_fetch.py
    python3 backend/daily/step1_fetch.py --cycle 2026102100     # a specific cycle
    python3 backend/daily/step1_fetch.py --product parquet      # override config
    python3 backend/daily/step1_fetch.py --dry-run              # plan only
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import config as C          # noqa: E402

S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
FETCH_RECORD = "fetch.json"
INPUT_POINTER = C.INPUT_POINTER
PARALLEL = 8
TIMEOUT = 120


# ---- talking to a public bucket --------------------------------------------

def _endpoint(bucket=None, region=None):
    return (f"https://{bucket or C.GEOGLOWS_BUCKET}"
            f".s3.{region or C.GEOGLOWS_REGION}.amazonaws.com")


def s3_list(prefix, delimiter=""):
    """(objects, folders) under `prefix`. Anonymous; the bucket is public.

    Paged, because a listing caps at 1000 keys and a cycle folder holds more than
    that between the CSVs and the parquets.
    """
    objects, folders, token = [], [], None
    while True:
        q = f"?list-type=2&prefix={urllib.parse.quote(prefix)}"
        if delimiter:
            q += f"&delimiter={urllib.parse.quote(delimiter)}"
        if token:
            q += f"&continuation-token={urllib.parse.quote(token)}"
        with urllib.request.urlopen(_endpoint() + q, timeout=TIMEOUT) as r:
            root = ET.fromstring(r.read())
        for c in root.findall(f"{S3_NS}Contents"):
            key = c.findtext(f"{S3_NS}Key") or ""
            size = int(c.findtext(f"{S3_NS}Size") or 0)
            if key and not key.endswith("/"):
                objects.append((key, size))
        for c in root.findall(f"{S3_NS}CommonPrefixes"):
            pre = c.findtext(f"{S3_NS}Prefix")
            if pre:
                folders.append(pre)
        if (root.findtext(f"{S3_NS}IsTruncated") or "").lower() != "true":
            return objects, folders
        token = root.findtext(f"{S3_NS}NextContinuationToken")
        if not token:
            return objects, folders


def latest_cycle():
    """The newest cycle stamp the bucket actually holds, or None.

    Asked rather than computed. The feed skips days, and a cycle lands about five
    hours after its nominal time, so "today at 00Z" is a filename that does not
    exist for much of the morning and does not exist at all on a skipped day.
    """
    _objs, folders = s3_list(C.GEOGLOWS_PREFIX, delimiter="/")
    stamps = sorted(f.rstrip("/").rsplit("/", 1)[-1] for f in folders)
    stamps = [s for s in stamps if re.fullmatch(r"\d{10}", s)]
    return stamps[-1] if stamps else None


# ---- what a cycle owes us ---------------------------------------------------

def release_of(cycle):
    """`2026102100` -> `2026-10-21`, the name the release will carry.

    The bucket's stamp is not a release name: step 2 derives the release from the
    input folder's basename, and both the pointer and the manifest only recognise
    YYYY-MM-DD. A folder called 2026102100 would be written, then ignored by
    everything downstream.
    """
    return f"{cycle[0:4]}-{cycle[4:6]}-{cycle[6:8]}"


def local_name(key, cycle):
    """What a fetched object is called on disk.

    The cycle stamp is appended to a CSV, because the bucket names those for the
    time the forecast is VALID and leaves the cycle in the folder — which the
    reader cannot see once the file is on its own. The parquets already carry it.
    """
    base = os.path.basename(key)
    if base.lower().endswith(".csv") and cycle not in base:
        stem, ext = os.path.splitext(base)
        return f"{stem}_{cycle}{ext}"
    return base


def plan_geoglows(cycle, product):
    """[(key, size)] for one cycle — the objects this product needs.

    For CSV that is exactly one object, the step named by GEOGLOWS_FORECAST_HOUR.
    For parquet it is every per-VPU file of that cycle, because a parquet cannot
    be subset by time before it is read.
    """
    objs, _ = s3_list(f"{C.GEOGLOWS_PREFIX}{cycle}/")
    if not objs:
        raise RuntimeError(f"cycle {cycle} holds no objects")

    if product == "parquet":
        hits = [(k, n) for k, n in objs if k.endswith(".parquet")]
        if not hits:
            raise RuntimeError(f"cycle {cycle} publishes no parquet files")
        return hits

    want = (f"mapstyletable_{release_of(cycle)}-"
            f"{C.GEOGLOWS_FORECAST_HOUR:02d}.csv")
    hits = [(k, n) for k, n in objs if os.path.basename(k) == want]
    if not hits:
        have = sorted(os.path.basename(k) for k, _ in objs
                      if k.endswith(".csv"))[:4]
        raise RuntimeError(
            f"cycle {cycle} publishes no {want}. "
            f"It carries {len(have)}+ CSV step(s), e.g. {', '.join(have)}. "
            f"Check GEOGLOWS_FORECAST_HOUR.")
    return hits


# ---- getting bytes onto disk ------------------------------------------------

def fetch_one(key, size, dest_dir, cycle):
    """(status, path). Skips a file already there at the right size.

    Downloaded to `.part` and renamed only once the byte count matches, because a
    half-written CSV reads as a complete shorter one and would be published
    without anything appearing to have gone wrong.
    """
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, local_name(key, cycle))
    if os.path.exists(path) and (not size or os.path.getsize(path) == size):
        return "skipped", path

    tmp = path + ".part"
    url = f"{_endpoint()}/{urllib.parse.quote(key)}"
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)

    got = os.path.getsize(tmp)
    if size and got != size:
        os.remove(tmp)
        raise RuntimeError(f"{os.path.basename(key)}: got {got:,} bytes, "
                           f"the bucket advertised {size:,}")
    os.replace(tmp, path)
    return "fetched", path


def fetch_all(plan, dest_dir, cycle):
    os.makedirs(dest_dir, exist_ok=True)
    done, failed = [], []

    def one(item):
        key, size = item
        try:
            return key, fetch_one(key, size, dest_dir, cycle), None
        except Exception as e:                       # one object is not the run
            return key, (None, None), e

    workers = min(PARALLEL, max(1, len(plan)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for key, (status, path), err in pool.map(one, plan):
            if err:
                failed.append((key, err))
            else:
                done.append((key, status, path))
    return done, failed


# ---- the run ----------------------------------------------------------------

def write_pointer(release, root=None):
    """Which input folder step 2 should read, the mirror of current_release.txt."""
    root = root or C.INPUT_DIR
    p = os.path.join(root, INPUT_POINTER)
    tmp = p + ".tmp"
    os.makedirs(root, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(release.strip() + "\n")
    os.replace(tmp, p)
    return p


def main(cycle=None, product=None, dry_run=False, input_root=None):
    t0 = time.time()
    product = (product or C.GEOGLOWS_PRODUCT).lower()
    root = input_root or C.INPUT_DIR
    record = {"fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "models": {}}

    print(f"Bucket: {C.GEOGLOWS_BUCKET}/{C.GEOGLOWS_PREFIX}")
    try:
        cycle = cycle or latest_cycle()
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach the bucket: {e}\n"
                 f"  Nothing was written; the last input folder is untouched.")
    if not cycle:
        sys.exit("The bucket lists no cycle folders.")

    release = release_of(cycle)
    dest = os.path.join(root, release)
    print(f"Cycle:  {cycle}   ->  release {release}")
    print(f"Into:   {dest}\n")

    row = {"status": C.MODEL_SKIPPED, "cycle": cycle, "product": product,
           "notes": []}
    record["models"]["geoglows"] = row
    try:
        plan = plan_geoglows(cycle, product)
        total = sum(n for _k, n in plan)
        print(f"  geoglows   {len(plan)} object(s), {total / 1e6:,.0f} MB")
        if dry_run:
            for k, n in plan[:6]:
                print(f"               {os.path.basename(k):<48} {n / 1e6:>7.1f} MB")
            if len(plan) > 6:
                print(f"               ... and {len(plan) - 6} more")
            print(f"\nDry run — nothing written.  {time.time() - t0:.0f}s")
            return release

        done, failed = fetch_all(plan, dest, cycle)
        got = sum(1 for _k, s, _p in done if s == "fetched")
        skipped = sum(1 for _k, s, _p in done if s == "skipped")
        row.update({"objects": len(plan), "bytes": total,
                    "fetched": got, "skipped": skipped,
                    "files": sorted(os.path.basename(p) for _k, _s, p in done)})
        if failed:
            row["status"] = C.MODEL_DROPPED
            for k, e in failed:
                row["notes"].append(f"{os.path.basename(k)}: {e}")
            print(f"  geoglows   {len(failed)} object(s) FAILED", file=sys.stderr)
            for k, e in failed[:5]:
                print(f"               {os.path.basename(k)}: {e}", file=sys.stderr)
        else:
            row["status"] = C.MODEL_OK
            print(f"  geoglows   {got} fetched, {skipped} already present "
                  f"({time.time() - t0:.0f}s)")
    except Exception as e:
        row["status"] = C.MODEL_DROPPED
        row["notes"].append(f"{type(e).__name__}: {e}")
        print(f"  geoglows   FAILED: {e}", file=sys.stderr)

    # The other three are not automated yet. Said out loud rather than left to be
    # noticed, because a release missing them is a different thing from a release
    # that chose to leave them out.
    for model in ("flood_hub", "glofas", "flash"):
        record["models"][model] = {
            "status": C.MODEL_SKIPPED,
            "notes": ["not automated yet; place the file in the folder by hand"],
        }
        print(f"  {model:<10} not automated yet — place its file in {dest}")

    os.makedirs(dest, exist_ok=True)
    tmp = os.path.join(dest, FETCH_RECORD + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, os.path.join(dest, FETCH_RECORD))

    if record["models"]["geoglows"]["status"] == C.MODEL_OK:
        write_pointer(release, root)
        print(f"\n{INPUT_POINTER} -> {release}")
    else:
        print(f"\n{INPUT_POINTER} left alone — geoglows did not land cleanly",
              file=sys.stderr)

    print(f"Done in {time.time() - t0:.0f}s")
    return release


if __name__ == "__main__":
    a = sys.argv[1:]
    main(cycle=a[a.index("--cycle") + 1] if "--cycle" in a else None,
         product=a[a.index("--product") + 1] if "--product" in a else None,
         dry_run="--dry-run" in a)
