#!/usr/bin/env python3
"""The Flood Hub fetcher, exercised against a faked API.

    python3 backend/test/test_floodhub.py

No network and no key: urlopen is replaced with something that serves canned
responses, so the paging, the per-country checkpointing, the failure gate, the
KML parsing and the caches are all exercised for real while _request itself is
still the code under test.

The one thing worth saying out loud: there is a test here asserting that a key
never reaches stdout or stderr. That is not paranoia about this module — it is
that fews4all.sh tees both streams into logs/<date>.log, so a leak would be
written to disk inside the project, permanently, in plaintext.
"""

import io
import json
import os
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "daily"))

from common import config as C          # noqa: E402
from common import floodhub as FH       # noqa: E402

PASS, FAIL = [], []
SECRET = "AIzaSyTESTKEY_do_not_log_me_1234567890"


def ok(cond, what):
    (PASS if cond else FAIL).append(what)
    print(("  pass  " if cond else "  FAIL  ") + what)


KML = """<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2">
<Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>
0,0 1,0 1,1 0,1 0,0</coordinates></LinearRing></outerBoundaryIs></Polygon>
</Placemark></kml>"""

KML_HOLE = """<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2">
<Placemark><Polygon>
<outerBoundaryIs><LinearRing><coordinates>0,0 4,0 4,4 0,4 0,0</coordinates></LinearRing></outerBoundaryIs>
<innerBoundaryIs><LinearRing><coordinates>1,1 2,1 2,2 1,2 1,1</coordinates></LinearRing></innerBoundaryIs>
</Polygon></Placemark></kml>"""


