"""Readers that turn each model's native file into one common forecast record.

Every model publishes something different — GEOGLOWS a CSV of reaches keyed by
comid, Flood Hub a CSV of gauges, GloFAS a shapefile of reporting points — and
each says "this place is flooding" in its own vocabulary. This module is the only
place that knows those differences. Everything downstream sees one record shape.

Two rules hold for every reader:

  * Only flagged forecasts come back. A model's file lists everything it watches,
    flooding or not; filtering here rather than after the intersect is what keeps
    the crosswalk join to the few thousand reaches that matter instead of all of
    them.

  * A field is filled from what the model actually published, or left blank.
    Nothing is inferred, and a value the model does not carry stays empty rather
    than being borrowed from somewhere plausible.

Adding a fourth model means writing one function here and one entry in READERS.
"""

import csv
import glob
import os
import sys
from datetime import datetime

from . import config as C


# ---- helpers ---------------------------------------------------------------

def _f(v):
    """A float, or None for blanks and anything unparseable."""
    v = str(v or "").strip()
    if v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _s(v, placeholders=()):
    """A trimmed string, blanked when it is one of `placeholders`."""
    out = str(v if v is not None else "").strip()
    return "" if out.lower() in placeholders else out


def _col(cols, name, i, placeholders=()):
    """One cell of a shapefile's column arrays, blank when the column is absent."""
    col = cols.get(name)
    return "" if col is None else _s(col[i], placeholders)


def blank_record():
    """Every column the contract promises, all empty. Readers fill what they have,
    so a reader can never emit a record that is missing a column downstream expects."""
    return {k: "" for k in C.FORECAST_COLUMNS}


def find_input(model, input_dir=None):
    """The newest file in the input folder matching this model's shape.

    Release filenames carry their date, so they are matched by pattern. Newest by
    modification time wins when a folder holds more than one release, and the
    caller is told which file was chosen — picking the wrong day's file silently
    is the worst failure this pipeline could have.

    A dated folder falls back to the input root. Step 1 fetches into
    input/<YYYY-MM-DD>/, but only GEOGLOWS is automated so far, and without this a
    live run would publish a one-model release while the other three sat in the
    folder above. Looking in the dated folder FIRST and the root second means each
    model moves from hand-placed to fetched on its own, with no flag day — and the
    moment a model is automated, its fetched file shadows whatever is in the root.

    The fallback is deliberately one level and only to the configured input root,
    so it can never wander into a different day's folder.
    """
    root = input_dir or C.current_input()
    places = [root]
    parent = os.path.dirname(os.path.normpath(root))
    if os.path.normpath(root) != os.path.normpath(C.INPUT_DIR) and \
            os.path.normpath(parent) == os.path.normpath(C.INPUT_DIR):
        places.append(C.INPUT_DIR)

    for place in places:
        hits = []
        for pattern in C.INPUT_GLOBS[model]:
            hits.extend(glob.glob(os.path.join(place, pattern)))
        if hits:
            return max(hits, key=os.path.getmtime)
    return None


# ---- per-model readers -----------------------------------------------------

def reach_points(comids):
    """{comid: (lat, lon)} for the reaches asked for, from the static table.

    The daily map-tables carry no coordinates, and admin.assign places a forecast
    by coordinate alone, so this is what keeps a GEOGLOWS forecast knowing which
    district it is in. Built by static/build_reach_points.py.

    The table is sorted by comid and written in row groups, so the reader skips
    most of the file on its statistics rather than scanning 6.8M rows: measured at
    0.67s against 6.85s for a streamed gzip of the same data.
    """
    import pyarrow.compute as pc
    import pyarrow.dataset as ds

    want = sorted({int(c) for c in comids})
    if not want:
        return {}
    if not os.path.exists(C.REACH_POINTS):
        print(f"  note: no {os.path.basename(C.REACH_POINTS)}; GEOGLOWS forecasts "
              f"will have no coordinates, so no district either "
              f"(build it with static/build_reach_points.py)", file=sys.stderr)
        return {}
    t = ds.dataset(C.REACH_POINTS, format="parquet").to_table(
        columns=["comid", "lat", "lon"], filter=pc.field("comid").isin(want))
    return {int(c): (float(la), float(lo)) for c, la, lo in
            zip(t["comid"].to_pylist(), t["lat"].to_pylist(), t["lon"].to_pylist())}


