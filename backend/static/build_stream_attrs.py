#!/usr/bin/env python3
"""Static build — river network topology, read from the GEOGLOWS v2 model table.

Step 2 has to decide what a hexagon holding several flagged reaches is actually
looking at: one river reported twice, two rivers meeting, or two rivers that never
meet at all. Those are questions about the NETWORK, and the daily GEOGLOWS file
answers none of them — it carries a reach id, a flow and a return period.

WHICH NETWORK
-------------
v2, because the forecasts are v2. This table used to be swept out of the v3 stream
TILES, which carry the same three facts under different names — and that was wrong
in a way that looked like nothing: v3 is a different network with a different id
space, overlapping v2 by 68.5%. The sweep succeeded, the table was 4.9M reaches
and perfectly well-formed, and then on a measured release 47.4% of the flagged
reaches were not in it. `streams.py` never merges a reach it does not know, so
those cells kept reporting four rivers where they hold one — 3,572 of 3,591
multi-reach cells unresolved. Nothing errored anywhere.

The model table is the same file `h3_streams_global_r6.csv` was built from: its
DSLINKNO is what produced the crosswalk's `report_id`. So the topology here and
the topology behind the crosswalk are now one source, over one id set.

THE COLUMNS
-----------
    LINKNO        -> river_id
    DSLINKNO      -> next_id       the reach immediately downstream
    TerminalLink  -> outlet_id     where the water finally leaves the network
    DSContArea    -> ds_cont_area  catchment draining to this reach's outlet
    strmOrder     -> strahler

Two things worth knowing about the source:

  * A terminal reach carries `DSLINKNO = -1`, not its own id (34,621 of them).
    Comparing two reaches on `next_id` alone would therefore make every pair of
    river MOUTHS look like a confluence. The sentinel is written out as a blank
    here so a caller cannot fall into that; `Network.load` leaves a blank absent.
    `TerminalLink` has no sentinel — a terminal reach is its own outlet, checked.

  * `DSContArea` really is upstream drainage area: across 1,165 junctions the
    child reach's area came to the sum of its parents' to within 0.5%.

    python3 backend/static/build_stream_attrs.py
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from common import config as C          # noqa: E402

COLUMNS = ["river_id", "next_id", "outlet_id", "ds_cont_area", "strahler"]
# Output column <- model table column.
SOURCE = {
    "river_id": "LINKNO",
    "next_id": "DSLINKNO",
    "outlet_id": "TerminalLink",
    "ds_cont_area": "DSContArea",
    "strahler": "strmOrder",
}


def build(src, out_path):
    """Read the five topology columns, blank the terminal sentinel, write sorted.

    The whole network is 6.8M reaches and five narrow columns, which is small
    enough to hold — unlike the tile sweep this replaces, which had to stream to
    disk and `sort -u` afterwards because reaches span tiles and arrive many times
    over. A parquet column read has each reach exactly once.
    """
    import pandas as pd

    t0 = time.time()
    df = pd.read_parquet(src, columns=[SOURCE[c] for c in COLUMNS])
    df.columns = COLUMNS
    print(f"  {os.path.basename(src)}: {len(df):,} reach(es)  "
          f"{time.time() - t0:.0f}s")

    # Guards. Each of these held when measured; they are here because the failure
    # this file exists to undo was a silent one, and a wrong table looks fine.
    if not df.river_id.is_unique:
        sys.exit("river_id is not unique in the model table")
    bad = int((df.outlet_id <= 0).sum())
    if bad:
        sys.exit(f"{bad:,} reach(es) carry a non-positive outlet_id")
    term = df[df.next_id <= 0]
    off = int((term.river_id != term.outlet_id).sum())
    if off:
        sys.exit(f"{off:,} terminal reach(es) are not their own outlet")
    if not df.outlet_id.isin(set(df.river_id)).all():
        sys.exit("some outlet_id is not itself a river_id")

    df = df.sort_values("river_id", ignore_index=True)
    # -1 means "no downstream". Written blank so a caller cannot compare two
    # river mouths and read a confluence.
    df["next_id"] = df.next_id.where(df.next_id > 0).astype("Int64")
    df["strahler"] = df.strahler.astype("Int64")

    tmp = out_path + ".tmp"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.to_csv(tmp, index=False)
    os.replace(tmp, out_path)

    print(f"  terminal reach(es): {len(term):,} (next_id written blank)")
    print(f"\n  {len(df):,} reach(es) -> {out_path}  "
          f"{os.path.getsize(out_path) / 1e6:.1f} MB  {time.time() - t0:.0f}s")


def main():
    src = C.STREAMS_V2_MODEL_TABLE
    if not os.path.exists(src):
        sys.exit(f"Not found: {src}")
    build(src, C.STREAM_ATTRS_CSV)


if __name__ == "__main__":
    main()