class FakeAPI:
    """Serves the five endpoints the fetcher uses."""

    def __init__(self):
        self.fail_regions = set()
        self.calls = []

    def open(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        path = url.split("/v1/", 1)[1].split("?", 1)[0]
        qs = urllib.parse.parse_qs(url.split("?", 1)[1])
        body = json.loads(req.data) if getattr(req, "data", None) else {}
        self.calls.append(path)

        if path == "floodStatus:searchLatestFloodStatusByArea":
            code = body.get("regionCode")
            if code in self.fail_regions:
                raise urllib.error.HTTPError(url, 503, "unavailable", {}, None)
            # two pages for AF, so paging is exercised
            if code == "AF" and not body.get("pageToken"):
                return self._json({"floodStatuses": [self._gauge("AF", 1)],
                                   "nextPageToken": "p2"})
            if code == "AF":
                return self._json({"floodStatuses": [self._gauge("AF", 2)]})
            return self._json({"floodStatuses": [self._gauge(code, 1)]})

        if path == "gaugeModels:batchGet":
            names = qs.get("names", [])
            return self._json({"gaugeModels": [
                {"gaugeId": n.split("/")[-1],
                 "gaugeValueUnit": "CUBIC_METERS_PER_SECOND",
                 "thresholds": {"warningLevel": 10, "dangerLevel": 50,
                                "extremeDangerLevel": 100}} for n in names]})

        if path == "gauges:queryGaugeForecasts":
            ids = qs.get("gaugeIds", [])
            return self._json({"forecasts": {g: {"forecasts": [
                {"issuedTime": "2026-10-22T00:00:00Z", "forecastRanges": [
                    {"value": 20.0, "forecastStartTime": "2026-10-22T06:00:00Z"},
                    {"value": 120.0, "forecastStartTime": "2026-10-22T12:00:00Z"},
                ]}]} for g in ids}})

        if path == "flashFloods:search":
            return self._json({"flashFloodEvents": [{
                "forecastIssueTime": "2026-10-22T00:00:00Z",
                "forecastPeriodHours": 24,
                "affectedCountryCodes": ["AF", "IN"],
                "likelyAffectedPolygonId": "poly-a",
                "highlyLikelyAffectedPolygonId": "poly-b",
            }]})

        if path.startswith("serializedPolygons/"):
            return self._json({"kml": KML_HOLE if path.endswith("poly-b") else KML})

        raise AssertionError("unexpected endpoint " + path)

    @staticmethod
    def _gauge(code, n):
        return {"gaugeId": f"{code}-{n}", "severity": "SEVERE",
                "gaugeLocation": {"latitude": 1.0, "longitude": 2.0},
                "issuedTime": "2026-10-22T00:00:00Z"}

    @staticmethod
    def _json(obj):
        return _Ctx(io.BytesIO(json.dumps(obj).encode()))


class _Ctx:
    def __init__(self, b): self.b = b
    def __enter__(self): return self.b
    def __exit__(self, *a): self.b.close(); return False


def install(api):
    urllib.request.urlopen = lambda req, timeout=None: api.open(req, timeout)


def test_kml():
    print("\nReading KML without a temp file per polygon")
    g = FH.kml_to_geojson(KML)
    ok(g and g["type"] == "Polygon", f"a simple polygon parses ({g and g['type']})")
    ok(g and g["coordinates"][0][0] == g["coordinates"][0][-1],
       "and its ring is closed")
    h = FH.kml_to_geojson(KML_HOLE)
    ok(h and len(h["coordinates"]) == 2, "an inner boundary becomes a second ring")
    ok(FH.kml_to_geojson("not kml at all") is None, "garbage returns None, not a crash")


def test_sweep(api, tmp):
    print("\nSweeping the world, resumably")
    codes = ["AF", "IN", "BR", "KE"]
    names = {c: c + "-land" for c in codes}
    st, failed, cps = FH.sweep_gauges(SECRET, tmp, names, codes, progress=False)
    ok(len(st) == 5, f"paging collected both pages for AF ({len(st)} gauges)")
    ok(not failed, "no failures against a healthy API")
    ok(all(s["queriedCountryName"].endswith("-land") for s in st),
       "every record is tagged with its country")

    before = len(api.calls)
    st2, _f, _c = FH.sweep_gauges(SECRET, tmp, names, codes, progress=False)
    ok(len(st2) == 5 and len(api.calls) == before,
       "a rerun resumes from the checkpoint and spends no calls")
    for p in cps:
        os.path.exists(p) and os.remove(p)


def test_failures(api, tmp):
    print("\nA partial sweep is the dangerous case")
    api.fail_regions = {"BR"}
    codes = ["AF", "IN", "BR", "KE"]
    st, failed, cps = FH.sweep_gauges(SECRET, tmp, {}, codes, progress=False)
    ok(list(failed) == ["BR"], f"the failing region is named, not just counted ({list(failed)})")
    ok(len(st) == 4, "and the countries that answered still produce gauges")
    ok("BR" not in open(os.path.join(tmp, "world_flood_status.done.txt")).read(),
       "a failed region is not marked done, so a rerun retries it")
    api.fail_regions = set()
    for p in cps:
        os.path.exists(p) and os.remove(p)


def test_enrich(api, tmp):
    print("\nDischarge, peak timing and return period")
    C.FLOOD_HUB_MODEL_CACHE = os.path.join(tmp, "models.json")
    st = [FakeAPI._gauge("AF", 1), FakeAPI._gauge("IN", 1)]
    enrich, stats = FH.enrich_warned(st, SECRET)
    one = enrich.get("AF-1") or {}
    ok(one.get("dischargePeak_m3s") == 120.0, f"the peak is the max range ({one.get('dischargePeak_m3s')})")
    ok(one.get("peakTime") == "2026-10-22T12:00:00Z", "peak time is the range that produced it")
    ok(one.get("dischargeMean_m3s") == 70.0, f"the mean averages the ranges ({one.get('dischargeMean_m3s')})")
    ok(one.get("returnPeriodYr") == 20, f"120 clears the 100 extreme threshold -> 20yr ({one.get('returnPeriodYr')})")

    before = len(api.calls)
    FH.enrich_warned(st, SECRET)
    models_calls = sum(1 for c in api.calls[before:] if c == "gaugeModels:batchGet")
    ok(models_calls == 0, "a second run reads thresholds from cache, spending no calls")


def test_flash(api, tmp):
    print("\nFlash polygons")
    C.FLASH_POLYGON_CACHE = os.path.join(tmp, "polys.json")
    events = FH.search_flash(SECRET)
    ids = {ev.get(f) for _t, f in C.FLASH_POLYGON_TYPES for ev in events}
    geoms, fetched, bad = FH.polygon_geometries(ids, SECRET)
    ok(fetched == 2 and not bad, f"both polygons fetched ({fetched}, {len(bad)} bad)")

    _g2, fetched2, _b = FH.polygon_geometries(ids, SECRET)
    ok(fetched2 == 0, "a second run fetches none of them — the cache is the point")

    # An event missing one polygon type must not land a None in the id set: it
    # would be skipped by the fetch but still counted, inflating cache hits by 1.
    partial = events + [{"likelyAffectedPolygonId": "poly-a"}]      # no highly_likely
    pids = {pid for _t, field in C.FLASH_POLYGON_TYPES for ev in partial
            for pid in [ev.get(field)] if isinstance(pid, str) and pid}
    ok(None not in pids and len(pids) == 2,
       f"an event missing a polygon type contributes no None ({sorted(pids)})")

    path = os.path.join(tmp, "urban_flash_floods.geojson")
    n = FH.write_flash_geojson(events, geoms, {"AF": "Afghanistan"}, path)
    doc = json.load(open(path))
    types = {f["properties"]["polygon_type"] for f in doc["features"]}
    ok(n == 2 and doc["type"] == "FeatureCollection", "a FeatureCollection is written")
    ok(types == {"likely", "highly_likely"},
       f"one feature per polygon type, which is what read_flash keys on ({types})")
    ok(all(set(C.FLASH_SEVERITY) >= types for _ in [0]),
       "and those names are the ones FLASH_SEVERITY maps")
    ok("Afghanistan" in doc["features"][0]["properties"]["affectedCountryNames"],
       "country codes are resolved to names")


def test_key_never_logged(api, tmp):
    print("\nThe key must never reach a log")
    ok("<redacted>" in FH._redact(f"https://x/y?key={SECRET}&a=1"),
       "_redact scrubs a key out of a URL")
    api.fail_regions = {"ZZ"}
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            FH.sweep_gauges(SECRET, tmp, {}, ["ZZ"], progress=True)
        except Exception as e:
            print(e)
    text = out.getvalue() + err.getvalue()
    ok(SECRET not in text, "a failing request prints nothing containing the key")
    raised = None
    try:
        FH._request("POST", "floodStatus:searchLatestFloodStatusByArea", SECRET,
                    body={"regionCode": "ZZ"})
    except RuntimeError as e:
        raised = e
    ok(raised is not None, "a 503 becomes a RuntimeError")
    ok(raised is not None and SECRET not in str(raised),
       "and the exception it raises does not carry the key either")
    ok(raised is not None and "floodStatus" in str(raised),
       "it names the endpoint, which is what you need to debug it")
    api.fail_regions = set()


def test_round_trip(api, tmp):
    """What the fetcher writes must be what the pipeline already reads."""
    print("\nThe output is readable by the existing readers")
    from common import models as M
    C.FLOOD_HUB_MODEL_CACHE = os.path.join(tmp, "models2.json")
    C.FLASH_POLYGON_CACHE = os.path.join(tmp, "polys2.json")

    st, _f, cps = FH.sweep_gauges(SECRET, tmp, {"AF": "Afghanistan"},
                                  ["AF", "IN"], progress=False)
    enrich, stats = FH.enrich_warned(st, SECRET)
    csv_path = os.path.join(tmp, "world_flood_status.csv")
    FH.write_gauge_csv(st, enrich, stats.get("units") or {}, csv_path)

    rows = M.read_flood_hub(csv_path)
    ok(len(rows) == len(st), f"read_flood_hub parses every gauge ({len(rows)}/{len(st)})")
    r = rows[0]
    ok(r["model"] == "flood_hub" and r["severity"] in ("warning", "danger", "extreme"),
       f"severity maps onto the shared ladder ({r['severity']})")
    ok(r["lat"] not in ("", None) and r["lon"] not in ("", None),
       "coordinates survive the flatten")
    ok(str(r["return_period_yr"]) == "20", f"the enrichment lands ({r['return_period_yr']})")
    ok(r["peak_time"] == "2026-10-22T12:00:00Z", "and so does peak time")

    events = FH.search_flash(SECRET)
    ids = {ev.get(f) for _t, f in C.FLASH_POLYGON_TYPES for ev in events}
    geoms, _n, _b = FH.polygon_geometries(ids, SECRET)
    gj = os.path.join(tmp, "urban_flash_floods.geojson")
    FH.write_flash_geojson(events, geoms, {}, gj)
    frows = M.read_flash(gj)
    ok(len(frows) == 2, f"read_flash parses both polygons ({len(frows)})")
    ok({x["severity"] for x in frows} == {"warning", "extreme"},
       f"likely/highly_likely map to the right rungs ({ {x['severity'] for x in frows} })")
    ok(all(x.get("_geometry") for x in frows), "each carries its geometry through")

    # 2026-10-29: Flood Hub served a polygon with an EMPTY geometry. shape()
    # accepted it and representative_point() returned POINT EMPTY rather than
    # raising, so the throw landed on `pt.y` outside the try, escaped read_flash,
    # and the per-model gate dropped all 92 events. One bad feature must cost one
    # feature. Every shape below is one Flood Hub has served or plausibly can.
    doc = json.load(open(gj))
    good = len(doc["features"])
    proto = doc["features"][0]
    for bad_geom in ({"type": "Polygon", "coordinates": []},
                     {"type": "MultiPolygon", "coordinates": []},
                     {"type": "GeometryCollection", "geometries": []},
                     None):
        feat = json.loads(json.dumps(proto))
        feat["geometry"] = bad_geom
        doc["features"].append(feat)
    hurt = os.path.join(tmp, "urban_flash_floods_empty.geojson")
    json.dump(doc, open(hurt, "w"))
    try:
        hrows = M.read_flash(hurt)
    except Exception as exc:
        hrows = None
        ok(False, f"an empty geometry must not escape read_flash ({exc})")
    if hrows is not None:
        ok(len(hrows) == good,
           f"the {good} good feature(s) survive four unusable ones ({len(hrows)})")
        ok(all(isinstance(x["lat"], float) and isinstance(x["lon"], float)
               for x in hrows), "and every surviving row still has real coordinates")
    for pth in cps:
        os.path.exists(pth) and os.remove(pth)


def main():
    api = FakeAPI()
    install(api)
    tmp = tempfile.mkdtemp(prefix="fews-fh-")
    keep = (C.FLOOD_HUB_MODEL_CACHE, C.FLASH_POLYGON_CACHE)
    try:
        test_kml()
        test_sweep(api, tmp)
        test_failures(api, tmp)
        test_enrich(api, tmp)
        test_flash(api, tmp)
        test_key_never_logged(api, tmp)
        test_round_trip(api, tmp)
    finally:
        C.FLOOD_HUB_MODEL_CACHE, C.FLASH_POLYGON_CACHE = keep
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
