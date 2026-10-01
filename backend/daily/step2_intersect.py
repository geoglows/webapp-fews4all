#!/usr/bin/env python3
"""Daily step 2 — put every flagged forecast onto the res-6 H3 grid.

Reads the release's native model files out of the input folder and writes two
tables. It does nothing else: no rollup, no concurrence, no severity aggregation.
Those are step 3's, and keeping them out of here is deliberate — agreement has to
be decided at the base resolution and carried upward, and that rule only stays
true while one file owns both halves of it.

Two ways a forecast reaches the grid, because the models publish two shapes:

  lines     GEOGLOWS forecasts a reach, which runs through many cells. The reach
            id is joined against the crosswalk, so a flooding river lights its
            whole path rather than the single hexagon holding its midpoint.
  points    Flood Hub gauges and GloFAS reporting points sit at one coordinate and
            bin straight into the cell containing it. Neither has a crosswalk entry
            — they are not GEOGLOWS reaches.
  areas     Flash-flood footprints are NOT gridded. They keep their own geometry
            end to end, and travel out as a sidecar keyed by the same forecast_uid
            so the forecast table stays one shape across every model.

INPUTS
    backend/input/                          the release's native model files,
                                            matched by pattern (see INPUT_GLOBS)
    Files/h3_streams_global_r6.csv          reach -> res-6 cell crosswalk

OUTPUTS   (<release> is derived from the data; see derive_release)
    backend/output/<release>/forecasts.csv        one row per flagged forecast,
                                                  keyed by forecast_uid, every
                                                  model — gridded or not
    backend/output/<release>/cell_forecasts.csv   one row per (res, cell,
                                                  forecast) — written here at the
                                                  base resolution only, expanded
                                                  in place by step 3
    backend/output/<release>/flash_areas.geojson  the ungridded footprints,
                                                  carrying forecast_uid

The tables are normalised rather than flat: a reach crossing forty cells would
otherwise repeat all fourteen of its attributes forty times over.

Needs only h3 and pyogrio. The GDAL/vgridpandas environment the old GeoJSON build
required is no longer on the daily path at all: cell geometry now lives in the
PMTiles archive the static tier builds, so nothing here has to draw a hexagon.
"""

import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import config as C          # noqa: E402
from common import models as M          # noqa: E402


def load_all(input_dir):
    """Every model's flagged forecasts, tagged with a uid unique across the run.

    The uid is `model:native_id`. Native ids are unique within a model in every
    release seen so far, but that is a property of the data rather than a promise
    the format makes, so a collision is disambiguated and reported instead of
    silently collapsing two forecasts into one.
    """
    by_model, records = {}, []
    for model, (reader, kind) in M.READERS.items():
        path = M.find_input(model, input_dir)
        if not path:
            print(f"  {model:<10} no input file found — skipped", file=sys.stderr)
            by_model[model] = {"kind": kind, "path": None, "records": []}
            continue

        t0 = time.time()
        found = reader(path)
        seen, dupes = set(), 0
        for r in found:
            uid = f"{model}:{r['native_id']}"
            if uid in seen:
                dupes += 1
                uid = f"{uid}#{dupes}"
            seen.add(uid)
            r["forecast_uid"] = uid
        records.extend(found)
        by_model[model] = {"kind": kind, "path": path, "records": found}
        print(f"  {model:<10} {len(found):>6,} flagged  ({kind}s)  "
              f"{os.path.basename(path)}  {time.time() - t0:.1f}s"
              + (f"  [{dupes} duplicate native id(s) disambiguated]" if dupes else ""))
    return by_model, records


def match_points(records):
    """(cell, uid) for every point forecast, from its own coordinate."""
    import h3
    pairs = []
    for r in records:
        try:
            cell = h3.latlng_to_cell(float(r["lat"]), float(r["lon"]), C.BASE_RES)
        except (TypeError, ValueError):
            continue
        pairs.append((cell, r["forecast_uid"]))
    return pairs


def write_areas(records, path):
    """The ungridded footprints, as a FeatureCollection keyed by forecast_uid.

    Only the join key goes in the properties. Every other attribute already lives
    in forecasts.csv, and duplicating it here would create a second copy to drift.
    """
    import json
    feats = [{
        "type": "Feature",
        "geometry": r["_geometry"],
        "properties": {"forecast_uid": r["forecast_uid"], "model": r["model"],
                       "severity": r["severity"]},
    } for r in records if r.get("_geometry")]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f)
    return len(feats), os.path.getsize(path)


def match_lines(records, crosswalk_path):
    """(cell, uid) for every line forecast, by streaming the reach crosswalk.

    The crosswalk is ~12.5M rows, so it is read once, streamed, and tested against
    the flagged reaches rather than loaded. A row is one (cell, segment) crossing,
    so several segments of the same reporting reach routinely cross one cell —
    about 56% of rows repeat a (cell, report_id) pair. Those collapse here, or a
    cell would list the same forecast several times over and every count built on
    top of it would be inflated.
    """
    by_reach = {r["native_id"]: r["forecast_uid"] for r in records}
    if not by_reach:
        return [], (0, 0)

    pairs, seen, rows, dupes = [], set(), 0, 0
    with open(crosswalk_path, newline="", encoding="utf-8-sig") as f:
        rd = csv.reader(f)
        header = next(rd, None)
        if not header:
            sys.exit(f"{os.path.basename(crosswalk_path)} is empty.")
        cols = {c.strip(): i for i, c in enumerate(header)}
        for key in ("h3_id", C.CROSSWALK_ID_COL):
            if key not in cols:
                sys.exit(f"{os.path.basename(crosswalk_path)} has no '{key}' column "
                         f"(found: {list(cols)}).")
        i_cell, i_id = cols["h3_id"], cols[C.CROSSWALK_ID_COL]

        for row in rd:
            rows += 1
            try:
                # Ids are written as floats in places; normalise before matching.
                reach = str(int(float(row[i_id])))
            except (ValueError, IndexError):
                continue
            uid = by_reach.get(reach)
            if uid is None:
                continue
            cell = row[i_cell].strip()
            if not cell:
                continue
            if (cell, uid) in seen:
                dupes += 1
                continue
            seen.add((cell, uid))
            pairs.append((cell, uid))
    return pairs, (rows, dupes)


