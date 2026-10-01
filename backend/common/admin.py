"""The "Near" lookup: which administrative unit a forecast physically sits in.

Point-in-polygon against geoBoundaries CGAZ, finest level first and falling back
only for the points the finer level could not place. The fallback is not a nicety:
CGAZ's ADM2 does not tile every country, so an ADM2-only lookup silently returns
nothing for whole nations rather than naming the region or the country.

Each level is streamed in batches with its own spatial index, and only the points
still unplaced are queried, so every fallback level costs less than the one before.
"""

import os
import sys

from . import config as C


def _names_at_level(path, pts, wanted):
    """{point index: containing polygon's name} for the points listed in `wanted`."""
    import numpy as np
    from pyogrio.raw import read
    from shapely import STRtree, from_wkb

    if not os.path.exists(path):
        print(f"  note: {os.path.basename(path)} not found — level skipped", file=sys.stderr)
        return {}

    found = {}
    offset = 0
    while True:
        meta, _m2, geoms, fields = read(path, skip_features=offset,
                                        max_features=C.ADMIN_READ_BATCH)
        if geoms is None or len(geoms) == 0:
            break
        cols = dict(zip(list(meta["fields"]), fields))
        names = cols.get("shapeName")
        polys = from_wkb(geoms)
        tree = STRtree(polys)

        idx = np.array(sorted(wanted - set(found)))
        if idx.size == 0:
            break
        hit_pts, hit_poly = tree.query(pts[idx], predicate="within")
        for pi, gi in zip(hit_pts, hit_poly):
            point_index = int(idx[pi])
            if point_index in found:
                continue
            found[point_index] = str(names[gi]).strip() if names is not None else ""
        offset += len(geoms)
    return found


def assign(records):
    """Set `district` and `district_level` on every record that has a coordinate.

    Both stay empty where there is no usable coordinate or no containing unit at
    any level, rather than being filled with something plausible.
    """
    from shapely import points as shapely_points

    coords, idx = [], []
    for i, r in enumerate(records):
        r["district"] = ""
        r["district_level"] = ""
        try:
            coords.append((float(r["lon"]), float(r["lat"])))
            idx.append(i)
        except (KeyError, TypeError, ValueError):
            continue
    if not coords:
        return 0

    pts = shapely_points(coords)
    remaining = set(range(len(coords)))
    for level, fname in C.ADMIN_LEVELS:
        if not remaining:
            break
        found = _names_at_level(os.path.join(C.BOUNDARIES_DIR, fname), pts, remaining)
        for p_idx, name in found.items():
            records[idx[p_idx]]["district"] = name
            records[idx[p_idx]]["district_level"] = level
            remaining.discard(p_idx)
        if found:
            print(f"    ADM{level}: placed {len(found):,}"
                  + (f", {len(remaining):,} still unplaced" if remaining else ""))

    # Last resort for the handful no polygon contains — coastal and delta points
    # that fall just outside every CGAZ unit. Several models name the country on
    # the forecast itself, so such a point can still say which country it is in
    # rather than showing nothing. Level 0 is what the front end already calls a
    # country, so this needs no new wording.
    rescued = 0
    for r in records:
        if not r["district"] and str(r.get("country", "")).strip():
            r["district"] = str(r["country"]).strip()
            r["district_level"] = 0
            rescued += 1
    if rescued:
        print(f"    fallback: {rescued:,} placed by the forecast's own country field")
    return len(coords) - len(remaining) + rescued