def _cycle_of(path):
    """`mapstyletable_201_2026082200.parquet` -> ('2026082200', datetime).

    The cycle is in the filename, and it is the only authoritative statement of
    when this forecast was issued — the rows themselves carry valid times, not an
    issue time, and a file's mtime says when we downloaded it. Before this,
    GEOGLOWS was the one model with no issue date of its own, which is why the run
    record had to fall back to mtime for it.
    """
    stem = os.path.basename(path).rsplit(".", 1)[0]
    tail = stem.rsplit("_", 1)[-1]
    if len(tail) == 10 and tail.isdigit():
        try:
            return tail, datetime(int(tail[0:4]), int(tail[4:6]),
                                  int(tail[6:8]), int(tail[8:10]))
        except ValueError:
            pass
    return "", None


def _valid_of(path):
    """`mapstyletable_2026-10-21-06_2026102100.csv` -> datetime(2026,10,21,6).

    The bucket names each map-table CSV for the time the forecast is VALID, not
    for the cycle that produced it — the cycle is the folder. step1_fetch appends
    the cycle stamp when it saves one, so a local file carries both.

    Returns None for the older archived files, which carry neither, and for
    anything that does not parse. A missing valid time costs the record its
    peak_time; it never costs the forecast.
    """
    stem = os.path.basename(path).rsplit(".", 1)[0]
    for part in stem.split("_"):
        bits = part.split("-")
        if len(bits) == 4 and all(b.isdigit() for b in bits):
            try:
                return datetime(int(bits[0]), int(bits[1]), int(bits[2]), int(bits[3]))
            except ValueError:
                pass
    return None


def _cycle_siblings(path):
    """Every per-VPU parquet of the SAME cycle sitting beside `path`.

    Matched on the cycle stamp rather than on "every parquet in the folder", so a
    folder that ended up holding two cycles reads as one of them instead of
    silently blending a day's forecasts with yesterday's.
    """
    stamp, _ = _cycle_of(path)
    if not stamp:
        return [path]
    pattern = os.path.join(os.path.dirname(path), f"*_{stamp}.parquet")
    return sorted(glob.glob(pattern)) or [path]


def _publish_step(files, cycle):
    """(timestamp, note) — which step of the forecast this release reports.

    C.GEOGLOWS_FORECAST_HOUR on the cycle's own date.

    The step is chosen from the TIME GRID the files publish, never from the steps
    that happen to carry a flag. Those are different questions, and answering the
    second one looks right until a quiet morning: if nothing floods anywhere at
    06:00, "the steps with flags" would not contain 06:00, and the reader would
    silently report a different hour than the config claims.

    One file is probed rather than all of them — every VPU in a cycle shares the
    same time axis — and the probe pushes an equality filter down, so it reads the
    statistics of a few row groups rather than any data.

    Replacing this with a max-over-window rule is a change to this function and the
    filter that uses it; every step is already in the files.
    """
    import pyarrow.compute as pc
    import pyarrow.dataset as ds

    one = ds.dataset(files[0], format="parquet")

    def grid():
        return sorted(set(one.to_table(columns=["timestamp"])["timestamp"].to_pylist()))

    if cycle is None:
        steps = grid()
        return (steps[0] if steps else None), "cycle unknown; took the first step"

    target = cycle.replace(hour=C.GEOGLOWS_FORECAST_HOUR, minute=0,
                           second=0, microsecond=0)
    probe = one.to_table(columns=["timestamp"],
                         filter=pc.equal(pc.field("timestamp"), target))
    if probe.num_rows:
        return target, ""

    steps = grid()
    if not steps:
        return None, "the parquets carry no timesteps"
    nearest = min(steps, key=lambda t: abs(t - target))
    return nearest, (f"this cycle publishes no {target:%Y-%m-%d %H:%M} step; "
                     f"reporting {nearest:%Y-%m-%d %H:%M} instead")


def _severity(rp):
    return next((s for yr, s in C.GEOGLOWS_THRESHOLDS if rp >= yr), None)


