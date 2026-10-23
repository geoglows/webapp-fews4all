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
            — they are not GEOGLOWS reaches. Flood Hub gets a second pass on top:
            the gauge is spread onto the HydroRIVERS reach it stands on, so a
            warned river reads as a piece of river rather than as a dot
            (common/hydrorivers.py).
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
from common import config as C
from common import streams as S          # noqa: E402
from common import hydrorivers as HR    # noqa: E402
from common import models as M          # noqa: E402
from common import manifest as MAN      # noqa: E402
from common.tables import write_csv, write_text   # noqa: E402


def model_date(records, path):
    """(date, where it came from) for one model's file.

    The issue date the model itself publishes, when it publishes one — the modal
    value, because a release can carry a stray row from a neighbouring cycle. Not
    every model does: GEOGLOWS's export carries no issue time at all, so the file's
    own modification time stands in. That is a weaker signal and is labelled as
    such, because "the file was written today" and "the forecast is for today" are
    not the same claim and the manifest should not pretend otherwise.
    """
    from collections import Counter
    dates = Counter(str(r.get("issued_time", ""))[:10] for r in records
                    if str(r.get("issued_time", "")).strip())
    if dates:
        return dates.most_common(1)[0][0], "issued_time"
    try:
        return time.strftime("%Y-%m-%d", time.gmtime(os.path.getmtime(path))), "file mtime"
    except OSError:
        return "", "unknown"


def _days_between(a, b):
    from datetime import date
    try:
        ya, ma, da = (int(x) for x in a.split("-"))
        yb, mb, db = (int(x) for x in b.split("-"))
        return abs((date(ya, ma, da) - date(yb, mb, db)).days)
    except (ValueError, AttributeError):
        return None


def load_all(input_dir, output_dir=None):
    """Every model's flagged forecasts, each model isolated from the others.

    The uid is `model:native_id`. Native ids are unique within a model in every
    release seen so far, but that is a property of the data rather than a promise
    the format makes, so a collision is disambiguated and reported instead of
    silently collapsing two forecasts into one.

    ONE MODEL CANNOT TAKE DOWN THE RUN. A missing file was always survivable; an
    unreadable one was not, and that is backwards for a live feed, where a
    truncated download or a changed column is far likelier than a file that never
    arrives. Every reader is now called inside a guard, and a model that throws is
    recorded and left out rather than ending the morning.

    Returns (by_model, records, status) where `status` is the per-model manifest
    rows — the record of what was read, what it was dated, and what was trusted.
    """
    by_model, records, status = {}, [], {}
    for model, (reader, kind) in M.READERS.items():
        row = {"status": C.MODEL_SKIPPED, "kind": kind, "notes": []}
        status[model] = row
        by_model[model] = {"kind": kind, "path": None, "records": []}

        path = M.find_input(model, input_dir)
        if not path:
            row["notes"].append("no input file in the folder")
            print(f"  {model:<10} no input file found — skipped", file=sys.stderr)
            continue
        row["file"] = os.path.basename(path)
        try:
            row["file_mtime"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(path)))
        except OSError:
            pass

        t0 = time.time()
        try:
            found = reader(path)
        except SystemExit as e:              # a reader that still exits on bad input
            row["status"] = C.MODEL_DROPPED
            row["notes"].append(f"reader refused the file: {e}")
            print(f"  {model:<10} UNREADABLE ({e}) — dropped", file=sys.stderr)
            continue
        except Exception as e:
            row["status"] = C.MODEL_DROPPED
            row["notes"].append(f"unreadable: {type(e).__name__}: {e}")
            print(f"  {model:<10} UNREADABLE ({type(e).__name__}: {e}) — dropped",
                  file=sys.stderr)
            continue

        seen, dupes = set(), 0
        for r in found:
            uid = f"{model}:{r['native_id']}"
            if uid in seen:
                dupes += 1
                uid = f"{uid}#{dupes}"
            seen.add(uid)
            r["forecast_uid"] = uid
        row["flagged"] = len(found)
        row["issued"], row["issued_from"] = model_date(found, path)
        row["status"] = C.MODEL_OK
        if dupes:
            row["notes"].append(f"{dupes} duplicate native id(s) disambiguated")
        if not found:
            # A file that exists and yields nothing is not a quiet day — a quiet
            # day still has a handful of warnings somewhere on earth. It is a
            # truncated download, a changed column name, or a reader quietly
            # parsing a file that is no longer the shape it expects. Marked thin
            # rather than ok so the morning says so; it contributes no rows
            # either way, so nothing is lost by keeping it in the release.
            row["status"] = C.MODEL_THIN
            row["notes"].append("file present but held no flagged forecasts")

        by_model[model] = {"kind": kind, "path": path, "records": found}
        records.extend(found)
        print(f"  {model:<10} {len(found):>6,} flagged  ({kind}s)  "
              f"{os.path.basename(path)}  {time.time() - t0:.1f}s"
              + (f"  [{dupes} duplicate native id(s) disambiguated]" if dupes else ""))
    return by_model, records, status


