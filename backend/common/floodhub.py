"""Google Flood Hub — the two products FEWS4All reads, fetched for one day.

    world_flood_status.csv      every gauge's latest status world-wide, plus
                                discharge, peak timing and return period for the
                                ones under warning          -> the flood_hub model
    urban_flash_floods.geojson  likely / highly-likely flash polygons
                                                            -> the flash model

Ported from Other/Flood_Hub_Downloader, which runs in Colab and reads its key
from Colab's Secrets tab. The column names here are deliberately identical to the
notebook's, because those names are the contract read_flood_hub already reads.

THE KEY NEVER REACHES A LOG

Google puts credentials in the query string, so every request URL contains the
key. This module ships its output to a file that fews4all.sh tees into
logs/<date>.log, so one unhandled traceback containing a URL would write the key
into the project folder in plaintext, permanently. Therefore: URLs are built
inside _request and never returned, errors name the ENDPOINT and never the URL,
and _redact scrubs anything that looks like a key out of exception text before it
is allowed near a print. Treat that as load-bearing, not tidiness.

WHAT IT GUARDS AGAINST

  A partial sweep. 248 countries are queried one at a time. If some fail, the CSV
  is still well-formed and still parses — it is just missing a continent, which
  reads downstream as "no floods there". So failures are counted, named, and
  gate the result rather than being printed and forgotten.

  Spending the quota twice. The sweep checkpoints per country, so a run that dies
  at country 200 resumes instead of paying for those 200 again. Gauge thresholds
  and flash polygon geometry are cached between runs, because both are static
  data that merely happen to arrive with a daily feed.
"""

import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from . import config as C
from .tables import write_text

TIMEOUT = 120
KML_NS = {"k": "http://www.opengis.net/kml/2.2"}


# ---- talking to the API, without ever exposing the key ---------------------

def _redact(text):
    """Scrub anything key-shaped out of a string bound for a log."""
    return re.sub(r"(key=)[^&\s'\")]+", r"\1<redacted>", str(text))


def _request(method, path, key, params=None, body=None):
    """One call. Returns parsed JSON, or raises with the ENDPOINT named, never
    the URL — see the module docstring for why that distinction matters."""
    query = dict(params or {})
    query["key"] = key
    url = f"{C.FLOOD_HUB_BASE}/{path}?" + urllib.parse.urlencode(query, doseq=True)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:
            pass
        raise RuntimeError(f"{path}: HTTP {e.code} {_redact(detail)}") from None
    except Exception as e:
        raise RuntimeError(f"{path}: {type(e).__name__}: {_redact(e)}") from None


def key_from_env():
    return os.environ.get(C.FLOOD_HUB_KEY_ENV, "").strip()


def _buckets(seq, n):
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def _resilient(ids, call, bad):
    """Yield successful responses, binary-splitting a batch that one bad id sinks.

    Kept from the notebook. A single id the endpoint rejects would otherwise cost
    the whole bucket of 50; splitting finds it in log2(n) calls and skips only it.
    """
    if not ids:
        return
    try:
        yield call(ids)
    except RuntimeError:
        if len(ids) == 1:
            bad.append(ids[0])
            return
        mid = len(ids) // 2
        yield from _resilient(ids[:mid], call, bad)
        yield from _resilient(ids[mid:], call, bad)


# ---- a cache for the things that are static ---------------------------------