def _geoglows_record(comid, rp, mean, cycle, step, pts):
    la, lo = pts.get(comid, ("", ""))
    r = blank_record()
    r.update({
        "model": "geoglows", "severity": _severity(rp), "native_id": str(comid),
        "return_period_yr": int(rp), "flow_cms": mean,
        "lat": la, "lon": lo,
    })
    if cycle:
        r["issued_time"] = cycle.strftime("%Y-%m-%dT%H:%M:%SZ")
        if step:
            r["peak_time"] = step.strftime("%Y-%m-%dT%H:%M:%SZ")
            r["lead_time_days"] = round((step - cycle).total_seconds() / 86400, 2)
    return r


def read_geoglows_parquet(path):
    """Flooding GEOGLOWS reaches, from one cycle's per-VPU parquets.

    Each parquet holds one VPU's whole horizon — every reach at all 81 three-hourly
    steps — as `timestamp, comid, mean, thickness, ret_per`. Almost none of it is a
    flood: measured on one VPU, 832 of 3,434,562 rows clear the 2-year return
    period, and globally the flagged share is about 0.3%. So the step and the
    thresholds are pushed down into the reader together and whole row groups are
    skipped without being decompressed.

    The return-period floor comes from the configured ladder rather than a literal,
    so moving a threshold cannot leave the pushdown filtering on the old one.
    """
    import pyarrow.compute as pc
    import pyarrow.dataset as ds

    files = _cycle_siblings(path)
    stamp, cycle = _cycle_of(path)
    step, why = _publish_step(files, cycle)
    if why:
        print(f"  note: geoglows {why}", file=sys.stderr)
    if step is None:
        return []

    floor = min(yr for yr, _ in C.GEOGLOWS_THRESHOLDS)
    expr = (pc.equal(pc.field("timestamp"), step)) & (pc.field("ret_per") >= floor)
    if C.GEOGLOWS_MIN_MEAN_FLOW:
        expr = expr & (pc.field("mean") >= C.GEOGLOWS_MIN_MEAN_FLOW)

    rows = ds.dataset(files, format="parquet").to_table(
        columns=["comid", "mean", "ret_per"], filter=expr)

    comids = [int(c) for c in rows["comid"].to_pylist()]
    means = rows["mean"].to_pylist()
    rps = rows["ret_per"].to_pylist()
    pts = reach_points(comids)
    out = [_geoglows_record(c, rp, m, cycle, step, pts)
           for c, rp, m in zip(comids, rps, means) if _severity(rp)]

    lead = f" (+{(step - cycle).total_seconds() / 3600:.0f}h)" if cycle else ""
    print(f"  geoglows   {len(files)} parquet(s), cycle {stamp or '?'}, "
          f"reporting {step:%Y-%m-%d %H:%M}Z{lead}")
    missing = sum(1 for r in out if r["lat"] == "")
    if missing:
        print(f"  note: {missing:,} GEOGLOWS reach(es) are not in "
              f"{os.path.basename(C.REACH_POINTS)}; they will have no district",
              file=sys.stderr)
    return out


