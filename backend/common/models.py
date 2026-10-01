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
    """GloFAS reporting points under alert. Point forecasts, like Flood Hub.

    `ThresGroup` is the highest discharge threshold whose maximum ensemble
    exceedance probability reached 30% — 16 of the 51 members. 0 means the point
    is watched but not alerting. The three thresholds are the 2-, 5- and 20-year
    return periods, the same ladder GEOGLOWS is cut at.

    `start_time` stays blank on purpose. `LeadtimeH` looks like an event start but
    is not one: on 90 of the 1,012 points that carry it, it lands AFTER PeakTime,
    and every one of those 90 is flagged EarlyPeak. It is the lead time of the
    strongest probability, not of the first crossing, so it rides along unchanged
    as lead_time_days rather than being written into a field it would falsify.
    """
    from pyogrio.raw import read

    meta, _m2, _geom, fields = read(path)
    cols = dict(zip(list(meta["fields"]), fields))
    missing = [c for c in ("ThresGroup", "Lat", "Long", "PointID", "ForecastDa", "PeakTime")
               if c not in cols]
    if missing:
        sys.exit(f"{os.path.basename(path)} is missing column(s): {missing}")

    out = []
    for i in range(len(cols["ThresGroup"])):
        try:
            group = int(cols["ThresGroup"][i])
        except (TypeError, ValueError):
            continue
        sev = C.GLOFAS_SEVERITY.get(group)
        if sev is None:
            continue
        lat, lon = _f(cols["Lat"][i]), _f(cols["Long"][i])
        if lat is None or lon is None:
            continue
        issued = _s(cols["ForecastDa"][i])
        r = blank_record()
        r.update({
            "model": "glofas", "severity": sev,
            "native_id": _s(cols["PointID"][i]),
            "return_period_yr": C.GLOFAS_RETURN_PERIOD[group],
            # GloFAS publishes no discharge on the reporting points, and none of
            # the companion rasters carry one either.
            "peak_discharge_cms": "",
            "issued_time": issued,
            "peak_time": _day_offset(issued, cols["PeakTime"][i], base=1),
            "lead_time_days": _int_or_blank(cols.get("LeadtimeH"), i),
            "country": _col(cols, "Country", i),
            "region": _col(cols, "Region", i, C.GLOFAS_PLACEHOLDERS),
            "basin": _col(cols, "Basin", i, C.GLOFAS_PLACEHOLDERS),
            "sub_basin": _col(cols, "Sub-Basin", i, C.GLOFAS_PLACEHOLDERS),
            "station": _col(cols, "Station", i, C.GLOFAS_PLACEHOLDERS),
            "river": _col(cols, "River", i, C.GLOFAS_PLACEHOLDERS),
            "lat": lat, "lon": lon,
        })
        out.append(r)
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
