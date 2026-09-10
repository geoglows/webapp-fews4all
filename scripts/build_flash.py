#!/usr/bin/env python3
"""
build_flash.py — build the app's flash-flood overlay from the Flood Hub urban
flash-flood export, enriching each polygon with the place it covers.

Self-contained, like the other build scripts. The polygons come from the GeoJSON;
the per-event centroid (used to name the containing district/region) comes from the
CSV, joined by polygonId.

Location naming is derived STRICTLY from the geoBoundaries admin files in
Files/International_boundaries (ADM1 = regions/states, ADM2 = districts) — never
from the flash export's own country field. For each polygon we record:

  - district / district_count : ADM2 the centroid falls in, and how many ADM2 it
                                overlaps  (the local "Lahore + 3 districts" case)
  - region   / region_count   : ADM1 the centroid falls in, and how many ADM1 it
                                overlaps  (the wider "Punjab + 1 region" case)
  - countries                 : names of the countries it overlaps, read straight
                                from the ADM0 layer's shapeName

The front end picks the tier: district when it stays inside one region, region when
it spans several regions of one country, country when it spans several countries.
Coverage is only as complete as the boundary files (some countries are absent for
now); a polygon outside coverage simply gets blank fields.

Reads:  ../Files/urban_flash_floods.geojson              (likely/highly_likely polygons)
        ../Files/urban_flash_floods_with_location.csv     (per-event centroid + attributes)
        ../Files/International_boundaries/geoBoundariesCGAZ_ADM2.gpkg
        ../Files/International_boundaries/geoBoundariesCGAZ_ADM1.gpkg
        ../Files/International_boundaries/geoBoundariesCGAZ_ADM0.gpkg
Writes: ../public/data_flash_floods.geojson
"""

import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                        # <repo>
FILES = os.path.join(ROOT, "Files")
PUBLIC = os.path.join(ROOT, "public")   # the app reads its data from here
os.makedirs(PUBLIC, exist_ok=True)

GEOJSON_IN = os.path.join(FILES, "urban_flash_floods.geojson")
CSV_IN = os.path.join(FILES, "urban_flash_floods_with_location.csv")
BOUNDARIES = os.path.join(FILES, "International_boundaries")
ADM2_GPKG = "geoBoundariesCGAZ_ADM2.gpkg"   # districts
ADM1_GPKG = "geoBoundariesCGAZ_ADM1.gpkg"   # regions / states
ADM0_GPKG = "geoBoundariesCGAZ_ADM0.gpkg"   # countries (shapeName = country)
ADMIN_READ_BATCH = 4000   # admin polygons per streamed read (keeps memory flat)
OUTPUT = os.path.join(PUBLIC, "data_flash_floods.geojson")

# Properties carried through from the source polygons (geometry aside). The front
# end derives Chance from polygon_type and formats Issued/Period from these. The
# affectedCountry* fields are kept for reference only — location naming (including
# country) comes from the admin files, not from here.
KEEP = ["polygon_type", "polygonId", "event_index", "forecastIssueTime",
        "forecastPeriodHours", "affectedCountryCodes", "affectedCountryNames"]