def read_geoglows_csv(path):
    """One timestep of the map-table, as CSV.

    GEOGLOWS has no severity label — only a return period — so the ladder in config
    turns years into the shared vocabulary. A reach whose mean flow is below the
    floor is dropped: a trickle can clear its 2-year return period on noise alone,
    and flagging it would put a warning on a dry channel.

    Two vintages arrive here and the difference is the coordinates. The archived
    files carry lat/lon per row; the bucket's carry only
    `comid, mean, thickness, ret_per`. Where the columns are absent the static
    reach table supplies them, so a forecast keeps its district either way — and
    the lookup runs once for the whole file rather than per row.
    """
    out, need = [], []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rp = _f(row.get("ret_per"))
            if rp is None:
                continue
            sev = _severity(rp)
            if sev is None:
                continue
            mean = _f(row.get("mean"))
            if C.GEOGLOWS_MIN_MEAN_FLOW and (mean is None or mean < C.GEOGLOWS_MIN_MEAN_FLOW):
                continue
            try:
                comid = int(float(str(row.get("comid", "")).strip()))
            except ValueError:
                continue
            la, lo = _f(row.get("lat")), _f(row.get("lon"))
            r = blank_record()
            r.update({
                "model": "geoglows", "severity": sev, "native_id": str(comid),
                "return_period_yr": int(rp), "flow_cms": mean,
                "lat": "" if la is None else la, "lon": "" if lo is None else lo,
            })
            if la is None or lo is None:
                need.append((comid, r))
            out.append(r)

    if need:
        pts = reach_points(c for c, _ in need)
        for comid, r in need:
            if comid in pts:
                r["lat"], r["lon"] = pts[comid]
        missing = sum(1 for _c, r in need if r["lat"] == "")
        if missing:
            print(f"  note: {missing:,} GEOGLOWS reach(es) are not in "
                  f"{os.path.basename(C.REACH_POINTS)}; they will have no district",
                  file=sys.stderr)

    # The filename is the only place a CSV states when it was issued. Archived
    # files carry neither stamp, and then the run record falls back to mtime as
    # it always did.
    _stamp, cycle = _cycle_of(path)
    valid = _valid_of(path)
    if cycle:
        issued = cycle.strftime("%Y-%m-%dT%H:%M:%SZ")
        for r in out:
            r["issued_time"] = issued
            if valid:
                r["peak_time"] = valid.strftime("%Y-%m-%dT%H:%M:%SZ")
                r["lead_time_days"] = round((valid - cycle).total_seconds() / 86400, 2)
    return out


def read_geoglows(path):
    """Flooding GEOGLOWS reaches, keyed by the reach a forecast is issued against.

    This is the only model whose forecasts are LINES. The record carries no
    geometry; step 2 turns a reach id into its cells through the crosswalk.

    Two shapes reach this, and which one arrives is decided by the file extension
    rather than by configuration: the per-VPU parquets the bucket publishes, and
    the single-timestep CSV the earlier releases were built from.
    """
    if str(path).lower().endswith(".parquet"):
        return read_geoglows_parquet(path)
    return read_geoglows_csv(path)


def read_flood_hub(path):
    """Flood Hub gauge alerts. Point forecasts, binned by their own coordinate.

    A gauge with no usable coordinate is dropped rather than guessed at — there is
    no reach id to fall back on the way GEOGLOWS has.
    """
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            sev = C.FLOOD_HUB_SEVERITY.get(_s(row.get("severity")).upper())
            if sev is None:
                continue
            lat, lon = _f(row.get("gaugeLocation.latitude")), _f(row.get("gaugeLocation.longitude"))
            if lat is None or lon is None:
                continue
            r = blank_record()
            r.update({
                "model": "flood_hub", "severity": sev,
                "native_id": _s(row.get("gaugeId")),
                "return_period_yr": _s(row.get("returnPeriodYr")),
                # `discharge` was the column name before the world_flood_status
                # export; both are read so an older release still loads.
                "flow_cms": _s(row.get("dischargePeak_m3s") or row.get("discharge")),
                "issued_time": _s(row.get("issuedTime")),
                "start_time": _s(row.get("forecastTimeRange.start")),
                "peak_time": _s(row.get("peakTime")),
                "end_time": _s(row.get("forecastTimeRange.end")),
                "country": _s(row.get("queriedCountryName")),
                "lat": lat, "lon": lon,
            })
            out.append(r)
    return out


