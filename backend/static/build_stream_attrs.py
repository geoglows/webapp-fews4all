#!/usr/bin/env python3
"""Static build — river network topology, lifted out of the stream tiles.

Step 2 has to decide what a hexagon holding several flagged reaches is actually
looking at: one river reported twice, two rivers meeting, or two rivers that never
meet at all. Those are questions about the NETWORK, and the daily GEOGLOWS file
answers none of them — it carries a reach id, a flow and a return period.

The v3 stream tiles do carry it: `nextRiverId` (the reach immediately downstream),
`outletRiverId` (where the water finally leaves the network) and `DSContArea` (the
catchment draining to this reach's outlet). This sweeps them into a flat table so
the daily run is a dictionary lookup rather than a 3.4 GB archive read.

Two things worth knowing about the source:

  * A terminal reach carries `nextRiverId = -1`, not its own id. Comparing two
    reaches on `nextRiverId` alone therefore makes every pair of river MOUTHS look
    like a confluence. The sentinel is written out as a blank here so a caller
    cannot fall into that; it cost two false confluences in 127 when measured.

  * `DSContArea` really is upstream drainage area: across 1,165 junctions the
    child reach's area came to the sum of its parents' to within 0.5%, and the
    leftover matched `areaM2`, which is the reach's own increment.

    python3 backend/static/build_stream_attrs.py
"""

import csv
import gzip
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from common import config as C          # noqa: E402
from mvt import layer_properties        # noqa: E402
from pmtiles_merge import Archive, zoom_of   # noqa: E402

SOURCE_LAYER = "streams"
# Only the deepest zoom is read. Tilers thin features at coarse zooms, so a sweep
# of anything shallower silently drops reaches; the maxzoom level is the only one
# that carries the whole network.
COLUMNS = ["river_id", "next_id", "outlet_id", "ds_cont_area", "strahler"]


def sweep(archive_path, out_path):
    """Stream every reach to disk, then sort and dedupe there.

    The whole network is ~6.8M reaches. Holding them in a list, with a set to
    dedupe the ones that span tiles, needs more memory than this is worth and was
    killed twice doing it. Rows go straight out instead, duplicates and all, and
    `sort -u` removes them afterwards — identical lines, so nothing else is needed.
    """
    a = Archive(archive_path)
    entries = [e for e in a.entries() if zoom_of(e[0]) == a.maxzoom]
    print(f"  {os.path.basename(archive_path)}: {len(entries):,} tile(s) at z{a.maxzoom}")

    # Resumable in slices. The sweep is only a couple of minutes end to end, but
    # it is often run where a long-lived process is not guaranteed, so progress is
    # recorded after each slice and a rerun picks up where it stopped.
    raw_path = out_path + ".raw"
    mark_path = out_path + ".progress"
    done = 0
    if os.path.exists(mark_path) and os.path.exists(raw_path):
        try:
            done = int(open(mark_path).read().strip())
        except ValueError:
            done = 0
    if done >= len(entries):
        print(f"  sweep already complete ({done:,} tiles)")
    todo = entries[done:]
    limit = _arg_int("--limit")
    if limit:
        todo = todo[:limit]
    print(f"  resuming at tile {done:,}; this pass covers {len(todo):,}")

    t0, written = time.time(), 0
    with open(raw_path, "a" if done else "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for n, (_tid, off, ln) in enumerate(todo):
            blob = a.tile(off, ln)
            try:
                blob = gzip.decompress(blob)
            except Exception:
                pass
            for p in layer_properties(blob, SOURCE_LAYER):
                rid = p.get("riverId")
                if rid is None:
                    continue
                nxt = p.get("nextRiverId")
                nxt = int(nxt) if nxt is not None else None
                w.writerow([
                    int(rid),
                    "" if nxt is None or nxt < 0 else nxt,   # -1 is "no downstream"
                    int(p["outletRiverId"]) if p.get("outletRiverId") is not None else "",
                    p.get("DSContArea", ""),
                    p.get("strahlerOrder", ""),
                ])
                written += 1
            if n and n % 100000 == 0:
                print(f"    {n:,}/{len(entries):,} tiles, {written:,} rows, "
                      f"{time.time() - t0:.0f}s", flush=True)

    done += len(todo)
    with open(mark_path, "w") as m:
        m.write(str(done))
    if done < len(entries):
        print(f"  paused at {done:,}/{len(entries):,} tiles ({written:,} rows this pass). "
              f"Run again to continue.")
        return
    print(f"  sweeping done ({done:,} tiles); sorting...", flush=True)
    tmp = out_path + ".sorted"
    rc = subprocess.run(["sort", "-u", "-t,", "-k1,1n", "-S", "256M",
                         "-T", os.path.dirname(out_path), "-o", tmp, raw_path])
    if rc.returncode != 0:
        sys.exit("sort failed")
    with open(out_path, "w", encoding="utf-8") as out, open(tmp) as body:
        out.write(",".join(COLUMNS) + "\n")
        shutil.copyfileobj(body, out)
    for junk in (raw_path, tmp, mark_path):
        try:
            os.remove(junk)
        except OSError:
            pass
    rows = sum(1 for _ in open(out_path, encoding="utf-8")) - 1
    print(f"\n  {rows:,} reach(es) -> {out_path}  "
          f"{os.path.getsize(out_path) / 1e6:.1f} MB  {time.time() - t0:.0f}s")


def _arg_int(flag):
    if flag in sys.argv:
        try:
            return int(sys.argv[sys.argv.index(flag) + 1])
        except (IndexError, ValueError):
            return None
    return None


def main():
    src = C.STREAM_TILES_PATH
    if not os.path.exists(src):
        sys.exit(f"Not found: {src}")
    sweep(src, C.STREAM_ATTRS_CSV)


if __name__ == "__main__":
    main()