def apply_guards(by_model, status, release=None, output_dir=None):
    """Decide which models are trustworthy enough to go into the release.

    Two checks, and they are deliberately different in severity.

    STALENESS DROPS. A model whose date trails the freshest model in the run by
    more than the tolerance is yesterday's file left in the folder — the failure
    the whole pipeline is least able to notice on its own, because a stale file
    reads as perfectly valid data. It is dropped, and the release is written
    without it.

    VOLUME ONLY WARNS. A model far below its recent median is the signature of a
    truncated download, but it is also exactly what a genuinely quiet day looks
    like from here. Discarding real forecasts on a heuristic is worse than
    publishing a thin release with a note on it, so this marks the model `thin`
    and keeps every row.

    Returns (release, dropped_models).
    """
    # Which date names the release. Not simply the freshest: the dates are not
    # equally trustworthy. A model that publishes an issue time is stating what
    # the forecast is FOR; a file mtime is only stating when a download finished,
    # and a download that ran after midnight would otherwise name the whole
    # release a day ahead of every model in it — and then strand those models as
    # "stale" against a date none of them claimed. So the models that date
    # themselves decide, and mtime is consulted only when none of them do.
    dated = {m: r["issued"] for m, r in status.items()
             if r.get("issued") and r["status"] == C.MODEL_OK}
    stated = {m: d for m, d in dated.items()
              if status[m].get("issued_from") == "issued_time"}
    newest = max(stated.values()) if stated else (max(dated.values()) if dated else None)
    release = release or newest or time.strftime("%Y-%m-%d", time.gmtime())

    dropped = []
    for model, r in status.items():
        if r["status"] != C.MODEL_OK or not r.get("issued"):
            continue
        gap = _days_between(r["issued"], release)
        if gap is not None and r["issued"] > release:
            # Ahead of the release, not behind it — a file written after the
            # release's own cycle. Nothing is stale about that.
            gap = 0
        if gap is not None and gap > C.MODEL_DATE_SPREAD_DAYS:
            r["status"] = C.MODEL_DROPPED
            r["notes"].append(
                f"issued {r['issued']} ({r['issued_from']}), {gap} day(s) behind the "
                f"release — stale, left out")
            dropped.append(model)
            print(f"  {model:<10} STALE: dated {r['issued']}, release is {release} "
                  f"— dropped", file=sys.stderr)

    for model, r in status.items():
        if r["status"] != C.MODEL_OK or not r.get("flagged"):
            continue
        base = MAN.baseline(model, output_dir, exclude=release)
        if not base:
            continue
        r["baseline"] = base
        if r["flagged"] < base * C.MODEL_VOLUME_FLOOR:
            r["status"] = C.MODEL_THIN
            r["notes"].append(
                f"{r['flagged']:,} forecasts against a recent median of {base:,.0f} "
                f"— kept, but check the feed")
            print(f"  {model:<10} THIN: {r['flagged']:,} vs median {base:,.0f} "
                  f"— kept with a warning", file=sys.stderr)

    # Age of the release itself, relative to the day it is being built.
    gap = _days_between(release, time.strftime("%Y-%m-%d", time.gmtime()))
    if gap is not None and gap > C.RELEASE_MAX_AGE_DAYS:
        print(f"  note: this release is dated {release}, {gap} day(s) ago — "
              f"rerunning an archive, or no model delivered today's file",
              file=sys.stderr)

    for model in dropped:
        by_model[model] = {"kind": by_model[model]["kind"], "path": None, "records": []}
    return release, dropped


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

    Almost nothing goes in the properties: every attribute a reader needs is in
    forecasts.csv under the same forecast_uid, and copying it here would make a
    second version to drift from the first.

    `polygon_type` is the exception, because it is the one thing forecasts.csv
    cannot give back. It is what the map FILTERS and STYLES on — likely against
    highly-likely — and the forecast table only keeps the severity it maps to, so
    deriving it back would mean inverting FLASH_SEVERITY in the browser.
    """
    import json
    feats = [{
        "type": "Feature",
        "geometry": r["_geometry"],
        "properties": {"forecast_uid": r["forecast_uid"], "model": r["model"],
                       "severity": r["severity"],
                       "polygon_type": r.get("_polygon_type", "")},
    } for r in records if r.get("_geometry")]
    size = write_text(path, json.dumps({"type": "FeatureCollection",
                                        "features": feats}))
    return len(feats), size


def match_lines(records, crosswalk_path):
    """(cell, uid) for every line forecast, by streaming the reach crosswalk.

    The crosswalk is ~12.5M rows, so it is read once, streamed, and tested against
    the flagged reaches rather than loaded. Matching is on C.CROSSWALK_ID_COL,
    which is the reach's own id — see the note there for why it is not report_id,
    and what joining the wrong column cost.

    A (cell, reach) pair can still repeat, because one reach can cross a cell more
    than once where the line re-enters it. Those collapse here, or a cell would
    list the same forecast several times over and every count built on top of it
    would be inflated.
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

    pairs, resolved = resolve_reaches(pairs, by_reach)
    return pairs, (rows, dupes, resolved)


