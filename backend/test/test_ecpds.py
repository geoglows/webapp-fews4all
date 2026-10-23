#!/usr/bin/env python3
"""The GloFAS fetcher, exercised against a faked data store.

    python3 backend/test/test_ecpds.py

Nobody on this side of the connection can reach ECPDS — it is credentialed and
only the machine running the pipeline can talk to it — so everything that can be
checked without a real connection is checked here: the URL shape, the cycle
probe, the all-or-nothing rule, the resume, and the two failures that would
otherwise be discovered days later as a confusing read error.
"""

import io
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
sys.path.insert(0, BACKEND)

from common import config as C          # noqa: E402
from common import ecpds as E           # noqa: E402

PASS, FAIL = [], []
USER, PASSWORD = "someone", "s3cr3t-do-not-log"
GRID = b"CDF\x01" + b"\0" * 4096
LOGIN = b"<!DOCTYPE html><html><body><form>sign in</form></body></html>"


def ok(cond, what):
    (PASS if cond else FAIL).append(what)
    print(("  pass  " if cond else "  FAIL  ") + what)


class Store:
    def __init__(self):
        self.days = {"20261023"}
        self.missing = set()        # filenames to 404
        self.serve_login = False
        self.bad_auth = False
        self.truncate = set()
        self.calls = []

    def open(self, req, timeout=None):
        url, method = req.full_url, req.get_method()
        self.calls.append((method, url))
        if self.bad_auth or req.headers.get("Authorization") is None:
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
        tail = url.split("/file/" + C.ECPDS_PRODGEN + "/", 1)[1]
        day, name = tail.split("/", 1)
        if day not in self.days or name in self.missing:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        body = LOGIN if self.serve_login else GRID
        if name in self.truncate:
            body = body[:16]
        head = {"Content-Length": str(len(GRID)),
                "Content-Type": "text/html" if self.serve_login
                else "application/x-netcdf"}
        return _Resp(b"" if method == "HEAD" else body, head)


class _Resp:
    def __init__(self, body, headers):
        self.b, self.headers, self.status = io.BytesIO(body), headers, 200
    def read(self, n=-1): return self.b.read(n)
    def __enter__(self): return self
    def __exit__(self, *a): self.b.close(); return False


def install(store):
    urllib.request.urlopen = lambda req, timeout=None: store.open(req, timeout)


AUTH = None


def test_urls():
    print("\nThe URL shape")
    u = E.file_url("2026102300", "sumAL_medium_AL_2026102300.nc")
    ok("/prodgen/20261023/" in u,
       "the folder is the DAY, not the 10-digit cycle stamp")
    ok(u.endswith("sumAL_medium_AL_2026102300.nc"),
       "...while the filename keeps the cycle hour")
    ok(len(E.plan("2026102300")) == 8, "a cycle is eight files")
    ok(sum(1 for n in E.plan("2026102300") if n.startswith("sumAL")) == 3,
       "three of them are alert levels, which must arrive together")


def test_probe(store):
    print("\nFinding the cycle by probing")
    store.days = {"20261023"}
    got = E.latest_cycle(AUTH, today=_t("20261023"))
    ok(got == "2026102300", f"today's cycle is found ({got})")

    got = E.latest_cycle(AUTH, today=_t("20261024"))
    ok(got == "2026102300",
       f"and yesterday's when today has not published yet ({got})")

    store.days = {"20261019"}
    ok(E.latest_cycle(AUTH, back=2, today=_t("20261024")) is None,
       "a cycle beyond the lookback is not found rather than guessed at")
    store.days = {"20261023"}


def _t(day):
    import time as _time
    return _time.mktime(_time.strptime(day + " 12", "%Y%m%d %H"))


def test_fetch(store, tmp):
    print("\nTaking the files")
    rec = E.fetch(tmp, USER, PASSWORD, stamp="2026102300")
    ok(rec["fetched"] == 8, f"all eight land ({rec['fetched']})")
    ok(os.path.exists(os.path.join(tmp, "RPG_U2026102300.dbf")),
       "including every part of the shapefile")

    rec = E.fetch(tmp, USER, PASSWORD, stamp="2026102300")
    ok(rec["skipped"] == 8 and rec["fetched"] == 0,
       "a rerun skips them all — the size matches what the store advertises")

    ok(not [f for f in os.listdir(tmp) if f.endswith(".part")],
       "no .part file is left behind")


def test_partial(store, tmp):
    print("\nA cycle missing one alert level must not half-publish")
    store.missing = {"sumAL_extreme_AL_2026102300.nc"}
    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))
    raised = None
    try:
        E.fetch(tmp, USER, PASSWORD, stamp="2026102300")
    except RuntimeError as e:
        raised = e
    ok(raised is not None, "the fetch fails rather than returning seven files")
    ok(raised and "sumAL_extreme" in str(raised), "and names the file that is missing")
    store.missing = set()


def test_truncation(store, tmp):
    print("\nA truncated grid")
    store.truncate = {"sumAL_high_AL_2026102300.nc"}
    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))
    raised = None
    try:
        E.fetch(tmp, USER, PASSWORD, stamp="2026102300")
    except RuntimeError as e:
        raised = e
    ok(raised is not None and "advertised" in str(raised),
       "a short file is refused, with both byte counts")
    ok(not os.path.exists(os.path.join(tmp, "sumAL_high_AL_2026102300.nc")),
       "...and is not left on disk to be read as a valid shorter grid")
    store.truncate = set()


def test_login_page(store, tmp):
    print("\nAn HTML login page is an auth problem, not a 2 KB netCDF")
    store.serve_login = True
    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))
    raised = None
    try:
        E.fetch(tmp, USER, PASSWORD, stamp="2026102300")
    except RuntimeError as e:
        raised = e
    ok(raised is not None and "HTML" in str(raised),
       "HTML where a grid was expected is caught at the first chunk")
    ok(raised and "credentials" in str(raised).lower(),
       "and is reported as a credential problem, which is what it is")
    store.serve_login = False


def test_auth_failure(store, tmp):
    print("\nBad credentials")
    store.bad_auth = True
    raised = None
    try:
        E.latest_cycle(AUTH, today=_t("20261023"))
    except RuntimeError as e:
        raised = e
    ok(raised is not None and C.ECPDS_USER_ENV in str(raised),
       "a 401 names the settings to check")
    ok(raised and "tried:" in str(raised),
       "and says which URL it tried — safe here, since Basic auth keeps the "
       "credential in a header")
    ok(raised and PASSWORD not in str(raised),
       "the password itself never appears in the message")
    store.bad_auth = False


def main():
    global AUTH
    store = Store()
    install(store)
    AUTH = E._auth_header(USER, PASSWORD)
    tmp = tempfile.mkdtemp(prefix="fews-ecpds-")
    try:
        test_urls()
        test_probe(store)
        test_fetch(store, tmp)
        test_partial(store, tmp)
        test_truncation(store, tmp)
        test_login_page(store, tmp)
        test_auth_failure(store, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