def write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(columns)
        w.writerows(rows)
    return os.path.getsize(path)


def derive_release(input_dir, records):
    """What to call this release.

    It used to be the input folder's name, which worked while each release sat in
    its own dated folder. A live feed drops into one folder that is always called
    `input`, so the name now comes from the data — the date the models themselves
    say they were issued. A folder that IS named for something specific still wins,
    so an archived release can still be rerun in place.
    """
    base = os.path.basename(os.path.normpath(input_dir))
    if base not in ("input", ""):
        return base
    from collections import Counter
    dates = Counter(str(r.get("issued_time", ""))[:10] for r in records
                    if str(r.get("issued_time", "")).strip())
    if dates:
        return dates.most_common(1)[0][0]
    from datetime import date
    print("  note: no model published an issue time; naming the release by today",
          file=sys.stderr)
    return date.today().isoformat()


def main(input_dir=None, output_dir=None, release=None):
    input_dir = input_dir or C.INPUT_DIR
    if not os.path.isdir(input_dir):
        sys.exit(f"Input folder not found: {input_dir}")
    print(f"Input: {input_dir}\n")

    print("Reading models (flagged forecasts only):")
    by_model, records = load_all(input_dir)
    if not records:
        sys.exit("No flagged forecasts in any model — nothing to intersect.")

    release = release or derive_release(input_dir, records)
    out_dir = os.path.join(output_dir or C.OUTPUT_DIR, release)
    print(f"\nRelease: {release}")

    print(f"\nIntersecting with the res-{C.BASE_RES} grid:")
    pairs = []

    point_records = [r for m in by_model.values() if m["kind"] == "point" for r in m["records"]]
    if point_records:
        t0 = time.time()
        pts = match_points(point_records)
        pairs.extend(pts)
        print(f"  points     {len(pts):>6,} cell/forecast pair(s) from "
              f"{len(point_records):,} forecast(s)  {time.time() - t0:.1f}s")

    line_records = [r for m in by_model.values() if m["kind"] == "line" for r in m["records"]]
    if line_records:
        if not os.path.exists(C.CROSSWALK_CSV):
            sys.exit(f"Crosswalk not found: {C.CROSSWALK_CSV}")
        t0 = time.time()
        lns, (rows, dupes) = match_lines(line_records, C.CROSSWALK_CSV)
        pairs.extend(lns)
        print(f"  lines      {len(lns):>6,} cell/forecast pair(s) from "
              f"{len(line_records):,} reach(es)  {time.time() - t0:.1f}s")
        print(f"             crosswalk: {rows:,} row(s) read, "
              f"{dupes:,} repeat crossing(s) collapsed")

    if not pairs:
        sys.exit("No forecast reached the grid — nothing to write.")

    # A forecast that matched nothing is dropped rather than written with no cell:
    # step 3 keys off the join table, so an orphan row would be invisible anyway
    # and would only make the two files disagree about how many forecasts exist.
    # An ungridded model has no pairs by definition, so only the gridded ones are
    # held to this: a gridded forecast that matched nothing is dropped, because
    # step 3 keys off the join table and an orphan row would be invisible there
    # while still inflating every count taken from the forecast table.
    area_records = [r for m in by_model.values() if m["kind"] == "area" for r in m["records"]]
    area_uids = {r["forecast_uid"] for r in area_records}
    matched = {uid for _cell, uid in pairs} | area_uids
    kept = [r for r in records if r["forecast_uid"] in matched]
    orphans = len(records) - len(kept)

    f_path = os.path.join(out_dir, "forecasts.csv")
    m_path = os.path.join(out_dir, "cell_forecasts.csv")
    f_bytes = write_csv(f_path, C.FORECAST_COLUMNS,
                        [[r[c] for c in C.FORECAST_COLUMNS] for r in kept])
    # Tagged with the resolution from the start, so step 3 expands this file
    # rather than writing a second one beside it.
    m_bytes = write_csv(m_path, C.MATCH_COLUMNS,
                        sorted([C.BASE_RES, cell, uid] for cell, uid in pairs))

    a_count, a_bytes = (0, 0)
    if area_records:
        a_count, a_bytes = write_areas(area_records, os.path.join(out_dir, "flash_areas.geojson"))

    cells = len({c for c, _ in pairs})
    print(f"\nWrote {out_dir}")
    print(f"  forecasts.csv          {len(kept):>7,} row(s)  {f_bytes / 1e6:>6.2f} MB"
          + (f"   ({orphans:,} forecast(s) matched no cell, dropped)" if orphans else ""))
    print(f"  cell_forecasts.csv     {len(pairs):>7,} row(s)  {m_bytes / 1e6:>6.2f} MB"
          f"   across {cells:,} distinct cell(s) at res {C.BASE_RES}")
    if a_count:
        print(f"  flash_areas.geojson    {a_count:>7,} area(s)  {a_bytes / 1e6:>6.2f} MB"
              f"   ungridded, joined on forecast_uid")


if __name__ == "__main__":
    main(*sys.argv[1:3] if len(sys.argv) > 1 else ())