def resolve_reaches(pairs, by_reach):
    """Apply the river-network rules to cells holding more than one reach.

    A hexagon is wider than a GEOGLOWS reach, so one routinely catches several
    flagged ones — sometimes the same river twice, sometimes two rivers meeting,
    sometimes two that never meet. common/streams.py holds the rules and the
    evidence for them; this is just where they are applied to the day's pairs.

    Reaches the attribute table does not know are kept untouched. A missing
    topology must not silently delete a forecast.
    """
    net = S.Network().load()
    if not net.ready:
        print("  note: no stream attribute table; reaches left as matched "
              "(build it with static/build_stream_attrs.py)", file=sys.stderr)
        return pairs, {}

    uid_of = {}
    by_cell = {}
    for cell, uid in pairs:
        by_cell.setdefault(cell, []).append(uid)
    for reach, uid in by_reach.items():
        rid = _reach_int(reach)
        if rid is not None:
            uid_of[uid] = rid

    out, tally = [], {}
    for cell, uids in by_cell.items():
        ids = [uid_of.get(u) for u in uids]
        if len(uids) < 2 or any(i is None for i in ids):
            out.extend((cell, u) for u in uids)
            continue
        kept, droppedlist, reason = S.resolve_cell(ids, net)
        if droppedlist:
            tally[reason] = tally.get(reason, 0) + len(droppedlist)
        keep = set(kept)
        out.extend((cell, u) for u, i in zip(uids, ids) if i in keep)
    return out, tally


def _reach_int(reach):
    try:
        return int(float(reach))
    except (TypeError, ValueError):
        return None


def derive_release(input_dir, records):
    """A folder named for something specific still wins, so an archived release
    can be rerun in place. Otherwise the date comes from the models themselves,
    in apply_guards — which picks the FRESHEST model rather than the most common
    date. Majority vote was the old rule and it had GloFAS, the model with the most
    rows, able to name the release from a stale file and quietly pull a second
    day's forecasts in with it.
    """
    base = os.path.basename(os.path.normpath(input_dir))
    return base if base not in ("input", "") else None


