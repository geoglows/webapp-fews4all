"""GloFAS, from ECMWF's Auxiliary Data Store over HTTPS.

Eight files a cycle: three sumAL alert grids and a five-part RPG_U shapefile,
about 126 MB in total — close enough to the GEOGLOWS fetch that this module is
shaped like that one rather than like the Flood Hub client. List nothing, parse
no JSON, just take the files a dated folder owes us.

FINDING THE CYCLE BY PROBING, NOT BY LISTING

The folder is dated and every filename inside it is determined by the stamp, so
the newest cycle is found by asking whether one known file exists, walking back a
day at a time. That skips parsing a web UI's HTML — which would be the most
fragile thing in the pipeline and would break the morning somebody at ECMWF
redesigns a page — and it handles a skipped day for free.

THE CREDENTIAL IS IN A HEADER

HTTP Basic, so the username and password travel in an Authorization header and
never appear in a URL. That is the opposite of Flood Hub, and it has a useful
consequence: this module CAN print the URL it tried. On a 404 that is the
difference between "the path shape is wrong, here it is" and a mystery, which
matters because nobody here can test a real connection — the machine that runs
this is the only one that can reach the host at all.

WHAT A LOGIN PAGE LOOKS LIKE IF YOU ARE NOT CAREFUL

A server that wants a session cookie may answer an unauthenticated request with
200 and an HTML login form. Written to disk under a .nc name, that is a 2 KB file
the reader will fail on in some confusing way days later. So the content type and
the first bytes are checked before anything is accepted, and an HTML body where a
grid was expected is reported as an authentication problem, which is what it is.
"""

import base64
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config as C

TIMEOUT = 300           # these are 41 MB files
CHUNK = 1 << 20


def credentials():
    return (os.environ.get(C.ECPDS_USER_ENV, "").strip(),
            os.environ.get(C.ECPDS_PASSWORD_ENV, "").strip())


def _auth_header(user, password):
    raw = f"{user}:{password}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def file_url(stamp, name):
    """The folder is the DAY (20261023); the filenames carry the cycle hour too
    (2026102300). They are not the same string, and conflating them 404s every
    request — the kind of mistake that reads as "no cycle published today"."""
    return (f"{C.ECPDS_BASE}/file/{C.ECPDS_PRODGEN}/{stamp[:8]}/"
            + urllib.parse.quote(name))


def _open(url, auth, method="GET"):
    """Preemptive Basic, because a server that answers 403 without challenging
    would otherwise never see the credentials at all."""
    req = urllib.request.Request(url, method=method,
                                 headers={"Authorization": auth})
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def _looks_like_html(head, content_type):
    if "html" in (content_type or "").lower():
        return True
    return head[:1].strip()[:1] in (b"<",)


def exists(stamp, auth):
    """Whether this cycle has published the file that stands for it."""
    url = file_url(stamp, C.GLOFAS_PROBE_FILE.format(stamp=stamp))
    try:
        with _open(url, auth, method="HEAD") as r:
            return r.status == 200 and not _looks_like_html(
                b"", r.headers.get("Content-Type"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError(
                f"ECPDS rejected the credentials (HTTP {e.code}). Check "
                f"{C.ECPDS_USER_ENV} and {C.ECPDS_PASSWORD_ENV} in "
                f"variables.env.\n  tried: {url}") from None
        return False
    except urllib.error.URLError as e:
        raise RuntimeError(f"could not reach {C.ECPDS_BASE}: {e.reason}") from None


def latest_cycle(auth, back=None, today=None):
    """The newest cycle the store actually holds, or None.

    Probed rather than computed: GloFAS lands around 09 UTC, so "today" is a
    folder that does not exist for most of the morning, and a skipped day would
    otherwise read as an outage.
    """
    back = C.ECPDS_LOOKBACK_DAYS if back is None else back
    now = today if today is not None else time.time()
    for days in range(back + 1):
        stamp = time.strftime("%Y%m%d", time.gmtime(now - days * 86400)) + "00"
        if exists(stamp, auth):
            return stamp
    return None


def plan(stamp):
    return [n.format(stamp=stamp) for n in C.GLOFAS_FILES]


def fetch_one(stamp, name, dest_dir, auth):
    """(status, path, bytes). Skips a file already present at the same size."""
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, name)
    url = file_url(stamp, name)

    try:
        with _open(url, auth, method="HEAD") as r:
            advertised = int(r.headers.get("Content-Length") or 0)
    except urllib.error.HTTPError:
        advertised = 0

    if advertised and os.path.exists(path) and os.path.getsize(path) == advertised:
        return "skipped", path, advertised

    tmp = path + ".part"
    try:
        with _open(url, auth) as r, open(tmp, "wb") as f:
            ctype = r.headers.get("Content-Type")
            first = r.read(CHUNK)
            if _looks_like_html(first, ctype):
                raise RuntimeError(
                    f"{name}: the server returned HTML, not a file — that is "
                    f"usually a login page, so the credentials were not accepted "
                    f"or this path needs a session rather than Basic auth.\n"
                    f"  tried: {url}")
            f.write(first)
            while True:
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
    except urllib.error.HTTPError as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RuntimeError(f"{name}: HTTP {e.code} {e.reason}\n  tried: {url}") from None
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise

    got = os.path.getsize(tmp)
    if advertised and got != advertised:
        os.remove(tmp)
        raise RuntimeError(f"{name}: got {got:,} bytes, the store advertised "
                           f"{advertised:,}")
    os.replace(tmp, path)
    return "fetched", path, got


def fetch(dest_dir, user, password, stamp=None):
    """The whole cycle. Returns a record for the run manifest.

    All eight files or none of them: a GloFAS release missing one alert level
    would be published a severity too low, so a partial download is reported as a
    failure rather than left for models.py to discover.
    """
    auth = _auth_header(user, password)
    t0 = time.time()
    stamp = stamp or latest_cycle(auth)
    if not stamp:
        raise RuntimeError(
            f"no cycle found in the last {C.ECPDS_LOOKBACK_DAYS} day(s) under "
            f"{C.ECPDS_BASE}/file/{C.ECPDS_PRODGEN}/")

    names = plan(stamp)
    got, skipped, total, failed = 0, 0, 0, []
    for name in names:
        try:
            status, _path, size = fetch_one(stamp, name, dest_dir, auth)
        except RuntimeError as e:
            failed.append(str(e))
            continue
        total += size
        if status == "fetched":
            got += 1
        else:
            skipped += 1

    record = {"cycle": stamp, "objects": len(names), "fetched": got,
              "skipped": skipped, "bytes": total}
    if failed:
        record["notes"] = failed
        raise RuntimeError(f"{len(failed)} of {len(names)} file(s) failed; "
                           f"the first was: {failed[0]}")
    print(f"  glofas     {got} fetched, {skipped} already present, "
          f"{total / 1e6:,.0f} MB, cycle {stamp}  ({time.time() - t0:.0f}s)")
    return record