def centroid_by_polygon():
    """polygonId -> (lon, lat). Each CSV row is one event carrying its likely and
    (optionally) highly_likely polygon ids, each with its own centroid."""
    out = {}
    if not os.path.exists(CSV_IN):
        sys.exit(f"Not found: {CSV_IN}")
    TYPES = [
        ("likelyAffectedPolygonId", "likely_centroid_lat", "likely_centroid_lng"),
        ("highlyLikelyAffectedPolygonId", "highly_likely_centroid_lat", "highly_likely_centroid_lng"),
    ]
    with open(CSV_IN, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            for id_col, lat_col, lng_col in TYPES:
                pid = (r.get(id_col) or "").strip()
                if not pid:
                    continue
                try:
                    out[pid] = (float(r.get(lng_col)), float(r.get(lat_col)))
                except (TypeError, ValueError):
                    continue
    return out


def admin_pass(path, coords, cidx, fgeoms, fidx):
    """Stream one admin level once (STRtree per batch) and return, keyed by feature
    index:
      name_by    : shapeName of the admin unit whose polygon contains the centroid
      count_by   : how many admin units the flash polygon overlaps
      touched_by : set of shapeName(s) the flash polygon overlaps
    Memory stays flat; the full layer is read because the overlap tallies need every
    batch."""
    from pyogrio.raw import read
    from shapely import from_wkb, STRtree, points as shapely_points

    pts = shapely_points(coords) if coords else None
    name_by, count_by, touched_by = {}, {}, {}
    assigned = set()
    skip = 0
    while True:
        meta, _fid, geom, fields = read(path, columns=["shapeName"],
                                        skip_features=skip, max_features=ADMIN_READ_BATCH)
        n = len(geom)
        if n == 0:
            break
        col = {name: i for i, name in enumerate(meta["fields"])}
        names = fields[col["shapeName"]]
        polys = from_wkb([bytes(g) for g in geom])
        tree = STRtree(polys)

        # Centroid -> containing admin unit name.
        if pts is not None:
            pi, gi = tree.query(pts, predicate="intersects")
            for p_idx, g_idx in zip(pi, gi):
                if p_idx in assigned:
                    continue
                if polys[g_idx].contains(pts[p_idx]):
                    assigned.add(p_idx)
                    nm = (names[g_idx] or "").strip()
                    if nm:
                        name_by[cidx[p_idx]] = nm

        # Flash polygon -> overlap count and the names it touches.
        if fgeoms:
            qi, gj = tree.query(fgeoms, predicate="intersects")
            for q_idx, g_idx in zip(qi, gj):
                fi = fidx[q_idx]
                count_by[fi] = count_by.get(fi, 0) + 1
                nm = (names[g_idx] or "").strip()
                if nm:
                    touched_by.setdefault(fi, set()).add(nm)

        skip += n
        if n < ADMIN_READ_BATCH:
            break
    return name_by, count_by, touched_by


def assign_admin(features, centroids):
    """Enrich each feature with district/region/country facts from the ADM files."""
    from shapely.geometry import shape

    for f in features:
        f["properties"].update({"district": "", "district_count": 0,
                                "region": "", "region_count": 0, "countries": []})

    # Centroid points (for the "contains" name at each level).
    coords, cidx = [], []
    for i, f in enumerate(features):
        c = centroids.get(str(f["properties"].get("polygonId", "")))
        if c:
            coords.append(c)
            cidx.append(i)

    # Flash polygon geometries (for the overlap counts and countries touched).
    fgeoms, fidx = [], []
    for i, f in enumerate(features):
        try:
            fgeoms.append(shape(f["geometry"]))
            fidx.append(i)
        except (AttributeError, KeyError, ValueError, TypeError):
            continue

    adm2 = os.path.join(BOUNDARIES, ADM2_GPKG)
    adm1 = os.path.join(BOUNDARIES, ADM1_GPKG)
    adm0 = os.path.join(BOUNDARIES, ADM0_GPKG)
    if not os.path.exists(adm2):
        print(f"  note: {ADM2_GPKG} not found; location naming skipped.", file=sys.stderr)
        return

    # ADM2: district name (centroid) + how many districts the polygon overlaps.
    n2, c2, _ = admin_pass(adm2, coords, cidx, fgeoms, fidx)
    for i, nm in n2.items():
        features[i]["properties"]["district"] = nm
    for i, c in c2.items():
        features[i]["properties"]["district_count"] = int(c)

    # ADM1: region name (centroid) + how many regions the polygon overlaps.
    if os.path.exists(adm1):
        n1, c1, _ = admin_pass(adm1, coords, cidx, fgeoms, fidx)
        for i, nm in n1.items():
            features[i]["properties"]["region"] = nm
        for i, c in c1.items():
            features[i]["properties"]["region_count"] = int(c)
    else:
        print(f"  note: {ADM1_GPKG} not found; region tier skipped.", file=sys.stderr)

    # ADM0: the country name(s) the polygon overlaps, straight from shapeName.
    if os.path.exists(adm0):
        _, _, t0 = admin_pass(adm0, coords, cidx, fgeoms, fidx)
        for i, names in t0.items():
            features[i]["properties"]["countries"] = sorted(names)
    else:
        print(f"  note: {ADM0_GPKG} not found; country tier skipped.", file=sys.stderr)

    named = sum(1 for f in features if f["properties"]["district"])
    multi_country = sum(1 for f in features if len(f["properties"]["countries"]) > 1)
    print(f"Admin lookup: {named:,}/{len(features):,} centroid district(s) named; "
          f"{multi_country:,} polygon(s) span multiple countries.")


def main():
    if not os.path.exists(GEOJSON_IN):
        sys.exit(f"Not found: {GEOJSON_IN}")
    src = json.load(open(GEOJSON_IN, encoding="utf-8"))
    feats_in = src.get("features", [])
    print(f"Flash polygons: {len(feats_in):,}")

    centroids = centroid_by_polygon()
    print(f"Event centroids from CSV: {len(centroids):,}")

    # Keep only the props we surface, plus the geometry.
    features = [
        {"type": "Feature", "geometry": f["geometry"],
         "properties": {k: (f.get("properties") or {}).get(k, "") for k in KEEP}}
        for f in feats_in
    ]

    assign_admin(features, centroids)

    out = {"type": "FeatureCollection", "kind": "flash-floods", "features": features}
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"Wrote {len(features):,} feature(s) -> {OUTPUT}")


if __name__ == "__main__":
    main()
