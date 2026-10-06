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
    """
    root = input_dir or C.INPUT_DIR
    hits = []
    for pattern in C.INPUT_GLOBS[model]:
        hits.extend(glob.glob(os.path.join(root, pattern)))
    if not hits:
        return None
    return max(hits, key=os.path.getmtime)


# ---- per-model readers -----------------------------------------------------

def read_geoglows(path):
    """Flooding GEOGLOWS reaches, keyed by the reach a forecast is issued against.

    GEOGLOWS has no severity label — only a return period — so the ladder in
    config turns years into the shared vocabulary. A reach whose mean flow is
    below the floor is dropped: a trickle can clear its 2-year return period on
    noise alone, and flagging it would put a warning on a dry channel.

    This is the only model whose forecasts are LINES. The record carries no
    geometry; step 2 turns a reach id into its cells through the crosswalk.
    """
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rp = _f(row.get("ret_per"))
            if rp is None:
                continue
            sev = next((s for yr, s in C.GEOGLOWS_THRESHOLDS if rp >= yr), None)
            if sev is None:
                continue
            mean = _f(row.get("mean"))
            if C.GEOGLOWS_MIN_MEAN_FLOW and (mean is None or mean < C.GEOGLOWS_MIN_MEAN_FLOW):
                continue
            try:
                comid = int(float(str(row.get("comid", "")).strip()))
            except ValueError:
                continue
            r = blank_record()
            r.update({
                "model": "geoglows", "severity": sev, "native_id": str(comid),
                "return_period_yr": int(rp), "peak_discharge_cms": mean,
                "lat": _f(row.get("lat")), "lon": _f(row.get("lon")),
            })
            out.append(r)
    return out


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
                "peak_discharge_cms": _s(row.get("dischargePeak_m3s") or row.get("discharge")),
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

    `peak_discharge_cms` and `start_time` stay blank, as they were on the point
    path — GloFAS publishes neither. `lead_time_days` is blank off the overlay:
    meanLT.nc looks like the gridded `LeadtimeH` but matches it on only 9.8% of
    the points where both exist, so it is a different quantity and is not used.
    """
    import numpy as np
    import netCDF4

    stamp = os.path.basename(path).split("_AL_")[-1].split(".")[0]
    issued = _glofas_issued(stamp)
    folder = os.path.dirname(path)

    lat = lon = None
    level_of = None
    for group in sorted(C.GLOFAS_GRID_LEVELS):                 # 1, 2, 3
        name = os.path.basename(path).replace(
            f"sumAL_{C.GLOFAS_GRID_LEVELS[1]}_AL_",
            f"sumAL_{C.GLOFAS_GRID_LEVELS[group]}_AL_")
        p = os.path.join(folder, name)
        if not os.path.exists(p):
            sys.exit(f"{os.path.basename(path)}: companion grid not found: {name}")
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
    # hexagon is already coloured by, so nothing on the map moves — only the
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
            "peak_discharge_cms": "",
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
