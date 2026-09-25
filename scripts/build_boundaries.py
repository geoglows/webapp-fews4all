#!/usr/bin/env python3
"""
build_boundaries.py — ADM boundaries as one telescoping context layer.

geoBoundaries CGAZ, which is CC BY 4.0; the app carries the credit in its map
attribution (BOUNDARY_ATTRIBUTION in src/config.js).

geoBoundaries CGAZ at three levels — ADM0 countries, ADM1 regions/states, ADM2
districts — written into a single GeoJSON, each feature tagged with `adm` (0/1/2)
so the front end can show one level per zoom band (see layers/boundaries.js).

Only one level is drawn at a time, which is what makes the file affordable: each
level is simplified to the resolution of the zooms it is actually shown at, rather
than to the finest zoom in the app. Full-resolution CGAZ is 1.3 GB of GeoJSON
across the three, and nearly all of that is coastline vertices no one sees at the
zoom where that level appears. Showing one level at a time loses nothing: ADM1
units tile their country and ADM2 tile their ADM1, so the national border is still
drawn — as the outer edge of the finer level.

Polygons are kept rather than reduced to lines: the front end puts a transparent
fill under the outlines as the hit target for the hover label.

Reads:  ../Files/International_boundaries/geoBoundariesCGAZ_ADM{0,1,2}.gpkg
Writes: ../public/data_boundaries.geojson    kind "boundaries"
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

BOUNDARIES = os.path.join(FILES, "International_boundaries")
OUTPUT = os.path.join(PUBLIC, "data_boundaries.geojson")

# (level, file, simplify tolerance in degrees, the zoom band it is drawn at)
# The tolerance is ~a tenth of a pixel at the coarsest zoom of each band, so the
# simplification is invisible where the level is on screen. Raise a tolerance to
# shrink the file; lower it if a level looks faceted at the top of its band.
LEVELS = [
    (0, "geoBoundariesCGAZ_ADM0.gpkg", 0.01,  "zoom < 4"),
    (1, "geoBoundariesCGAZ_ADM1.gpkg", 0.005, "zoom 4-6"),
    (2, "geoBoundariesCGAZ_ADM2.gpkg", 0.002, "zoom 7+"),
]
COORD_DP = 5           # ~1 m at the equator
MIN_AREA = 1e-6        # drop slivers that survive simplification as specks


def round_coords(obj, dp):
    """Round every coordinate in a GeoJSON coordinate tree, dropping the repeats
    that rounding creates."""
    if isinstance(obj[0], (int, float)):
        return [round(obj[0], dp), round(obj[1], dp)]
    out = [round_coords(x, dp) for x in obj]
    if out and isinstance(out[0][0], (int, float)):        # a ring
        squashed = [p for i, p in enumerate(out) if i == 0 or p != out[i - 1]]
        return squashed if len(squashed) >= 4 else out
    return out


def main():
    try:
        from pyogrio.raw import read
        from shapely import from_wkb
        from shapely.geometry import mapping
    except ImportError as e:
        sys.exit(f"Missing dependency: {e.name}.\n"
                 f"    conda install -c conda-forge gdal\n"
                 f"    pip install pyogrio shapely")

    for _lvl, fname, _tol, _band in LEVELS:
        p = os.path.join(BOUNDARIES, fname)
        if not os.path.exists(p):
            sys.exit(f"Not found: {p}")

    t0 = time.time()
    written = 0
    with open(OUTPUT, "w", encoding="utf-8") as out:
        out.write('{"type":"FeatureCollection","kind":"boundaries","levels":[0,1,2],"features":[')
        first = True
        for lvl, fname, tol, band in LEVELS:
            path = os.path.join(BOUNDARIES, fname)
            cols = ["shapeName", "shapeGroup"]
            meta, _fid, geom, fields = read(path, columns=cols)
            names = {name: i for i, name in enumerate(meta["fields"])}
            shape_name = fields[names["shapeName"]]
            shape_group = fields[names["shapeGroup"]]
            geoms = from_wkb([bytes(g) for g in geom])

            kept = dropped = 0
            verts_in = verts_out = 0
            for i, g in enumerate(geoms):
                if g is None or g.is_empty:
                    dropped += 1
                    continue
                simple = g.simplify(tol, preserve_topology=True)
                if simple.is_empty or simple.area < MIN_AREA:
                    dropped += 1
                    continue
                gj = mapping(simple)
                gj = {"type": gj["type"], "coordinates": round_coords(gj["coordinates"], COORD_DP)}
                feat = {
                    "type": "Feature",
                    "geometry": gj,
                    "properties": {
                        "adm": lvl,
                        "name": (shape_name[i] or "").strip(),
                        "group": (shape_group[i] or "").strip(),
                    },
                }
                out.write(("" if first else ",") + json.dumps(feat, separators=(",", ":")))
                first = False
                kept += 1
            written += kept
            print(f"  ADM{lvl}: {kept:,} feature(s) kept"
                  + (f", {dropped:,} dropped" if dropped else "")
                  + f" | tolerance {tol} | shown at {band} ({time.time() - t0:.0f}s)", flush=True)
        out.write("]}")

    size = os.path.getsize(OUTPUT) / 1e6
    print(f"Wrote {written:,} boundary feature(s), {size:,.1f} MB in "
          f"{time.time() - t0:.0f}s -> {OUTPUT}")


if __name__ == "__main__":
    main()