def _load_cache(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_cache(path, data):
    try:
        write_text(path, json.dumps(data, sort_keys=True))
    except OSError as e:
        print(f"  note: could not write {os.path.basename(path)} ({e})",
              file=sys.stderr)


# ---- part one: every gauge's latest status ----------------------------------

def region_codes():
    """(codes, {code: country name}) from the static country table."""
    codes, names = [], {}
    with open(C.COUNTRY_CODES, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            code = (row.get("alpha-2") or "").strip()
            if code:
                codes.append(code)
                names[code] = (row.get("name") or "").strip()
    return codes, names


def sweep_gauges(key, work_dir, names, codes, progress=True):
    """Every gauge's latest status, one country at a time, resumable.

    Checkpointed because this is the expensive half of the quota: each country's
    records are appended to a .jsonl as they arrive and the code written to a
    .done file, so a run that dies partway resumes rather than paying again.
    """
    part = os.path.join(work_dir, "world_flood_status.part.jsonl")
    done_path = os.path.join(work_dir, "world_flood_status.done.txt")
    os.makedirs(work_dir, exist_ok=True)

    done = set()
    if os.path.exists(done_path):
        with open(done_path, encoding="utf-8") as f:
            done = {ln.strip() for ln in f if ln.strip()}
    statuses = []
    if done and os.path.exists(part):
        with open(part, encoding="utf-8") as f:
            statuses = [json.loads(ln) for ln in f if ln.strip()]
        print(f"  flood_hub  resuming — {len(done)} country(ies) already queried, "
              f"{len(statuses):,} gauge(s) held")

    failed, t0 = {}, time.time()
    with open(part, "a", encoding="utf-8") as pf, \
            open(done_path, "a", encoding="utf-8") as df:
        for i, code in enumerate(codes, 1):
            if code in done:
                continue
            body = {"regionCode": code, "pageSize": 10000,
                    "includeNonQualityVerified": False}
            got = []
            try:
                while True:
                    resp = _request("POST", "floodStatus:searchLatestFloodStatusByArea",
                                    key, body=body)
                    for rec in resp.get("floodStatuses", []) or []:
                        rec["queriedRegionCode"] = code
                        rec["queriedCountryName"] = names.get(code, "")
                        got.append(rec)
                    token = resp.get("nextPageToken")
                    if not token:
                        break
                    body["pageToken"] = token
            except RuntimeError as e:
                failed[code] = str(e)
                continue                      # not marked done; a rerun retries it
            for rec in got:
                pf.write(json.dumps(rec) + "\n")
            statuses.extend(got)
            df.write(code + "\n")
            if progress and (i % 25 == 0 or i == len(codes)):
                pf.flush(); df.flush()
                print(f"  flood_hub  [{i:>3}/{len(codes)}] {len(statuses):>6,} gauge(s), "
                      f"{len(failed)} region(s) failed  {time.time() - t0:>4.0f}s",
                      flush=True)
    return statuses, failed, (part, done_path)


# ---- part two: discharge, peak timing and return period for warned gauges ---

def enrich_warned(statuses, key, refresh_models=False):
    """{gaugeId: {...}} for gauges under warning, with their thresholds applied.

    A gauge's thresholds ARE its return periods and do not change, so they are
    cached and only fetched for gauges never seen before. The forecasts are not
    cacheable — that is the part that is actually new each morning.
    """
    import statistics

    warned = sorted({str(s.get("gaugeId")) for s in statuses
                     if s.get("severity") in C.FLOOD_HUB_WARNING_SEVERITIES
                     and s.get("gaugeId")})
    if not warned:
        return {}, {"warned": 0}

    cache = {} if refresh_models else _load_cache(C.FLOOD_HUB_MODEL_CACHE)
    unknown = [g for g in warned if g not in cache]
    bad_models = []
    if unknown:
        def call(ids):
            return _request("GET", "gaugeModels:batchGet", key,
                            params={"names": [f"gaugeModels/{g}" for g in ids]})
        for bucket in _buckets(unknown, C.FLOOD_HUB_MODEL_BUCKET):
            for res in _resilient(bucket, call, bad_models):
                for m in res.get("gaugeModels") or []:
                    gid = str(m.get("gaugeId"))
                    thr = m.get("thresholds") or {}
                    cache[gid] = {
                        "unit": m.get("gaugeValueUnit"),
                        "thresholds": {k: thr.get(k) for k in
                                       ("warningLevel", "dangerLevel",
                                        "extremeDangerLevel")},
                    }
        _save_cache(C.FLOOD_HUB_MODEL_CACHE, cache)

    discharge = [g for g in warned
                 if (cache.get(g) or {}).get("unit") == C.FLOOD_HUB_DISCHARGE_UNIT]

    start = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 7 * 86400))
    end = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 86400))
    bad_fc, out = [], {}

    def call_fc(ids):
        return _request("GET", "gauges:queryGaugeForecasts", key,
                        params={"gaugeIds": ids, "issuedTimeStart": start,
                                "issuedTimeEnd": end})

    for bucket in _buckets(discharge, C.FLOOD_HUB_FORECAST_BUCKET):
        for res in _resilient(bucket, call_fc, bad_fc):
            for gid, block in (res.get("forecasts") or {}).items():
                runs = block.get("forecasts") or []
                if not runs:
                    continue
                latest = max(runs, key=lambda f: f.get("issuedTime", ""))
                ranges = [r for r in (latest.get("forecastRanges") or [])
                          if isinstance(r.get("value"), (int, float))]
                if not ranges:
                    continue
                top = max(ranges, key=lambda r: r["value"])
                thr = (cache.get(str(gid)) or {}).get("thresholds") or {}
                out[str(gid)] = {
                    "dischargePeak_m3s": round(top["value"], 1),
                    "peakTime": top.get("forecastStartTime") or "",
                    "dischargeMean_m3s": round(
                        statistics.mean(r["value"] for r in ranges), 1),
                    "returnPeriodYr": _return_period(top["value"], thr),
                }

    return out, {"warned": len(warned), "discharge_gauges": len(discharge),
                 "enriched": len(out), "models_fetched": len(unknown),
                 "bad_model_ids": len(bad_models), "bad_forecast_ids": len(bad_fc),
                 "units": {g: (cache.get(g) or {}).get("unit") for g in warned}}


