#!/usr/bin/env python3
"""
build_streams.py — the TDX-Hydro stream network as a telescoping context layer.

TDX-Hydro is the hydrography GEOGLOWS runs its forecasts on, so this layer and the
flagged cells are describing the same rivers — the app labels it "TDX-Hydro Streams"
rather than just "rivers" to make that explicit.

Self-contained: stream the source once in batches, keep memory flat, write one
GeoJSON the app filters client-side.

Two inputs have to be joined, because the geometry file carries nothing but an id:

  * global_streams_simplified.gpkg   LINKNO + geometry, 6.84M reaches, no attributes
  * v2-model-table.parquet           LINKNO -> strmOrder (same 6.84M keys, 1:1)

Only reaches at or above MIN_ORDER are written — the whole network is ~1.4 GB of
GeoJSON, which no browser should be asked to fetch. Order 5 and up is 843k reaches;
that threshold was measured, not guessed: rasterising each order band over River
Forecast System v3's own viewport (zoom 8 over Louisiana) at its line width gives
3.3% pixel coverage at order 6, 5.3% at order 5 and 9.4% at order 4, against 5.6%
measured from RFS itself. Finer channels stay the job of the per-basin streams
layer, which is drawn only for the basin you select.

Each feature carries `ord` so the front end can telescope: at low zoom it draws only
the highest orders and reveals the rest as you zoom in (see layers/streams.js).

Reads:  ../Files/global_streams_simplified.gpkg
        ../Other/matching/files/v2-model-table.parquet
Writes: ../public/data_streams.geojson    kind "streams-global"
"""

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                        # <repo>
FILES = os.path.join(ROOT, "Files")
PUBLIC = os.path.join(ROOT, "public")   # the app reads its data from here
os.makedirs(PUBLIC, exist_ok=True)

STREAMS_GPKG = os.path.join(FILES, "global_streams_simplified.gpkg")
MODEL_TABLE = os.path.join(ROOT, "Other", "matching", "files", "v2-model-table.parquet")
OUTPUT = os.path.join(PUBLIC, "data_streams.geojson")

ID_COL = "LINKNO"
MIN_ORDER = 5          # see the note above before lowering this
READ_BATCH = 200_000   # reaches per streamed read (the gpkg is 2.1 GB)
COORD_DP = 5           # ~1 m at the equator; halves the file next to full precision


def load_orders(path, min_order):
    """{LINKNO: strmOrder} for reaches at or above min_order.

    Filtered while streaming so only the reaches we will actually write are held
    in memory — the full table is 6.84M rows.
    """
    import pyarrow.parquet as pq

    if not os.path.exists(path):
        sys.exit(f"Not found: {path}\n"
                 f"  The geometry file has no stream order; it comes from this table.")
    out = {}
    pf = pq.ParquetFile(path)
    scanned = 0
    for batch in pf.iter_batches(batch_size=500_000, columns=[ID_COL, "strmOrder"]):
        d = batch.to_pydict()
        scanned += len(d[ID_COL])
        for lid, order in zip(d[ID_COL], d["strmOrder"]):
            if order is not None and order >= min_order:
                out[lid] = order
    print(f"Model table: {scanned:,} reach(es) scanned, "
          f"{len(out):,} at order >= {min_order}.")
    return out


def coords_of(geom, dp):
    """A reach's coordinates, rounded, as one or more line strings."""
    parts = geom.geoms if geom.geom_type == "MultiLineString" else [geom]
    out = []
    for part in parts:
        line = [[round(x, dp), round(y, dp)] for x, y in part.coords]
        # Rounding can collapse neighbouring vertices; drop the repeats.
        squashed = [p for i, p in enumerate(line) if i == 0 or p != line[i - 1]]
        if len(squashed) >= 2:
            out.append(squashed)
    return out


def main():
    try:
        from pyogrio.raw import read
        import pyogrio
        from shapely import from_wkb
    except ImportError as e:
        sys.exit(f"Missing dependency: {e.name}.\n"
                 f"    conda install -c conda-forge gdal\n"
                 f"    pip install pyogrio shapely pyarrow")

    for p in (STREAMS_GPKG, MODEL_TABLE):
        if not os.path.exists(p):
            sys.exit(f"Not found: {p}")

    t0 = time.time()
    orders = load_orders(MODEL_TABLE, MIN_ORDER)

    info = pyogrio.read_info(STREAMS_GPKG)
    total = info["features"]
    if ID_COL not in list(info["fields"]):
        sys.exit(f"{os.path.basename(STREAMS_GPKG)} has no {ID_COL!r} column "
                 f"(found: {list(info['fields'])}).")
    print(f"Streams: {total:,} reach(es) in {os.path.basename(STREAMS_GPKG)}.")

    # Features are written as they are produced, so the whole network is never
    # held in memory and the output file is the only thing that grows.
    kept = skipped = done = 0
    by_order = {}
    with open(OUTPUT, "w", encoding="utf-8") as out:
        out.write('{"type":"FeatureCollection","kind":"streams-global",'
                  f'"min_order":{MIN_ORDER},"features":[')
        first = True
        while done < total:
            _meta, _fid, geom, fields = read(
                STREAMS_GPKG, columns=[ID_COL],
                skip_features=done, max_features=min(READ_BATCH, total - done))
            n = len(geom)
            if n == 0:
                break
            ids = fields[0]
            wanted = [i for i, lid in enumerate(ids) if int(lid) in orders]
            if wanted:
                geoms = from_wkb([bytes(geom[i]) for i in wanted])
                for k, i in enumerate(wanted):
                    lid = int(ids[i])
                    lines = coords_of(geoms[k], COORD_DP)
                    if not lines:
                        skipped += 1
                        continue
                    g = ({"type": "LineString", "coordinates": lines[0]} if len(lines) == 1
                         else {"type": "MultiLineString", "coordinates": lines})
                    feat = {"type": "Feature", "geometry": g,
                            "properties": {"id": lid, "ord": orders[lid]}}
                    out.write(("" if first else ",") + json.dumps(feat, separators=(",", ":")))
                    first = False
                    kept += 1
                    by_order[orders[lid]] = by_order.get(orders[lid], 0) + 1
            done += n
            if done % 1_000_000 < READ_BATCH:
                print(f"  {done:,}/{total:,} read, {kept:,} kept "
                      f"({time.time() - t0:.0f}s)", flush=True)
        out.write("]}")

    for o in sorted(by_order, reverse=True):
        print(f"  order {o}: {by_order[o]:,} reach(es)")
    if skipped:
        print(f"  note: {skipped:,} reach(es) collapsed to a point when rounded "
              f"and were dropped.", file=sys.stderr)
    size = os.path.getsize(OUTPUT) / 1e6
    print(f"Wrote {kept:,} reach(es), {size:,.1f} MB in {time.time() - t0:.0f}s "
          f"-> {OUTPUT}")


if __name__ == "__main__":
    main()