def read_glofas(path):
    """GloFAS alerts, read from the severity grids rather than the reporting points.

    `path` is the 2-year grid; the 5- and 20-year grids sit beside it under the
    same release stamp. Each is a global 0.05-degree field carried only on river
    pixels, whose value is how many of the 51 ensemble members exceeded that
    return period. A pixel is flagged at the highest level reaching 30% of the
    ensemble — 16 members.

    That is the same rule the reporting points encode as `ThresGroup`, which is
    why this is a change of resolution and not a change of source. Sampling these
    grids at all 4,122 point coordinates reproduces `ThresGroup` exactly, with no
    exceptions, and the X/Y columns snap to grid centres at zero distance: the
    points ARE pixels. They are a 5% sample of the field — 1,330 alerting points
    against 26,185 alerting pixels — so reading the grid instead keeps every cell
    the points produced and adds roughly twenty thousand more.

    The points still ride along as an overlay. The grid has no concept of a
    station, a river, or the day a flood peaks, so where a pixel IS a point its
    row keeps that point's id and attributes unchanged and the output for it is
    identical to what the point reader wrote. Pixels without one carry severity,
    return period and position, and let step 4's ADM cascade do the naming. No
    attribute is ever inherited from a neighbouring point: the nearest one sits
    20 km away at the median and disagrees on the alert level once in five, which
    would be a guess dressed as a fact.

    `flow_cms` and `start_time` stay blank, as they were on the point
    path — GloFAS publishes neither. `lead_time_days` is blank off the overlay:
    meanLT.nc looks like the gridded `LeadtimeH` but matches it on only 9.8% of
    the points where both exist, so it is a different quantity and is not used.
    """
    import numpy as np
    import netCDF4

    stamp = os.path.basename(path).split("_AL_")[-1].split(".")[0]
    issued = _glofas_issued(stamp)
    folder = os.path.dirname(path)

    # A missing companion grid is not a partial loss, because the levels NEST:
    # every extreme pixel is also a high pixel and a medium one, and a pixel is
    # reported at the highest grid that still has it over threshold. So dropping a
    # grid does not drop those forecasts — it hands them to the next grid down and
    # publishes them one severity too low. An extreme flood reported as high is
    # the one failure an early warning system must never ship quietly, and it
    # looks like a perfectly ordinary release from every side.
    #
    # The lowest grid is the exception, and only because it is the file this
    # reader is handed: without it there is no GloFAS input to find at all, and
    # step 2 records the model as skipped before reaching here.
    #
    # So: any companion grid missing means GloFAS is left out of the release.
    # Step 2 catches this and writes the day without it, which is honest — a map
    # with three models and a note beats a map with four and a wrong one.
    floor = min(C.GLOFAS_GRID_LEVELS)
    found = {}
    for group in sorted(C.GLOFAS_GRID_LEVELS):                 # 1, 2, 3
        name = os.path.basename(path).replace(
            f"sumAL_{C.GLOFAS_GRID_LEVELS[1]}_AL_",
            f"sumAL_{C.GLOFAS_GRID_LEVELS[group]}_AL_")
        q = os.path.join(folder, name)
        if os.path.exists(q):
            found[group] = q
        elif group != floor:
            raise RuntimeError(
                f"{os.path.basename(path)}: the {C.GLOFAS_GRID_LEVELS[group]} grid "
                f"({name}) is missing. The levels nest, so without it every pixel "
                f"that reaches {C.GLOFAS_GRID_LEVELS[group]} would be published at "
                f"the severity below it. GloFAS is left out of this release rather "
                f"than understated.")

    lat = lon = None
    level_of = None
    for group, p in sorted(found.items()):
        with netCDF4.Dataset(p) as d:
            if lat is None:
                lat = np.asarray(d.variables["lat"][:])
                lon = np.asarray(d.variables["lon"][:])
                level_of = np.zeros((lat.size, lon.size), dtype=np.int8)
            v = d.variables[C.GLOFAS_GRID_VAR]
            counts = np.ma.filled(v[:], v._FillValue)
            # Later levels overwrite earlier ones, so a pixel ends up at the
            # highest it reaches. The levels nest — every 20-year pixel is also a
            # 5-year pixel — so this cannot leave a gap.
            level_of[(counts != v._FillValue)
                     & (counts >= C.GLOFAS_ALERT_MEMBERS)] = group

    points = _glofas_points(folder)
    iy, ix = np.nonzero(level_of > 0)

    # One forecast per CELL, not per pixel. A res-6 hexagon is 36.1 km2 while a
    # 0.05-degree pixel runs from 30.4 km2 at the equator down to 13.6 km2 above
    # 60 degrees, so two and three pixels routinely land in the same hexagon —
    # 3,816 of 22,057 cells in this forecast — and one forecast each puts the
    # identical warning in the panel two and three times over. Nothing separates
    # them: they agree on severity in 93.5% of those cells and on every displayed
    # field in 72.7%.
    #
    # The cell keeps the WORST severity among its pixels, which is what the
    # hexagon is already colored by, so nothing on the map moves — only the
    # repeated row leaves. Choosing the most DOWNSTREAM pixel would be better
    # hydrology, but these files carry no flow direction and no upstream area,
    # just a value per pixel; and it could resolve an extreme-and-danger pair
    # down to danger, which is the wrong direction for a warning to travel.
    import h3

    # Severity and identity come from the worst pixel in the cell. The PLACE
    # names come from the cell's gauge if it has one, even where that gauge is
    # not the worst pixel: `station` and `river` answer where the cell IS and
    # stay true however bad the forecast gets, while `peak_time` and
    # `lead_time_days` belong to one pixel's own forecast and would be a lie
    # pinned to another pixel's severity.
    #
    # Two gauges in one hexagon is the one place upstream area exists, so it
    # settles that tie: the larger catchment is the downstream one, and the reach
    # a reader is likelier to mean.
    best, gauge = {}, {}
    for y, x in zip(iy, ix):
        la, lo = round(float(lat[y]), 3), round(float(lon[x]), 3)
        cell = h3.latlng_to_cell(la, lo, C.BASE_RES)
        pt = points.get((la, lo))
        key = (int(level_of[y, x]), 1 if pt else 0)
        if cell not in best or key > best[cell][0]:
            best[cell] = (key, la, lo, pt or {})
        if pt and (cell not in gauge or pt["ups_area"] > gauge[cell]["ups_area"]):
            gauge[cell] = pt

    rows = []
    for cell, ((group, _has_pt), la, lo, pt) in best.items():
        place = gauge.get(cell) or pt
        r = blank_record()
        r.update({
            "model": "glofas",
            "severity": C.GLOFAS_SEVERITY[group],
            "native_id": pt.get("native_id") or f"g{la:.3f}_{lo:.3f}",
            "return_period_yr": C.GLOFAS_RETURN_PERIOD[group],
            "flow_cms": "",
            "issued_time": pt.get("issued_time") or issued,
            "peak_time": pt.get("peak_time", ""),
            "lead_time_days": pt.get("lead_time_days", ""),
            "country": place.get("country", ""),
            "region": place.get("region", ""),
            "basin": place.get("basin", ""),
            "sub_basin": place.get("sub_basin", ""),
            "station": place.get("station", ""),
            "river": place.get("river", ""),
            "lat": la, "lon": lo,
        })
        rows.append(r)
    return rows