def _return_period(value, thr):
    for name, years in C.FLOOD_HUB_THRESHOLD_YEARS:
        t = thr.get(name)
        if isinstance(t, (int, float)) and value >= t:
            return years
    return ""                                  # below the 2-year threshold


def write_gauge_csv(statuses, enrich, units, path):
    """The flattened table, with the notebook's column names preserved.

    pandas.json_normalize is what produced names like `gaugeLocation.latitude`,
    and read_flood_hub reads those, so it is used here rather than a hand-rolled
    flattener that would drift from the contract by one dot.
    """
    import pandas as pd

    df = pd.json_normalize(statuses)
    if df.empty:
        return 0
    df["gaugeValueUnit"] = df["gaugeId"].astype(str).map(units)
    for col in ("dischargePeak_m3s", "peakTime", "dischargeMean_m3s", "returnPeriodYr"):
        df[col] = df["gaugeId"].astype(str).map(
            lambda g, c=col: (enrich.get(g) or {}).get(c))

    front = [c for c in ["queriedCountryName", "queriedRegionCode", "gaugeId",
                         "severity", "gaugeLocation.latitude",
                         "gaugeLocation.longitude", "issuedTime"]
             if c in df.columns]
    df = df[front + [c for c in df.columns if c not in front]]

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    df.to_csv(tmp, index=False)
    os.replace(tmp, path)
    return len(df)


# ---- part three: urban flash floods -----------------------------------------

def search_flash(key):
    events, body = [], {}
    while True:
        resp = _request("POST", "flashFloods:search", key, body=body)
        events.extend(resp.get("flashFloodEvents", []) or [])
        token = resp.get("nextPageToken")
        if not token:
            return events
        body["pageToken"] = token


