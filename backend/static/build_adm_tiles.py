#!/usr/bin/env python3
"""Static build — the ADM boundary archive, as PMTiles.

geoBoundaries CGAZ at three levels: ADM0 countries, ADM1 regions and states,
ADM2 districts. Built once and rebuilt only when CGAZ publishes a new release.

This replaces a 115 MB GeoJSON that the browser fetched whole, parsed whole, and
then held in memory for the session — 5.3 million vertices, every one of them
loaded whether or not the viewer ever left their own country. Tiles are fetched
by range request, so the opening view costs a few hundred kilobytes.

One archive, one layer per level, each tiled only across the zoom band it is
actually drawn at:

    adm0      218 countries   z0-4
    adm1    3,224 regions     z4-7
    adm2   49,349 districts   z7-9

The bands are the same ones layers/boundaries.js telescopes through, and both
read them from config so they cannot drift apart. Showing one level at a time
loses nothing: ADM1 units tile their country and ADM2 tile their ADM1, so the
national border is still on screen at every zoom, drawn as the outer edge of
whichever level is live.

Each level is thinned to a tenth of a pixel at the DEEPEST zoom it is drawn at,
which is the one number that matters and is derived rather than guessed. The
retired GeoJSON build thinned to a tenth of a pixel at each band's COARSEST zoom,
because one file had to serve the whole band at once; tiles are fetched per zoom,
so the floor moves to the other end of the band and the geometry gets finer.

It is not optional. Full-resolution CGAZ is 10.1 million vertices for 218
countries, coastline detail measured in metres, none of which survives being
drawn at z4.

Two ways to get there. `ogr2ogr` is preferred and does the whole job in one
streaming pass, never holding the level in memory; GDAL applies the tolerance
itself via -simplify. Without it on PATH the fallback reads through pyogrio in
chunks and simplifies each before the next arrives — correct, but it still has to
hold the simplified level, and on a small machine ADM1 and ADM2 will not fit. The
fallback was OOM-killed on a 3.9 GB box, so treat it as the convenience path and
install GDAL for a real rebuild.

Stopping a band early costs nothing: MapLibre overzooms past a source's maxzoom
by scaling its deepest tiles, and vector geometry stays sharp doing it.

    python3 backend/static/build_adm_tiles.py
    python3 backend/static/build_adm_tiles.py --merge-only
"""

import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import config as C          # noqa: E402

OUT_DIR = os.path.join(C.BACKEND, "static", "tiles")
COMBINED = os.path.join(OUT_DIR, "adm.pmtiles")

# (level, source file, zoom band). The band's floor is config's, shared with the
# front end; its ceiling is the next level's floor, so the bands meet without
# overlapping more than the one zoom where the handover happens.
SOURCES = {
    0: ("geoBoundariesCGAZ_ADM0.gpkg", "globalADM0"),
    1: ("geoBoundariesCGAZ_ADM1.gpkg", "globalADM1"),
    2: ("geoBoundariesCGAZ_ADM2.gpkg", "globalADM2"),
}
# Vector tiles are 4096 units across and conventionally 512 CSS pixels, so a
# degree per pixel at zoom z is 360 / (512 * 2**z). A tenth of that at the band's
# deepest zoom is below what any screen resolves, and simplify() preserves
# topology, so no polygon collapses or self-intersects.
SIMPLIFY_PIXEL_FRACTION = 0.1
# Features per streamed read. Each chunk is simplified before the next is read, so
# peak memory tracks the SIMPLIFIED size rather than the source's.
READ_CHUNK = 2000

# CGAZ's own column names, mapped to what the front end reads.
RENAME = {"shapeName": "name", "shapeGroup": "group"}
KEEP = ["name", "group", "adm"]
# GDAL drops features from a tile that exceeds this, with only a warning. A z0
# tile holds every country at once, so the 500 KB default is far too tight.
MAX_TILE_BYTES = "5000000"


def band(level):
    """(minzoom, maxzoom) for one level's archive."""
    order = sorted(C.ADM_ZOOM)
    lo = C.ADM_ZOOM[level]
    i = order.index(level)
    return lo, (C.ADM_ZOOM[order[i + 1]] if i + 1 < len(order) else lo + 2)


def tolerance(level):
    """Simplification tolerance in degrees for one level, from its deepest zoom."""
    _lo, hi = band(level)
    return SIMPLIFY_PIXEL_FRACTION * 360.0 / (512.0 * 2 ** hi)


def part_path(level):
    return os.path.join(OUT_DIR, f"adm{level}.part.pmtiles")


def select_sql(level, layer):
    """CGAZ's columns renamed to what the front end reads. `group` needs quoting."""
    return (f'SELECT shapeName AS name, shapeGroup AS "group", {level} AS adm, geom '
            f'FROM "{layer}"')


def clear(out):
    """GDAL opens an existing PMTiles archive rather than replacing it, then
    refuses to create the layer. A rebuild has to clear its own output."""
    for stale in (out, out + ".tmp.mbtiles"):
        if os.path.exists(stale):
            os.remove(stale)