def _glofas_issued(stamp):
    """`2026092300` -> `2026-09-23`. The hour is the forecast cycle, not a date."""
    return f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}" if len(stamp) >= 8 else ""


def _glofas_points(folder):
    """Reporting points keyed by their grid centre, for the attribute overlay.

    Keyed on X/Y rather than Lat/Long: those are the point's position ON the
    GloFAS river network — the grid cell it was drawn from — while Lat/Long is a
    display position that can sit a few hundred metres off the cell centre.
    """
    from pyogrio.raw import read

    hits = glob.glob(os.path.join(folder, C.GLOFAS_POINTS_GLOB))
    if not hits:
        return {}
    meta, _m2, _geom, fields = read(max(hits, key=os.path.getmtime))
    cols = dict(zip(list(meta["fields"]), fields))
    if not {"X", "Y", "ThresGroup"} <= set(cols):
        return {}

    out = {}
    for i in range(len(cols["ThresGroup"])):
        try:
            if int(cols["ThresGroup"][i]) <= 0:
                continue                      # watched, but not alerting
        except (TypeError, ValueError):
            continue
        x, y = _f(cols["X"][i]), _f(cols["Y"][i])
        if x is None or y is None:
            continue
        issued = _s(cols["ForecastDa"][i]) if "ForecastDa" in cols else ""
        out[(round(y, 3), round(x, 3))] = {
            "ups_area": (_f(cols["UpsArea"][i]) or 0.0) if "UpsArea" in cols else 0.0,
            "native_id": _s(cols["PointID"][i]) if "PointID" in cols else "",
            "issued_time": issued,
            "peak_time": _day_offset(issued, cols["PeakTime"][i], base=1)
                         if "PeakTime" in cols else "",
            "lead_time_days": _int_or_blank(cols.get("LeadtimeH"), i),
            "country": _col(cols, "Country", i),
            "region": _col(cols, "Region", i, C.GLOFAS_PLACEHOLDERS),
            "basin": _col(cols, "Basin", i, C.GLOFAS_PLACEHOLDERS),
            "sub_basin": _col(cols, "Sub-Basin", i, C.GLOFAS_PLACEHOLDERS),
            "station": _col(cols, "Station", i, C.GLOFAS_PLACEHOLDERS),
            "river": _col(cols, "River", i, C.GLOFAS_PLACEHOLDERS),
        }
    return out