def kml_to_geojson(kml_text):
    """A KML string -> a GeoJSON geometry dict, or None.

    Parsed directly rather than through geopandas' KML driver, which needs a temp
    file per polygon and would mean ~360 of them on a cold cache. Falls back to
    geopandas if this finds nothing, so an unexpected KML shape degrades to the
    slow path instead of silently dropping a polygon.
    """
    def ring(el):
        text = (el.findtext("k:coordinates", default="", namespaces=KML_NS) or "")
        pts = []
        for tok in text.split():
            bits = tok.split(",")
            if len(bits) >= 2:
                try:
                    pts.append([float(bits[0]), float(bits[1])])
                except ValueError:
                    pass
        if len(pts) >= 4 and pts[0] != pts[-1]:
            pts.append(pts[0])
        return pts if len(pts) >= 4 else None

    try:
        root = ET.fromstring(kml_text)
    except ET.ParseError:
        return _kml_via_geopandas(kml_text)

    polys = []
    for poly in root.iter(f"{{{KML_NS['k']}}}Polygon"):
        rings = []
        outer = poly.find("k:outerBoundaryIs/k:LinearRing", KML_NS)
        if outer is not None:
            r = ring(outer)
            if r:
                rings.append(r)
        for inner in poly.findall("k:innerBoundaryIs/k:LinearRing", KML_NS):
            r = ring(inner)
            if r:
                rings.append(r)
        if rings:
            polys.append(rings)

    if not polys:
        return _kml_via_geopandas(kml_text)
    if len(polys) == 1:
        return {"type": "Polygon", "coordinates": polys[0]}
    return {"type": "MultiPolygon", "coordinates": polys}


def _kml_via_geopandas(kml_text):
    import tempfile
    try:
        import geopandas as gpd
        from shapely.geometry import mapping
    except ImportError:
        return None
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".kml", delete=False) as f:
            f.write(kml_text)
            tmp = f.name
        g = gpd.read_file(tmp, driver="KML")
        if g.empty:
            return None
        try:
            geom = g.geometry.union_all()
        except AttributeError:
            geom = g.geometry.unary_union
        return mapping(geom)
    except Exception:
        return None
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


def polygon_geometries(ids, key, refresh=False):
    """{polygonId: geometry}, fetching only the ids not already cached.

    A flash polygon is a fixed ~20km urban cell, so the same ids recur as weather
    moves across them. Whether they recur OFTEN is an open question — if Google
    mints a fresh id per forecast run the cache simply never hits, which costs a
    little disk and nothing else.
    """
    cache = {} if refresh else _load_cache(C.FLASH_POLYGON_CACHE)
    want = [p for p in ids if p and p not in cache]
    failed = []
    for i, pid in enumerate(want, 1):
        try:
            resp = _request("GET", f"serializedPolygons/{pid}", key)
        except RuntimeError as e:
            failed.append((pid, str(e)))
            continue
        kml = resp.get("kml")
        geom = kml_to_geojson(kml) if kml else None
        if geom:
            cache[pid] = geom
        else:
            failed.append((pid, "no usable geometry in the KML"))
        if i % 50 == 0:
            print(f"  flash      {i}/{len(want)} polygon(s) fetched", flush=True)
    if want:
        _save_cache(C.FLASH_POLYGON_CACHE, cache)
    return cache, len(want), failed


def write_flash_geojson(events, geoms, names, path):
    """One feature per (event, polygon type), which is what read_flash expects:
    it keys the severity off `polygon_type` and reads nothing else but geometry.
    """
    feats = []
    for idx, ev in enumerate(events):
        codes = ev.get("affectedCountryCodes") or []
        if isinstance(codes, list):
            code_text = ", ".join(codes)
            name_text = ", ".join(names.get(c, c) for c in codes)
        else:
            code_text, name_text = codes or "", ""
        for ptype, field in C.FLASH_POLYGON_TYPES:
            pid = ev.get(field)
            if not isinstance(pid, str) or pid not in geoms:
                continue
            feats.append({
                "type": "Feature",
                "geometry": geoms[pid],
                "properties": {
                    "event_index": idx,
                    "polygon_type": ptype,
                    "polygonId": pid,
                    "forecastIssueTime": ev.get("forecastIssueTime"),
                    "forecastPeriodHours": ev.get("forecastPeriodHours"),
                    "affectedCountryCodes": code_text,
                    "affectedCountryNames": name_text,
                },
            })
    write_text(path, json.dumps({"type": "FeatureCollection", "features": feats}))
    return len(feats)