def main(input_dir=None, output_dir=None, release=None, staging=False):
    input_dir = input_dir or C.current_input()
    if not os.path.isdir(input_dir):
        sys.exit(f"Input folder not found: {input_dir}")
    print(f"Input: {input_dir}\n")

    print("Reading models (flagged forecasts only):")
    by_model, records, status = load_all(input_dir, output_dir)
    release = release or derive_release(input_dir, records)
    release, dropped = apply_guards(by_model, status, release, output_dir)
    if dropped:
        records = [r for r in records
                   if r["forecast_uid"].split(":", 1)[0] not in set(dropped)]
    if not records:
        sys.exit("No model delivered a usable forecast — refusing to write an "
                 "empty release over the last good one.")

    root = output_dir or C.OUTPUT_DIR
    out_dir = (os.path.join(root, C.STAGING_DIR, release) if staging
               else os.path.join(root, release))
    print(f"\nRelease: {release}" + ("   (staged)" if staging else ""))

    print(f"\nIntersecting with the res-{C.BASE_RES} grid:")
    pairs = []

    point_records = [r for m in by_model.values() if m["kind"] == "point" for r in m["records"]]
    if point_records:
        t0 = time.time()
        pts = match_points(point_records)
        pairs.extend(pts)
        print(f"  points     {len(pts):>6,} cell/forecast pair(s) from "
              f"{len(point_records):,} forecast(s)  {time.time() - t0:.1f}s")

    # Flood Hub reports gauges, so its warnings land as isolated hexagons. Each one
    # is spread onto the HydroRIVERS reach it sits on — Flood Hub's own network —
    # and no further. See common/hydrorivers.py for the rule and what it rests on.
    fh = by_model.get("flood_hub", {}).get("records", [])
    if fh:
        fh_uids = {r["forecast_uid"] for r in fh}
        gauge_cells = {c for c, u in pairs if u in fh_uids}
        t0 = time.time()
        fills, fill_pairs, fs = HR.fill(fh, gauge_cells)
        if fill_pairs:
            records.extend(fills)
            pairs.extend(fill_pairs)
            print(f"  fill       {len(fill_pairs):>6,} cell(s) filled along warned "
                  f"gauges' own reaches, from {fs.get('filled_forecasts', 0):,} "
                  f"gauge(s)  {time.time() - t0:.1f}s")
            print(f"             gauges on a reach: {fs.get('by_id', 0):,} by id, "
                  f"{fs.get('snapped', 0):,} snapped, "
                  f"{fs.get('unresolved', 0):,} unresolved "
                  f"({fs.get('claimed_reaches', 0):,} reach(es) claimed)")

    line_records = [r for m in by_model.values() if m["kind"] == "line" for r in m["records"]]
    if line_records and not os.path.exists(C.CROSSWALK_CSV):
        # Not fatal: the point models and the gauge fill have already produced
        # their cells, and a map missing one model beats no map at all. The line
        # models are recorded as dropped so the manifest says why they vanished.
        print(f"  note: no crosswalk at {C.CROSSWALK_CSV}; GEOGLOWS reaches "
              f"left ungridded", file=sys.stderr)
        for m, row in status.items():
            if row.get("kind") == "line" and row["status"] in (C.MODEL_OK, C.MODEL_THIN):
                row["status"] = C.MODEL_DROPPED
                row["notes"].append("no reach-to-cell crosswalk; left ungridded")
        line_records = []

    if line_records:
        t0 = time.time()
        lns, (rows, dupes, resolved) = match_lines(line_records, C.CROSSWALK_CSV)
        pairs.extend(lns)
        print(f"  lines      {len(lns):>6,} cell/forecast pair(s) from "
              f"{len(line_records):,} reach(es)  {time.time() - t0:.1f}s")
        print(f"             crosswalk: {rows:,} row(s) read, "
              f"{dupes:,} repeat crossing(s) collapsed")
        if resolved:
            total = sum(resolved.values())
            detail = ", ".join(f"{k} {v:,}" for k, v in sorted(resolved.items()))
            print(f"             network rules: {total:,} reach(es) dropped from "
                  f"shared cells ({detail})")

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

    # The run record, and the pointer that tells steps 3 to 5 which release this
    # was. Both are written last, on success only: a pointer to a half-written
    # folder is worse than no pointer, because the later steps would believe it.
    gridded = {}
    for cell, uid in pairs:
        gridded.setdefault(uid.split(":", 1)[0], set()).add(cell)
    for model, row in status.items():
        row["cells"] = len(gridded.get(model, ()))
        if not row["notes"]:
            row.pop("notes")
    data = MAN.blank(release)
    data["models"] = status
    data["steps"]["step2_intersect"] = {
        "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "input_dir": input_dir,
        "forecasts": len(kept),
        "cell_forecasts": len(pairs),
        "cells": cells,
        "orphans": orphans,
        "flash_areas": a_count,
    }
    if dropped:
        data["notes"].append("models left out of this release: " + ", ".join(sorted(dropped)))
    MAN.write(out_dir, data)
    # Staged runs leave the pointer alone: run_daily moves it once the whole
    # pipeline has finished, so nothing ever points at a part-built release.
    if not staging:
        C.write_release_pointer(release, output_dir)
    print(f"  {C.MANIFEST_NAME:<22} the run record\n")
    print(MAN.summarise(data))
    return release


if __name__ == "__main__":
    main(*sys.argv[1:3] if len(sys.argv) > 1 else ())