def _int_or_blank(col, i):
    if col is None:
        return ""
    try:
        n = int(col[i])
    except (TypeError, ValueError):
        return ""
    return "" if n < 0 else n


def _day_offset(issued, steps, base):
    """`issued` (YYYY-MM-DD) shifted by a whole-day index, as a date string.

    GloFAS counts its 15-day horizon in whole forecast days, and its two day
    columns are numbered differently — PeakTime is 1-based, LeadtimeH 0-based —
    so `base` says which. A negative index is the file's not-applicable sentinel
    and yields "" rather than a date in the past.
    """
    from datetime import date, timedelta
    try:
        n = int(steps)
    except (TypeError, ValueError):
        return ""
    if n < 0:
        return ""
    try:
        y, m, d = (int(x) for x in str(issued).split("-"))
        start = date(y, m, d)
    except ValueError:
        return ""
    return (start + timedelta(days=n - base)).isoformat()


def read_flash(path):
    """Urban flash-flood forecast areas. The only model that publishes POLYGONS.

    Flash areas stay areas the whole way through. They are not put on the H3 grid:
    an urban flash warning is about a footprint, and snapping it to hexagons would
    both coarsen a boundary that is already precise and quietly inflate its extent
    to the cells it merely clips.

    The geometry rides on the record under a private `_geometry` key. It is not one
    of FORECAST_COLUMNS, so it never reaches the forecast table — step 2 routes it
    to the areas sidecar instead.

    `lat`/`lon` are a representative interior point rather than the centroid: a
    crescent-shaped or multipart area can have its centroid outside itself, which
    would put the Near lookup in the wrong district.
    """
    import json
    from shapely.geometry import shape

    with open(path, encoding="utf-8") as f:
        geo = json.load(f)

    out = []
    for feat in geo.get("features", []):
        props = feat.get("properties") or {}
        sev = C.FLASH_SEVERITY.get(_s(props.get("polygon_type")).lower())
        if sev is None:
            continue
        geom = feat.get("geometry")
        if not geom:
            continue
        try:
            pt = shape(geom).representative_point()
        except Exception:
            continue
        issued = _s(props.get("forecastIssueTime"))
        r = blank_record()
        r.update({
            "model": "flash", "severity": sev,
            "native_id": _s(props.get("polygonId")) or _s(props.get("event_index")),
            "issued_time": issued,
            "start_time": issued,          # the window opens when it is issued
            "end_time": _add_hours(issued, props.get("forecastPeriodHours")),
            "country": _s(props.get("affectedCountryNames")),
            "lat": round(pt.y, 6), "lon": round(pt.x, 6),
        })
        r["_geometry"] = geom
        out.append(r)
    return out


def _add_hours(iso, hours):
    """An ISO instant advanced by a whole number of hours, or "".

    Flash floods publish an issue time and a window length rather than an end, so
    the end is the one derived value in these readers — arithmetic on two published
    numbers, not a guess.
    """
    from datetime import datetime, timedelta
    try:
        n = int(hours)
    except (TypeError, ValueError):
        return ""
    txt = str(iso or "").strip()
    if not txt:
        return ""
    try:
        base = datetime.fromisoformat(txt.replace("Z", "+00:00"))
    except ValueError:
        return ""
    end = base + timedelta(hours=n)
    return end.isoformat().replace("+00:00", "Z")


# model -> (reader, how its forecasts reach the grid).
#   line     joined through the reach crosswalk — one reach, many cells
#   point    binned by its own coordinate — one forecast, one cell
#   area     not gridded at all — its own footprint is the forecast, and travels
#            alongside the tables rather than through them
READERS = {
    "geoglows": (read_geoglows, "line"),
    "flood_hub": (read_flood_hub, "point"),
    "glofas": (read_glofas, "point"),
    "flash": (read_flash, "area"),
}