def ogr2ogr_command(level, src, layer):
    """The streaming conversion for one level, as a list ready for subprocess."""
    lo, hi = band(level)
    return [
        "ogr2ogr", "-f", "PMTiles", part_path(level), src,
        "-dialect", "SQLITE", "-sql", select_sql(level, layer),
        # CGAZ ships the layer tagged "Undefined geographic SRS". The coordinates
        # are WGS84 degrees, but the tiler needs that said out loud or it cannot
        # project to Web Mercator.
        "-a_srs", "EPSG:4326",
        "-simplify", f"{tolerance(level):.8f}",
        "-dsco", f"MINZOOM={lo}",
        "-dsco", f"MAXZOOM={hi}",
        "-dsco", f"MAX_SIZE={MAX_TILE_BYTES}",
        "-nln", C.ADM_SOURCE_LAYER.format(level=level),
    ]


def write_level_streaming(level, src, layer):
    """One level via ogr2ogr: a single pass, nothing held in memory."""
    out = part_path(level)
    clear(out)
    r = subprocess.run(ogr2ogr_command(level, src, layer), capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ADM{level}: ogr2ogr failed\n{r.stderr.strip()[:600]}")
    if "skip" in r.stderr.lower() and "feature" in r.stderr.lower():
        print(f"  ADM{level}: WARNING — {r.stderr.strip()[:200]}")
    clear(out + ".tmp.mbtiles")
    return out, None, 0


def write_level(level, src):
    """One level's polygons to its own PMTiles, through pyogrio. Memory hungry."""
    import pandas as pd
    import pyogrio

    tol = tolerance(level)
    chunks, before, skip = [], 0, 0
    while True:
        part = pyogrio.read_dataframe(src, skip_features=skip, max_features=READ_CHUNK)
        if part.empty:
            break
        before += len(part)
        skip += READ_CHUNK
        part = part[part.geometry.notna() & ~part.geometry.is_empty]
        part = part.rename(columns=RENAME)
        for col in KEEP:
            if col not in part.columns:
                part[col] = ""
        part["adm"] = level
        part = part[KEEP + ["geometry"]].copy()
        part["geometry"] = part.geometry.simplify(tol, preserve_topology=True)
        chunks.append(part)
    df = pd.concat(chunks, ignore_index=True)
    # CGAZ ships the layer tagged "Undefined geographic SRS". The coordinates are
    # WGS84 degrees, but the tiler needs that said out loud or it cannot project
    # to Web Mercator.
    df = df.set_crs("EPSG:4326", allow_override=True)

    lo, hi = band(level)
    out = part_path(level)
    clear(out)
    pyogrio.write_dataframe(
        df, out, driver="PMTiles", layer=C.ADM_SOURCE_LAYER.format(level=level),
        dataset_options={"MINZOOM": str(lo), "MAXZOOM": str(hi),
                         "MAX_SIZE": MAX_TILE_BYTES})
    clear(out + ".tmp.mbtiles")      # GDAL stages tiles here and leaves it behind
    return out, len(df), before - len(df)


def merge_parts(parts):
    """Fold the per-level archives into the one the app reads."""
    missing = [p for p in parts if not os.path.exists(p)]
    if missing:
        sys.exit("Missing: " + ", ".join(os.path.basename(p) for p in missing))

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from pmtiles_merge import merge          # ours; no tippecanoe needed

    merge(COMBINED, parts)
    for p in parts:
        try:
            os.remove(p)                     # the parts exist only to be merged
        except OSError as e:
            print(f"  (left {os.path.basename(p)} in place: {e.strerror})")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    streaming = shutil.which("ogr2ogr") is not None
    how = ("found — streaming, one pass, nothing held in memory" if streaming
           else "NOT on PATH — falling back to pyogrio, which holds each level "
                "in memory and will not fit on a small machine")
    print(f"ogr2ogr: {how}\n")
    parts = []
    for level in sorted(SOURCES):
        fname, layer = SOURCES[level]
        src = os.path.join(C.ADM_DIR, fname)
        if not os.path.exists(src):
            sys.exit(f"Not found: {src}")
        lo, hi = band(level)
        t = time.time()
        print(f"  ADM{level}: tiling z{lo}-{hi} from {fname}, "
              f"thinned to {tolerance(level):.5f} deg ...", flush=True)
        if streaming:
            out, kept, dropped = write_level_streaming(level, src, layer)
        else:
            out, kept, dropped = write_level(level, src)
        parts.append(out)
        count = f"{kept:>7,} feature(s)" if kept is not None else "       done"
        print(f"  ADM{level}: {count} -> {os.path.basename(out)}  "
              f"{os.path.getsize(out) / 1e6:>7.1f} MB  {time.time() - t:>5.0f}s"
              + (f"   [{dropped} empty geometr(ies) dropped]" if dropped else ""), flush=True)

    print("\nMerging into one archive...")
    merge_parts(parts)


if __name__ == "__main__":
    if "--merge-only" in sys.argv:
        merge_parts([part_path(l) for l in sorted(SOURCES)])
        sys.exit(0)
    main()
